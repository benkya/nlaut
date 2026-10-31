#!/usr/bin/env python3
"""Judge 标定脚本

用法:
    # 标定 LLM Judge
    python scripts/judge_calibration.py llm

    # 标定 Agent-as-Judge
    python scripts/judge_calibration.py agent

    # 生成对比报告
    python scripts/judge_calibration.py report
"""

import json
import os
import sys
from pathlib import Path
from typing import Any

# 添加项目根目录到 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from nlaut.judge.agent_judge import AgentJudge
from nlaut.judge.llm_judge import LLMJudge
from nlaut.judge.protocol import Evidence, Question, Verdict


def load_golden_standard(path: str = "config/calibrate_v1.json") -> list[dict]:
    """加载金标准样本"""
    with open(path) as f:
        data = json.load(f)
    return data.get("samples", [])


def get_llm_config() -> dict:
    """获取 LLM 配置"""
    # 从环境变量读取
    base_url = os.getenv("DEEPSEEK_V4_FLASH_URL", "http://10.62.64.38:30089/v1")
    api_key = os.getenv("DEEPSEEK_V4_FLASH_KEY", "sk-dummy")
    model = os.getenv("DEEPSEEK_V4_FLASH_MODEL", "deepseek-chat")
    
    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
    }


def build_judge_prompt(sample: dict) -> str:
    """构建 Judge prompt"""
    claim = sample["system_claim"]
    
    prompt = f"""请判定以下系统自述是否正确。

【判定目标】
{claim.get('objective', 'N/A')}

【系统自述】
{claim.get('claimed_success', 'N/A')}

【判定问题】
{sample['question']}

【数据库状态】
```json
{json.dumps(claim.get('db_state', {}), ensure_ascii=False, indent=2)}
```

请给出判定结果：
- judgement: true 或 false
- 理由：一句话说明判定依据

格式：
judgement: true/false
理由: xxx
"""
    return prompt


def calibrate_llm_judge(samples: list[dict]) -> dict:
    """标定 LLM Judge"""
    config = get_llm_config()
    
    print("=" * 60)
    print("LLM Judge 标定")
    print("=" * 60)
    print()
    print(f"端点: {config['base_url']}")
    print(f"模型: {config['model']}")
    print()
    
    # 创建 LLM Judge
    judge = LLMJudge(
        base_url=config["base_url"],
        api_key=config["api_key"],
        model=config["model"],
    )
    
    results = []
    
    for i, s in enumerate(samples, 1):
        print(f"[{i}/{len(samples)}] {s['id']}")
        print(f"  问题: {s['question'][:50]}...")
        
        # 构建证据（使用 db_state 字段）
        evidence = Evidence(
            case_id=s["id"],
            db_state=s["system_claim"].get("db_state"),
            llm_response=s["system_claim"].get("claimed_success"),
        )
        
        # 构建问题
        question = Question(
            kind="noul",
            text=s["question"],
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
                "raw": verdict.raw,
            }
            
            print(f"  金标准: {result['golden_judgement']}")
            print(f"  Judge 判定: {result['judge_judgement']} (置信度: {result['judge_confidence']:.2f})")
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
    
    print(f"一致率: {agreed}/{total} = {agreed/total*100:.1f}%")
    
    return {
        "judge_type": "llm_judge",
        "model": config["model"],
        "base_url": config["base_url"],
        "total_samples": total,
        "agreed": agreed,
        "agreement_rate": agreed / total * 100,
        "results": results,
    }


