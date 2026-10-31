# Judge 标定基准集 + 评分器组合实施方案

**版本**：v1.0  
**日期**：2026-10-31  
**目标**：让 nlaut 的 LLM Judge 和 Agent-as-Judge 判定结果可信

---

## 一、问题定义

### 1.1 当前问题

**CeCoder 方案要求**：
> 未经与人工标注做过一致性标定的 LLM Judge，其评分结果不得作为放行依据。

**nlaut 现状**：
- LLM Judge 和 Agent-as-Judge 未做过人工标定
- 判定结论的可信度无法量化
- 无法回答"Judge 的判定与人工判定有多一致"

### 1.2 目标

1. **建立标定基准集**：20-30 个样本，由人工独立标注作为金标准
2. **验证一致性**：Judge 与人工标注一致率 ≥ 85%
3. **建立评分器组合**：每任务 3-5 个评分器，多维度判定

---

## 二、标定基准集建设

### 2.1 样本选择原则

| 原则 | 说明 |
|---|---|
| **代表性** | 覆盖 V2/V3/V4 三个验证域 |
| **难度分布** | 30% 简单 + 50% 中等 + 20% 困难 |
| **陷阱样本** | 至少 20% 是"表面正确实际错误"的陷阱 |
| **边界样本** | 包含判定边界附近的样本（如 0.45-0.55 置信度区间） |

### 2.2 样本来源

**优先级顺序**：

1. **真实缺陷案例**（最优先）
   - 从历史缺陷库抽取已确认的缺陷样本
   - 包含"系统说成功但实际失败"的案例

2. **金标准用例**
   - 已有 20 个 GOLDEN_SAMPLES 可直接使用
   - 包含陷阱样本（`is_trap=True`）

3. **合成样本**（补充）
   - 覆盖边界场景（空值、超长文本、矛盾信息）
   - 补足样本数到 30 个

### 2.3 样本结构

```yaml
# 标定样本示例
id: calibrate_001
source: "历史缺陷 #12345"
domain: V3  # V2/V3/V4
difficulty: medium  # easy/medium/hard
is_trap: false  # 是否陷阱样本
scenario: "AI 生成的代码逻辑正确但变量名不规范"

# 被测系统自述（Judge 的输入）
system_claim:
  code: |
    def calc_sum(nums):
      result = 0
      for n in nums:
        if n % 2 == 0:
          result += n ** 2
      return result
  claim: "实现了'返回偶数平方和'功能"

# 判定问题
question: "代码是否正确实现'返回列表中偶数的平方'？"

# 人工标注（金标准）
human_annotation:
  judgement: false  # 人工判定结果
  reason: "代码只累加平方，但未返回列表"
  confidence: 1.0
  dimensions:
    correctness: false
    code_style: true
    edge_cases: false

# 预期 Judge 判定
expected_judge:
  judgement: false
  confidence: 0.9
```

### 2.4 人工标注流程

```
┌─────────────────────────────────────────────────────────────┐
│                    标定基准集建设流程                        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   1. 样本准备                                               │
│      ├─ 从 GOLDEN_SAMPLES 抽取 15 个                        │
│      ├─ 从历史缺陷库抽取 10 个                               │
│      └─ 合成边界样本 5 个                                    │
│                                                             │
│   2. 双盲标注                                               │
│      ├─ 标注者 A 独立标注 30 个样本                          │
│      ├─ 标注者 B 独立标注同样 30 个样本                      │
│      └─ 计算标注者间一致性（需 ≥ 90%）                       │
│                                                             │
│   3. 金标准生成                                             │
│      ├─ 对不一致样本，引入第三标注者仲裁                     │
│      ├─ 生成最终金标准（30 个样本）                          │
│      └─ 记录标注者间一致性指标                               │
│                                                             │
│   4. 版本化                                                 │
│      ├─ 保存为 calibrate_v1.yaml                            │
│      ├─ 记录标注日期、标注者、一致性                         │
│      └─ 变更视为行为回归，需重跑标定                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 2.5 标注表结构

| 字段 | 类型 | 说明 |
|---|---|---|
| sample_id | string | 样本唯一标识 |
| domain | enum | V2/V3/V4 |
| difficulty | enum | easy/medium/hard |
| is_trap | bool | 是否陷阱样本 |
| system_claim | object | 被测系统自述（Judge 输入） |
| question | string | 判定问题 |
| human_judgement | bool | 人工判定结果 |
| human_reason | string | 人工判定理由 |
| human_confidence | float | 人工置信度 |
| dimensions | object | 多维度评分（正确性/风格/边界等） |
| annotator_a | string | 标注者 A ID |
| annotator_b | string | 标注者 B ID |
| agreement | bool | A/B 是否一致 |

---

## 三、评分器组合设计

### 3.1 评分器分级

| 级别 | 评分器类型 | 可靠性 | 使用场景 |
|---|---|---|---|
| **1** | 确定性校验 | 最高 | 编译、测试执行、Schema 校验 |
| **2** | 规则匹配 | 高 | 断言集合、正则、结构化检查 |
| **3** | LLM Judge | 中（需标定） | 开放式文本判定 |
| **4** | 人工 | 兜底 | 抽检 + 标定基准集 |

### 3.2 评分器组合示例

#### 示例 1：代码生成任务

```yaml
# 评测任务配置
task_id: eval_code_gen_001
name: Python 基础函数生成

