"""Layer A 框架元测试：IR schema（含判别联合、校验器、序列化回路）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))  # 未安装时直接引用 src 布局

from nlaut.ir.model import AssertChoice, Quad, StepFill, TestCaseIR


def _base_ir() -> dict:
    return {
        "id": "tc_smoke_001",
        "title": "冒烟-页面可打开",
        "source": "口述: 首页能打开",
        "channel": "web",
        "quad": {"actor": "首页", "action": "打开", "object": "浏览器标签页"},
        "steps": [{"action": "nav", "path": "/"}],
        "assertions": [{"kind": "text_visible", "selector": "h1", "expected": "首页"}],
    }


class TestIRModel:
    def test_minimal_ir_roundtrip(self):
        ir = TestCaseIR.model_validate(_base_ir())
        dumped = TestCaseIR.model_validate(_ir := ir.model_dump(mode="json"))
        assert dumped == ir and _ir["id"].startswith("tc_")

    def test_demo_case_loads_and_validates(self):
        ir = TestCaseIR.from_yaml_file(ROOT / "cases/demo/tc_login_001.yaml")
        assert ir.id == "tc_login_001" and ir.channel == "web"
        assert ir.source  # 追溯锚点非空
        assert len(ir.steps) >= 4 and len(ir.assertions) == 3
        kinds = {a.kind for a in ir.assertions}
        assert kinds == {"text_visible", "visual_state", "noul"}  # 三级漏斗齐备

    def test_source_required(self):
        data = _base_ir() | {"source": ""}
        with pytest.raises(ValidationError):
            TestCaseIR.model_validate(data)

    def test_id_pattern(self):
        data = _base_ir() | {"id": "Login-Case-1"}
        with pytest.raises(ValidationError):
            TestCaseIR.model_validate(data)

    def test_unknown_action_rejected(self):
        data = _base_ir() | {"steps": [{"action": "sleep", "ms": 3000}]}
        with pytest.raises(ValidationError):
            TestCaseIR.model_validate(data)  # 未知 action 一律拒绝——禁止 sleep 类步骤

    def test_unknown_assertion_kind_rejected(self):
        data = _base_ir() | {"assertions": [{"kind": "llm_vibe", "prompt": "感觉对吗"}]}
        with pytest.raises(ValidationError):
            TestCaseIR.model_validate(data)

    def test_choice_requires_options(self):
        data = _base_ir() | {
            "assertions": [{"kind": "choice", "question": "失败归因?", "options": []}]
        }
        with pytest.raises(ValidationError):
            TestCaseIR.model_validate(data)  # choice 必须闭合选项集

    def test_choice_with_options_ok(self):
        data = _base_ir() | {
            "assertions": [
                {"kind": "choice", "question": "归因", "options": ["环境", "产品缺陷"]}
            ]
        }
        ir = TestCaseIR.model_validate(data)
        assert isinstance(ir.assertions[0], AssertChoice)

    def test_empty_steps_or_assertions_rejected(self):
        for field in ("steps", "assertions"):
            data = _base_ir() | {field: []}
            with pytest.raises(ValidationError):
                TestCaseIR.model_validate(data)

    def test_fill_step_discriminated(self):
        data = _base_ir() | {"steps": [{"action": "fill", "selector": "#u", "value": "$user"}]}
        ir = TestCaseIR.model_validate(data)
        assert isinstance(ir.steps[0], StepFill)

    def test_yaml_roundtrip_preserves_fields(self):
        ir = TestCaseIR.model_validate(_base_ir())
        again = TestCaseIR.model_validate(__import__("yaml").safe_load(ir.to_yaml()))
        assert again == ir

    def test_channel_must_be_known(self):
        data = _base_ir() | {"channel": "mobile"}
        with pytest.raises(ValidationError):
            TestCaseIR.model_validate(data)


class TestQuad:
    def test_preconditions_default_empty(self):
        q = Quad(actor="登录页", action="提交", object="表单")
        assert q.preconditions == []
