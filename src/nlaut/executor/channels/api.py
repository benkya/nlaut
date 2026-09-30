"""L4 API 通道：IR steps → HTTP 调用 LLM API → Evidence（v0.2.0 新增）。

支持四种 step 类型：
- api_call: 单轮调用（OpenAI-compatible chat/completions）
- api_followup: 多轮对话后续消息（维护 messages 列表）
- api_tool_call: 带工具定义的调用（tools 参数，解析 tool_calls）
- api_stream: 流式调用（stream=true，收完整后比对）

模型配置通过 config/models.yaml 或 CLI --model/--api-base/--api-key 注入。
所有调用使用 httpx 同步客户端（与 web 通道保持一致的同步模式）。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import httpx

from ...ir.model import TestCaseIR


def _resolve_vars(value: str, data: dict[str, str]) -> str:
    """把 $var 替换为数据集字段；未知变量报错（与 web 通道一致）。"""
    import re

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in data:
            raise KeyError(f"数据集缺少字段 {key!r}（data_ref 提供: {sorted(data)}）")
        return data[key]

    return re.sub(r"\$([a-zA-Z_][a-zA-Z0-9_]*)", _sub, value)


def _build_messages(
    steps: list,
    data: dict[str, str],
    messages: list[dict] | None = None,
) -> tuple[list[dict], list[dict] | None, tuple[bool, str | None] | None]:
    """从 steps 构建请求 messages + tools + stream 标记。

    返回 (messages, tools, stream_flag_info)
    - messages: OpenAI 格式的消息列表
    - tools: 工具定义（仅 api_tool_call 时有值）
    - stream_flag_info: None 或 (is_stream: bool, system_prompt: str|None)

    对于 api_followup，追加到已有 messages。
    """
    if messages is None:
        messages = []

    tools = None
    system_prompt = None
    is_stream = False

    for step in steps:
        if step.action == "api_call":
            system_prompt = step.system_prompt
            sp = _resolve_vars(step.system_prompt, data) if step.system_prompt else None
            if sp:
                messages.append({"role": "system", "content": sp})
            messages.append({"role": "user", "content": _resolve_vars(step.prompt, data)})
        elif step.action == "api_followup":
            messages.append({"role": "user", "content": _resolve_vars(step.prompt, data)})
        elif step.action == "api_tool_call":
            messages.append({"role": "user", "content": _resolve_vars(step.prompt, data)})
            tools = step.tools
        elif step.action == "api_stream":
            system_prompt = step.system_prompt
            sp = _resolve_vars(step.system_prompt, data) if step.system_prompt else None
            if sp:
                messages.append({"role": "system", "content": sp})
            messages.append({"role": "user", "content": _resolve_vars(step.prompt, data)})
            is_stream = True
        # web steps (nav/fill/click/...) 在 API 通道中忽略

    return messages, tools, (is_stream, system_prompt) if is_stream or system_prompt else None


def _call_api(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    tools: list[dict] | None = None,
    stream: bool = False,
    timeout: float = 60.0,
    repeat: int = 1,
    reasoning_effort: str = "",
) -> tuple[dict, float]:
    """调用 OpenAI-compatible API，返回 (response_json, latency_ms)。

    支持 stream=true：收集所有 chunk 拼成完整 response。
    reasoning_effort: 推理模型思考强度（如 "low"/"medium"/"high"），空串不传。
    """
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body: dict = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    if tools:
        body["tools"] = tools
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort

    t0 = time.time()

    if stream:
        full_content = ""
        finish_reason = None
        tool_calls_accum: dict[int, dict] = {}
        ttft_ms: float | None = None  # 首 token 延迟（首个含 content/reasoning 的 chunk，见下方语义注释）
        stream_chunks = 0
        reasoning_len = 0  # 推理模型思考总量（字符）——用于报告与诊断，不参与断言

        with (
            httpx.Client(timeout=timeout, trust_env=False) as client,
            client.stream("POST", url, headers=headers, json=body) as resp,
        ):
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                # Qwen3 等推理模型的流式会插入 choices 为空数组的 chunk
                # （thinking 段/usage 统计段）——必须容错跳过，否则 IndexError
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta", {}) or {}
                # TTFT 语义（v0.2.7）：首个含 content 或 reasoning_content 的 chunk 即计时——
                # 推理模型（glm-5.2 等）先流式输出思考再出正文，若只认 content，
                # 3s 上限会把所有推理模型误判为 TTFT 超标（实测首chunk 0.6-1.5s、首content 10-12s）。
                if ttft_ms is None and (delta.get("content") or delta.get("reasoning_content")):
                    ttft_ms = (time.time() - t0) * 1000
                if delta.get("content"):
                    full_content += delta["content"]
                    stream_chunks += 1
                if delta.get("reasoning_content"):
                    reasoning_len += len(delta.get("reasoning_content") or "")
                if delta.get("tool_calls"):
                    for tc in delta["tool_calls"]:
                        idx = tc.get("index", 0)
                        if idx not in tool_calls_accum:
                            tool_calls_accum[idx] = {
                                "id": tc.get("id", ""),
                                "type": "function",
                                "function": {"name": "", "arguments": ""},
                            }
                        if tc.get("function", {}).get("name"):
                            tool_calls_accum[idx]["function"]["name"] += tc["function"]["name"]
                        if tc.get("function", {}).get("arguments"):
                            tool_calls_accum[idx]["function"]["arguments"] += tc["function"]["arguments"]
                    if choices[0].get("finish_reason"):
                        finish_reason = chunk["choices"][0]["finish_reason"]

        latency_ms = (time.time() - t0) * 1000
        response = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": full_content,
                    "tool_calls": list(tool_calls_accum.values()) if tool_calls_accum else None,
                },
                "finish_reason": finish_reason or "stop",
            }],
            "streamed": True,
            "ttft_ms": round(ttft_ms, 1) if ttft_ms is not None else None,
            "stream_chunks": stream_chunks,
            "reasoning_len": reasoning_len,
        }
        return response, latency_ms

    # 非流式
    # trust_env=False：内网推理服务（如 10.x IP）必须绕过系统代理直连，
    # 否则 http_proxy 环境变量会把请求转发给代理导致连接失败（2026-09-24 实测坑）
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        resp = client.post(url, headers=headers, json=body)
        resp.raise_for_status()
    latency_ms = (time.time() - t0) * 1000
    return resp.json(), latency_ms


def execute(
    ir: TestCaseIR,
    data: dict[str, str] | None = None,
    base_url: str = "",
    api_key: str = "",
    model: str = "",
    timeout: float = 60.0,
    reasoning_effort: str = "",
    logger: Callable[[str], None] = print,
) -> dict:
    """执行一条 API 通道 IR 用例，返回 Evidence 字段。

    必须提供 base_url, api_key, model（由 runner 从模型配置注入）。
    reasoning_effort: 推理模型思考强度（如 "low"），空串不传。
    返回 dict 直接喂 Evidence(case_id=ir.id, **result)。
    """
    data = data or {}
    if not base_url or not api_key or not model:
        raise ValueError(
            f"API 通道需要 base_url / api_key / model，"
            f"得到: base_url={base_url!r} model={model!r} api_key={'***' if api_key else '空'}"
        )

    # 从 steps 构建 messages + tools
    messages, tools, stream_info = _build_messages(ir.steps, data)

    is_stream = bool(stream_info and stream_info[0])
    logger(f"[{ir.id}] API 调用: model={model} messages={len(messages)} "
           f"tools={len(tools) if tools else 0} stream={is_stream}")

    # v0.2.7 TASK-002: 当 stream 时支持 repeat 多次采样
    # 优先取最后一个 stream step 的 repeat 字段（同一用例多 step 取最大）
    repeat = 1
    if is_stream:
        from nlaut.ir.model import StepApiStream
        stream_steps = [s for s in ir.steps if isinstance(s, StepApiStream)]
        if stream_steps:
            repeat = max(s.repeat for s in stream_steps)

    if is_stream and repeat > 1:
        # 多次采样：每次单独计时，累积 ttft_samples 到 evidence
        from nlaut.metrics.percentile import p95_or_none
        ttft_samples: list[float] = []
        aggregated_content = ""
        aggregated_tool_calls: list[dict] = []
        aggregated_latency = 0.0
        last_response: dict = {}
        for i in range(repeat):
            response_i, latency_i = _call_api(
                base_url=base_url,
                api_key=api_key,
                model=model,
                messages=messages,
                tools=tools,
                stream=True,
                timeout=timeout,
                reasoning_effort=reasoning_effort,
            )
            last_response = response_i
            aggregated_latency += latency_i
            ttft_i = response_i.get("ttft_ms")
            if ttft_i is not None:
                ttft_samples.append(ttft_i)
            msg_i = response_i.get("choices", [{}])[0].get("message", {})
            aggregated_content = msg_i.get("content") or aggregated_content
            if msg_i.get("tool_calls"):
                aggregated_tool_calls = msg_i["tool_calls"]
        p95_ms = p95_or_none(ttft_samples)
        logger(f"[{ir.id}] 重复采样 {repeat} 次: 有效 TTFT 样本 {len(ttft_samples)} 个, P95={p95_ms}ms")
        response = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": aggregated_content,
                    "tool_calls": aggregated_tool_calls or None,
                },
                "finish_reason": last_response.get("choices", [{}])[0].get("finish_reason", "stop"),
            }],
            "streamed": True,
            "ttft_ms": ttft_samples[-1] if ttft_samples else None,
            "stream_chunks": last_response.get("stream_chunks"),
        }
        latency_ms = aggregated_latency
        # 把 samples 和 p95 附加到 response 之外的全局变量，execute 函数后面会读
        response["__ttft_samples__"] = ttft_samples  # type: ignore[assignment]
        response["__ttft_p95_ms__"] = p95_ms  # type: ignore[assignment]
    else:
        response, latency_ms = _call_api(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            tools=tools,
            stream=is_stream,
            timeout=timeout,
            reasoning_effort=reasoning_effort,
        )

    # 提取 assistant message
    choice = response.get("choices", [{}])[0]
    msg = choice.get("message", {})
    llm_response = msg.get("content") or ""
    tool_calls = msg.get("tool_calls") or None

    # 尝试解析 JSON（如果响应看起来像 JSON）
    llm_response_json = None
    stripped = llm_response.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            llm_response_json = json.loads(stripped)
        except json.JSONDecodeError:
            pass  # 不是合法 JSON，留为 None

    logger(f"[{ir.id}] API 响应: {latency_ms:.0f}ms "
           f"len={len(llm_response)} tool_calls={len(tool_calls) if tool_calls else 0}")

    return {
        "response": response,
        "llm_response": llm_response,
        "llm_response_json": llm_response_json,
        "tool_calls": tool_calls,
        "latency_ms": latency_ms,
        "ttft_ms": response.get("ttft_ms"),
        "stream_chunks": response.get("stream_chunks"),
        "reasoning_len": response.get("reasoning_len"),
        "ttft_samples": response.get("__ttft_samples__", []),
        "ttft_p95_ms": response.get("__ttft_p95_ms__"),
        "conversation": messages,
        # web 通道字段留空
        "screenshot": None,
        "dom_state": None,
    }
