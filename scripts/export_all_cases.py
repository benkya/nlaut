#!/usr/bin/env python3
"""导出 nlaut 测试用例为自包含 MD 文档（用户偏好：内容内嵌不外链）。

用法:
    # 全量 139 条
    .venv/bin/python scripts/export_all_cases.py ~/Downloads/nlaut测试用例全集.md
    # 仅大模型能力用例（cases/llm，93 条）
    .venv/bin/python scripts/export_all_cases.py ~/Downloads/大模型能力用例.md llm
    # 仅 Web 演示（demo） / 仅 GienCoderWorkbench（giencoder）
    .venv/bin/python scripts/export_all_cases.py ~/Downloads/web用例.md demo
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# 数据集（用例 data_ref 引用，步骤 prompt 里 $var 占位）
DS_PATH = ROOT / "data" / "datasets.yaml"
DATASETS: dict = {}
if DS_PATH.exists():
    DATASETS = yaml.safe_load(DS_PATH.read_text(encoding="utf-8")) or {}


def resolve_var(text: str, data: dict | None) -> str:
    if not text or not data:
        return text
    import re as _re
    return _re.sub(r"\$([a-zA-Z_][a-zA-Z0-9_]*)",
                   lambda m: str(data.get(m.group(1), m.group(0))), text)

from nlaut.ir.model import TestCaseIR

DIMS = {
    # cases/llm 的维度中文名（按 tc_ 前缀）
    "d01": "D01 语义理解", "d02": "D02 指代消解", "d04": "D04 数学计算",
    "d06": "D06 上下文保持", "d07": "D07 安全合规", "d09": "D09 事实性知识",
    "dialog": "对话能力", "instruct": "指令遵循", "know": "事实知识",
    "math": "数学计算", "nlu": "自然语言理解", "reason": "逻辑推理",
    "rag": "RAG 检索增强", "code": "代码生成", "tool": "工具调用",
    "stream": "流式输出", "ctx": "长上下文", "creative": "创意生成",
    "perf": "性能", "agent_verify": "Agent 判定",
}


def dim_of(cid: str) -> str:
    seg = cid[3:].split("_")[0]
    if seg in DIMS:
        return DIMS[seg]
    if cid.startswith("tc_stream_p95"):
        return "流式输出"
    return "其他"


def fmt_assertions(ir) -> str:
    out = []
    for a in ir.assertions:
        k = a.kind
        parts = [k]
        if k == "response_contains":
            parts.append(f"关键词 {getattr(a, 'keywords', [])} 命中≥{getattr(a, 'min_match', 1)}")
        elif k == "response_not_contains":
            parts.append(f"禁词 {getattr(a, 'forbidden', [])}")
        elif k == "response_exact":
            parts.append(f"精确匹配 {getattr(a, 'expected', '')!r}")
        elif k == "response_length":
            parts.append(f"长度 {getattr(a, 'min_chars', '')}~{getattr(a, 'max_chars', '')}")
        elif k == "response_json_schema":
            parts.append("JSON schema 校验")
        elif k == "response_time":
            parts.append(f"≤{getattr(a, 'max_seconds', '')}s")
        elif k == "response_ttft":
            parts.append(f"首token≤{getattr(a, 'max_ms', '')}ms")
        elif k == "response_ttft_p95":
            parts.append(f"P95首token≤{getattr(a, 'max_p95_ms', '')}ms")
        elif k == "tool_call_params":
            parts.append("工具参数校验")
        elif k == "noul":
            q = getattr(a, "question", "")
            parts.append(f"语义判定「{q[:50]}{'…' if len(q) > 50 else ''}」≥{getattr(a, 'threshold', 0.9)}")
        elif k == "choice":
            parts.append(f"选项 {getattr(a, 'options', [])} ≥{getattr(a, 'threshold', 0.9)}")
        elif k == "score":
            parts.append(f"评分≥{getattr(a, 'min_score', '')}")
        elif k == "text_visible":
            parts.append(f"可见文本 {getattr(a, 'expected', '')!r} @ {getattr(a, 'selector', '')[:40]}")
        elif k == "attribute":
            parts.append(f"属性 {getattr(a, 'name', '')}={getattr(a, 'expected', '')!r}")
        elif k == "visual_state":
            parts.append(f"视觉判定「{getattr(a, 'prompt', '')[:40]}…」")
        elif k == "agent_verify":
            o = getattr(a, "objective", "") or ""
            parts.append(f"Agent自主取证「{o[:60]}{'…' if len(o) > 60 else ''}」≥{getattr(a, 'threshold', 0.9)}")
        out.append(" · ".join(parts))
    return "\n".join(f"      {x}" for x in out) if out else "      （无断言）"


def fmt_steps(ir, data: dict | None = None) -> str:
    out = []
    for i, s in enumerate(ir.steps, 1):
        act = s.action
        if act in ("api_call", "api_followup"):
            p = resolve_var(getattr(s, "prompt", ""), data)
            out.append(f"      {i}. [{act}] {p[:120]}{'…' if len(p) > 120 else ''}")
        elif act == "api_tool_call":
            p = resolve_var(getattr(s, "prompt", ""), data)
            out.append(f"      {i}. [带工具调用] {p[:90]}{'…' if len(p) > 90 else ''}")
        elif act == "api_stream":
            p = resolve_var(getattr(s, "prompt", ""), data)
            rep = getattr(s, "repeat", 1)
            out.append(f"      {i}. [流式{'×' + str(rep) if rep > 1 else ''}] {p[:90]}{'…' if len(p) > 90 else ''}")
        elif act == "nav":
            out.append(f"      {i}. [导航] {getattr(s, 'url', '')[:70]}")
        elif act == "fill":
            v = resolve_var(getattr(s, "value", ""), data)
            out.append(f"      {i}. [输入] @{getattr(s, 'selector', '')[:40]} = {v[:40]!r}")
        elif act == "click":
            out.append(f"      {i}. [点击] @{getattr(s, 'selector', '')[:60]}")
        elif act == "select_option":
            v = resolve_var(getattr(s, "value", ""), data)
            out.append(f"      {i}. [选择] @{getattr(s, 'selector', '')[:40]} = {v!r}")
        elif act == "wait_visible":
            out.append(f"      {i}. [等待可见] @{getattr(s, 'selector', '')[:50]}")
        else:
            out.append(f"      {i}. [{act}]")
    return "\n".join(out) if out else "      （无步骤）"


def load_all(d: Path):
    cases = []
    files = [d] if d.is_file() else sorted(d.rglob("*.yaml"))
    for p in files:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        try:
            ir = TestCaseIR.model_validate(raw)
            cases.append((ir, p, None))
        except Exception as e:  # noqa: BLE001  逐样本兜底，失败记入结果继续跑
            cases.append((raw, p, str(e)[:120]))
    return cases


def main() -> None:
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("~/Downloads/nlaut测试用例全集.md").expanduser()
    only = sys.argv[2] if len(sys.argv) > 2 else ""   # 可选：llm / demo / giencoder
    now = time.strftime("%Y-%m-%d %H:%M")
    L: list[str] = []

    groups = [
        ("cases/llm", "第一部分：大模型对话能力用例（cases/llm）", "nlaut LLM 通道用例，P0=对话能力基线（本会话跑批即此集）"),
        ("cases/demo", "第二部分：Web 演示系统用例（cases/demo）", "nlaut Web/Electron 通道演示用例（GienCoderWorkbench 等被测系统）"),
        ("cases/giencoder", "第三部分：GienCoderWorkbench 用例（cases/giencoder）", "UI 设计工作台 + Agent-as-Judge 数据落库验证用例"),
    ]

    if only:
        groups = [g for g in groups if g[0].split("/")[-1] == only]
        if not groups:
            sys.exit(f"未知分组: {only}（可选 llm / demo / giencoder）")
        # 2026-11-04 用户在全集 HTML 上批注：demo / giencoder 部分「不需要」，
        # 大模型能力用例集 = 纯 cases/llm（93 条，P0=46）。
        # 跑批的 47 条 P0 中另有 tc_agent_verify_001（属 cases/demo）——按该口径排除。

    all_cases: dict[str, list] = {}
    n_bad = 0
    for d, _, _ in groups:
        cs = load_all(ROOT / d)
        all_cases[d] = cs
        n_bad += sum(1 for _, _, err in cs if err)

    n_total = sum(len(v) for v in all_cases.values())
    title = "nlaut 测试用例全集" if not only else f"nlaut 大模型能力测试用例集（{only}）"
    L.append(f"# {title}")
    L.append("")
    L.append(f"> 生成时间：{now} · 来源仓库：`~/workspace/nlaut/cases/` · 共 **{n_total} 条**用例"
             f"（{'全部通过 IR schema 校验' if n_bad == 0 else f'⚠️ {n_bad} 条未通过校验，见文末'}）")
    L.append(">")
    L.append("> 本文档自包含：每条用例的判定标准直接写在本页，不引用外部文档。")
    L.append("")

    # 总览统计
    L.append("## 总览")
    L.append("")
    pri = {"P0": 0, "P1": 0, "P2": 0}
    ch = {}
    for cs in all_cases.values():
        for ir, _, err in cs:
            p = (ir.get("priority") if isinstance(ir, dict) else ir.priority) or "?"
            c = (ir.get("channel") if isinstance(ir, dict) else ir.channel) or "?"
            pri[p if p in pri else "?"] = pri.get(p if p in pri else "?", 0) + 1
            ch[c] = ch.get(c, 0) + 1
    L.append("| 维度 | 数量 |")
    L.append("|---|---|")
    L.append(f"| 用例总数 | {n_total} |")
    for p in ("P0", "P1", "P2"):
        L.append(f"| {p} | {pri[p]} |")
    for c, n in sorted(ch.items(), key=lambda x: -x[1]):
        L.append(f"| 通道 {c} | {n} |")
    L.append("")

    for d, title, desc in groups:
        cs = all_cases[d]
        L.append(f"## {title}")
        L.append("")
        L.append(f"{desc}，共 {len(cs)} 条。")
        L.append("")

        # 索引表
        L.append("### 用例索引")
        L.append("")
        L.append("| # | 用例ID | 标题 | 优先级 | 通道 | 断言数 |")
        L.append("|---|---|---|---|---|---|")
        for i, (ir, p, err) in enumerate(cs, 1):
            cid = (ir.get("id") if isinstance(ir, dict) else ir.id) or p.stem
            t = (ir.get("title") if isinstance(ir, dict) else ir.title) or ""
            pr = (ir.get("priority") if isinstance(ir, dict) else ir.priority) or "?"
            chn = (ir.get("channel") if isinstance(ir, dict) else ir.channel) or "?"
            na = len(ir.get("assertions") or []) if isinstance(ir, dict) else len(ir.assertions)
            flag = " ⚠️" if err else ""
            L.append(f"| {i} | `{cid}`{flag} | {t} | {pr} | {chn} | {na} |")
        L.append("")

        # 明细
        L.append("### 用例明细")
        L.append("")
        for ir, p, err in cs:
            if isinstance(ir, dict):
                cid, t = ir.get("id", p.stem), ir.get("title", "")
                L.append(f"#### {cid} — {t}")
                L.append("")
                L.append(f"- **状态**：⚠️ 未通过 IR 校验：`{err}`")
                L.append(f"- **文件**：`{p}`")
                L.append("")
                continue
            L.append(f"#### {ir.id} — {ir.title}")
            L.append("")
            quad = ir.quad
            ds = DATASETS.get(ir.data_ref) if ir.data_ref else None
            L.append(f"- **优先级**：{ir.priority} · **通道**：{ir.channel} · **维度**：{dim_of(ir.id)}")
            if ir.req_ref:
                L.append(f"- **需求关联**：{ir.req_ref}")
            if quad:
                act = f"{quad.actor} / {quad.action} / {quad.object}"
                L.append(f"- **测试要点（Quad）**：{act}")
                if quad.preconditions:
                    pre = [resolve_var(x, ds) for x in quad.preconditions]
                    L.append(f"- **前置条件**：{'；'.join(pre)}")
            if ir.data_ref:
                if ds:
                    kv = "；".join(f"{k}={str(v)[:60]}" for k, v in list(ds.items())[:4])
                    L.append(f"- **数据集**：`{ir.data_ref}` → {kv}")
                else:
                    L.append(f"- **数据集**：`{ir.data_ref}`（⚠️ datasets.yaml 中未找到）")
            L.append("- **判定标准**：")
            L.append(fmt_assertions(ir))
            L.append("- **执行步骤**：")
            L.append(fmt_steps(ir, ds))
            reps = [getattr(s, "repeat", 1) or 1 for s in ir.steps
                    if getattr(s, "action", "") == "api_stream"]
            rep = max(reps) if reps else 1
            if rep > 1:
                L.append(f"- **重复采样**：{rep} 次（P95 口径）")
            L.append("")

    if n_bad:
        L.append("## 附：未通过 IR 校验的用例")
        L.append("")
        L.append("这些用例当前无法被 runner 加载（schema 不符），需修复后才能跑批：")
        L.append("")
        for d, _, _ in groups:
            for ir, p, err in all_cases[d]:
                if err:
                    L.append(f"- `{p}`：{err}")
        L.append("")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(L), encoding="utf-8")
    print(f"生成: {out_path}")
    print(f"大小: {out_path.stat().st_size} 字节")
    print(f"用例: {n_total} 条（坏 {n_bad}）")


if __name__ == "__main__":
    main()