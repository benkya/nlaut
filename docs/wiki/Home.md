# nlaut 工程 Wiki

本 Wiki 是 **nlaut 自然语言自动化测试框架**的工程文档中心。

## 什么是 nlaut？

自然语言口述 → 结构化用例 IR → 三通道执行（Web / Electron / 大模型 API）→ 四级判定 → 置信度路由 → HTML 报告 → 飞书群分发。

## 快速导航

| 页面 | 内容 |
| --- | --- |
| **[[Installation]]** | **安装指南**（4 步 / 30 分钟，含权重下载 + key 配置 + 验证） |
| [[Architecture]] | 六层架构详解、Harness 角色映射、数据流 |
| [[IR-Schema]] | 用例 IR 字段规范、步骤类型、断言类型 |
| [[Judge-Protocol]] | 四级判定协议、置信度路由、Agent-as-Judge、引擎接入门槛 |
| [[Verbal-to-IR-Protocol]] | 口述→IR 生成协议（团队操作手册） |
| [[LLM-Onboarding-SOP]] | 大模型上线测试 SOP（注册→P0→定性→决策） |
| [[Feishu-Integration]] | 飞书链路集成（口述表→cron→执行→报告发群） |
| [[Roadmap]] | 演进规划（M0.5→M3） |
| [[Agent-Conventions]] | Agent 行为规范（AGENTS.md） |

## 工程数据

- **代码**：~4,200 行 Python
- **用例**：133 条 IR（Web 32 + LLM 93 + Electron 8 + Agent 门禁示例 1）
- **测试**：149 项元测试全绿
- **模型**：13 个预配置（glm/qwen/deepseek/gpt-4o 等）
- **报告**：统一报告（对话能力 + 4P12S 交付能力合并 + 上线结论卡 + 失败用例请求/返回展示）
- **仓库**：https://github.com/benkya/nlaut

## 判定层四级（v0.4.0）

| 级 | 引擎 | 适用 | 延迟 |
| --- | --- | --- | --- |
| 1° | deterministic | 确定性断言（DOM / 响应内容 / 性能） | <1ms |
| 2° | mlx-vlm | 页面视觉状态（本地 Qwen2.5-VL-3B） | 5-6s |
| 3° | laya-mlx | 语义判断 / 归因 / 量表（本地） | 13ms |
| 4° | **agent（Agent-as-Judge）** | **judge 自主调只读工具取证** | **2-5s** |

> 4° agent 的结论置信度**强制封顶 0.85**，必定转人工仲裁——它是「证据充分的判定」，
> 不是「自动放行许可」。实测 9 样本 × 3 轮 = 27/27 判对、翻转率 0%。
