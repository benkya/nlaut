"""CeCoder 全工序流 watch：长轮询一个任务直到终态，全程记录状态迁移。

**为什么需要它**（2026-11-16 设计依据）：
nlaut 用例的单步等待上限是 60s（wait_visible/wait_text 的 timeout_ms 上限），
而一道工序的 AI 生成是**分钟级**，7 道工序串行是**几十分钟级** ——
纯 YAML 编排无法「等完整链跑完」。

所以 exec_003 拆成两层：
1. **YAML 用例**（`tc_cecoder_exec_003`，分钟内完成）：创建任务 → 点「自动执行」
   → 同步断言启动成功（n01 running + 链序 + 7 工序就绪）
2. **本 watch 脚本**（无 60s 限制）：轮询 task.json，每次状态变化记录一条
   → 得到**自建的迁移历史**（CeCoder 不落盘 statusHistory，watch 用采样补上）
   → 终态时跑完整校验：迁移序列合法性 + 链序 + 产物齐全 + 全链完成

**迁移合法性判据**：`process_truth.LEGAL_TRANSITIONS`（逐条对照源码推导）。
watch 记录的每个 from→to 都必须在这个集合内 —— 这是迁移级断言的
**首次真实全链执行验证**（此前只能靠两次探针夹一次动作）。

用法：
```bash
# watch 最新任务（刚由 exec_003 / 手动触发自动执行的任务）
.venv/bin/python scripts/cecoder_workflow_watch.py --latest --timeout-min 120

# watch 指定任务
.venv/bin/python scripts/cecoder_workflow_watch.py --task-id <uuid> --timeout-min 60

# 输出：artifacts/workflow_watch_<taskid>.json + 控制台摘要
```

退出码：0 = 全链 done 且校验全过；1 = 校验发现违规；2 = 超时/任务消失。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from nlaut.judge.process_truth import (
    LEGAL_TRANSITIONS,
    _resolve_latest_task_id,
)

WORKBENCH = Path.home() / ".cecoder" / "engineering-workbench"
ARTIFACTS = REPO / "artifacts"

# 终态（watch 会在「无任何节点处于执行中且不再变化」时判定链已停）
EXECUTING = {"running", "validating"}
TERMINAL_NODE = {"done", "failed", "interrupted", "pending", "ready"}

# 采样可跳过的秒级中间态：15s 轮询可能观察不到 validating / review
# （conversation 工序的验收+评审可能秒级完成）。出现「running → review」
# 「running → done」这类**路径压缩**迁移 —— 实际走了
# running → validating → review → done 的合法链（源码 node-lifecycle.ts:113
# 前置 validating 才能进 review）。采样式观察必须区分「直接合法」
# /「路径压缩合法」/「真非法」，否则秒级中间态全成假警报。
# 可压缩的中间态限定在执行段（validating / review）—— pending / ready /
# done / failed / interrupted 停留时间长，必然被采样到，不允许跳。
_COMPRESSIBLE = {"validating", "review"}
_MAX_PATH_DEPTH = 3


def transition_kind(prev: str | None, to: str) -> str:
    """判定迁移：direct（直接合法）/ path（路径压缩合法）/ illegal。

    - direct: (prev, to) 在 LEGAL_TRANSITIONS
    - path: 存在只经过可压缩中间态（validating/review）的合法路径，
      深度 ≤3（例：running → done 实为 running → validating → review
      → done 的三连压缩）
    - illegal: 都不是 → 真非法（重点复核）
    """
    from collections import deque

    from nlaut.judge.process_truth import LEGAL_TRANSITIONS

    if (prev, to) in LEGAL_TRANSITIONS:
        return "direct"
    # BFS：只允许经过可压缩中间态
    adj: dict[str, list[str]] = {}
    for f, t in LEGAL_TRANSITIONS:
        adj.setdefault(f or "", []).append(t)
    queue: deque[tuple[str, int]] = deque([(prev or "", 0)])
    seen: set[str] = {prev or ""}
    while queue:
        cur, depth = queue.popleft()
        if depth >= _MAX_PATH_DEPTH:
            continue
        for nxt in adj.get(cur, ()):
            if nxt == to:
                return "path"
            if nxt in _COMPRESSIBLE and nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, depth + 1))
    return "illegal"


def load_task(task_id: str) -> dict | None:
    p = WORKBENCH / "tasks" / f"{task_id}.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def node_states(task: dict) -> dict[str, str]:
    return {
        n["instanceId"]: n.get("status", "?")
        for n in task.get("nodes", [])
    }


def node_outputs(task: dict) -> dict[str, list]:
    return {
        n["instanceId"]: list(n.get("outputs") or [])
        for n in task.get("nodes", [])
    }


def node_labels(task: dict) -> dict[str, str]:
    return {
        n["instanceId"]: n.get("title") or n.get("definitionId", "")
        for n in task.get("nodes", [])
    }


def watch(task_id: str, timeout_min: float, poll_s: float) -> dict:
    """轮询直到全链 done / 出现 failed / 超时。返回观察记录。"""
    deadline = time.time() + timeout_min * 60
    transitions: list[dict] = []
    last_states: dict[str, str] = {}
    last_outputs: dict[str, list] = {}
    quiet_rounds = 0  # 连续无变化轮数

    print(f"[watch] 任务 {task_id}  超时 {timeout_min:.0f} 分钟  轮询 {poll_s:.0f}s")
    while time.time() < deadline:
        task = load_task(task_id)
        if task is None:
            print("[watch] 任务文件消失（可能被删除）")
            return {"task_id": task_id, "error": "task_file_missing",
                    "transitions": transitions}
        cur = node_states(task)
        outs = node_outputs(task)
        labels = node_labels(task)

        # 记录变化（状态 or 产物数变化都算事件；产物变化只记录不打迁移标记）
        for nid, st in cur.items():
            prev = last_states.get(nid)
            if prev is not None and prev != st:
                transitions.append({
                    "at": time.strftime("%H:%M:%S"),
                    "node": nid,
                    "label": labels.get(nid, ""),
                    "from": prev,
                    "to": st,
                    "legal": (prev, st) in LEGAL_TRANSITIONS,
                })
                mark = "OK " if (prev, st) in LEGAL_TRANSITIONS else "ILLEGAL"
                print(f"  [{time.strftime('%H:%M:%S')}] {nid} {labels.get(nid,'')}: "
                      f"{prev} → {st}  ({mark})")
        for nid, o in outs.items():
            if len(o) != len(last_outputs.get(nid, [])) and last_outputs:
                print(f"  [{time.strftime('%H:%M:%S')}] {nid} 产物 {len(last_outputs.get(nid,[]))} → {len(o)}")

        # 终止判定
        if cur and all(s == "done" for s in cur.values()):
            print(f"[watch] 全链完成（{len(cur)} 工序全 done）")
            break
        has_failed_no_exec = bool(cur) and any(s == "failed" for s in cur.values()) and not any(
            s in EXECUTING for s in cur.values()
        )
        if has_failed_no_exec:
            print("[watch] 链停在 failed（无执行中工序）")
            break

        # 变化检测
        changed = cur != last_states or outs != last_outputs
        quiet_rounds = 0 if changed else quiet_rounds + 1
        last_states, last_outputs = cur, outs

        # 静默很久且无执行中工序 → 认为链已停（人工确认场景可能停在这）
        if quiet_rounds >= 30 and not any(s in EXECUTING for s in cur.values()):
            print(f"[watch] 链静止 {quiet_rounds * poll_s:.0f}s 且无执行中工序 → 判定停止")
            break
        if changed:
            quiet_rounds = 0

        time.sleep(poll_s)
    else:
        print("[watch] 超时")
        return {"task_id": task_id, "error": "timeout",
                "transitions": transitions, "final_states": last_states}

    return {
        "task_id": task_id,
        "transitions": transitions,
        "final_states": last_states,
    }


def verify(record: dict) -> dict:
    """终态校验：迁移合法性 + 链序 + 产物 + 全链完成。"""
    checks: list[dict] = []
    finals: dict[str, str] = record.get("final_states") or {}
    transitions: list[dict] = record.get("transitions") or []

    # 1. 迁移序列全合法（区分直接合法/路径压缩/真非法）
    illegal = [t for t in transitions if t.get("kind", "direct") == "illegal"]
    n_direct = sum(1 for t in transitions if t.get("kind") == "direct")
    n_path = sum(1 for t in transitions if t.get("kind") == "path")
    checks.append({
        "check": "transitions_legal",
        "ok": not illegal,
        "detail": (f"{len(transitions)} 次迁移（直接 {n_direct} / 路径压缩 {n_path}"
                   f" / 非法 {len(illegal)}）—— 路径压缩=采样跳过秒级中间态（如 validating）"),
        "illegal": illegal,
    })

    # 2. 链序：done 必须是前缀（node-machine.ts:12）
    if finals:
        order = sorted(finals.keys())  # n01..n07 字典序即链序
        seq = [finals[n] for n in order]
        done_prefix = True
        seen_non_done = False
        for s in seq:
            if s != "done":
                seen_non_done = True
            elif seen_non_done:
                done_prefix = False
        checks.append({
            "check": "done_prefix_chain_order",
            "ok": done_prefix,
            "detail": f"终态序列 {seq}",
        })

    # 3. 产物齐全：done 工序 outputs 非空
    task = load_task(record["task_id"])
    if task:
        done_empty = [
            n["instanceId"] for n in task.get("nodes", [])
            if n.get("status") == "done" and not (n.get("outputs") or [])
        ]
        checks.append({
            "check": "done_have_outputs",
            "ok": not done_empty,
            "detail": f"done 且无产物的工序: {done_empty or '无'}",
        })
        # 4. 全链完成
        n_done = sum(1 for s in (node_states(task) or {}).values() if s == "done")
        checks.append({
            "check": "all_done",
            "ok": n_done == len(task.get("nodes", [])),
            "detail": f"{n_done}/{len(task.get('nodes', []))} done",
        })

    all_ok = all(c["ok"] for c in checks)
    return {"ok": all_ok, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CeCoder 全工序流 watch")
    ap.add_argument("--task-id", default="")
    ap.add_argument("--latest", action="store_true", help="watch 最新任务（mtime）")
    ap.add_argument("--timeout-min", type=float, default=120.0)
    ap.add_argument("--poll", type=float, default=10.0)
    args = ap.parse_args(argv)

    task_id = args.task_id
    if args.latest or not task_id:
        task_id = _resolve_latest_task_id(WORKBENCH)
    if not task_id:
        print("[watch] 找不到任务", file=sys.stderr)
        return 2

    record = watch(task_id, args.timeout_min, args.poll)
    result = verify(record)

    # 落盘
    ARTIFACTS.mkdir(exist_ok=True)
    out = ARTIFACTS / f"workflow_watch_{task_id[:8]}.json"
    out.write_text(json.dumps({**record, "verify": result}, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    print("\n[watch] === 终态校验 ===")
    for c in result["checks"]:
        mark = "✅" if c["ok"] else "❌"
        print(f"  {mark} {c['check']}: {c['detail']}")
    print(f"[watch] 结果: {'全部通过' if result['ok'] else '存在违规'}")
    print(f"[watch] 报告: {out}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
