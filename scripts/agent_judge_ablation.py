"""Agent-as-Judge vs LLM-as-Judge 消融对照实验。

目的：验证「Agent 的优势来自**自主取证**，而非模型更强」——
这直接支撑「软件工厂该不该用 Agent-as-Judge」的决策。

对照设计（严格同模型、同 rubric、只差「能否调工具」）：
- **有工具组（Agent）**：judge 可调只读取证工具，自己拿证据
- **无工具组（LLM Judge）**：同一模型、同一 system prompt、同一样本，
  但**不提供 tools**，judge 只能读预置在 brief 里的 Evidence 摘要

两组唯一差异 = `tools` 参数为空与否。这样差异只能归因于「自主取证」。

三种证据呈现档位（用于定位「Agent 赢在哪」）：
- `brief`：只给被测系统自述（llm_response）—— 最贴近真实 LLM-as-Judge 场景
- `brief+db`：额外把 db_state 快照塞进 brief（此时无工具组也能看见表）
- `tools`：Agent 组自主取证

用法：
    cd ~/workspace/nlaut
    .venv/bin/python scripts/agent_judge_ablation.py --repeats 3
    .venv/bin/python scripts/agent_judge_ablation.py --repeats 3 \
        --out artifacts/agent_judge_ablation.json
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from nlaut.judge.agent_judge import AgentJudge, build_agent_judge_from_env
from nlaut.judge.agent_tools import (
    DEFAULT_TOOLS,
    ToolPolicy,
    build_env_tool_impls,
)
from nlaut.judge.protocol import Evidence, Question
from tests.agent_judge_test import GOLDEN_SAMPLES, _evidence_for

# 无工具组用的 rubric —— 与 Agent 组**逐字相同**，只是删掉取证相关约束，
# 换成「你只能依据给定信息判断」。
LLM_JUDGE_SYSTEM = (
    "你是取证型判定器（Agent-as-Judge）。\n"
    "规则：\n"
    "1. 必须先用工具取证，再给结论；不得凭空判断。\n"
    "2. 结论基于最终/实际状态，不基于中间步骤。\n"
    "3. 探索要完整：找不到目标前，先穷举相关视图/表/接口再判失败。\n"
    "4. 无法取证时判为不确定并说明缺什么证据。\n"
    "5. **取证要高效**：能用一次工具调用拿到的证据，不要拆成多次；\n"
    "   优先查最可能直接证伪目标的那一项（如验证落库先查表）。\n"
    "   工具返回 error 或空结果时，不要反复重试同一个来源。\n"
    "6. **取证完成后立即给结论**，不要再补充额外检查。\n"
    "输出格式（严格三行，不要额外章节）：\n"
    "judgement: true|false\n"
    "理由: <基于你实际取证到的证据，一句话点明关键证据>\n"
    "CONFIDENCE: High|Medium|Low"
)


def build_brief_evidence(sample: dict, mode: str) -> Evidence:
    """按档位构造无工具组可见的 Evidence。

    - `brief`：只有被测系统自述（llm_response）—— LLM Judge 最常见处境
    - `brief+db`：额外给 db_state 快照（让无工具组也能看见表）
    """
    ev = _evidence_for(sample)
    if mode == "brief":
        return ev.model_copy(update={"db_state": None})
    return ev


def run_one(
    judge: AgentJudge,
    sample: dict,
    mode: str,
    with_tools: bool,
    max_steps: int,
) -> dict:
    """跑一次判定，返回结果 dict。"""
    ev = build_brief_evidence(sample, mode)
    q = Question(
        kind="noul",
        text=sample["objective"],
        context={"max_steps": max_steps, "system_prompt": LLM_JUDGE_SYSTEM},
    )
    t0 = time.time()
    v = judge.judge(ev, q)
    dt = time.time() - t0
    return {
        "value": float(v.value) >= 0.5,
        "confidence": v.confidence,
        "degraded": "degraded" in v.raw,
        "rounds": v.raw.get("rounds"),
        "tool_calls": v.raw.get("tool_calls_total", 0),
        "cost_usd": v.raw.get("cost_usd", 0.0),
        "elapsed_ms": dt * 1000,
        "reason": str(v.raw.get("reason", ""))[:400],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent-as-Judge vs LLM-as-Judge 消融对照")
    ap.add_argument("--repeats", type=int, default=3, help="每样本重复轮次")
    ap.add_argument("--model", default="deepseek-v4-flash", help="模型 key")
    ap.add_argument("--max-steps", type=int, default=4)
    ap.add_argument("--out", default="", help="结果 JSON 输出路径")
    args = ap.parse_args()

    policy = ToolPolicy()
    # 无工具组：tools=[] → judge 拿不到任何工具，只能读 brief
    llm_impls = build_env_tool_impls(policy=policy)
    agent_impls = build_env_tool_impls(policy=policy)

    arms = {
        # arm 名 → (是否给工具, 证据档位)
        "agent_tools": (True, "tools"),
        "llm_brief": (False, "brief"),
        "llm_brief_db": (False, "brief+db"),
    }

    results: dict[str, dict[str, list[dict]]] = collections.defaultdict(
        lambda: collections.defaultdict(list)
    )
    t0 = time.time()

    for arm, (with_tools, mode) in arms.items():
        tools = DEFAULT_TOOLS if with_tools else []
        judge = build_agent_judge_from_env(
            tools=tools,
            tool_impls=agent_impls if with_tools else llm_impls,
            model_key=args.model,
            policy=policy,
        )
        for r in range(1, args.repeats + 1):
            for s in GOLDEN_SAMPLES:
                res = run_one(judge, s, mode, with_tools, args.max_steps)
                results[arm][s["case_id"]].append(res)
            print(
                f"[{arm}] 轮 {r}/{args.repeats} 完成（累计 {time.time() - t0:.0f}s）",
                flush=True,
            )

    # --- 统计 ---
    def summarize(arm: str) -> dict:
        total = 0
        wrong = 0
        degraded = 0
        flips = 0
        lat: list[float] = []
        cost = 0.0
        trap_total = 0
        trap_wrong = 0
        trap_fp = 0  # 陷阱样本被误判为「通过」= 假阳性（最危险）
        for cid, runs in results[arm].items():
            sample = next(x for x in GOLDEN_SAMPLES if x["case_id"] == cid)
            exp = sample["expected"]
            vals = [x["value"] for x in runs]
            total += len(vals)
            wrong += sum(1 for v in vals if v != exp)
            degraded += sum(1 for x in runs if x["degraded"])
            lat += [x["elapsed_ms"] for x in runs]
            cost += sum(x["cost_usd"] for x in runs)
            if len(set(vals)) > 1:
                flips += 1
            if sample["category"].startswith("trap") or sample["category"] in (
                "field_semantics",
                "locate_ambiguity",
            ):
                trap_total += len(vals)
                trap_wrong += sum(1 for v in vals if v != exp)
                # 假阳性：期望 False 却判 True
                trap_fp += sum(1 for v in vals if v != exp and v is True)
        lat.sort()
        n = len(lat)
        return {
            "total": total,
            "wrong": wrong,
            "accuracy": (total - wrong) / total if total else 0.0,
            "flip_samples": flips,
            "flip_rate": flips / len(results[arm]) if results[arm] else 0.0,
            "degraded": degraded,
            "latency_median_ms": round(statistics.median(lat), 1) if n else 0.0,
            "latency_p95_ms": round(lat[int(n * 0.95)], 1) if n else 0.0,
            "cost_total_usd": round(cost, 6),
            "cost_per_judgement_usd": round(cost / total, 6) if total else 0.0,
            "trap_total": trap_total,
            "trap_wrong": trap_wrong,
            "trap_accuracy": (trap_total - trap_wrong) / trap_total if trap_total else 0.0,
            "trap_false_positive": trap_fp,
        }

    summary = {arm: summarize(arm) for arm in arms}

    # --- 打印 ---
    print("\n" + "=" * 78)
    print("Agent-as-Judge vs LLM-as-Judge 消融对照（同模型 · 同 rubric · 只差「能否取证」）")
    print("=" * 78)
    header = f"{'臂':16s} {'准确率':>8s} {'陷阱准确':>9s} {'陷阱FP':>7s} {'翻转率':>8s} {'降级':>5s} {'中位ms':>9s} {'成本$':>9s}"
    print(header)
    print("-" * 78)
    for arm in arms:
        s = summary[arm]
        print(
            f"{arm:16s} {s['accuracy']:8.1%} {s['trap_accuracy']:9.1%} "
            f"{s['trap_false_positive']:7d} {s['flip_rate']:8.1%} {s['degraded']:5d} "
            f"{s['latency_median_ms']:9.0f} {s['cost_per_judgement_usd']:9.5f}"
        )

    # 关键对比
    a = summary["agent_tools"]
    lb = summary["llm_brief"]
    ld = summary["llm_brief_db"]
    print("\n" + "-" * 78)
    print("关键对比")
    print("-" * 78)
    print(
        f"Agent（能取证）      vs LLM Judge（只有自述）："
        f"准确率 {a['accuracy']:.1%} vs {lb['accuracy']:.1%}"
        f"（{(a['accuracy'] - lb['accuracy']) * 100:+.1f}pp）"
    )
    print(
        f"                        陷阱准确率 {a['trap_accuracy']:.1%} vs {lb['trap_accuracy']:.1%}"
        f"（{(a['trap_accuracy'] - lb['trap_accuracy']) * 100:+.1f}pp）"
        f"  陷阱假阳性 {a['trap_false_positive']} vs {lb['trap_false_positive']}"
    )
    print(
        f"LLM Judge（额外给 db 快照）      ："
        f"准确率 {ld['accuracy']:.1%} 陷阱准确率 {ld['trap_accuracy']:.1%}"
        f" 陷阱假阳性 {ld['trap_false_positive']}"
    )
    print(
        f"\n成本/延迟：Agent ${a['cost_per_judgement_usd']:.5f} / {a['latency_median_ms']:.0f}ms"
        f"  vs  LLM ${lb['cost_per_judgement_usd']:.5f} / {lb['latency_median_ms']:.0f}ms"
    )

    out = {
        "repeats": args.repeats,
        "model": args.model,
        "max_steps": args.max_steps,
        "samples": len(GOLDEN_SAMPLES),
        "arms": summary,
        "detail": {
            arm: {cid: rs for cid, rs in samples.items()} for arm, samples in results.items()
        },
    }
    if args.out:
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入: {outp}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
