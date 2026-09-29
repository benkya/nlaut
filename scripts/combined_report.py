#!/usr/bin/env python3
"""统一测试报告生成器：对话能力 P0 + 4P12S 交付能力 → 同一份 HTML 报告。

用法:
    set -a; source ~/.hermes/.env; set +a
    .venv/bin/python scripts/combined_report.py --model deepseek-v4-flash --think default

输出: artifacts/report_combined_<model>.html
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def run_llm_batch(model_key: str) -> list[dict]:
    """跑对话能力 P0 批次，返回 run_store 格式的 results。"""
    from nlaut.executor.runner import run_store

    # 读 models.yaml 拿配置
    cfg_path = ROOT / "config" / "models.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    models = cfg.get("models", {})
    mc = models.get(model_key, {})
    base_url = mc.get("base_url", "")
    key_env = mc.get("api_key_env", "")
    model_id = mc.get("model_id", model_key)
    api_key = os.environ.get(key_env, "")
    if not api_key:
        sys.exit(f"缺少环境变量 {key_env}")

    print(f"[对话能力] P0 批次: model={model_id} base={base_url}")
    t0 = time.time()
    results = run_store(
        case_ids=None,  # 全部 P0
        channels=["api"],
        priorities=["P0"],
        api_config={
            "base_url": base_url,
            "api_key": api_key,
            "model": model_id,
            "timeout": 120,
        },
        session_log_path=str(ROOT / "artifacts" / "session" / "run_combined.jsonl"),
    )
    elapsed = time.time() - t0
    passed = sum(r["status"] == "passed" for r in results)
    print(f"[对话能力] {passed}/{len(results)} 通过, {elapsed:.0f}s\n")
    # 给每个 result 加 section 标记
    for r in results:
        r["_section"] = "对话能力 P0"
    return results


def run_4p12s_probe(model_key: str, think: str, max_tokens: int) -> list[dict]:
    """跑 4P12S 探针，把结果转成 run_store 格式的 results（含 assertions + confidence）。"""
    # 读模型配置
    cfg_path = ROOT / "config" / "models.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    models = cfg.get("models", {})
    mc = models.get(model_key, {})
    base_url = mc.get("base_url", "")
    key_env = mc.get("api_key_env", "")
    model_id = mc.get("model_id", model_key)
    api_key = os.environ.get(key_env, "")
    if not api_key:
        sys.exit(f"缺少环境变量 {key_env}")

    # 导入探针模块的函数和 STEPS
    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib
    probe = importlib.import_module("four_p12s_probe")
    STEPS = probe.STEPS
    chat_fn = probe.chat
    run_code_gate = probe.run_code_gate
    run_model_test = probe.run_model_test
    extract_python_block = probe.extract_python_block
    thinking_leak_lines = probe.thinking_leak_lines
    render_user = probe.render_user

    # Laya 引擎（用于 4P12S 产物的语义判定）
    from nlaut.judge.protocol import Evidence, Question, get_judge
    laya = None
    try:
        laya = get_judge("laya")
    except Exception:  # noqa: BLE001  # noqa: BLE001  # noqa: BLE001
        print("[4P12S] Laya 引擎不可用，跳过 noul 判定")

    results: list[dict] = []
    outputs: dict[str, str] = {}
    total_t0 = time.monotonic()

    for step in STEPS:
        sid = step["id"]
        sname = step["name"]
        user = render_user(step["user"], outputs)
        content = ""
        usage = {}
        err = ""
        budget = max_tokens
        gate_ok = False
        gates = []
        code_ok = code_note = test_ok = test_note = None
        leak = code_loc = 0
        attempts = []

        for attempt in range(3):  # retries=2
            t0 = time.monotonic()
            try:
                content, usage = chat_fn(base_url, api_key, model_id,
                                         step["system"], user, budget, think)
                err = ""
            except Exception as e:  # noqa: BLE001
                content, usage, err = "", {}, f"{type(e).__name__}: {e}"[:200]
            elapsed = time.monotonic() - t0

            gates = [bool(re.search(p, content)) for p in step["gate"]]
            n_tests = len(re.findall(r"def test_", content))
            gate_ok = all(gates) and not err
            if gate_ok and step.get("min_tests") and n_tests < step["min_tests"]:
                gate_ok = False

            code_ok = code_note = test_ok = test_note = None
            if gate_ok and step.get("code_gate"):
                code = extract_python_block(content)
                code_loc = len(code.splitlines())
                leak = thinking_leak_lines(code)
                code_ok, code_note = run_code_gate(code, ROOT / "artifacts" / f"4p12s_{model_key}" / "code")
                if code_ok is False:
                    gate_ok = False
            if gate_ok and step.get("test_gate"):
                code = extract_python_block(content)
                code_loc = len(code.splitlines())
                leak = thinking_leak_lines(code)
                test_ok, test_note = run_model_test(
                    code, step["test_file"],
                    ROOT / "artifacts" / f"4p12s_{model_key}" / "code")
                if test_ok is False:
                    gate_ok = False

            attempts.append({"attempt": attempt + 1, "elapsed_s": round(elapsed, 1),
                             "completion_tokens": usage.get("completion_tokens"),
                             "gate_ok": gate_ok, "error": err})
            if gate_ok:
                break
            budget = budget * 2

        # 保存产物
        out_dir = ROOT / "artifacts" / f"4p12s_{model_key}"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{sid}.md").write_text(content, encoding="utf-8")
        outputs[sid] = content

        # 构建 assertions 列表（与 run_store 格式一致）
        assertions = []
        # ① 结构门禁（确定性）
        for i, (pat, ok) in enumerate(zip(step["gate"], gates)):
            assertions.append({
                "assertion": f"structure_gate_{i+1}",
                "engine": "deterministic",
                "value": 1.0 if ok else 0.0,
                "confidence": 1.0,
                "status": "auto_pass" if ok else "auto_fail",
                "detail": f"regex: {pat[:50]}",
            })
        # ② pytest 门禁（确定性）
        if code_ok is not None:
            assertions.append({
                "assertion": "code_gate_pytest",
                "engine": "deterministic",
                "value": 1.0 if code_ok else 0.0,
                "confidence": 1.0,
                "status": "auto_pass" if code_ok else "auto_fail",
                "detail": code_note[:120] if code_note else "",
            })
        if test_ok is not None:
            assertions.append({
                "assertion": "test_gate_pytest",
                "engine": "deterministic",
                "value": 1.0 if test_ok else 0.0,
                "confidence": 1.0,
                "status": "auto_pass" if test_ok else "auto_fail",
                "detail": test_note[:120] if test_note else "",
            })
        # ③ 思考泄漏指标（确定性，信息项不门禁）
        if code_ok is not None or test_ok is not None:
            assertions.append({
                "assertion": "thinking_leak",
                "engine": "deterministic",
                "value": leak,
                "confidence": 1.0,
                "status": "auto_pass",  # 信息项，不门禁
                "detail": f"代码注释中推理独白行数: {leak}",
            })
        # ④ Laya 语义判定（产物质量）
        if laya and content and not err:
            try:
                ev = Evidence(case_id=sid, llm_response=content[:4000])
                q = Question(
                    kind="noul",
                    text=f"该《{sname}》产物是否结构完整、内容专业、满足该步骤的交付要求？只答 yes 或 no。",
                    threshold=0.9,
                )
                verdict = laya.judge(ev, q)
                from nlaut.judge.arbiter import route
                decision = route(verdict, threshold=0.9)
                assertions.append({
                    "assertion": "noul_quality",
                    "engine": verdict.engine,
                    "value": round(float(verdict.value), 4),
                    "confidence": round(float(verdict.confidence), 4),
                    "status": decision.status,
                    "detail": decision.reason[:120] if decision.reason else "",
                })
            except Exception as e:  # noqa: BLE001
                assertions.append({
                    "assertion": "noul_quality",
                    "engine": "laya",
                    "value": 0.5,
                    "confidence": 0.5,
                    "status": "human_review",
                    "detail": f"判定异常: {type(e).__name__}",
                })

        # 综合 status
        has_fail = any(a["status"] == "auto_fail" for a in assertions)
        has_review = any(a["status"] == "human_review" for a in assertions)
        status = "failed" if has_fail else ("need_review" if has_review else "passed")

        last = attempts[-1] if attempts else {}
        results.append({
            "case_id": sid,
            "title": f"4P12S-{sname}",
            "source": f"4P12S 探针 step: {sname}",
            "channel": "api",
            "status": status,
            "duration_s": last.get("elapsed_s", 0),
            "screenshot": None,
            "assertions": assertions,
            "_section": "4P12S 交付能力",
            "_attempts": attempts,
            "_completion_tokens": last.get("completion_tokens"),
            "_code_loc": code_loc if (code_ok is not None or test_ok is not None) else None,
        })
        mark = "PASS" if gate_ok else "FAIL"
        print(f"[{mark}] {sid} {sname}: {last.get('elapsed_s',0)}s "
              f"tokens={last.get('completion_tokens')} 结构={gates}"
              f"{' pytest=' + str(code_ok or test_ok) if code_ok is not None or test_ok is not None else ''}"
              f"{' leak=' + str(leak) if code_ok is not None or test_ok is not None else ''}"
              f" {err}", flush=True)

    total = round(time.monotonic() - total_t0, 1)
    passed = sum(1 for r in results if r["status"] == "passed")
    print(f"\n[4P12S] {passed}/{len(results)} 步通过, {total}s\n")
    return results


def _verdict_card(p0_rate: float, probe_rate: float) -> tuple[str, str]:
    """按 P0 对话通过率 + 4P12S 交付通过率自动生成上线结论卡。

    规则（沿用 final_report.py 的 verdict 逻辑并扩展到双维度）：
    - P0 ≥95% 且 4P12S ≥80% → ✅ 推荐上线
    - P0 ≥90% 或 4P12S ≥60%    → ⚠️ 有条件上线（修复/观察失败项后上线）
    - 否则                      → ❌ 暂不上线
    """
    if p0_rate >= 0.95 and probe_rate >= 0.80:
        css, emoji, text = "pass", "✅", "推荐上线"
    elif p0_rate >= 0.90 or probe_rate >= 0.60:
        css, emoji, text = "warn", "⚠️", "有条件上线（修复/观察失败项后上线）"
    else:
        css, emoji, text = "fail", "❌", "暂不上线（P0 或 4P12S 未达标）"
    msg = (
        f"{emoji} 综合结论：{text} —— "
        f"P0 对话通过率 {p0_rate:.1%} · 4P12S 交付通过率 {probe_rate:.1%}"
    )
    return css, msg


def render_combined(llm_results: list[dict], probe_results: list[dict],
                    model_name: str, out_path: str) -> Path:
    """渲染统一报告：对话能力 + 4P12S 交付能力 合并。"""
    from nlaut.report.render import render_html

    all_results = llm_results + probe_results

    # 分段统计
    llm_pass = sum(r["status"] == "passed" for r in llm_results)
    llm_total = len(llm_results)
    probe_pass = sum(r["status"] == "passed" for r in probe_results)
    probe_total = len(probe_results)
    llm_rate = llm_pass / llm_total if llm_total else 0.0
    probe_rate = probe_pass / probe_total if probe_total else 0.0

    # 上线结论卡
    verdict_css, verdict_msg = _verdict_card(llm_rate, probe_rate)

    # 失败项汇总
    llm_fails = [r["case_id"] for r in llm_results if r["status"] in ("failed", "error")]
    probe_fails = [r["case_id"] for r in probe_results if r["status"] in ("failed", "error")]
    llm_review = [r["case_id"] for r in llm_results if r["status"] == "need_review"]
    fail_summary = ""
    if llm_fails or probe_fails or llm_review:
        items = []
        if llm_fails:
            items.append(f"对话能力失败: {', '.join(llm_fails)}")
        if llm_review:
            items.append(f"对话能力转人工: {', '.join(llm_review)}")
        if probe_fails:
            items.append(f"4P12S 失败: {', '.join(probe_fails)}")
        fail_summary = f'<div style="margin:8px 0;padding:10px;background:#fef2f2;border-radius:6px;font-size:13px"><b>失败/待审项：</b>{" · ".join(items)}</div>'

    header_html = f"""
