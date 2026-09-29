"""4p12s 端到端交付代理能力探针（模型作为执行代理，逐步产出真实交付物）。

用法:
    set -a; source ~/.hermes/.env; set +a
    .venv/bin/python scripts/four_p12s_probe.py --model qwen3.8-flash-next --think off

4p12s 12 步 → 本探针 9 步映射（②~⑩全覆盖）:
    ① 流程初始化       跳过（交付状态跟踪表，agent 流程件，无模型能力考察价值）
    ② 需求登记         step2_requirements    （D02 需求理解/结构化）
    ③ PRD              step3_prd             （D03 结构化文档）
    ④ 用户故事         step4_user_stories    （D03 拆分 + 验收标准）
    ⑤ 技术设计         step5_design          （D04 架构设计/文件级方案）
    ⑥ 验证计划         step6_verification    （D08 测试设计）
    ⑦ 任务拆分         step7_tasks           （D04/D06 任务分解 + 依赖关系）
    ⑧ 实现             step8_implementation  （D07 真实编码，元测试 pytest 门禁）
    ⑨ 集成测试         step9_integration     （模型为自己的代码写集成测试，真实 pytest 门禁）
    ⑩ E2E 测试         step10_e2e            （业务闭环场景测试，真实 pytest 门禁）
    ⑪ git push         跳过（按约定）
    ⑫ CI/CD 部署       跳过（按约定）

质量门禁: 每步 regex 结构门禁；⑧⑨⑩ 为确定性执行门禁（pytest 必须全绿）。
思考泄漏检测: 代码注释中的推理独白（wait/let me/让我/等等）计为 thinking_leak_lines 指标。
计时: 每步 wall-time + prompt/completion tokens；汇总 JSON 供模型间对比。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
import yaml

RAW_REQUIREMENT = """【业务原始需求】我们是 AI 网关团队，提供多个大模型代理服务。
现在运营反馈：告警系统用"单次首 token 延迟 > 3 秒"判断模型体验劣化，误报太多——
同一个模型有时 1 秒有时 5 秒，值班同学疲于奔命，已经出现狼来了效应。
我们要把判据改成统计口径：同一用例跑多次，看 P95 分位是否超阈值。
第一期先支持 P95，跑 5 次取最坏那个作为 P95 的近似；数据要能在测试报告里看到
（P95 数值 + 采样次数）。P99 和并发采样暂不做。验收：跑 qwen 内网模型时用例
显示 P95=xxx ms、N 次采样字样，且延迟稳定时不再误报。"""


def build_client(model_key: str) -> tuple[str, str, dict]:
    try:
        p = Path(__file__).resolve().parents[1] / "config" / "models.yaml"
        presets = yaml.safe_load(p.read_text(encoding="utf-8")).get("models", {})
        cfg = presets[model_key]
        base_url, key_env = cfg["base_url"], cfg.get("api_key_env", "")
        model_name = cfg.get("model_id") or cfg.get("model") or model_key
    except (OSError, KeyError, ValueError, yaml.YAMLError):
        base_url = "http://10.62.64.38:30808/inference/v1"
        key_env, model_name = "QWEN38_FLASH_KEY", "Qwen3.8-Flash-Next"
    api_key = os.environ.get(key_env, "")
    if not api_key:
        sys.exit(f"缺少 API key 环境变量: {key_env}")
    return base_url, api_key, {"model": model_name, "internal": True}


def chat(base_url: str, api_key: str, model_name: str, system: str, user: str,
         max_tokens: int = 6000, think: str = "default") -> tuple[str, dict]:
    """单轮调用，返回 (content, usage)。推理模型剥离 reasoning_content。

    think: "default" 不传思考参数（DS-Flash 30841 网关干净）；
           "off" 传 chat_template_kwargs.enable_thinking=False（Qwen 30808 网关必须）。
    """
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }
    if think == "off":
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    with httpx.Client(trust_env=False, timeout=300.0) as c:
        r = c.post(f"{base_url}/chat/completions",
                   headers={"Authorization": f"Bearer {api_key}"}, json=payload)
        r.raise_for_status()
        d = r.json()
    msg = d["choices"][0]["message"]
    return (msg.get("content") or "").strip(), d.get("usage", {})


STEPS: list[dict] = [
    {
        "id": "step2_requirements", "name": "需求登记",
        "gate": [r"需求", r"(FUNC|REQ|RULE|NFR)[- ]?\d"],
        "system": "你是资深需求分析师。把业务原始需求提炼为《原始需求登记表》"
                  "（Markdown），必须含：功能需求条目（ID 用 FUNC-001 递增）、"
                  "业务规则条目（RULE-001 递增）、非功能需求（NFR-001 递增）、"
                  "每条一句话可验证表述。只输出 Markdown 正文。",
        "user": RAW_REQUIREMENT,
    },
    {
        "id": "step3_prd", "name": "PRD",
        "gate": [r"目标|背景", r"验收", r"(不做|范围外|范围)"],
        "system": "你是产品经理。基于上游《原始需求登记表》输出 PRD（Markdown），"
                  "必须含：业务背景、目标（可量化）、范围内/不做、验收标准"
                  "（可执行判据）。只输出 Markdown 正文。",
        "user": "原始需求登记表如下：\n\n{step2_requirements}",
    },
    {
        "id": "step4_user_stories", "name": "用户故事",
        "gate": [r"US-\d", r"AC-\d"],
        "system": "你是敏捷教练。基于上游 PRD 拆分用户故事（Markdown），"
                  "故事 ID 用 US-001 递增，每个故事含 2-4 条验收场景，"
                  "AC ID 用 AC-001 全表递增，含 Given/When/Then。只输出 Markdown 正文。",
        "user": "PRD 如下：\n\n{step3_prd}",
    },
    {
        "id": "step5_design", "name": "技术设计",
        "gate": [r"\.py|文件", r"设计|架构|方案"],
        "system": "你是 Python 架构师。项目是 pytest 自动化测试框架 nlaut"
                  "（src/nlaut/ 包结构，判定器 judge/、执行通道 executor/channels/）。"
                  "基于上游用户故事输出技术设计（Markdown），必须含：新增/修改文件清单"
                  "（真实相对路径）、核心函数签名、数据结构扩展、风险与失败模式。"
                  "只输出 Markdown 正文。",
        "user": "用户故事如下：\n\n{step4_user_stories}",
    },
    {
        "id": "step6_verification", "name": "验证计划",
        "gate": [r"测试|验证", r"数据|前置"],
        "system": "你是测试架构师。基于上游技术设计输出验证计划（Markdown），"
                  "必须含：验证覆盖表（需求 ID→验证类型→层级）、测试数据要求、"
                  "范围内/范围外。只输出 Markdown 正文。",
        "user": "技术设计如下：\n\n{step5_design}",
    },
    {
        "id": "step7_tasks", "name": "任务拆分",
        "gate": [r"TASK-\d", r"依赖"],
        "system": "你是技术负责人。基于上游技术设计与验证计划，把实现工作拆分为开发任务"
                  "清单（Markdown）：任务 ID 用 TASK-001 递增；每个任务必须含：目标"
                  "（一句话）、涉及文件（真实相对路径）、验收判据（可执行）、依赖关系"
                  "（依赖哪些前置 TASK）。任务数量 2-4 个，粒度适中可独立验证。"
                  "只输出 Markdown 正文。",
        "user": "技术设计如下：\n\n{step5_design}\n\n验证计划如下：\n\n{step6_verification}",
    },
    {
        "id": "step8_implementation", "name": "实现(P95算子)",
        "gate": [r"def p95", r"def p95_or_none"],
        "code_gate": True,
        "system": "你是资深 Python 工程师。实现一个零依赖模块 percentile.py：\n"
                  "- p95(samples): 返回 P95（排序后取 ceil(N*0.95) 位，即 N>=1 时最末位；空列表抛 ValueError）\n"
                  "- p95_or_none(samples): 空列表返回 None，否则同 p95\n"
                  "- 输入可为任意可迭代的数值；函数必须纯函数无副作用\n"
                  "只输出一个 ```python 代码块，包含完整模块（含 docstring 和 from __future__ import）。",
        "user": "请实现 percentile.py。",
    },
    {
        "id": "step9_integration", "name": "集成测试",
        "gate": [r"def test_"],
        "test_gate": True,
        "test_file": "test_percentile_integration.py",
        "min_tests": 5,
        "system": "你是测试工程师。为你刚实现的 percentile.py 编写集成测试模块 "
                  "test_percentile_integration.py（pytest）：\n"
                  "- import percentile 中的 p95 与 p95_or_none\n"
                  "- 覆盖：正常多样本、单样本、空列表（ValueError / None）、乱序输入、"
                  "浮点样本，至少 5 个测试函数\n"
                  "- 只断言接口契约行为，不做性能断言\n"
                  "只输出一个 ```python 代码块。",
        "user": "被测模块 percentile.py 如下：\n\n{step8_implementation}",
    },
    {
        "id": "step10_e2e", "name": "E2E测试",
        "gate": [r"def test_", r"阈值|threshold"],
        "test_gate": True,
        "test_file": "test_p95_alert_e2e.py",
        "min_tests": 2,
        "system": "你是测试工程师。编写端到端业务场景测试 test_p95_alert_e2e.py（pytest），"
                  "验证业务闭环：\n"
                  "- 模拟同一用例多次延迟采样（如 [0.9, 1.1, 0.8, 1.0, 1.2] 秒）\n"
                  "- 调用 percentile.p95 计算统计口径延迟\n"
                  "- 断言：延迟稳定时 P95 低于告警阈值（3 秒）不触发告警；"
                  "构造一次尖刺采样（如混入 4.0 秒）时 P95 达到阈值触发告警\n"
                  "- 至少 2 个测试函数，断言用真实数值\n"
                  "只输出一个 ```python 代码块。",
        "user": "业务原始需求：\n\n{RAW}\n\n你的 percentile.py 实现：\n\n{step8_implementation}",
    },
]

THINK_LEAK_PAT = re.compile(r"(wait[ ,.]|let me|let's|hmm|i need to|让我|等等|嗯)", re.IGNORECASE)


def extract_python_block(text: str) -> str:
    m = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1)
    m = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    return m.group(1) if m else text


def thinking_leak_lines(code: str) -> int:
    """统计代码注释行中的推理独白痕迹（思考泄漏指标）。"""
    return sum(1 for ln in code.splitlines()
               if ln.lstrip().startswith("#") and THINK_LEAK_PAT.search(ln))


def run_pytest(workdir: Path, test_file: str) -> tuple[bool, str]:
    """在 workdir 跑指定测试文件，返回 (是否全绿, 输出尾部)。"""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", test_file, "--no-header", "-p", "no:cacheprovider"],
        cwd=workdir, capture_output=True, text=True, timeout=120, check=False)
    ok = r.returncode == 0
    tail = (r.stdout or r.stderr).strip().splitlines()[-4:]
    return ok, " | ".join(tail)


def run_code_gate(code: str, workdir: Path) -> tuple[bool, str]:
    """把模型生成的 percentile.py 写入隔离目录，用真实元测试跑门禁。"""
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "percentile.py").write_text(code, encoding="utf-8")
    test_src = '''
from percentile import p95, p95_or_none

def test_5(): assert p95([100, 200, 300, 400, 500]) == 500
def test_10(): assert p95(list(range(100, 1001, 100))) == 1000
def test_single(): assert p95([1500]) == 1500
def test_unsorted(): assert p95([500, 100, 300, 200, 400]) == 500
def test_none_empty(): assert p95_or_none([]) is None
def test_none_normal(): assert p95_or_none([1, 2, 3, 4, 10]) == 10
'''
    (workdir / "test_gate.py").write_text(test_src, encoding="utf-8")
    return run_pytest(workdir, "test_gate.py")


def run_model_test(code: str, test_file: str, workdir: Path) -> tuple[bool, str]:
    """把模型写的测试文件放进其实现旁，用真实 pytest 验证自洽性。"""
    workdir.mkdir(parents=True, exist_ok=True)
    if not (workdir / "percentile.py").exists():
        return False, "percentile.py 缺失（上游 step8 未产出）"
    (workdir / test_file).write_text(code, encoding="utf-8")
    return run_pytest(workdir, test_file)


def render_user(tpl: str, outputs: dict[str, str]) -> str:
    try:
        return tpl.format(RAW=RAW_REQUIREMENT, **outputs)
    except (KeyError, IndexError, ValueError):
        return tpl


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="models.yaml 中的 key，如 qwen3.8-flash-next")
    ap.add_argument("--out", default="", help="结果 JSON 输出路径")
    ap.add_argument("--think", default="default", choices=["default", "off"],
                    help="思考参数策略：default=不传（DS-Flash 网关）；off=enable_thinking=False（Qwen 网关）")
    ap.add_argument("--max-tokens", type=int, default=6000, help="每步 completion 预算")
    ap.add_argument("--retries", type=int, default=2,
                    help="每步重试次数（空返回/门禁未过时触发，重试时预算翻倍）")
    args = ap.parse_args()

    base_url, api_key, meta = build_client(args.model)
    model_name = meta["model"]
    out_dir = Path(args.out or f"artifacts/4p12s_probe_{args.model}")
    out_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    outputs: dict[str, str] = {}
    total_t0 = time.monotonic()
    for step in STEPS:
        user = render_user(step["user"], outputs)
        content, usage, err = "", {}, ""
        attempts: list[dict] = []
        budget = args.max_tokens
        gate_ok, gates, elapsed = False, [False], 0.0
        code_ok, code_note, test_ok, test_note = None, "", None, ""
        leak, code_loc = 0, 0
        for attempt in range(args.retries + 1):
            t0 = time.monotonic()
            try:
                content, usage = chat(base_url, api_key, model_name, step["system"],
                                      user, budget, args.think)
                err = ""
            except Exception as e:  # noqa: BLE001
                content, usage, err = "", {}, f"{type(e).__name__}: {e}"[:200]
            elapsed = time.monotonic() - t0
            gates = [bool(re.search(p, content)) for p in step["gate"]]
            n_tests = len(re.findall(r"def test_", content))
            gate_ok = all(gates) and not err
            if gate_ok and step.get("min_tests") and n_tests < step["min_tests"]:
                gate_ok = False  # 测试函数数量不足（偷工）
            code_ok, code_note, test_ok, test_note = None, "", None, ""
            if gate_ok and step.get("code_gate"):
                code = extract_python_block(content)
                code_loc = len(code.splitlines())
                leak = thinking_leak_lines(code)
                code_ok, code_note = run_code_gate(code, out_dir / "code")
                if code_ok is False:
                    gate_ok = False
            if gate_ok and step.get("test_gate"):
                code = extract_python_block(content)
                code_loc = len(code.splitlines())
                leak = thinking_leak_lines(code)
                test_ok, test_note = run_model_test(code, step["test_file"], out_dir / "code")
                if test_ok is False:
                    gate_ok = False
            attempts.append({"attempt": attempt + 1, "elapsed_s": round(elapsed, 1),
                             "completion_tokens": usage.get("completion_tokens"),
                             "n_tests": n_tests, "gate_ok": gate_ok, "error": err})
            if gate_ok:
                break
            budget = budget * 2  # 重试时预算翻倍，对抗思考耗尽
        (out_dir / f"{step['id']}.md").write_text(content, encoding="utf-8")
        results.append({
            "step": step["id"], "name": step["name"],
            "elapsed_s": attempts[-1]["elapsed_s"],
            "attempts": attempts,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "structure_gate": gate_ok, "gate_detail": gates,
            "pytest_gate": code_ok if code_ok is not None else test_ok,
            "pytest_note": code_note or test_note,
            "code_loc": code_loc if (code_ok is not None or test_ok is not None) else None,
            "thinking_leak_lines": leak if (code_ok is not None or test_ok is not None) else None,
            "error": err,
        })
        mark = "PASS" if gate_ok else "FAIL"
        extra = ""
        if code_ok is not None:
            extra = f" pytest={code_ok} loc={code_loc} leak={leak}"
        if test_ok is not None:
            extra = f" 自测pytest={test_ok} loc={code_loc} leak={leak}"
        print(f"[{mark}] {step['id']} {step['name']}: {elapsed:.1f}s "
              f"tokens={usage.get('completion_tokens')} 结构={gates}{extra} {err}", flush=True)
        outputs[step["id"]] = content

    total = round(time.monotonic() - total_t0, 1)
    summary = {
        "model": model_name, "total_elapsed_s": total,
        "steps_passed": sum(1 for r in results if r["structure_gate"]),
        "steps_total": len(results),
        "total_completion_tokens": sum(r["completion_tokens"] or 0 for r in results),
        "total_thinking_leak_lines": sum(r["thinking_leak_lines"] or 0 for r in results),
        "steps": results,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 汇总: {summary['steps_passed']}/{summary['steps_total']} 步过门禁, "
          f"总耗时 {total}s, completion tokens={summary['total_completion_tokens']}, "
          f"思考泄漏行={summary['total_thinking_leak_lines']} ===")
    print(f"产物目录: {out_dir}")


if __name__ == "__main__":
    main()
