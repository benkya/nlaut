"""process_state 断言 + process_probe 步骤的测试。

v0.5.0 新增。覆盖：
1. IR 解析：process_state 断言 / process_probe 步骤能被正确解析
2. 判定引擎：规则模式（rule=）各种分支
3. 判定引擎：探针模式（from_probe=）各种分支
4. 判定引擎：迁移判定（transition:）—— 真正的状态机测试能力
5. 降级语义：探针缺失 / 规则未知 → 绝不判通过
"""
import json

import pytest
import yaml

from nlaut.executor.runner import _engine_name
from nlaut.ir.model import TestCaseIR
from nlaut.judge import DeterministicJudge, Evidence, Question
from nlaut.judge.arbiter import route


def _write_task(tasks, task_id, nodes):
    tasks.mkdir(parents=True, exist_ok=True)
    (tasks / f"{task_id}.json").write_text(
        json.dumps({"id": task_id, "nodes": nodes}, ensure_ascii=False),
        encoding="utf-8",
    )


def _node(instance_id, definition_id, status, gates=None, outputs=0, layout=None):
    n = {
        "instanceId": instance_id,
        "definitionId": definition_id,
        "status": status,
        "outputs": [{"name": f"o{i}.md", "path": f"/x/o{i}.md"} for i in range(outputs)],
    }
    if gates is not None:
        # 真实结构: assessment.value.gates（不是 assessment.gates）
        n["assessment"] = {"value": {"kind": "normal", "gates": gates}}
    if layout is not None:
        n["assetLayout"] = {"kind": layout}
    return n


ALLOW = {"start": "allow", "complete": "allow", "autoAdvance": "allow"}


@pytest.fixture
def workbench(tmp_path):
    root = tmp_path / "engineering-workbench"
    tasks = root / "tasks"
    return root, tasks, tmp_path


# ---------- IR 解析 ----------


class TestIRParsing:
    def test_process_state_assertion_parses(self):
        raw = {
            "id": "tc_ps_001",
            "title": "工序状态断言",
            "source": "unit-test",
            "quad": {"actor": "tester", "action": "probe", "object": "cecoder"},
            "channel": "electron",
            "steps": [{"action": "nav", "path": "/"}],
            "assertions": [{"kind": "process_state", "rule": "done_without_outputs"}],
        }
        ir = TestCaseIR.model_validate(raw)
        a = ir.assertions[0]
        assert a.kind == "process_state"
        assert a.rule == "done_without_outputs"
        assert a.expect == "none"
        assert a.exclude_legacy is True
        assert a.min_severity == "medium"

    def test_process_probe_step_parses(self):
        raw = {
            "id": "tc_ps_002",
            "title": "工序探针",
            "source": "unit-test",
            "quad": {"actor": "tester", "action": "probe", "object": "cecoder"},
            "channel": "electron",
            "steps": [
                {"action": "process_probe", "probe": "node", "task_id": "T1", "node": "n01"},
            ],
            "assertions": [{"kind": "process_state", "from_probe": "status_eq:running"}],
        }
        ir = TestCaseIR.model_validate(raw)
        assert ir.steps[0].action == "process_probe"
        assert ir.steps[0].probe == "node"
        assert ir.assertions[0].from_probe == "status_eq:running"

    def test_process_state_uses_deterministic_engine(self):
        raw = {
            "id": "tc_ps_003",
            "title": "x",
            "source": "unit-test",
            "quad": {"actor": "tester", "action": "probe", "object": "cecoder"},
            "channel": "electron",
            "steps": [{"action": "nav", "path": "/"}],
            "assertions": [{"kind": "process_state", "rule": "done_without_outputs"}],
        }
        ir = TestCaseIR.model_validate(raw)
        assert _engine_name(ir.assertions[0]) == "deterministic"

    def test_yaml_roundtrip(self, tmp_path):
        p = tmp_path / "c.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "id": "tc_ps_004",
                    "title": "y",
                    "source": "unit-test",
                    "quad": {"actor": "tester", "action": "probe", "object": "cecoder"},
                    "channel": "electron",
                    "steps": [{"action": "process_probe", "probe": "chain"}],
                    "assertions": [
                        {"kind": "process_state", "rule": "chain_order", "expect": "none"}
                    ],
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        ir = TestCaseIR.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
        assert ir.assertions[0].kind == "process_state"


