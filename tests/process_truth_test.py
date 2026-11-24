"""工序域真值源（ProcessTruthSource）测试。

Layer A 元测试：用**构造的临时数据**，不依赖本机真实 CeCoder 数据。
验证规则查的判定逻辑正确、边界处理正确。

真实数据的集成验证见 scripts/ 下的人工探查入口。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nlaut.judge.process_truth import (
    DEFINITION_LABELS,
    ProcessTruthSource,
    collect_probe,
)


def _write_task(tasks_dir: Path, task_id: str, nodes: list[dict], assets: dict | None = None) -> None:
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / f"{task_id}.json").write_text(
        json.dumps(
            {
                "id": task_id,
                "title": f"task {task_id}",
                "typeId": "all",
                "assetRoot": str(assets) if assets else None,
                "nodes": nodes,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _node(
    instance: str,
    definition: str,
    status: str,
    *,
    gates: dict | None = None,
    outputs: int = 1,
    started: str | None = "2026-11-09T10:00:00.000Z",
    completed: str | None = "2026-11-09T10:05:00.000Z",
) -> dict:
    n = {
        "instanceId": instance,
        "definitionId": definition,
        "title": DEFINITION_LABELS.get(definition, definition),
        "status": status,
        "outputs": [{"name": f"out{i}.md", "path": f"/x/out{i}.md"} for i in range(outputs)],
    }
    if gates is not None:
        n["assessment"] = {"value": {"kind": "normal", "gates": gates}}
    if started:
        n["startedAt"] = started
    if completed:
        n["completedAt"] = completed
    return n


ALLOW = {"start": "allow", "complete": "allow", "autoAdvance": "allow"}
BLOCKED = {"start": "allow", "complete": "allow", "autoAdvance": "block"}


@pytest.fixture
def workbench(tmp_path):
    """构造一个工序工作台根目录。"""
    root = tmp_path / "wb"
    tasks = root / "tasks"
    assets = root / "assets"
    return root, tasks, assets


class TestCollect:
    def test_collects_nodes_and_tasks(self, workbench):
        root, tasks, _ = workbench
        _write_task(tasks, "1001", [_node("n01", "example.concept-design", "done", gates=ALLOW)])
        snap = ProcessTruthSource(root).collect()
        assert len(snap.tasks) == 1
        assert len(snap.nodes) == 1
        assert snap.nodes[0].status == "done"
        assert snap.nodes[0].definition_id == "example.concept-design"
        assert snap.nodes[0].label == "概要设计"

    def test_missing_tasks_dir_returns_error_stat(self, tmp_path):
        snap = ProcessTruthSource(tmp_path / "nonexistent").collect()
        assert snap.stats.get("error") == "tasks_dir_not_found"
        assert snap.nodes == []

    def test_duration_uses_node_timestamps(self, workbench):
        """工序耗时必须用节点时间戳，不能用会话时长。"""
        root, tasks, _ = workbench
        _write_task(
            tasks,
            "1001",
            [_node("n01", "example.concept-design", "done", gates=ALLOW,
                   started="2026-11-09T10:00:00.000Z", completed="2026-11-09T10:30:00.000Z")],
        )
        snap = ProcessTruthSource(root).collect()
        assert snap.nodes[0].duration_min == pytest.approx(30.0)

    def test_duration_none_when_missing_timestamp(self, workbench):
        root, tasks, _ = workbench
        _write_task(tasks, "1001", [_node("n01", "x", "done", gates=ALLOW, started=None, completed=None)])
        snap = ProcessTruthSource(root).collect()
        assert snap.nodes[0].duration_min is None


class TestInvariants:
    def test_done_without_outputs_is_violation(self, workbench):
        root, tasks, _ = workbench
        _write_task(tasks, "1001", [_node("n01", "example.delivery", "done", gates=ALLOW, outputs=0)])
        snap = ProcessTruthSource(root).collect()
        rules = [v["rule"] for v in snap.violations]
        assert "done_without_outputs" in rules

    def test_done_with_outputs_no_violation(self, workbench):
        root, tasks, _ = workbench
        _write_task(tasks, "1001", [_node("n01", "example.delivery", "done", gates=ALLOW, outputs=3)])
        snap = ProcessTruthSource(root).collect()
        assert snap.violations == []

    def test_done_but_gate_blocked_is_violation(self, workbench):
        """complete:block（硬门禁）且 done —— 状态与门禁矛盾。

        注意: autoAdvance:block 不算（只拦自动放行，人工确认完成合法）。
        """
        root, tasks, _ = workbench
        _write_task(
            tasks,
            "1001",
            [_node("n01", "example.ui-prototype", "done",
                   gates={"start": "allow", "complete": "block", "autoAdvance": "allow"})],
        )
        snap = ProcessTruthSource(root).collect()
        rules = [v["rule"] for v in snap.violations]
        assert "done_but_gate_blocked" in rules

    def test_done_without_gate_evaluation_is_violation(self, workbench):
        root, tasks, _ = workbench
        _write_task(tasks, "1001", [_node("n01", "example.concept-design", "done", gates=None)])
        snap = ProcessTruthSource(root).collect()
        rules = [v["rule"] for v in snap.violations]
        assert "done_without_gate_evaluation" in rules

    def test_outputs_not_written_back_detected_via_disk(self, workbench):
        """task.json 的 outputs 为空，但磁盘有产物 —— 产物清单未回写。"""
        root, tasks, assets = workbench
        task_assets = assets / "1001"
        (task_assets / "outputs").mkdir(parents=True)
        (task_assets / "outputs" / "概要设计文档.md").write_text("x", encoding="utf-8")
        (task_assets / "templates").mkdir()
        (task_assets / "templates" / "t.md").write_text("x", encoding="utf-8")  # 模板不算产物
        _write_task(tasks, "1001", [_node("n01", "example.concept-design", "done", gates=ALLOW, outputs=0)],
                    assets=task_assets)
        snap = ProcessTruthSource(root).collect()
        rules = [v["rule"] for v in snap.violations]
        assert "outputs_not_written_back" in rules
        detail = next(v["detail"] for v in snap.violations if v["rule"] == "outputs_not_written_back")
        assert "1 个产物" in detail  # templates/ 不计入

    def test_pending_node_not_flagged(self, workbench):
        """pending 是正常状态，不该被当成违规。"""
        root, tasks, _ = workbench
        _write_task(tasks, "1001", [_node("n01", "example.detail-design", "pending", gates=ALLOW, outputs=0)])
        snap = ProcessTruthSource(root).collect()
        assert snap.violations == []

    def test_failed_node_not_flagged(self, workbench):
        root, tasks, _ = workbench
        _write_task(tasks, "1001", [_node("n01", "example.ui-prototype", "failed", gates=ALLOW, outputs=0)])
        snap = ProcessTruthSource(root).collect()
        assert snap.violations == []


class TestConditionalRules:
    """条件化规则 —— 防止把合法形态误报成 bug。

    2026-11-14 实测修正：上一版把以下三类合法形态误报了 ——
    1. legacy 迁移任务（task.legacySource 存在）
    2. autoAdvance:block（人工确认完成合法，只拦自动放行）
    3. 平铺布局下的产物共享
    """

    def _write_legacy_task(self, tasks, nodes):
        tasks.mkdir(parents=True, exist_ok=True)
        (tasks / "1001.json").write_text(
            json.dumps(
                {
                    "id": "1001",
                    "legacySource": {"workspacePath": "/x/legacy"},
                    "nodes": nodes,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_legacy_task_excluded_from_outputs_rule(self, workbench):
        """legacy 迁移任务天然无 outputs 记录，不该报 done_without_outputs。"""
        root, tasks, _ = workbench
        self._write_legacy_task(
            tasks, [_node("n01", "example.ui-prototype", "done", gates=None, outputs=0)]
        )
        snap = ProcessTruthSource(root).collect()
        rules = [v["rule"] for v in snap.violations]
        assert "done_without_outputs" not in rules
        assert "done_without_gate_evaluation" not in rules

    def test_non_legacy_still_reported(self, workbench):
        """非 legacy 的同样形态仍要报出来。"""
        root, tasks, _ = workbench
        _write_task(tasks, "1001", [_node("n01", "example.ui-prototype", "done", gates=None, outputs=0)])
        snap = ProcessTruthSource(root).collect()
        rules = [v["rule"] for v in snap.violations]
        assert "done_without_outputs" in rules
        assert "done_without_gate_evaluation" in rules

    def test_auto_advance_block_is_not_a_violation(self, workbench):
        """autoAdvance:block 只拦自动放行，人工确认完成合法（node-lifecycle.ts:169）。"""
        root, tasks, _ = workbench
        _write_task(tasks, "T1", [_node("n01", "example.ui-prototype", "done", gates=BLOCKED)])
        snap = ProcessTruthSource(root).collect()
        rules = [v["rule"] for v in snap.violations]
        assert "done_but_gate_blocked" not in rules

    def test_complete_block_is_a_violation(self, workbench):
        """complete:block 是硬门禁 —— done 就是矛盾。"""
        root, tasks, _ = workbench
        _write_task(
            tasks,
            "T1",
            [_node("n01", "example.ui-prototype", "done",
                   gates={"start": "allow", "complete": "block", "autoAdvance": "allow"})],
        )
        snap = ProcessTruthSource(root).collect()
        rules = [v["rule"] for v in snap.violations]
        assert "done_but_gate_blocked" in rules

    def test_flat_layout_pending_with_outputs_not_reported(self, workbench):
        """平铺布局下 pending 带产物是合法的。"""
        root, tasks, _ = workbench
        n = _node("n01", "example.implement", "pending", gates=ALLOW, outputs=5)
        n["assetLayout"] = {"kind": "task"}
        _write_task(tasks, "T1", [n])
        snap = ProcessTruthSource(root).collect()
        rules = [v["rule"] for v in snap.violations]
        assert "pending_with_outputs" not in rules

    def test_pending_with_outputs_aggregated_per_task(self, workbench):
        """非平铺下 pending 带产物要报，但按任务聚合成一条。"""
        root, tasks, _ = workbench
        n1 = _node("n01", "example.implement", "pending", gates=ALLOW, outputs=3)
        n2 = _node("n02", "example.delivery-confirmation", "pending", gates=ALLOW, outputs=3)
        _write_task(tasks, "T1", [n1, n2])
        snap = ProcessTruthSource(root).collect()
        found = [v for v in snap.violations if v["rule"] == "pending_with_outputs"]
        assert len(found) == 1  # 聚合成一条
        assert found[0]["task_id"] == "T1"
        assert "n01" in found[0]["instance_id"]
        assert "n02" in found[0]["instance_id"]


class TestLatestTaskProbe:
    """`task_id: "@latest"` —— 新建任务探针定位（id 是动态 UUID，用例无法预知）。"""

    def test_latest_resolves_mtime_newest(self, tmp_path):
        import os
        import time as _time

        root = tmp_path / "wb"
        tasks = root / "tasks"
        tasks.mkdir(parents=True)
        old = tasks / "old.json"
        new = tasks / "new.json"
        old.write_text(json.dumps({"id": "old", "nodes": []}), encoding="utf-8")
        new.write_text(json.dumps({"id": "new", "nodes": []}), encoding="utf-8")
        # 让 new 的 mtime 晚于 old（保证顺序，避开文件系统时间粒度）
        os.utime(old, (_time.time() - 100, _time.time() - 100))

        r = collect_probe(root, probe="node", task_id="@latest", node="n01")
        # 没有节点 → found=False，但 task_id 应解析成 new
        assert r["task_id"] == "new"

    def test_latest_missing_dir_returns_not_found(self, tmp_path):
        r = collect_probe(tmp_path, probe="node", task_id="@latest", node="n01")
        assert r["found"] is False

    def test_latest_with_node(self, tmp_path):
        import os
        import time as _time

        root = tmp_path / "wb"
        tasks = root / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "a.json").write_text(
            json.dumps({"id": "a", "nodes": [
                {"instanceId": "n01", "definitionId": "ex.x", "status": "ready"}
            ]}),
            encoding="utf-8",
        )
        (tasks / "b.json").write_text(
            json.dumps({"id": "b", "nodes": [
                {"instanceId": "n01", "definitionId": "ex.x", "status": "done"}
            ]}),
            encoding="utf-8",
        )
        os.utime(tasks / "a.json", (_time.time() - 100, _time.time() - 100))
        # b 最新 → n01 应是 done
        r = collect_probe(root, probe="node", task_id="@latest", node="n01")
        assert r["found"] is True
        assert r["status"] == "done"

    def test_tilde_root_expanded(self, tmp_path, monkeypatch):
        """root 带 `~` 必须展开 —— 否则 @latest 解析失败退化成全表扫描。

        2026-11-16 exec_001 实测踩坑：_resolve_latest_task_id 忘了 expanduser，
        Path("~/.cecoder/...") 的 is_dir()=False → task_id=None →
        node 探针取了全表第一个任务（n01=done），transition 断言读到
        ['done','done'] 而失败 —— 但 UI/DOM 断言全过，极具迷惑性。
        """

        home = tmp_path / "home"
        tasks = home / ".cecoder" / "engineering-workbench" / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "a.json").write_text(
            json.dumps({"id": "a", "nodes": [
                {"instanceId": "n01", "definitionId": "ex.x", "status": "ready"}
            ]}),
            encoding="utf-8",
        )
        monkeypatch.setenv("HOME", str(home))
        # root 用 ~ 写法（通道默认值就是这种写法）
        r = collect_probe("~/.cecoder/engineering-workbench", probe="node",
                          task_id="@latest", node="n01")
        assert r["found"] is True
        assert r["status"] == "ready"
        assert r["task_id"] == "a"

    def test_explicit_task_id_still_works(self, tmp_path):
        root = tmp_path / "wb"
        tasks = root / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "a.json").write_text(
            json.dumps({"id": "a", "nodes": [
                {"instanceId": "n01", "definitionId": "ex.x", "status": "ready"}
            ]}),
            encoding="utf-8",
        )
        (tasks / "b.json").write_text(
            json.dumps({"id": "b", "nodes": [
                {"instanceId": "n01", "definitionId": "ex.x", "status": "done"}
            ]}),
            encoding="utf-8",
        )
        import os
        import time as _time
        os.utime(tasks / "a.json", (_time.time() - 100, _time.time() - 100))
        # 显式指定 a（旧）→ 不受 @latest 影响
        r = collect_probe(root, probe="node", task_id="a", node="n01")
        assert r["status"] == "ready"


class TestStateMachineRules:
    """状态机级规则 —— 依据 node-machine.ts 的权威定义。

    注意: 真实数据里这些规则**零命中**，说明 CeCoder 的状态机实现自洽。
    这里的测试用构造数据验证规则逻辑本身正确（否则可能是规则写错导致漏检）。
    """

    def test_done_after_pending_breaks_chain_order(self, workbench):
        """链顺序: done 必须是前缀（node-machine.ts:12 currentNodeIndex）。"""
        root, tasks, _ = workbench
        _write_task(tasks, "T1", [
            _node("n01", "example.concept-design", "pending", gates=ALLOW),
            _node("n02", "example.detail-design", "done", gates=ALLOW),
        ])
        snap = ProcessTruthSource(root).collect()
        rules = [v["rule"] for v in snap.violations]
        assert "done_after_pending_breaks_chain_order" in rules

    def test_prefix_done_is_legal(self, workbench):
        root, tasks, _ = workbench
        _write_task(tasks, "T1", [
            _node("n01", "example.concept-design", "done", gates=ALLOW),
            _node("n02", "example.detail-design", "pending", gates=ALLOW),
        ])
        snap = ProcessTruthSource(root).collect()
        rules = [v["rule"] for v in snap.violations]
        assert "done_after_pending_breaks_chain_order" not in rules

    def test_executing_not_current_node(self, workbench):
        """只能执行当前工序（node-machine.ts:48）。"""
        root, tasks, _ = workbench
        _write_task(tasks, "T1", [
            _node("n01", "example.concept-design", "running", gates=ALLOW),
            _node("n02", "example.detail-design", "running", gates=ALLOW),
        ])
        snap = ProcessTruthSource(root).collect()
        found = [v for v in snap.violations if v["rule"] == "executing_not_current_node"]
        assert len(found) == 1
        assert found[0]["instance_id"] == "n02"  # n01 是当前节点，n02 才是违规

    def test_review_without_validation_ok(self, workbench):
        """review 必须有 validation.ok 才能推进（node-machine.ts:77）。"""
        root, tasks, _ = workbench
        n = _node("n01", "example.ui-prototype", "review", gates=ALLOW)
        n["run"] = {"id": "r1", "kind": "conversation"}  # 没有 validation
        _write_task(tasks, "T1", [n])
        snap = ProcessTruthSource(root).collect()
        rules = [v["rule"] for v in snap.violations]
        assert "review_without_validation_ok" in rules

    def test_review_with_validation_ok_is_legal(self, workbench):
        root, tasks, _ = workbench
        n = _node("n01", "example.ui-prototype", "review", gates=ALLOW)
        n["run"] = {"id": "r1", "kind": "conversation", "validation": {"ok": True}}
        _write_task(tasks, "T1", [n])
        snap = ProcessTruthSource(root).collect()
        rules = [v["rule"] for v in snap.violations]
        assert "review_without_validation_ok" not in rules

    def test_unknown_status_value(self, workbench):
        root, tasks, _ = workbench
        _write_task(tasks, "T1", [_node("n01", "example.concept-design", "weird_status", gates=ALLOW)])
        snap = ProcessTruthSource(root).collect()
        rules = [v["rule"] for v in snap.violations]
        assert "unknown_status_value" in rules

    def test_validating_is_legal_status(self):
        """validating 是源码里的合法状态（NODE_STATUSES 第 4 项）。"""
        from nlaut.judge.process_truth import STATUS_VALUES

        assert "validating" in STATUS_VALUES
        assert len(STATUS_VALUES) == 8
        assert set(STATUS_VALUES) == {
            "pending", "ready", "running", "validating",
            "review", "done", "failed", "interrupted",
        }

    def test_startable_and_executing_sets_match_source(self):
        """STARTABLE / EXECUTING 常量必须与 node-machine.ts 一致。"""
        from nlaut.judge.process_truth import EXECUTING_STATUSES, STARTABLE_STATUSES

        assert set(STARTABLE_STATUSES) == {"ready", "review", "failed", "interrupted"}
        assert set(EXECUTING_STATUSES) == {"running", "validating"}


class TestSnapshotContract:
    """TruthSource 契约。"""

    def test_snapshot_violations(self, workbench):
        root, tasks, _ = workbench
        _write_task(tasks, "1001", [_node("n01", "example.delivery", "done", gates=ALLOW, outputs=0)])
        ts = ProcessTruthSource(root)
        ev = ts.snapshot("violations")
        assert ev.ok is True
        assert "violations" in ev.value
        assert ev.source.endswith("#violations")

    def test_snapshot_unknown_key_returns_error(self, workbench):
        root, _, _ = workbench
        ev = ProcessTruthSource(root).snapshot("nonexistent")
        assert ev.ok is False
        assert ev.error is not None
        assert "unsupported_key" in ev.error

    def test_as_db_state_shape(self, workbench):
        root, tasks, _ = workbench
        _write_task(tasks, "1001", [_node("n01", "example.concept-design", "done", gates=ALLOW)])
        state = ProcessTruthSource(root).as_db_state()
        assert set(state) == {"process_violations", "process_stats", "process_nodes"}


class TestStats:
    def test_stats_counts(self, workbench):
        root, tasks, _ = workbench
        _write_task(tasks, "1001", [
            _node("n01", "example.concept-design", "done", gates=ALLOW),
            _node("n02", "example.detail-design", "pending", gates=ALLOW, outputs=0),
        ])
        snap = ProcessTruthSource(root).collect()
        assert snap.stats["task_count"] == 1
        assert snap.stats["node_count"] == 2
        assert snap.stats["by_status"] == {"done": 1, "pending": 1}
        assert snap.stats["violation_count"] == 0
