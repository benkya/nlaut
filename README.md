# nlaut — 自然语言自动化测试框架

自然语言口述 → 结构化用例 IR（`cases/`，唯一事实源）→ **三通道执行**（Playwright Web / Electron CDP / 大模型 API）
→ **三级判定**（1° 确定性断言 → 2° VLM 视觉（本地 mlx-vlm）→ 3° Laya System One（本地 laya-mlx））
→ 置信度路由（≥0.9 自动 / 0.5–0.9 人工仲裁）→ **自包含 HTML 报告**（截图+置信度+溯源内嵌）→ **飞书群自动分发**。

## 能力总览

| 被测对象 | 通道 | 用例数 | 典型场景 |
| --- | --- | --- | --- |
| **Web 应用** | Playwright（系统 Chrome） | 32 条 | 登录/任务列表/查询/翻页/新增 |
| **大模型 API** | httpx（OpenAI 兼容） | 93 条（18 维度 × P0/P1/P2） | 安全/代码/推理/工具调用/流式/RAG/对话 |
| **Electron/IDE 插件** | CDP | 7 条 | GienCoderWorkbench 桌面端 |

**框架规模**：~4,000 行 Python · 125 条用例 IR · 99 项元测试全绿 · 13 个模型预配置 · 9 篇 GitHub Wiki · 统一报告（对话+4P12S+上线结论卡）

## 快速开始

### 跑 Web 演示用例（自动拉起本地被测系统）

```bash
cd ~/workspace/nlaut
.venv/bin/python -m nlaut.cli
```

### 测一个大模型的能力（P0 批次，45 条）

```bash
# 方式一：预配置模型（config/models.yaml 已有 13 个）
.venv/bin/python -m nlaut.cli --model glm-5.2 --api-only --priority P0

# 方式二：临时模型，不预配置，地址+key 直接走命令行
.venv/bin/python -m nlaut.cli --model my-model --api-base https://api.example.com/v1 \
    --api-key sk-xxx --api-only --priority P0
```

### 统一能力报告（对话能力 + 4P12S 交付能力合并）

```bash
# 同一份 HTML 报告：P0 对话 45 条 + 4P12S 交付 9 步，含上线结论卡
.venv/bin/python scripts/combined_report.py --model deepseek-v4-flash

# 只跑对话能力 / 只跑 4P12S
.venv/bin/python scripts/combined_report.py --model deepseek-v4-flash --skip-4p12s
.venv/bin/python scripts/combined_report.py --model deepseek-v4-flash --skip-llm
```

报告顶部自动给出 **上线结论卡**（✅ 推荐上线 / ⚠️ 有条件上线 / ❌ 暂不上线），失败和转人工的用例可折叠查看**请求（system+user）和模型返回全文**，便于人工定性。

### 飞书口述零动作测模型（最终形态）

在飞书「口述测试需求表」填一行：

> **测试glm-5.2大模型能力，模型地址是：https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1，key是：sk-xxxxx**

cron 每 5 分钟扫描 → 自动识别 → 跑 P0 批次 → 报告发群 → 表格写回。**全程零额外动作。**

## 命令矩阵

| 场景 | 命令 |
| --- | --- |
| 跑全部 Web 用例（演示系统自动拉起） | `.venv/bin/python -m nlaut.cli` |
| 测大模型 P0 能力 | `.venv/bin/python -m nlaut.cli --model <名> --api-only --priority P0` |
| 测大模型全量（P0+P1，81 条） | `.venv/bin/python -m nlaut.cli --model <名> --api-only --priority P0,P1` |
| **统一能力报告（对话+4P12S 合并）** | `.venv/bin/python scripts/combined_report.py --model <名>` |
| 只跑某条用例 | `.venv/bin/python -m nlaut.cli --case tc_login_001` |
| 打真实被测系统 | `.venv/bin/python -m nlaut.cli --external --base-url http://你的地址` |
| 有头模式（看浏览器操作） | `.venv/bin/python -m nlaut.cli --headed` |
| 跳过 VLM（提速，只确定性+Laya） | `.venv/bin/python -m nlaut.cli --no-vlm` |
| 框架元测试 | `.venv/bin/python -m pytest`（99 项，~0.5s） |
| AI 引擎真实推理回归 | `.venv/bin/python -m pytest -m mlx -v`（需本机权重） |
| 静态检查 | `make lint`（ruff） |
| 完整验证 | `make verify`（lint + test） |

## 三级判定漏斗

```
1° 确定性断言（deterministic.py）     零 AI 成本，微秒级
    ↓ 未覆盖的场景
2° VLM 视觉判定（mlx-vlm，Qwen2.5-VL-3B）  本地推理，单图 5-6s
    ↓ 未覆盖的场景
3° Laya System One（laya-mlx）         Noul/Choice/Score，13ms 级，0 输出 token
```

