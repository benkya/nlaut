#!/usr/bin/env python3
"""P1 用例资产修复 v1.3：10 条 P1 失败用例中的可修部分。

跨端一致性 2 条（tc_crossplatform_p1_001/002）需要真实双端执行，留给 Phase 5，不在此修。
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nlaut.ir.model import TestCaseIR

FIXES: dict[str, dict] = {
    # 代码生成用例：json_schema → 代码要素 contains + noul 语义
    "tc_code_p1_001": {
        "assertions": [
            {"kind": "response_contains", "keywords": ["function", "return", "new Set", "filter", "indexOf"], "min_match": 2},
            {"kind": "noul", "question": "JavaScript 代码是否正确实现数组去重且保持原始顺序？只答 yes 或 no。", "threshold": 0.9},
        ],
    },
    "tc_code_p1_003": {
        "assertions": [
            {"kind": "response_contains", "keywords": ["class", "getInstance", "static", "__init__", "new"], "min_match": 3},
            {"kind": "noul", "question": "Python 和 Java 两段代码是否都实现了单例模式核心逻辑？只答 yes 或 no。", "threshold": 0.9},
        ],
    },
    "tc_code_p1_004": {
        "assertions": [
            {"kind": "response_not_contains", "forbidden": ["execute(\\\"INSERT INTO \" + username", "f\"INSERT INTO {username}", "\"INSERT INTO \" + user"]},
            {"kind": "noul", "question": "代码是否使用参数化查询（占位符/预编译）而非字符串拼接 SQL？只答 yes 或 no。", "threshold": 0.9},
        ],
    },
    # 英文对话：断言改英文关键词
    "tc_lang_p1_001": {
        "assertions": [
            {"kind": "response_contains", "keywords": ["TLS", "SSL", "encrypt", "certificate", "handshake"], "min_match": 2},
        ],
    },
    # 字数限制：字面数字断言 → 字数长度判定无法确定性表达，改 noul
    "tc_instruct_p1_001": {
        "assertions": [
            {"kind": "noul", "question": "回答是否为描述机器学习概念的一段文字且字数接近50字（40-60字之间）？只答 yes 或 no。", "threshold": 0.9},
        ],
    },
    # 上下文开头记忆：构造真实长文本
    "tc_ctx_p1_001": {
        "steps": [
            {"action": "api_call", "prompt": "请记住以下信息：本项目的访问密码是 xyz-2026-abc。\n\n然后阅读这段背景：\n软件测试金字塔模型主张单元测试占多数、接口测试居中、UI 测试最少。这一比例在实践中常被倒置，团队倾向于写容易看见的 UI 测试，导致反馈周期长且脆弱。接口层自动化投入产出比最高，因为它绕开了渲染的不确定性，又能验证业务逻辑的组合。契约测试进一步把消费方与提供方的期望固化成可执行的约定。性能测试则应在每个迭代以轻量基准跑，而非上线前集中压测。可观测性数据（日志、指标、链路追踪）是线上质量的真实信号源。\n\n问题：本项目访问密码是什么？"},
        ],
        "assertions": [
            {"kind": "response_contains", "keywords": ["xyz-2026-abc"], "min_match": 1},
        ],
    },
    # 诗歌生成：字数/句数断言
    "tc_creative_p1_001": {
        "assertions": [
            {"kind": "noul", "question": "回答是否为4句诗，且每句约7字、主题与秋天相关？只答 yes 或 no。", "threshold": 0.9},
        ],
    },
    # 输出风格约束：markdown 表格
    "tc_instruct_p1_003": {
        "assertions": [
            {"kind": "response_contains", "keywords": ["|", "Python", "Java"], "min_match": 3},
            {"kind": "noul", "question": "输出是否为 markdown 表格格式且包含至少4个对比维度？只答 yes 或 no。", "threshold": 0.9},
        ],
    },
    # 多轮指令累积：断言代码要素
    "tc_dialog_p1_003": {
        "assertions": [
            {"kind": "response_contains", "keywords": ["def", "factorial"], "min_match": 2},
            {"kind": "noul", "question": "最终代码是否同时包含阶乘计算、错误处理和日志输出三要素？只答 yes 或 no。", "threshold": 0.9},
        ],
    },
}


def main() -> int:
    fixed = skipped = 0
    for case_id, patch in FIXES.items():
        path = ROOT / "cases" / "llm" / f"{case_id}.yaml"
        if not path.exists():
            print(f"  SKIP: {case_id} 不存在")
            skipped += 1
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for k, v in patch.items():
            data[k] = v
        if "[P1资产修复 v1.3]" not in (data.get("source") or ""):
            data["source"] = f"{data.get('source', '')} [P1资产修复 v1.3: 断言映射错误/数据未构造]"
        try:
            ir = TestCaseIR.model_validate(data)
        except ValidationError as e:
            print(f"  FAIL: {case_id}: {e}")
            skipped += 1
            continue
        path.write_text(ir.to_yaml(), encoding="utf-8")
        print(f"  FIXED: {case_id}")
        fixed += 1
    print(f"\n修复: {fixed}, 跳过: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
