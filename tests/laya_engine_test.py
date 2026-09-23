"""Laya System One 引擎测试。

契约/纯函数测试默认跑；真实推理测试需权重+标记:
    uv run pytest tests/laya_engine_test.py -m mlx -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nlaut.judge import Evidence, Question
from nlaut.judge.laya import LayaJudge, render_state


class TestRenderState:
    def test_dom_state_rendered(self):
        ev = Evidence(case_id="tc_1", dom_state={".msg": "密码错误"})
        s = render_state(ev)
        assert "tc_1" in s and ".msg" in s and "密码错误" in s

    def test_empty_evidence_minimal(self):
        s = render_state(Evidence(case_id="tc_2"))
        assert "tc_2" in s and len(s.splitlines()) == 1

    def test_response_truncated(self):
        ev = Evidence(case_id="tc_3", response={"k": "x" * 500})
        assert len(render_state(ev)) < 600


class TestLayaContract:
    def test_choice_requires_options(self):
        with pytest.raises(ValueError):
            LayaJudge()._question_def(Question(kind="choice", text="归因?"))

    def test_question_defs_shape(self):
        j = LayaJudge()
        d1 = j._question_def(Question(kind="noul", text="登录失败?"))
        assert d1 == {"type": "noul", "instructions": "登录失败?"}
        d2 = j._question_def(Question(kind="choice", text="归因?", options=["环境", "缺陷"]))
        assert set(d2["criteria"]) == {"环境", "缺陷"}
        d3 = j._question_def(Question(kind="score", text="符合度?"))
        assert isinstance(d3["criteria"], list) and len(d3["criteria"]) == 5


@pytest.mark.mlx
class TestLayaIntegration:
    """真实推理：4 题金标准（需 laya-multilingual-mlx 权重已缓存）。"""

    GOLDEN: ClassVar = [
        (
            {"case_id": "tc_laya_1", "dom_state": {".error-msg": "用户名或密码错误"}},
            "该状态是否表明登录被拒绝（登录失败）？",
            1.0,
        ),
        (
            {"case_id": "tc_laya_2", "dom_state": {"#welcome": "欢迎回来 张鹏"}},
            "该状态是否表明登录被拒绝（登录失败）？",
            0.0,
        ),
        (
            {"case_id": "tc_laya_3", "dom_state": {"#net": "网络异常 ERR_CONNECTION_TIMED_OUT"}},
            "该状态是否表明发生了网络/环境类故障（而非产品功能缺陷）？",
            1.0,
        ),
        (
            {"case_id": "tc_laya_4", "response": {"status": 500, "body": "Internal Server Error"}},
            "该状态是否表明发生了网络/环境类故障（而非产品功能缺陷）？",
            0.0,
        ),
    ]

    def test_golden_four(self):
        j = LayaJudge()
        ok = 0
        for ev_kw, q_text, expected in self.GOLDEN:
            v = j.judge(Evidence(**ev_kw), Question(kind="noul", text=q_text))
            assert v.engine == "laya" and v.kind == "noul"
            assert 0.0 <= float(v.value) <= 1.0 and 0.0 <= v.confidence <= 1.0
            if (v.value >= 0.5) == (expected >= 0.5):
                ok += 1
        assert ok >= 3, f"金标准 4 题仅对 {ok} 题（要求 ≥3）"

    def test_choice_judgement(self):
        j = LayaJudge()
        ev = Evidence(case_id="tc_laya_5", dom_state={".msg": "网络异常 连接超时"})
        q = Question(
            kind="choice",
            text="失败归因是什么？",
            options=["环境问题", "产品缺陷", "用例缺陷", "数据问题"],
        )
        v = j.judge(ev, q)
        assert v.kind == "choice" and str(v.value) in (q.options or [])  # 必须命中闭合选项集
