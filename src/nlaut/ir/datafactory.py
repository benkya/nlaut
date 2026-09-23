"""L3 数据工厂（MVP 最小实现）：按 data_ref tag 供给测试数据。

MVP 阶段：data/datasets.yaml 单文件维护 tag → 字段映射。
M4 再升级为 faker + 业务规则 + DB 快照/回滚。
铁律不变：用例不内嵌数据，只引用 tag。
"""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_DATA_FILE = Path(__file__).resolve().parents[3] / "data" / "datasets.yaml"


class DataFactory:
    def __init__(self, data_file: str | Path = DEFAULT_DATA_FILE) -> None:
        self.data_file = Path(data_file)

    def build(self, data_ref: str) -> dict[str, str]:
        """返回指定 tag 的数据集字段；tag 不存在时报错（禁止猜测）。"""
        if not self.data_file.exists():
            raise FileNotFoundError(f"数据集文件不存在: {self.data_file}")
        datasets = yaml.safe_load(self.data_file.read_text(encoding="utf-8")) or {}
        if data_ref not in datasets:
            raise KeyError(f"未定义的数据 tag: {data_ref!r}（已定义: {sorted(datasets)}）")
        return {k: str(v) for k, v in datasets[data_ref].items()}
