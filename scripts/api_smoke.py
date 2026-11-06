"""API 层冒烟执行器：读 YAML 配置重放接口并断言，产出 MD/HTML 报告。

用法：
    .venv/bin/python scripts/api_smoke.py --config data/api_smoke_sweng.local.yaml \
        --report artifacts/api_smoke_sweng

登录方式：Playwright 走真实登录页（复用 UI 链路，拿 X-Token + Cookie），
随后在已登录页面内用 fetch 重放每个接口——天然携带认证，绕开前端缺陷
（如按钮静默失败），直接验证后端接口契约。

断言三级：
    1. HTTP 状态码 == 200
    2. 响应为 JSON
    3. 业务码：success == true（SIT 风格；PROD 风格 code:0/200 可配）
配置中 expect: defect_404 的接口预期 404——确认已知缺陷仍在（单列不计失败）。

配置格式见 data/api_smoke_sweng.local.yaml（含内网地址与真实参数，gitignore）。
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import yaml

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def login(page, cfg: dict, logger=print) -> None:
    base = cfg["base_url"]
    cred = cfg["credentials"]
    page.goto(base, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_selector(f"input[placeholder='{cred['user_ph']}']", timeout=60000)
    page.fill(f"input[placeholder*='{cred['user_ph']}']", cred["username"])
    page.fill("input[type='password']", cred["password"])
    page.click("button[type=submit]")
    page.wait_for_selector(cred["home_anchor"], timeout=30000)
    page.wait_for_timeout(2000)
    # 关闭首次登录的新手引导遮罩（可选步骤语义）
    page.evaluate("document.querySelector('button.driver-popover-close-btn')?.click()")
    page.wait_for_timeout(500)
    logger("登录完成")


def replay(page, eps: list[dict], logger=print) -> list[dict]:
    """在已登录页面内逐个 fetch 重放接口，返回结果记录列表。"""
    results = []
    for i, ep in enumerate(eps, 1):
        url, method = ep["url"], ep["method"]
        body = ep.get("body")
        expect = ep.get("expect", "ok")
        # 动态 id 注入：先调 from 接口，按 path 提取值（如 data[0].instanceId），
        # 替换本接口 query 中的占位 {pick}
        pick = ep.get("pick_id")
        if pick:
            try:
                src = page.evaluate(
                    """async ([u, tok]) => {
                        const r = await fetch(u, {headers: {'X-Token': tok}});
                        return await r.json();
                    }""",
                    arg=[pick["from"], page.evaluate("JSON.parse(localStorage.getItem('user')||'{}').xToken||''")],
                )
                val = src
                import re as _re
                for part in pick["path"].split("."):
                    m = _re.match(r"(\w+)\[(\d+)\]", part)
                    if m:  # 形如 data[0] → val['data'][0]
                        val = val[m.group(1)][int(m.group(2))]
                    else:
                        val = val[part]
                url = url.replace("{pick}", str(val))
            except Exception as e:  # noqa: BLE001
                logger(f"  pick_id 提取失败({e})，跳过该接口原样重放")
        t0 = time.time()
        try:
            r = page.evaluate(
                """async ([url, method, body]) => {
                    // 认证：X-Token 头（app 的 axios 拦截器从 localStorage.user 注入）
                    const tok = JSON.parse(localStorage.getItem('user') || '{}').xToken || '';
                    const opt = {method, headers: {'Content-Type': 'application/json', 'X-Token': tok}};
                    if (body !== null && method !== 'GET') opt.body = JSON.stringify(body);
                    const resp = await fetch(url, opt);
                    const text = await resp.text();
                    let json = null;
                    try { json = JSON.parse(text); } catch (e) {}
                    return {status: resp.status, json, textLen: text.length,
                            contentType: resp.headers.get('content-type') || '',
                            tokenLen: tok.length};
                }""",
                arg=[url, method, body],
            )
            dur = round((time.time() - t0) * 1000)
            status = r["status"]
            j = r["json"]
            biz_ok = None
            biz_detail = ""
            if isinstance(j, dict):
                if "success" in j:
                    biz_ok = j["success"] is True
                    biz_detail = j.get("errMessage") or "" if not biz_ok else ""
                elif "code" in j:
                    biz_ok = j["code"] in (0, 200, "0", "200")
                    biz_detail = j.get("message") or ""
            data_len = None
            if isinstance(j, dict) and isinstance(j.get("data"), (list, dict)):
                data_len = len(j["data"])
            # 判定
            if expect == "defect_404":
                verdict = "DEFECT_CONFIRMED" if status == 404 else "DEFECT_MISSING(接口已修复?)"
            elif expect == "text_200":
                # 文件类接口：200 + 有内容即通过（content-type 非 JSON 属正常）
                if status == 200 and (r["textLen"] or 0) > 0:
                    verdict = "PASS"
                    biz_detail = f"text({(r['contentType'] or '?').split(';')[0]})"
                else:
                    verdict = "FAIL(空响应)"
            else:
                if status != 200:
                    verdict = "FAIL"
                elif not isinstance(j, dict):
                    verdict = "FAIL(非JSON)"
                elif biz_ok is False:
                    verdict = "FAIL(业务码)"
                else:
                    verdict = "PASS"
            results.append({
                "method": method, "url": url, "page": ep.get("page", ""),
                "status": status, "verdict": verdict, "dur_ms": dur,
                "json": j, "data_len": data_len, "biz_detail": biz_detail,
                "contentType": r["contentType"], "textLen": r["textLen"],
            })
            logger(f"[{i:2d}/{len(eps)}] {method:4s} {url[:60]:60s} {status} {verdict} {dur}ms")
        except Exception as e:  # noqa: BLE001
            results.append({
                "method": method, "url": url, "page": ep.get("page", ""),
                "status": None, "verdict": "ERROR", "dur_ms": round((time.time() - t0) * 1000),
                "json": None, "data_len": None, "biz_detail": str(e)[:100],
                "contentType": "", "textLen": None,
            })
            logger(f"[{i:2d}/{len(eps)}] {method:4s} {url[:60]:60s} ERROR {e}")
    return results


def render_report(results: list[dict], cfg: dict, out_base: Path) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    n_pass = sum(1 for r in results if r["verdict"] == "PASS")
    n_fail = sum(1 for r in results if r["verdict"].startswith("FAIL") or r["verdict"] == "ERROR")
    n_defect = sum(1 for r in results if r["verdict"] == "DEFECT_CONFIRMED")
    total = len(results)

    def cjk_width(s: str, width: int) -> str:
        w = sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)
        return s + " " * max(0, width - w)

    lines = [
        f"# API 层冒烟报告 — {cfg['base_url']}",
        "",
        f"- 执行时间：{ts}",
        f"- 接口总数：{total}，**通过 {n_pass}**，失败 {n_fail}，已知缺陷确认 {n_defect}",
        "- 认证：Playwright 真实登录后页内 fetch 重放（X-Token + Cookie）",
        "",
        "| # | 方法 | 接口 | 页面 | 状态码 | 业务码 | data | 耗时 | 结论 |",
        "|--:|------|------|------|-------:|--------|-----:|-----:|------|",
    ]
    for i, r in enumerate(results, 1):
        biz = "success:true" if r["verdict"] == "PASS" else (r["biz_detail"][:24] or "-")
        data = str(r["data_len"]) if r["data_len"] is not None else "-"
        page = (r["page"] or "-")[:10]
        lines.append(
            f"| {i} | {r['method']} | `{r['url']}` | {page} "
            f"| {r['status']} | {biz} | {data} | {r['dur_ms']}ms | {r['verdict']} |"
        )
    md = "\n".join(lines) + "\n"
    out_md = out_base.with_suffix(".md")
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md, encoding="utf-8")

    # HTML（与 MD 同步，表格样式与终端固定宽度口径一致）
    rows = "\n".join(
        f"<tr><td>{i}</td><td>{r['method']}</td><td class='url'>{r['url']}</td>"
        f"<td>{r['page'] or '-'}</td><td class='num'>{r['status']}</td>"
        f"<td>{'success:true' if r['verdict'] == 'PASS' else (r['biz_detail'][:30] or '-')}</td>"
        f"<td class='num'>{r['data_len'] if r['data_len'] is not None else '-'}</td>"
        f"<td class='num'>{r['dur_ms']}ms</td>"
        f"<td class=\"v {r['verdict'].split('(')[0].lower()}\">{r['verdict']}</td></tr>"
        for i, r in enumerate(results, 1)
    )
    html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>API 冒烟报告 — {cfg['base_url']}</title>
<style>
body{{font:14px/1.7 -apple-system,'PingFang SC',sans-serif;margin:24px;color:#1a1a1a;background:#fff}}
h1{{font-size:20px}} .sum{{background:#f6f8fa;padding:12px 16px;border-radius:8px;margin:12px 0}}
table{{border-collapse:collapse;width:100%;margin:16px 0}}
th,td{{border:1px solid #d0d7de;padding:5px 10px;text-align:left;font-size:13px;white-space:nowrap}}
th{{background:#f6f8fa}} .num{{text-align:right}} .url{{font-family:ui-monospace,monospace}}
.v.pass{{color:#1a7f37;font-weight:600}} .v.fail,.v.fail(非json),.v.fail(业务码){{color:#cf222e;font-weight:600}}
.v.defect_confirmed{{color:#9a6700;font-weight:600}} .v.error{{color:#cf222e}}
td{{white-space:normal;word-break:break-all}}
</style></head><body>
<h1>API 层冒烟报告</h1>
<div class="sum">
被测系统：{cfg['base_url']} · 执行时间：{ts}<br>
接口总数 <b>{total}</b> · 通过 <b>{n_pass}</b> · 失败 <b>{n_fail}</b> · 已知缺陷确认 <b>{n_defect}</b> ·
认证方式：Playwright 真实登录后页内 fetch 重放
</div>
<table><thead><tr><th>#</th><th>方法</th><th>接口</th><th>页面</th><th>状态码</th><th>业务码</th><th>data</th><th>耗时</th><th>结论</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""
    out_html = out_base.with_suffix(".html")
    out_html.write_text(html, encoding="utf-8")

    # 终端固定宽度表（中文=2）
    print("\n" + cjk_width("方法", 6) + cjk_width("接口", 62) + cjk_width("状态", 6) + cjk_width("结论", 22) + "耗时")
    for r in results:
        print(cjk_width(r["method"], 6) + cjk_width(r["url"][:58], 62)
              + cjk_width(str(r["status"]), 6) + cjk_width(r["verdict"], 22) + f"{r['dur_ms']}ms")
    print(f"\n=== 通过 {n_pass}/{total} · 失败 {n_fail} · 已知缺陷确认 {n_defect}")
    print(f"报告: {out_md}\n      {out_html}")


def main() -> None:
    ap = argparse.ArgumentParser(description="API 层冒烟执行器")
    ap.add_argument("--config", required=True, help="YAML 配置（base_url + credentials + endpoints）")
    ap.add_argument("--report", default="artifacts/api_smoke", help="报告输出基名（生成 .md/.html）")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, executable_path=CHROME)
        pg = b.new_context(viewport={"width": 1680, "height": 1000}).new_page()
        login(pg, cfg)
        results = replay(pg, cfg["endpoints"])
        b.close()
    render_report(results, cfg, Path(args.report))


if __name__ == "__main__":
    main()
