# nlaut 工程 Wiki

本 Wiki 是 **nlaut 自然语言自动化测试框架**的工程文档中心。

## 什么是 nlaut？

自然语言口述 → 结构化用例 IR → 三通道执行（Web / Electron / 大模型 API）→ 三级判定 → 置信度路由 → HTML 报告 → 飞书群分发。

## 快速导航

| 页面 | 内容 |
| --- | --- |
| [[Architecture]] | 六层架构详解、Harness 角色映射、数据流 |
| [[IR-Schema]] | 用例 IR 字段规范、步骤类型、断言类型 |
| [[Judge-Protocol]] | 三级判定协议、置信度路由、引擎接入门槛 |
| [[Verbal-to-IR-Protocol]] | 口述→IR 生成协议（团队操作手册） |
| [[LLM-Onboarding-SOP]] | 大模型上线测试 SOP（注册→P0→定性→决策） |
| [[Feishu-Integration]] | 飞书链路集成（口述表→cron→执行→报告发群） |
| [[Roadmap]] | 演进规划（M0.5→M3） |
| [[Agent-Conventions]] | Agent 行为规范（AGENTS.md） |

## 工程数据

- **代码**：~3,200 行 Python
- **用例**：125 条 IR（Web 32 + LLM 93）
- **测试**：99 项元测试全绿
- **模型**：13 个预配置（glm/qwen/deepseek/gpt-4o 等）
- **仓库**：https://github.com/benkya/nlaut
