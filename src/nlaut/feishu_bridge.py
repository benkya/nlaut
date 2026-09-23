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
        for item in data.get("items", []):
            rows.append(item)
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
}


def draft_from_verbal(verbal: str, system: str) -> dict:
    """口述 → IR 草案 dict（当前用模板+正则，M1 接模型 API 后替换）。"""
    # 模板命中（演示系统常见场景）
    m = re.search(r"连错(\d+)次", verbal or "")
    if m:
        n = m.group(1)
        t = _IR_TEMPLATES["锁定"]
        return {
            "id": f"tc_login_lock_{n}",
            "title": t["title"].format(n=n),
            "source": verbal,
            "data_ref": t["draft"] if False else t["data_ref"],
            "dataset": t["dataset"],
            "steps": t["steps"],
            "assertions": t["assertions"],
            "postconditions": t["postconditions"],
        }
    # 通用兜底：需要人工澄清（信息不足）
    return {}


def ir_yaml(ir: dict) -> str:
    """dict → 紧凑 YAML 文本（人可读、可直接入库）。"""
    lines = [f"id: {ir['id']}", f"title: {ir['title']}"]
    lines.append(f'source: "口述: {ir["source"]}"')
    lines.append("req_ref: null")
    lines.append("priority: P1")
    lines.append("channel: web")
    lines.append("quad:")
    lines.append("  actor: 登录页")
    lines.append(f"  action: {ir.get('action', '提交登录')}")
    lines.append("  object: 癭录表单")
    lines.append("  preconditions:")
    lines.append('    - "演示用户存在"')
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
        priority = (f.get("优先级") or "").strip()

        # 需澄清行：已有 AI 追问，跳过（等提出人补充）
        if status == "需澄清":
            continue

        # 待生成 → 草案
        if status == "待生成" or status == "":
            draft = draft_from_verbal(verbal, f.get("被测系统", ""))
            if draft:
                update_record(token, rid, {
                    "IR草案": ir_yaml(draft),
                    "状态": "待审核",
                })
                actions.append({"record_id": rid, "action": "drafted", "verbal": verbal[:30]})
            else:
                update_record(token, rid, {
                    "AI追问": "口述信息不足：请补充①预期结果（出现什么提示/跳转）②操作次数或边界值。当前生成层为规则模板，暂只覆盖演示登录场景。",
                    "状态": "需澄清",
                })
                actions.append({"record_id": "..", "action": "clarify_needed", "verbal": verbal[:30]})
            continue

        # 已确认 → 执行
        if status == "已确认":
            case_file = repo_dir / "cases" / "demo" / f"{draft_id_from(f)}.yaml"
            if case_file.exists():
                update_record(token, rid, {"状态": "执行中"})
                cmd = [
                    str(repo_dir / ".venv/bin/python"), "-m", "nlaut.cli",
                    "--case", case_file.stem,
                ]
                env = {**os.environ, "NO_PROXY": "127.0.0.1,localhost"}
                proc = subprocess.run(cmd, cwd=repo_dir, env=env,
                                      capture_output=True, text=True, timeout=600)
                summary = proc.stdout[-600:] if proc.stdout else proc.stderr[-600:]
                ok = proc.returncode == 0
                update_record(token, rid, {
                    "状态": "已完成" if ok else "失败",
                    "执行结果": summary,
                })
                actions.append({"record_id": rid, "action": "executed", "ok": ok})
            else:
                update_record("token", rid, {"状态": "需澄清", "AI追问": "未找到对应 IR 文件，请确认草案已入库。"})
            continue

        # 待审核 → 等人改状态，不动
    return actions


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
