"""tests/metrics_test.py — nlaut P95 算子单元测试。

设计依据：deliveries/llm-ttft-p95/design.md DE-001
验收标准：US-001 AC-001/002/003
"""
from __future__ import annotations

from nlaut.metrics.percentile import p95, p95_or_none


class TestP95Operator:
    def test_p95_5_samples(self):
        """5 个样本 [100,200,300,400,500]，P95 = 500（最末位）。"""
        assert p95([100, 200, 300, 400, 500]) == 500

    def test_p95_10_samples(self):
        """10 个样本 [100,200,...,1000]，P95 = 1000。"""
        assert p95(list(range(100, 1001, 100))) == 1000

    def test_p95_single_sample(self):
        """N=1 退化，N*0.95=0.95，下取整+1=1，取 sorted[0]=唯一样本。"""
        assert p95([1500]) == 1500

    def test_p95_unsorted_input(self):
        """输入未排序，函数内部排序，行为一致。"""
        assert p95([500, 100, 300, 200, 400]) == 500

    def test_p95_or_none_empty(self):
        """空列表返回 None（用于 evidence 缺失时的降级路径）。"""
        assert p95_or_none([]) is None

    def test_p95_or_none_normal(self):
        """非空列表返回 P95。"""
        assert p95_or_none([100, 200, 300, 400, 500]) == 500