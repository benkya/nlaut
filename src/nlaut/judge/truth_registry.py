"""真值源注册表：从 YAML 声明式配置构建 TruthSource 实例。

**背景**：这个模块原本是死代码 —— `config/truth_source_registry.yaml` 声明了
真值源，但没有任何代码读它。`truth_source.py` 里的 5 种实现
（Dict / Http / Sql / Log / Composite）也从未被生产路径调用。

**现在接上了**：runner 在构造 Evidence 时读注册表，把声明的真值源
快照注入 `Evidence.db_state` —— 让 LLM-as-Judge 这类「只读拼进 prompt 的证据」
的判定器有真东西可读。

**只读约束**（沿用 truth_source.py 的契约）：
注册表只声明**读**操作（file / http GET / select SQL）。配置里不允许写操作，
`HttpTruthSource` 只发 GET，`SqlTruthSource` 只执行 SELECT。

用法：
```python
from nlaut.judge.truth_registry import load_registry, collect_db_state

reg = load_registry("config/truth_source_registry.yaml")
db_state = collect_db_state(reg)      # -> {"design_tasks": {...}, ...}
```
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .truth_source import (
    CompositeTruthSource,
    DictTruthSource,
    HttpTruthSource,
    LogTruthSource,
    SqlTruthSource,
    TruthEvidence,
    TruthSource,
)

__all__ = [
    "DEFAULT_REGISTRY_PATH",
    "RegistryEntry",
    "TruthRegistry",
    "collect_db_state",
    "load_registry",
]

DEFAULT_REGISTRY_PATH = "config/truth_source_registry.yaml"


@dataclass
class RegistryEntry:
    """注册表里的一条声明。"""

    key: str
    type: str
    description: str = ""
    path: str | None = None
    base_url: str | None = None
    path_map: dict[str, str] = field(default_factory=dict)
    query_map: dict[str, str] = field(default_factory=dict)
    timeout: float = 15.0
    extras: dict[str, Any] = field(default_factory=dict)

    def build(self) -> TruthSource:
        """按 type 构建对应的 TruthSource 实例。"""
        t = self.type.lower()
        if t == "file":
            # file 型走 LogTruthSource（默认 parser 取尾部原文），
            # 路径从 path_map 取（单条声明时 path 也接受）
            pmap = dict(self.path_map)
            if self.path and self.key not in pmap:
                pmap[self.key] = self.path
            return LogTruthSource(path_map=pmap, name=f"file:{self.key}", tail_lines=200)
        if t == "http":
            pmap = dict(self.path_map)
            if self.path and self.key not in pmap:
                pmap[self.key] = self.path
            return HttpTruthSource(
                base_url=self.base_url or "",
                path_map=pmap,
                name=f"http:{self.key}",
                timeout=self.timeout,
            )
        if t == "sql":
            qmap = dict(self.query_map)
            if self.path and self.key not in qmap:
                qmap[self.key] = self.path
            # SQL 需要外部执行器 —— 注册表阶段不构造，
            # 由调用方通过 build_sql_sources 提供 executor
            raise ValueError(
                f"sql 型真值源 '{self.key}' 需要外部 executor，"
                "请用 build_sql_sources() 构造"
            )
        if t == "dict":
            return DictTruthSource(data={self.key: self.extras.get("data")}, name=f"dict:{self.key}")
        raise ValueError(f"未知的真值源类型: {self.type!r}（支持 file / http / sql / dict）")


@dataclass
class TruthRegistry:
    """真值源注册表。"""

    entries: dict[str, RegistryEntry] = field(default_factory=dict)
    source_path: str = ""

    def get(self, key: str) -> RegistryEntry | None:
        return self.entries.get(key)

    def build_sources(self, sql_executors: dict[str, Any] | None = None) -> dict[str, TruthSource]:
        """构建所有可构建的真值源（sql 型需提供 executor）。"""
        out: dict[str, TruthSource] = {}
        for key, e in self.entries.items():
            if e.type.lower() == "sql":
                executor = (sql_executors or {}).get(key)
                if executor is None:
                    continue
                qmap = dict(e.query_map)
                if e.path and key not in qmap:
                    qmap[key] = e.path
                out[key] = SqlTruthSource(query_map=qmap, executor=executor, name=f"sql:{key}")
                continue
            try:
                out[key] = e.build()
            except (ValueError, KeyError):
                # 构建不了的类型静默跳过 —— 注册表不该让整条用例跑不起来
                continue
        return out

    def composite(self, sql_executors: dict[str, Any] | None = None) -> CompositeTruthSource:
        return CompositeTruthSource(self.build_sources(sql_executors))


def load_registry(path: str | Path = DEFAULT_REGISTRY_PATH) -> TruthRegistry:
    """读 YAML 注册表。文件不存在时返回空注册表（不报错 —— 注册表是可选增强）。"""
    import yaml

    p = Path(path)
    if not p.is_file():
        return TruthRegistry(entries={}, source_path=str(p))

    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    entries: dict[str, RegistryEntry] = {}
    for key, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        known = {"type", "description", "path", "base_url", "path_map", "query_map", "timeout"}
        extras = {k: v for k, v in cfg.items() if k not in known}
        entries[str(key)] = RegistryEntry(
            key=str(key),
            type=str(cfg.get("type", "file")),
            description=str(cfg.get("description", "")),
            path=cfg.get("path"),
            base_url=cfg.get("base_url"),
            path_map=dict(cfg.get("path_map") or {}),
            query_map=dict(cfg.get("query_map") or {}),
            timeout=float(cfg.get("timeout", 15.0)),
            extras=extras,
        )
    return TruthRegistry(entries=entries, source_path=str(p))


def collect_db_state(
    registry: TruthRegistry,
    sql_executors: dict[str, Any] | None = None,
    max_bytes: int = 20000,
) -> dict[str, Any]:
    """采集注册表声明的全部真值源快照，转成 Evidence.db_state 形态。

    每条记录形如：
    ```json
    {"value": ..., "source": "...", "collected_at": ..., "error": null}
    ```

    **取证失败不中断** —— 记录带 error 字段，judge 可把「取不到」当作一种证据。
    """
    if not registry.entries:
        return {}

    out: dict[str, Any] = {}
    for key, src in registry.build_sources(sql_executors).items():
        ev: TruthEvidence = src.snapshot(key)
        rec = ev.to_dict()
        # 大文件裁剪 —— db_state 会进 prompt，不能无限大
        val = rec.get("value")
        if isinstance(val, str) and len(val) > max_bytes:
            rec["value"] = val[:max_bytes] + f"...[截断，共 {len(val)} 字符]"
        elif isinstance(val, (dict, list)):
            try:
                s = json.dumps(val, ensure_ascii=False)
                if len(s) > max_bytes:
                    rec["value"] = {
                        "_truncated": True,
                        "_original_bytes": len(s),
                        "_preview": s[:max_bytes],
                    }
            except (TypeError, ValueError):
                pass
        out[key] = rec
    return out


def registry_enabled() -> bool:
    """注册表是否启用（环境变量可强制关闭）。

    环境变量 `NLaut_TRUTH_REGISTRY` 显式设为 0/off 时关闭。
    """
    v = os.environ.get("NLaut_TRUTH_REGISTRY", "").strip().lower()
    return v not in ("0", "off", "false", "no")
