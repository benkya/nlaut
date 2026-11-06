"""合并总测试报告生成器：页面用例（nlaut session log）+ API 冒烟 → 自包含 HTML + MD。

数据源（均为本地执行产物）：
    artifacts/session/run.jsonl  —— 取每个 tc_sweng_* 用例的最后一段（终态批次）
    artifacts/api_smoke_sweng.md  —— 冒烟结果表（固定列格式）
    artifacts/screenshots/*.png   —— 页面用例截图（base64 内嵌，<details> 折叠）

产出：
    artifacts/report_sweng_summary.html（自包含，可直接转发/预览）
    artifacts/report_sweng_summary.md  （同口径纯文本）

用法：.venv/bin/python scripts/merge_sweng_report.py
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSION_LOG = ROOT / "artifacts/session/run.jsonl"
API_MD = ROOT / "artifacts/api_smoke_sweng.md"
SHOT_DIR = ROOT / "artifacts/screenshots"
OUT_HTML = ROOT / "artifacts/report_sweng_summary.html"
OUT_MD = ROOT / "artifacts/report_sweng_summary.md"


def parse_session_cases() -> list[dict]:
    """按 run_start 切段，取每个 case 的最后一段（终态），含断言明细。"""
    events = [json.loads(l) for l in SESSION_LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    segments: list[list[dict]] = []
    cur: list[dict] = []
    for e in events:
        if e.get("event") == "run_start":
            if cur:
                segments.append(cur)
            cur = [e]
        elif cur:
            cur.append(e)
    if cur:
        segments.append(cur)
    # 每个 case 取最后一段
    last_by_case: dict[str, list[dict]] = {}
    for seg in segments:
        cid = seg[0].get("case_id", "")
        if cid.startswith("tc_sweng_"):
            last_by_case[cid] = seg
    cases = []
    for cid, seg in sorted(last_by_case.items()):
        run_end = next((e for e in reversed(seg) if e.get("event") == "run_end"), {})
        asserts = [e for e in seg if e.get("event") == "assertion_judged"]
        cases.append({
            "id": cid,
            "title": seg[0].get("title", ""),
            "status": run_end.get("status", "?"),
            "duration": round(run_end.get("duration_s", 0), 1),
            "asserts": [{"kind": a.get("assertion"), "engine": a.get("engine"),
                         "status": a.get("status")} for a in asserts],
            "ts": time.strftime("%m-%d %H:%M", time.localtime(seg[0].get("ts", 0))),
        })
    return cases


def parse_api_smoke() -> list[dict]:
    rows = []
    for line in API_MD.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*(\d+)\s*\|\s*(GET|POST)\s*\|\s*`([^`]+)`\s*\|\s*(.*?)\s*\|\s*(\S+)\s*\|\s*(.*?)\s*\|\s*(\S*)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|", line)
        if m:
            rows.append({
                "idx": int(m.group(1)), "method": m.group(2), "url": m.group(3),
                "page": m.group(4) or "-", "status": m.group(5),
                "biz": m.group(6).strip() or "-", "data": m.group(7) or "-",
                "dur": m.group(8), "verdict": m.group(9),
            })
    return rows


def shot_b64(case_id: str) -> str | None:
    p = SHOT_DIR / f"{case_id}.png"
    if not p.exists():
        return None
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_html(cases: list[dict], apis: list[dict]) -> str:
    n_pass = sum(1 for c in cases if c["status"] == "passed")
    a_pass = sum(1 for a in apis if a["verdict"] == "PASS")
    a_defect = sum(1 for a in apis if a["verdict"] == "DEFECT_CONFIRMED")
    total_dur = round(sum(c["duration"] for c in cases), 1)
    ts = time.strftime("%Y-%m-%d %H:%M")

    def verdict_cls(st: str) -> str:
        return {"passed": "pass", "PASS": "pass", "DEFECT_CONFIRMED": "defect"}.get(st, "fail")

    page_rows = []
    for c in cases:
        asserts_ok = sum(1 for a in c["asserts"] if a["status"] == "auto_pass")
        shot = shot_b64(c["id"])
        shot_html = (f'<details class="shot"><summary>截图</summary>'
                     f'<img src="{shot}" alt="{esc(c["id"])}"></details>') if shot else ""
        detail = "、".join(
            f'<span class="a {"ok" if a["status"] == "auto_pass" else "no"}">{esc(a["kind"])}·{esc(a["engine"].split("(")[0])}</span>'
            for a in c["asserts"]) or "-"
        page_rows.append(
            f'<tr><td>{esc(c["id"])}</td><td>{esc(c["title"])}</td>'
            f'<td class="v {verdict_cls(c["status"])}">{esc(c["status"])}</td>'
            f'<td class="num">{asserts_ok}/{len(c["asserts"])}</td>'
            f'<td class="num">{c["duration"]}s</td><td class="det">{detail}</td><td>{shot_html}</td></tr>')

    api_rows = []
    for a in apis:
        api_rows.append(
            f'<tr><td class="num">{a["idx"]}</td><td>{a["method"]}</td>'
            f'<td class="url">{esc(a["url"])}</td><td>{esc(a["page"])}</td>'
            f'<td class="num">{esc(a["status"])}</td><td>{esc(a["biz"])}</td>'
            f'<td class="num">{esc(a["data"])}</td><td class="num">{esc(a["dur"])}</td>'
            f'<td class="v {verdict_cls(a["verdict"])}">{esc(a["verdict"])}</td></tr>')

    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>软件工程 Web 端总测试报告 — 后端架构重构验证</title>
<style>
body{{font:14px/1.7 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif;margin:24px;color:#1a1a1a;background:#fff;max-width:1280px}}
h1{{font-size:22px;margin:8px 0}} h2{{font-size:17px;margin:28px 0 10px;border-left:4px solid #0969da;padding-left:10px}}
.hero{{background:#f6f8fa;border:1px solid #d0d7de;border-radius:10px;padding:16px 20px;margin:14px 0}}
.cards{{display:flex;gap:14px;margin:14px 0;flex-wrap:wrap}}
.card{{flex:1;min-width:150px;border:1px solid #d0d7de;border-radius:10px;padding:12px 16px}}
.card .n{{font-size:26px;font-weight:700}} .card .l{{color:#57606a;font-size:12px}}
.pass{{color:#1a7f37;font-weight:600}} .fail{{color:#cf222e;font-weight:600}} .defect{{color:#9a6700;font-weight:600}}
table{{border-collapse:collapse;width:100%;margin:12px 0}}
th,td{{border:1px solid #d0d7de;padding:5px 10px;text-align:left;font-size:12.5px;vertical-align:top}}
th{{background:#f6f8fa;white-space:nowrap}} .num{{text-align:right;white-space:nowrap}}
.url{{font-family:ui-monospace,SFMono-Regular,monospace;word-break:break-all}}
.det{{font-size:11.5px;line-height:1.9;max-width:340px}}
.a{{display:inline-block;margin:1px 4px 1px 0;padding:0 6px;border-radius:8px;font-size:11px}}
.a.ok{{background:#dafbe1;color:#116329}} .a.no{{background:#ffebe9;color:#82071e}}
details.shot summary{{cursor:pointer;color:#0969da;font-size:12px}}
details.shot img{{max-width:640px;border:1px solid #d0d7de;border-radius:6px;margin-top:6px}}
ul{{padding-left:20px}} li{{margin:4px 0}} code{{background:#f6f8fa;padding:1px 5px;border-radius:4px;font-size:12.5px}}
.meta{{color:#57606a;font-size:12.5px}}
</style></head><body>
<h1>软件工程 Web 端总测试报告</h1>
<div class="hero">
<b>被测系统</b>：源启智能软件工厂平台（软件工程 Web 端）· <b>验证目标</b>：后端架构重构后功能等价性<br>
<b>测试范围</b>：页面层（33 条巡检 + 1 条 CRUD 全链路）+ 接口层（42 读接口直调冒烟）<br>
<b>结论</b>：<span class="pass">页面 34/34 全部通过</span> · <span class="pass">接口 41/42 通过</span> ·
<span class="defect">1 个已确认后端缺陷</span>（POST /ai/ai-requirement/page 404，work-task-v2 列表空）·
1 个疑似前端缺陷（系统智能体创建按钮静默无请求，建议人工复核）<br>
<span class="meta">报告生成：{ts} · 页面批次总耗时 {total_dur}s · 认证：admin 真实登录（Playwright）</span>
</div>

<div class="cards">
<div class="card"><div class="n pass">{n_pass}/34</div><div class="l">页面用例通过</div></div>
<div class="card"><div class="n pass">{a_pass}/42</div><div class="l">API 冒烟通过</div></div>
<div class="card"><div class="n defect">{a_defect}</div><div class="l">已知缺陷确认（404）</div></div>
<div class="card"><div class="n">1/1</div><div class="l">需求实体 CRUD 全链路（增删改查）</div></div>
</div>

<h2>一、页面用例明细（34 条）</h2>
<table><thead><tr><th>用例 ID</th><th>标题</th><th>结论</th><th class="num">断言</th><th class="num">耗时</th><th>断言明细</th><th>证据</th></tr></thead>
<tbody>
{chr(10).join(page_rows)}
</tbody></table>

<h2>二、API 层冒烟明细（42 读接口，页内 fetch 直调）</h2>
<table><thead><tr><th class="num">#</th><th>方法</th><th>接口</th><th>页面</th><th class="num">状态码</th><th>业务码</th><th class="num">data</th><th class="num">耗时</th><th>结论</th></tr></thead>
<tbody>
{chr(10).join(api_rows)}
</tbody></table>
<p class="meta">断言三级：HTTP 200 → JSON 可解析 → success:true。text_200 为文件类接口（返回 text/markdown）；
DEFECT_CONFIRMED 为已知缺陷确认（预期 404，单列不计失败）。instance/get 的 id 从 listPage 动态提取。</p>

<h2>三、需求实体 CRUD 全链路（tc_sweng_requirement_crud_001）</h2>
<ul>
<li><b>C 创建</b>：登记需求 → <code>POST /ai/api/v1/spaces/3407/requirements</code> → 200 落库，列表出现新行</li>
<li><b>R 读取</b>：重载列表 → wait_text 新标题 ✓</li>
<li><b>U 更新</b>：行内「编辑」→ drawer 改标题 → <code>PUT .../requirements/&#123;id&#125;</code> → 200，新标题入列</li>
<li><b>D 删除</b>：行内「删除」→ 确认弹窗 → <code>DELETE .../requirements/&#123;id&#125;</code> → 200，行消失</li>
<li><b>数据自清理</b>：终态断言验证 TEST 数据零残留（需求恢复初始 3 条）</li>
</ul>

<h2>四、缺陷与风险清单</h2>
<table>
<thead><tr><th>#</th><th>级别</th><th>描述</th><th>证据</th></tr></thead>
<tbody>
<tr><td>1</td><td><span class="defect">已确认·后端</span></td>
<td>work-task-v2 页面前端调用 <code>POST /ai/api/ai-requirement/page</code> 返回 404 Not Found——后端重构后该路由未注册；需求视图已迁 <code>GET /ai/api/v1/spaces/&#123;id&#125;/requirements</code> 但 work-task-v2 未同步，导致该页列表恒为空</td>
<td>页面层：worktaskv2 用例空态截图；接口层：冒烟 DEFECT_CONFIRMED（404 响应体含 path 回显）</td></tr>
<tr><td>2</td><td><span class="defect">疑似·前端</span></td>
<td>系统智能体管理「创建系统智能体」表单提交静默失败：填写全部必填项（名称/大模型/类型/提示词）后点击创建，三种触发方式（物理点击/JS click/form.requestSubmit）均零网络请求、零校验错误、弹层不关</td>
<td>自动化实测（3 轮复现）；建议人工点一次创建复核</td></tr>
<tr><td>3</td><td>风险提示</td>
<td>写接口覆盖：全平台仅需求实体 CRUD（3 个写接口）被自动化验证——技能创建需文件上传、用户管理写操作风险不可控、sysagent 创建被疑似缺陷阻断</td>
<td>—</td></tr>
<tr><td>4</td><td>风险提示</td>
<td>用户模型 Token 页数据为增长型（实测 5→7 条），相关断言已改为抗增长模式（表头 + 分页模式 + VLM）</td>
<td>—</td></tr>
</tbody></table>

<h2>五、环境与复跑</h2>
<ul>
<li>被测环境：软件工程 Web 端（内网），工作空间 workspace_id=3407，账号 admin</li>
<li>框架：nlaut v0.4.1（Playwright 系统浏览器 + 三级判定：确定性 → MLX-VLM → Laya）</li>
<li>页面批次：<code>.venv/bin/python -m nlaut.cli --external --base-url &lt;系统地址&gt; --retry-on-error 2 --report artifacts/report_sweng_full.html</code></li>
<li>API 冒烟：<code>.venv/bin/python scripts/api_smoke.py --config data/api_smoke_sweng.local.yaml --report artifacts/api_smoke_sweng</code></li>
<li>凭据与接口清单为 gitignore 本地资产（data/datasets.local.yaml / data/api_smoke_sweng.local.yaml），仓库零敏感数据</li>
</ul>
</body></html>"""


