"""Layer A 元测试：会话日志（append-only/回放）+ 证据存储 + IR 库。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nlaut.harness.session import SessionLog
from nlaut.ir.store import IRStore
from nlaut.judge import Evidence
from nlaut.report.evidence import EvidenceStore


class TestSessionLog:
    def test_append_only_and_replay(self, tmp_path):
        log = SessionLog(tmp_path / "session.jsonl")
        log.record({"type": "prompt", "text": "生成登录用例"})
        log.record({"type": "tool_call", "name": "write_file", "ok": True})
        entries = SessionLog(tmp_path / "session.jsonl").replay()  # 重开延续 seq
        assert [e["seq"] for e in entries] == [1, 2]
        assert entries[0]["type"] == "prompt"
        log.record({"type": "retry"})
        assert SessionLog(tmp_path / "session.jsonl").replay()[-1]["seq"] == 3

    def test_replay_missing_file(self, tmp_path):
        assert SessionLog(tmp_path / "nope.jsonl").replay() == []


class TestEvidenceStore:
    def test_save_and_load_roundtrip(self, tmp_path):
        store = EvidenceStore(tmp_path / "evidence")
        ev = Evidence(case_id="tc_login_001", dom_state={".msg": "密码错误"})
        ref = store.save(ev, run_id="r001")
        loaded = store.load(ref)
        assert loaded == ev and "tc_login_001/r001/evidence.json" in ref


class TestIRStore:
    def test_load_all_demo(self):
        store = IRStore(ROOT / "cases")
        cases = store.load_all()
        assert {c.id for c in cases} >= {
            "tc_login_001", "tc_login_002", "tc_login_003", "tc_login_004",
            "tc_login_005", "tc_login_006",
            "tc_tasks_001", "tc_tasks_002", "tc_tasks_003", "tc_tasks_004",
            "tc_tasks_006",
            "tc_query_001", "tc_query_002", "tc_query_003",
            "tc_query_004", "tc_query_005", "tc_query_006",
            "tc_page_001", "tc_page_002", "tc_page_003",
            "tc_new_001", "tc_new_002", "tc_new_003", "tc_new_004",
            "tc_giencwb_001", "tc_giencwb_002", "tc_giencwb_003",
            "tc_giencwb_004", "tc_giencwb_005", "tc_giencwb_006",
            "tc_giencwb_007",
            # v0.3.0 Electron CDP 通道
            "tc_electron_cdp_001",
            "tc_giencwb_electron_002", "tc_giencwb_electron_003",
            "tc_giencwb_electron_004", "tc_giencwb_electron_005",
            "tc_giencwb_electron_006", "tc_giencwb_electron_007",
            # v0.2.0 LLM 用例
            "tc_d01_p0_001", "tc_d02_p0_001", "tc_d04_p0_001",
            "tc_d06_p0_001", "tc_d07_p0_001", "tc_d07_p0_002", "tc_d09_p0_001",
        }
        # v0.4.1 软件工程 web 端用例为**本地资产**（cases/sweng/ 已 gitignore，
        # 含真实系统地址与凭据，不入库）；本地存在时才校验，fresh clone 不受影响。
        sweng_dir = ROOT / "cases" / "sweng"
        if sweng_dir.exists():
            sweng_ids = {p.stem for p in sweng_dir.glob("tc_sweng_*.yaml")}
            assert len(sweng_ids) >= 33
            assert sweng_ids <= {c.id for c in cases}
        assert store.get("tc_login_001").title.startswith("登录")
        assert store.get("tc_login_002").data_ref == "login_valid"
        assert store.get("tc_login_004").data_ref == "login_lock_demo"
        assert store.get("tc_nope") is None

    def test_sweng_cases_have_optional_and_wait_text(self):
        """sweng 用例约定（本地资产，目录不存在则跳过）：登录后必须关新手引导
        遮罩(optional)，且导航类用例必须 wait_text 等真实内容（消除 SPA 渲染竞态）。"""
        sweng_dir = ROOT / "cases" / "sweng"
        if not sweng_dir.exists():
            pytest.skip("本地无 cases/sweng（真实系统用例不入库）")
        store = IRStore(ROOT / "cases")
        sweng = [c for c in store.load_all() if c.id.startswith("tc_sweng_")]
        assert len(sweng) >= 33
        for c in sweng:
            if c.id == "tc_sweng_login_001":
                continue
            # 有关闭引导遮罩的 optional 步骤
            assert any(getattr(s, "selector", "") == "button.driver-popover-close-btn"
                       and getattr(s, "optional", False) for s in c.steps), c.id
            # 有 wait_text 步骤等真实内容
            assert any(getattr(s, "action", "") == "wait_text" for s in c.steps), c.id
            # 每条用例都有反向断言（挡住 404 空壳页假通过）
            assert any(getattr(a, "negated", False) for a in c.assertions), c.id

    def test_assertion_expected_resolves_data_vars(self):
        """断言 expected 里的 $var 必须被数据集解析（v0.4.1）。"""
        from nlaut.executor.runner import _question_for
        from nlaut.ir.model import AssertTextVisible

        a = AssertTextVisible(kind="text_visible", selector="body",
                              expected="共 $total 项")
        q = _question_for(a, ir=None, data={"total": "3"})
        assert q.text == "共 3 项"

    def test_optional_flag_defaults_false(self):
        """optional 默认 False —— 保持既有语义：步骤失败即用例失败。"""
        from nlaut.ir.model import StepClick, StepWaitText

        assert StepClick(action="click", selector="x").optional is False
        assert StepWaitText(action="wait_text", text="y").optional is False
        assert StepWaitText(action="wait_text", text="y", optional=True).optional is True

    def test_retry_on_error_only_retries_error(self, monkeypatch):
        """--retry-on-error：只重试 error（基础设施异常），断言失败不重试。"""
        from nlaut.executor import runner

        calls = {"n": 0}

        def fake_run_case(ir, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"case_id": ir.id, "title": ir.title, "source": ir.source,
                        "channel": ir.channel, "status": "error",
                        "assertions": [], "screenshot": None, "duration_s": 0.0}
            return {"case_id": ir.id, "title": ir.title, "source": ir.source,
                    "channel": ir.channel, "status": "passed",
                    "assertions": [], "screenshot": None, "duration_s": 1.0}

        monkeypatch.setattr(runner, "run_case", fake_run_case)
        store = IRStore(ROOT / "cases")
        res = runner.run_store(store=store, case_ids=["tc_login_001"],
                               retry_on_error=2, logger=lambda *_: None)
        assert len(res) == 1
        assert res[0]["status"] == "passed"
        assert calls["n"] == 2  # 第一次 error → 重试一次成功

        # 断言失败（failed）不触发重试
        calls["n"] = 0

        def fake_failed(ir, **kw):
            calls["n"] += 1
            return {"case_id": ir.id, "title": ir.title, "source": ir.source,
                    "channel": ir.channel, "status": "failed",
                    "assertions": [], "screenshot": None, "duration_s": 1.0}

        monkeypatch.setattr(runner, "run_case", fake_failed)
        res = runner.run_store(store=store, case_ids=["tc_login_001"],
                               retry_on_error=3, logger=lambda *_: None)
        assert res[0]["status"] == "failed"
        assert calls["n"] == 1  # 断言失败不重试

    def test_datafactory_local_override(self, tmp_path):
        """datasets.local.yaml 覆盖同名 tag（真实系统凭据走本地文件，不入库）。"""
        from nlaut.ir.datafactory import DataFactory

        (tmp_path / "datasets.yaml").write_text(
            "tag_a:\n  user: demo\n  pwd: base\ntag_b:\n  user: keep\n",
            encoding="utf-8")
        df = DataFactory(tmp_path / "datasets.yaml")
        # 无 local 文件 → 基础数据
        assert df.build("tag_a") == {"user": "demo", "pwd": "base"}
        # 有 local 文件 → 同名 tag 整体覆盖，其余 tag 保留
        (tmp_path / "datasets.local.yaml").write_text(
            "tag_a:\n  user: real-admin\n  pwd: Real@Secret\ntag_c:\n  x: '1'\n",
            encoding="utf-8")
        assert df.build("tag_a") == {"user": "real-admin", "pwd": "Real@Secret"}
        assert df.build("tag_b") == {"user": "keep"}
        assert df.build("tag_c") == {"x": "1"}

    def test_diff_ids(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        for d, cid in ((tmp_path / "a", "tc_x_1"), (tmp_path / "b", "tc_x_2")):
            (d / f"{cid}.yaml").write_text(
                f"""
id: {cid}
title: t
source: s
channel: web
quad: {{actor: a, action: b, object: c}}
steps: [{{action: nav, path: /}}]
assertions: [{{kind: text_visible, selector: h1, expected: x}}]
""",
                encoding="utf-8",
            )
        assert IRStore(tmp_path / "a").diff_ids(tmp_path / "b") == {"tc_x_1", "tc_x_2"}
