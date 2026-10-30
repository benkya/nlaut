"""P0 侧关思考能力元测试（enable_thinking / disable_thinking）

背景：--think off 只作用于 4P12S 探针（经 chat_template_kwargs.enable_thinking），
P0 对话批次此前无任何关思考通道。DeepSeek-V4.1-Flash 实测：
  裸请求            1.9s reasoning_tokens=469
  reasoning_effort=low 4.6s reasoning_tokens=1236（反而更差，服务不认该字段）
  enable_thinking=False 0.3s reasoning_tokens=0（提速 6.3×）
故 P0 侧需要独立开关，且必须走 chat_template_kwargs 而非 reasoning_effort。
"""
from __future__ import annotations

import inspect
from typing import ClassVar

from nlaut.executor.channels import api as api_mod


class _FakeResp:
    status_code = 200
    text = "{}"

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"role": "assistant", "content": "ok"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    def iter_lines(self):
        return iter([])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeClient:
    """替换 httpx.Client —— api.py 用 with httpx.Client(...) as client 发请求"""

    captured: ClassVar[dict] = {}

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, json=None, **kw):
        type(self).captured["body"] = json
        return _FakeResp()

    def stream(self, method, url, json=None, **kw):
        class _Ctx:
            def __enter__(_s):
                return iter([])

            def __exit__(_s, *a):
                return False

        type(self).captured["body"] = json
        return _Ctx()


def _capture_body(monkeypatch) -> dict:
    _FakeClient.captured = {}
    monkeypatch.setattr(api_mod.httpx, "Client", _FakeClient)
    return _FakeClient.captured


def test_call_api_signature_accepts_disable_thinking():
    """_call_api 必须支持 disable_thinking 参数"""
    sig = inspect.signature(api_mod._call_api)
    assert "disable_thinking" in sig.parameters, "_call_api 缺少 disable_thinking 参数"
    assert sig.parameters["disable_thinking"].default in (False, "", None)


def test_execute_signature_accepts_disable_thinking():
    """execute 必须把 disable_thinking 透传给 _call_api"""
    sig = inspect.signature(api_mod.execute)
    assert "disable_thinking" in sig.parameters, "execute 缺少 disable_thinking 参数"


def test_disable_thinking_builds_chat_template_kwargs(monkeypatch):
    """disable_thinking=True → body 带 chat_template_kwargs.enable_thinking=False"""
    cap = _capture_body(monkeypatch)

    api_mod._call_api(
        base_url="http://x/v1", api_key="k", model="m",
        messages=[{"role": "user", "content": "hi"}],
        disable_thinking=True,
    )
    body = cap["body"]
    assert body.get("chat_template_kwargs") == {"enable_thinking": False}, (
        f"disable_thinking=True 未生成 chat_template_kwargs.enable_thinking=False，body={body}")
    # 关键：不能同时传 reasoning_effort（该服务不认且会放大思考）
    assert "reasoning_effort" not in body, "disable_thinking 与 reasoning_effort 不应同时出现"


def test_disable_thinking_false_no_extra_params(monkeypatch):
    """默认关闭时不注入任何思考控制参数（保持旧行为零回归）"""
    cap = _capture_body(monkeypatch)

    api_mod._call_api(
        base_url="http://x/v1", api_key="k", model="m",
        messages=[{"role": "user", "content": "hi"}],
    )
    body = cap["body"]
    assert "chat_template_kwargs" not in body, "默认不应注入 chat_template_kwargs"
    assert "reasoning_effort" not in body, "默认不应注入 reasoning_effort"


def test_disable_thinking_wins_over_reasoning_effort(monkeypatch):
    """两者同时给时 disable_thinking 优先，reasoning_effort 被丢弃

    理由：DeepSeek-V4.1-Flash 网关实测 reasoning_effort=low 会让
    reasoning_tokens 从 469 涨到 1236（思考放大 2.6×），是反向优化。
    """
    cap = _capture_body(monkeypatch)

    api_mod._call_api(
        base_url="http://x/v1", api_key="k", model="m",
        messages=[{"role": "user", "content": "hi"}],
        reasoning_effort="low",
        disable_thinking=True,
    )
    body = cap["body"]
    assert body.get("chat_template_kwargs") == {"enable_thinking": False}
    assert "reasoning_effort" not in body, (
        "disable_thinking 应覆盖 reasoning_effort（该网关下 reasoning_effort 会放大思考）")


def test_ttft_accepts_reasoning_field(monkeypatch):
    """TTFT 判定必须同时认 reasoning_content 与 reasoning 两种思考字段名

    DeepSeek-V4.1-Flash 用 reasoning（无 _content 后缀），
    只认 reasoning_content 会把思考时间全算进 TTFT 等待（实测 12.5s 误判超标）。
    """
    import inspect as _i
    src = _i.getsource(api_mod)
    assert 'delta.get("reasoning_content") or delta.get("reasoning")' in src, (
        "TTFT/reasoning 采集未兼容 reasoning 字段（DeepSeek-V4.1-Flash 用 reasoning）")