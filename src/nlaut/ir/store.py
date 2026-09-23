"""IR 库读写：扫描 cases/、加载校验、漂移审计（模型升级后的 regen diff）。"""

from __future__ import annotations

from pathlib import Path

from .model import TestCaseIR


class IRStore:
    def __init__(self, root: str | Path = Path("cases")) -> None:
        self.root = Path(root)

    def _yaml_files(self) -> list[Path]:
        return sorted([*self.root.rglob("*.yaml"), *self.root.rglob("*.yml")])

    def load_all(self) -> list[TestCaseIR]:
        """加载并校验库内全部 IR；任一文件非法即抛 ValidationError（fail-fast）。"""
        return [TestCaseIR.from_yaml_file(p) for p in self._yaml_files()]

    def get(self, case_id: str) -> TestCaseIR | None:
        for ir in self.load_all():
            if ir.id == case_id:
                return ir
        return None

    def diff_ids(self, other_root: str | Path) -> set[str]:
        """比较两处 IR 库的 id 集合差异（regen 前后 / 分支对比审计用）。"""
        mine = {ir.id for ir in self.load_all()}
        other = {ir.id for ir in IRStore(other_root).load_all()}
        return mine ^ other
