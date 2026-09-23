# nlaut 架构（六层）

设计理念沿用 `Agent = Model + Harness`：AGENTS.md 定义做事规范，Harness 提供执行能力，
IR 中间表示保证「换模型/换通道不换框架」。

```
L0 规范层   AGENTS.md（命令矩阵/策略/输出模板/禁止事项，≤200 行）
L1 编排层   harness/（会话持久化 append-only JSONL、工具调用、审批、验证 Loop）
L2 生成层   pipeline/（口述 → 意图澄清 → 四元组 → RAG → IR）
L3 资产层   ir/（TestCaseIR = 唯一事实源；数据工厂；快照/回滚）
L4 执行层   executor/（双通道：Playwright web + Electron CDP；环境矩阵；flaky 统计）
L5 判定层   judge/（三级漏斗：确定性 → mlx-vlm → laya-mlx；置信度路由）
L6 报告层   report/（Allure/HTML + 证据链：截图/原始判定输出/置信度/输入追溯）
```

## 数据流

```
口述"测登录错误密码"
  → pipeline.ingest   意图澄清（缺前置则追问）
  → pipeline.semantic 四元组 {actor, action, object, preconditions}
  → pipeline.generate 生成 IR → cases/demo/tc_login_001.yaml
  → git PR 人工审核（资产变更的唯一入口）
  → make regen         gen/test_tc_login_001.py（pytest + Playwright）
  → executor           执行（web 或 electron 通道），产出 Evidence
  → judge              1°确定性 → 2°VLM(noul) → 3°Laya(归因)
  → report             HTML：结果 + 置信度 + 截图 + 原始口述文本
```

## 三级判定漏斗（L5）

| 级别 | 引擎 | 适用 | 成本/延迟 |
| --- | --- | --- | --- |
| 1° 确定性 | `judge/deterministic.py` | selector 文案/JSON schema/SQL 比对 | 零 AI、微秒级 |
| 2° 视觉 | `judge/vlm_mlx.py`（mlx-vlm，Qwen 系 VLM） | 页面状态、渲染、语义文案 | 本地推理，单图秒级 [推测] |
| 3° System One | `judge/laya.py`（laya-mlx；jev 云端同接口预留） | Noul/Choice/Score 类型决策 | 本地 13.4ms 级 / 云 150ms |

路由规则（`judge/arbiter.py`）：置信度 ≥0.9 自动判定；0.5–0.9 人工仲裁队列（附完整证据包）；
引擎自报低置信视为不确定。判定层自身是被测对象：`tests/judge_golden/` ≥30 张标注截图回归，
统计准确率与校准曲线，防止 AI 误报进入报告。

## 双通道注意（L4）

- Electron：`_electron.launch()` 直接 spawn 目标 App，**必须** `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`；
  macOS 无 GNU `timeout`，用后台进程 + 轮询；asar 需 `npx @electron/asar extract` 解包后分析。
- IDE 插件 webview CDP 不可达时降级 AX-tree；`__e2eAppReady` 等 e2e 探针 prod 构建通常保留。

## 路线图

| 阶段 | 内容 | 出口标准 | 状态 |
| --- | --- | --- | --- |
| M0-skeleton | IR 模型 + Judge 协议 + 基础设施 + 元测试 | `make verify` 绿 | ✅ |
| M0-poc | 装 mlx-vlm + laya-mlx，标注截图集实测判定 | 准确率 ≥90%，置信度与正确性正相关 | ✅ 2026-09-23 |
| M1 | AGENTS.md + 最小 Harness + 口述→IR 闭环（人工审核） | 3 条真实口述全通过审核 | 待做 |
| M2 | IR → pytest/Playwright 代码生成 + 验证 Loop | 生成用例可直接执行、失败自动回传修正 | 待做 |
| M3 | 三级判定接入执行链 + 报告证据链 | AI 判定用例跑通，报告含置信度/归因 | 待做 |
| M4 | 数据工厂/快照回滚 + RAG 历史用例库 | 数据可构造可还原，检索命中率验证 | 待做 |

## 风险登记

1. 视觉判定非确定性 → 采样温度 0 + 多次采样投票 + 全程留痕。
2. AI 判定误报 → 金标准回归 + 人工仲裁队列 + 报告分层统计（确定性 vs AI 判定）。
3. 18GB 内存并发预算：VLM 7B 与浏览器同跑紧张 → 降 3B 或判定串行 [推测]。
4. 模型/提示词升级导致生成漂移 → IR diff 审计（`make regen && git diff cases/`）。
