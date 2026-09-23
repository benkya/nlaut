"""L2 验证修正桩（M1/M2）：IR schema 校验 + 试执行 + 错误回传重新生成。

Harness 验证 Loop 的落地：失败信息回传模型，≤N 轮（默认 3），
超限转人工——禁止无限重试。
"""

MAX_ITER = 3


def verify_and_fix(ir_yaml: str) -> str:
    raise NotImplementedError("M1/M2: 验证 Loop 待实现")
