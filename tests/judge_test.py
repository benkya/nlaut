"""Layer A 元测试：判定协议 + 确定性引擎 + 置信度路由。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nlaut.judge import (
    DeterministicJudge,
    Evidence,
    Question,
    Verdict,
    get_judge,
    register_judge,
    route,
)


class TestProtocol:
    def test_registry_roundtrip(self):
        register_judge("det-test", DeterministicJudge())
        assert get_judge("det-test") is not None

    def test_get_unknown_judge_raises(self):
        with pytest.raises(KeyError):
            get_judge("不存在的引擎")

    def test_verdict_validates_confidence_range(self):
        with pytest.raises(ValidationError):
            Verdict(engine="laya", kind="noul", value=0.5, confidence=1.5)


class TestDeterministicJudge:
    def _ev(self, dom: dict[str, str]) -> Evidence:
        return Evidence(case_id="tc_login_001", dom_state=dom)

    def test_match_pass(self):
        q = Question(kind="noul", text="密码错误", context={"selector": ".error-msg"})
        v = DeterministicJudge().judge(self._ev({".error-msg": "用户名或密码错误"}), q)
        assert v.value == 1.0 and v.confidence == 1.0
        assert route(v).status == "auto_pass"

    def test_no_match_fail(self):
        q = Question(kind="noul", text="登录成功", context={"selector": ".error-msg"})
        v = DeterministicJudge().judge(self._ev({".error-msg": "密码错误"}), q)
        assert v.value == 0.0
        assert route(v).status == "auto_fail"

    def test_negated(self):
        q = Question(
            kind="noul", text="登录成功", context={"selector": ".msg", "negated": True}
        )
        v = DeterministicJudge().judge(self._ev({".msg": "密码错误"}), q)
        assert v.value == 1.0

    def test_missing_selector_raises(self):
        q = Question(kind="noul", text="x")
        with pytest.raises(ValueError):
            DeterministicJudge().judge(self._ev({}), q)


class TestArbiterRouting:
    """三级置信度路由是 AI 误报防线，规则必须逐条锁死。"""

    def _v(self, conf: float, value: float, engine: str = "mlx-vlm") -> Verdict:
        return Verdict(engine=engine, kind="noul", value=value, confidence=conf)

    def test_deterministic_autodecides(self):
        assert route(self._v(1.0, 0.0, "deterministic")).status == "auto_fail"
        assert route(self._v(1.0, 1.0, "deterministic")).status == "auto_pass"

    def test_high_confidence_autodecides(self):
        assert route(self._v(0.95, 0.95)).status == "auto_pass"
        assert route(self._v(0.95, 0.10)).status == "auto_fail"

    def test_mid_confidence_human_review(self):
        assert route(self._v(0.7, 0.95)).status == "human_review"

    def test_low_confidence_human_review(self):
        assert route(self._v(0.3, 0.95)).status == "human_review"

    def test_threshold_boundary(self):
        # threshold=0.9 时 value=0.9 应 pass（>=）
        assert route(self._v(0.95, 0.90), threshold=0.9).status == "auto_pass"
        assert route(self._v(0.95, 0.89), threshold=0.9).status == "auto_fail"
