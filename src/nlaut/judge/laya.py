"""L5 System One 判定引擎：laya-mlx（本地，Apple Silicon，0 输出 token）。

Noul/Choice/Score 是 Laya 的原生题型：
- noul   → 0-1 概率，confidence 为校准置信度（max(p, 1-p)）
- choice → 闭合选项集概率分布，confidence 取选中项概率
- score  → 有序量表期望分，confidence 取分布峰值
jev.py 将来以同一协议接云 API，切换零改动。

state 构造：Evidence 各字段渲染为文本（截图路径之外的全部证据），
Laya 是纯文本决策模型——视觉判定请走 MlxVlmJudge。
"""

from __future__ import annotations

from pathlib import Path

from .protocol import Evidence, Question, Verdict

DEFAULT_MODEL = "aac6fef/laya-multilingual-mlx"  # HF id；若本地缓存已有则零下载


def render_state(evidence: Evidence) -> str:
    """把 Evidence 渲染为 Laya 的 state 文本（可审计、可回放）。

    v0.2.0：API 通道证据优先渲染 llm_response（模型文本输出）与 conversation
    （用户输入），再渲染原始 response JSON。Laya 是纯文本决策模型，
    长 prompt 优先，避免 choices 包装结构稀释关键信息。
    """
    parts = [f"case: {evidence.case_id}"]
    # API 通道（v0.2.0）：用户 prompt + 模型回答优先渲染
    if evidence.conversation:
        user_turns = [m.get("content", "") for m in evidence.conversation
                      if m.get("role") == "user"]
        if user_turns:
            parts.append(f"用户输入: {' / '.join(t[:200] for t in user_turns)}")
    if evidence.llm_response:
        parts.append(f"模型回答: {evidence.llm_response[:600]}")
    if evidence.tool_calls:
        calls = "; ".join(
            f"{c.get('function', {}).get('name')}({c.get('function', {}).get('arguments', '')[:100]})"
            for c in evidence.tool_calls
        )
        parts.append(f"工具调用: {calls}")
    if evidence.latency_ms is not None:
        parts.append(f"响应延迟: {evidence.latency_ms:.0f}ms")
    # Web 通道原有渲染
    if evidence.dom_state:
        dom = "; ".join(f"{sel} -> {txt[:80]}" for sel, txt in evidence.dom_state.items())
        parts.append(f"DOM 可见状态: {dom}")
    if evidence.response is not None:
        parts.append(f"API 响应: {str(evidence.response)[:300]}")
    if evidence.db_state is not None:
        parts.append(f"数据库状态: {str(evidence.db_state)[:300]}")
    if evidence.screenshot:
        parts.append(f"截图(供人工复核): {evidence.screenshot}")
    return "\n".join(parts)


class LayaJudge:
    """实现 Judge 协议的本地 System One 引擎。Agent 懒加载。"""

    engine = "laya"

    def __init__(self, model: str | Path = DEFAULT_MODEL) -> None:
        self.model_id = str(model)
        self._agent = None

    def _ensure_loaded(self) -> None:
        if self._agent is not None:
            return
        import laya_mlx

        agent = laya_mlx.load(self.model_id)
        assert agent is not None
        self._agent = agent

    def _question_def(self, question: Question) -> dict:
        if question.kind == "choice":
            if not question.options:
                raise ValueError("choice 问题必须提供 options")
            return {
                "type": "choice",
                "instructions": question.text,
                "criteria": {opt: opt for opt in question.options},
            }
        if question.kind == "score":
            return {
                "type": "score",
                "instructions": question.text,
                "criteria": question.context.get(
                    "legend", ["完全不符合", "基本不符合", "部分符合", "基本符合", "完全符合"]
                ),
            }
        return {"type": "noul", "instructions": question.text}

    def judge(self, evidence: Evidence, question: Question) -> Verdict:
        self._ensure_loaded()
        agent = self._agent
        assert agent is not None
        state = render_state(evidence)
        resp = agent.system_one(state, {"q1": self._question_def(question)})
        ans = resp["answers"]["q1"]
        raw = dict(ans)
        raw["usage"] = resp.get("usage", {})

        if question.kind == "noul":
            return Verdict(
                engine=self.engine,
                kind="noul",
                value=float(ans["noul"]),
                confidence=float(ans["confidence"]),
                evidence_refs=[evidence.case_id],
                raw=raw,
            )
        if question.kind == "choice":
            probs = ans.get("probabilities", {})
            chosen = ans["choice"]
            return Verdict(
                engine=self.engine,
                kind="choice",
                value=chosen,
                confidence=float(probs.get(chosen, ans.get("confidence", 0.5))),
                evidence_refs=[evidence.case_id],
                raw=raw,
            )
        # score
        return Verdict(
            engine=self.engine,
            kind="score",
            value=float(ans["score"]),
            confidence=max(ans["probabilities"].values()),
            evidence_refs=[evidence.case_id],
            raw=raw,
        )