<div class="card" style="border-left:4px solid #0969da">
  <h2>🔧 统一模型能力测试报告</h2>
  <p class="muted">模型: <b>{model_name}</b> · 生成时间: {time.strftime('%Y-%m-%d %H:%M')}</p>
  <div class="verdict {verdict_css}">{verdict_msg}</div>
  <div style="margin:10px 0">
    <span class="kpi"><b style="color:#1a7f37">{llm_pass}</b>/<b>{llm_total}</b> 对话能力 P0（{llm_rate:.1%}）</span>
    <span class="kpi"><b style="color:#1a7f37">{probe_pass}</b>/<b>{probe_total}</b> 4P12S 交付能力（{probe_rate:.1%}）</span>
  </div>
  {fail_summary}
  <div style="margin:8px 0; padding:8px; background:#f0f2f5; border-radius:6px; font-size:13px">
    <b>测试范围：</b>① 对话能力 P0（{llm_total} 条，18 维度：安全/代码/推理/工具/流式…）
    ② 4P12S 交付能力（{probe_total} 步：需求→PRD→设计→编码→测试，含真实 pytest 门禁）
    <br><b>判定引擎：</b>1° 确定性断言（零 AI 成本）→ 3° Laya System One（本地推理，置信度路由 ≥0.9 自动）
    <br><b>上线规则：</b>P0≥95% 且 4P12S≥80% → 推荐上线；P0≥90% 或 4P12S≥60% → 有条件上线；否则暂不上线
  </div>
