"""M0-poc: mlx-vlm 视觉判定实测。

三张金标准截图（登录成功/密码错误/网络异常），对每张问一个 noul 型问题，
统计判定准确性、延迟、峰值内存。采样温度 0（框架硬规则：禁止随机判定）。

运行: uv run python tests/judge_golden/poc_vlm.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

GOLDEN_DIR = Path(__file__).parent / "images"
MODEL_PATH = Path.home() / ".cache/nlaut-models/Qwen2.5-VL-3B-Instruct-4bit"

# 金标准: (图片, noul 问题, 期望值 True/False)
CASES = [
    (
        "login_success.png",
        "页面是否呈现登录成功状态（出现欢迎/工作台元素，且没有错误提示）？只答 yes 或 no。",
        True,
    ),
    (
        "login_wrong_pwd.png",
        "页面是否呈现登录失败状态（出现用户名或密码错误提示）？只答 yes 或 no。",
        True,
    ),
    (
        "login_network_error.png",
        "页面是否呈现网络异常/连接失败状态？只答 yes 或 no。",
        True,
    ),
    (
        "login_success.png",
        "页面上是否出现了错误提示（如密码错误、网络异常字样）？只答 yes 或 no。",
        False,
    ),
    (
        "login_wrong_pwd.png",
        "页面是否呈现登录成功状态（出现欢迎/工作台元素）？只答 yes 或 no。",
        False,
    ),
    (
        "login_network_error.png",
        "页面是否呈现登录成功状态（出现欢迎/工作台元素）？只答 yes 或 no。",
        False,
    ),
]


def yes_no(output) -> bool:
    """从 GenerationResult 或 str 中提取 yes/no。模型输出对象不是字符串——首版 bug。"""
    text = getattr(output, "text", None) or str(output)
    t = text.strip().lower()
    return t.startswith(("yes", "true")) or t.split()[:1] == ["yes"]


def main() -> None:
    from mlx_vlm import generate, load
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import load_config, load_image

    t0 = time.time()
    model, processor = load(str(MODEL_PATH))
    config = load_config(str(MODEL_PATH))
    print(f"[load] 模型加载 {time.time()-t0:.1f}s（首次含权重入显存）")

    results = []
    for img, question, expected in CASES:
        image = load_image(str(GOLDEN_DIR / img))
        t1 = time.time()
        prompt = apply_chat_template(processor, config, prompt=question, num_images=1)
        t2 = time.time()
        output = generate(model, processor, prompt, image, max_tokens=8, temperature=0.0)
        t3 = time.time()
        answer = yes_no(output)  # 传对象本身，text 属性在函数内提取
        ok = answer == expected
        results.append(
            {
                "image": img,
                "question": question[:40],
                "raw": str(output)[:60],
                "expected": expected,
                "got": answer,
                "pass": ok,
                "latency_s": round(t3 - t2, 2),
                "template_s": round(t2 - t1, 2),
            }
        )
        print(
            f"{'PASS' if ok else 'FAIL'} | {img:26s} | {str(output)[:24]:24s} | "
            f"模板 {t2-t1:.2f}s 推理 {t3-t2:.2f}s"
        )

    acc = sum(r["pass"] for r in results) / len(results)
    lat = [r["latency_s"] for r in results]
    print(f"\n[summary] 准确率 {acc:.0%}（{sum(r['pass'] for r in results)}/{len(results)}）"
          f" | 推理延迟 min/med/max = {min(lat):.2f}/{sorted(lat)[len(lat)//2]:.2f}/{max(lat):.2f}s")
    out = Path(__file__).parent / "poc_vlm_result.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[artifact] {out}")


if __name__ == "__main__":
    main()
