#!/usr/bin/env python3
"""Agent-as-Judge 标定脚本（使用消融实验配置）

与消融实验保持一致：
1. db_state 注入到工具实现，而非放在 Evidence.db_state
2. 使用专门的 LLM_JUDGE_SYSTEM prompt
3. 让 Agent 需要调用工具才能获取证据

用法:
    python scripts/agent_judge_calibration_v2.py
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

# 添加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from nlaut.judge.agent_judge import AgentJudge
from nlaut.judge.agent_tools import DEFAULT_TOOLS, build_env_tool_impls
from nlaut.judge.protocol import Evidence, Question


# 与消融实验相同的 system prompt
LLM_JUDGE_SYSTEM = (
    "你是取证型判定器（Agent-as-Judge）。\n"
    "规则：\n"
    "1. 必须先用工具取证，再给结论；不得凭空判断。\n"
    "2. 结论基于最终/实际状态，不基于中间步骤。\n"
    "3. 探索要完整：找不到目标前，先穷举相关视图/表/接口再判失败。\n"
    "4. 无法取证时判为不确定并说明缺什么证据。\n"
    "5. **取证要高效**：能用一次工具调用拿到的证据，不要拆成多次；\n"
    "   优先查最可能直接证伪目标的那一项（如验证落库先查表）。\n"
    "   工具返回 error 或空结果时，不要反复重试同一个来源。\n"
    "6. **取证完成后立即给结论**，不要再补充额外检查。\n"
    "输出格式（严格三行，不要额外章节）：\n"
    "judgement: true|false\n"
    "理由: <基于你实际取证到的证据，一句话点明关键证据>\n"
    "CONFIDENCE: High|Medium|Low"
)


def load_samples(path: str = "config/calibrate_v2.json") -> list[dict]:
    """加载样本"""
    with open(path) as f:
        data = json.load(f)
    return data.get("samples", [])


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


def calibrate_agent_judge_v2(samples: list[dict], max_samples: int = 20) -> dict:
    """标定 Agent-as-Judge（使用消融实验配置）"""
    config = get_llm_config()
    
    print("=" * 60)
    print("Agent-as-Judge 标定（消融实验配置）")
    print("=" * 60)
    print()
    print(f"端点: {config['base_url']}")
    print(f"模型: {config['model']}")
    print(f"样本数: {min(len(samples), max_samples)}")
    print(f"配置: db_state 注入工具，Agent 需调用工具")
    print()
    
    results = []
    
    for i, s in enumerate(samples[:max_samples], 1):
        print(f"[{i}/{min(len(samples), max_samples)}] {s['id']}")
        print(f"  问题: {s['question'][:50]}...")
        
        # 关键：db_state 注入到工具实现，而非 Evidence
        db_state = s["system_claim"].get("db_state")
        
        # 构建 Evidence（只有 llm_response，不包含 db_state）
        evidence = Evidence(
            case_id=s["id"],
            llm_response=s["system_claim"].get("claimed_success"),
            conversation=[{"role": "user", "content": s["system_claim"].get("objective", s["question"])}],
        )
        
        # 构建问题
        question = Question(
            kind="noul",
            text=s["question"],
            context={
                "max_steps": 4,
                "system_prompt": LLM_JUDGE_SYSTEM,
            },
        )
        
        # 构建 Agent Judge（注入 db_state 到工具）
        tool_impls = build_env_tool_impls(db_state=db_state)
        
        judge = AgentJudge(
            base_url=config["base_url"],
            api_key=config["api_key"],
            model=config["model"],
            tools=DEFAULT_TOOLS,  # 提供工具 schema，让 Agent 知道可调用哪些工具
            tool_impls=tool_impls,  # 注入 db_state
            max_steps=4,
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
        "judge_type": "agent_judge_v2",
        "model": config["model"],
        "base_url": config["base_url"],
        "config": "消融实验配置（db_state 注入工具）",
        "total_samples": total,
        "agreed": agreed,
        "agreement_rate": agreed / total * 100,
        "avg_tool_calls": avg_tool_calls,
        "results": results,
    }


def main():
    samples = load_samples()
    print(f"加载样本: {len(samples)} 个\n")
    
    # 只测试前 20 个样本（数据落库场景）
    result = calibrate_agent_judge_v2(samples, max_samples=20)
    
    # 保存结果
    output_path = Path("artifacts/agent_judge_calibration_v2.json")
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n结果已保存: {output_path}")


if __name__ == "__main__":
    main()