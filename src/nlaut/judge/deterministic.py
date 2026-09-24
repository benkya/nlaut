"""1° 确定性判定：零 AI 成本、零幻觉面。

v0.2.0 扩展：从仅支持 DOM selector 文案匹配，扩展为 7 种判定模式：

| mode                  | 输入来源                    | 适用断言                |
|-----------------------|---------------------------|------------------------|
| dom (原有)             | evidence.dom_state[sel]   | text_visible / attribute |
| response_exact        | evidence.llm_response     | 精确匹配（数学/事实）      |
| response_contains     | evidence.llm_response     | 关键词匹配（知识/代码）     |
| response_not_contains | evidence.llm_response     | 反向验证（安全性）         |
| response_json_schema  | evidence.llm_response     | JSON 结构验证（格式约束）  |
| tool_call_params      | evidence.tool_calls       | 工具调用参数验证           |
| response_time         | evidence.latency_ms       | 性能断言                 |

约定：
- Question.kind = "noul"
- Question.context["mode"] 指定判定模式（默认 "dom" 向后兼容）
- Evidence 对应字段提供判定输入
"""

from __future__ import annotations

import json

from .protocol import Evidence, Question, Verdict


class DeterministicJudge:
    """确定性判定引擎：7 种模式，全部零 AI 成本、置信度恒为 1.0。"""

    engine = "deterministic"

    def judge(self, evidence: Evidence, question: Question) -> Verdict:
        ctx = question.context
        mode = ctx.get("mode", "dom")

        if mode == "dom":
            return self._judge_dom(evidence, question, ctx)
        if mode == "response_exact":
            return self._judge_response_exact(evidence, question, ctx)
        if mode == "response_contains":
            return self._judge_response_contains(evidence, question, ctx)
        if mode == "response_not_contains":
            return self._judge_response_not_contains(evidence, question, ctx)
        if mode == "response_json_schema":
            return self._judge_response_json_schema(evidence, question, ctx)
        if mode == "tool_call_params":
            return self._judge_tool_call_params(evidence, question, ctx)
        if mode == "response_time":
            return self._judge_response_time(evidence, question, ctx)

        raise ValueError(f"DeterministicJudge 未知判定模式: {mode!r}")

    # --- DOM 模式（原有，向后兼容）---

    def _judge_dom(self, evidence: Evidence, question: Question, ctx: dict) -> Verdict:
        selector = ctx.get("selector")
        negated = bool(ctx.get("negated", False))
        if question.kind != "noul" or not selector:
            raise ValueError("dom 模式需要 context.selector 和 noul 问题")
        visible = (evidence.dom_state or {}).get(selector) or ""
        matched = (question.text in visible) ^ negated
        return Verdict(
            engine=self.engine,
            kind="noul",
            value=1.0 if matched else 0.0,
            confidence=1.0,
            evidence_refs=[evidence.case_id],
            raw={
                "mode": "dom",
                "selector": selector,
                "expected": question.text,
                "negated": negated,
                "visible_text": visible[:200],
            },
        )

    # --- LLM 响应精确匹配 ---

    def _judge_response_exact(self, evidence: Evidence, question: Question, ctx: dict) -> Verdict:
        text = (evidence.llm_response or "").strip()
        expected = question.text.strip()
        case_sensitive = ctx.get("case_sensitive", False)
        if not case_sensitive:
            text_cmp = text.lower()
            expected_cmp = expected.lower()
        else:
            text_cmp = text
            expected_cmp = expected
        matched = text_cmp == expected_cmp
        return Verdict(
            engine=self.engine,
            kind="noul",
            value=1.0 if matched else 0.0,
            confidence=1.0,
            evidence_refs=[evidence.case_id],
            raw={
                "mode": "response_exact",
                "expected": expected,
                "actual": text[:200],
                "case_sensitive": case_sensitive,
            },
        )

    # --- LLM 响应关键词匹配 ---

    def _judge_response_contains(self, evidence: Evidence, question: Question, ctx: dict) -> Verdict:
        text = (evidence.llm_response or "").lower()
        keywords = ctx.get("keywords", [])
        min_match = ctx.get("min_match", 1)
        matched_keywords = [kw for kw in keywords if kw.lower() in text]
        matched = len(matched_keywords) >= min_match
        return Verdict(
            engine=self.engine,
            kind="noul",
            value=1.0 if matched else 0.0,
            confidence=1.0,
            evidence_refs=[evidence.case_id],
            raw={
                "mode": "response_contains",
                "keywords": keywords,
                "min_match": min_match,
                "matched_keywords": matched_keywords,
                "matched_count": len(matched_keywords),
                "response_preview": (evidence.llm_response or "")[:200],
            },
        )

    # --- LLM 响应反向验证 ---

    def _judge_response_not_contains(self, evidence: Evidence, question: Question, ctx: dict) -> Verdict:
        text = (evidence.llm_response or "").lower()
        forbidden = ctx.get("forbidden", [])
        violations = [fw for fw in forbidden if fw.lower() in text]
        matched = len(violations) == 0
        return Verdict(
            engine=self.engine,
            kind="noul",
            value=1.0 if matched else 0.0,
            confidence=1.0,
            evidence_refs=[evidence.case_id],
            raw={
                "mode": "response_not_contains",
                "forbidden": forbidden,
                "violations": violations,
                "response_preview": (evidence.llm_response or "")[:200],
            },
        )

    # --- LLM 响应 JSON 结构验证 ---

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        """剥离 markdown 代码围栏（```json ... ```）——LLM 输出 JSON 的通用习惯。"""
        stripped = text.strip()
        if stripped.startswith("```"):
            # 去掉首行 ``` 或 ```json
            lines = stripped.split("\n")
            if len(lines) >= 2:
                lines = lines[1:]
            # 去掉结尾 ```
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            stripped = "\n".join(lines).strip()
        return stripped

    def _judge_response_json_schema(self, evidence: Evidence, question: Question, ctx: dict) -> Verdict:
        text = self._strip_code_fence(evidence.llm_response or "")
        required_fields = ctx.get("required_fields", [])
        expected_values = ctx.get("expected_values", {})
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return Verdict(
                engine=self.engine,
                kind="noul",
                value=0.0,
                confidence=1.0,
                evidence_refs=[evidence.case_id],
                raw={
                    "mode": "response_json_schema",
                    "error": "response is not valid JSON",
                    "response_preview": text[:200],
                },
            )
        if not isinstance(parsed, dict):
            return Verdict(
                engine=self.engine, kind="noul", value=0.0, confidence=1.0,
                evidence_refs=[evidence.case_id],
                raw={"mode": "response_json_schema", "error": "JSON is not an object",
                     "response_preview": text[:200]},
            )
        missing = [f for f in required_fields if f not in parsed]
        value_errors = {
            k: {"expected": v, "actual": str(parsed.get(k))}
            for k, v in expected_values.items()
            if str(parsed.get(k)) != str(v)
        }
        matched = len(missing) == 0 and len(value_errors) == 0
        return Verdict(
            engine=self.engine,
            kind="noul",
            value=1.0 if matched else 0.0,
            confidence=1.0,
            evidence_refs=[evidence.case_id],
            raw={
                "mode": "response_json_schema",
                "required_fields": required_fields,
                "missing": missing,
                "expected_values": expected_values,
                "value_errors": value_errors,
                "parsed_keys": list(parsed.keys())[:20],
            },
        )

    # --- 工具调用参数验证 ---

    def _judge_tool_call_params(self, evidence: Evidence, question: Question, ctx: dict) -> Verdict:
        calls = evidence.tool_calls or []
        expected_fn = ctx.get("expected_function", "")
        expected_params = ctx.get("expected_params", {})
        matching = [
            c for c in calls
            if c.get("function", {}).get("name") == expected_fn
        ]
        if not matching:
            return Verdict(
                engine=self.engine,
                kind="noul",
                value=0.0,
                confidence=1.0,
                evidence_refs=[evidence.case_id],
                raw={
                    "mode": "tool_call_params",
                    "expected_function": expected_fn,
                    "actual_functions": [c.get("function", {}).get("name") for c in calls],
                    "error": "expected function not called",
                },
            )
        try:
            args = json.loads(matching[0]["function"]["arguments"])
        except (json.JSONDecodeError, KeyError, TypeError):
            return Verdict(
                engine=self.engine, kind="noul", value=0.0, confidence=1.0,
                evidence_refs=[evidence.case_id],
                raw={"mode": "tool_call_params", "error": "tool call arguments not parseable",
                     "raw_arguments": matching[0].get("function", {}).get("arguments", "")[:200]},
            )
        param_errors = {
            k: {"expected": v, "actual": str(args.get(k))}
            for k, v in expected_params.items()
            if str(args.get(k)) != str(v)
        }
        matched = len(param_errors) == 0
        return Verdict(
            engine=self.engine,
            kind="noul",
            value=1.0 if matched else 0.0,
            confidence=1.0,
            evidence_refs=[evidence.case_id],
            raw={
                "mode": "tool_call_params",
                "expected_function": expected_fn,
                "expected_params": expected_params,
                "actual_params": args,
                "param_errors": param_errors,
            },
        )

    # --- 响应时间断言 ---

    def _judge_response_time(self, evidence: Evidence, question: Question, ctx: dict) -> Verdict:
        latency_ms = evidence.latency_ms or 0
        max_seconds = ctx.get("max_seconds", 30.0)
        max_ms = max_seconds * 1000
        matched = latency_ms <= max_ms
        return Verdict(
            engine=self.engine,
            kind="noul",
            value=1.0 if matched else 0.0,
            confidence=1.0,
            evidence_refs=[evidence.case_id],
            raw={
                "mode": "response_time",
                "latency_ms": round(latency_ms, 1),
                "max_ms": round(max_ms, 1),
                "max_seconds": max_seconds,
            },
        )
