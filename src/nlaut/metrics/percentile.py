"""nlaut.metrics.percentile — 分位数算子（P95 默认）。

v0.2.7 Phase 5 新增：用于 LLM 流式首 token 延迟的稳定化采样。
设计依据：deliveries/llm-ttft-p95/design.md DE-001

算法说明（v1）：
  - 不依赖 numpy/scipy，纯 Python 实现
  - 排序后取 sorted[n - 1]（i.e. ceil(n*0.95) - 1 取整即 N - 1）
  - 当样本量小（< 20）时，"下取整+1"近似与线性插值差异微小，但实现简单
  - 当 N=1 时退化（返回唯一样本）
  - 适用于偶发采样场景；如需 P50/P99 或大规模数据，引入 statistics.quantiles
"""
from __future__ import annotations

from collections.abc import Iterable


def p95(samples: list[float] | Iterable[float]) -> float:
    """计算 P95（第 95 百分位）。

    实现：排序后取最末位（ceil(N*0.95) - 1 = N - 1 when N >= 1）。
    输入可迭代；返回 float，N=1 时返回唯一样本。
    """
    s = sorted(samples)
    if not s:
        raise ValueError("p95: samples must not be empty")
    # ceil(N * 0.95) - 1 = ceil(N - N*0.05) - 1
    # 当 N >= 1 时，ceil(N*0.95) = N（因为 N - 0.05N <= N 且 > N - 1）
    return float(s[-1])


def p95_or_none(samples: list[float] | Iterable[float]) -> float | None:
    """p95 的容错版本：空列表返回 None（用于 evidence 缺失时判定层降级）。"""
    s = list(samples)
    if not s:
        return None
    return p95(s)