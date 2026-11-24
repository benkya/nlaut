"""置信度路由：把 Verdict 映射为 auto_pass / auto_fail / human_review。

规则（docs/judge-protocol.md 的落地）：
- deterministic：value>=0.5 直接裁决（零 AI 风险，不进人工队列）
- confidence >= 0.9：达到阈值 auto_pass，未达 auto_fail
- 0.5 <= confidence < 0.9：human_review（人工仲裁队列，附证据包）
- confidence < 0.5：human_review（引擎自认不确定）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .protocol import Verdict

RouteStatus = Literal["auto_pass", "auto_fail", "human_review"]


@dataclass
class RouteDecision:
    status: RouteStatus
    reason: str


def route(verdict: Verdict, threshold: float = 0.9) -> RouteDecision:
    if verdict.engine == "deterministic":
        # 降级态必须转人工，不能判通过。
        # 降级约定：value=0.5 + confidence<1.0（基线缺失 / 引擎不可用 / 证据不足）。
        # 旧实现只看 value>=0.5 就 auto_pass，会把 value=0.5 的降级态判成「通过」——
        # 2026-11-13 实测发现（视觉回归基线缺失时误报 auto_pass）。
        # deterministic 的正常判定 confidence 恒为 1.0，故此判断不影响既有行为。
        if verdict.confidence < 1.0:
            return RouteDecision(
                "human_review",
                f"确定性判定降级（confidence={verdict.confidence:.2f}），转人工仲裁",
            )
        ok = float(verdict.value) >= 0.5
        return RouteDecision(
            "auto_pass" if ok else "auto_fail", "确定性判定，value>=0.5 即裁决"
        )
    if verdict.confidence < 0.5:
        return RouteDecision("human_review", f"引擎自报不确定 confidence={verdict.confidence:.2f}")
    if verdict.confidence < 0.9:
        return RouteDecision(
            "human_review", f"置信度中带 confidence={verdict.confidence:.2f}，转人工仲裁"
        )
    ok = float(verdict.value) >= threshold
    return RouteDecision(
        "auto_pass" if ok else "auto_fail",
        f"高置信 confidence={verdict.confidence:.2f}，value={verdict.value} 对 threshold={threshold}",
    )
