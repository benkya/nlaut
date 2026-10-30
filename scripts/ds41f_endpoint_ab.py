#!/usr/bin/env python3
"""DeepSeek-V4.1-Flash 同模型跨端点对比（vllm 30828 vs sglang 30089 INT8）

同模型名，不同部署：
  30828: vllm-0.31.0-tp8，未标注量化，思考字段 reasoning
  30089: sglang，INT8 量化，思考字段 reasoning_content

对比维度：
  A. 思考控制参数有效性（enable_thinking / reasoning_effort）
  B. TTFT / 吞吐（流式，5 采样，P95）
  C. 思考预算截断行为（max_tokens 阶梯）
  D. 端点稳定性（响应时间分布）

各端点参数语义不同，必须各自独立探测，不共用配置。
"""
from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path

import requests

PROMPT = "用一句话介绍 Python。"
SAMPLES = 5

ENDPOINTS = [
    {
        "name": "vllm@30828 (未标注量化)",
        "base": "http://10.102.78.254:30828/api/inference/v1",
        "model": "DeepSeek-V4.1-Flash",
        "key": os.environ.get("DEEPSEEK_V4_1_FLASH_INTRA_KEY", ""),
        "think_fields": ["reasoning", "reasoning_content"],
    },
    {
        "name": "sglang@30089 (INT8)",
        "base": "http://10.62.64.38:30089/v1",
        "model": "/root/models/DeepSeek-V4.1-Flash-1017-INT8",
        "key": os.environ.get("DS41F_SGLANG_KEY", ""),
        "think_fields": ["reasoning_content", "reasoning"],
    },
]


def sess(ep):
    S = requests.Session()
    h = {"Content-Type": "application/json"}
    if ep["key"]:
        h["Authorization"] = f"Bearer {ep['key']}"
    S.headers.update(h)
    S.trust_env = False          # 内网端点不走环境代理
    return S


def non_stream(ep, extra, mt=3000, prompt=PROMPT):
    S = sess(ep)
    t0 = time.time()
    r = S.post(f"{ep['base']}/chat/completions", timeout=900,
               json={"model": ep["model"], "messages": [{"role": "user", "content": prompt}],
                     "max_tokens": mt, **extra})
    dt = time.time() - t0
    if r.status_code != 200:
        return {"ok": False, "err": f"HTTP {r.status_code}: {r.text[:150]}"}
    d = r.json()
    m = d["choices"][0]["message"]
    u = d.get("usage", {}) or {}
    dtl = u.get("completion_tokens_details") or {}
    rtok = dtl.get("reasoning_tokens")
    if rtok is None:
        rtok = u.get("reasoning_tokens", 0)
    return {"ok": True, "latency_ms": dt * 1000, "completion": u.get("completion_tokens", 0),
            "rtok": rtok, "content": (m.get("content") or ""),
            "think_len": sum(len(m.get(k) or "") for k in ep["think_fields"])}


def stream(ep, extra, mt=2000):
    S = sess(ep)
    t0 = time.time()
    ttft = None
    content = ""
    keys = {}
    p = {"model": ep["model"], "messages": [{"role": "user", "content": PROMPT}],
         "max_tokens": mt, "stream": True, **extra}
    with S.post(f"{ep['base']}/chat/completions", json=p, stream=True, timeout=900) as r:
        if r.status_code != 200:
            return {"ok": False, "err": f"HTTP {r.status_code}"}
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
            rs = None
            for k in ep["think_fields"]:
                if d.get(k):
                    rs = d.get(k)
                    break
            if ttft is None and (d.get("content") or rs):
                ttft = (time.time() - t0) * 1000
            if d.get("content"):
                content += d["content"]
    return {"ok": True, "ttft_ms": ttft, "total_ms": (time.time() - t0) * 1000,
            "content_len": len(content), "delta_keys": keys, "head": content[:60]}


def p95(v):
    s = sorted(v)
    if len(s) == 1:
        return s[0]
    return s[min(len(s) - 1, max(0, round(0.95 * (len(s) - 1))))]


