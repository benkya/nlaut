#!/usr/bin/env python3
"""大模型上线测试最终报告生成器（v2：复用 render.py 的断言明细表）。

数据源：artifacts/session/run.jsonl → 取每用例最后一次运行，构造 render_html 期望的 results 结构。
能力探测数据从 config/report_probes/<短名>.yaml 读取。

用法:
    .venv/bin/python scripts/final_report.py --model qwen3.8-flash-next --title "Qwen3.8-Flash-Next" \\
        --endpoint "内部部署 10.62.64.38:30808" --out artifacts/qwen38_flash_final_report.html
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nlaut.report.render import render_html  # noqa: E402

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
    cur_cid: str | None = None
    for e in events:
        cid = e.get("case_id")
        if e.get("event") == "run_start":
            cur = {"case_id": cid, "status": None, "assertions": [], "title": e.get("title", "")}
            cur_cid = cid
        elif cur is not None and cid == cur_cid:
            if e.get("event") == "assertion_judged":
                cur["assertions"].append(e)
            elif e.get("event") == "run_end" and e.get("status"):
                cur["status"] = e["status"]
                # 该用例本次 run 收尾，提交到 runs 并清空 cur（防止跨用例串入）
                runs[cid] = cur
                cur = None
                cur_cid = None
    return runs


def load_probes(model_key: str) -> list[dict]:
    """读取 config/report_probes/<短名>.yaml 的能力探测数据。"""
    p = ROOT / "config/report_probes" / f"{model_key}.yaml"
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return data.get("probes", []) or []


def load_notes(model_key: str) -> dict[str, str]:
    """合并通用备注 + 模型专属备注。"""
    notes = dict(COMMON_NOTES)
    p = ROOT / "config/report_probes" / f"{model_key}.yaml"
    if p.exists():
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        for cid, note in (data.get("notes") or {}).items():
            notes[cid] = note
    return notes


def verdict(p0_rate: float, p1_rate: float) -> tuple[str, str]:
    """按通过率自动生成结论卡。"""
    if p0_rate >= 0.95:
        css, emoji, text = "pass", "✅", "推荐上线"
    elif p0_rate >= 0.90:
        css, emoji, text = "warn", "⚠️", "有条件上线（修复/观察 P0 失败项后上线）"
    else:
        css, emoji, text = "fail", "❌", "暂不上线（P0 未达标，先定性失败根因）"
    return css, f"{emoji} 综合结论：{text} —— P0 通过率 {p0_rate:.1%} · P1 通过率 {p1_rate:.1%}"


def build_results_for_render(selected: dict[str, dict], notes: dict[str, str]) -> list[dict]:
    """把 latest_runs 的输出转换为 render_html 期望的 results 结构。"""
    out: list[dict] = []
    for cid, r in selected.items():
        case_path = ROOT / "cases/llm" / f"{cid}.yaml"
        source = ""
        if case_path.exists():
            try:
                data = yaml.safe_load(case_path.read_text(encoding="utf-8"))
                source = (data.get("source") or "").split("[")[0].strip()
            except yaml.YAMLError:
                source = ""
        note = notes.get(cid, "")
        assertions_for_render: list[dict] = []
        for a in r["assertions"]:
            detail = a.get("detail", "") or ""
            if note:
                detail = (detail + " | " if detail else "") + f"定性备注：{note}"
            assertions_for_render.append({
                "assertion": a.get("assertion", ""),
                "engine": a.get("engine", ""),
                "value": a.get("value", ""),
                "confidence": a.get("confidence", ""),
                "status": a.get("status", ""),
                "detail": detail,
            })
        out.append({
            "case_id": cid,
            "title": r["title"],
            "status": r["status"] or "unknown",
            "source": source,
            "channel": "api",
            "duration_s": "",
            "screenshot": None,
            "assertions": assertions_for_render,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="大模型上线测试最终报告生成器")
    ap.add_argument("--model", required=True, help="模型短名（用于装载 probes/notes）")
    ap.add_argument("--title", required=True, help="报告标题中的模型显示名")
    ap.add_argument("--endpoint", default="", help="模型端点描述")
    ap.add_argument("--priority", default="P0,P1", help="统计的优先级集合（默认 P0,P1）")
    ap.add_argument("--out", default="", help="输出 HTML 路径")
    args = ap.parse_args()

    priorities = [p.strip().lower() for p in args.priority.split(",") if p.strip()]
    runs = latest_runs()
    live_ids = [p.stem for p in (ROOT / "cases/llm").glob("*.yaml")]
    selected = {
        cid: r for cid, r in runs.items()
        if cid in live_ids and any(f"_{p}_" in cid for p in priorities)
    }

    def _stat(cases: dict) -> tuple[int, int, int, int]:
        total = len(cases)
        passed = sum(1 for r in cases.values() if r["status"] == "passed")
        failed = sum(1 for r in cases.values() if r["status"] == "failed")
        review = sum(1 for r in cases.values() if r["status"] in ("need_review", "error"))
        return total, passed, failed, review

    notes = load_notes(args.model)
    probes = load_probes(args.model)
    p0 = {cid: r for cid, r in selected.items() if "_p0_" in cid}
    p1 = {cid: r for cid, r in selected.items() if "_p1_" in cid}
    total_t, total_p, total_f, total_r = _stat(selected)
    p0_t, p0_p, _, _ = _stat(p0)
    p1_t, p1_p, _, _ = _stat(p1)
    p0_rate = p0_p / p0_t if p0_t else 0.0
    p1_rate = p1_p / p1_t if p1_t else 0.0
    verdict_css, verdict_msg = verdict(p0_rate, p1_rate)

    results = build_results_for_render(selected, notes)

    endpoint_html = f"（{args.endpoint}）" if args.endpoint else ""
    now = datetime.now(tz=ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
    probe_card = ""
    if probes:
        rows = "".join(
            f"<tr><td>{p['name']}</td><td>{p['value']}</td><td class='muted'>{p.get('detail', '')}</td></tr>"
            for p in probes
        )
        probe_card = (
            "<div class='card'><h3>能力探测（独立于用例批次）</h3>"
            f"<table><tr><th>能力</th><th>结果</th><th>实测明细</th></tr>{rows}</table></div>"
        )

    header_html = f"""
<h1>{args.title} 上线测试报告</h1>
<p class="muted">模型：{args.title}{endpoint_html} · 框架：nlaut · 优先级集合：{args.priority} · 生成：{now} · 口径：每用例最后一次运行（修复后资产）</p>
<div class="verdict {verdict_css}">{verdict_msg}</div>
<div class="kpi"><b style="color:#1a7f37">{total_p}</b>用例通过</div>
<div class="kpi"><b style="color:#c0392b">{total_f}</b>用例失败</div>
<div class="kpi"><b style="color:#b8860b">{total_r}</b>待人工仲裁</div>
<div class="kpi"><b>{total_t}</b>用例总数</div>
<div class="kpi"><b>{p0_p}/{p0_t}</b>P0 通过</div>
<div class="kpi"><b>{p1_p}/{p1_t}</b>P1 通过</div>
{probe_card}
"""

    out = Path(args.out) if args.out else ROOT / "artifacts" / f"{args.model}_final_report.html"
    render_html(results, out_path=out, header_html=header_html, title=f"{args.title} 上线测试报告")

    print(f"P0: {p0_p}/{p0_t} 通过（率 {p0_rate:.1%}）")
    print(f"P1: {p1_p}/{p1_t} 通过（率 {p1_rate:.1%}）")
    print(f"结论: {verdict_msg.split('——')[0].strip()}")
    print(f"报告: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
