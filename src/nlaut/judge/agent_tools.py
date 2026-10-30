"""Agent-as-Judge 工具集：judge 可自主调用的取证工具。

每个工具 = (OpenAI tools schema, 只读实现)。实现方保证只读语义——
agent judge 只做验证，不允许改动被测系统状态（软件工厂门禁场景的硬约束）。

工具分三类（对应 AJ-Bench 的三个验证维度）：
1. 信息获取 (information acquisition)：查接口、查表、翻日志、读文件
2. 状态验证 (state verification)：读当前实际状态（表行、DOM、接口响应）
3. 过程验证 (process verification)：翻执行轨迹（trace / tool_calls 历史）
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

# --- OpenAI tools schema 定义 ---

QUERY_DB_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "query_db",
        "description": "查询数据库中某张表的当前行数与关键字段内容，用于验证数据是否真的落库/落表。",
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "表名"},
                "limit": {"type": "integer", "description": "最多返回几行，默认 20"},
            },
            "required": ["table"],
        },
    },
}

HTTP_PROBE_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "http_probe",
        "description": "对给定 URL 发起只读 GET 请求，返回状态码与响应体，用于验证模块对外接口是否可用、数据是否返回。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整 URL"},
                "timeout_s": {"type": "number", "description": "超时秒数，默认 15"},
            },
            "required": ["url"],
        },
    },
}

READ_LOG_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "read_log",
        "description": "读取日志文件尾部内容，用于验证执行过程是否留痕、是否有错误。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "日志文件路径"},
                "tail_lines": {"type": "integer", "description": "读取末尾多少行，默认 50"},
            },
            "required": ["path"],
        },
    },
}

READ_FILE_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "读取指定文件的内容（只读），用于核对配置、契约、模块注册信息。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径"},
                "max_bytes": {"type": "integer", "description": "最多读取字节数，默认 8000"},
            },
            "required": ["path"],
        },
    },
}

INSPECT_TRACE_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "inspect_trace",
        "description": "查看被测系统的执行轨迹（历史 tool_calls 与对话），用于验证过程是否走了正确路径。",
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "integer", "description": "起始下标，默认 0"},
                "count": {"type": "integer", "description": "返回条数，默认 20"},
            },
            "required": [],
        },
    },
}

LIST_REGISTRY_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "list_registry",
        "description": "列出当前可用的模块注册信息（模块名、是否启用、注册时间），用于验证模块是否真的完成注册。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

LOCATE_OBJECT_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "locate_object",
        "description": "按关键词在数据源中定位目标对象并返回其所在位置与关键字段（相当于'按需求定位相关记录'）。用于在被测系统数据量大时，先精确定位目标再判断，避免把'整体有数据'误当成'目标对象存在'。",
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "表名"},
                "keyword": {"type": "string", "description": "要定位的关键字（匹配 id / 名称 / 业务字段）"},
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要返回的字段名列表，默认返回整行",
                },
            },
            "required": ["table", "keyword"],
        },
    },
}

DEFAULT_TOOLS: list[dict] = [
    QUERY_DB_TOOL,
    HTTP_PROBE_TOOL,
    READ_LOG_TOOL,
    READ_FILE_TOOL,
    INSPECT_TRACE_TOOL,
    LIST_REGISTRY_TOOL,
    LOCATE_OBJECT_TOOL,
]


def build_tool_impls(
    db_state: dict | None = None,
    http_probe: Callable[[str, float], Any] | None = None,
    log_reader: Callable[[str, int], Any] | None = None,
    file_reader: Callable[[str, int], Any] | None = None,
    trace: list[dict] | None = None,
    registry: dict | None = None,
) -> dict[str, Callable[[dict], Any]]:
    """构造工具实现表。

    所有实现都是**只读**的：查快照、发 GET、读文件、查轨迹，不写任何状态。

    db_state:  预置的数据库状态快照 {table: rows|row_count}
    http_probe: 自定义 HTTP GET 探针 (url, timeout) -> dict
    log_reader: 自定义日志读取 (path, tail_lines) -> str
    file_reader: 自定义文件读取 (path, max_bytes) -> str
    trace:      被测系统执行轨迹 list[dict]
    registry:   模块注册信息 dict
    """
    impls: dict[str, Callable[[dict], Any]] = {}

    def _query_db(args: dict) -> Any:
        table = args.get("table", "")
        limit = int(args.get("limit") or 20)
        if db_state is None:
            return {"error": "db_state_not_provided", "hint": "本次判定未接入数据库快照"}
        if table not in db_state:
            return {
                "error": "table_not_found",
                "available_tables": sorted(db_state),
            }
        val = db_state[table]
        if isinstance(val, list):
            return {"table": table, "row_count": len(val), "rows": val[:limit]}
        return {"table": table, "value": val}

    def _http_probe(args: dict) -> Any:
        url = args.get("url", "")
        timeout = float(args.get("timeout_s") or 15)
        if http_probe is None:
            return {"error": "http_probe_not_configured", "url": url}
        return http_probe(url, timeout)

    def _read_log(args: dict) -> Any:
        path = args.get("path", "")
        tail = int(args.get("tail_lines") or 50)
        if log_reader is None:
            return {"error": "log_reader_not_configured", "path": path}
        return log_reader(path, tail)

    def _read_file(args: dict) -> Any:
        path = args.get("path", "")
        max_bytes = int(args.get("max_bytes") or 8000)
        if file_reader is None:
            return {"error": "file_reader_not_configured", "path": path}
        return file_reader(path, max_bytes)

    def _inspect_trace(args: dict) -> Any:
        start = int(args.get("start") or 0)
        count = int(args.get("count") or 20)
        items = trace or []
        return {
            "total": len(items),
            "returned": items[start : start + count],
        }

    def _list_registry(args: dict) -> Any:
        if registry is None:
            return {"error": "registry_not_configured"}
        return registry

    def _locate_object(args: dict) -> Any:
        """按关键词定位目标对象。

        对应 Zhuge et al. 的 locate 模块（消融显示单项增益最大 +8.20pp：
        82.24% → 90.44%）。它的价值在于区分「整体有数据」与「**目标对象**存在」——
        没有定位能力时，judge 容易因为表里有其它行就误判为真。
        """
        table = args.get("table", "")
        keyword = str(args.get("keyword") or "").strip()
        fields = args.get("fields") or []
        if db_state is None:
            return {"error": "db_state_not_provided", "hint": "本次判定未接入数据库快照"}
        if table not in db_state:
            return {"error": "table_not_found", "available_tables": sorted(db_state)}
        rows = db_state[table]
        if not isinstance(rows, list):
            return {"error": "table_not_searchable", "table": table, "value": rows}
        if not keyword:
            return {"error": "keyword_required", "table": table, "row_count": len(rows)}

        matches = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            hit = any(keyword.lower() in str(v).lower() for v in row.values())
            if hit:
                matches.append({k: row.get(k) for k in fields} if fields else row)
        return {
            "table": table,
            "keyword": keyword,
            "matched": len(matches),
            "total_rows": len(rows),
            "matches": matches[:20],
        }

    impls["query_db"] = _query_db
    impls["http_probe"] = _http_probe
    impls["read_log"] = _read_log
    impls["read_file"] = _read_file
    impls["inspect_trace"] = _inspect_trace
    impls["list_registry"] = _list_registry
    impls["locate_object"] = _locate_object
    return impls


def default_log_reader(path: str, tail_lines: int) -> str:
    """默认日志读取实现：读文件末尾 N 行。文件不存在返回明确错误（不是抛异常）。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-tail_lines:])
    except FileNotFoundError:
        return f"[log_not_found] {path}"
    except OSError as e:
        return f"[log_unreadable] {path}: {type(e).__name__}"


