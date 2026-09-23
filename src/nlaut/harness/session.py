"""L1 会话管理：append-only JSONL 日志，可回放、可追溯。

记录模型看到的一切（提示词、工具调用与结果）——DeepSeek Harness 的
append-only 会话日志模式的最小实现。生成过程的留痕即审计材料。
"""

from __future__ import annotations

import json
import time
from pathlib import Path


class SessionLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = 0
        if self.path.exists():  # 重开日志时延续序号，保证 append-only
            with self.path.open(encoding="utf-8") as f:
                self._seq = sum(1 for line in f if line.strip())

    def record(self, event: dict) -> dict:
        """追加一条事件（自动加 seq/ts），返回完整条目。"""
        self._seq += 1
        entry = {"seq": self._seq, "ts": round(time.time(), 3), **event}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def replay(self) -> list[dict]:
        """按序回放全部事件（会话审计 / 失败回传模型重生成均基于此）。"""
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