def main():
    print("=" * 92)
    print("DeepSeek-V4.1-Flash 同模型跨端点对比：vllm@30828 vs sglang@30089(INT8)")
    print(f"prompt={PROMPT!r}  samples={SAMPLES}")
    print("=" * 92)

    out = {}
    for ep in ENDPOINTS:
        nm = ep["name"]
        print(f"\n{'='*92}\n【{nm}】  model={ep['model']}\n{'='*92}")
        out[nm] = {}

        # A. 思考控制参数有效性
        print("\n-- A. 思考控制参数（每口径 2 采样，看 reasoning_tokens）--")
        for tag, extra in [("裸请求", {}),
                           ("enable_thinking=False", {"chat_template_kwargs": {"enable_thinking": False}}),
                           ("reasoning_effort=low", {"reasoning_effort": "low"})]:
            rts, cts, lats = [], [], []
            for _ in range(2):
                r = non_stream(ep, extra)
                if not r["ok"]:
                    print(f"  {tag:26s} ERR {r['err']}")
                    break
                rts.append(r["rtok"])
                cts.append(r["completion"])
                lats.append(round(r["latency_ms"]))
            else:
                print(f"  {tag:26s} rtok={rts}  completion={cts}  延迟={lats}ms")
                out[nm].setdefault("think_ctrl", {})[tag] = {
                    "rtok": rts, "completion": cts, "latency_ms": lats}

        # B. 流式 TTFT
        print("\n-- B. 流式 TTFT（5 采样）--")
        for tag, extra in [("裸请求", {}),
                           ("enable_thinking=False", {"chat_template_kwargs": {"enable_thinking": False}})]:
            tts, tots, head, dkeys = [], [], None, None
            for _ in range(SAMPLES):
                r = stream(ep, extra)
                if not r["ok"]:
                    print(f"  {tag:26s} ERR {r['err']}")
                    break
                if r["ttft_ms"]:
                    tts.append(r["ttft_ms"])
                tots.append(r["total_ms"])
                head = r["head"]
                dkeys = r["delta_keys"]
            else:
                p = p95(tts) if tts else 0
                print(f"  {tag:26s} TTFT中位={statistics.median(tts):7.0f}ms  P95={p:7.0f}ms  "
                      f"总耗时中位={statistics.median(tots):7.0f}ms  样本={[round(x) for x in tts]}")
                print(f"  {'':26s} delta字段={dkeys}")
                print(f"  {'':26s} 开头={head!r}")
                out[nm].setdefault("stream", {})[tag] = {
                    "ttft_samples_ms": [round(x) for x in tts],
                    "ttft_median_ms": round(statistics.median(tts)),
                    "ttft_p95_ms": round(p),
                    "total_median_ms": round(statistics.median(tots)),
                    "delta_keys": dkeys, "head": head}

        # C. max_tokens 截断
        print("\n-- C. max_tokens 截断（思考吃预算）--")
        rows = []
        for mt in (1000, 6000):
            r = non_stream(ep, {}, mt=mt,
                           prompt="请用恰好50个字（含标点）描述机器学习的概念。")
            if r["ok"]:
                flag = "❌ 空返回" if not r["content"] else "✅"
                print(f"  mt={mt:5d}: {r['latency_ms']/1000:6.1f}s completion={r['completion']:5d} "
                      f"rtok={r['rtok']:5d} content_len={len(r['content']):3d} {flag}")
                rows.append({"max_tokens": mt, "completion": r["completion"], "rtok": r["rtok"],
                             "content_len": len(r["content"]),
                             "latency_s": round(r["latency_ms"] / 1000, 1)})
        out[nm]["max_tokens"] = rows
        time.sleep(2)

    dst = Path("/Users/Ben/workspace/nlaut/artifacts/ds41f_endpoint_ab.json")
    dst.write_text(json.dumps({"prompt": PROMPT, "samples": SAMPLES,
                               "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "data": out},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细已存: {dst}")

    print("\n" + "=" * 92)
    print("汇总")
    print("=" * 92)
    print(f"{'端点':30s} {'思考可控':10s} {'TTFT中位':>10s} {'P0类延迟':>10s}")
    print("-" * 92)
    for nm, v in out.items():
        tc = v.get("think_ctrl", {})
        base_rt = tc.get("裸请求", {}).get("rtok", [0])
        off_rt = tc.get("enable_thinking=False", {}).get("rtok", [0])
        ctrl = "可关" if (base_rt and off_rt and min(off_rt) < min(base_rt) * 0.3) else (
            "部分有效" if (base_rt and off_rt and min(off_rt) < min(base_rt)) else "无效")
        st = v.get("stream", {})
        ttft = st.get("enable_thinking=False", {}).get("ttft_median_ms", 0)
        lat = tc.get("裸请求", {}).get("latency_ms", [0])
        print(f"{nm:30s} {ctrl:10s} {ttft:8.0f}ms {lat[0]/1000:8.1f}s")


if __name__ == "__main__":
    main()