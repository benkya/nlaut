"""CeCoder 完整 7 工序串行驱动器：编排全链 + 模拟产品要求的人工介入点。

**实测依据（2026-11-16，8363f6bf 任务全流程验证）**：

「自动执行」按钮**不能**全自动跑完 7 道工序 —— 产品在两处设计了人工介入点：

| # | 介入点 | 表现 | 解锁操作 |
|---|---|---|---|
| 1 | n02 UI 原型设计 | `start:block`（未选设计系统，definition.ts:30） | 点设计系统卡片（Ant/Shadcn/...）→ start 变 allow；**但自动循环不会自动恢复**，需再点「执行工序」 |
| 2 | n07 交付确认 | `needs-confirmation` / autoAdvance 规则 | 点「确认」完成交付 |

其余工序（n01 概念设计、n03-n06）gates=allow，可自动连续推进。

**本驱动器**把这些介入点自动化（点设计系统卡片 + 点执行工序 + 点确认），
配合长轮询（无 nlaut 单步 60s 限制）驱动全链到终态，最后跑完整校验：
迁移序列合法性（LEGAL_TRANSITIONS）+ 链序 + 产物齐全 + 全链完成。

**与 YAML 用例的分工**：
- `tc_cecoder_exec_003.yaml`（分钟级）：创建任务 → 点自动执行 → 断言启动
  （同步部分，进 CI 回归）
- 本驱动器（几十分钟 + 7 次 LLM）：全链到终态（发布前验收批跑，不进 CI）

用法：
```bash
# 全自动跑一条新任务的完整 7 工序（含 2 个人工介入点模拟）
.venv/bin/python scripts/cecoder_full_workflow_driver.py --timeout-min 90

# 指定标题
.venv/bin/python scripts/cecoder_full_workflow_driver.py --title "我的需求标题"
```
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from nlaut.judge.process_truth import _resolve_latest_task_id

sys.path.insert(0, str(REPO / "scripts"))
from cecoder_workflow_watch import (
    node_labels,
    node_states,
)
from cecoder_workflow_watch import (
    verify as watch_verify,
)

WORKBENCH = Path.home() / ".cecoder" / "engineering-workbench"
ARTIFACTS = REPO / "artifacts"

# 默认任务描述：给足需求材料，避免 n01 产物「阻断」（空需求时 AI 会产出阻塞记录）
DEFAULT_DESC = """需求：开发一个「图书借阅管理」小系统。
核心场景：
1. 图书入库：录入书名、作者、ISBN、库存数。
2. 读者借书：输入读者编号与 ISBN，校验库存，借出并减库存。
3. 读者还书：按借阅记录归还，恢复库存。
4. 借阅查询：按读者或日期查询当前在借清单。
验收：空库存不可借出；同一读者最多同时在借 3 本。"""


_PW = None


def _playwright():
    """Playwright 实例单例 —— sync API 不能重复 start（asyncio loop 冲突，
    2026-11-16 实测：main 补触发与 drive 各连一次时报错）。"""
    global _PW
    if _PW is None:
        from playwright.sync_api import sync_playwright

        _PW = sync_playwright().start()
    return _PW


def connect_page(cdp_port: int):
    """连接 CeCoder 应用页面（CDP）。注意：close 只关 browser，
    playwright 单例保留复用（exit 时统一清理）。"""
    import urllib.request

    tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{cdp_port}/json/list"))
    pages = [t for t in tabs if t.get("type") == "page" and "127.0.0.1" in t.get("url", "")]
    if not pages:
        raise RuntimeError("CDP 上没有应用页面（app 未启动或未带调试端口）")
    browser = _playwright().chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if "127.0.0.1" in pg.url:
                return browser, pg
    raise RuntimeError("连接成功但找不到应用页面")


def load_task(task_id: str) -> dict | None:
    p = WORKBENCH / "tasks" / f"{task_id}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def states_of(task: dict) -> dict[str, str]:
    return {n["instanceId"]: n.get("status", "?") for n in task.get("nodes", [])}


def gates_of(task: dict, nid: str) -> dict:
    for n in task.get("nodes", []):
        if n["instanceId"] == nid:
            return ((n.get("assessment") or {}).get("value") or {}).get("gates") or {}
    return {}


def create_task(pg, title: str, desc: str) -> None:
    """创建任务（新建弹窗 → 填写 → 提交）。"""
    for sel in ['button[aria-label="关闭"]']:
        if pg.locator(sel).count():
            try:
                pg.click(sel, timeout=1500)
            except Exception:  # noqa: BLE001,S110 — 关弹窗失败无需处理
                pass
    pg.wait_for_timeout(800)
    pg.click('button[aria-label="创建任务"]')
    pg.wait_for_timeout(1500)
    pg.fill('input[aria-label="任务标题"]', title)
    pg.fill('textarea[aria-label="任务详细描述"]', desc)
    pg.wait_for_timeout(300)
    # 弹窗内提交按钮（有文本；顶栏 icon 按钮无文本不会误匹配）
    pg.locator("button", has_text="创建任务").last.click()
    pg.wait_for_timeout(3000)
    pg.wait_for_selector(".egw-step-item", timeout=20000)
    pg.wait_for_timeout(1000)


def click_auto_execute(pg) -> None:
    """点「自动执行」启动自动推进。"""
    pg.click('.egw-stage-btn-auto')
    pg.wait_for_timeout(2000)


def select_design_system(pg, name: str = "Ant") -> bool:
    """人工介入点 1：为 UI 原型工序选择设计系统。

    位置：激活的节点视图层（.egw-node-view-container[data-active='true']）
    内的设计系统卡片按钮列表。点击后 start 门禁 block → allow。
    """
    try:
        btn = pg.locator(
            ".egw-node-view-container[data-active='true'] button", has_text=name
        ).first
        if btn.count() == 0:
            return False
        btn.click()
        pg.wait_for_timeout(2500)
        return True
    except Exception:  # noqa: BLE001
        return False


def click_execute(pg) -> bool:
    """点「执行工序」（解锁后自动循环不自动恢复，需手动再触发）。"""
    try:
        btn = pg.locator(".egw-stage-btn-primary")
        if btn.count() == 0 or btn.first.is_disabled():
            return False
        btn.first.click()
        pg.wait_for_timeout(2000)
        return True
    except Exception:  # noqa: BLE001
        return False


def confirm_node(pg) -> bool:
    """人工介入点 2：needs-confirmation 工序的人工确认（review → done）。

    实测（2026-11-16，8363f6bf）：确认入口在**激活的节点视图层**
    （.egw-node-view-container[data-active='true']）内的「确认完成」按钮，
    与桌面/平板/手机预览切换、「查看一致性报告」并排 ——
    **不在 .egw-stage 主按钮区**（那里「执行工序」在 review 时是 disabled）。

    UI 原型工序的 assessment.kind = 'needs-confirmation'
    （code: ui-design.approval-required，message 提示「点击确认完成」）。
    """
    try:
        btn = pg.locator(
            ".egw-node-view-container[data-active='true'] button",
            has_text="确认完成",
        ).first
        if btn.count() and not btn.is_disabled():
            btn.click()
            pg.wait_for_timeout(3000)
            return True
    except Exception:  # noqa: BLE001,S110 — 确认失败由外层轮询重试
        pass
    # 兜底：主按钮区的确认变体
    for text in ("确认交付", "完成交付"):
        try:
            btn = pg.locator(".egw-stage button", has_text=text).first
            if btn.count() and not btn.is_disabled():
                btn.click()
                pg.wait_for_timeout(2000)
                return True
        except Exception:  # noqa: BLE001,S112 — 变体逐个尝试
            continue
    return False


def ensure_task_page(pg, title: str) -> bool:
    """导航到该任务的详情页（介入操作的前提）。

    实测踩坑（2026-11-16）：driver 连上 CDP 时页面可能停在别的任务详情页
    或任务列表 —— 设计系统面板/确认完成按钮都只在该任务的激活视图层里，
    页面不对则介入操作全部落空（状态 4 分钟不变即此因）。

    无条件导航（每轮 goto+点卡）而非条件判断：详情页侧栏的任务列表也含
    目标标题，「已在详情页」的判断容易误判，5s/轮的导航代价可接受。
    """
    try:
        pg.goto(pg.url.split("#")[0])
        pg.wait_for_timeout(2000)
        pg.click(f'.egw-tasks-list .egw-card:has-text("{title}")')
        pg.wait_for_timeout(3000)
        return pg.locator(".egw-step-item").count() > 0
    except Exception:  # noqa: BLE001 — 导航失败由外层轮询重试
        return False


def drive(task_id: str, cdp_port: int, timeout_min: float, title: str = "") -> dict:
    """驱动全链到终态：轮询（记录全程迁移）+ 在人工介入点自动操作。"""
    browser, pg = connect_page(cdp_port)
    deadline = time.time() + timeout_min * 60
    interventions: list[dict] = []
    transitions: list[dict] = []
    last_states: dict[str, str] = {}
    states: dict[str, str] = {}

    try:
        while time.time() < deadline:
            task = load_task(task_id)
            if task is None:
                print("[drive] 退出原因: 任务文件消失")
                return {"error": "task_file_missing", "interventions": interventions,
                        "transitions": transitions}
            states = node_states(task)

            # 记录迁移（与 watch 相同口径）
            labels = node_labels(task)
            for nid, st in states.items():
                prev = last_states.get(nid)
                if prev is not None and prev != st:
                    from nlaut.judge.process_truth import LEGAL_TRANSITIONS

                    legal = (prev, st) in LEGAL_TRANSITIONS
                    transitions.append({
                        "at": time.strftime("%H:%M:%S"),
                        "node": nid,
                        "label": labels.get(nid, ""),
                        "from": prev,
                        "to": st,
                        "legal": legal,
                    })
                    mark = "OK " if legal else "ILLEGAL"
                    print(f"  [{time.strftime('%H:%M:%S')}] {nid} {labels.get(nid,'')}: "
                          f"{prev} → {st}  ({mark})")
            last_states = states

            executing = [n for n, s in states.items() if s in ("running", "validating")]

            if states and all(s == "done" for s in states.values()):
                print("[drive] 全链完成")
                break

            if not executing:
                # 链静止：先确保页面在该任务详情页（介入的前提）
                if title and not ensure_task_page(pg, title):
                    print("[drive] 页面导航到目标任务失败，下轮重试")
                    time.sleep(15)
                    continue
                # 找第一个非 done 工序，按门禁决定介入方式
                pending_chain = [n for n in sorted(states) if states[n] != "done"]
                if not pending_chain:
                    print("[drive] 退出原因: 无未完成工序但未触发全 done 判定"
                          f"（states={states}）")
                    break
                nid = pending_chain[0]
                gates = gates_of(task, nid)
                print(f"[drive] 链静止于 {nid}（status={states[nid]} gates={gates}）")

                if nid == "n02" and gates.get("start") == "block":
                    # 介入点 1：选设计系统
                    if select_design_system(pg):
                        interventions.append({"node": nid, "action": "select_design_system"})
                        print("[drive] 介入点1：已选设计系统")
                        pg.wait_for_timeout(1500)
                        if click_execute(pg):
                            interventions.append({"node": nid, "action": "click_execute"})
                            print("[drive] 已重新点「执行工序」")
                    else:
                        print("[drive] 设计系统卡片未找到（面板未激活？）")
                elif states[nid] == "review":
                    # 介入点 2：任何工序的 needs-confirmation（review → done）。
                    # 实测：UI 原型（ui-design.approval-required）与交付确认
                    # 都是「确认完成」按钮（激活视图层内）。
                    # ⚠️ normal 工序（如程序设计）review **无确认按钮** ——
                    # autoRun 循环会自动推进但有**分钟级延迟**（d52e0133 n04
                    # 实测约 10 分钟 review→done）。此时不要误判死锁，
                    # confirm 失败就继续等（下一轮 poll 再看）。
                    if confirm_node(pg):
                        interventions.append({"node": nid, "action": "confirm_node"})
                        print(f"[drive] 介入点2：{nid} 已点「确认完成」")
                    else:
                        print(f"[drive] {nid} review 无确认按钮（normal 工序），等待 autoRun 延迟推进（实测可达 10 分钟）")
                elif states[nid] == "failed":
                    # 区分瞬态失败与业务失败（d52e0133 n05 实测）：
                    # 「会话服务尚未就绪，请稍后重试」= 基础设施瞬态错误
                    # → 恢复重试（failed→ready→running 合法路径，UI 执行按钮可用）
                    node = next((x for x in task.get("nodes", []) if x["instanceId"] == nid), {})
                    reason = str((node.get("run") or {}).get("reason") or "")
                    if "尚未就绪" in reason or "稍后重试" in reason:
                        print(f"[drive] {nid} 瞬态失败（{reason[:30]}…），重试")
                        if click_execute(pg):
                            interventions.append({"node": nid, "action": "retry_transient"})
                    else:
                        print(f"[drive] {nid} 失败（{reason[:60]}），停止驱动（人工排查）")
                        break
                elif states[nid] in ("ready", "review") and nid != "n02" and click_execute(pg):
                    # 可执行但静止 → 点执行（n02 已在上面处理过解锁+重启）
                    interventions.append({"node": nid, "action": "click_execute"})
                # 等待下一轮观察
            time.sleep(15)
        else:
            print("[drive] 退出原因: 超时（deadline 到达）")
            return {"error": "timeout", "interventions": interventions,
                    "transitions": transitions, "final_states": states}
    finally:
        try:
            browser.close()
        except Exception:  # noqa: BLE001,S110 — CDP 连接清理失败无需处理
            pass

    if not (states and all(s == "done" for s in states.values())):
        print(f"[drive] 退出原因: 循环结束未全 done（final={states}）")
    return {"interventions": interventions, "transitions": transitions,
            "final_states": states}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CeCoder 完整 7 工序串行驱动器")
    ap.add_argument("--title", default="nlaut-e2e-完整工序流验证")
    ap.add_argument("--desc", default=DEFAULT_DESC)
    ap.add_argument("--cdp-port", type=int, default=9222)
    ap.add_argument("--timeout-min", type=float, default=90.0)
    ap.add_argument("--task-id", default="",
                    help="驱动指定任务（默认新建任务）")
    ap.add_argument("--reuse-latest", action="store_true",
                    help="不新建任务，驱动最新任务（半途续跑）")
    args = ap.parse_args(argv)

    task_id: str
    if args.task_id:
        task_id = args.task_id
        print(f"[drive] 指定任务 {task_id}")
        # 确保自动执行已触发（任务静止且首道 ready 时补触发）
        task = load_task(task_id)
        if task:
            states = states_of(task)
            if not any(s in ("running", "validating") for s in states.values()):
                browser, pg = connect_page(args.cdp_port)
                try:
                    click_auto_execute(pg)
                    print("[drive] 已补点「自动执行」")
                finally:
                    try:
                        browser.close()
                    except Exception:  # noqa: BLE001,S110
                        pass
    elif args.reuse_latest:
        task_id = _resolve_latest_task_id(WORKBENCH) or ""
        if not task_id:
            print("[drive] 没有可复用的任务", file=sys.stderr)
            return 2
        print(f"[drive] 复用任务 {task_id}")
    else:
        browser, pg = connect_page(args.cdp_port)
        try:
            print("[drive] 创建任务…")
            create_task(pg, args.title, args.desc)
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001,S110 — 关弹窗失败无需处理
                pass
        task_id = _resolve_latest_task_id(WORKBENCH) or ""
        if not task_id:
            print("[drive] 任务创建失败（找不到新任务文件）", file=sys.stderr)
            return 2
        print(f"[drive] 任务已创建: {task_id}")
        # 触发自动执行
        browser, pg = connect_page(args.cdp_port)
        try:
            click_auto_execute(pg)
            print("[drive] 已点「自动执行」")
        finally:
            try:
                browser.close()
            except Exception:  # noqa: BLE001,S110 — 关弹窗失败无需处理
                pass

    # 长轮询驱动（记录全程迁移）+ 介入
    record = drive(task_id, args.cdp_port, args.timeout_min, title=args.title)

    final_states = record.get("final_states") or {}
    result = watch_verify({
        "task_id": task_id,
        "final_states": final_states,
        "transitions": record.get("transitions") or [],
    })

    out = {
        "task_id": task_id,
        "title": args.title,
        "interventions": record.get("interventions"),
        "transitions": record.get("transitions"),
        "final_states": final_states,
        "verify": result,
    }
    ARTIFACTS.mkdir(exist_ok=True)
    p = ARTIFACTS / f"full_workflow_{task_id[:8]}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n[drive] === 终态校验 ===")
    for c in result.get("checks", []):
        mark = "✅" if c["ok"] else "❌"
        print(f"  {mark} {c['check']}: {c['detail']}")
    print(f"[drive] 人工介入点记录: {json.dumps(record.get('interventions'), ensure_ascii=False)}")
    print(f"[drive] 报告: {p}")
    ok = result.get("ok") and not record.get("error")
    print(f"[drive] 结果: {'完整 7 工序串行成功' if ok else '未完成/存在违规'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
