# P1 代码审查场景技术债务

创建时间：2026-10-31
优先级：P2（非阻塞）
状态：Open

---

## 问题描述

**症状**：代码审查场景一致率 70%，差 15% 未达标

**根因**：Evidence 包含完整代码 → Agent 认为不需要调用工具 → 工具调用次数 = 0

---

## 影响分析

| 场景 | 一致率 | 状态 |
|---|---|---|
| P0 数据落库 | 85% | ✓ 达标 |
| P1 代码审查 | 70% | ✗ 差 15% |

**影响范围**：代码审查场景判定质量

**影响程度**：中等（70% 并非不可用，但低于 85% 目标）

---

## 尝试过的解决方案

### 尝试 1：System Prompt 强制规则

```python
# 在 System Prompt 中添加
"判定前必须调用至少一个工具分析代码，禁止直接根据描述下结论"
```

**结果**：一致率 70% → 50%（更差）

**结论**：仅修改 System Prompt 会干扰 Agent 判断

### 尝试 2：代码层面降级

```python
# AgentJudge 添加 require_tool_call 参数
# 如果未调用工具，返回 degraded 状态
```

**结果**：一致率 70% → 70%（无变化），2 个样本被降级

**结论**：降级只是标记问题，并没有"逼迫" Agent 调用工具

---

## 推荐解决方案

### 方案：移除 Evidence 中的代码

```python
# 当前
evidence = Evidence(
    conversation=[
        {"role": "user", "content": objective},
        {"role": "assistant", "content": f"代码如下：\n```\n{code}\n```"},  # 移除
    ],
)

# 修改后
evidence = Evidence(
    conversation=[
        {"role": "user", "content": objective},  # 只保留目标描述
    ],
)
# 通过 tool_impls 注入代码
tool_impls = build_code_review_tool_impls(code=code)
```

**预期效果**：Agent 必须调用工具才能获取代码

**工作量**：2-3 天

**风险**：可能影响 P0 达标状态

---

## 后续计划

| 迭代 | 任务 | 优先级 |
|---|---|---|
| Sprint N | 积累更多代码审查样本 | P2 |
| Sprint N+1 | 分析误判模式 | P2 |
| Sprint N+2 | 实现方案（移除 Evidence 中的代码） | P1 |

---

## 相关文件

- `artifacts/code_review_calibration.json` - 标定结果
- `scripts/code_review_calibration.py` - 标定脚本
- `src/nlaut/judge/code_review_tools.py` - 代码审查工具
- `artifacts/p0_p1_calibration_comparison.md` - 对比报告