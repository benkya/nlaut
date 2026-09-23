#!/usr/bin/env python
"""nlaut MVP 一键入口：跑 IR 库 → 三级判定 → HTML 报告。

用法:
    .venv/bin/python -m nlaut.cli                    # 跑全部用例（含 VLM/Laya 真实推理）
    .venv/bin/python -m nlaut.cli --no-vlm           # 跳过 VLM（快，只确定性+Laya）
    .venv/bin/python -m nlaut.cli --case tc_login_001
    .venv/bin/python -m nlaut.cli --base-url http://localhost:3000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    parser = argparse.ArgumentParser(description="nlaut 自然语言自动化测试 MVP")
    parser.add_argument("--case", action="append", help="指定用例 id（可多次），默认全部")
    parser.add_argument("--base-url", default="", help="被测系统基址（nav 相对路径时拼接）")
    parser.add_argument("--no-vlm", action="store_true", help="跳过 VLM 视觉判定（提速）")
    parser.add_argument("--headed", action="store_true", help="有头模式运行浏览器")
    parser.add_argument("--report", default="artifacts/report.html", help="报告输出路径")
    parser.add_argument(
        "--external", action="store_true",
        help="打真实被测系统（须同时给 --base-url，禁自动拉起演示系统）",
    )
    args = parser.parse_args()

    from nlaut.executor.runner import run_store
    from nlaut.report.render import render_html

    # 无 --base-url 且非外部模式时，自动拉起本地演示被测系统（MVP 自包含演示）
    if not args.base_url and not args.external:
        sys.path.insert(0, str(ROOT / "tests"))
        from demo_app import start as start_demo

        args.base_url, _ = start_demo()
    if args.external and not args.base_url:
        parser.error("--external 必须与 --base-url 同用")

    results = run_store(
        case_ids=args.case,
        base_url=args.base_url,
        use_vlm=not args.no_vlm,
        headless=not args.headed,
        session_log_path="artifacts/session/run.jsonl",
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
