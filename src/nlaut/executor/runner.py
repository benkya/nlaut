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


def _question_for(a, ir: TestCaseIR):
    """把 IR 断言翻译为 Judge Question。"""
    from ..judge.protocol import Question

    # --- DOM 断言（原有，向后兼容）---
    if a.kind == "text_visible":
        return Question(
            kind="noul",
            text=a.expected,
            context={"selector": a.selector, "negated": a.negated},
        )
    if a.kind == "attribute":
        return Question(
            kind="noul",
            text=a.expected,
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
            text=a.expected,
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

    # --- AI 判定断言（原有）---
    if a.kind == "visual_state":
        return Question(kind="noul", text=a.prompt)
    if a.kind == "noul":
        return Question(kind="noul", text=a.question)
    if a.kind == "choice":
        return Question(kind="choice", text=a.question, options=a.options)
    if a.kind == "score":
        return Question(kind="score", text=a.question)

    raise ValueError(f"未知断言类型: {a.kind}")


def _engine_name(a) -> str:
    kind = getattr(a, "kind", "")
    # 确定性判定：DOM + LLM 响应断言
    if kind in (
        "text_visible", "attribute",
        "response_exact", "response_contains", "response_not_contains",
        "response_json_schema", "tool_call_params", "response_time",
    ):
        return "deterministic"
    # VLM 判定
    if kind == "visual_state":
        return "mlx-vlm"
    # Laya 判定
    return "laya"


def run_case(
    ir: TestCaseIR,
    data: dict[str, str] | None = None,
    base_url: str = "",
    screenshot_dir: str | Path = "artifacts/screenshots",
    headless: bool = True,
    use_vlm: bool = True,
    logger=print,
    session_log=None,
    api_config: dict | None = None,
) -> dict:
    """执行单条用例并完成三级判定，返回完整结果记录。

    api_config: API 通道配置 dict，包含 base_url / api_key / model / timeout。
                为 None 时走 web 通道（原有行为）。
    session_log: 可选 SessionLog——传入则每次执行全程留痕（harness 层审计材料），
    append-only JSONL 可回放。
    """
    from ..ir.datafactory import DataFactory
    from ..judge import Evidence, route
    from ..judge.protocol import get_judge

    def _log(event: dict) -> None:
        if session_log is not None:
            session_log.record({"case_id": ir.id, **event})

    _log({"event": "run_start", "title": ir.title, "channel": ir.channel})
    if data is None and ir.data_ref:
        data = DataFactory().build(ir.data_ref)
        _log({"event": "data_loaded", "data_ref": ir.data_ref})

    t0 = time.time()

    # 通道路由：api 通道走 channels/api.py，其余走 web
    if ir.channel == "api" and api_config:
        from .channels import api as api_channel
        ev_kw = api_channel.execute(
            ir, data=data,
            base_url=api_config.get("base_url", ""),
            api_key=api_config.get("api_key", ""),
            model=api_config.get("model", ""),
            timeout=api_config.get("timeout", 60.0),
            logger=logger,
        )
    else:
        ev_kw = web.execute(
            ir, data=data, base_url=base_url, screenshot_dir=screenshot_dir,
            headless=headless, logger=logger,
        )
    evidence = Evidence(case_id=ir.id, **ev_kw)

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
        verdict = judge.judge(evidence, _question_for(a, ir))
        decision = route(verdict, threshold=getattr(a, "threshold", 0.9))
        _log({
            "event": "assertion_judged",
            "assertion": a.kind,
            "engine": verdict.engine,
            "value": float(verdict.value) if isinstance(verdict.value, (int, float)) else str(verdict.value),
            "confidence": verdict.confidence,
            "status": decision.status,
        })
        results.append({
            "assertion": a.kind,
            "engine": verdict.engine,
            "value": verdict.value,
            "confidence": verdict.confidence,
            "status": decision.status,
            "detail": decision.reason,
            "latency_ms": round((time.time() - t1) * 1000, 1),
        })

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
        "duration_s": round(time.time() - t0, 1),
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


def run_store(
    store: IRStore | None = None,
    case_ids: list[str] | None = None,
    channels: list[str] | None = None,
    priorities: list[str] | None = None,
    session_log_path: str | Path | None = None,
    **kwargs,
) -> list[dict]:
    """跑整个 IR 库（或指定 id），返回结果列表。

    session_log_path: 传入则创建 SessionLog，全程留痕到 artifacts/session/。
    api_config: 传入则用于 API 通道用例（base_url / api_key / model）。
    channels: 通道过滤（如 ["api"] 只跑 API 通道；None = 全部通道）。
    priorities: 优先级过滤（如 ["P0"]；None = 全部优先级）。
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
        try:
            results.append(run_case(ir, logger=logger, **kwargs))
        except Exception as e:  # noqa: BLE001 — 单用例崩不中断批次
            results.append({
                "case_id": ir.id, "title": ir.title, "source": ir.source,
                "channel": ir.channel, "status": "error",
                "assertions": [{"assertion": "-", "engine": "-",
                                "status": "error", "detail": f"{type(e).__name__}: {e}"}],
                "screenshot": None, "duration_s": 0.0,
            })
        finally:
            try:
                _apply_postconditions(ir)
            except Exception as e:  # noqa: BLE001 — 清理失败不吞结果
                print(f"[{ir.id}] 后置清理失败: {e}")
    kwargs["logger"] = logger
    return results
