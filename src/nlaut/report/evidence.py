"""L6 证据存储：判定证据包落盘，报告证据链的数据源。

每个 (case_id, run_id) 一个目录，evidence.json 全量留痕；
截图等二进制由执行器写入同目录并相对引用。
"""

from __future__ import annotations

import time
from pathlib import Path

from ..judge.protocol import Evidence


class EvidenceStore:
    def __init__(self, root: str | Path = Path("artifacts/evidence")) -> None:
        self.root = Path(root)

    def save(self, evidence: Evidence, run_id: str | None = None) -> str:
        """落盘证据包，返回引用路径（进 Verdict.evidence_refs）。"""
        run_id = run_id or time.strftime("%Y%m%d-%H%M%S")
        ref_dir = self.root / evidence.case_id / run_id
        ref_dir.mkdir(parents=True, exist_ok=True)
        ref = ref_dir / "evidence.json"
        ref.write_text(evidence.model_dump_json(indent=2), encoding="utf-8")
        return str(ref)

    def load(self, ref: str | Path) -> Evidence:
        return Evidence.model_validate_json(Path(ref).read_text(encoding="utf-8"))
