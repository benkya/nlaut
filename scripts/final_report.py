#!/usr/bin/env python3
"""大模型上线测试最终报告生成器（泛化版）。

数据源：artifacts/session/run.jsonl（每用例取最后一次运行 = 最新资产+模型的真实判定）。
能力探测数据从 config/report_probes/<短名>.yaml 读取（无则省略该卡片）。

用法:
    .venv/bin/python scripts/final_report.py --model qwen3.8-flash-next --title "Qwen3.8-Flash-Next" \\
        --endpoint "内部部署 10.62.64.38:30808" --out artifacts/qwen38_flash_final_report.html
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]

# 通用用例定性备注（与模型无关）
COMMON_NOTES = {
    "tc_crossplatform_p1_001": "待 Phase 5：跨端一致性需 Web+API 双端执行编排，当前 API 单通道无法测",
    "tc_crossplatform_p1_002": "待 Phase 5：同上",
}


def latest_runs() -> dict[str, dict]:
    """从 run.jsonl 取每个用例最后一次运行的判定结果（修复后资产口径）。"""
    path = ROOT / "artifacts/session/run.jsonl"
    events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    runs: dict[str, dict] = {}
    cur: dict | None = None
    for e in events:
        cid = e.get("case_id")
        if e.get("event") == "run_start":
            cur = {"case_id": cid, "status": None, "assertions": [], "title": e.get("title", "")}
            runs[cid] = cur
        elif cur is not None and cid == cur["case_id"]:
            if e.get("event") == "assertion_judged":
                cur["assertions"].append(e)
            elif e.get("event") == "run_end" and e.get("status"):
                cur["status"] = e["status"]
    return runs


def load_probes(model_key: str) -> list[tuple[str, str, str]]:
    """读取 config/report_probes/<短名>.yaml 的能力探测数据。"""
    p = ROOT / "config/report_probes" / f"{model_key}.yaml"
    if not p.exists():
        return []
    import yaml
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return [(r["name"], r["value"], r.get("detail", "")) for r in data.get("probes", [])]


def load_notes(model_key: str) -> dict[str, str]:
    """合并通用备注 + 模型专属备注。"""
    notes = dict(COMMON_NOTES)
    p = ROOT / "config/report_probes" / f"{model_key}.yaml"
    if p.exists():
        import yaml
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        for cid, note in (data.get("notes") or {}).items():
            notes[cid] = note
    return notes


def verdict_html(p0_rate: float, p1_rate: float) -> tuple[str, str]:
    """按通过率自动生成结论卡。"""
    if p0_rate >= 0.95:
        css, emoji, text = "pass", "✅", "推荐上线"
    elif p0_rate >= 0.90:
        css, emoji, text = "warn", "⚠️", "有条件上线（修复/观察 P0 失败项后上线）"
    else:
        css, emoji, text = "fail", "❌", "暂不上线（P0 未达标，先定性失败根因）"
    detail = f"P0 通过率 {p0_rate:.1%} · P1 通过率 {p1_rate:.1%}"
    return css, f"{emoji} 综合结论：{text} —— {detail}"


def main() -> int:
    ap = argparse.ArgumentParser(description="大模型上线测试最终报告生成器")
    ap.add_argument("--model", required=True, help="模型短名（config/models.yaml 的 key，用于 probes/notes 装载）")
    ap.add_argument("--title", required=True, help="报告标题中的模型显示名")
    ap.add_argument("--endpoint", default="", help="模型端点描述（如 内部部署 10.62.64.38:30808）")
    ap.add_argument("--out", default="", help="输出 HTML 路径（默认 artifacts/<model>_final_report.html）")
    args = ap.parse_args()

    runs = latest_runs()
    live_ids = [p.stem for p in (ROOT / "cases/llm").glob("*.yaml")]
    p0 = {cid: r for cid, r in runs.items() if cid in live_ids and "_p0_" in cid}
    p1 = {cid: r for cid, r in runs.items() if cid in live_ids and "_p1_" in cid}

    def _stat(cases: dict) -> tuple[int, int, int, int]:
        total = len(cases)
        passed = sum(1 for r in cases.values() if r["status"] == "passed")
        failed = sum(1 for r in cases.values() if r["status"] == "failed")
        review = sum(1 for r in cases.values() if r["status"] in ("need_review", "error"))
        return total, passed, failed, review

    p0_t, p0_p, p0_f, p0_r = _stat(p0)
    p1_t, p1_p, p1_f, p1_r = _stat(p1)
    p0_rate = p0_p / p0_t if p0_t else 0.0
    p1_rate = p1_p / p1_t if p1_t else 0.0

    notes = load_notes(args.model)
    probes = load_probes(args.model)

    def _row(cid: str, r: dict | None) -> str:
        if r is None:
            return f"<tr><td>{cid}</td><td>—</td><td>未执行</td><td>—</td></tr>"
        badge = {"passed": "✅", "failed": "❌", "need_review": "👀", "error": "⚠️"}.get(r["status"], "?")
        note = notes.get(cid, "")
        note_html = f'<span class="note">{note}</span>' if note else ""
        return f"<tr><td>{cid}</td><td>{r['title']}</td><td>{badge} {r['status']}</td><td>{note_html}</td></tr>"

    rows_p0 = "\n".join(_row(cid, p0.get(cid)) for cid in sorted(p0))
    rows_p1 = "\n".join(_row(cid, p1.get(cid)) for cid in sorted(p1))
    probe_card = ""
    if probes:
        probe_rows = "\n".join(f"<tr><td>{n}</td><td>{v}</td><td>{d}</td></tr>" for n, v, d in probes)
        probe_card = f'<div class="card"><h3>能力探测（独立于用例批次）</h3><table><tr><th>能力</th><th>结果</th><th>实测明细</th></tr>{probe_rows}</table></div>'

    css, verdict = verdict_html(p0_rate, p1_rate)
    now = datetime.now(tz=ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
    endpoint_html = f"（{args.endpoint}）" if args.endpoint else ""
    out = Path(args.out) if args.out else ROOT / "artifacts" / f"{args.model}_final_report.html"

    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>{args.title} 上线测试报告</title>
<style>
body{{font-family:-apple-system,"PingFang SC",sans-serif;margin:0;padding:24px;background:#f7f8fa;color:#1a1a1a}}
.wrap{{max-width:960px;margin:0 auto}}
h1{{font-size:22px;margin:0 0 4px}} .meta{{color:#666;font-size:13px;margin-bottom:20px}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:18px;margin-bottom:16px}}
.verdict{{font-size:18px;font-weight:600;padding:14px 18px;border-radius:10px;margin-bottom:16px}}
.pass{{background:#ecfdf5;border:1px solid #a7f3d0;color:#065f46}}
.warn{{background:#fffbeb;border:1px solid #fde68a;color:#92400e}}
.fail{{background:#fef2f2;border:1px solid #fecaca;color:#991b1b}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;background:#f3f4f6;padding:8px;border-bottom:2px solid #e5e7eb}}
td{{padding:7px 8px;border-bottom:1px solid #f0f0f0;vertical-align:top}}
.note{{color:#b45309;font-size:12px}}
.kpis{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}}
.kpi{{flex:1;min-width:140px;background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:12px;text-align:center}}
.kpi .v{{font-size:24px;font-weight:700}} .kpi .l{{font-size:12px;color:#666;margin-top:2px}}
</style></head><body><div class="wrap">
<h1>{args.title} 上线测试报告</h1>
<div class="meta">模型：{args.title}{endpoint_html} · 框架：nlaut · 用例集：{len(live_ids)} 条 · 生成：{now} · 口径：每用例最后一次运行（修复后资产）</div>

<div class="verdict {css}">{verdict}</div>

<div class="kpis">
<div class="kpi"><div class="v">{p0_p}/{p0_t}</div><div class="l">P0 通过</div></div>
<div class="kpi"><div class="v">{p1_p}/{p1_t}</div><div class="l">P1 通过</div></div>
<div class="kpi"><div class="v">{p0_rate:.1%}</div><div class="l">P0 通过率</div></div>
<div class="kpi"><div class="v">{p1_rate:.1%}</div><div class="l">P1 通过率</div></div>
</div>

{probe_card}

<div class="card"><h3>P0 用例明细（{p0_t} 条）</h3>
<table><tr><th>用例ID</th><th>标题</th><th>结果</th><th>定性备注</th></tr>{rows_p0}</table></div>

<div class="card"><h3>P1 用例明细（{p1_t} 条）</h3>
<table><tr><th>用例ID</th><th>标题</th><th>结果</th><th>定性备注</th></tr>{rows_p1}</table></div>

<div class="card"><h3>说明</h3>
<ul style="font-size:13px;line-height:1.8">
<li>口径：每用例取最后一次运行结果；历史失败定性证据链见 artifacts/ 各批次 log。</li>
<li>need_review = AI 判定置信度 0.5-0.9 转人工仲裁（设计行为，非失败）。</li>
<li>判定漏斗：确定性断言（零 AI 成本）→ Laya 语义 → 置信度路由。</li>
</ul></div>
</div></body></html>"""

    out.write_text(html, encoding="utf-8")
    print(f"P0: {p0_p}/{p0_t} 通过（失败{p0_f} 转人工{p0_r}，率 {p0_rate:.1%}）")
    print(f"P1: {p1_p}/{p1_t} 通过（失败{p1_f} 转人工{p1_r}，率 {p1_rate:.1%}）")
    print(f"结论: {verdict.split('——')[0].strip()}")
    print(f"报告: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
