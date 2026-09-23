"""L4 web 通道桩（M2）：IR steps → Playwright 动作序列。

步骤翻译：nav→goto / fill→locator.fill($var 解析) / click→click /
wait_visible→expect(locator).to_be_visible(timeout_ms)。禁止生成硬编码 sleep。
"""

from ..ir.model import TestCaseIR


def execute(ir: TestCaseIR) -> None:
    raise NotImplementedError("M2: web 通道待实现")