def calibrate_agent_judge(samples: list[dict]) -> dict:
    """标定 Agent-as-Judge"""
    config = get_llm_config()
    
    print("=" * 60)
    print("Agent-as-Judge 标定")
    print("=" * 60)
    print()
    print(f"端点: {config['base_url']}")
    print(f"模型: {config['model']}")
    print()
    
    # 创建 Agent Judge
    judge = AgentJudge(
        base_url=config["base_url"],
        api_key=config["api_key"],
        model=config["model"],
        tools=None,  # 使用默认工具
        max_steps=4,  # 限制步数
    )
    
    results = []
    
    for i, s in enumerate(samples, 1):
        print(f"[{i}/{len(samples)}] {s['id']}")
        print(f"  问题: {s['question'][:50]}...")
        
        # 构建证据（包含 db_state）
        evidence = Evidence(
            case_id=s["id"],
            db_state=s["system_claim"].get("db_state"),
            llm_response=s["system_claim"].get("claimed_success"),
        )
        
        # 构建问题
        question = Question(
            kind="noul",
            text=s["question"],
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
    
    print(f"一致率: {agreed}/{total} = {agreed/total*100:.1f}%")
    
    return {
        "judge_type": "agent_judge",
        "model": config["model"],
        "base_url": config["base_url"],
        "total_samples": total,
        "agreed": agreed,
        "agreement_rate": agreed / total * 100,
        "results": results,
    }


def generate_report(llm_result: dict = None, agent_result: dict = None):
    """生成对比报告"""
    print("=" * 60)
    print("Judge 标定对比报告")
    print("=" * 60)
    print()
    
    report = f"""# Judge 标定报告

**生成时间**: 2026-10-31

---

## 一、标定概览

- **金标准样本数**: 5
- **标注者一致性**: 80% (4/5)
- **仲裁样本**: 1

---

## 二、LLM Judge 标定结果

"""

    if llm_result:
        report += f"""
| 指标 | 值 |
|---|---|
| 模型 | {llm_result['model']} |
| 总样本 | {llm_result['total_samples']} |
| 一致样本 | {llm_result['agreed']} |
| **一致率** | **{llm_result['agreement_rate']:.1f}%** |
| 判定标准 | ≥ 85% 通过 |

"""
        if llm_result['agreement_rate'] >= 85:
            report += "**结论**: ✓ 一致率达标，可用于判定\n"
        else:
            report += "**结论**: ✗ 一致率未达标，需调整\n"
        
        # 误判样本
        errors = [r for r in llm_result['results'] if not r['agree']]
        if errors:
            report += "\n### 误判样本\n\n"
            for e in errors:
                report += f"- {e['sample_id']}: 金标准={e['golden_judgement']}, 判定={e['judge_judgement']}\n"
    else:
        report += "未标定\n"
    
    report += """
---

## 三、Agent-as-Judge 标定结果

"""
    
    if agent_result:
        report += f"""
| 指标 | 值 |
|---|---|
| 模型 | {agent_result['model']} |
| 总样本 | {agent_result['total_samples']} |
| 一致样本 | {agent_result['agreed']} |
| **一致率** | **{agent_result['agreement_rate']:.1f}%** |
| 判定标准 | ≥ 85% 通过 |

"""
        if agent_result['agreement_rate'] >= 85:
            report += "**结论**: ✓ 一致率达标，可用于判定\n"
        else:
            report += "**结论**: ✗ 一致率未达标，需调整\n"
        
        # 误判样本
        errors = [r for r in agent_result['results'] if not r['agree']]
        if errors:
            report += "\n### 误判样本\n\n"
            for e in errors:
                report += f"- {e['sample_id']}: 金标准={e['golden_judgement']}, 判定={e['judge_judgement']}\n"
    else:
        report += "未标定\n"
    
    # 对比
    if llm_result and agent_result:
        report += f"""
---

## 四、对比分析

| 指标 | LLM Judge | Agent-as-Judge | 差异 |
|---|---|---|---|
| 一致率 | {llm_result['agreement_rate']:.1f}% | {agent_result['agreement_rate']:.1f}% | {agent_result['agreement_rate'] - llm_result['agreement_rate']:+.1f}% |
| 可用性 | {'✓ 可用' if llm_result['agreement_rate'] >= 85 else '✗ 不可用'} | {'✓ 可用' if agent_result['agreement_rate'] >= 85 else '✗ 不可用'} | - |

"""
        
        if agent_result['agreement_rate'] > llm_result['agreement_rate']:
            report += "**结论**: Agent-as-Judge 优于 LLM Judge，推荐使用 Agent-as-Judge\n"
        elif llm_result['agreement_rate'] > agent_result['agreement_rate']:
            report += "**结论**: LLM Judge 优于 Agent-as-Judge（罕见情况）\n"
        else:
            report += "**结论**: 两者一致率相同\n"
    
    report += """
---

## 五、下一步

1. 扩充金标准样本至 30 个
2. 多轮标定验证稳定性
3. 实现 ScorerConfig + DecisionRule
4. 集成到 nlaut 框架

---

**报告生成时间**: 2026-10-31
"""
    
    # 保存报告
    report_path = Path("artifacts/judge_calibration_comparison.md")
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(report)
    
    print(report)
    print(f"\n报告已保存: {report_path}")


def save_results(result: dict, path: str):
    """保存标定结果"""
    with open(path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"结果已保存: {path}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    
    cmd = sys.argv[1]
    
    samples = load_golden_standard()
    print(f"加载金标准样本: {len(samples)} 个\n")
    
    if cmd == "llm":
        result = calibrate_llm_judge(samples)
        save_results(result, "artifacts/llm_judge_calibration.json")
        
    elif cmd == "agent":
        result = calibrate_agent_judge(samples)
        save_results(result, "artifacts/agent_judge_calibration.json")
        
    elif cmd == "report":
        # 加载已有结果
        llm_result = None
        agent_result = None
        
        llm_path = Path("artifacts/llm_judge_calibration.json")
        if llm_path.exists():
            with open(llm_path) as f:
                llm_result = json.load(f)
        
        agent_path = Path("artifacts/agent_judge_calibration.json")
        if agent_path.exists():
            with open(agent_path) as f:
                agent_result = json.load(f)
        
        generate_report(llm_result, agent_result)
    
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()