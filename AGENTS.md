# AGENTS.md — nlaut 规范层（地图而非手册，≤200 行）

## 1. 项目摘要

本仓库是自然语言驱动的自动化测试框架（nlaut）。输入：口述测试点、需求片段、API 文档。
输出：结构化用例 IR（`cases/`）→ 派生 pytest 代码（`gen/`）→ 执行 → 三级判定 → 证据链报告。
被测系统类型：Web 应用（Playwright）、Electron/IDE 插件（CDP）。所有回复与代码用中文注释。

## 2. 命令矩阵

| 意图 | 命令 | 关键参数 |
| --- | --- | --- |
| 安装依赖 | `uv sync` | — |
| 框架元测试 | `.venv/bin/python -m pytest` | 默认排除 `-m mlx` |
| 静态检查 | `make lint` | ruff |
| 完整验证 | `make verify` | lint + test，改动后必跑 |
| 执行用例 | `.venv/bin/python -m nlaut.cli` | `--case <id>`；真实系统 `--external --base-url` |
| AI 引擎回归 | `.venv/bin/python -m pytest -m mlx -v` | 需本机权重 |

## 3. 测试策略与分层

- **Layer A 框架元测试**（`tests/`）：IR schema、判定路由、会话/证据基础设施。任何改动必跑。
- **Layer B 判定层金标准**（`tests/judge_golden/`）：标注截图回归，判定引擎改动必跑。
- **Layer C 业务用例**（`cases/` IR）：唯一事实源，变更走 git。

三级判定漏斗（`src/nlaut/judge/`）：
1° 确定性断言（selector 文案，零 AI 成本）→ 2° mlx-vlm 视觉判定（本地）→
3° laya-mlx System One（Noul/Choice/Score，本地）。
置信度 ≥0.9 自动判定；0.5–0.9 转人工仲裁；<0.5 视为不确定。

## 4. 输出格式规范

用例唯一产出格式是 IR（YAML，schema 见 `docs/ir-schema.md`）。
必填：`id`（`tc_` 前缀）、`title`、`source`（原始口述，一字不改）、`channel`、
`quad`、`steps`（声明式）、`assertions`（每条声明 judge 级别）。
口述→IR 的生成协议见 `docs/verbal-to-ir-protocol.md`——生成断言预期值前
必须先确认，禁止编造。

## 5. 禁止事项

1. 禁止手工编辑 `gen/` 下派生代码——改 `cases/` 后重新生成。
2. 禁止生成无 `source` 的孤立用例。
3. 禁止硬编码 sleep 等待——用 `wait_visible` + `timeout_ms`。
4. 禁止绕过 `tests/judge_golden` 金标准回归接入新判定引擎。
5. Electron 通道必须设 `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`。
6. 禁止在 Layer A/B 测试中访问任何非本地环境数据。
7. 禁止编造执行结果——需要执行态结论必须真跑。

## 6. 导航索引

| 主题 | 文档 |
| --- | --- |
| 六层架构、harness 角色映射、路线图 | `docs/architecture.md` |
| IR 字段规范 | `docs/ir-schema.md` |
| Judge 判定协议 | `docs/judge-protocol.md` |
| 口述→IR 生成协议（v1） | `docs/verbal-to-ir-protocol.md` |
