# nlaut Agent 行为规范（AGENTS.md 素材）

> 本文件是仓库根 AGENTS.md 的完整内容备份。根目录 AGENTS.md 属于受保护的
> agent 指令文件，AI 不得自行写入（本次尝试写入时被审批拦截，行为正确）。
> 由你本人执行一条命令生效：
> `cp docs/agent-conventions.md AGENTS.md && git add AGENTS.md`

## 1. 项目摘要

本仓库是自然语言驱动的自动化测试框架（nlaut）。输入：口述测试点、需求片段、API 文档。
输出：结构化用例 IR（`cases/`）→ 派生 pytest 代码（`gen/`）→ 执行 → 三级判定 → 证据链报告。
被测系统类型：Web 应用（Playwright）、Electron/IDE 插件（CDP）。所有回复与代码用中文注释。

## 2. 命令矩阵

| 意图 | 命令 | 关键参数 |
| --- | --- | --- |
| 安装依赖 | `uv sync` | — |
| 框架元测试 | `make test` | — |
| 静态检查 | `make lint` | — |
| 完整验证 | `make verify` | lint + test，改动后必跑 |
| IR → 代码再生成 | `make regen` | M2 里程碑提供 |
| 执行用例 | `make run TARGET=web` | TARGET=web\|electron，M2 提供 |

## 3. 测试策略与分层

- **Layer A 框架元测试**（`tests/`）：IR schema、判定路由、会话/证据基础设施。任何改动必跑。
- **Layer B 判定层金标准**（`tests/judge_golden/`）：≥30 张人工标注截图回归，判定引擎改动必跑。
- **Layer C 业务用例**（`gen/`）：由 IR 派生，禁止直接维护。

三级判定漏斗（`src/nlaut/judge/`）：
1° 确定性断言（selector/JSON/SQL 比对，零 AI 成本）→ 2° mlx-vlm 视觉判定（截图 + 结构化提问）
→ 3° Laya-MLX typed decision（Noul/Choice/Score，本地 Apple Silicon）。
置信度 ≥0.9 自动判定；0.5–0.9 转人工仲裁队列；<0.5 视为不确定。

## 4. 输出格式规范

用例唯一产出格式是 IR（YAML）。必填字段：`id`（`tc_` 前缀 snake_case）、`title`、
`source`（原始自然语言文本，追溯锚点）、`channel`（web|electron）、`quad`（四元组）、
`steps`（声明式：nav/fill/click/wait_visible）、`assertions`（每条声明 judge 级别与 threshold）、
可选：`req_ref`、`priority`、`data_ref`、`postconditions`。
完整 schema 与示例：`docs/ir-schema.md`、`cases/demo/tc_login_001.yaml`。

## 5. 禁止事项

1. 禁止手工编辑 `gen/` 下派生代码——改 `cases/` 后 `make regen`。
2. 禁止生成无 `source` 的孤立用例。
3. 禁止硬编码 sleep 等待——用 `wait_visible` + `timeout_ms`。
4. 禁止绕过 `tests/judge_golden` 金标准回归接入新判定引擎。
5. Electron 通道必须设 `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`（用 App 自带 Electron，免 200MB 下载）。
6. 禁止在 Layer A/B 测试中访问任何非本地环境数据。

## 6. 导航索引

| 主题 | 文档 |
| --- | --- |
| 六层架构、路线图、风险 | `docs/architecture.md` |
| IR 字段规范 | `docs/ir-schema.md` |
| Judge 判定协议 | `docs/judge-protocol.md` |