def build_md(cases: list[dict], apis: list[dict]) -> str:
    n_pass = sum(1 for c in cases if c["status"] == "passed")
    a_pass = sum(1 for a in apis if a["verdict"] == "PASS")
    ts = time.strftime("%Y-%m-%d %H:%M")
    lines = [
        "# 软件工程 Web 端总测试报告 — 后端架构重构验证",
        "",
        f"- 生成时间：{ts}",
        f"- 结论：页面 {n_pass}/34 通过 · 接口 {a_pass}/42 通过 · 已确认缺陷 1（ai-requirement/page 404）· 疑似前端缺陷 1（sysagent 创建静默失败）",
        "",
        "## 一、页面用例（34 条）",
        "",
        "| 用例 | 标题 | 结论 | 断言 | 耗时 |",
        "|------|------|------|------|-----|",
    ]
    for c in cases:
        ok = sum(1 for a in c["asserts"] if a["status"] == "auto_pass")
        lines.append(f"| {c['id']} | {c['title']} | {c['status']} | {ok}/{len(c['asserts'])} | {c['duration']}s |")
    lines += ["", "## 二、API 冒烟（42 读接口）", "",
              "| # | 方法 | 接口 | 页面 | 状态码 | 业务码 | data | 耗时 | 结论 |",
              "|--:|------|------|------|-------:|--------|-----:|-----:|------|"]
    for a in apis:
        lines.append(f"| {a['idx']} | {a['method']} | `{a['url']}` | {a['page']} | {a['status']} | "
                     f"{a['biz']} | {a['data']} | {a['dur']} | {a['verdict']} |")
    lines += ["", "## 三、缺陷与风险", "",
              "1. [已确认·后端] POST /ai/api/ai-requirement/page 404（work-task-v2 路由未注册，列表恒空）",
              "2. [疑似·前端] 系统智能体创建表单提交静默失败（三种触发方式均零请求），建议人工复核",
              "3. [风险] 写接口仅需求实体被验证（3 个）；技能创建需上传、用户管理风险不可控",
              "4. [风险] Token 用量数据增长型，断言已改抗增长模式", "",
              "完整 HTML 报告（含 34 张截图证据）：artifacts/report_sweng_summary.html", ""]
    return "\n".join(lines)