# 评分器组合（5 个）
scorers:
  - id: syntax_check
    type: deterministic
    weight: 0.2
    description: "语法检查：代码能否编译/执行"
    check: "python -m py_compile"
    
  - id: test_execution
    type: deterministic
    weight: 0.3
    description: "测试执行：是否通过单元测试"
    test_cases:
      - input: [1, 2, 3, 4]
        expected: [4, 16]
      - input: []
        expected: []
      - input: [1, 3, 5]
        expected: []
    
  - id: structure_match
    type: rule
    weight: 0.15
    description: "结构匹配：是否包含 def/return"
    patterns: ["def ", "return "]
    
  - id: semantic_correctness
    type: llm_judge
    weight: 0.25
    description: "语义正确性：逻辑是否正确"
    prompt: |
      判断代码是否正确实现功能：
      功能：返回列表中偶数的平方
      代码：{code}
      只答 yes 或 no。
    calibrated: true  # 已标定
    
  - id: code_style
    type: llm_judge
    weight: 0.1
    description: "代码风格：命名是否规范"
    prompt: |
      判断代码风格是否符合 PEP8：
      代码：{code}
      只答 yes 或 no。
    calibrated: true

# 综合判定规则
decision_rule: "加权平均 ≥ 0.6 且 确定性评分器全部通过"
```

#### 示例 2：数据落库验证（Agent-as-Judge）

```yaml
# 评测任务配置
task_id: eval_data_persistence_001
name: 设计任务数据落库验证

# 评分器组合（4 个）
scorers:
  - id: file_exists
    type: deterministic
    weight: 0.25
    description: "文件存在性"
    check: "os.path.exists('~/.gienx/design-task.json')"
    
  - id: json_valid
    type: deterministic
    weight: 0.25
    description: "JSON 合法性"
    check: "json.load(file)"
    
  - id: record_found
    type: agent_judge
    weight: 0.4
    description: "记录匹配：是否找到对应任务"
    tools: ["read_file", "locate_record"]
    truth_source: "design_tasks"
    calibrated: true
    
  - id: field_correctness
    type: rule
    weight: 0.1
    description: "字段完整性"
    required_fields: ["id", "title", "status", "createdAt"]

# 综合判定规则
decision_rule: "加权平均 ≥ 0.7 且 确定性评分器全部通过"
```

### 3.3 评分器组合规则

**CeCoder 方案要求**：
1. 每个评测任务配置 **3~5 个评分器**
2. 单一评分器的结论不得作为放行依据
3. 评分器与数据的映射须显式声明
4. 评分器变量与数据字段类型须一致

**nlaut 实现规则**：

```python
class ScorerConfig:
    """评分器配置"""
    id: str
    type: Literal["deterministic", "rule", "llm_judge", "agent_judge"]
    weight: float  # 权重，总和应为 1.0
    description: str
    calibrated: bool = False  # 是否已标定
    
class DecisionRule:
    """判定规则"""
    min_weighted_score: float  # 加权平均最低阈值
    require_all_deterministic: bool  # 确定性评分器是否必须全部通过
    require_calibrated: bool  # 是否要求评分器已标定
