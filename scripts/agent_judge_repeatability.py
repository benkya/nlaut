"""Agent-as-Judge 重复轮次一致性度量（实测脚本）。

目的：量化「同输入 → 同判定」的稳定度（翻转率），这是软件工厂门禁
能否依赖 agent judge 作为阻断项的核心指标（对应 PoC 方案判据 C）。

用法：
    cd ~/workspace/nlaut && .venv/bin/python scripts/agent_judge_repeatability.py
    .venv/bin/python scripts/agent_judge_repeatability.py --repeats 5 --model deepseek-v4-flash
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from nlaut.judge.agent_judge import build_agent_judge_from_env
from nlaut.judge.agent_tools import DEFAULT_TOOLS, build_env_tool_impls
from nlaut.judge.protocol import Question
from tests.agent_judge_test import GOLDEN_SAMPLES, _evidence_for


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent-as-Judge 重复轮次一致性度量")
    ap.add_argument("--repeats", type=int, default=3, help="每样本重复轮次")
    ap.add_argument("--model", default="deepseek-v4-flash", help="模型 key（config/models.yaml）")
    ap.add_argument("--max-steps", type=int, default=4, help="单次判定最大取证步数")
    ap.add_argument("--out", default="", help="结果 JSON 输出路径")
    args = ap.parse_args()

    per: dict[str, list[dict]] = collections.defaultdict(list)
    t0 = time.time()

    for r in range(1, args.repeats + 1):
        for s in GOLDEN_SAMPLES:
            impls = build_env_tool_impls(db_state=s.get("db_state"))
            judge = build_agent_judge_from_env(
                tools=DEFAULT_TOOLS, tool_impls=impls, model_key=args.model
            )
            v = judge.judge(
                _evidence_for(s),
                Question(
                    kind="noul",
                    text=s["objective"],
                    context={"max_steps": args.max_steps},
                ),
            )
            per[s["case_id"]].append(
                {
                    "value": float(v.value) >= 0.5,
                    "degraded": "degraded" in v.raw,
                    "confidence": v.confidence,
                    "elapsed_ms": v.raw.get("elapsed_ms", 0),
                    "rounds": v.raw.get("rounds"),
                    "tools": [t["tool"] for t in v.raw.get("trace", [])],
                }
            )
        print(f"轮 {r}/{args.repeats} 完成（累计 {time.time() - t0:.0f}s）", flush=True)

    # --- 统计 ---
    flips: list[str] = []
    wrong = 0
    degraded = 0
    total = 0
    latencies: list[float] = []
    samples: list[dict] = []

    for cid, runs in per.items():
        exp = next(x for x in GOLDEN_SAMPLES if x["case_id"] == cid)["expected"]
        vals = [r["value"] for r in runs]
        acc = sum(1 for v in vals if v == exp) / len(vals)
        total += len(vals)
        wrong += sum(1 for v in vals if v != exp)
        degraded += sum(1 for r in runs if r["degraded"])
        latencies += [r["elapsed_ms"] / 1000 for r in runs]
        if len(set(vals)) > 1:
            flips.append(cid)
        samples.append(
            {
                "case_id": cid,
                "category": next(x for x in GOLDEN_SAMPLES if x["case_id"] == cid)["category"],
                "expected": exp,
                "judgements": ["T" if v else "F" for v in vals],
                "accuracy": acc,
                "confidences": [r["confidence"] for r in runs],
                "tools_used": sorted({t for r in runs for t in r["tools"]}),
            }
        )
        print(
            f"{cid:42s} exp={exp!s:5s} {['T' if v else 'F' for v in vals]} 准确率={acc:.0%}",
            flush=True,
        )

    latencies.sort()
    n = len(latencies)
    summary = {
        "repeats": args.repeats,
        "model": args.model,
        "max_steps": args.max_steps,
        "total_judgements": total,
        "wrong": wrong,
        "degraded": degraded,
        "flip_samples": flips,
        "flip_rate": len(flips) / len(per) if per else 0.0,
        "accuracy": (total - wrong) / total if total else 0.0,
        "latency_median_s": round(latencies[n // 2], 1) if n else 0.0,
        "latency_p95_s": round(latencies[int(n * 0.95)], 1) if n else 0.0,
        "latency_max_s": round(latencies[-1], 1) if n else 0.0,
        "samples": samples,
    }

    print("\n=== 汇总 ===")
    print(f"总判定 {total} | 判错 {wrong} | 降级 {degraded}")
    print(f"准确率 {summary['accuracy']:.1%} | 翻转率 {summary['flip_rate']:.1%} | 抖动样本 {flips}")
    print(
        f"延迟 median={summary['latency_median_s']}s "
        f"p95={summary['latency_p95_s']}s max={summary['latency_max_s']}s"
    )

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入: {out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
