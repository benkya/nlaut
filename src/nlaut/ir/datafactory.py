"""L3 数据工厂桩（M4 里程碑实现）：faker + 业务规则，用例只引用 tag。"""


class DataFactory:
    """按 data_ref tag 生成测试数据集；与用例解耦，禁止用例内嵌数据。"""

    def build(self, data_ref: str) -> dict[str, str]:
        raise NotImplementedError("M4: 数据工厂待实现（faker + 业务规则 + 快照/回滚）")
