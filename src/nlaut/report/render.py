"""L6 报告渲染（MVP）：结果列表 → 自包含 HTML 报告。

分层统计：确定性断言 vs AI 判定（VLM/Laya）各自通过率；
每条用例附截图（内嵌 base64）+ source 追溯 + 置信度 + 判定详情。
报告自包含：单 HTML 文件，可直接发给同事。
"""

from __future__ import annotations

import base64
import html
from pathlib import Path

_STATUS_COLOR = {
    "passed": "#1a7f37",
    "failed": "#c0392b",
    "need_review": "#b8860b",
    "error": "#8b0000",
    "skipped": "#6b7280",
}


def _img_b64(path: str | None) -> str | None:
    if not path or not Path(path).exists():
        return None
    return base64.b64encode(Path(path).read_bytes()).decode()


def _esc(s) -> str:
    return html.escape(str(s))


def render_html(
    results: list[dict],
    out_path: str | Path = "artifacts/report.html",
    header_html: str = "",
    kpi_html: str = "",
    title: str = "nlaut 测试报告",
) -> Path:
    """渲染测试报告。

    新增可选参数 header_html / kpi_html / title 让上层报告生成器可注入自定义头部与KPI。
    默认行为与之前完全相同，向后兼容。
    """
    det_pass = det_total = ai_pass = ai_total = review = 0
    for r in results:
        for a in r.get("assertions", []):
            st = a.get("status")
            if a.get("engine") == "deterministic":
                det_total += 1
                det_pass += st == "auto_pass"
            elif st in ("auto_pass", "auto_fail", "human_review", "skipped"):
                ai_total += 1
                ai_pass += st == "auto_pass"
                review += st == "human_review"

    total = len(results)
    passed = sum(r["status"] == "passed" for r in results)
    failed = sum(r["status"] in ("failed", "error") for r in results)
    need = sum(r["status"] == "need_review" for r in results)

    rows = []
    for r in results:
        color = _STATUS_COLOR.get(r["status"], "#333")
        img = _img_b64(r.get("screenshot"))
        img_html = (
            f'<img src="data:image/png;base64,{img}" style="max-width:720px;'
            'border:1px solid #ddd;border-radius:6px;margin:8px 0">'
            if img else ""
        )
        assertion_rows = "".join(
            f"<tr><td>{_esc(a.get('assertion'))}</td>"
            f"<td>{_esc(a.get('engine'))}</td>"
            f"<td>{_esc(a.get('value'))}</td>"
            f"<td>{_esc(a.get('confidence'))}</td>"
            f"<td style='color:{_STATUS_COLOR.get(a.get('status'), '#333')}'>"
            f"{_esc(a.get('status'))}</td>"
            f"<td class='muted'>{_esc(a.get('detail', ''))[:120]}</td></tr>"
            for a in r.get("assertions", [])
        )
        # 失败/转人工时展示请求与返回（便于人工定性）
        evidence_html = ""
        if r["status"] in ("failed", "need_review", "error"):
            parts = []
            # 请求（conversation 含 system+user messages）
            conv = r.get("conversation")
            if conv:
                req_lines = []
                for msg in conv:
                    role = msg.get("role", "?")
                    content = (msg.get("content") or "")[:500]
                    if content:
                        req_lines.append(f"[{role}] {content}")
                if req_lines:
                    parts.append(
                        f'<details><summary>📥 请求（{len(conv)} 条消息）</summary>'
                        f'<pre style="white-space:pre-wrap;font-size:12px;background:#f6f8fa;'
                        f'padding:10px;border-radius:6px;overflow-x:auto">'
                        f'{_esc(chr(10).join(req_lines))}</pre></details>'
                    )
            # LLM 返回
            llm_resp = r.get("llm_response")
            if llm_resp:
                truncated = llm_resp[:2000]
                suffix = f"\n…（共 {len(llm_resp)} 字符，截断显示前 2000）" if len(llm_resp) > 2000 else ""
                parts.append(
                    f'<details><summary>📤 模型返回（{len(llm_resp)} 字符）</summary>'
                    f'<pre style="white-space:pre-wrap;font-size:12px;background:#fef3f2;'
                    f'padding:10px;border-radius:6px;overflow-x:auto">'
                    f'{_esc(truncated)}{_esc(suffix)}</pre></details>'
                )
            # 工具调用
            tc = r.get("tool_calls")
            if tc:
                import json as _json
                parts.append(
                    f'<details><summary>🔧 工具调用（{len(tc)} 个）</summary>'
                    f'<pre style="white-space:pre-wrap;font-size:12px;background:#f6f8fa;'
                    f'padding:10px;border-radius:6px;overflow-x:auto">'
                    f'{_esc(_json.dumps(tc, ensure_ascii=False, indent=2)[:1500])}</pre></details>'
                )
            # 延迟
            lat = r.get("latency_ms")
            if lat:
                parts.append(f'<p class="muted">响应延迟: {lat:.0f}ms</p>')
            if parts:
                evidence_html = f'<div style="margin:8px 0">{"&nbsp;".join(parts)}</div>'
        rows.append(f"""
<div class="card">
  <h3 style="color:{color}">{_esc(r['case_id'])} — [{_esc(r['status'])}] {_esc(r['title'])}</h3>
  <p class="muted">来源: {_esc(r.get('source'))} · 通道: {_esc(r.get('channel'))} · 耗时: {_esc(r.get('duration_s'))}s</p>
  {img_html}
  <table>
    <tr><th>断言</th><th>引擎</th><th>value</th><th>置信度</th><th>状态</th><th>说明</th></tr>
    {assertion_rows}
  </table>
  {evidence_html}
</div>""")

    default_kpi = (
        f'<div>'
        f'<span class="kpi"><b style="color:#1a7f37">{passed}</b>用例通过</span>'
        f'<span class="kpi"><b style="color:#c0392b">{failed}</b>用例失败</span>'
        f'<span class="kpi"><b style="color:#b8860b">{need}</b>待人工仲裁</span>'
        f'<span class="kpi"><b>{total}</b>用例总数</span>'
        f'<span class="kpi"><b>{det_pass}/{det_total}</b>确定性断言</span>'
        f'<span class="kpi"><b>{ai_pass}/{ai_total}</b>AI 断言自动通过</span>'
        f'<span class="kpi"><b style="color:#b8860b">{review}</b>AI 断言转人工</span>'
        f'</div>'
    )

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(f"""<!DOCTYPE html>
<html lang="zh"><head><meta charset="UTF-8"><title>{_esc(title)}</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", sans-serif; background: #f5f6f8; margin: 24px; color: #222; }}
  .card {{ background: #fff; border-radius: 10px; padding: 20px; margin: 16px 0;
          box-shadow: 0 2px 8px rgba(0,0,0,.06); }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid #eee; }}
  th {{ background: #f0f2f5; }}
  .muted {{ color: #888; font-size: 12px; }}
  .kpi {{ display: inline-block; background: #fff; border-radius: 10px; padding: 14px 22px;
          margin: 0 10px 10px 0; box-shadow: 0 2px 8px rgba(0,0,0,.06); }}
  .kpi b {{ font-size: 22px; display: block; }}
  .verdict {{ font-size: 18px; font-weight: 600; padding: 14px 18px; border-radius: 10px; margin: 14px 0; }}
  .verdict.pass {{ background:#ecfdf5; border:1px solid #a7f3d0; color:#065f46; }}
  .verdict.warn {{ background:#fffbeb; border:1px solid #fde68a; color:#92400e; }}
  .verdict.fail {{ background:#fef2f2; border:1px solid #fecaca; color:#991b1b; }}
</style></head><body>
{header_html}
{kpi_html or default_kpi}
{''.join(rows)}
</body></html>""", encoding="utf-8")
    return out