# ---------- 规则模式判定 ----------


class TestRuleMode:
    def _judge(self, root=None, **ctx):
        j = DeterministicJudge()
        ctx.setdefault("root", str(root) if root else "~/.cecoder/engineering-workbench")
        q = Question(kind="noul", text="", context={"mode": "process_state", **ctx})
        return j.judge(Evidence(case_id="t"), q)

    def test_violation_present_fails(self, workbench):
        root, tasks, _ = workbench
        _write_task(tasks, "T1", [_node("n01", "ex.a", "done", gates=ALLOW, outputs=0)])
        v = self._judge(root, rule="done_without_outputs", min_severity="all")
        assert v.value == 0.0
        assert v.confidence == 1.0
        assert v.raw["violation_count"] == 1
        assert route(v).status == "auto_fail"

    def test_no_violation_passes(self, workbench):
        root, tasks, _ = workbench
        _write_task(tasks, "T1", [_node("n01", "ex.a", "done", gates=ALLOW, outputs=3)])
        v = self._judge(root, rule="done_without_outputs", min_severity="all")
        assert v.value == 1.0
        assert route(v).status == "auto_pass"

    def test_expect_any_inverts(self, workbench):
        root, tasks, _ = workbench
        _write_task(tasks, "T1", [_node("n01", "ex.a", "done", gates=ALLOW, outputs=0)])
        v = self._judge(root, rule="done_without_outputs", expect="any", min_severity="all")
        assert v.value == 1.0  # 有违规 → expect=any 判通过

    def test_task_id_filter(self, workbench):
        root, tasks, _ = workbench
        _write_task(tasks, "T1", [_node("n01", "ex.a", "done", gates=ALLOW, outputs=0)])
        _write_task(tasks, "T2", [_node("n01", "ex.a", "done", gates=ALLOW, outputs=3)])
        v = self._judge(root, rule="done_without_outputs", task_id="T2", min_severity="all")
        assert v.value == 1.0
        v2 = self._judge(root, rule="done_without_outputs", task_id="T1", min_severity="all")
        assert v2.value == 0.0

    def test_min_severity_filters_low(self, workbench):
        root, tasks, _ = workbench
        # pending + outputs → pending_with_outputs（low）
        _write_task(tasks, "T1", [_node("n01", "ex.a", "pending", gates=ALLOW, outputs=3)])
        v = self._judge(root, rule="pending_with_outputs", min_severity="medium")
        assert v.value == 1.0  # low 被过滤掉 → 无违规
        v2 = self._judge(root, rule="pending_with_outputs", min_severity="all")
        assert v2.value == 0.0

    def test_unknown_rule_degrades(self):
        j = DeterministicJudge()
        q = Question(kind="noul", text="", context={"mode": "process_state", "rule": "nope"})
        v = j.judge(Evidence(case_id="t"), q)
        assert v.value == 0.5
        assert v.raw["degraded"] == "unknown_rule"
        assert route(v).status == "human_review"  # 绝不 auto_pass

    def test_missing_process_dir_degrades(self, tmp_path):
        j = DeterministicJudge()
        q = Question(
            kind="noul", text="",
            context={"mode": "process_state", "rule": "done_without_outputs",
                     "root": str(tmp_path / "nope")},
        )
        v = j.judge(Evidence(case_id="t"), q)
        assert v.value == 0.5
        assert v.raw["degraded"] == "process_dir_not_found"
        assert route(v).status == "human_review"


# ---------- 探针模式判定 ----------


