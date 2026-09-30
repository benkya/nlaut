"""4p12s 探针报告渲染（与 glm-5.2 上线报告同款 HTML）。

输入: artifacts/4p12s_v3_<model>/summary.json + code/ + log
输出: 上线报告风格 HTML — 顶部 KPI + 能力探测卡 + 每步一张断言明细卡
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_STATUS_COLOR = {
    True: "#1a7f37",
    False: "#c0392b",
    None: "#b8860b",  # need_review
}


def _esc(s) -> str:
    return html.escape(str(s))


def render(out_dir: Path, model_label: str | None = None) -> Path:
    summary_path = out_dir / "summary.json"
    if not summary_path.exists():
        sys.exit(f"缺 summary.json: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    model_id = model_label or summary["model"]
    now = datetime.now(tz=timezone.utc).isoformat(timespec="minutes")[:16]
    total = summary["steps_total"]
    passed = summary["steps_passed"]
    failed = total - passed
    pct = round(passed / total * 100, 1) if total else 0.0

    retried = sum(1 for r in summary["steps"] if len(r.get("attempts", [])) > 1)
    leak = summary.get("total_thinking_leak_lines", 0)

    if pct == 100:
        verdict_cls, verdict = "pass", "✅ 推荐上线 — 9/9 步全过 · 交付代理能力达标"
    elif pct >= 80:
        verdict_cls, verdict = "warn", f"⚠️ 谨慎上线 — {passed}/9 步过门禁（{failed} 步未过），建议补 gateway 稳定性"
    else:
        verdict_cls, verdict = "fail", f"❌ 不建议上线 — 仅 {pct:.0f}%，关键能力缺失"

    parts: list[str] = []
    parts.append(f"""<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">