**置信度路由**（`judge/arbiter.py`）：
- 确定性引擎 → value ≥0.5 直接 auto_pass/auto_fail
- AI 引擎 conf ≥0.9 → auto_pass/auto_fail
- 0.5 ≤ conf < 0.9 → human_review（人工仲裁队列，附完整证据包）

**判定层自身是被测对象**：`tests/judge_golden/` 金标准回归防止 AI 误报进入报告。

## 断言类型速查

| kind | 引擎 | 适用 |
| --- | --- | --- |
| `text_visible` / `attribute` | 确定性（1°） | DOM 文案/属性——**能用就用它** |
| `response_contains` / `response_not_contains` | 确定性（1°） | LLM 回复关键词/反向验证（含否定语境豁免） |
| `response_exact` / `response_json_schema` | 确定性（1°） | 精确匹配/JSON 结构验证 |
| `tool_call_params` | 确定性（1°） | LLM 工具调用参数验证 |
| `response_ttft` / `response_ttft_p95` | 确定性（1°） | 流式首 token 延迟（v0.2.7 P95 多次采样） |
| `response_length` / `response_time` | 确定性（1°） | 字数区间/延迟断言 |
| `visual_state` | VLM（2°） | 页面整体状态、渲染正确性 |
| `noul` / `choice` / `score` | Laya（3°） | 概率判断/归因分类/量表评分 |

## 用例 IR（唯一事实源）

```yaml
id: tc_d04_p0_001              # 必填，^tc_[a-z0-9_]+$
title: 多步算术
source: "大模型测试用例集 D04-P0-001"  # 必填，原始输入文本（追溯锚点）
priority: P0                    # P0|P1|P2
channel: api                   # web | electron | api
quad: {actor: 大模型, action: 数学计算, object: 数学问题}
data_ref: d04_multi_step       # 数据走 data/datasets.yaml，用例不内嵌
steps:
  - {action: api_call, prompt: "$prompt"}
assertions:
  - kind: response_contains     # 确定性：关键词匹配
    keywords: ["130"]
    min_match: 1
  - kind: noul                  # AI 判定兜底
    question: "模型是否正确计算出最终价格130元？只答 yes 或 no。"
    threshold: 0.9
postconditions: []
```

完整 Schema 见 [docs/ir-schema.md](docs/ir-schema.md)。口述→IR 生成协议见 [docs/verbal-to-ir-protocol.md](docs/verbal-to-ir-protocol.md)。

## 大模型能力测试

### 18 个维度（93 条用例）

| 维度 | P0 | P1 | P2 | 测什么 |
| --- | --- | --- | --- | --- |
| safety | 4 | 3 | 2 | 拒绝有害指令/隐私窃取/代码恶意/偏见检测 |
| code | 3 | 4 | 1 | Python 基础/SQL 生成/数据结构 |
| tool | 4 | 3 | 2 | 正确选择工具/参数提取/多工具串联 |
| math | 2 | 3 | 2 | 多步算术/几何/概率 |
| reason | 3 | 3 | — | 反事实推理/时间序列/逻辑链 |
| know | 3 | 3 | 1 | 事实知识/常识/专业领域 |
| instruct | 4 | 3 | — | 指令遵循/格式约束/角色保持 |
| rag | 3 | 2 | 1 | 长文本信息抽取/多跳推理 |
| nlu | 3 | 3 | — | 意图识别/多义词消歧/情感分析 |
| dialog | 3 | 3 | — | 多轮对话/上下文保持/话题切换 |
| ctx | 2 | 1 | 2 | 长上下文/跨段落 |
| stream | 3 | — | — | 首token延迟/内容完整/P95 稳定性 |
| creative | 1 | 1 | — | 结构化创意 |
| lang | — | 2 | — | 多语言 |
| crossplatform | — | 2 | — | 跨平台 |
| perf | 1 | — | — | 性能基准 |
| d01-d09 | 4 | — | — | 基础能力（意图/多步/知识/排序/摘要/抽取） |

### 模型配置（config/models.yaml）

```yaml
glm-5.2:
  provider: aliyun_maas
  base_url: "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
  api_key_env: "CUSTOM_API_KEY"    # key 存环境变量，不进 git
  model_id: "glm-5.2"
  supports_streaming: true
  supports_function_calling: true
  context_window: 131072
```

已预配置 13 个模型（glm-5.2/5.3、qwen3.8-flash-next/3.7-max/max、deepseek-v4-pro/flash/chat、gpt-4o、glm-4-plus、qwen-local 等）。新模型加一个条目即可。

**key 安全**：口述携带的 key 只存内存 → 环境变量注入子进程 → 表格/报告一律打码。

### 飞书链路（已闭环）

```
飞书口述表 → cron 5 分钟扫描 → 识别模型名+地址+key
→ 零动作执行（LLM 批次跳过确认卡点）/ 普通用例走「已确认」卡点
→ 执行 → HTML 报告上传群文件 → 表格写回（状态+执行结果+报告位置）
```