def default_file_reader(path: str, max_bytes: int) -> str:
    """默认文件读取实现：读文件前 max_bytes 字节。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(max_bytes)
    except FileNotFoundError:
        return f"[file_not_found] {path}"
    except OSError as e:
        return f"[file_unreadable] {path}: {type(e).__name__}"


def default_http_probe(url: str, timeout: float) -> dict:
    """默认 HTTP GET 探针（只读）。内网端点需 trust_env=False。"""
    import httpx

    try:
        r = httpx.get(url, timeout=timeout, trust_env=False, follow_redirects=True)
        body = r.text[:2000]
        return {"status": r.status_code, "body": body}
    except Exception as e:  # noqa: BLE001 — 探针失败返回结构化错误
        return {"error": f"{type(e).__name__}: {e}", "url": url}


def build_env_tool_impls(**kwargs: Any) -> dict[str, Callable[[dict], Any]]:
    """带默认实现（真实读文件/发 GET）的工具表。"""
    base = build_tool_impls(**kwargs)
    if kwargs.get("http_probe") is None:
        base["http_probe"] = lambda a: default_http_probe(
            a.get("url", ""), float(a.get("timeout_s") or 15)
        )
    if kwargs.get("log_reader") is None:
        base["read_log"] = lambda a: default_log_reader(
            a.get("path", ""), int(a.get("tail_lines") or 50)
        )
    if kwargs.get("file_reader") is None:
        base["read_file"] = lambda a: default_file_reader(
            a.get("path", ""), int(a.get("max_bytes") or 8000)
        )
    return base


def snapshot_registry_from_env(env_name: str = "NLautRegistry") -> dict:
    """从环境变量读取模块注册快照（JSON 字符串），用于演示与门禁对接。"""
    raw = os.environ.get(env_name)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_raw": raw[:500]}
