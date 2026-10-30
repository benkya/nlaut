#!/usr/bin/env python3
"""DeepSeek-V4.1-Flash 同窗口双口径对照实验（消除服务端负载变量）

设计要点：
1. 交替执行（A/B/A/B...），不是先跑完 A 再跑 B —— 服务端负载漂移对两边影响一致
2. 同一 prompt 严格对照（同一进程内同一 messages 对象）
3. 每个口径跑 N 轮，输出均值/中位数/P95/极值，供统计判断
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

import requests

BASE = os.environ.get("DS41F_BASE", "http://10.102.78.254:30828/api/inference/v1")
# key 只从环境变量读，绝不写进仓库（与其他模型的 api_key_env 机制一致）
KEY = os.environ.get("DEEPSEEK_V4_1_FLASH_INTRA_KEY", "")
MODEL = os.environ.get("DS41F_MODEL", "DeepSeek-V4.1-Flash")
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 3

if not KEY:
    sys.exit("缺少环境变量 DEEPSEEK_V4_1_FLASH_INTRA_KEY\n"
             "先执行: set -a; source ~/.hermes/.env; set +a")

S = requests.Session()
S.headers.update({"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})

CASES = [
    {
        "id": "len_constraint",
        "arg": "字数约束（tc_instruct_p0_004：要恰好50字，区间40-60）",
        "messages": [{"role": "user", "content":
                      "请用恰好50个字（含标点，不要多也不要少）描述机器学习的概念。只输出这50个字，不要任何解释。"}],
        "max_tokens": 2000, "stream": False, "check": "len", "lo": 40, "hi": 60,
    },
    {
        "id": "ttft",
        "arg": "TTFT（tc_stream_p95_p0_001：用一句话介绍 Python）",
        "messages": [{"role": "user", "content": "用一句话介绍 Python。"}],
        "max_tokens": 2000, "stream": True, "check": "ttft",
    },
    {
        "id": "long_summarize",
        "arg": "长文摘要（tc_nlu_p0_003：3要点每点≤20字）",
        "messages": [{"role": "user", "content":
                      "请用3个要点总结以下文章，每个要点不超过20字：\n\n远程办公正在改变企业的协作方式。"
                      "调查显示，超过六成的科技公司已允许员工每周至少两天居家办公。管理者发现，灵活的工作安排"
                      "显著提升了员工满意度，但也带来了沟通效率下降的挑战。为此，许多团队引入了异步协作工具，"
                      "用文档和录屏替代即时会议。同时，公司开始重新审视办公室的角色，将其改造为团队协作与创意"
                      "碰撞的场所，而非日常工位。未来五年，混合办公模式预计将成为科技行业的主流形态。"}],
        "max_tokens": 3000, "stream": False, "check": "latency",
    },
    {
        "id": "code_gen",
        "arg": "代码生成（tc_code_p0_002：SQL查询）",
        "messages": [{"role": "user", "content":
                      "写一个 SQL 查询：找出每个部门薪资总额前3的员工，"
                      "表结构 employees(id, name, dept_id, salary)，"
                      "departments(id, name)。只输出 SQL，不要解释。"}],
        "max_tokens": 3000, "stream": False, "check": "latency",
    },
]

MODES = {
    "think_on": {},
    "think_off": {"chat_template_kwargs": {"enable_thinking": False}},
}


def call(mode_extra: dict, case: dict) -> dict:
    t0 = time.time()
    out = {"latency_ms": 0.0, "ttft_ms": None, "content": "",
           "completion_tokens": 0, "reasoning_tokens": 0, "ok": False, "err": ""}
    payload = {"model": MODEL, "messages": case["messages"],
               "max_tokens": case["max_tokens"], **mode_extra}

    if case["stream"]:
        try:
            with S.post(f"{BASE}/chat/completions", json=payload, stream=True, timeout=300) as r:
                if r.status_code != 200:
                    out["err"] = f"HTTP {r.status_code}: {r.text[:200]}"
                    return out
                content = ""
                for line in r.iter_lines():
                    if not line:
                        continue
                    line = line.decode("utf-8", errors="ignore")
                    if not line.startswith("data: "):
                        continue
                    p = line[6:]
                    if p.strip() == "[DONE]":
                        break
                    try:
                        c = json.loads(p)
                    except json.JSONDecodeError:
                        continue
                    ch = c.get("choices") or []
                    if not ch:
                        continue
                    d = ch[0].get("delta", {}) or {}
                    reason = d.get("reasoning_content") or d.get("reasoning")
                    if out["ttft_ms"] is None and (d.get("content") or reason):
                        out["ttft_ms"] = (time.time() - t0) * 1000
                    if d.get("content"):
                        content += d["content"]
                    u = c.get("usage") or {}
                    if u:
                        out["completion_tokens"] = u.get("completion_tokens", 0)
                        dtl = u.get("completion_tokens_details") or {}
                        out["reasoning_tokens"] = dtl.get("reasoning_tokens", 0)
                out["content"] = content
                out["ok"] = True
                out["latency_ms"] = (time.time() - t0) * 1000
        except Exception as e:  # noqa: BLE001 — 探针/守卫脚本需捕获任意异常并记录
            out["err"] = f"{type(e).__name__}: {e}"
        return out

    try:
        r = S.post(f"{BASE}/chat/completions", json=payload, timeout=300)
        if r.status_code != 200:
            out["err"] = f"HTTP {r.status_code}: {r.text[:200]}"
            return out
        d = r.json()
        m = d["choices"][0]["message"]
        u = d.get("usage", {}) or {}
        dtl = u.get("completion_tokens_details") or {}
        out["content"] = m.get("content") or ""
        out["completion_tokens"] = u.get("completion_tokens", 0)
        out["reasoning_tokens"] = dtl.get("reasoning_tokens", 0)
        out["ok"] = True
        out["latency_ms"] = (time.time() - t0) * 1000
    except Exception as e:  # noqa: BLE001 — 探针/守卫脚本需捕获任意异常并记录
        out["err"] = f"{type(e).__name__}: {e}"
    return out


def pct(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    return s[min(len(s) - 1, max(0, round(q * (len(s) - 1))))]


def main() -> None:
    print("=" * 78)
    print(f"DeepSeek-V4.1-Flash 同窗口双口径对照  rounds={ROUNDS}  交替执行 A/B")
    print("=" * 78)

    results: dict = {}
    raw_log: list = []
    t_start = time.time()

    for rnd in range(1, ROUNDS + 1):
        for case in CASES:
            for mode_name, extra in MODES.items():
                r = call(extra, case)
                b = results.setdefault(case["id"], {}).setdefault(mode_name, {
                    "latency_ms": [], "ttft_ms": [], "content_len": [],
                    "completion_tokens": [], "reasoning_tokens": [], "pass": [],
                })
                if r["ok"]:
                    b["latency_ms"].append(r["latency_ms"])
                    if r["ttft_ms"] is not None:
                        b["ttft_ms"].append(r["ttft_ms"])
                    b["content_len"].append(len(r["content"]))
                    b["completion_tokens"].append(r["completion_tokens"])
                    b["reasoning_tokens"].append(r["reasoning_tokens"])
                    if case["id"] == "len_constraint":
                        n = len(r["content"])
                        b["pass"].append(1 if case["lo"] <= n <= case["hi"] else 0)
                raw_log.append({"round": rnd, "case": case["id"], "mode": mode_name,
                                "wallclock": round(time.time() - t_start, 1), **r,
                                "content_preview": r["content"][:60]})
                metric = r["ttft_ms"] if case["check"] == "ttft" else r["latency_ms"]
                flag = "OK " if r["ok"] else "ERR"
                mt = f"{metric:8.0f}ms" if metric else "   n/a  "
                ex = ""
                if case["id"] == "len_constraint" and r["ok"]:
                    n = len(r["content"])
                    ex = f" len={n:3d}{'✓' if case['lo'] <= n <= case['hi'] else '✗'}"
                print(f"  r{rnd} {case['id']:16s} {mode_name:10s} {flag} {mt}"
                      f"  rtok={r['reasoning_tokens']:5d}{ex}")

    print("\n" + "=" * 78)
    print("汇总（同窗口 N 轮交替执行）")
    print("=" * 78)
    summary: dict = {}
    for case in CASES:
        cid = case["id"]
        print(f"\n【{cid}】{case['arg']}")
        summary[cid] = {"arg": case["arg"], "modes": {}}
        row = {}
        for mode_name in MODES:
            b = results[cid][mode_name]
            key = "ttft_ms" if case["check"] == "ttft" else "latency_ms"
            vals = b[key] or b["latency_ms"]
            st = {
                "n": len(vals), "primary_metric": key,
                "mean_ms": round(statistics.mean(vals), 1) if vals else 0,
                "median_ms": round(statistics.median(vals), 1) if vals else 0,
                "p95_ms": round(pct(vals, 0.95), 1),
                "min_ms": round(min(vals), 1) if vals else 0,
                "max_ms": round(max(vals), 1) if vals else 0,
                "reasoning_tokens_mean": round(statistics.mean(b["reasoning_tokens"]), 1) if b["reasoning_tokens"] else 0,
                "completion_tokens_mean": round(statistics.mean(b["completion_tokens"]), 1) if b["completion_tokens"] else 0,
                "content_len_samples": b["content_len"],
            }
            if b["pass"]:
                st["pass_count"] = sum(b["pass"])
                st["pass_total"] = len(b["pass"])
            row[mode_name] = st
            m = f"{st['median_ms']:.0f}ms(med) {st['p95_ms']:.0f}ms(p95)" if vals else "n/a"
            pc = f"  pass={st['pass_count']}/{st['pass_total']}" if "pass_count" in st else ""
            print(f"   {mode_name:10s} n={st['n']}  {m:26s} rtok={st['reasoning_tokens_mean']:.0f}{pc}")
        on, off = row["think_on"], row["think_off"]
        if on["median_ms"] and off["median_ms"]:
            sp = on["median_ms"] / off["median_ms"]
            print(f"   → 提速 {sp:.1f}×（中位数口径）")
            row["speedup_median"] = round(sp, 2)
        summary[cid]["modes"] = row

    out = Path("/Users/Ben/workspace/nlaut/artifacts/ds41f_ab_control.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"rounds": ROUNDS, "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                               "summary": summary, "raw": raw_log},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细已存: {out}")


if __name__ == "__main__":
    main()