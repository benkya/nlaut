#!/usr/bin/env python3
"""DeepSeek-V4.1-Flash INT8 @ 10.62.64.38:30089 (sglang) 能力探测

与 30828 端点（vllm-0.31.0-tp8，reasoning 字段）对比：
  - 后端 sglang（非 vllm）
  - 量化 INT8（非 FP8/NVFP4/W8A8）
  - 思考字段 reasoning_content（30828 是 reasoning）
  - model_id 是路径形式 /root/models/...
新端点必须双向验证参数行为，不能沿用历史结论。
"""
import json
import os
import time

import requests

BASE = "http://10.62.64.38:30089/v1"
KEY = os.environ.get("DS41F_SGLANG_KEY", "swardz")
MODEL = "/root/models/DeepSeek-V4.1-Flash-1017-INT8"
SHORT = "DeepSeek-V4.1-Flash"

S = requests.Session()
S.headers.update({"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
# 内网 10.x 端点：禁用环境代理（否则走 127.0.0.1:7897 导致 ReadTimeout）
S.trust_env = False


def non_stream(label, extra, model=MODEL, prompt="用一句话说明什么是单元测试。", mt=3000):
    t0 = time.time()
    r = S.post(f"{BASE}/chat/completions",
               json={"model": model, "messages": [{"role": "user", "content": prompt}],
                     "max_tokens": mt, **extra}, timeout=300)
    dt = time.time() - t0
    if r.status_code != 200:
        print(f"  {label}: HTTP {r.status_code} {r.text[:250]}")
        return None
    d = r.json()
    m = d["choices"][0]["message"]
    u = d.get("usage", {}) or {}
    print(f"  {label}: {dt:.1f}s completion={u.get('completion_tokens')} "
          f"reasoning_tokens={u.get('reasoning_tokens', 0)}")
    print(f"      message字段: {list(m.keys())}")
    print(f"      content: {(m.get('content') or '')[:110]!r}")
    for k in ("reasoning_content", "reasoning"):
        if m.get(k):
            print(f"      {k}({len(m[k])}): {m[k][:130]!r}")
    return d


def stream_probe(label, extra, model=MODEL):
    t0 = time.time()
    ttft = None
    content = ""
    keys = {}
    p = {"model": model, "messages": [{"role": "user", "content": "用一句话介绍 Python。"}],
         "max_tokens": 2000, "stream": True, **extra}
    with S.post(f"{BASE}/chat/completions", json=p, stream=True, timeout=300) as r:
        if r.status_code != 200:
            print(f"  {label}: HTTP {r.status_code}")
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
    print(f"  {label}: TTFT={ttft:.0f}ms 总耗时={(time.time()-t0)*1000:.0f}ms "
          f"delta字段={keys} content_len={len(content)}")
    print(f"      开头: {content[:60]!r}")


if __name__ == "__main__":
    print("=" * 76)
    print("DeepSeek-V4.1-Flash INT8 (sglang) @ 10.62.64.38:30089 能力探测")
    print("=" * 76)

    print("\n=== 1) 非流式 · 裸请求（探测思考字段名）===")
    non_stream("裸请求", {})

    print("\n=== 2) chat_template_kwargs.enable_thinking=False 双向验证 ===")
    # 2026-10-27 实测：单次探测看似"完全无效"（rtok 50 vs 50），
    # 但 2 次采样显示是「部分有效、降幅极小」：裸请求 rtok 36/41 → 关思考 rtok 34/31（降约 13%）。
    # 结论：与 30828 端点的「完全关闭」(rtok 123→0) 完全不同，此端点无法真正关闭思考。
    for _ in range(2):
        non_stream("关思考", {"chat_template_kwargs": {"enable_thinking": False}})

    print("\n=== 3) reasoning_effort=low 是否被识别 ===")
    for _ in range(2):
        non_stream("effort=low", {"reasoning_effort": "low"})

    print("\n=== 4) 短 model_id 是否等价 ===")
    non_stream("短名", {}, model=SHORT, prompt="1+1=?")

    print("\n=== 5) 流式 TTFT（两口径）===")
    stream_probe("流式·裸请求", {})
    stream_probe("流式·关思考", {"chat_template_kwargs": {"enable_thinking": False}})

    print("\n=== 6) 工具调用能力 ===")
    try:
        r = S.post(f"{BASE}/chat/completions", timeout=180, json={
            "model": MODEL,
            "messages": [{"role": "user", "content": "北京今天天气怎么样？用工具查。"}],
            "tools": [{"type": "function", "function": {
                "name": "get_weather", "description": "查询城市天气",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                               "required": ["city"]}}}],
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

    print("\n=== 7) max_tokens 截断行为（思考吃预算）===")
    # 实测：mt=200/1000 → rtok 超过 max_tokens 本身 → content 为空（思考吃光预算）
    #      mt=6000 → 正常输出 50 字。生产必须 max_tokens >= 6000。
    for mt in (200, 1000, 6000):
        t0 = time.time()
        r = S.post(f"{BASE}/chat/completions", timeout=180, json={
            "model": MODEL,
            "messages": [{"role": "user", "content": "请用恰好50个字（含标点）描述机器学习的概念。"}],
            "max_tokens": mt})
        if r.status_code != 200:
            print(f"  max_tokens={mt}: HTTP {r.status_code}")
            continue
        d = r.json()
        m = d["choices"][0]["message"]
        u = d.get("usage", {}) or {}
        print(f"  max_tokens={mt:5d}: {time.time()-t0:5.1f}s completion={u.get('completion_tokens')} "
              f"reasoning_tokens={u.get('reasoning_tokens')} content_len={len(m.get('content') or '')} "
              f"content={ (m.get('content') or '')[:50]!r}")