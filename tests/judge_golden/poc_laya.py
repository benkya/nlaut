"""M0-poc: laya-mlx System One 判定实测。

对三个登录场景的文本状态描述问 noul 题，验证:
1. 中文 checkpoint 是否可用（多语言版）
2. confidence 语义是否符合路由规则（≥0.9 自动/0.5-0.9 人工）
3. 延迟是否在承诺量级（官方 13.4ms 中位）

运行: uv run python tests/judge_golden/poc_laya.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

LAYA_PATH_HINT = "aac6fef/laya-multilingual-mlx"  # 经代理从 HF 下载到本地缓存

# (场景状态文本, 问题, 期望 noul)
CASES = [
    (
        "登录页执行结果: 表单提交后, .error-msg 元素显示 '用户名或密码错误', 页面仍停留在 /login, 未发生跳转。",
        "该状态是否表明登录被拒绝（登录失败）？",
        1.0,
    ),
    (
        "登录页执行结果: 提交后跳转到 /workspace, 顶部显示 '欢迎回来', 无任何错误提示。",
        "该状态是否表明登录被拒绝（登录失败）？",
        0.0,
    ),
    (
        "登录页执行结果: 页面显示 '网络异常, 连接失败 ERR_CONNECTION_TIMED_OUT', 无法提交表单。",
        "该状态是否表明发生了网络/环境类故障（而非产品功能缺陷）？",
        1.0,
    ),
    (
        "登录页执行结果: 提交后页面空白, 控制台报 500 错误, 服务器返回 Internal Server Error。",
        "该状态是否表明发生了网络/环境类故障（而非产品功能缺陷）？",
        0.0,
    ),
]


def main() -> None:
    import os

    if "http_proxy" not in os.environ:  # HF 下载需代理（若缓存已存在则直接本地命中）
        os.environ["https_proxy"] = "http://127.0.0.1:7897"
        os.environ["http_proxy"] = "http://127.0.0.1:7897"

    import laya_mlx

    t0 = time.time()
    agent = laya_mlx.load(LAYA_PATH_HINT)
    print(f"[load] Laya 加载 {time.time()-t0:.1f}s（含权重下载/入显存）")

    results = []
    for state, question, expected in CASES:
        q = {"q1": {"type": "noul", "instructions": question}}
        t1 = time.time()
        resp = agent.system_one(state, q)
        dt = (time.time() - t1) * 1000
        ans = resp["answers"]["q1"]
        p = float(ans["noul"])
        conf = float(ans["confidence"])
        ok = (p >= 0.5) == (expected >= 0.5)
        route = "auto" if conf >= 0.9 else ("human" if conf >= 0.5 else "human(不确定)")
        results.append(
            {
                "state": state[:30],
                "question": question[:30],
                "expected": expected,
                "noul": p,
                "confidence": conf,
                "pass": ok,
                "route": route,
                "latency_ms": round(dt, 1),
            }
        )
        print(
            f"{'PASS' if ok else 'FAIL'} | noul={p:.3f} conf={conf:.3f} | {route:12s} | "
            f"{dt:7.1f}ms | {state[:36]}"
        )

    acc = sum(r["pass"] for r in results) / len(results)
    lat = [r["latency_ms"] for r in results]
    print(f"\n[summary] 准确率 {acc:.0%} | 延迟 min/med/max = "
          f"{min(lat):.1f}/{sorted(lat)[len(lat)//2]:.1f}/{max(lat):.1f}ms")
    out = Path(__file__).parent / "poc_laya_result.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[artifact] {out}")


if __name__ == "__main__":
    main()