- 口述表：[飞书 bitable](https://gaientech.feishu.cn/base/Yfm7bQmFeaEwYxsLYJAcQozRnah?table=tblK7ug76gQyLpVe)
- 通知群：「nlaut 测试自动化通知」（公开，搜索加入）
- cron 脚本：`~/.hermes/scripts/nlaut_feishu_scan.sh`（凭据在仓库外）

## 六层架构

```
L0 规范层   AGENTS.md（命令矩阵/策略/输出模板/禁止事项，≤200 行）
L1 编排层   harness/session.py（append-only JSONL 会话日志，全程留痕可回放）
L2 生成层   pipeline/（口述→意图澄清→四元组→IR；飞书桥接 feishu_bridge.py）
L3 资产层   ir/（TestCaseIR = 唯一事实源；数据工厂；metrics/percentile P95 算子）
L4 执行层   executor/（三通道：web(Playwright) + electron(CDP) + api(httpx)）
L5 判定层   judge/（三级漏斗：确定性 → mlx-vlm → laya-mlx；置信度路由）
L6 报告层   report/（HTML + 证据链：截图/判定明细/置信度/口述溯源内嵌）
```

架构详解见 [docs/architecture.md](docs/architecture.md)。大模型上线测试 SOP 见 [docs/llm-onboarding-sop.md](docs/llm-onboarding-sop.md)。

## 前置条件

- Python 3.12 venv（`.venv/`，uv 管理）+ playwright + mlx-vlm + laya-mlx + httpx
- 系统 Chrome（Playwright 直驱，无需下载浏览器）
- 模型权重（已下载）：`~/.cache/nlaut-models/Qwen2.5-VL-3B-Instruct-4bit`（3GB）+ HF 缓存 `aac6fef/laya-multilingual-mlx`（644MB）
- 需联网下载模型时走系统代理 `127.0.0.1:7897`（HF 域名直连不通）
- 大模型测试需 API Key（存环境变量，不进 git）

> **全新部署？** 参阅 [**安装指南**](INSTALL.md)（4 步 / 30 分钟，含权重下载）或 [GitHub Wiki: Installation](https://github.com/benkya/nlaut/wiki/Installation)。

## 质量门禁

- 任何框架改动：`make verify` 全绿（lint + 99 项测试）
- 任何判定引擎变更：`pytest -m mlx`（金标准回归）必须通过
- 用例变更走 git（IR 即资产，commit 即审计记录）
- `cases/` 唯一事实源，`gen/` 派生代码禁手编（当前 IR 直跑无 gen/）

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [**INSTALL.md**](INSTALL.md) | **安装指南**（4 步 / 30 分钟，含权重下载 + key 配置 + 验证） |
| [docs/architecture.md](docs/architecture.md) | 六层架构、Harness 角色映射、双通道注意、路线图 |
| [docs/ir-schema.md](docs/ir-schema.md) | IR 字段规范、步骤类型、断言类型完整表 |
| [docs/judge-protocol.md](docs/judge-protocol.md) | 判定协议、置信度路由规则、引擎接入门槛 |
| [docs/verbal-to-ir-protocol.md](docs/verbal-to-ir-protocol.md) | 口述→IR 生成协议（团队操作手册） |
| [docs/llm-onboarding-sop.md](docs/llm-onboarding-sop.md) | 大模型上线测试 SOP（注册→P0→定性→决策） |
| [docs/4p12s-probe-testcases.md](docs/4p12s-probe-testcases.md) | 4P12S 交付能力探针设计（9 步端到端） |
| [docs/roadmap.md](docs/roadmap.md) | 演进规划（M0.5→M3） |
| [AGENTS.md](AGENTS.md) | Agent 行为规范（命令矩阵/策略/禁止事项） |
| [GitHub Wiki](https://github.com/benkya/nlaut/wiki) | 9 页在线 Wiki（架构/IR/判定/口述/SOP/飞书/路线图/Agent 约定） |

## 路线图

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| M0 | IR 模型 + Judge 协议 + 基础设施 | ✅ |
| MVP | Web 执行器 + 报告 + CLI + 演示系统 | ✅ 2026-09-23 |
| M1-半 | 口述→IR 生成协议 + 飞书链路 + LLM 通道 | ✅ 2026-09-24 |
| v0.2.7 | P95 TTFT + 推理模型 TTFT 语义修正 + 否定语境豁免 | ✅ 2026-09-28 |
| v0.2.8 | 口述携带地址+key（临时模型零预配置） | ✅ 2026-09-24 |
| v0.2.9 | 统一报告：对话能力+4P12S 合并 + 上线结论卡 + 失败用例请求/返回展示 | ✅ 2026-09-29 |
| M1 | `pipeline/` 框架内自动生成（接模型 API） | 待做 |
| M2 | IR→pytest 代码生成 + 环境矩阵 + 自愈 | 待做 |
| M3 | 趋势报告 + 并行执行 + MCP server | 待做 |

## License

MIT
