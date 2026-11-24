"""工序域真值源：把 CeCoder 工序工作台的数据抽象成「可直接判定」的证据。

解决什么问题
------------
Agent-as-Judge 用通用工具（``list_dir`` / ``query_json``）取证时，每次只能查
**一个文件**。要回答「交付确认工序有没有在无产物时放行」这类**跨任务**问题，
它得挨个读 36 个 task JSON 再自己汇总——实测一次判定要 10~17 次工具调用，
且汇总逻辑靠模型推理，不确定。

本模块把「取数 + 规则判定」下沉到确定性代码：
- 跨任务聚合一次算完（36 个任务 → 1 次调用）
- 输出的是**已判定的违规清单**，不是原始 JSON

它不做什么
----------
- 不产出 pass/fail 结论 —— 结论仍由 judge 出（保持「判定权在 judge 层」）
- 不访问被测系统状态 —— 只读本机文件
- 不替代通用工具 —— 通用工具查不了的东西（跨文件聚合）才用它

数据源（本机实测）
------------------
- ``~/.cecoder/engineering-workbench/tasks/*.json`` — 工序节点状态、门禁、时间戳
- ``~/.cecoder/engineering-workbench/assets/<taskId>/`` — 实际产物文件
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .truth_source import TruthEvidence

# 工序定义 ID → 界面名。运行时 ID 与源码包目录名不同（example.concept-design
# ↔ node-concept-design），此处用界面名做人类可读的键。
DEFINITION_LABELS: dict[str, str] = {
    "example.concept-design": "概要设计",
    "example.ui-prototype": "UI 原型设计",
    "example.detail-design": "详细设计",
    "example.program-design": "程序设计",
    "example.code-change-design": "代码变更设计",
    "example.code-change-analysis": "代码变更分析",
    "example.code-review": "代码审查",
    "example.whitebox-test": "白盒测试",
    "implement": "开发实现",
    "example.delivery-confirmation": "交付确认",
    "example.delivery": "交付确认",
    "example.confirmation": "方案确认",
}

# 工序生命周期状态。
# 权威来源：plugins/engineering-node-api/src/index.ts:30
#   export const NODE_STATUSES = ['pending', 'ready', 'running', 'validating',
#                                  'review', 'done', 'failed', 'interrupted'] as const
# 注意: validating 是「校验中」，与 interrupted（已中断）是两个不同状态。
# 2026-11-14 修正: 此前漏了 validating，会让基于该常量的合法状态集断言
# 漏掉 running→validating 这条核心迁移。
STATUS_VALUES = (
    "pending",
    "ready",
    "running",
    "validating",
    "review",
    "done",
    "failed",
    "interrupted",
)

# 允许被「开始执行」的起始状态。
# 权威来源：workbenches/engineering/page/src/client/node-machine.ts:50
#   if (!['ready', 'review', 'failed', 'interrupted'].includes(node.status))
#     return { ok: false, reason: '当前工序尚未就绪' }
STARTABLE_STATUSES = ("ready", "review", "failed", "interrupted")

# 处于「执行中」的状态。
# 权威来源：node-machine.ts:10
#   export const isExecuting = (node) => node.status === 'running' || node.status === 'validating'
EXECUTING_STATUSES = ("running", "validating")

# 合法状态迁移集（v0.5.0）。
# 权威来源（逐条对照源码）：
#   pending → ready          node-machine.ts:79（上游完成/激活）
#   ready → running          node-machine.ts:60（markStarted，start 唯一入口）
#   running → validating     node-lifecycle.ts:97（运行结束进校验）
#   validating → review      node-lifecycle.ts:115（校验通过）
#   validating → running     node-lifecycle.ts:157（校验失败重试）
#   review → done            node-machine.ts:78（人工/自动确认推进）
#   review → running         node-lifecycle.ts:143（退回重做）
#   running/validating/review → failed        node-lifecycle.ts:74-75
#   running/validating/review → interrupted   node-lifecycle.ts:270
#   failed/interrupted → ready                node-lifecycle.ts:371（恢复）
#   done → ready             node-lifecycle.ts:373-374（重新打开，限当前/最后节点）
# 首次进入：null → pending（节点创建）。
# 明确禁止（迁移级断言重点盯这些）：
#   pending → done / pending → running / ready → done / done → running /
#   failed → running（须先回 ready）/ validating → done / review → pending
LEGAL_TRANSITIONS: frozenset[tuple[str | None, str]] = frozenset({
    (None, "pending"),
    ("pending", "ready"),
    ("ready", "running"),
    ("running", "validating"),
    ("validating", "review"),
    ("validating", "running"),
    ("review", "done"),
    ("review", "running"),
    ("running", "failed"),
    ("validating", "failed"),
    ("review", "failed"),
    ("running", "interrupted"),
    ("validating", "interrupted"),
    ("review", "interrupted"),
    ("failed", "ready"),
    ("interrupted", "ready"),
    ("done", "ready"),
})

# 「从 review 推进到 done」的前置条件。
# 权威来源：node-machine.ts:77
#   if (task.nodes[index].status !== 'review' || task.nodes[index].run?.validation?.ok !== true) return task
ADVANCE_FROM_STATUS = "review"
ADVANCE_REQUIRES_VALIDATION_OK = True

# 会被排除在「产物统计」之外的子目录名（模板与素材不是产物）
_NON_ARTIFACT_DIRS = ("templates", "materials")


@dataclass(frozen=True)
class ProcessNode:
    """一个工序节点的状态快照（只读视图）。"""

    task_id: str
    instance_id: str
    definition_id: str
    title: str
    status: str
    dir_name: str | None
    gates: dict[str, str]
    assessment_kind: str | None
    output_names: tuple[str, ...]
    output_count: int
    started_at: str | None
    completed_at: str | None
    duration_min: float | None
    index: int = 0
    """在任务 nodes 数组里的序号（0 起）。链顺序的关键。"""
    run_kind: str | None = None
    validation_ok: bool | None = None
    """run.validation.ok —— review→done 推进的前置条件（node-machine.ts:77）。"""
    legacy: bool = False
    """任务是否 legacy 迁移而来（task.legacySource 存在）。"""
    layout: str | None = None
    """assetLayout.kind —— 'task' 表示平铺布局（多工序共享任务根目录）。"""

    @property
    def label(self) -> str:
        return DEFINITION_LABELS.get(self.definition_id, self.title or self.definition_id)

    @property
    def all_gates_allow(self) -> bool:
        return bool(self.gates) and all(v == "allow" for v in self.gates.values())

    @property
    def blocked(self) -> bool:
        return any(v == "block" for v in self.gates.values())

    @property
    def executing(self) -> bool:
        return self.status in EXECUTING_STATUSES

    @property
    def startable(self) -> bool:
        return self.status in STARTABLE_STATUSES

    @property
    def flat_layout(self) -> bool:
        """平铺布局：多个工序共享任务根目录，产物会被写回每个节点。"""
        return self.layout == "task"

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "instance_id": self.instance_id,
            "index": self.index,
            "definition_id": self.definition_id,
            "label": self.label,
            "status": self.status,
            "gates": dict(self.gates),
            "assessment_kind": self.assessment_kind,
            "output_count": self.output_count,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_min": self.duration_min,
            "run_kind": self.run_kind,
            "validation_ok": self.validation_ok,
            "legacy": self.legacy,
            "layout": self.layout,
        }


@dataclass
class ProcessSnapshot:
    """一次工序域取证的结果。"""

    root: str
    tasks: list[dict] = field(default_factory=list)
    nodes: list[ProcessNode] = field(default_factory=list)
    violations: list[dict] = field(default_factory=list)
    """已判定的违规清单（规则查的结果，不是最终结论）。"""
    stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "root": self.root,
            "task_count": len(self.tasks),
            "node_count": len(self.nodes),
            "stats": self.stats,
            "violations": self.violations,
            "nodes": [n.to_dict() for n in self.nodes],
        }


def _duration_min(started_at: str | None, completed_at: str | None) -> float | None:
    """工序耗时（分钟）。必须用工序时间戳，不能用会话时长。

    实测依据：交付确认工序自身耗时 0.1 min，但会话时长 1441 min ——
    同一 workspace 下多个工序复用会话目录，会话时长不可用作工序耗时。
    """
    if not started_at or not completed_at:
        return None
    try:
        from datetime import datetime

        # Python 3.11 的 fromisoformat 不支持 'Z' 后缀（3.12 才支持），
        # 而工序 JSON 的时间戳统一是 '...Z' 格式 —— 显式换成 +00:00 兼容两者。
        # ruff FURB162 建议直接用原串，但会破坏 3.11 兼容性（pyproject 要求 >=3.11）。
        a = datetime.fromisoformat(started_at.replace("Z", "+00:00"))  # noqa: FURB162
        b = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))  # noqa: FURB162
        return round((b - a).total_seconds() / 60.0, 2)
    except (ValueError, TypeError):
        return None


def _parse_task(path: Path) -> tuple[dict, list[ProcessNode]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}, []
    task_id = str(data.get("id") or path.stem)
    is_legacy = data.get("legacySource") is not None
    nodes: list[ProcessNode] = []
    for idx, n in enumerate(data.get("nodes") or []):
        if not isinstance(n, dict):
            continue
        assessment = n.get("assessment") or {}
        value = assessment.get("value") or {}
        gates = value.get("gates") or {}
        outputs = n.get("outputs") or []
        run = n.get("run") or {}
        validation = run.get("validation") or {}
        asset_layout = n.get("assetLayout")
        layout_kind = (
            asset_layout.get("kind") if isinstance(asset_layout, dict) else asset_layout
        )
        nodes.append(
            ProcessNode(
                task_id=task_id,
                instance_id=str(n.get("instanceId") or ""),
                definition_id=str(n.get("definitionId") or ""),
                title=str(n.get("title") or ""),
                status=str(n.get("status") or ""),
                dir_name=n.get("dirName"),
                gates={str(k): str(v) for k, v in gates.items()},
                assessment_kind=value.get("kind"),
                output_names=tuple(str(o.get("name")) for o in outputs if isinstance(o, dict)),
                output_count=len(outputs),
                started_at=n.get("startedAt"),
                completed_at=n.get("completedAt"),
                duration_min=_duration_min(n.get("startedAt"), n.get("completedAt")),
                index=idx,
                run_kind=run.get("kind"),
                validation_ok=validation.get("ok") if isinstance(validation, dict) else None,
                legacy=is_legacy,
                layout=layout_kind,
            )
        )
    task = {
        "id": task_id,
        "title": data.get("title"),
        "typeId": data.get("typeId"),
        "node_count": len(nodes),
        "done_count": sum(1 for n in nodes if n.status == "done"),
        "asset_root": data.get("assetRoot"),
    }
    return task, nodes


def _count_disk_artifacts(asset_root: str | None) -> int:
    """磁盘上该任务的实际产物数（排除 templates/ 与 materials/）。

    实测坑：产物**不在节点 dirName 目录**，而在任务根目录或根目录的同名子目录。
    按 dirName 精确匹配会漏掉 77/102 个节点 —— 必须按任务根目录递归。
    """
    if not asset_root or not os.path.isdir(asset_root):
        return 0
    count = 0
    for dirpath, dirnames, filenames in os.walk(asset_root):
        rel = os.path.relpath(dirpath, asset_root)
        parts = rel.split(os.sep)
        if any(p in _NON_ARTIFACT_DIRS for p in parts):
            dirnames[:] = []
            continue
        count += len(filenames)
    return count


def _check_state_machine(nodes: list[ProcessNode]) -> list[dict]:
    """状态机级规则 —— 全部由源码权威定义推导。

    权威来源：workbenches/engineering/page/src/client/node-machine.ts
    - L11-14 currentNodeIndex: 链是严格顺序的，当前节点 = 第一个 status !== 'done' 的节点
    - L43-52 canStart: 只有当前节点、且状态在 STARTABLE_STATUSES 内才可启动
    - L74-80 applyAdvance: review + validation.ok===true → done，且下一个 pending → ready
    - L88-96 reconcileRunning: 失联的执行（running/validating 且无活会话）→ interrupted
    """
    violations: list[dict] = []
    if not nodes:
        return violations

    # 规则 5：链顺序 —— done 必须是前缀（不能跳过中间节点先完成后面的）
    # 依据 node-machine.ts:12  currentNodeIndex = 第一个非 done 节点
    seen_non_done = False
    for n in sorted(nodes, key=lambda x: x.index):
        if n.status == "done":
            if seen_non_done:
                violations.append(
                    {
                        "rule": "done_after_pending_breaks_chain_order",
                        "severity": "high",
                        "task_id": n.task_id,
                        "instance_id": n.instance_id,
                        "label": n.label,
                        "detail": (
                            f"序号 {n.index} 的工序已 done，但前面有未 done 的工序 —— "
                            "违反「当前节点 = 第一个非 done 节点」的链顺序语义"
                        ),
                    }
                )
        else:
            seen_non_done = True

    # 规则 6：非当前节点却处于执行中
    # 依据 node-machine.ts:48  if (node.instanceId !== instanceId) return { ok:false, reason:'只能执行当前工序' }
    current = next((n for n in sorted(nodes, key=lambda x: x.index) if n.status != "done"), None)
    for n in nodes:
        if current is None:
            break
        if n.executing and n.index != current.index:
            violations.append(
                {
                    "rule": "executing_not_current_node",
                    "severity": "high",
                    "task_id": n.task_id,
                    "instance_id": n.instance_id,
                    "label": n.label,
                    "detail": (
                        f"序号 {n.index} 的工序处于 {n.status}，"
                        f"但当前工序是序号 {current.index}（{current.label}）—— "
                        "违反「只能执行当前工序」"
                    ),
                }
            )

    # 规则 7：review 却缺 validation.ok —— 永远无法推进到 done
    # 依据 node-machine.ts:77  applyAdvance 要求 status==='review' 且 run.validation.ok===true
    for n in nodes:
        if n.status == "review" and n.validation_ok is not True:
            violations.append(
                {
                    "rule": "review_without_validation_ok",
                    "severity": "medium",
                    "task_id": n.task_id,
                    "instance_id": n.instance_id,
                    "label": n.label,
                    "detail": (
                        f"status=review 但 run.validation.ok={n.validation_ok!r} —— "
                        "无法推进到 done（node-machine.ts:77 要求 ok===true）"
                    ),
                }
            )

    # 规则 8：非法状态值（不在源码的封闭集内）
    for n in nodes:
        if n.status and n.status not in STATUS_VALUES:
            violations.append(
                {
                    "rule": "unknown_status_value",
                    "severity": "high",
                    "task_id": n.task_id,
                    "instance_id": n.instance_id,
                    "label": n.label,
                    "detail": (
                        f"status={n.status!r} 不在合法状态集内"
                        f"（合法: {', '.join(STATUS_VALUES)}）"
                    ),
                }
            )

    return violations


def _check_invariants(nodes: list[ProcessNode], disk_counts: dict[str, int]) -> list[dict]:
    """规则查：找出违反工序链不变量的节点。

    这里只判「数据层面的自相矛盾」，不判「业务对不对」——
    业务结论仍归 judge。规则查的作用是把「需要语义推理的部分」缩小到最小。

    **条件化是必须的**（2026-11-14 实测修正）——
    上一版把以下三类合法形态误报成 bug：

    1. **legacy 迁移任务**（task.legacySource 存在，如 1001/1002）：
       迁移路径不写 run / gates / outputs，「done 但无 outputs」是迁移的固有形态，
       不是产品缺陷。
    2. **平铺布局**（assetLayout.kind == 'task'）：多个工序共享任务根目录，
       `node-machine.ts:64-72` 的 applyTurnEnd 把整目录扫描结果写回每个节点，
       因此「pending 但有 outputs」在平铺下是合法的。
    3. **人工确认完成**：node-ui-design 声明 `completion:'manual'`，
       其 done 由人工确认而来。`autoAdvance:block` 只拦**自动**放行，
       `complete:allow` 存在即说明人工完成合法
       （依据 node-lifecycle.ts:169 —— 只在 automatic===true 时才校验 autoAdvance）。
    """
    violations: list[dict] = []
    pending_output_nodes: list[ProcessNode] = []

    for n in nodes:
        # 规则 1：已完成但零产物 —— 门禁应当拦住却放行了
        # 排除 legacy（迁移任务天然无 outputs 记录）
        if n.status == "done" and n.output_count == 0 and not n.legacy:
            violations.append(
                {
                    "rule": "done_without_outputs",
                    "severity": "high",
                    "task_id": n.task_id,
                    "instance_id": n.instance_id,
                    "label": n.label,
                    "detail": f"status=done 但 outputs 为空；gates={n.gates}",
                }
            )

        # 规则 2：产物清单未回写 —— 磁盘有产物但 task.json 记为 0
        # 排除 legacy（其产物本来就不回写）
        disk = disk_counts.get(n.task_id, 0)
        if n.status == "done" and n.output_count == 0 and disk > 0 and not n.legacy:
            violations.append(
                {
                    "rule": "outputs_not_written_back",
                    "severity": "medium",
                    "task_id": n.task_id,
                    "instance_id": n.instance_id,
                    "label": n.label,
                    "detail": (
                        f"task.json 的 outputs 为空，但磁盘上有 {disk} 个产物文件 —— "
                        "产物清单未回写，只看单源会误判为「工序未产出」"
                    ),
                }
            )

        # 规则 3：**硬门禁**已拦但状态却是 done —— 状态与门禁矛盾
        # 只看 start / complete —— autoAdvance:block 只拦自动放行，
        # 人工确认完成（complete:allow + validation.ok）是合法的
        # 依据 node-lifecycle.ts:162,168（complete 门禁）与 :169（autoAdvance 仅自动时校验）
        hard_block = any(n.gates.get(k) == "block" for k in ("start", "complete"))
        if n.status == "done" and hard_block:
            violations.append(
                {
                    "rule": "done_but_gate_blocked",
                    "severity": "high",
                    "task_id": n.task_id,
                    "instance_id": n.instance_id,
                    "label": n.label,
                    "detail": (
                        f"status=done 但 start/complete 门禁存在 block；gates={n.gates}"
                        "（autoAdvance:block 不算 —— 人工确认完成是合法的）"
                    ),
                }
            )

        # 规则 4：门禁从未评估却已放行
        # 排除 legacy（迁移任务无 assessment 记录）
        if n.status == "done" and not n.gates and not n.legacy:
            violations.append(
                {
                    "rule": "done_without_gate_evaluation",
                    "severity": "medium",
                    "task_id": n.task_id,
                    "instance_id": n.instance_id,
                    "label": n.label,
                    "detail": "status=done 但 assessment.gates 缺失（门禁从未评估即视为放行）",
                }
            )

        # 规则 5：未完成的工序却有产物（非平铺布局下才判）
        # 平铺布局（assetLayout.kind=='task'）下多工序共享任务根目录，
        # applyTurnEnd（node-machine.ts:64-72）把整目录扫描结果写回每个节点，
        # 因此「pending 且有 outputs」在平铺下合法。
        #
        # 注意: 非平铺节点也会记录上游目录的产物（实测 task 1018 的 n06/n07
        # 记录的是上游 ui-prototype 的 desktop-play.png 等），
        # 实测 22 条全是同一模式 —— 逐条报是噪声，按任务聚合成 1 条才可读。
        # 严重度 low：这是「产物归属错位」的观察，不是确定的缺陷。
        if n.status == "pending" and n.output_count > 0 and not n.flat_layout:
            pending_output_nodes.append(n)

    # 规则 5 聚合输出：每个任务一条
    by_task_pending: dict[str, list[ProcessNode]] = defaultdict(list)
    for n in pending_output_nodes:
        by_task_pending[n.task_id].append(n)
    for task_id, group in by_task_pending.items():
        violations.append(
            {
                "rule": "pending_with_outputs",
                "severity": "low",
                "task_id": task_id,
                "instance_id": ",".join(n.instance_id for n in group),
                "label": group[0].label,
                "detail": (
                    f"{len(group)} 个未完成工序记录了产物"
                    f"（{', '.join(n.instance_id for n in group)}）；"
                    "非平铺布局下产物应归属各自工序目录 —— "
                    "疑为 applyTurnEnd 按目录扫描导致的归属错位（需人工确认）"
                ),
            }
        )

    return violations


# 规则注册表：规则名 → 说明 + 默认严重度。
# 供 IR 层的 `process_state` 断言按名查询（见 `RULES`）。
RULES: dict[str, dict[str, str]] = {
    # --- 节点级 ---
    "done_without_outputs": {
        "severity": "high",
        "desc": "status=done 但 outputs 为空（门禁应当拦住却放行）",
    },
    "outputs_not_written_back": {
        "severity": "medium",
        "desc": "task.json 的 outputs 为空但磁盘上有产物（产物清单未回写）",
    },
    "done_but_gate_blocked": {
        "severity": "high",
        "desc": "status=done 但 start/complete 硬门禁为 block（autoAdvance 不算）",
    },
    "done_without_gate_evaluation": {
        "severity": "medium",
        "desc": "status=done 但 assessment.gates 缺失（门禁从未评估即放行）",
    },
    "pending_with_outputs": {
        "severity": "low",
        "desc": "未完成工序却记录了产物（疑为 applyTurnEnd 目录扫描导致归属错位）",
    },
    # --- 状态机级（依据 node-machine.ts）---
    "done_after_pending_breaks_chain_order": {
        "severity": "high",
        "desc": "链上 done 不是前缀（跳过了前面的未完成工序）",
    },
    "executing_not_current_node": {
        "severity": "high",
        "desc": "非当前工序却处于执行中（违反「只能执行当前工序」）",
    },
    "review_without_validation_ok": {
        "severity": "medium",
        "desc": "status=review 但缺 run.validation.ok=true（无法推进到 done）",
    },
    "unknown_status_value": {
        "severity": "high",
        "desc": "status 不在源码定义的合法状态集内",
    },
}

# 严重度排序（用于 min_severity 过滤）
_SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}


def _resolve_latest_task_id(root: str | Path) -> str | None:
    """解析「最新任务」的 task id（按 tasks/*.json 的 mtime）。

    用途：新建任务的 id 是动态 UUID，用例无法预知 ——
    探针用 `task_id: "@latest"` 定位刚创建/刚操作的任务。
    依据：执行中的任务会持续写盘（revision/run 更新），mtime 最新；
    已归档/已完成任务不再写盘。因此 @latest 在「创建 → 执行 → 探针」
    时序内稳定指向被测任务。
    """
    import os

    # ⚠️ 必须展开 ~ —— 注册表/用例里写 `~/.cecoder/...` 是常规写法，
    # Path 不展开会把字面 `~` 当目录名 → is_dir()=False → 返回 None
    # → task_id 退化为 None → node 探针全表扫描取第一个任务
    # （2026-11-16 exec_001 实测踩坑：seq 读成 ['done','done'] 即此因）
    tasks = Path(os.path.expanduser(str(root))) / "tasks"
    if not tasks.is_dir():
        return None
    files = [(p.stat().st_mtime, p.stem) for p in tasks.glob("*.json")]
    if not files:
        return None
    return max(files)[1]


def collect_probe(root: str | Path, probe: str = "node", task_id: str | None = None,
                  node: str | None = None) -> dict:
    """采集工序域探针结果（通道无关）。

    probe 取值:
    - node: 单个节点的状态（需 node 参数）
    - chain: 整条链的状态序列
    - snapshot: 全量统计
    - violations: 违规清单

    task_id 支持特殊值 ``@latest`` —— 按 tasks/*.json 的 mtime 取最新任务，
    用于「新建任务后探针」场景（新任务 id 是动态 UUID，用例无法预知）。
    """
    if task_id == "@latest":
        task_id = _resolve_latest_task_id(root)
    ts = ProcessTruthSource(root)
    snap = ts.collect()

    if probe == "snapshot":
        return {"probe": "snapshot", "stats": snap.stats, "root": str(snap.root)}

    if probe == "violations":
        return {
            "probe": "violations",
            "violations": snap.violations,
            "violation_count": len(snap.violations),
            "stats": snap.stats,
        }

    if probe == "chain":
        chains: dict[str, list[dict]] = {}
        for n in snap.nodes:
            if task_id and n.task_id != task_id:
                continue
            chains.setdefault(n.task_id, []).append(
                {"instance_id": n.instance_id, "index": n.index,
                 "status": n.status, "label": n.label,
                 "definition_id": n.definition_id}
            )
        for lst in chains.values():
            lst.sort(key=lambda x: x["index"])
        return {"probe": "chain", "chains": chains, "stats": snap.stats}

    # probe == "node"
    candidates = [
        n for n in snap.nodes
        if (not task_id or n.task_id == task_id)
        and (not node or node == n.instance_id or node == n.definition_id or node == n.label)
    ]
    if not candidates:
        return {"probe": "node", "found": False, "task_id": task_id, "node": node}
    n = candidates[0]
    return {
        "probe": "node",
        "found": True,
        "task_id": n.task_id,
        "instance_id": n.instance_id,
        "index": n.index,
        "status": n.status,
        "label": n.label,
        "definition_id": n.definition_id,
        "gates": dict(n.gates),
        "assessment_kind": n.assessment_kind,
        "output_count": n.output_count,
        "validation_ok": n.validation_ok,
        "run_kind": n.run_kind,
        "started_at": n.started_at,
        "completed_at": n.completed_at,
        "legacy": n.legacy,
        "layout": n.layout,
    }


class ProcessTruthSource:
    """工序域真值源：一次取证拿到全量工序状态 + 已判定的违规清单。

    对应 TruthSource 契约，但 key 是工序域的查询键（见 ``snapshot``）。

    典型用法（agent judge 侧）::

        ts = ProcessTruthSource("~/.cecoder/engineering-workbench")
        ev = ts.snapshot("violations")
        # ev.value -> {"violations": [...], "stats": {...}}
    """

    name = "process"

    def __init__(self, root: str | os.PathLike[str] = "~/.cecoder/engineering-workbench") -> None:
        self.root = Path(root).expanduser()
        self.source = f"file://{self.root}"

    # --- TruthSource 契约 ---

    def snapshot(self, key: str) -> TruthEvidence:
        if key not in ("violations", "nodes", "tasks", "stats"):
            return TruthEvidence(
                key=key,
                value=None,
                source=self.source,
                error=(
                    f"unsupported_key: {key}"
                    f"（可用: violations / nodes / tasks / stats）"
                ),
            )
        snap = self.collect()
        if key == "violations":
            value: object = {"violations": snap.violations, "stats": snap.stats}
        elif key == "nodes":
            value = [n.to_dict() for n in snap.nodes]
        elif key == "tasks":
            value = snap.tasks
        else:
            value = snap.stats
        return TruthEvidence(key=key, value=value, source=f"{self.source}#{key}")

    # --- 采集 ---

    def collect(self) -> ProcessSnapshot:
        tasks_dir = self.root / "tasks"
        if not tasks_dir.is_dir():
            return ProcessSnapshot(root=str(self.root), stats={"error": "tasks_dir_not_found"})

        snap = ProcessSnapshot(root=str(self.root))
        disk_counts: dict[str, int] = {}

        for path in sorted(tasks_dir.glob("*.json")):
            task, nodes = _parse_task(path)
            if not task:
                continue
            snap.tasks.append(task)
            snap.nodes.extend(nodes)
            disk_counts[task["id"]] = _count_disk_artifacts(task.get("asset_root"))

        snap.violations = _check_invariants(snap.nodes, disk_counts)
        # 状态机规则按任务分组判定（链顺序、当前节点都是任务级语义）
        by_task: dict[str, list[ProcessNode]] = defaultdict(list)
        for n in snap.nodes:
            by_task[n.task_id].append(n)
        for task_nodes in by_task.values():
            snap.violations.extend(_check_state_machine(task_nodes))
        snap.stats = self._stats(snap)
        return snap

    def _stats(self, snap: ProcessSnapshot) -> dict:
        by_status = Counter(n.status for n in snap.nodes)
        by_def = defaultdict(Counter)
        for n in snap.nodes:
            by_def[n.definition_id][n.status] += 1
        durations = [n.duration_min for n in snap.nodes if n.duration_min is not None]
        complete = sum(
            1 for t in snap.tasks if t["node_count"] >= 7 and t["done_count"] == t["node_count"]
        )
        return {
            "task_count": len(snap.tasks),
            "node_count": len(snap.nodes),
            "by_status": dict(by_status),
            "by_definition": {k: dict(v) for k, v in by_def.items()},
            "violation_count": len(snap.violations),
            "violation_by_rule": dict(Counter(v["rule"] for v in snap.violations)),
            "complete_chains": complete,
            "duration_min_median": round(sorted(durations)[len(durations) // 2], 2) if durations else None,
            "duration_min_max": max(durations) if durations else None,
        }

    def as_db_state(self) -> dict:
        """转成 Evidence.db_state 形态（兼容现有 judge 协议）。"""
        snap = self.collect()
        return {
            "process_violations": snap.violations,
            "process_stats": snap.stats,
            "process_nodes": [n.to_dict() for n in snap.nodes],
        }

    # --- 按规则查询（供 IR 层 process_state 断言使用）---

    def violations_by_rule(
        self,
        rule: str,
        *,
        task_id: str | None = None,
        node: str | None = None,
        exclude_legacy: bool = True,
        exclude_flat_layout: bool = True,
        min_severity: str = "medium",
    ) -> list[dict]:
        """取指定规则的违规清单（已按 task/node/severity 过滤）。

        参数
        ----
        rule              规则名，必须在 RULES 里
        task_id           限定任务；None = 全部
        node              限定节点（instanceId 或 definitionId）；None = 全部
        exclude_legacy    排除 legacy 迁移任务
        exclude_flat_layout 排除平铺布局的节点
        min_severity      最低严重度（high/medium/low/all）

        返回值：违规 dict 列表（空列表 = 无违规）
        """
        if rule not in RULES:
            raise KeyError(
                f"未知规则: {rule!r}（可用: {', '.join(sorted(RULES))}）"
            )
        snap = self.collect()
        floor = 0 if min_severity == "all" else _SEVERITY_ORDER.get(min_severity, 2)

        out: list[dict] = []
        for v in snap.violations:
            if v.get("rule") != rule:
                continue
            sev = _SEVERITY_ORDER.get(v.get("severity", "low"), 1)
            if sev < floor:
                continue
            if task_id and v.get("task_id") != task_id:
                continue
            if node:
                # node 可以是 instanceId（也可能是聚合后的逗号列表）或 definitionId
                inst = str(v.get("instance_id") or "")
                if node not in inst.split(","):
                    label = v.get("label") or ""
                    if node != label:
                        continue
            out.append(v)
        return out

    def check_rule(self, rule: str, **kwargs) -> dict:
        """按规则判定，返回可直接进 Verdict.raw 的结构。"""
        found = self.violations_by_rule(rule, **kwargs)
        return {
            "rule": rule,
            "rule_desc": RULES[rule]["desc"],
            "violation_count": len(found),
            "violations": found,
            "severity": RULES[rule]["severity"],
        }


if __name__ == "__main__":  # pragma: no cover — 手动探查入口
    import json as _json

    ts = ProcessTruthSource()
    s = ts.collect()
    print(_json.dumps(s.stats, ensure_ascii=False, indent=2))
    print(f"\n违规 {len(s.violations)} 条：")
    for v in s.violations[:20]:
        print(f"  [{v['severity']}] {v['rule']:<30} {v['task_id'][:10]} {v['instance_id']} {v['label']}")
        print(f"      {v['detail']}")
