"""1° 确定性判定：selector 文案包含匹配（零 AI 成本、零幻觉面）。

约定：
- Question.kind = "noul"，Question.text = 期望文案
- Question.context = {"selector": ..., "negated": ...}
- Evidence.dom_state = {selector: 元素可见文本}
"""

from __future__ import annotations

from .protocol import Evidence, Question, Verdict


class DeterministicJudge:
    engine = "deterministic"

    def judge(self, evidence: Evidence, question: Question) -> Verdict:
        selector = question.context.get("selector")
        negated = bool(question.context.get("negated", False))
        if question.kind != "noul" or not selector:
            raise ValueError("DeterministicJudge 仅支持携带 selector 的 noul 问题")
        visible = (evidence.dom_state or {}).get(selector) or ""
        matched = (question.text in visible) ^ negated
        return Verdict(
            engine=self.engine,
            kind="noul",
            value=1.0 if matched else 0.0,
            confidence=1.0,
            evidence_refs=[evidence.case_id],
            raw={
                "selector": selector,
                "expected": question.text,
                "negated": negated,
                "visible_text": visible[:200],
            },
        )
