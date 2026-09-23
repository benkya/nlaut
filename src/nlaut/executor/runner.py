"""L4 执行器桩（M2）：收集 IR → 驱动通道执行 → 产出 Evidence。

职责边界：
- flaky 统计：重试通过标 flaky 单独统计，不淹没真实失败率
- 产出 Evidence（截图/dom_state/响应/db_state）供 L5 判定
"""


def run(case_id: str, target: str = "web") -> None:
    raise NotImplementedError("M2: 执行器待实现")
