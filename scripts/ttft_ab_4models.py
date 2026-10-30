#!/usr/bin/env python3
"""Qwen3.8-27B 三部署 + DeepSeek-V4.1-Flash 同口径 TTFT 实测

旧报告的 P0 数据混了开/关思考口径，TTFT 不可比。
本脚本用完全相同的 prompt + 完全相同的采样方式重测，
每个模型 5 次采样，报 P95（与 nlaut response_ttft_p95 判定口径一致）。
"""
from __future__ import annotations

import json
import os
import statistics
import time

import requests

PROMPT = "用一句话介绍 Python。"          # 与 tc_stream_p95_p0_001 完全相同
SAMPLES = 5

# 注意 2026-10-23 实测：47.239.178.97:30021 端点当前只提供 qwen3.8-flash-next
#（root=/root/Swift-1.5-Qwen3.8-Flash-Next-NVFP4, max_model_len=524288），
# 27B 的 fp8 / nvfp4 两个部署已下线（HTTP 404 The model does not exist），
# 只能用历史报告数据对比，无法补测 TTFT。
MODELS = [
    ("qwen3.8-flash-next@30021", "http://47.239.178.97:30021/v1", "qwen3.8-flash-next", False),
    ("qwen3.8-27b-w8a8@8866", "http://10.62.48.94:8866/v1", "Qwen3.8-27B", False),
    ("DeepSeek-V4.1-Flash@30828", "http://10.102.78.254:30828/api/inference/v1",
     "DeepSeek-V4.1-Flash", os.environ.get("DEEPSEEK_V4_1_FLASH_INTRA_KEY", "")),
]


def sample(base: str, model: str, key: str | None, think: str) -> dict:
    """think: 'default' | 'off'"""
    S = requests.Session()
    h = {"Content-Type": "application/json"}
    if key:
        h["Authorization"] = f"Bearer {key}"
    S.headers.update(h)

    payload = {"model": model,
               "messages": [{"role": "user", "content": PROMPT}],
               "max_tokens": 2000, "stream": True}
    if think == "off":
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    t0 = time.time()
    ttft = None
    content = ""
    keys: dict = {}
    try:
        with S.post(f"{base}/chat/completions", json=payload, stream=True, timeout=300) as r:
            if r.status_code != 200:
                return {"ok": False, "err": f"HTTP {r.status_code}: {r.text[:120]}"}
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
                # TTFT = 首个含 content 或 reasoning 的 chunk（与 nlaut 判定一致）
                if ttft is None and (d.get("content") or rs):
                    ttft = (time.time() - t0) * 1000
                if d.get("content"):
                    content += d["content"]
        return {"ok": True, "ttft_ms": ttft, "total_ms": (time.time() - t0) * 1000,
                "content_len": len(content), "delta_keys": keys,
                "leak": content[:40].replace("\n", " ")}
    except Exception as e:  # noqa: BLE001 — 探针/守卫脚本需捕获任意异常并记录
        return {"ok": False, "err": f"{type(e).__name__}: {e}"}


def p95(vals):
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    return s[min(len(s) - 1, max(0, round(0.95 * (len(s) - 1))))]


def main():
    print("=" * 86)
    print("同口径 TTFT 实测（prompt 与采样数完全一致，P0 用例 tc_stream_p95_p0_001 原prompt）")
    print(f"prompt={PROMPT!r}  samples={SAMPLES}")
    print("=" * 86)

    out = {}
    for name, base, model, key in MODELS:
        print(f"\n【{name}】  model_id={model}")
        for think in ("default", "off"):
            tts, tot, leaks = [], [], None
            for _ in range(SAMPLES):
                r = sample(base, model, key, think)
                if not r["ok"]:
                    print(f"  think={think:8s} ERR {r['err']}")
                    break
                if r["ttft_ms"]:
                    tts.append(r["ttft_ms"])
                tot.append(r["total_ms"])
                leaks = r["leak"]
            else:
                tag = "开思考(默认)" if think == "default" else "关思考    "
                p = p95(tts) if tts else 0
                verdict = "PASS" if p and p <= 3000 else "FAIL"
                print(f"  {tag} TTFT中位={statistics.median(tts):7.0f}ms  P95={p:7.0f}ms  "
                      f"[{verdict}]  总耗时中位={statistics.median(tot):7.0f}ms")
                print(f"                开头: {leaks!r}")
                out.setdefault(name, {})[think] = {
                    "ttft_samples_ms": [round(x) for x in tts],
                    "ttft_median_ms": round(statistics.median(tts)),
                    "ttft_p95_ms": round(p),
                    "total_median_ms": round(statistics.median(tot)),
                    "verdict": verdict,
                    "content_head": leaks,
                }
        time.sleep(2)

    dst = "artifacts/ttft_ab_4models.json"
    with open(dst, "w", encoding="utf-8") as f:
        json.dump({"prompt": PROMPT, "samples": SAMPLES, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "data": out}, f, ensure_ascii=False, indent=2)
    print(f"\n明细已存: {dst}")

    print("\n" + "=" * 86)
    print("汇总（TTFT P95，阈值 3000ms）")
    print("=" * 86)
    print(f"{'模型':26s} {'开思考P95':>12s} {'关思考P95':>12s} {'最快口径':>10s}")
    print("-" * 86)
    for name, v in out.items():
        on = v.get("default", {}).get("ttft_p95_ms")
        off = v.get("off", {}).get("ttft_p95_ms")
        best = min([x for x in (on, off) if x], default=0)
        print(f"{name:26s} {str(on)+'ms' if on else '-':>12s} {str(off)+'ms' if off else '-':>12s} "
              f"{best}ms{'':>4s}")


if __name__ == "__main__":
    main()