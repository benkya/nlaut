"""IR 库读写：扫描 cases/、加载校验、漂移审计（模型升级后的 regen diff）。

**非用例目录排除**：`cases/` 下可能同时存放用例的**测试资产/夹具**（如
`cases/cecoder/assets/变更资产清单.yaml` —— 供 upload 步骤上传的文件）。
这些不是 IR，若一起塞进 TestCaseIR 校验会 fail-fast 炸掉整库
（2026-11-24 实测：assets 里的资产 YAML 缺少 id/title/steps → 全库加载失败）。

排除按**路径中任一目录名**匹配，而不是只匹配末级目录——`cases/x/assets/y/*.yaml`
同样被排除。若某天要用例文件本身叫 assets.yaml，它不在 assets 目录内，不受影响。
"""

from __future__ import annotations

from pathlib import Path

from .model import TestCaseIR

#: 用例库内不参与 IR 校验的子目录名
NON_CASE_DIRS: frozenset[str] = frozenset({"assets", "fixtures", "data", "attachments"})


class IRStore:
    def __init__(self, root: str | Path = Path("cases")) -> None:
        self.root = Path(root)

    def _is_case_file(self, path: Path) -> bool:
        """路径中不含非用例目录名才视为 IR 文件。"""
        return not (set(path.relative_to(self.root).parts[:-1]) & NON_CASE_DIRS)

    def _yaml_files(self) -> list[Path]:
        files = [*self.root.rglob("*.yaml"), *self.root.rglob("*.yml")]
        return sorted(p for p in files if self._is_case_file(p))

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