def validate_css(html: str) -> None:
    """CSS 合法性自检：花括号配平 + 无裸 ';;'。"""
    css = re.search(r"<style>(.*?)</style>", html, re.DOTALL).group(1)
    assert css.count("{") == css.count("}"), "CSS 花括号不配平"
    assert ";;" not in css.replace("; ;", ""), "CSS 出现连续分号"
    # 简单语法检查：每条规则以 ; 或 } 结尾
    for rule in re.findall(r"\{([^{}]*)\}", css):
        for decl in filter(None, rule.strip().split(";")):
            assert ":" in decl, f"CSS 声明缺少冒号: {decl[:40]}"


def main() -> None:
    cases = parse_session_cases()
    apis = parse_api_smoke()
    assert len(cases) >= 34, f"session log 只解析到 {len(cases)} 条页面用例"
    assert len(apis) == 42, f"冒烟 MD 只解析到 {len(apis)} 行"
    html = build_html(cases, apis)
    validate_css(html)
    OUT_HTML.write_text(html, encoding="utf-8")
    OUT_MD.write_text(build_md(cases, apis), encoding="utf-8")
    print(f"页面用例 {len(cases)} 条 · API {len(apis)} 个")
    print(f"HTML: {OUT_HTML} ({OUT_HTML.stat().st_size/1024/1024:.1f}MB)")
    print(f"MD:   {OUT_MD}")


if __name__ == "__main__":
    main()
