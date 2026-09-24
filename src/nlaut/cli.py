#!/usr/bin/env python
"""nlaut 一键入口：跑 IR 库 → 三级判定 → HTML 报告。

v0.2.0 扩展：新增 --model / --api-key / --api-base 参数，支持 API 通道用例。

用法:
    # Web UI 测试（原有）
    .venv/bin/python -m nlaut.cli                    # 跑全部（演示系统自动拉起）
    .venv/bin/python -m nlaut.cli --no-vlm           # 跳过 VLM（快）
    .venv/bin/python -m nlaut.cli --case tc_login_001
    .venv/bin/python -m nlaut.cli --external --base-url http://localhost:3000

    # 大模型 API 测试（v0.2.0 新增）
    .venv/bin/python -m nlaut.cli --model deepseek-chat \\
        --api-base https://api.deepseek.com/v1 \\
        --api-key sk-xxx \\
        --case tc_d04_p0_001

    # 使用 config/models.yaml 中的预设模型
    .venv/bin/python -m nlaut.cli --model deepseek-chat \\
        --api-key sk-xxx \\
        --case tc_d07_p0_001

    # 只跑 API 通道用例（跳过 web 演示）
    .venv/bin/python -m nlaut.cli --model qwen-max \\
        --api-base https://dashscope.aliyuncs.com/compatible-mode/v1 \\
        --api-key sk-xxx --api-only
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

MODELS_CONFIG = ROOT / "config" / "models.yaml"


def _load_model_config(model_name: str) -> dict | None:
    """从 config/models.yaml 加载预设模型配置。"""
    if not MODELS_CONFIG.exists():
        return None
    cfg = yaml.safe_load(MODELS_CONFIG.read_text(encoding="utf-8")) or {}
    models = cfg.get("models", {})
    return models.get(model_name)


def main() -> int:
    parser = argparse.ArgumentParser(description="nlaut 自然语言自动化测试")
    parser.add_argument("--case", action="append", help="指定用例 id（可多次），默认全部")
    parser.add_argument("--base-url", default="", help="Web 被测系统基址（nav 相对路径时拼接）")
    parser.add_argument("--no-vlm", action="store_true", help="跳过 VLM 视觉判定（提速）")
    parser.add_argument("--headed", action="store_true", help="有头模式运行浏览器")
    parser.add_argument("--report", default="artifacts/report.html", help="报告输出路径")
    parser.add_argument(
        "--external", action="store_true",
        help="打真实被测系统（须同时给 --base-url，禁自动拉起演示系统）",
    )
    # --- v0.2.0 API 通道参数 ---
    parser.add_argument("--model", default="", help="大模型 ID（如 deepseek-chat / qwen-max）")
    parser.add_argument("--api-key", default="", help="API Key（或设环境变量 LLM_API_KEY）")
    parser.add_argument("--api-base", default="", help="API 基址（或从 config/models.yaml 读取）")
    parser.add_argument("--api-only", action="store_true",
                        help="只跑 API 通道用例（跳过 web 演示系统）")
    parser.add_argument("--priority", default="",
                        help="优先级过滤（如 P0 只跑 P0；空 = 全部优先级）")
    parser.add_argument("--timeout", type=float, default=60.0, help="API 调用超时（秒）")
    args = parser.parse_args()

    from nlaut.executor.runner import run_store
    from nlaut.report.render import render_html

    # 构建 API 配置
    api_config = None
    if args.model:
        api_key = args.api_key or os.environ.get("LLM_API_KEY", "")
        api_base = args.api_base

        # 从 models.yaml 补全缺失项
        model_cfg = _load_model_config(args.model)
        if model_cfg:
            if not api_base:
                api_base = model_cfg.get("base_url", "")
            if not api_key:
                api_key = os.environ.get(model_cfg.get("api_key_env", ""), "")
            # 用 model_cfg 中的 model_id 覆盖
            args.model = model_cfg.get("model_id", args.model)

        if not api_key:
            parser.error(f"模型 {args.model} 缺少 API Key（--api-key 或环境变量 LLM_API_KEY）")

        api_config = {
            "base_url": api_base,
            "api_key": api_key,
            "model": args.model,
            "timeout": args.timeout,
        }
        print(f"[cli] API 通道: model={args.model} base_url={api_base}")

    # Web 演示系统拉起逻辑：仅当非 api-only 且无 base-url 且无 external 时
    base_url = args.base_url
    if not base_url and not args.external and not args.api_only:
        sys.path.insert(0, str(ROOT / "tests"))
        try:
            from demo_app import start as start_demo
            base_url, _ = start_demo()
        except ImportError:
            pass  # demo_app 不存在时跳过（API-only 场景）
    if args.external and not base_url:
        parser.error("--external 必须与 --base-url 同用")

    results = run_store(
        case_ids=args.case,
        channels=["api"] if args.api_only else None,
        priorities=[p.strip() for p in args.priority.split(",") if p.strip()] or None,
        base_url=base_url,
        use_vlm=not args.no_vlm,
        headless=not args.headed,
        session_log_path="artifacts/session/run.jsonl",
        api_config=api_config,
    )

    for r in results:
        print(f"[{r['status']:>11}] {r['case_id']}  {r['title']}  ({r['duration_s']}s)")

    report = render_html(results, out_path=args.report)
    passed = sum(r["status"] == "passed" for r in results)
    print(f"\n通过 {passed}/{len(results)} · 报告: {report}")
    failed = any(r["status"] in ("failed", "error") for r in results)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
