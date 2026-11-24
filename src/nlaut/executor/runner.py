"""L4 执行编排器：IR → 通道执行 → 三级判定 → 结构化结果。

v0.2.0 扩展：
- 通道路由：api 通道 → channels/api.py，web 通道 → channels/web.py（原有）
- _question_for / _engine_name 新增 6 种 LLM 响应断言的翻译和路由
- run_case 新增 api_config 参数（base_url / api_key / model）
- run_store 新增 api_config 透传

ruff: noqa: BLE001 —— run_store 对单用例的宽泛 except 是刻意兜底：
一条用例崩不允许中断整个批次，错误进结果列表由报告层呈现。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from ..ir.model import TestCaseIR
from ..ir.store import IRStore
from .channels import web

ROOT = Path(__file__).resolve().parents[3]


def _question_for(a, ir: TestCaseIR | None, data: dict[str, str] | None = None):
    """把 IR 断言翻译为 Judge Question。

    data 非空时，断言的 expected/text/keywords 等字面量里的 $var 会被替换成
    数据集字段值（与 steps 的 $var 语义一致）——否则「共 $requirement_total 项」
    这类由数据驱动的预期值无法生效。
    """
    from ..judge.protocol import Question
    from .channels.web import resolve_vars

    data = data or {}

    def _t(s: str) -> str:
        return resolve_vars(s, data) if data else s

    # --- DOM 断言（原有，向后兼容）---
    if a.kind == "text_visible":
        return Question(
            kind="noul",
            text=_t(a.expected),
            context={"selector": a.selector, "negated": a.negated},
        )
    if a.kind == "attribute":
        return Question(
            kind="noul",
            text=_t(a.expected),
            context={
                "selector": f"{a.selector}|attr:{a.name}",
                "negated": a.negated,
                "attr_mode": True,
                "raw_selector": a.selector,
                "attr_name": a.name,
            },
        )

    # --- LLM 响应断言（v0.2.0 新增）---
    if a.kind == "response_exact":
        return Question(
            kind="noul",
            text=_t(a.expected),
            context={"mode": "response_exact", "case_sensitive": a.case_sensitive},
        )
    if a.kind == "response_contains":
        return Question(
            kind="noul",
            text="",  # response_contains 不使用 question.text，用 context.keywords
            context={
                "mode": "response_contains",
                "keywords": a.keywords,
                "min_match": a.min_match,
            },
        )
    if a.kind == "response_not_contains":
        return Question(
            kind="noul",
            text="",
            context={"mode": "response_not_contains", "forbidden": a.forbidden},
        )
    if a.kind == "response_json_schema":
        return Question(
            kind="noul",
            text="",
            context={
                "mode": "response_json_schema",
                "required_fields": a.required_fields,
                "expected_values": a.expected_values,
            },
        )
    if a.kind == "tool_call_params":
        return Question(
            kind="noul",
            text="",
            context={
                "mode": "tool_call_params",
                "expected_function": a.expected_function,
                "expected_params": a.expected_params,
            },
        )
    if a.kind == "response_time":
        return Question(
            kind="noul",
            text="",
            context={"mode": "response_time", "max_seconds": a.max_seconds},
        )
    if a.kind == "response_ttft":
        return Question(
            kind="noul",
            text="",
            context={"mode": "response_ttft", "max_ms": a.max_ms},
        )
    if a.kind == "response_ttft_p95":
        return Question(
            kind="noul",
            text="",
            context={"mode": "response_ttft_p95", "max_p95_ms": a.max_p95_ms},
        )
    if a.kind == "response_length":
        return Question(
            kind="noul",
            text="",
            context={"mode": "response_length", "min_chars": a.min_chars, "max_chars": a.max_chars},
        )

    if a.kind == "visual_regression":
        # 视觉回归：SSIM 像素级比对（零 AI token）
        return Question(
            kind="noul",
            text="",
            context={
                "mode": "visual_regression",
                "baseline": a.baseline,
                "min_similarity": a.min_similarity,
                "require_same_size": a.require_same_size,
            },
        )

    if a.kind == "process_state":
        # 工序状态机断言：确定性引擎自己读工序快照（零 AI token）
        return Question(
            kind="noul",
            text="",
            context={
                "mode": "process_state",
                "rule": a.rule,
                "expect": a.expect,
                "task_id": a.task_id,
                "node": a.node,
                "exclude_legacy": a.exclude_legacy,
                "exclude_flat_layout": a.exclude_flat_layout,
                "min_severity": a.min_severity,
                "from_probe": a.from_probe,
            },
        )

    # --- AI 判定断言（原有）---
    if a.kind == "visual_state":
        return Question(kind="noul", text=a.prompt)
    if a.kind == "noul":
        return Question(kind="noul", text=a.question)
    if a.kind == "choice":
        return Question(kind="choice", text=a.question, options=a.options)
    if a.kind == "score":
        return Question(kind="score", text=a.question)
    if a.kind == "agent_verify":
        # Agent-as-Judge：judge 自主取证，证据不预先塞进 Evidence
        return Question(
            kind="noul",
            text=a.objective,
            context={
                "system_prompt": a.system_prompt,
                "max_steps": a.max_steps,
                "threshold": a.threshold,
            },
        )
    if a.kind == "llm_verify":
        # LLM-as-Judge：judge 只能读 Evidence 里已拼好的证据（db_state / llm_response）
        return Question(
            kind="noul",
            text=a.question,
            context={"threshold": a.threshold},
        )

    raise ValueError(f"未知断言类型: {a.kind}")


def _engine_name(a) -> str:
    kind = getattr(a, "kind", "")
    # 确定性判定：DOM + LLM 响应断言
    if kind in (
        "text_visible", "attribute",
        "response_exact", "response_contains", "response_not_contains",
        "response_json_schema", "tool_call_params", "response_time",
        "response_ttft", "response_length", "response_ttft_p95",
        "visual_regression", "process_state",
    ):
        return "deterministic"
    # VLM 判定
    if kind == "visual_state":
        return "mlx-vlm"
    # Agent-as-Judge 判定（自主取证型）
    if kind == "agent_verify":
        return "agent"
    # LLM-as-Judge 判定（证据已完整，一轮 LLM 判断）
    if kind == "llm_verify":
        return "llm"
    # Laya 判定
    return "laya"


def run_case(
    ir: TestCaseIR,
    data: dict[str, str] | None = None,
    base_url: str = "",
    cdp_url: str = "",
    screenshot_dir: str | Path = "artifacts/screenshots",
    headless: bool = True,
    use_vlm: bool = True,
    logger=print,
    session_log=None,
    api_config: dict | None = None,
    registry_path: str = "config/truth_source_registry.yaml",
) -> dict:
    """执行单条用例并完成三级判定，返回完整结果记录。

    api_config: API 通道配置 dict，包含 base_url / api_key / model / timeout。
                为 None 时走 web 通道（原有行为）。
    cdp_url: Electron 通道的 CDP 连接地址（如 http://127.0.0.1:9222）。
             用 channel: electron 的用例走此通道。
    session_log: 可选 SessionLog——传入则每次执行全程留痕（harness 层审计材料），
    append-only JSONL 可回放。
    registry_path: 真值源注册表 YAML 路径。声明的快照会注入 Evidence.db_state。
    """
    from ..ir.datafactory import DataFactory
    from ..judge import Evidence, route
    from ..judge.protocol import get_judge
    from ..judge.truth_registry import (
        collect_db_state,
        load_registry,
        registry_enabled,
    )

    def _log(event: dict) -> None:
        if session_log is not None:
            session_log.record({"case_id": ir.id, **event})

    _log({"event": "run_start", "title": ir.title, "channel": ir.channel})
    if data is None and ir.data_ref:
        data = DataFactory().build(ir.data_ref)
        _log({"event": "data_loaded", "data_ref": ir.data_ref})

    t0 = time.time()

    # 通道路由：api 通道走 channels/api.py，electron 通道走 channels/electron.py，
    # 其余走 web 通道
    if ir.channel == "api" and api_config:
        from .channels import api as api_channel
        ev_kw = api_channel.execute(
            ir, data=data,
            base_url=api_config.get("base_url", ""),
            api_key=api_config.get("api_key", ""),
            model=api_config.get("model", ""),
            timeout=api_config.get("timeout", 60.0),
            reasoning_effort=api_config.get("reasoning_effort", ""),
            disable_thinking=bool(api_config.get("disable_thinking", False)),
            logger=logger,
        )
    elif ir.channel == "electron":
        from .channels import electron as electron_channel
        ev_kw = electron_channel.execute(
            ir, data=data,
            cdp_url=cdp_url,
            base_url=base_url,
            screenshot_dir=screenshot_dir,
            logger=logger,
        )
    else:
        ev_kw = web.execute(
            ir, data=data, base_url=base_url, screenshot_dir=screenshot_dir,
            headless=headless, logger=logger,
        )
    evidence = Evidence(case_id=ir.id, **ev_kw)

    # 真值源注册表（v0.5.0 接线）：读 config/truth_source_registry.yaml，
    # 把声明的真值源快照注入 db_state —— 让只读拼 prompt 的判定器
    # （LLM-as-Judge 等）有真东西可读。
    # 取证失败不阻断：记录带 error 字段，judge 把「取不到」当作一种证据。
    if registry_enabled():
        try:
            reg = load_registry(registry_path)
            if reg.entries:
                db_state = collect_db_state(reg)
                if db_state:
                    evidence.db_state = {**(evidence.db_state or {}), **db_state}
                    _log({"event": "truth_registry", "keys": sorted(db_state)})
        except (OSError, ValueError, ImportError) as e:
            _log({"event": "truth_registry", "error": f"{type(e).__name__}: {e}"})

    # 工序域探针（process_probe 步骤）：通道已在步骤执行时采集并回传，
    # 这里只做兜底 —— 通道没采到时（如 api 通道）再采一次。
    # 通道无关 —— 工序域是文件系统，不依赖浏览器。
    if not ev_kw.get("process_probes"):
        probe_steps = [s for s in ir.steps if getattr(s, "action", "") == "process_probe"]
        if probe_steps:
            from ..judge.process_truth import collect_probe

            ps = probe_steps[-1]
            try:
                probe_result = collect_probe(
                    root=getattr(ps, "root", "~/.cecoder/engineering-workbench"),
                    probe=getattr(ps, "probe", "node"),
                    task_id=getattr(ps, "task_id", None),
                    node=getattr(ps, "node", None),
                )
            except OSError as e:
                probe_result = {"error": f"{type(e).__name__}: {e}"}
            ev_kw["process_probes"] = [probe_result]
            _log({"event": "process_probe", "probe": getattr(ps, "probe", "node"),
                  "source": "runner_fallback"})
    if ev_kw.get("process_probes"):
        probes = ev_kw["process_probes"]
        evidence.process_probes = probes if isinstance(probes, list) else [probes]
        evidence.process_probe = evidence.process_probes[-1]
        _log({"event": "process_probe", "count": len(evidence.process_probes),
              "source": "channel"})

    # Agent-as-Judge 取证轨迹（若有）：报告层展示 judge 查了什么、拿到什么证据
    agent_trace = None
    agent_meta = None
    results = []
    for a in ir.assertions:
        engine_name = _engine_name(a)
        if engine_name == "mlx-vlm" and not use_vlm:
            results.append({
                "assertion": a.kind, "engine": "mlx-vlm(跳过)",
                "status": "skipped", "detail": "use_vlm=False",
            })
            continue
        judge = get_judge(engine_name)
        t1 = time.time()
        verdict = judge.judge(evidence, _question_for(a, ir, data))
        decision = route(verdict, threshold=getattr(a, "threshold", 0.9))
        _log({
            "event": "assertion_judged",
            "assertion": a.kind,
            "engine": verdict.engine,
            "value": float(verdict.value) if isinstance(verdict.value, (int, float)) else str(verdict.value),
            "confidence": verdict.confidence,
            "status": decision.status,
        })
        # 说明列：路由理由 + 关键数值明细（让人知道「差在哪」而不只是「失败了」）
        detail = decision.reason
        raw_extras: dict = {}
        for key in ("similarity", "min_similarity", "size_matched", "baseline_size",
                    "current_size", "degraded", "mode"):
            if key in verdict.raw:
                raw_extras[key] = verdict.raw[key]
        if raw_extras:
            detail = f"{detail} | " + " ".join(
                f"{k}={v}" for k, v in raw_extras.items()
            )
        results.append({
            "assertion": a.kind,
            "engine": verdict.engine,
            "value": verdict.value,
            "confidence": verdict.confidence,
            "status": decision.status,
            "detail": detail,
            "latency_ms": round((time.time() - t1) * 1000, 1),
        })
        # Agent-as-Judge 取证轨迹：报告层展示 judge 查了什么、拿到什么证据
        if verdict.engine == "agent" and verdict.raw.get("trace"):
            agent_trace = verdict.raw.get("trace")
            agent_meta = {
                "rounds": verdict.raw.get("rounds"),
                "tool_calls_total": verdict.raw.get("tool_calls_total"),
                "cost_usd": verdict.raw.get("cost_usd"),
                "elapsed_ms": verdict.raw.get("elapsed_ms"),
                "degraded": verdict.raw.get("degraded"),
                "scope_violations": verdict.raw.get("scope_violations") or [],
                "reason": verdict.raw.get("reason", "")[:2000],
            }

    # 用例级状态：任一 auto_fail → failed；有 human_review → need_review；否则 passed
    statuses = [r["status"] for r in results if r.get("status")]
    if "auto_fail" in statuses:
        case_status = "failed"
    elif "human_review" in statuses:
        case_status = "need_review"
    elif statuses and all(s == "skipped" for s in statuses):
        case_status = "skipped"
    else:
        case_status = "passed"

    _log({"event": "run_end", "status": case_status, "duration_s": round(time.time() - t0, 1)})

    return {
        "case_id": ir.id,
        "title": ir.title,
        "source": ir.source,
        "channel": ir.channel,
        "status": case_status,
        "assertions": results,
        "screenshot": ev_kw.get("screenshot"),
        "duration_s": round((time.time() - t0), 1),
        # 请求与返回（失败/转人工时报告展示，便于人工定性）
        "llm_response": evidence.llm_response,
        "conversation": evidence.conversation,
        "tool_calls": evidence.tool_calls,
        "latency_ms": evidence.latency_ms,
        "response_raw": evidence.response,
        # Agent-as-Judge 取证轨迹（v0.4.0）
        "agent_trace": agent_trace,
        "agent_meta": agent_meta,
    }


def _apply_postconditions(ir: TestCaseIR) -> None:
    """执行后置钩子（MVP）：数据还原动作。"""
    for post in ir.postconditions:
        tag = getattr(post, "cleanup_tag", None)
        if tag == "RESET_LOCK_STATE":
            sys.path.insert(0, str(ROOT / "tests"))
            try:
                from demo_app import reset_lock_state

                reset_lock_state()
            except ImportError:
                print(f"[{ir.id}] 后置钩子跳过: demo_app 不可导入")
        elif tag == "RESET_TASKS":
            sys.path.insert(0, str(ROOT / "tests"))
            try:
                from demo_app import reset_tasks

                reset_tasks()
            except ImportError:
                print(f"[{ir.id}] 后置钩子跳过: demo_app 不可导入")


def llm_suite_case_ids(priorities: list[str] | None = None) -> list[str]:
    """大模型能力测试的用例集 = cases/llm 目录（目录即集合边界）。

    背景（2026-11-07 用户发现口径污染）：tc_agent_verify_001
    （集成准入门禁 G-11，system_prompt「你是软件工厂的任务创建服务」——
    被测对象是软件工厂任务落库，非模型对话能力）位于 cases/demo 且
    channel:api + priority:P0，被 run_llm_batch 的 api+P0 过滤捞进
    大模型能力批次：三批全部 need_review，拖低 P0 通过率 2.1pp
    （46/47 vs 46/46）。修复：大模型能力批次按目录选集，
    cases/demo、cases/giencoder、cases/sweng 的 api 用例不再混入。

    参数 priorities: 优先级过滤（如 ["P0"]；None = 全部）。
    注意：相对路径 cases/llm 依赖调用方工作目录在仓库根（与 run_store 一致）。
    """
    from ..ir.store import IRStore
    irs = IRStore("cases/llm").load_all()
    if priorities:
        keep = set(priorities)
        irs = [ir for ir in irs if ir.priority in keep]
    return [ir.id for ir in irs]


def run_store(
    store: IRStore | None = None,
    case_ids: list[str] | None = None,
    channels: list[str] | None = None,
    priorities: list[str] | None = None,
    session_log_path: str | Path | None = None,
    retry_on_error: int = 0,
    **kwargs,
) -> list[dict]:
    """跑整个 IR 库（或指定 id），返回结果列表。

    session_log_path: 传入则创建 SessionLog，全程留痕到 artifacts/session/。
    api_config: 传入则用于 API 通道用例（base_url / api_key / model）。
    channels: 通道过滤（如 ["api"] 只跑 API 通道；None = 全部通道）。
    priorities: 优先级过滤（如 ["P0"]；None = 全部优先级）。
    retry_on_error: 用例因**基础设施异常**（步骤抛错，如被测系统响应慢导致
        登录页元素未渲染）而 status=error 时，重试次数（0 = 不重试）。
        只重试 error，不重试断言失败——断言失败是真实结论，重试无意义。
    """
    store = store or IRStore(Path("cases"))
    logger = kwargs.pop("logger", print)
    session_log = None
    if session_log_path:
        from ..harness.session import SessionLog

        Path(session_log_path).parent.mkdir(parents=True, exist_ok=True)
        session_log = SessionLog(session_log_path)
    kwargs["session_log"] = session_log
    results = []
    for ir in store.load_all():
        if case_ids and ir.id not in case_ids:
            continue
        if channels and ir.channel not in channels:
            continue
        if priorities and ir.priority not in priorities:
            continue
        result = None
        for attempt in range(retry_on_error + 1):
            try:
                result = run_case(ir, logger=logger, **kwargs)
            except Exception as e:  # noqa: BLE001 — 单用例崩不中断批次
                result = {
                    "case_id": ir.id, "title": ir.title, "source": ir.source,
                    "channel": ir.channel, "status": "error",
                    "assertions": [{"assertion": "-", "engine": "-",
                                    "status": "error", "detail": f"{type(e).__name__}: {e}"}],
                    "screenshot": None, "duration_s": 0.0,
                }
            # 只有 error（基础设施异常）才重试；断言失败是真实结论，不重试
            if result.get("status") != "error" or attempt >= retry_on_error:
                break
            logger(f"[{ir.id}] 第 {attempt + 1} 次执行报 error，重试"
                   f"（{retry_on_error - attempt} 次机会）")
        results.append(result)
        try:
            _apply_postconditions(ir)
        except Exception as e:  # noqa: BLE001 — 清理失败不吞结果
            print(f"[{ir.id}] 后置清理失败: {e}")
    kwargs["logger"] = logger
    return results