</div>
"""
    kpi_html = ""  # 用默认 KPI

    title = f"统一能力测试报告 — {model_name}"
    return render_html(all_results, out_path=out_path,
                       header_html=header_html, kpi_html=kpi_html, title=title)


def main():
    ap = argparse.ArgumentParser(description="统一模型能力测试报告（对话 + 4P12S）")
    ap.add_argument("--model", required=True, help="models.yaml 中的 key")
    ap.add_argument("--think", default="default", choices=["default", "off"])
    ap.add_argument("--max-tokens", type=int, default=6000)
    ap.add_argument("--out", default="", help="报告输出路径")
    ap.add_argument("--skip-llm", action="store_true", help="跳过对话能力批次")
    ap.add_argument("--skip-4p12s", action="store_true", help="跳过 4P12S 探针")
    args = ap.parse_args()

    # 读模型显示名
    cfg = yaml.safe_load((ROOT / "config" / "models.yaml").read_text(encoding="utf-8"))
    model_name = cfg["models"].get(args.model, {}).get("model_id", args.model)

    llm_results = []
    probe_results = []

    if not args.skip_llm:
        llm_results = run_llm_batch(args.model)
    if not args.skip_4p12s:
        probe_results = run_4p12s_probe(args.model, args.think, args.max_tokens)

    out_path = args.out or f"artifacts/report_combined_{re.sub(r'[^a-z0-9]', '_', args.model.lower())}.html"
    path = render_combined(llm_results, probe_results, model_name, out_path)
    print(f"\n=== 统一报告: {path} ===")
    total_pass = sum(r["status"] == "passed" for r in llm_results + probe_results)
    total = len(llm_results) + len(probe_results)
    print(f"总计: {total_pass}/{total} 通过")


if __name__ == "__main__":
    main()