```

---

## 四、标定流程

### 4.1 Judge 标定步骤

```
┌─────────────────────────────────────────────────────────────┐
│                    Judge 标定流程                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   步骤 1：准备标定基准集                                     │
│      ├─ 30 个样本，人工标注完成                              │
│      └─ 标注者间一致性 ≥ 90%                                 │
│                                                             │
│   步骤 2：Judge 批量判定                                     │
│      ├─ LLM Judge 判定 30 个样本                             │
│      ├─ Agent-as-Judge 判定 30 个样本                        │
│      └─ 记录判定结果、置信度、理由                           │
│                                                             │
│   步骤 3：计算一致性指标                                     │
│      ├─ 一致率 = (Judge 与人工一致数) / 总数                 │
│      ├─ 精确率 = (Judge 判定正确且人工也正确) / (Judge 判定正确) │
│      ├─ 召回率 = (Judge 判定正确且人工也正确) / (人工判定正确) │
│      └─ F1 = 2 * P * R / (P + R)                            │
│                                                             │
│   步骤 4：分析误判方向                                       │
│      ├─ 过松：Judge 判定通过但人工判定失败                   │
│      ├─ 过严：Judge 判定失败但人工判定通过                   │
│      └─ 记录误判样本 ID 和原因                               │
│                                                             │
│   步骤 5：决定是否可用                                       │
│      ├─ 一致率 ≥ 85% → 可用于放行判定                        │
│      ├─ 70% ≤ 一致率 < 85% → 仅作参考，需人工复核            │
│      └─ 一致率 < 70% → 不可用，需调整 Judge 配置             │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 4.2 标定报告结构

```markdown
# Judge 标定报告

## 标定概览
- 标定日期：2026-11-01
- 标定基准集：calibrate_v1.yaml（30 样本）
- 标注者一致性：92%
- Judge 版本：deepseek-v4-flash

## 一致性指标
| Judge 类型 | 一致率 | 精确率 | 召回率 | F1 |
|---|---|---|---|---|
| LLM Judge | 78% | 82% | 75% | 78% |
| Agent-as-Judge | 95% | 97% | 93% | 95% |

## 误判分析
### 过松误判（假阳性）
- sample_id: calibrate_005
  - 人工判定：失败
  - Judge 判定：通过
  - 原因：Judge 未发现边界条件缺陷

### 过严误判（假阴性）
- sample_id: calibrate_012
  - 人工判定：通过
  - Judge 判定：失败
  - 原因：Judge 对代码风格要求过严

## 结论
- LLM Judge 一致率 78% < 85% → 仅作参考，需人工复核
- Agent-as-Judge 一致率 95% ≥ 85% → 可用于放行判定

## 建议
- LLM Judge 需调整 prompt，降低对代码风格的敏感度
- Agent-as-Judge 可直接用于 G-11 门禁验证
```

---

## 五、实施步骤

### 5.1 第一周：标定基准集建设

| 天 | 任务 | 产出 |
|---|---|---|
| 1-2 | 样本选择与准备 | 30 个候选样本 |
| 3-4 | 双盲标注 | 标注者 A/B 各 30 个标注 |
| 5 | 一致性分析与仲裁 | 金标准 calibrate_v1.yaml |

### 5.2 第二周：Judge 标定

| 天 | 任务 | 产出 |
|---|---|---|
| 1-2 | LLM Judge 批量判定 | 30 个判定结果 |
| 3-4 | Agent-as-Judge 批量判定 | 30 个判定结果 |
| 5 | 一致性分析与报告 | 标定报告 |

### 5.3 第三周：评分器组合实现

| 天 | 任务 | 产出 |
|---|---|---|
| 1-3 | 评分器组合 DSL 实现 | ScorerConfig 类 |
| 4-5 | 集成到 nlaut 框架 | tests/scorer_config_test.py |

---

## 六、代码实现建议

### 6.1 标定脚本

```python
# scripts/calibrate_judge.py
from pathlib import Path
import yaml
from nlaut.judge.llm_judge import LLMJudge
from nlaut.judge.agent_judge import AgentJudge

def load_golden_standard(path: str) -> list:
    """加载标定基准集"""
    with open(path) as f:
        return yaml.safe_load(f)

def calibrate_judge(judge, samples: list) -> dict:
    """对样本进行判定并计算一致性"""
    results = []
    for sample in samples:
        # Judge 判定
        verdict = judge.judge(
            Evidence(case_id=sample["id"], dom_state={}),
            Question(kind="noul", text=sample["question"])
        )
        
        # 记录结果
        results.append({
            "sample_id": sample["id"],
            "human_judgement": sample["human_annotation"]["judgement"],
            "judge_judgement": verdict.value == 1.0,
            "judge_confidence": verdict.confidence,
            "agree": (verdict.value == 1.0) == sample["human_annotation"]["judgement"]
        })
    
    # 计算一致性
    agreements = sum(1 for r in results if r["agree"])
    total = len(results)
    
    return {
        "samples": results,
        "agreement_rate": agreements / total,
        "total": total
    }

if __name__ == "__main__":
    # 加载金标准
    samples = load_golden_standard("config/calibrate_v1.yaml")
    
    # 标定 LLM Judge
    llm_judge = LLMJudge(base_url="...", model="deepseek-v4-flash")
    llm_report = calibrate_judge(llm_judge, samples)
    
    # 标定 Agent-as-Judge
    agent_judge = AgentJudge(base_url="...", model="deepseek-v4-flash", tools=DEFAULT_TOOLS)
    agent_report = calibrate_judge(agent_judge, samples)
    
    # 生成报告
    print(f"LLM Judge 一致率: {llm_report['agreement_rate']:.1%}")
    print(f"Agent-as-Judge 一致率: {agent_report['agreement_rate']:.1%}")
```

