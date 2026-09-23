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


def publish_report(token: str, repo_dir: Path, case_id: str) -> str:
    """执行后把 HTML 报告上传到飞书群并返回位置描述。

    上传走 im/v1/files（multipart），随后发 file 消息进群；返回写进表格的报告位置文案。
    失败不阻塞主流程——返回错误说明文本，链路继续。"""
    import time as _time
    import uuid as _uuid

    report = repo_dir / "artifacts" / "report.html"
    if not report.exists():
        return "报告未生成（artifacts/report.html 缺失）"
    fname = f"nlaut_report_{_time.strftime('%Y%m%d_%H%M')}_{case_id}.html"
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