class TestProbeMode:
    def _judge(self, probe, spec, expect="none", probes=None):
        j = DeterministicJudge()
        ev = Evidence(case_id="t")
        if probes is not None:
            ev.process_probes = probes
            ev.process_probe = probes[-1]
        else:
            ev.process_probe = probe
        q = Question(
            kind="noul", text="",
            context={"mode": "process_state", "from_probe": spec, "expect": expect},
        )
        return j.judge(ev, q)

    def test_status_eq_match(self):
        v = self._judge({"probe": "node", "found": True, "status": "running"},
                         "status_eq:running")
        assert v.value == 1.0
        assert v.raw["matched"] is True

    def test_status_eq_mismatch(self):
        v = self._judge({"probe": "node", "found": True, "status": "pending"},
                         "status_eq:running")
        assert v.value == 0.0

    def test_status_in_match(self):
        v = self._judge({"probe": "node", "found": True, "status": "validating"},
                         "status_in:running,validating")
        assert v.value == 1.0

    def test_index_eq(self):
        v = self._judge({"probe": "node", "found": True, "status": "x", "index": 3},
                         "index_eq:3")
        assert v.value == 1.0

    def test_output_count_ge(self):
        v = self._judge({"probe": "node", "found": True, "status": "x", "output_count": 5},
                         "output_count_ge:3")
        assert v.value == 1.0

    def test_has_validation_ok(self):
        v = self._judge({"probe": "node", "found": True, "status": "x", "validation_ok": True},
                         "has_validation_ok")
        assert v.value == 1.0

    def test_missing_probe_degrades(self):
        v = self._judge(None, "status_eq:running")
        assert v.value == 0.5
        assert v.raw["degraded"] == "no_probe_result"
        assert route(v).status == "human_review"

    def test_node_not_found_degrades(self):
        v = self._judge({"probe": "node", "found": False}, "status_eq:running")
        assert v.value == 0.5
        assert v.raw["degraded"] == "probe_node_not_found"

    def test_probe_error_degrades(self):
        v = self._judge({"probe": "node", "error": "OSError: boom"}, "status_eq:running")
        assert v.value == 0.5
        assert v.raw["degraded"] == "probe_error"

    def test_unknown_spec_degrades(self):
        v = self._judge({"probe": "node", "found": True, "status": "x"}, "bogus_op:1")
        assert v.value == 0.5
        assert v.raw["degraded"] == "unknown_probe_spec"


# ---------- 迁移判定（真正的状态机测试）----------


class TestTransition:
    def _judge(self, probes, spec="transition:ready>running", expect="none"):
        j = DeterministicJudge()
        ev = Evidence(case_id="t")
        ev.process_probes = probes
        ev.process_probe = probes[-1]
        q = Question(
            kind="noul", text="",
            context={"mode": "process_state", "from_probe": spec, "expect": expect},
        )
        return j.judge(ev, q)

    def test_transition_match(self):
        probes = [
            {"probe": "node", "found": True, "status": "ready"},
            {"probe": "node", "found": True, "status": "running"},
        ]
        v = self._judge(probes)
        assert v.value == 1.0
        assert v.raw["transition"] == "ready→running"

    def test_transition_mismatch(self):
        probes = [
            {"probe": "node", "found": True, "status": "ready"},
            {"probe": "node", "found": True, "status": "pending"},
        ]
        v = self._judge(probes)
        assert v.value == 0.0

    def test_transition_illegal_jump_detected(self):
        """pending → done 跳过 review —— 非法迁移必须能检出。

        expect=any 是「反守门员」：条件（发生了 pending→done）必须**不**成立。
        """
        probes = [
            {"probe": "node", "found": True, "status": "pending"},
            {"probe": "node", "found": True, "status": "done"},
        ]
        v = self._judge(probes, "transition:pending>done", expect="any")
        assert v.value == 0.0  # 发生了 pending→done（非法）→ 反守门员判失败
        assert v.raw["matched"] is True
        assert v.raw["transition"] == "pending→done"

    def test_transition_illegal_jump_absent_passes(self):
        """没有发生 pending→done → 反守门员通过。"""
        probes = [
            {"probe": "node", "found": True, "status": "ready"},
            {"probe": "node", "found": True, "status": "running"},
        ]
        v = self._judge(probes, "transition:pending>done", expect="any")
        assert v.value == 1.0
        assert v.raw["matched"] is False

    def test_transition_not_enough_probes_degrades(self):
        probes = [{"probe": "node", "found": True, "status": "ready"}]
        v = self._judge(probes)
        assert v.value == 0.5
        assert v.raw["degraded"] == "not_enough_probes"
        assert route(v).status == "human_review"
