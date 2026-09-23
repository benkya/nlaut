"""Layer A 元测试：会话日志（append-only/回放）+ 证据存储 + IR 库。"""

from __future__ import annotations

import sys
from pathlib import Path

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
        assert {c.id for c in cases} == {
            "tc_login_001", "tc_login_002", "tc_login_003", "tc_login_004",
            "tc_login_005", "tc_login_006",
        }
        assert store.get("tc_login_001").title.startswith("登录")
        assert store.get("tc_login_002").data_ref == "login_valid"
        assert store.get("tc_login_004").data_ref == "login_lock_demo"
        assert store.get("tc_nope") is None

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
