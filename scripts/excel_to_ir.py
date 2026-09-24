#!/usr/bin/env python3
"""Excel 用例集 → nlaut IR YAML 批量转换器（Phase 3）。

读取桌面《大模型测试用例集_v1_20260924.xlsx》的"用例总览" Sheet，
把 L1-L4 判定级别（确定性断言）的用例转换为 cases/llm/ 下的 IR YAML。

转换规则：
- 判定级别 L1（精确匹配）  → response_exact / response_contains（数值含关键词匹配）
- 判定级别 L2（关键词匹配）→ response_contains
- 判定级别 L3（结构验证）  → response_json_schema / tool_call_params
- 判定级别 L4（反向验证）  → response_not_contains
- 判定级别 L5/L6/L7（语义/AI/人工）→ 跳过（Phase 2 的 noul/choice 断言单独配置）
- 执行通道含 "API" 的用例才转换；纯 Web/Desktop 通道跳过（留给 Web 通道）

输出：cases/llm/tc_<dim>_<pri>_<num>.yaml（遵循 nlaut IR schema）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import openpyxl
import yaml
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
EXCEL = Path("/Users/Ben/Desktop/大模型测试用例集_v1_20260924.xlsx")
OUT_DIR = ROOT / "cases" / "llm"

# 维度中文名 → 英文短码（用例 id 用）
DIM_CODE = {
    "D01": "nlu", "D02": "reason", "D03": "code", "D04": "math", "D05": "lang",
    "D06": "instruct", "D07": "safety", "D08": "ctx", "D09": "know", "D10": "creative",
    "D11": "dialog", "D12": "tool", "D13": "rag", "D14": "stream", "D15": "agent",
    "D16": "crossplatform", "D17": "except", "D18": "perf",
}

# 维度动作描述（quad.action 用）
DIM_ACTION = {
    "D01": "理解用户意图", "D02": "逻辑推理", "D03": "生成代码", "D04": "数学计算",
    "D05": "多语言处理", "D06": "遵循指令约束", "D07": "拒绝有害请求",
    "D08": "处理长上下文", "D09": "回答知识问题", "D10": "创意生成",
    "D11": "多轮对话", "D12": "调用工具", "D13": "RAG检索增强",
    "D14": "流式输出", "D15": "执行Agent任务", "D16": "多端一致性输出",
    "D17": "异常处理", "D18": "性能响应",
}

# 维度对象（quad.object 用）
DIM_OBJECT = {
    "D01": "自然语言输入", "D02": "推理问题", "D03": "代码需求", "D04": "数学问题",
    "D05": "多语言文本", "D06": "格式/角色约束", "D07": "有害请求",
    "D08": "长文本输入", "D09": "知识问题", "D10": "创意主题",
    "D11": "多轮对话上下文", "D12": "工具调用请求", "D13": "知识库问答",
    "D14": "流式输出内容", "D15": "复杂任务", "D16": "同一输入",
    "D17": "异常输入", "D18": "标准prompt",
}


def extract_keywords_from_expected(expected: str) -> list[str]:
    """从预期结果中提取关键词（用于 response_contains 断言）。

    规则：
    - 数字（含小数）直接作为关键词
    - 中文引号/英文引号内的内容
    - 常见专有名词（长度 >= 2 的中文词）
    """
    kws: list[str] = []

    # 1. 数字（含小数、百分比）
    kws += re.findall(r"\d+(?:\.\d+)?", expected)

    # 2. 引号内的内容
    for m in re.findall(r"[""](.*?)[""]", expected) + re.findall(r"'(.*?)'", expected):
        if m and len(m) <= 20:
            kws.append(m)

    # 3. 明确的专有名词表（高频结果词）
    proper_nouns = [
        "长江", "黄河", "苏格拉底", "北京", "上海", "长江", "长江",
        "无状态", "统一接口", "分层系统", "可缓存", "客户端-服务器",
        "同时落地", "等比数列", "斐波那契", "递归", "星期五", "周五",
        "同时", "DTO", "参数化查询", "SQL注入", "闭包", "加密", "证书",
        "TLS", "红心", "6300", "张三", "小黑", "2.4", "130", "64",
        "18446744073709551616", "78.5", "575", "13/221", "0.0588",
    ]
    for pn in proper_nouns:
        if pn in expected:
            kws.append(pn)

    # 去重保序
    seen: set[str] = set()
    unique = [k for k in kws if not (k in seen or seen.add(k))]
    return unique


def build_assertions(row: dict) -> list[dict]:
    """把 Excel 行的判定信息转换为 IR assertions 列表。"""
    level = row["判定级别"]
    judge_desc = row["判定方式"]
    expected = row["预期结果"]
    assertions: list[dict] = []

    if level == "L1":
        # 精确匹配：数值类用 response_contains（数字关键词），其他 exact
        kws = extract_keywords_from_expected(expected)
        if kws:
            assertions.append({
                "kind": "response_contains",
                "keywords": kws,
                "min_match": min(len(kws), 2),
            })
        else:
            assertions.append({"kind": "response_exact", "expected": expected})

    elif level == "L2":
        kws = extract_keywords_from_expected(expected)
        if kws:
            assertions.append({
                "kind": "response_contains",
                "keywords": kws,
                "min_match": 1,
            })
        else:
            # 无法提取关键词，降级为 noul（AI 判定）
            assertions.append({
                "kind": "noul",
                "question": f"模型回答是否满足：{expected}？只答 yes 或 no。",
                "threshold": 0.9,
            })

    elif level == "L3":
        # 结构化验证
        if "参数" in judge_desc or "工具" in judge_desc:
            assertions.append({
                "kind": "noul",
                "question": f"模型是否{expected}？只答 yes 或 no。",
                "threshold": 0.9,
            })
        else:
            assertions.append({
                "kind": "response_json_schema",
                "required_fields": [],
                "expected_values": {},
            })

    elif level == "L4":
        # 反向验证
        forbidden = extract_keywords_from_expected(expected)
        # 反向验证的 forbidden 应该从判定方式中提取
        m = re.search(r"[""](.*?)[""]", judge_desc)
        if m:
            forbidden = [m.group(1)]
        assertions.append({
            "kind": "response_not_contains",
            "forbidden": forbidden if forbidden else ["制作方法", "步骤"],
        })

    return assertions


def convert_row_to_ir(row: dict, idx: int) -> dict | None:
    """把 Excel 行转换为 IR dict。返回 None 表示跳过。"""
    dim = row["维度"]
    pri = row["优先级"]
    level = row["判定级别"]
    channels = row["执行通道"]

    # 只转换含 API 通道且 L1-L4 的用例
    if "API" not in channels:
        return None
    if level not in ("L1", "L2", "L3", "L4"):
        return None

    # D14 流式 / D15 Agent / D17 异常 / D18 性能 需要特殊 step，Phase 3 跳过（Phase 5 处理）
    if dim in ("D14", "D15", "D17", "D18"):
        return None

    # 工具调用类用例需要 tools 定义，单独处理（tc_d12 只转换非 tool step 的）
    if dim == "D12" and "工具" in row["输入/Prompt"]:
        pass  # 允许，但断言用 noul

    dim_code = DIM_CODE[dim]
    case_id = f"tc_{dim_code}_{pri.lower()}_{idx:03d}"

    # 多轮对话用 api_followup
    steps = []
    prompt_text = row["输入/Prompt"]
    is_multi_turn = dim == "D11" or "T1:" in prompt_text
    if is_multi_turn:
        # 解析 T1/T2/T3 格式
        turns = re.findall(r'T(\d)\s*:\s*["""](.*?)["""]', prompt_text)
        if turns:
            first = True
            for num, text in turns:
                if first:
                    steps.append({"action": "api_call", "prompt": text})
                    first = False
                else:
                    steps.append({"action": "api_followup", "prompt": text})
        else:
            steps.append({"action": "api_call", "prompt": prompt_text})
    else:
        steps.append({"action": "api_call", "prompt": prompt_text})

    assertions = build_assertions(row)
    if not assertions:
        return None

    return {
        "id": case_id,
        "title": row["用例标题"],
        "source": f"大模型测试用例集 v1 {row['用例编号']}：{row['用例标题']}",
        "req_ref": row["用例编号"],
        "priority": pri,
        "channel": "api",
        "quad": {
            "actor": "大模型",
            "action": DIM_ACTION[dim],
            "object": DIM_OBJECT[dim],
        },
        "steps": steps,
        "assertions": assertions,
        "postconditions": [],
    }


def main() -> int:
    if not EXCEL.exists():
        print(f"ERROR: Excel 不存在: {EXCEL}")
        return 1

    wb = openpyxl.load_workbook(EXCEL, data_only=True)
    ws = wb["用例总览"]
    headers = [c.value for c in ws[1]]
    rows = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r[0]:
            continue
        rows.append(dict(zip(headers, r)))

    print(f"读取 Excel: {len(rows)} 行")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 编号计数器（per 维度+优先级）
    counters: dict[str, int] = {}
    converted, skipped = 0, 0
    for row in rows:
        ir = convert_row_to_ir(row, counters.get(f"{row['维度']}_{row['优先级']}", 0) + 1)
        if ir is None:
            skipped += 1
            continue
        counters[f"{row['维度']}_{row['优先级']}"] = (
            counters.get(f"{row['维度']}_{row['优先级']}", 0) + 1
        )

        # 校验 IR schema
        sys.path.insert(0, str(ROOT / "src"))
        from nlaut.ir.model import TestCaseIR

        try:
            TestCaseIR.model_validate(ir)
        except ValidationError as e:
            print(f"  SKIP (schema 校验失败): {ir['id']} - {e}")
            skipped += 1
            continue

        # 写入 YAML
        out_path = OUT_DIR / f"{ir['id']}.yaml"
        out_path.write_text(
            yaml.safe_dump(ir, allow_unicode=True, sort_keys=False, default_flow_style=False),
            encoding="utf-8",
        )
        print(f"  OK: {ir['id']} ({ir['priority']}) {ir['title']}")
        converted += 1

    print(f"\n转换完成: {converted} 条, 跳过 {skipped} 条")
    print(f"输出目录: {OUT_DIR}")

    # 最终校验：所有 YAML 都能加载
    from nlaut.ir.store import IRStore

    store = IRStore(OUT_DIR)
    loaded = store.load_all()
    print(f"加载校验: {len(loaded)} 条全部通过 Pydantic 校验")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
