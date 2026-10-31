#!/usr/bin/env python3
"""代码审查场景标定脚本

使用新的代码审查工具对代码审查样本进行标定

用法:
    python scripts/code_review_calibration.py
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nlaut.judge.agent_judge import AgentJudge
from nlaut.judge.code_review_tools import (
    CODE_REVIEW_TOOLS,
    build_code_review_tool_impls,
)
from nlaut.judge.protocol import Evidence, Question


# 代码审查专用 System Prompt
CODE_REVIEW_SYSTEM = """你是代码质量判定器（Code-as-Judge）。

审查维度：
1. **安全性**：是否存在注入、XSS、命令注入等漏洞
2. **性能**：算法复杂度是否可接受（O(1) < O(log n) < O(n) < O(n log n) < O(n²)）
3. **边界**：是否处理了空值、空列表、极端输入
4. **并发**：是否线程安全（竞态条件、死锁）
5. **资源**：是否正确管理资源（文件句柄、数据库连接）

判定规则：
- 有安全漏洞 → 判否
- 性能不可接受（O(n²) 或更差且输入规模大）→ 判否
- 未处理边界条件 → 判否
- 存在竞态条件 → 判否
- 资源泄漏 → 判否
- 其他情况 → 判是

工具使用：
1. 先用 scan_security_issues 检查安全性
2. 用 analyze_performance 检查性能
3. 用 execute_test_cases 验证边界条件
4. 用 analyze_code_structure 分析代码结构

输出格式：
judgement: true|false
理由: <基于你实际分析的结果，一句话说明>
CONFIDENCE: High|Medium|Low
"""


def load_samples(path: str = "config/calibrate_v2.json") -> list[dict]:
    """加载样本"""
    with open(path) as f:
        data = json.load(f)
    # 只取后 10 个样本（代码审查场景）
    return [s for s in data["samples"] if int(s["id"].split("_")[1]) > 20]


def get_llm_config() -> dict:
    """获取 LLM 配置"""
    base_url = os.getenv("DEEPSEEK_V4_FLASH_URL", "http://10.62.64.38:30841/inference/ljcp9fuk/v1")
    api_key = os.getenv("DEEPSEEK_V4_FLASH_KEY", "sk-dummy")
    model = os.getenv("DEEPSEEK_V4_FLASH_MODEL", "DeepSeek-V4-Flash")
    
    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
    }


def calibrate_code_review(samples: list[dict]) -> dict:
    """标定代码审查场景"""
    config = get_llm_config()
    
    print("=" * 60)
    print("代码审查场景标定")
    print("=" * 60)
    print()
    print(f"端点: {config['base_url']}")
    print(f"模型: {config['model']}")
    print(f"样本数: {len(samples)}")
    print(f"配置: 代码审查专用工具 + 5 维度判定 Prompt")
    print()
    
    # 构建工具实现
    tool_impls = build_code_review_tool_impls()
    
    # 创建 Judge
    judge = AgentJudge(
        base_url=config["base_url"],
        api_key=config["api_key"],
        model=config["model"],
        tools=CODE_REVIEW_TOOLS,
        tool_impls=tool_impls,
        max_steps=6,
        require_tool_call=True,  # 强制要求至少调用一次工具
    )
    
    results = []
    
    for i, s in enumerate(samples, 1):
        print(f"[{i}/{len(samples)}] {s['id']}")
        print(f"  问题: {s['question'][:50]}...")
        
        # 构建 Evidence（包含代码）
        code = s["system_claim"].get("code", "")
        test_cases = s["system_claim"].get("test_cases", [])
        
        evidence = Evidence(
            case_id=s["id"],
            llm_response=s["system_claim"].get("claimed_success"),
            conversation=[
                {"role": "user", "content": s["system_claim"].get("objective", s["question"])},
                {"role": "assistant", "content": f"代码如下：\n```\n{code}\n```"},
            ],
        )
        
        if test_cases:
            test_cases_str = json.dumps(test_cases, ensure_ascii=False, indent=2)
            evidence.conversation.append({
                "role": "user",
                "content": f"测试用例：\n```json\n{test_cases_str}\n```",
            })
        
        # 构建问题
        question = Question(
            kind="noul",
            text=s["question"],
            context={
                "max_steps": 6,
                "system_prompt": CODE_REVIEW_SYSTEM,
            },
        )
        
        try:
            # 判定
            verdict = judge.judge(evidence, question)
            
            result = {
                "sample_id": s["id"],
                "golden_judgement": s.get("final_judgement", s["human_annotation"]["judgement"]),
                "judge_judgement": verdict.value == 1.0,
                "judge_confidence": verdict.confidence,
                "agree": (verdict.value == 1.0) == s.get("final_judgement", s["human_annotation"]["judgement"]),
                "tool_calls": verdict.raw.get("tool_calls", 0),
                "cost_usd": verdict.raw.get("cost_usd", 0),
            }
            
            print(f"  金标准: {result['golden_judgement']}")
            print(f"  Judge 判定: {result['judge_judgement']} (置信度: {result['judge_confidence']:.2f})")
            print(f"  工具调用: {result['tool_calls']} 次")
            print(f"  成本: ${result['cost_usd']:.4f}")
            print(f"  一致: {'✓' if result['agree'] else '✗'}")
            
        except Exception as e:
            result = {
                "sample_id": s["id"],
                "golden_judgement": s.get("final_judgement", s["human_annotation"]["judgement"]),
                "judge_judgement": None,
                "judge_confidence": 0,
                "agree": False,
                "error": str(e),
            }
            print(f"  错误: {e}")
        
        results.append(result)
        print()
    
    # 计算统计
    agreed = sum(1 for r in results if r["agree"])
    total = len(results)
    
    # 工具调用统计
    tool_calls_list = [r["tool_calls"] for r in results if r.get("tool_calls")]
    avg_tool_calls = sum(tool_calls_list) / len(tool_calls_list) if tool_calls_list else 0
    
    print(f"一致率: {agreed}/{total} = {agreed/total*100:.1f}%")
    print(f"平均工具调用: {avg_tool_calls:.1f} 次")
    
    return {
        "judge_type": "code_review_agent",
        "model": config["model"],
        "base_url": config["base_url"],
        "config": "代码审查专用工具 + 5 维度判定 Prompt",
        "total_samples": total,
        "agreed": agreed,
        "agreement_rate": agreed / total * 100,
        "avg_tool_calls": avg_tool_calls,
        "results": results,
    }


def main():
    samples = load_samples()
    print(f"加载代码审查样本: {len(samples)} 个\n")
    
    result = calibrate_code_review(samples)
    
    # 保存结果
    output_path = Path("artifacts/code_review_calibration.json")
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n结果已保存: {output_path}")


if __name__ == "__main__":
    main()