<title>{model_id} 4p12s 交付能力测试报告</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", sans-serif; background:#f5f6f8; margin:24px; color:#222; }}
  .card {{ background:#fff; border-radius:10px; padding:20px; margin:16px 0; box-shadow:0 2px 8px rgba(0,0,0,.06); }}
  table {{ border-collapse:collapse; width:100%; font-size:13px; }}
  th,td {{ text-align:left; padding:6px 10px; border-bottom:1px solid #eee; }}
  th {{ background:#f0f2f5; }}
  .muted {{ color:#888; font-size:12px; }}
  .kpi {{ display:inline-block; background:#fff; border-radius:10px; padding:14px 22px; margin:0 10px 10px 0; box-shadow:0 2px 8px rgba(0,0,0,.06); }}
  .kpi b {{ font-size:22px; display:block; }}
  .verdict {{ font-size:18px; font-weight:600; padding:14px 18px; border-radius:10px; margin:14px 0; }}
  .verdict.pass {{ background:#ecfdf5; border:1px solid #a7f3d0; color:#065f46; }}
  .verdict.warn {{ background:#fffbeb; border:1px solid #fde68a; color:#92400e; }}
  .verdict.fail {{ background:#fef2f2; border:1px solid #fecaca; color:#991b1b; }}
  .step-card {{ background:#fff; border-radius:10px; padding:18px; margin:14px 0; box-shadow:0 2px 6px rgba(0,0,0,.05); border-left:5px solid #ddd; }}
  .step-card.pass {{ border-left-color:#1a7f37; }}
  .step-card.fail {{ border-left-color:#c0392b; }}
  h2 {{ margin-top:0; }}
  pre.code {{ background:#f6f8fa; border:1px solid #e1e4e8; border-radius:6px; padding:10px; overflow:auto; font-size:12px; max-height:280px; }}
</style></head><body>

<h1>{model_id} 4p12s 交付代理能力测试报告</h1>
<p class="muted">模型：{model_id} · harness：scripts/four_p12s_probe.py v3 · 步骤：4p12s ②~⑩（跳 ①⑪⑫）· 生成：{now} · 口径：每步结构门禁 + ⑧⑨⑩ 真实 pytest 门禁</p>

<div class="verdict {verdict_cls}">{verdict}</div>

<div class="kpi"><b style="color:#1a7f37">{passed}</b>步通过</div>
<div class="kpi"><b style="color:#c0392b">{failed}</b>步未过</div>
<div class="kpi"><b>{total}</b>步总数</div>
<div class="kpi"><b>{pct}%</b>通过率</div>
<div class="kpi"><b>{summary['total_elapsed_s']}s</b>总耗时</div>
<div class="kpi"><b>{summary['total_completion_tokens']}</b>completion tokens</div>
<div class="kpi"><b>{retried}</b>步含重试</div>
<div class="kpi"><b>{leak}</b>思考泄漏行</div>
""")

    # 能力探测卡
    parts.append("""
<div class="card"><h3>能力探测（独立于用例）</h3>
<table><tr><th>能力</th><th>结果</th><th>实测明细</th></tr>
""")
    capability_rows = [
        ("思考模式可控性", "默认干净" if leak == 0 else f"泄漏 {leak} 行",
         "响应 keys 仅含 content（无 reasoning 字段）；探针内置思考泄漏门禁（grep wait/let me/让我 等）"),
        ("代码卫生", f"{leak} 行思考泄漏",
         "三件代码（实现+集成测试+E2E 测试）注释 grep 推理关键词计数"),
        ("结构化文档", f"{sum(1 for r in summary['steps'] if r['structure_gate'])} 步门禁过",
         "regex 门禁覆盖 7 类结构要素：FUNC/RULE/NFR 编号、目标、范围外、US/AC、TASK/依赖、文件清单、阈值关键词"),
        ("确定性执行门禁", f"{sum(1 for r in summary['steps'] if r.get('pytest_gate') is True)}/{sum(1 for r in summary['steps'] if 'pytest_gate' in r and r.get('pytest_gate') is not None)} 步过",
         "step ⑧ 元测试 6 断言 + step ⑨⑩ 模型自测真实 pytest"),
    ]
    for label, value, detail in capability_rows:
        parts.append(f"<tr><td>{label}</td><td>{_esc(value)}</td><td class='muted'>{_esc(detail)}</td></tr>")
    parts.append("</table></div>")

    # 逐步卡片
    parts.append("<h2>逐步执行明细</h2>")
    for r in summary["steps"]:
        ok = r["structure_gate"]
        cls = "pass" if ok else "fail"
        attempts = r.get("attempts", [])
        attempts_n = len(attempts)
        last_attempt = attempts[-1] if attempts else {}
        err = r.get("error", "") or (attempts[-1].get("error", "") if attempts else "")
        gate = r.get("gate_detail", [])
        gate_str = " ".join("✅" if g else "❌" for g in gate)
        code_loc = r.get("code_loc")
        leak_lines = r.get("thinking_leak_lines")

        parts.append(f"""
<div class="step-card {cls}">
  <h2 style="color:{'#1a7f37' if ok else '#c0392b'};font-size:16px">
    [{'PASS' if ok else 'FAIL'}] {r['step']} — {r['name']}
  </h2>
  <p class="muted">耗时: {r['elapsed_s']}s · attempts: {attempts_n} · completion_tokens: {r['completion_tokens']} · prompt_tokens: {r['prompt_tokens']}</p>
  <table>
    <tr><th>门禁项</th><th>结果</th></tr>
    <tr><td>结构门禁（regex）</td><td>{gate_str}</td></tr>
""")
        if r.get("pytest_gate") is not None:
            pytest_color = _STATUS_COLOR.get(r["pytest_gate"], "#333")
            parts.append(f"<tr><td>pytest 执行门禁</td><td style='color:{pytest_color}'>{'✅ 全绿' if r['pytest_gate'] else '❌ 失败'} · loc={code_loc}</td></tr>")
        if leak_lines is not None:
            parts.append(f"<tr><td>思考泄漏检测</td><td>{leak_lines} 行命中</td></tr>")
        if err:
            parts.append(f"<tr><td>错误</td><td style='color:#c0392b'>{_esc(err)}</td></tr>")
        if attempts_n > 1:
            parts.append("<tr><td>重试明细</td><td>")
            for a in attempts:
                a_ok = "✅" if a["gate_ok"] else "❌"
                parts.append(f"<span class='muted'>{a['attempt']}轮: {a_ok} {a['elapsed_s']}s comp={a['completion_tokens']}</span><br>")
            parts.append("</td></tr>")
        parts.append("</table>")

        # 产物预览（代码类步骤）
        if r["step"] in ("step8_implementation", "step9_integration", "step10_e2e"):
            code_file_map = {
                "step8_implementation": "percentile.py",
                "step9_integration": "test_percentile_integration.py",
                "step10_e2e": "test_p95_alert_e2e.py",
            }
            f = out_dir / "code" / code_file_map[r["step"]]
            if f.exists():
                preview = f.read_text(encoding="utf-8")[:1500]
                parts.append(f"<p class='muted'>产物：code/{code_file_map[r['step']]}（{f.stat().st_size} bytes）</p>")
                parts.append(f"<pre class='code'>{_esc(preview)}{'...' if f.stat().st_size > 1500 else ''}</pre>")

        # 产物 .md 文本预览
        md_file = out_dir / f"{r['step']}.md"
        if md_file.exists() and md_file.stat().st_size > 0:
            preview = md_file.read_text(encoding="utf-8")[:800]
            parts.append(f"<details><summary class='muted'>产物 {r['step']}.md（{md_file.stat().st_size} bytes）</summary>")
            parts.append(f"<pre class='code'>{_esc(preview)}{'...' if md_file.stat().st_size > 800 else ''}</pre>")
            parts.append("</details>")

        parts.append("</div>")

    parts.append("""
<div class="card">
  <h3>复跑方法</h3>
  <pre class='code'>.venv/bin/python  scripts/four_p12s_probe.py --model &lt;key_in_models.yaml&gt; [--think off]</pre>
  <p class="muted">探针 v3 · 4p12s ②~⑩ · 同预算同重试同门禁</p>
</div>
""")

    parts.append("</body></html>")

    out_html = out_dir / "report.html"
    out_html.write_text("\n".join(parts), encoding="utf-8")
    return out_html


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="artifacts/4p12s_v3_<model> 目录")
    ap.add_argument("--label", default="", help="报告标题覆盖模型名")
    args = ap.parse_args()
    out = render(Path(args.dir), args.label or None)
    print(f"OK {out}")


if __name__ == "__main__":
    main()