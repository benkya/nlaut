#!/usr/bin/env python3
"""Qwen3.8-27B (10.62.48.94:8866) 能力探测：思考字段名 + 参数行为 + TTFT
新端点必须先双向验证，不能沿用 47.239.178.97:30021 的历史结论。
"""
import json
import time

import requests

BASE = "http://10.62.48.94:8866/v1"
MODEL = "Qwen3.8-27B"          # 服务端 /v1/models 返回的原样大小写

S = requests.Session()
S.headers.update({"Content-Type": "application/json"})  # 该端点无需 key


def non_stream(label, extra, prompt="用一句话说明什么是二分查找。", mt=3000):
    t0 = time.time()
    r = S.post(f"{BASE}/chat/completions",
               json={"model": MODEL, "messages": [{"role": "user", "content": prompt}],
                     "max_tokens": mt, **extra}, timeout=300)
    dt = time.time() - t0
    if r.status_code != 200:
        print(f"{label}: HTTP {r.status_code} {r.text[:250]}")
        return None
    d = r.json()
    m = d["choices"][0]["message"]
    u = d.get("usage", {}) or {}
    dtl = u.get("completion_tokens_details") or {}
    print(f"{label}: {dt:.1f}s completion={u.get('completion_tokens')} "
          f"reasoning_tokens={dtl.get('reasoning_tokens', 0)} "
          f"fingerprint={d.get('system_fingerprint')}")
    print(f"    message字段: {list(m.keys())}")
    print(f"    content: {(m.get('content') or '')[:120]!r}")
    for k in ("reasoning", "reasoning_content"):
        if m.get(k):
            print(f"    {k}({len(m[k])}): {m[k][:150]!r}")
    return d


def stream_probe(label, extra):
    t0 = time.time()
    ttft = None
    content = ""
    keys = {}
    p = {"model": MODEL, "messages": [{"role": "user", "content": "用一句话介绍 Python。"}],
         "max_tokens": 2000, "stream": True, **extra}
    with S.post(f"{BASE}/chat/completions", json=p, stream=True, timeout=300) as r:
        if r.status_code != 200:
            print(f"{label}: HTTP {r.status_code}")
            return
        for line in r.iter_lines():
            if not line:
                continue
            line = line.decode("utf-8", "ignore")
            if not line.startswith("data: "):
                continue
            s = line[6:]
            if s.strip() == "[DONE]":
                break
            try:
                c = json.loads(s)
            except json.JSONDecodeError:
                continue
            ch = c.get("choices") or []
            if not ch:
                continue
            d = ch[0].get("delta", {}) or {}
            for k in d:
                keys[k] = keys.get(k, 0) + 1
            rs = d.get("reasoning_content") or d.get("reasoning")
            if ttft is None and (d.get("content") or rs):
                ttft = (time.time() - t0) * 1000
            if d.get("content"):
                content += d["content"]
    print(f"{label}: TTFT={ttft:.0f}ms 总耗时={(time.time()-t0)*1000:.0f}ms "
          f"delta字段={keys} content_len={len(content)}")


if __name__ == "__main__":
    print("=" * 74)
    print("Qwen3.8-27B  (10.62.48.94:8866) 能力探测")
    print("=" * 74)

    print("\n=== 1) 非流式 · 裸请求（探测思考字段名）===")
    non_stream("裸请求", {})

    print("\n=== 2) chat_template_kwargs.enable_thinking=False 双向验证 ===")
    non_stream("关思考", {"chat_template_kwargs": {"enable_thinking": False}})

    print("\n=== 3) reasoning_effort=low 是否被识别 ===")
    non_stream("effort=low", {"reasoning_effort": "low"})

    print("\n=== 4) 流式 TTFT（两口径）===")
    stream_probe("流式·裸请求  ", {})
    stream_probe("流式·关思考  ", {"chat_template_kwargs": {"enable_thinking": False}})

    print("\n=== 5) 工具调用能力 ===")
    try:
        r = S.post(f"{BASE}/chat/completions", timeout=120, json={
            "model": MODEL,
            "messages": [{"role": "user", "content": "北京今天天气怎么样？用工具查。"}],
            "tools": [{"type": "function", "function": {
                "name": "get_weather",
                "description": "查询城市天气",
                "parameters": {"type": "object", "properties": {
                    "city": {"type": "string"}}, "required": ["city"]}}}],
            "max_tokens": 2000})
        d = r.json()
        m = d["choices"][0]["message"]
        tc = m.get("tool_calls")
        print(f"  http={r.status_code} tool_calls={bool(tc)}")
        if tc:
            print(f"    -> {tc[0]['function']['name']}({tc[0]['function']['arguments']})")
        else:
            print(f"    content: {(m.get('content') or '')[:150]!r}")
    except Exception as e:  # noqa: BLE001 — 探针/守卫脚本需捕获任意异常并记录
        print(f"  ERR {type(e).__name__}: {e}")