"""飞书多维表格桥接：口述测试需求表 → nlaut 执行链。

状态流转（与 docs/verbal-to-ir-protocol.md 一致）:
    待生成 → 待审核 →（需澄清）→ 已确认 → 执行中 → 已完成/失败

用法:
    python -m nlaut.feishu_bridge --once      # 扫一轮（生成草案+写回）
    python -m nlaut.feishu_bridge --watch     # 常驻轮询（cron 用）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

APP_TOKEN = "Yfm7bQmFeaEwYxsLYJAcQozRnah"
TABLE_ID = "tblK7ug76gQyLpVe"
_BASE = "https://open.feishu.cn/open-apis"
_CONN_TIMEOUT = 15

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE  # 公司网关 MITM 证书，与既有链路一致


def _env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(f"缺少环境变量 {key}（nlaut 飞书凭据未注入）")
    return val


def _call(method: str, url: str, data: dict | None = None, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode() if data is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=_CONN_TIMEOUT, context=_CTX) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def get_token() -> str:
    resp = _call(
        "POST",
        f"{_BASE}/auth/v3/tenant_access_token/internal",
        {"app_id": _env("FEISHU_APP_ID"), "app_secret": _env("FEISHU_APP_SECRET")},
    )
    if resp.get("code") != 0:
        raise RuntimeError(f"飞书鉴权失败: {resp.get('msg')}")
    return resp["tenant_access_token"]


# ---------------------------------------------------------------- 扫描
def fetch_pending(token: str) -> list[dict]:
    """拉全部记录，返回还没走完流程的行（待生成）。"""
    rows = []
    page_token = ""
    while True:
        url = (
            f"{_BASE}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records"
            f"?page_size=50&page_token={page_token}"
        )
        resp = _call("GET", url, token=token)
        if resp.get("code") != 0:
            raise RuntimeError(f"拉取记录失败: {resp.get('msg')}")
        data = resp["data"]
        rows.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")
    return rows


def update_record(token: str, record_id: str, fields: dict) -> dict:
    """更新一行（状态/草案/结果/报告链接）。"""
    resp = _call(
        "PUT",
        f"{_BASE}/bitable/v1/apps/{APP_TOKEN}/tables/{TABLE_ID}/records/{record_id}",
        {"fields": fields},
        token,
    )
    return resp


# ---------------------------------------------------------------- IR 草案生成（当前=确定性规则，M1 换模型 API）
_IR_TEMPLATES = {
    "锁定": {
        "title": "登录-连错密码-{n}次-账号锁定并拒绝正确密码",
        "data_ref": "login_lock_demo",
        "dataset": {"user": "testlock", "wrong_pwd": "Wrong@999", "right_pwd": "Lock@123"},
        "steps": [
            {"action": "nav", "path": "/login"},
            {"action": "fill", "selector": "#username", "value": "$user"},
            {"action": "fill", "selector": "#password", "value": "$wrong_pwd"},
            {"action": "fill", "selector": "#password", "value": "$wrong_pwd"},
            {"action": "fill", "selector": "new_pwd", "value": "$wrong_pwd"},
            {"action": "click", "selector": "button[type=submit]"},
            {"action": "wait_visible", "selector": ".locked", "timeout_ms": 5000},
        ],
        "assertions": [
            {"kind": "text_visible", "selector": ".locked", "expected": "锁定"},
            {"kind": "visual_state", "prompt": "页面是否显示账号被锁定的提示（黄色警示样式），且没有出现登录成功的欢迎横幅？只答 yes 或 no。", "threshold": 0.9},
        ],
        "postconditions": [{"cleanup_tag": "RESET_LOCK_STATE"}],
    },
    "任务搜索": {
        "title": "任务列表-按任务名称搜索-结果正确匹配",
        "data_ref": None,
        "dataset": {},
        "kw_from_verbal": r"搜[索]?[\s\S]*?(?:「\s*?([\u4e00-\u9fa5A-Za-z0-9]+?)\s*?」|\"([^\"]+)\"|搜[索]?(.+?)(?:,|，|应|该|$))",
        "default_kw": "冒烟",
        "steps_template": [
            {"action": "nav", "path": "/tasks"},
            {"action": "wait_visible", "selector": "#kw", "timeout_ms": 5000},
            {"action": "fill", "selector": "#kw", "value": "$kw"},
            {"action": "click", "selector": "#btn-search"},
            {"action": "wait_visible", "selector": "table.task-table", "timeout_ms": 5000},
        ],
        "steps": [
            {"action": "nav", "path": "/tasks"},
            {"action": "wait_visible", "selector": "#kw", "timeout_ms": 5000},
            {"action": "fill", "selector": "#kw", "value": "冒烟"},
            {"action": "click", "selector": "#btn-search"},
            {"action": "wait_visible", "selector": "table.task-table", "timeout_ms": 5000},
        ],
        "assertions": [
            {"kind": "text_visible", "selector": ".result-count", "expected": "共 6 条结果"},
            {"kind": "text_visible", "selector": "table.task-table", "expected": "冒烟测试-001"},
            {"kind": "visual_state", "prompt": "任务列表页搜索'冒烟'后：表格是否只显示冒烟测试类型的任务（结果计数显示共 6 条），且每行任务名称都包含'冒烟测试'字样？只答 yes 或 no。", "threshold": 0.9},
        ],
        "postconditions": [],
    },
    "任务状态筛选": {
        "title": "任务列表-下拉筛选状态-仅显示所选状态任务",
        "steps": [
            {"action": "nav", "path": "/tasks"},
            {"action": "wait_visible", "selector": "#f-status", "timeout_ms": 5000},
            {"action": "select_option", "selector": "#f-status", "value": "已完成"},
            {"action": "click", "selector": "#btn-search"},
            {"action": "wait_visible", "selector": "table.task-table", "timeout_ms": 5000},
        ],
        "assertions": [
            {"kind": "text_visible", "selector": ".result-count", "expected": "共 8 条结果"},
            {"kind": "visual_state", "prompt": "任务列表筛选'已完成'状态后：表格中所有任务的状态列是否都显示'已完成'（绿色文字），结果计数为 8 条？只答 yes 或 no。", "threshold": 0.9},
        ],
        "postconditions": [],
    },
    "任务空结果": {
        "title": "任务列表-搜索无结果-提示未找到匹配任务",
        "kw": "不存在的关键词xyz",
        "steps": [
            {"action": "nav", "path": "/tasks"},
            {"action": "wait_visible", "selector": "#kw", "timeout_ms": 5000},
            {"action": "fill", "selector": "#kw", "value": "不存在的关键词xyz"},
            {"action": "click", "selector": "#btn-search"},
            {"action": "wait_visible", "selector": ".empty-tip", "timeout_ms": 5000},
        ],
        "assertions": [
            {"kind": "text_visible", "selector": ".empty-tip", "expected": "未找到匹配的任务"},
            {"kind": "text_visible", "selector": ".result-count", "expected": "共 0 条结果"},
            {"kind": "visual_state", "prompt": "任务列表搜索无结果时：页面是否显示'未找到匹配的任务'的空态提示（居中灰色文字），结果计数为 0，页面无报错无异常？只答 yes 或 no。", "threshold": 0.9},
        ],
        "postconditions": [],
    },
}


def draft_from_verbal(verbal: str, system: str) -> dict:
    """口述 → IR 草案 dict（当前用模板+正则，M1 接模型 API 后替换）。"""
    # ⓪ 大模型能力测试（v0.2.7 新增：口述含「测试XXX模型能力」即触发 LLM 批次）
    model_name = _extract_model_name(verbal) or _extract_model_name(system or "")
    if _looks_like_llm_test(verbal, system or ""):
        if not model_name:
            # 带了 endpoint 却没识别出模型名（如口述写「某某某」占位）——用被测系统列，再不行提示
            base_url, api_key = _extract_llm_endpoint(verbal) or _extract_llm_endpoint(system or "")
            if (verbal or "").strip() and (base_url or api_key):
                model_name = (system or "").strip() or "un-named-model"
            else:
                return {
                    "need_clarify": True,
                    "reason": "识别到大模型测试意图，但没读到模型名和地址/key。请按句式补全："
                              "测试<模型名>模型能力，模型地址是：<URL>，key是：<KEY>",
                }
        return _llm_batch_draft(verbal, model_name, system or "")

    # ① 任务搜索/筛选/空结果（演示列表页新增场景）
    if any(kw in (verbal or "") for kw in ["任务列表", "任务搜索", "任务筛选", "按任务名称", "按.*筛选"]):
        actor = "任务列表页"
        obj = "搜索框与结果表格"
        action = "搜索任务"
        # 找关键词
        kw = "冒烟"
        m = re.search(r"[搜搜索][索]?[「\"](.+?)[」\"]", verbal or "")
        if m:
            kw = m.group(1)
        elif "空" in (verbal or "") or "不存在" in (verbal or ""):
            return _expand_template("任务空结果", verbal, next_id("tasks"), actor, obj, action)
        # 状态筛选用 现成模板
        if "筛选" in (verbal or "") or "已完成" in (verbal or "") or "状态" in (verbal or ""):
            return _expand_template("任务状态筛选", verbal, next_id("tasks"), actor, obj, action)
        # 默认搜索
        t = _IR_TEMPLATES["任务搜索"]
        return {
            "id": next_id("tasks"),
            "title": t["title"],
            "source": verbal,
            "data_ref": t["data_ref"],
            "dataset": {"kw": kw},
            "actor": actor,
            "object": obj,
            "action": action,
            "steps": [
                {"action": "nav", "path": "/tasks"},
                {"action": "wait_visible", "selector": "#kw", "timeout_ms": 5000},
                {"action": "fill", "selector": "#kw", "value": kw},
                {"action": "click", "selector": "#btn-search"},
                {"action": "wait_visible", "selector": "table.task-table", "timeout_ms": 5000},
            ],
            "assertions": t["assertions"],
            "postconditions": [],
        }

    # ② 登录锁定（原有模板）
    m = re.search(r"连错(\d+)次", verbal or "")
    if m:
        n = m.group(1)
        t = _IR_TEMPLATES["锁定"]
        return {
            "id": f"tc_login_lock_{n}",
            "title": t["title"].format(n=n),
            "source": verbal,
            "data_ref": t["data_ref"],
            "dataset": t["dataset"],
            "steps": t["steps"],
            "assertions": t["assertions"],
            "postconditions": t["postconditions"],
        }

    # ③ 未命中: 真实澄清（语义改写，不再把责任推给用户）
    return {}


# ---------------------------------------------------------------- 大模型能力测试（v0.2.7）
_LLM_TEST_HINTS = ("模型能力", "大模型", "模型测试", "能力测试", "llm测试", "测试.*模型")

# 口述里的地址/key 提取（v0.2.8：临时模型免预配置，直接口述携带）
_RE_BASE_URL = re.compile(
    r"(?:模型地址|接口地址|地址|base[_\s-]?url|baseurl)[^\n]{0,6}"
    r"(https?://[^\s,，;；、\"']+)", re.IGNORECASE)
_RE_API_KEY = re.compile(
    r"(?:模型(?:的)?(?:key|密钥)|api[_\s-]?key|key是|(?<=[,，;；\s:])key(?=[是:：=\s])|(?<=[,，;；\s])密钥)"
    r"[是:：=\s]*([A-Za-z0-9_\-\.]{8,})", re.IGNORECASE)


def _mask_key(text: str) -> str:
    """把口述文本里的 key 值打码（写表格/日志前必过）。"""
    if not text:
        return text
    return _RE_API_KEY.sub(lambda m: f"key:***{m.group(1)[-4:]}", text)


def _extract_llm_endpoint(text: str) -> tuple[str, str]:
    """从口述提取 (base_url, api_key)。二者均可为空（为空则回落 models.yaml 预设）。"""
    if not text:
        return "", ""
    m_url = _RE_BASE_URL.search(text)
    m_key = _RE_API_KEY.search(text)
    base_url = m_url.group(1).rstrip("/") if m_url else ""
    api_key = m_key.group(1) if m_key else ""
    return base_url, api_key


def _load_models_yaml() -> dict:
    """读 config/models.yaml 的 models 节（不存在返回空）。"""
    import yaml

    p = Path(__file__).resolve().parents[2] / "config" / "models.yaml"
    if not p.exists():
        return {}
    cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return cfg.get("models", {})


def _extract_model_name(text: str) -> str:
    """从口述/被测系统列提取模型名。

    优先匹配 config/models.yaml 已配置名（含 model_id 别名）；
    未命中且口述携带了地址/key 时，按「测试<名>模型/大模型」句式捕获裸名（临时模型）。"""
    if not text:
        return ""
    models = _load_models_yaml()
    # 精确匹配：口述里直接写了配置名（含别名 model_id）
    names = sorted(models.keys(), key=len, reverse=True)
    ids = {m.get("model_id", ""): k for k, m in models.items() if m.get("model_id")}
    for n in names + list(ids.keys()):
        if n and n in text:
            return ids.get(n, n)
    # 临时模型 fallback：口述带了 endpoint 时按句式捕获裸名（v0.2.8）
    url, key = _extract_llm_endpoint(text)
    if url or key:
        m = re.search(r"测[试评]?\s*([A-Za-z0-9_\-\.]+)\s*(?:的能力|大?模型|模型能力)", text)
        if m:
            return m.group(1)
        m2 = re.search(r"模型名[称是:：=\s]+([A-Za-z0-9_\-\.]+)", text)
        if m2:
            return m2.group(1)
    return ""


def _looks_like_llm_test(verbal: str, system: str) -> bool:
    """口述是否表达「测某个模型」（而非 Web 场景）。"""
    text = f"{verbal or ''} {system or ''}"
    return any(h in text for h in ("模型能力", "大模型", "模型测试", "能力测试", "的能力", "测模型"))


def _llm_batch_draft(verbal: str, model_name: str, system: str) -> dict:
    """生成大模型能力测试批次声明（执行时跑 cases/llm 的 P0 批次 + 可选维度过滤）。

    v0.2.8：口述可直接携带模型地址和 key（「模型地址是：https://…，key是：sk-…」），
    此时无需在 config/models.yaml 预配置；未携带则回落预设。key 不写入任何持久化文本。"""
    models = _load_models_yaml()
    cfg = models.get(model_name, {})
    # 口述携带的 endpoint（优先于预设）
    base_url, api_key = _extract_llm_endpoint(verbal) or _extract_llm_endpoint(system)
    eff_base_url = base_url or cfg.get("base_url", "")
    key_via = "口述携带" if api_key else (f"环境变量 {cfg.get('api_key_env', '')}" if cfg.get("api_key_env") else "缺失")
    # 优先级：口述说「全量/P1」就跑 P1（P0+P1），否则默认 P0
    want_p1 = any(kw in verbal for kw in ("全量", "P1", "p1", "全部能力"))
    priority = "P0,P1" if want_p1 else "P0"
    # 维度过滤：口述点名某维度（如「重点测工具调用」→ tool 集）
    dims = ("tool", "code", "safety", "stream", "math", "reason", "rag", "ctx", "dialog", "instruct", "know", "nlu", "creative", "lang", "perf")
    dim_hits = [d for d in dims if d in (verbal or "").lower()]
    dim_note = f"，重点维度: {','.join(dim_hits)}" if dim_hits else ""

    return {
        "id": f"tc_llm_batch_{re.sub(r'[^a-z0-9]', '_', model_name.lower())}",
        "title": f"大模型能力测试-{model_name}-{priority}批次",
        "source": verbal,
        "channel": "api",
        "model": model_name,
        "priority": priority,
        "dims": dim_hits,
        "model_cfg": {
            "base_url": eff_base_url,
            "api_key_env": cfg.get("api_key_env", ""),
            "model_id": cfg.get("model_id", model_name),
            "inline_api_key": api_key,  # 仅存在于内存/运行期，严禁写入 YAML/表格
            "key_via": key_via,
        },
        "_llm_batch": True,  # 特殊标记：不是单用例，是批次编排
        "note": f"执行时运行 cases/llm 全部 {priority} 用例{dim_note}，模型 {model_name}（{cfg.get('provider', '临时模型' if api_key or base_url else '?')}）",
    }


def _llm_batch_yaml(draft: dict) -> str:
    """批次声明 → 表格可读文本（说明型，非执行 IR）。

    注意：api_key 永不写入此处（表格/表格导出都是泄漏面）。"""
    cfg = draft.get("model_cfg", {})
    lines = [
        "# 大模型能力测试批次（channel: api）",
        f"模型: {draft['model']}  (model_id: {cfg.get('model_id')})",
        f"批次: {draft['priority']}  (执行命令: nlaut.cli --model {draft['model']} --api-only --priority {draft['priority'].replace(',', '+')})",
        f"接口: {cfg.get('base_url')}",
        f"Key: {cfg.get('key_via', '环境变量')}",  # 只写来源，不写值
        f"来源口述: {draft['source']}",
    ]
    if draft.get("dims"):
        lines.append(f"重点维度: {','.join(draft['dims'])}")
    # 来源口述打码（key 值绝不落表格）
    masked = _mask_key(draft.get("source", ""))
    lines = [l.replace(draft.get("source", "\x00"), masked) if draft.get("source") else l for l in lines]
    return "\n".join(lines)


def _run_llm_batch(token, repo_dir, rid, model_name, prio,
                   base_url: str = "", inline_api_key: str = "") -> dict:
    """执行大模型能力批次：--model X --api-only --priority P0（45 条左右真实 API 调用）。

    v0.2.8：口述携带的地址/key 经环境变量注入子进程（LLM_API_KEY 优先级在 cli 里
    低于 --api-key，但为避免 key 出现在 ps 命令行里，改走 env 传递 + cli 已支持）。"""
    update_record(token, rid, {"状态": "执行中"})
    safe_name = re.sub(r"[^a-z0-9]", "_", model_name.lower())
    report_path = f"artifacts/report_llm_{safe_name}.html"
    cmd = [
        str(repo_dir / ".venv/bin/python"), "-m", "nlaut.cli",
        "--model", model_name,
        "--api-only",
        "--priority", prio,
        "--report", report_path,
    ]
    env = {**os.environ, "NO_PROXY": "127.0.0.1,localhost"}
    if base_url:
        cmd += ["--api-base", base_url]
    if inline_api_key:
        # key 走 env（防 ps 泄漏），cli 侧 LLM_API_KEY 兜底读取
        env["LLM_API_KEY"] = inline_api_key
    try:
        proc = subprocess.run(cmd, cwd=repo_dir, env=env, check=False,
                              capture_output=True, text=True, timeout=3600)
        summary = (proc.stdout[-600:] if proc.stdout else proc.stderr[-600:])
        ok = proc.returncode == 0
    except subprocess.TimeoutExpired:
        summary = "执行超时（>60 分钟）"
        ok = False
    report_loc = publish_report(
        token, repo_dir, f"llm_{safe_name}",
        report_name=f"nlaut_report_llm_{model_name}.html",
        report_path=report_path,
    )
    update_record(token, rid, {
        "状态": "已完成" if ok else "失败",
        "执行结果": summary,
        "报告位置": report_loc,
    })
    return {"record_id": rid, "action": "llm_batch_executed",
            "model": model_name, "ok": ok, "report": report_loc}


def _expand_template(key: str, verbal: str, case_id: str,
                     actor: str = "演示系统", obj: str = "目标页面",
                     action: str = "按口述操作") -> dict:
    """从模板生成 IR dict。"""
    t = _IR_TEMPLATES[key]
    return {
        "id": case_id,
        "title": t["title"],
        "source": verbal,
        "data_ref": t.get("data_ref"),
        "dataset": t.get("dataset", {}),
        "actor": actor,
        "object": obj,
        "action": action,
        "steps": t["steps"],
        "assertions": t["assertions"],
        "postconditions": t["postconditions"],
    }


_task_counter = {"n": 5}


def next_id(prefix: str) -> str:
    """生成自增用例 id（如 tc_tasks_006）。"""
    _task_counter["n"] += 1
    return f"tc_{prefix}_{_task_counter['n']:03d}"


def ir_yaml(ir: dict) -> str:
    """dict → 紧凑 YAML 文本（人可读、可直接入库）。"""
    actor = ir.get("actor", "演示系统")
    obj = ir.get("object", "目标页面")
    lines = [f"id: {ir['id']}", f"title: {ir['title']}"]
    lines.append(f'source: "口述: {ir["source"]}"')
    lines.append("req_ref: null")
    lines.append("priority: P1")
    lines.append("channel: web")
    lines.append("quad:")
    lines.append(f"  actor: {actor}")
    lines.append(f"  action: {ir.get('action', '按口述操作')}")
    lines.append(f"  object: {obj}")
    lines.append("  preconditions:")
    lines.append('    - "演示系统已启动且种子数据就绪"')
    if ir.get("data_ref"):
        lines.append(f"data_ref: {ir['data_ref']}")
    lines.append("steps:")
    for s in ir.get("steps", []):
        parts = ", ".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in s.items())
        lines.append(f"  - {{{parts}}}")
    lines.append("assertions:")
    for a in ir.get("assertions", []):
        parts = ", ".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in a.items())
        lines.append(f"  - {{{parts}}}")
    lines.append("postconditions: []")
    return "\n".join(lines)


# ---------------------------------------------------------------- 主流程
def process_once(token: str, repo_dir: Path, dry_run: bool = False) -> list[dict]:
    """扫一轮表：待生成→写草案；已确认→执行+写回结果。"""
    actions = []
    rows = fetch_pending(token)

    for item in rows:
        f = item["fields"]
        rid = item["record_id"]
        verbal = f.get("口述内容", "")
        status = (f.get("状态") or "").strip()

        # 需澄清行：已有 AI 追问，跳过（等提出人补充）
        if status == "需澄清":
            continue

        # 待生成 → 草案 + 真实入库到 cases/（让 cases/ 成为唯一事实源）
        if status == "待生成" or status == "":
            draft = draft_from_verbal(verbal, f.get("被测系统", ""))
            if draft:
                if draft.get("_llm_batch"):
                    # 大模型能力批次：零动作直达——写批次声明后立即执行（不设确认卡点）
                    update_record(token, rid, {
                        "IR草案": _llm_batch_yaml(draft),
                        "状态": "执行中",
                        "AI追问": f"✓ 已识别大模型能力批次，正在执行：--model {draft['model']} --api-only --priority {draft['priority']}",
                    })
                    act = _run_llm_batch(token, repo_dir, rid, draft["model"],
                                         draft["priority"].replace(",", "+"),
                                         base_url=draft["model_cfg"].get("base_url", ""),
                                         inline_api_key=draft["model_cfg"].get("inline_api_key", ""))
                    actions.append(act)
                    continue
                yaml_text = ir_yaml(draft)
                # 真正写入 cases/demo/<id>.yaml（AGENTS.md 要求的唯一事实源）
                case_path = repo_dir / "cases" / "demo" / f"{draft['id']}.yaml"
                case_path.parent.mkdir(parents=True, exist_ok=True)
                case_path.write_text(yaml_text + "\n", encoding="utf-8")
                update_record(token, rid, {
                    "IR草案": yaml_text,
                    "状态": "待审核",
                    "AI追问": f"✓ IR 已入库到 {case_path.relative_to(repo_dir)}",
                })
                actions.append({
                    "record_id": rid,
                    "action": "drafted",
                    "verbal": verbal[:30],
                    "case_file": str(case_path.relative_to(repo_dir)),
                })
            else:
                # 文案澄清：诚实说明是模板覆盖度问题，不是用户口述问题
                update_record(token, rid, {
                    "AI追问": "🤖 生成层模板暂未覆盖这条口述的场景。建议：①在对话里直接跟我说口述（我会按协议生成 IR）②或补一句你期望看到的结果，比如「应只看到 6 条冒烟任务」。",
                    "状态": "需澄清",
                })
                actions.append({"record_id": "..", "action": "clarify_needed", "verbal": verbal[:30]})
            continue

        # 已确认 → 执行（找不到文件时兜底从表格落盘）
        if status == "已确认":
            # 兼容旧批次行（新版在「待生成」阶段即执行，这里是历史行重跑入口）
            draft_text = f.get("IR草案") or ""
            if "大模型能力测试批次" in draft_text:
                m_model = re.search(r"^模型: (\S+)", draft_text, re.MULTILINE)
                m_prio = re.search(r"^批次: (\S+)", draft_text, re.MULTILINE)
                model_name = m_model.group(1) if m_model else ""
                prio = (m_prio.group(1) if m_prio else "P0").replace("+", ",")
                if not model_name:
                    update_record(token, rid, {
                        "状态": "需澄清",
                        "AI追问": "⚠️ 批次声明里读不出模型名，请在「被测系统」列填模型名（config/models.yaml 中已配置的）。",
                    })
                    continue
                act = _run_llm_batch(token, repo_dir, rid, model_name,
                                     prio.replace(",", "+"))
                actions.append(act)
                continue

            case_file = repo_dir / "cases" / "demo" / f"{draft_id_from(f)}.yaml"
            if not case_file.exists():
                # 兜底：把表格里的草案文本直接落盘（旧版本曾漏写，给个自愈机会）
                if f.get("IR草案"):
                    case_file.parent.mkdir(parents=True, exist_ok=True)
                    case_file.write_text(f["IR草案"], encoding="utf-8")
                    update_record(token, rid, {
                        "AI追问": f"⚠️ IR 之前没自动入库，刚补写到 {case_file.relative_to(repo_dir)}。下面立即执行一次。",
                    })
                else:
                    update_record(token, rid, {
                        "状态": "需澄清",
                        "AI追问": "⚠️ 状态是「已确认」但仓库里找不到 IR 文件，且表格没有 IR草案可兜底。请把状态改回「待审核」等下一轮扫描，或在对话里跟我说一声重新生成。",
                    })
                    continue
            # 至此 case_file 必然存在——执行
            update_record(token, rid, {"状态": "执行中"})
            cmd = [
                str(repo_dir / ".venv/bin/python"), "-m", "nlaut.cli",
                "--case", case_file.stem,
            ]
            env = {**os.environ, "NO_PROXY": "127.0.0.1,localhost"}
            proc = subprocess.run(cmd, cwd=repo_dir, env=env, check=False,
                                  capture_output=True, text=True, timeout=600)
            summary = proc.stdout[-600:] if proc.stdout else proc.stderr[-600:]
            ok = proc.returncode == 0
            report_loc = publish_report(token, repo_dir, case_file.stem)
            update_record(token, rid, {
                "状态": "已完成" if ok else "失败",
                "执行结果": summary,
                "报告位置": report_loc,
            })
            actions.append({"record_id": rid, "action": "executed", "ok": ok, "report": report_loc})
            continue

    return actions


# ---------------------------------------------------------------- 报告分发
CHAT_ID = "oc_f0ad8ca94fed6bcb6a1990d0889617bd"  # 「nlaut 测试自动化通知」群


def publish_report(token: str, repo_dir: Path, case_id: str,
                   report_name: str = "", report_path: str = "") -> str:
    """执行后把 HTML 报告上传到飞书群并返回位置描述。

    上传走 im/v1/files（multipart），随后发 file 消息进群；返回写进表格的报告位置文案。
    失败不阻塞主流程——返回错误说明文本，链路继续。"""
    import time as _time
    import uuid as _uuid

    report = repo_dir / report_path.lstrip("/") if report_path else repo_dir / "artifacts" / "report.html"
    if not report.exists():
        return f"报告未生成（{report_path or 'artifacts/report.html'} 缺失）"
    fname = report_name or f"nlaut_report_{_time.strftime('%Y%m%d_%H%M')}_{case_id}.html"
    try:
        boundary = _uuid.uuid4().hex
        body = b""
        for k, v in [
            ("file_type", "stream"),
            ("file_name", fname),
            ("file_size", str(report.stat().st_size)),
            ("duration", "0"),
        ]:
            body += (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
            ).encode()
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{fname}"\r\nContent-Type: text/html\r\n\r\n'
        ).encode()
        body += report.read_bytes()
        body += f"\r\n--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/im/v1/files",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30, context=_CTX) as resp:
            up = json.loads(resp.read())
        if up.get("code") != 0:
            return f"报告上传失败: {str(up.get('msg'))[:60]}"
        file_key = up["data"]["file_key"]

        msg = _call(
            "POST",
            f"{_BASE}/im/v1/messages?receive_id_type=chat_id",
            {
                "receive_id": CHAT_ID,
                "msg_type": "file",
                "content": json.dumps({"file_key": file_key}),
            },
            token,
        )
        if msg.get("code") != 0:
            return f"报告消息发送失败: {str(msg.get('msg'))[:60]}"
        return f"群「nlaut 测试自动化通知」: {fname}"
    except Exception as e:  # noqa: BLE001
        return f"报告分发异常: {e}"


def draft_id_from(fields: dict) -> str:
    """从 IR 草案文本提取 id（首个 `id:` 行）。"""
    text = fields.get("IR草案") or ""
    m = re.search(r"^id:\s*(\S+)", text, re.MULTILINE)
    return m.group(1) if m else ""


def main() -> None:
    parser = argparse.ArgumentParser(description="飞书口述表 ↔ nlaut 桥接")
    parser.add_argument("--once", action="store_true", help="扫一轮")
    parser.add_argument("--watch", action="store_true", help="常驻轮询（默认 5 分钟）")
    parser.add_argument("--interval", type=int, default=300, help="轮询间隔秒（watch 模式）")
    args = parser.parse_args()
    if not (args.once or args.watch):
        args.once = True

    repo_dir = Path(__file__).resolve().parents[2]
    token = get_token()
    if args.once:
        actions = process_once(token, repo_dir)
        print(json.dumps(actions, ensure_ascii=False, indent=1))
    else:
        import time
        while True:
            try:
                actions = process_once(token, repo_dir)
                if actions:
                    print(time.strftime("%H:%M:%S"), json.dumps(actions, ensure_ascii=False))
            except Exception as e:  # noqa: BLE001
                print("轮询异常:", e, file=sys.stderr)
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
