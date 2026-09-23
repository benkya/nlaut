"""L2 语义解析桩（M1）：非结构化输入 → 场景四元组。

重点不是"提取已有需求"而是"推断隐含测试条件"：
用户说"测一下登录"，需覆盖正常登录/错误密码/空用户名/账号锁定等隐含场景。
"""

from ..ir.model import Quad


def extract_quad(raw_input: str) -> Quad:
    raise NotImplementedError("M1: 四元组抽取待实现")
