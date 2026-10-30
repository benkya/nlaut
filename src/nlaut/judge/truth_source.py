"""真实组外模块接入层：`TruthSource` 契约。

背景：PoC 用 `db_state` 夹具快照验证了机制可行性，但**真实组外模块的真值源
形态未知**（可能是 DB 表 / HTTP 接口 / 日志文件 / 消息队列……）。
本模块定义一个**可插拔的接入契约**，让任意模块用自己的方式提供证据，
而不改 agent judge 引擎。

设计要点（对应《集成准入门禁判定项清单 v1.0》G-11）：
- G-11 只判「接入后能否正常工作」，**不判模块内部功能正确性**
  → TruthSource 只提供「当前实际状态」，不做业务逻辑判断
- 证据必须**只读**（与 ToolPolicy 的只读语义一致）
- 每次取证据都要带**来源标识 + 时间戳**，供报告审计与人工仲裁

四种内置实现覆盖常见形态：
| 实现 | 适用 |
| --- | --- |
| `DictTruthSource` | 夹具 / 内存快照（PoC 用） |
| `HttpTruthSource` | 被测模块提供查询接口（GET） |
| `SqlTruthSource` | 直接查库（需自行保证只读 SQL） |
| `LogTruthSource` | 从日志文件提取状态 |

用法：
```python
from nlaut.judge.truth_source import DictTruthSource, TruthEvidence

ts = DictTruthSource({"task": [{"id": 1, "module": "M"}]})
ev = ts.snapshot("task")          # -> TruthEvidence
```
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class TruthEvidence:
    """一份真值证据：值 + 来源 + 取证时间。

    `source` 必须在报告里可见——人工仲裁时要能判断「这份证据可不可信」。
    """

    key: str  # 证据键（如 "task"）
    value: Any  # 证据值（dict / list / str ...）
    source: str  # 来源标识（如 "db://prod/task" / "http://.../api/task"）
    collected_at: float = field(default_factory=time.time)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value": self.value,
            "source": self.source,
            "collected_at": self.collected_at,
            "error": self.error,
        }


@runtime_checkable
class TruthSource(Protocol):
    """真实模块真值源契约。

    实现方**只负责取状态**，不做判定（判定是 judge 的职责）。
    所有实现必须是只读的——不允许借「取证」之名改动被测系统。
    """

    name: str

    def snapshot(self, key: str) -> TruthEvidence:
        """取某个 key 的当前状态快照。

        key 的语义由接入方定义（表名 / 接口路径 / 日志标识）。
        失败时**返回带 error 的 TruthEvidence**，不抛异常——
        让 judge 能把「取不到证据」当作一种证据来处理。
        """
        ...


class DictTruthSource:
    """内存字典快照（夹具 / PoC 用）。

    真实接入时可用一个「从 DB 查出 dict」的函数替换它。
    """

    def __init__(self, data: dict | None = None, name: str = "dict", source: str = "memory://") -> None:
        self.data = data or {}
        self.name = name
        self.source = source

    def snapshot(self, key: str) -> TruthEvidence:
        if key not in self.data:
            return TruthEvidence(
                key=key,
                value=None,
                source=f"{self.source}{key}",
                error=f"key_not_found: {key}（可用: {sorted(self.data)}）",
            )
        return TruthEvidence(key=key, value=self.data[key], source=f"{self.source}{key}")

    def as_db_state(self) -> dict:
        """转成 Evidence.db_state 形态（兼容现有 judge 协议）。"""
        return dict(self.data)


class HttpTruthSource:
    """HTTP GET 取证（被测模块提供查询接口）。

    只允许 GET —— 防止「取证」变成写操作。
    """

    def __init__(
        self,
        base_url: str,
        path_map: dict[str, str] | None = None,
        name: str = "http",
        timeout: float = 15.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # key -> 路径模板（可用 {key} 占位）
        self.path_map = path_map or {}
        self.name = name
        self.timeout = timeout
        self.headers = headers or {}

    def snapshot(self, key: str) -> TruthEvidence:
        path = self.path_map.get(key)
        if not path:
            return TruthEvidence(
                key=key,
                value=None,
                source=self.base_url,
                error=f"no_path_configured: {key}（已配置: {sorted(self.path_map)}）",
            )
        url = path.format(key=key) if "{key}" in path else path
        if not url.startswith("http"):
            url = f"{self.base_url}{url}"
        try:
            import httpx

            r = httpx.get(
                url,
                timeout=self.timeout,
                headers=self.headers,
                trust_env=False,
                follow_redirects=True,
            )
        except Exception as e:  # noqa: BLE001 — 取证失败是「一种证据」，不中断判定
            return TruthEvidence(
                key=key,
                value=None,
                source=url,
                error=f"{type(e).__name__}: {e}",
            )
        source = f"{url}#{r.status_code}"
        if r.status_code != 200:
            return TruthEvidence(key=key, value=None, source=source, error=f"HTTP {r.status_code}")
        try:
            value = r.json()
        except (json.JSONDecodeError, ValueError):
            value = r.text[:4000]
        return TruthEvidence(key=key, value=value, source=source)


class SqlTruthSource:
    """直接查库取证。

    ⚠️ **只读约束由接入方保证**：本类只执行传入的 SQL，
    不做 SQL 解析拦截。接入时必须确保传入的是 SELECT。
    """

    def __init__(
        self,
        query_map: dict[str, str],
        executor: Callable[[str], Any],
        name: str = "sql",
        source_prefix: str = "db://",
    ) -> None:
        # key -> SQL 语句
        self.query_map = query_map
        self.executor = executor
        self.name = name
        self.source_prefix = source_prefix

    def snapshot(self, key: str) -> TruthEvidence:
        sql = self.query_map.get(key)
        if not sql:
            return TruthEvidence(
                key=key,
                value=None,
                source=self.source_prefix,
                error=f"no_query_configured: {key}",
            )
        if not sql.strip().lower().startswith("select"):
            return TruthEvidence(
                key=key,
                value=None,
                source=f"{self.source_prefix}{key}",
                error="not_a_select_statement（只读约束：拒绝执行非 SELECT）",
            )
        try:
            value = self.executor(sql)
        except Exception as e:  # noqa: BLE001 — 取证失败是「一种证据」
            return TruthEvidence(
                key=key,
                value=None,
                source=f"{self.source_prefix}{key}",
                error=f"{type(e).__name__}: {e}",
            )
        return TruthEvidence(key=key, value=value, source=f"{self.source_prefix}{key}")


class LogTruthSource:
    """从日志文件提取状态。

    `parser` 由接入方提供：从日志文本解析出状态结构。
    默认 parser 取文件末尾 N 行原文。
    """

    def __init__(
        self,
        path_map: dict[str, str],
        parser: Callable[[str], Any] | None = None,
        name: str = "log",
        tail_lines: int = 200,
    ) -> None:
        self.path_map = path_map
        self.parser = parser
        self.name = name
        self.tail_lines = tail_lines

    def snapshot(self, key: str) -> TruthEvidence:
        path = self.path_map.get(key)
        if not path:
            return TruthEvidence(
                key=key,
                value=None,
                source="file://",
                error=f"no_path_configured: {key}",
            )
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return TruthEvidence(
                key=key,
                value=None,
                source=f"file://{path}",
                error=f"{type(e).__name__}: {e}",
            )
        tail = "\n".join(text.splitlines()[-self.tail_lines :])
        if self.parser:
            try:
                value = self.parser(tail)
            except Exception as e:  # noqa: BLE001
                return TruthEvidence(
                    key=key,
                    value=None,
                    source=f"file://{path}",
                    error=f"parser_failed: {type(e).__name__}: {e}",
                )
        else:
            value = tail
        return TruthEvidence(key=key, value=value, source=f"file://{path}")


class CompositeTruthSource:
    """组合多个真值源（DB + HTTP + 日志）。

    接入真实模块时通常需要同时看多个来源——
    例如「表里有行」+「接口能查到」+「日志无错误」三者一起才说明真的通了。
    """

    def __init__(self, sources: dict[str, TruthSource], name: str = "composite") -> None:
        self.sources = sources
        self.name = name

    def snapshot(self, key: str) -> TruthEvidence:
        # 找第一个认识这个 key 的源
        for src in self.sources.values():
            ev = src.snapshot(key)
            if ev.ok:
                return ev
        # 全部不认识 → 汇总错误
        errors = []
        for nm, src in self.sources.items():
            ev = src.snapshot(key)
            if ev.error:
                errors.append(f"{nm}: {ev.error}")
        return TruthEvidence(
            key=key,
            value=None,
            source=f"composite:{','.join(self.sources)}",
            error="all_sources_failed: " + " | ".join(errors),
        )

    def snapshot_all(self, keys: list[str]) -> dict[str, TruthEvidence]:
        """批量取多个 key 的证据。"""
        return {k: self.snapshot(k) for k in keys}
