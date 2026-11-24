"""工序流 watch / driver 的迁移判定口径测试。

v0.5.0 新增。核心锁定的行为：
1. 采样式观察的「路径压缩」判定（running→review / running→done 不是非法）
2. 真非法仍然报 illegal（pending→done 等）
3. watch 的 verify 汇总口径
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from cecoder_workflow_watch import (
    transition_kind,
    verify,
    watch,
)


class TestTransitionKind:
    """采样式迁移判定：direct / path（压缩）/ illegal 三态。"""

    @pytest.mark.parametrize(
        "f,t,expect",
        [
            # 直接合法
            ("ready", "running", "direct"),
            ("running", "validating", "direct"),
            ("review", "done", "direct"),
            ("pending", "ready", "direct"),
            (None, "pending", "direct"),
            # 路径压缩（采样跳过秒级中间态）
            ("running", "review", "path"),      # running→validating→review
            ("running", "done", "path"),        # running→validating→review→done（n03 实测）
            ("validating", "done", "path"),     # validating→review→done
            # 真非法
            ("pending", "done", "illegal"),
            ("pending", "running", "illegal"),
            ("done", "running", "illegal"),
            ("failed", "running", "illegal"),
            ("review", "pending", "illegal"),
        ],
    )
    def test_kind(self, f, t, expect):
        assert transition_kind(f, t) == expect

    def test_ready_cannot_compress_to_done(self):
        """ready→done 非法：ready 的合法下一步是 running，
        running 不在可压缩集（它是分钟级停留，必然被采样到）。"""
        assert transition_kind("ready", "done") == "illegal"


class TestVerify:
    def test_all_legal_reported_ok(self):
        rec = {
            "task_id": "t",
            "final_states": {"n01": "done"},
            "transitions": [
                {"from": "ready", "to": "running", "kind": "direct", "legal": True},
                {"from": "running", "to": "done", "kind": "path", "legal": True},
            ],
        }
        r = verify(rec)
        c = next(x for x in r["checks"] if x["check"] == "transitions_legal")
        assert c["ok"] is True
        assert "路径压缩 1" in c["detail"]

    def test_illegal_reported_fail(self):
        rec = {
            "task_id": "t",
            "final_states": {},
            "transitions": [
                {"from": "pending", "to": "done", "kind": "illegal", "legal": False},
            ],
        }
        r = verify(rec)
        c = next(x for x in r["checks"] if x["check"] == "transitions_legal")
        assert c["ok"] is False
        assert len(c["illegal"]) == 1

    def test_done_prefix_order(self):
        rec = {
            "task_id": "t",
            "final_states": {"n01": "done", "n02": "done", "n03": "pending"},
            "transitions": [],
        }
        r = verify(rec)
        c = next(x for x in r["checks"] if x["check"] == "done_prefix_chain_order")
        assert c["ok"] is True

    def test_broken_prefix_order_fails(self):
        rec = {
            "task_id": "t",
            "final_states": {"n01": "done", "n02": "pending", "n03": "done"},
            "transitions": [],
        }
        r = verify(rec)
        c = next(x for x in r["checks"] if x["check"] == "done_prefix_chain_order")
        assert c["ok"] is False


class TestWatchImportable:
    def test_watch_function_exists(self):
        """watch/driver 是脚本但可被测试导入（scripts 目录入 sys.path）。"""
        assert callable(watch)
