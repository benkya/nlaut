#!/usr/bin/env python3
"""模型对比报告生成器：解析两个模型的批次 log，生成 diff 对比 HTML。

用法:
    .venv/bin/python scripts/compare_models.py \
        --glm-log artifacts/p0_final.log --glm-name "glm-5.2" \
        --qwen-log artifacts/qwen38_final.log --qwen-name "Qwen3.8-Flash-Next" \
        --out artifacts/compare_glm52_vs_qwen38.html
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

_STATUS_LINE = re.compile(r"\[\s*(\w+)\]\s+(tc_\w+)\s+(.+?)\s+\(([\d.]+)s\)")
_LATENCY_LINE = re.compile(r"\[(tc_\w+)\] API 响应: (\d+)ms")


def parse_log(path: str) -> tuple[dict, dict]:
    """解析批次 log → ({case_id: (status, duration_s)}, {case_id: latency_ms})"""
    cases: dict[str, tuple[str, str]] = {}
    latencies: dict[str, int] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = _STATUS_LINE.match(line)
        if m:
            cases[m.group(2)] = (m.group(1), m.group(4))
            continue
        m2 = _LATENCY_LINE.match(line)
        if m2:
            latencies[m2.group(1)] = int(m2.group(2))
    return cases, latencies


def _color(status: str) -> str:
    return {"passed": "#1a7f37", "failed": "#c0392b",
            "need_review": "#b8860b", "error": "#8b0000"}.get(status, "#333")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glm-log", required=True)
    ap.add_argument("--glm-name", default="glm-5.2")
    ap.add_argument("--qwen-log", required=True)
    ap.add_argument("--qwen-name", default="Qwen3.8-Flash-Next")
    ap.add_argument("--out", default="artifacts/compare.html")
    args = ap.parse_args()

    g_cases, g_lat = parse_log(args.glm_log)
    q_cases, q_lat = parse_log(args.qwen_log)

    all_ids = sorted(set(g_cases) | set(q_cases))

    def _rate(cases):
        if not cases:
            return "0/0 (0%)"
        p = sum(1 for s, _ in cases.values() if s == "passed")
        return f"{p}/{len(cases)} ({p / len(cases) * 100:.1f}%)"

    def _avg_lat(lat):
        if not lat:
            return "-"
        return f"{sum(lat.values()) / len(lat) / 1000:.1f}s"

    # 差异行（两边状态不同的用例）
    diffs = [cid for cid in all_ids
             if g_cases.get(cid, ("-",))[0] != q_cases.get(cid, ("-",))[0]]

    rows = []
    for cid in all_ids:
        gs, _gd = g_cases.get(cid, ("-", "-"))
        qs, _qd = q_cases.get(cid, ("-", "-"))
        gl = f"{g_lat.get(cid, 0) / 1000:.1f}s" if cid in g_lat else "-"
        ql = f"{q_lat.get(cid, 0) / 1000:.1f}s" if cid in q_lat else "-"
        rows.append(f"""
<tr{' style="background:#fff8e1"' if cid in diffs else ""}>
<td>{cid}</td>
<td style="color:{_color(gs)}">{gs}</td><td>{gl}</td>
<td style="color:{_color(qs)}">{qs}</td><td>{ql}</td>
</tr>""")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="UTF-8"><title>模型对比报告</title>
<style>
body {{ font-family: -apple-system, sans-serif; background: #f5f6f8; margin: 24px; color: #222; }}
.card {{ background: #fff; border-radius: 10px; padding: 20px; margin: 16px 0;
        box-shadow: 0 2px 8px rgba(0,0,0,.06); }}
table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid #eee; }}
th {{ background: #f0f2f5; }}
.muted {{ color: #888; font-size: 12px; }}
.kpi {{ display: inline-block; background: #fff; border-radius: 10px; padding: 14px 22px;
        margin: 0 10px 10px 0; box-shadow: 0 2px 8px rgba(0,0,0,.06); }}
.kpi b {{ font-size: 22px; display: block; }}
</style></head><body>
<h1>大模型上线测试对比报告</h1>
<p class="muted">生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')} · 同一套用例（41 条 P0）· 同一判定引擎（nlaut v0.2.1）</p>
<div>
<span class="kpi"><b>{_rate(g_cases)}</b>{args.glm_name} 通过率</span>
<span class="kpi"><b>{_rate(q_cases)}</b>{args.qwen_name} 通过率</span>
<span class="kpi"><b>{_avg_lat(g_lat)}</b>{args.glm_name} 平均延迟</span>
<span class="kpi"><b>{_avg_lat(q_lat)}</b>{args.qwen_name} 平均延迟</span>
<span class="kpi"><b style="color:#b8860b">{len(diffs)}</b>状态差异用例</span>
</div>
<div class="card">
<h2>逐用例对比（黄色行 = 两模型状态不同）</h2>
<table>
<tr><th>用例 ID</th>
<th>{args.glm_name}</th><th>延迟</th>
<th>{args.qwen_name}</th><th>延迟</th></tr>
{''.join(rows)}
</table>
</div>
</body></html>""", encoding="utf-8")

    print("=== 对比完成 ===")
    print(f"{args.glm_name}: {_rate(g_cases)}, 平均延迟 {_avg_lat(g_lat)}")
    print(f"{args.qwen_name}: {_rate(q_cases)}, 平均延迟 {_avg_lat(q_lat)}")
    print(f"状态差异用例: {len(diffs)} 条")
    for cid in diffs:
        print(f"  {cid}: {g_cases.get(cid, ('-',))[0]} vs {q_cases.get(cid, ('-',))[0]}")
    print(f"报告: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
