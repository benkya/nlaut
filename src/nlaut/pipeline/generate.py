"""L2 IR 生成桩（M1）：四元组 + RAG 检索 → IR。

RAG 建库对象：历史 IR（cases/）、接口文档、领域术语表、Bug 库。
禁止依赖模型训练记忆生成断言预期值。
"""

from ..ir.model import TestCaseIR


def generate(quad, retrieved: list[TestCaseIR]) -> TestCaseIR:
    raise NotImplementedError("M1: IR 生成待实现")