### 6.2 评分器组合实现

```python
# src/nlaut/judge/scorer.py
from dataclasses import dataclass
from typing import List, Literal

@dataclass
class ScorerConfig:
    """评分器配置"""
    id: str
    type: Literal["deterministic", "rule", "llm_judge", "agent_judge"]
    weight: float
    description: str
    calibrated: bool = False
    
    # 确定性评分器
    check: str = None  # 命令行检查
    test_cases: list = None  # 测试用例
    
    # 规则评分器
    patterns: list = None  # 正则模式
    
    # LLM/Agent Judge
    prompt: str = None
    tools: list = None

@dataclass
class DecisionRule:
    """判定规则"""
    min_weighted_score: float = 0.6
    require_all_deterministic: bool = True
    require_calibrated: bool = True

class ScorerPipeline:
    """评分器管道"""
    
    def __init__(self, scorers: List[ScorerConfig], rule: DecisionRule):
        self.scorers = scorers
        self.rule = rule
        
        # 检查权重总和
        total_weight = sum(s.weight for s in scorers)
        assert abs(total_weight - 1.0) < 0.01, f"权重总和应为 1.0，实际 {total_weight}"
        
    def evaluate(self, context: dict) -> dict:
        """执行评分"""
        results = {}
        
        for scorer in self.scorers:
            if scorer.type == "deterministic":
                score = self._run_deterministic(scorer, context)
            elif scorer.type == "rule":
                score = self._run_rule(scorer, context)
            elif scorer.type == "llm_judge":
                score = self._run_llm_judge(scorer, context)
            elif scorer.type == "agent_judge":
                score = self._run_agent_judge(scorer, context)
            
            results[scorer.id] = {
                "score": score,
                "weight": scorer.weight,
                "type": scorer.type
            }
        
        # 计算加权平均
        weighted_score = sum(
            r["score"] * r["weight"] 
            for r in results.values()
        )
        
        # 确定性评分器检查
        deterministic_passed = all(
            r["score"] >= 0.5 
            for r in results.values() 
            if r["type"] == "deterministic"
        )
        
        # 综合判定
        passed = (
            weighted_score >= self.rule.min_weighted_score and
            (not self.rule.require_all_deterministic or deterministic_passed)
        )
        
        return {
            "passed": passed,
            "weighted_score": weighted_score,
            "scorer_results": results,
            "deterministic_passed": deterministic_passed
        }
```

---

## 七、产出清单

| 产出物 | 路径 | 说明 |
|---|---|---|
| 标定基准集 | `config/calibrate_v1.yaml` | 30 个样本，人工标注 |
| 标定报告 | `artifacts/judge_calibration_report.md` | 一致性分析与建议 |
| 评分器组合 DSL | `src/nlaut/judge/scorer.py` | ScorerConfig + DecisionRule |
| 评分器组合测试 | `tests/scorer_test.py` | 单元测试 |
| 标定脚本 | `scripts/calibrate_judge.py` | 自动化标定流程 |

---

## 八、验收标准

| 标准 | 说明 |
|---|---|
| **标注者一致性 ≥ 90%** | 两个标注者对同一样本的判定一致率 |
| **Judge 一致率 ≥ 85%** | Judge 与人工金标准的一致率 |
| **评分器组合 ≥ 3 个** | 每任务至少 3 个评分器 |
| **权重总和 = 1.0** | 评分器权重归一化 |
| **判定规则可解释** | 决策过程可追溯 |

---

**文档版本**：v1.0  
**生成时间**：2026-10-31