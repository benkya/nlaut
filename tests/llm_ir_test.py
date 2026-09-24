"""Layer A 框架元测试：v0.2.0 新增 API 通道 IR + 确定性判定引擎扩展。

测试覆盖：
1. 新增 step 类型（api_call / api_followup / api_tool_call / api_stream）IR 校验
2. 新增 assertion 类型（response_exact / response_contains / response_not_contains /
   response_json_schema / tool_call_params / response_time）IR 校验
3. channel: "api" 接受
4. LLM 用例 YAML 文件加载并校验
5. DeterministicJudge 新增 6 种模式的判定逻辑验证
6. Evidence 新增字段验证
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nlaut.ir.model import (
    AssertResponseContains,
    AssertResponseExact,
    AssertResponseJsonSchema,
    AssertResponseNotContains,
    AssertResponseTime,
    AssertToolCallParams,
    StepApiCall,
    StepApiFollowup,
    StepApiStream,
    StepApiToolCall,
    TestCaseIR,
)
from nlaut.judge.deterministic import DeterministicJudge
from nlaut.judge.protocol import Evidence, Question


def _base_api_ir() -> dict:
    return {
        "id": "tc_llm_smoke_001",
        "title": "LLM冒烟-基础对话",
        "source": "口述: 测试大模型能正常回答问题",
        "channel": "api",
        "quad": {"actor": "大模型", "action": "回答", "object": "数学问题"},
        "steps": [{"action": "api_call", "prompt": "1+1等于几？"}],
        "assertions": [{"kind": "response_contains", "keywords": ["2"], "min_match": 1}],
    }


class TestApiStepTypes:
    """新增 API 步骤类型的 IR 校验。"""

    def test_api_call_step_ok(self):
        ir = TestCaseIR.model_validate(_base_api_ir())
        assert isinstance(ir.steps[0], StepApiCall)

    def test_api_followup_step_ok(self):
        data = _base_api_ir() | {
            "steps": [
                {"action": "api_call", "prompt": "我叫张三"},
                {"action": "api_followup", "prompt": "我叫什么？"},
            ]
        }
        ir = TestCaseIR.model_validate(data)
        assert isinstance(ir.steps[1], StepApiFollowup)

    def test_api_tool_call_step_ok(self):
        data = _base_api_ir() | {
            "steps": [{
                "action": "api_tool_call",
                "prompt": "北京天气",
                "tools": [{"type": "function", "function": {"name": "get_weather"}}],
            }]
        }
        ir = TestCaseIR.model_validate(data)
        assert isinstance(ir.steps[0], StepApiToolCall)

    def test_api_stream_step_ok(self):
        data = _base_api_ir() | {
            "steps": [{"action": "api_stream", "prompt": "讲个故事"}]
        }
        ir = TestCaseIR.model_validate(data)
        assert isinstance(ir.steps[0], StepApiStream)

    def test_api_channel_accepted(self):
        ir = TestCaseIR.model_validate(_base_api_ir())
        assert ir.channel == "api"


class TestLlmAssertionTypes:
    """新增 LLM 响应断言类型的 IR 校验。"""

    def test_response_exact_assertion_ok(self):
        data = _base_api_ir() | {
            "assertions": [{"kind": "response_exact", "expected": "130"}]
        }
        ir = TestCaseIR.model_validate(data)
        assert isinstance(ir.assertions[0], AssertResponseExact)

    def test_response_contains_assertion_ok(self):
        data = _base_api_ir() | {
            "assertions": [{"kind": "response_contains", "keywords": ["长江"], "min_match": 1}]
        }
        ir = TestCaseIR.model_validate(data)
        assert isinstance(ir.assertions[0], AssertResponseContains)

    def test_response_not_contains_assertion_ok(self):
        data = _base_api_ir() | {
            "assertions": [{"kind": "response_not_contains", "forbidden": ["炸弹"]}]
        }
        ir = TestCaseIR.model_validate(data)
        assert isinstance(ir.assertions[0], AssertResponseNotContains)

    def test_response_json_schema_assertion_ok(self):
        data = _base_api_ir() | {
            "assertions": [{
                "kind": "response_json_schema",
                "required_fields": ["name"],
                "expected_values": {"name": "Python"},
            }]
        }
        ir = TestCaseIR.model_validate(data)
        assert isinstance(ir.assertions[0], AssertResponseJsonSchema)

    def test_tool_call_params_assertion_ok(self):
        data = _base_api_ir() | {
            "assertions": [{
                "kind": "tool_call_params",
                "expected_function": "get_weather",
                "expected_params": {"city": "北京"},
            }]
        }
        ir = TestCaseIR.model_validate(data)
        assert isinstance(ir.assertions[0], AssertToolCallParams)

    def test_response_time_assertion_ok(self):
        data = _base_api_ir() | {
            "assertions": [{"kind": "response_time", "max_seconds": 5.0}]
        }
        ir = TestCaseIR.model_validate(data)
        assert isinstance(ir.assertions[0], AssertResponseTime)

    def test_unknown_assertion_kind_still_rejected(self):
        """未知断言类型仍然被拒绝（判别联合拒绝面）。"""
        data = _base_api_ir() | {
            "assertions": [{"kind": "llm_vibe", "prompt": "感觉对吗"}]
        }
        with pytest.raises(ValidationError):
            TestCaseIR.model_validate(data)


class TestLlmCaseYamlLoad:
    """cases/llm/ 下的 YAML 用例能正确加载。"""

    def test_all_llm_cases_load_and_validate(self):
        llm_dir = ROOT / "cases" / "llm"
        if not llm_dir.exists():
            pytest.skip("cases/llm/ 不存在")
        yamls = sorted(llm_dir.glob("*.yaml"))
        assert len(yamls) >= 7, f"期望至少 7 条 LLM 用例，实际 {len(yamls)}"
        for p in yamls:
            ir = TestCaseIR.from_yaml_file(p)
            assert ir.channel == "api", f"{p.name} channel 不是 api"
            assert ir.source, f"{p.name} source 为空"
            assert len(ir.steps) >= 1
            assert len(ir.assertions) >= 1


class TestDeterministicJudgeLlmModes:
    """DeterministicJudge 新增 6 种 LLM 响应判定模式的逻辑验证。"""

    judge = DeterministicJudge()

    def _ev(self, **kw) -> Evidence:
        return Evidence(case_id="test", **kw)

    def _q(self, ctx: dict) -> Question:
        return Question(kind="noul", text="", context=ctx)

    def test_response_exact_match_pass(self):
        ev = self._ev(llm_response="130")
        q = self._q({"mode": "response_exact"})
        q = Question(kind="noul", text="130", context={"mode": "response_exact"})
        v = self.judge.judge(ev, q)
        assert v.value == 1.0 and v.confidence == 1.0

    def test_response_exact_match_fail(self):
        ev = self._ev(llm_response="140")
        q = Question(kind="noul", text="130", context={"mode": "response_exact"})
        v = self.judge.judge(ev, q)
        assert v.value == 0.0

    def test_response_contains_pass(self):
        ev = self._ev(llm_response="中国最长的河流是长江，约6300公里")
        q = self._q({"mode": "response_contains", "keywords": ["长江", "6300"], "min_match": 2})
        v = self.judge.judge(ev, q)
        assert v.value == 1.0

    def test_response_contains_fail(self):
        ev = self._ev(llm_response="中国最长的河流是黄河")
        q = self._q({"mode": "response_contains", "keywords": ["长江"], "min_match": 1})
        v = self.judge.judge(ev, q)
        assert v.value == 0.0

    def test_response_not_contains_pass(self):
        ev = self._ev(llm_response="我无法提供此类信息")
        q = self._q({"mode": "response_not_contains", "forbidden": ["炸药", "硝酸铵"]})
        v = self.judge.judge(ev, q)
        assert v.value == 1.0

    def test_response_not_contains_fail(self):
        ev = self._ev(llm_response="炸药的制作方法是...")
        q = self._q({"mode": "response_not_contains", "forbidden": ["炸药"]})
        v = self.judge.judge(ev, q)
        assert v.value == 0.0

    def test_response_json_schema_pass(self):
        ev = self._ev(llm_response='{"name": "Python", "domain": "web"}')
        q = self._q({"mode": "response_json_schema", "required_fields": ["name"], "expected_values": {"name": "Python"}})
        v = self.judge.judge(ev, q)
        assert v.value == 1.0

    def test_response_json_schema_code_fence_stripped(self):
        """LLM 输出 JSON 带 ```json 围栏（通用行为），判定必须容错。"""
        ev = self._ev(llm_response='```json\n{"name": "Python", "items": 3}\n```')
        q = self._q({"mode": "response_json_schema", "required_fields": ["name", "items"]})
        v = self.judge.judge(ev, q)
        assert v.value == 1.0

    def test_response_json_schema_array_form(self):
        """合法 JSON 但为数组形态（模型风格差异，如 Qwen3.8-Flash），不应判失败。"""
        ev = self._ev(llm_response='```json\n[{"语言": "Python", "领域": "AI"}, {"语言": "Java"}]\n```')
        q = self._q({"mode": "response_json_schema", "required_fields": ["语言"]})
        v = self.judge.judge(ev, q)
        assert v.value == 1.0
        assert v.raw.get("json_form") == "array"

    def test_response_json_schema_array_missing_field(self):
        """数组形态：required_fields 在元素级检查，全部元素缺该字段才 fail。"""
        ev = self._ev(llm_response='[{"a": 1}, {"a": 2}]')
        q = self._q({"mode": "response_json_schema", "required_fields": ["name"]})
        v = self.judge.judge(ev, q)
        assert v.value == 0.0
        assert v.raw.get("missing") == ["name"]

    def test_response_json_schema_truncated_json(self):
        """真正截断的 JSON（语法错误）必须 fail——容错不等于放水。"""
        ev = self._ev(llm_response='```json\n[{"name": "Python",\n')
        q = self._q({"mode": "response_json_schema", "required_fields": []})
        v = self.judge.judge(ev, q)
        assert v.value == 0.0
        assert "not valid JSON" in v.raw.get("error", "")

    def test_response_json_schema_not_json(self):
        ev = self._ev(llm_response="这不是JSON")
        q = self._q({"mode": "response_json_schema", "required_fields": ["name"]})
        v = self.judge.judge(ev, q)
        assert v.value == 0.0

    def test_tool_call_params_pass(self):
        ev = self._ev(tool_calls=[{
            "id": "call_1", "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city": "北京"}'},
        }])
        q = self._q({"mode": "tool_call_params", "expected_function": "get_weather", "expected_params": {"city": "北京"}})
        v = self.judge.judge(ev, q)
        assert v.value == 1.0

    def test_tool_call_params_wrong_function(self):
        ev = self._ev(tool_calls=[{
            "function": {"name": "other_fn", "arguments": "{}"},
        }])
        q = self._q({"mode": "tool_call_params", "expected_function": "get_weather"})
        v = self.judge.judge(ev, q)
        assert v.value == 0.0

    def test_response_time_pass(self):
        ev = self._ev(latency_ms=3200.0)
        q = self._q({"mode": "response_time", "max_seconds": 5.0})
        v = self.judge.judge(ev, q)
        assert v.value == 1.0

    def test_response_time_fail(self):
        ev = self._ev(latency_ms=8200.0)
        q = self._q({"mode": "response_time", "max_seconds": 5.0})
        v = self.judge.judge(ev, q)
        assert v.value == 0.0


class TestEvidenceLlmFields:
    """Evidence 新增字段验证。"""

    def test_evidence_llm_fields(self):
        ev = Evidence(
            case_id="test",
            llm_response="答案",
            llm_response_json={"k": "v"},
            tool_calls=[{"function": {"name": "f"}}],
            latency_ms=123.4,
            conversation=[{"role": "user", "content": "hi"}],
        )
        assert ev.llm_response == "答案"
        assert ev.llm_response_json == {"k": "v"}
        assert ev.tool_calls[0]["function"]["name"] == "f"
        assert ev.latency_ms == 123.4
        assert ev.conversation[0]["content"] == "hi"

    def test_evidence_llm_fields_optional(self):
        """新增字段都是可选的，不影响 web 通道使用。"""
        ev = Evidence(case_id="test")
        assert ev.llm_response is None
        assert ev.tool_calls is None
        assert ev.latency_ms is None


class TestLayaRenderState:
    """Laya render_state 对 API 通道证据的渲染（v0.2.0 Phase 2）。"""

    def test_llm_response_rendered_first(self):
        """llm_response 优先渲染，且用户 prompt 也在 state 中。"""
        from nlaut.judge.laya import render_state

        ev = Evidence(
            case_id="tc_d04_p0_001",
            llm_response="最终价格是130元。",
            conversation=[{"role": "user", "content": "一个商品原价200元..."}],
            response={"choices": [{"message": {"content": "最终价格是130元。"}}]},
            latency_ms=1200.0,
        )
        state = render_state(ev)
        # 模型回答必须渲染，且位置在原始 response JSON 之前
        assert "模型回答: 最终价格是130元。" in state
        assert "用户输入: 一个商品原价200元..." in state
        assert "响应延迟: 1200ms" in state
        assert state.index("模型回答") < state.index("API 响应")

    def test_web_evidence_unchanged(self):
        """web 通道证据（无 llm 字段）渲染结果不引入新噪音。"""
        from nlaut.judge.laya import render_state

        ev = Evidence(
            case_id="tc_login_001",
            dom_state={".error-msg": "密码错误"},
            screenshot="artifacts/screenshots/t.png",
        )
        state = render_state(ev)
        assert "DOM 可见状态" in state
        assert "模型回答" not in state  # 无 LLM 字段时不应渲染空节
        assert "用户输入" not in state

    def test_tool_calls_rendered(self):
        from nlaut.judge.laya import render_state

        ev = Evidence(
            case_id="tc_d12_p0_001",
            llm_response="",
            tool_calls=[{
                "function": {"name": "get_weather", "arguments": '{"city": "北京"}'},
            }],
        )
        state = render_state(ev)
        assert "工具调用: get_weather(" in state
