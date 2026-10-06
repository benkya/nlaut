# nlaut 自然语言自动化测试框架 — 技术汇报材料

> **版本**：v0.3.0  
> **日期**：2026-09-30  
> **作者**：张鹏（资深质量经理 / 测试工程师）  
> **仓库**：https://github.com/benkya/nlaut  
> **Wiki**：https://github.com/benkya/nlaut/wiki

---

## 一、设计初衷

### 1.1 问题背景

公司 AI Agent 应用每次接入新大模型，上线前需要人工评估模型能力。现有测试方式存在三个核心痛点：

| 痛点 | 表现 | 影响 |
|------|------|------|
| **测试人员时间占用大** | 人工逐条设计用例、执行、判定，一个模型测一轮需要 2-3 人天 | 人力成本高，迭代慢 |
| **随机测试遗漏多** | 不同测试人员关注点不同，同一模型测两轮结果不可比 | 质量不可控 |
| **判定口径因人而异** | "模型回答是否正确"依赖主观判断，无量化标准 | 结论不可信 |

### 1.2 设计目标

构建一套**自然语言驱动**的自动化测试框架，实现：

1. **口述 → 用例 → 执行 → 判定 → 报告** 全链路自动化
2. **判定口径统一**：三级判定漏斗（确定性优先 → AI 判定兜底 → 置信度路由转人工）
3. **多被测对象**：大模型 API + Web 应用 + Electron 桌面端，共用同一套用例 IR
4. **数据不出本机**：AI 判定引擎跑在本地 Apple Silicon 芯片上，零远程依赖

### 1.3 核心设计决策

| 决策 | 理由 | 代价 |
|------|------|------|
| 用例 IR（YAML）作为唯一事实源 | 可 diff、可审计、不依赖代码生成 | 需要手写或生成 IR |
| 判定引擎分层（确定性 → VLM → Laya） | 90%+ 用例走零 AI 成本的确定性判定 | 需要维护三级引擎 |
| Laya 作为最终语义判定引擎 | 0 输出 token、13ms 级延迟、本地推理 | 需要 Apple Silicon 硬件 |
| 置信度路由（≥0.9 自动 / 0.5-0.9 人工） | AI 判定不绝对，保留人工仲裁通道 | 少量用例需人工处理 |

---

## 二、框架架构

### 2.1 六层架构总览

```
┌─────────────────────────────────────────────────────────┐
│  L0 规范层   AGENTS.md（命令矩阵/策略/禁止事项）          │
├─────────────────────────────────────────────────────────┤
│  L1 编排层   harness/session.py（JSONL 会话日志，可回放）  │
├─────────────────────────────────────────────────────────┤
│  L2 生成层   pipeline/（口述→IR 生成）+ feishu_bridge.py  │
│             （飞书口述表自动识别 → IR 入库 → 执行）        │
├─────────────────────────────────────────────────────────┤
│  L3 资产层   ir/（TestCaseIR 唯一事实源）                  │
│             cases/ 132 条用例（API 93 + Web 32 + Elec 7） │
│             data/datasets.yaml + metrics/percentile.py   │
├─────────────────────────────────────────────────────────┤
│  L4 执行层   executor/（三通道）                           │
│             ├─ channels/api.py     大模型 API（httpx）     │
│             ├─ channels/web.py     Web 应用（Playwright）  │
│             └─ channels/electron.py Electron CDP          │
├─────────────────────────────────────────────────────────┤
│  L5 判定层   judge/（三级漏斗）                            │
│             ├─ deterministic.py  确定性断言（1°，零 AI）  │
│             ├─ vlm_mlx.py         视觉判定（2°，Qwen-VL） │
│             └─ laya.py            语义判定（3°，Laya-MLX）│
│             └─ arbiter.py         置信度路由               │
├─────────────────────────────────────────────────────────┤
│  L6 报告层   report/render.py（HTML + 证据链 + 上线结论卡）│
│             scripts/combined_report.py（统一报告生成器）   │
└─────────────────────────────────────────────────────────┘
```

### 2.2 框架规模

| 维度 | 数值 |
|------|------|
| Python 代码 | 3,361 行 |
| 用例 IR | 132 条（API 93 + Web 32 + Electron 7） |
| 元测试 | 99 项（全绿） |
| 判定引擎 | 3 级（确定性 448 行 + VLM 124 行 + Laya 141 行） |
| 执行通道 | 3 个（API 328 行 + Web 147 行 + Electron 104 行） |
| 预配置模型 | 15 个 |
| GitHub commit | 63 个 |
| 文件总数 | 248 个 |

---

## 三、三级判定漏斗（核心设计）

### 3.1 设计原理

测试用例的断言判定采用**漏斗式分层**——从低成本高确定性的判定逐级升级到高成本语义判定，每层只处理上层无法覆盖的场景：

```
用例执行 → Evidence 证据包
    │
    ▼
1° 确定性断言（deterministic.py，448 行）
    │  零 AI 成本，微秒级，value≥0.5 直接裁决
    │  覆盖：文本匹配/关键词/JSON schema/工具调用/延迟/TTFT/字数
    │  覆盖率：~90% 用例在此层终结
    ▼ 未覆盖的场景
2° VLM 视觉判定（vlm_mlx.py，124 行）
    │  本地推理（Qwen2.5-VL-3B），单图 5-6s，温度恒 0
    │  覆盖：页面截图 yes/no 判定
    │  覆盖率：~5% 用例在此层终结
    ▼ 未覆盖的场景
3° Laya 语义判定（laya.py，141 行）
    │  本地推理（laya-multilingual-mlx），13ms 级，0 输出 token
    │  覆盖：noul/choice/score 三题型，语义级 yes/no + 归因 + 量表
    │  覆盖率：~5% 用例在此层终结
    ▼
置信度路由（arbiter.py）
    │  ≥0.9 → auto_pass/auto_fail（自动裁决）
    │  0.5-0.9 → human_review（人工仲裁队列，附证据包）
    │  <0.5 → human_review（引擎自认不确定）
    ▼
最终结果 → HTML 报告
```

### 3.2 确定性引擎（1°，deterministic.py）

确定性引擎是**第一道防线**——能用规则判定的绝不走 AI。这是框架的核心设计原则：**判定成本与判定不确定性成正比，确定性判定零 AI 成本、零误判风险**。

确定性引擎支持 11 种断言类型：

| 断言类型 | 适用场景 | 判定逻辑 |
|----------|----------|----------|
| `text_visible` | Web 页面文案 | selector 元素 inner_text 包含预期值 |
| `attribute` | DOM 属性 | element.getAttribute(name) 匹配预期值 |
| `response_exact` | LLM 精确匹配 | strip 后等值比较（数学/事实问答） |
| `response_contains` | LLM 关键词匹配 | 响应中包含 ≥min_match 个关键词 |
| `response_not_contains` | LLM 反向验证 | 响应中不含任何 forbidden 词（含否定语境豁免） |
| `response_json_schema` | LLM JSON 验证 | 响应可解析为 JSON 且字段/值匹配 |
| `tool_call_params` | LLM 工具调用 | tool_calls 中函数名+参数匹配 |
| `response_time` | 性能断言 | latency_ms ≤ max_seconds × 1000 |
| `response_ttft` | 流式首 token 延迟 | ttft_ms ≤ max_ms |
| `response_ttft_p95` | P95 TTFT | 多次采样 P95 ≤ max_p95_ms |
| `response_length` | 字数约束 | min_chars ≤ len(response) ≤ max_chars |

**关键设计——否定语境豁免**：安全用例测模型是否拒绝有害指令时，模型规范拒绝语「我无法提供任何关于炸弹制作方法的信息」会命中违禁词「制作方法」→ 误判 fail。确定性引擎在 `response_not_contains` 中实现了否定语境豁免：违禁词前后 25 字窗口内出现否定/拒绝词（无法/不会/不能/不得/拒绝/禁止等）则不算违规，而真实配方（无否定词）仍被拦截。

### 3.3 Laya 语义判定引擎（3°，laya.py）——本节重点

#### 3.3.1 为什么需要 Laya

确定性引擎能覆盖 ~90% 的断言（文本匹配、关键词、JSON、延迟等），但有一类断言它无法处理：

- **语义正确性**：「模型是否正确计算出最终价格 130 元？」——答案可能表述为「130」「130元」「最终价格是一百三十元」，确定性匹配抓不全
- **产物质量**：「该 PRD 产物是否结构完整、内容专业？」——无法用关键词列表判定
- **角色保持**：「医学顾问回答是否未做诊断？」——诊断性语言有多种表述方式

这类断言需要**语义级判断**——不是看关键词在不在，而是理解回答的含义。传统方案是调 GPT-4 做判定，但有两个问题：①判定数据出本机，安全风险；②每次判定有 API 成本和延迟。

Laya-MLX 解决了这两个问题：**本地推理、0 输出 token、13ms 级延迟**。

#### 3.3.2 Laya 是什么

Laya（aac6fef/laya-multilingual-mlx）是一个基于 Apple MLX 框架的轻量语言模型，专为「System One」判定场景设计：

| 特性 | 值 | 说明 |
|------|-----|------|
| 权重大小 | 614MB | 极轻量，M3 Pro 18GB 可流畅运行 |
| 推理方式 | logits 前向传播 | **不生成输出 token**，只算各选项概率 |
| 延迟 | ~13ms | 比传统 LLM 生成快 100 倍 |
| 题型 | noul / choice / score | 三种判定题型覆盖不同场景 |
| 运行位置 | 本机 Apple Silicon | 数据不出本机，零远程依赖 |
| 权重位置 | `~/.cache/huggingface/hub/models--aac6fef--laya-multilingual-mlx/` | 下载后永久离线 |

#### 3.3.3 三种判定题型

Laya 的核心 API 是 `agent.system_one(state, questions)`——传入状态文本和问题定义，一次前向传播返回各选项概率。

**noul 题型（语义 yes/no）**：

```python
# 问题定义
{"type": "noul", "instructions": "模型是否正确计算出最终价格130元？只答 yes 或 no。"}

# 返回
{"noul": 0.95, "confidence": 0.95}
# noul=概率值（0-1），confidence=校准置信度 max(p, 1-p)
```

**choice 题型（归因分类）**：

```python
# 问题定义
{"type": "choice", "instructions": "该回答属于哪种质量等级？",
 "criteria": {"优秀": "优秀", "合格": "合格", "不合格": "不合格"}}

# 返回
{"choice": "合格", "probabilities": {"优秀": 0.1, "合格": 0.85, "不合格": 0.05}}
# confidence = 选中项概率
```

**score 题型（量表评分）**：

```python
# 问题定义
{"type": "score", "instructions": "该产物专业程度评分",
 "criteria": ["完全不符合", "基本不符合", "部分符合", "基本符合", "完全符合"]}

# 返回
{"score": 3.8, "probabilities": {0: 0.01, 1: 0.04, 2: 0.1, 3: 0.6, 4: 0.25}}
# confidence = 分布峰值
```

#### 3.3.4 Laya 在测试流程中的调用链

```
用例执行 → Evidence 证据包
    │
    ├─ llm_response（模型回答全文，截取 600 字）
    ├─ conversation（用户输入 prompt）
    ├─ tool_calls（工具调用）
    ├─ latency_ms（延迟）
    └─ ttft_p95_ms（P95 首 token 延迟）
    │
    ▼ render_state() 渲染为纯文本
    │
    │  state = "case: tc_d04_p0_001\n用户输入: 计算...\n模型回答: 130元\n响应延迟: 5231ms"
    │
    ▼ agent.system_one(state, {"q1": question_def})
    │
    │  一次前向传播，0 输出 token
    │  只取各选项 logits → softmax → 概率 → confidence
    │
    ▼ Verdict(engine="laya", value=0.95, confidence=0.95)
    │
    ▼ arbiter.route(verdict, threshold=0.9)
    │
    │  confidence ≥0.9 → auto_pass（自动通过）
    │  0.5 ≤ confidence <0.9 → human_review（转人工）
    │  confidence <0.5 → human_review（不确定）
    │
    ▼ 报告（含置信度、引擎、证据链）
```

#### 3.3.5 Laya 在实测中的表现

以 DeepSeek-V4-Flash-0731 的 4P12S 测试为例，9 步端到端交付能力探针中，Laya 判定结果如下：

| 步骤 | 确定性门禁 | Laya noul 判定 | 置信度 | 路由结果 |
|------|------------|---------------|--------|----------|
| step2 需求登记 | ✅ 结构门禁通过 | noul=0.82 | 0.82 | human_review |
| step3 PRD | ✅ 结构门禁通过 | noul=0.93 | 0.93 | auto_pass |
| step4 用户故事 | ✅ 结构门禁通过 | noul=0.85 | 0.85 | human_review |
| step5 技术设计 | ✅ 结构门禁通过 | noul=0.91 | 0.91 | auto_pass |
| step6 验证计划 | ✅ 结构门禁通过 | noul=0.94 | 0.94 | auto_pass |
| step7 任务拆分 | ✅ 结构门禁通过 | noul=0.96 | 0.96 | auto_pass |
| step8 实现 | ✅ pytest 通过 | noul=0.83 | 0.83 | human_review |
| step9 集成测试 | ✅ pytest 通过 | noul=0.92 | 0.92 | auto_pass |
| step10 E2E | ✅ pytest 通过 | noul=0.95 | 0.95 | auto_pass |

**关键观察**：
1. 确定性门禁（结构 regex + pytest）先过滤——过了才进 Laya
2. Laya 对高质量产物给出高置信度（step7 0.96），对中等质量产物给出中置信度（step4 0.85）
3. 3 条转人工（step2/4/8）——人工查看产物后判定为通过，更新报告
4. **Laya 的判定不是终点，是人工的筛选器**——高置信度自动通过，低置信度交人工

#### 3.3.6 Laya vs 传统 LLM 判定对比

| 维度 | 传统方案（调 GPT-4 判定） | Laya 方案 |
|------|--------------------------|-----------|
| 判定延迟 | 2-5 秒 | 13ms |
| 成本 | 每次判定消耗 API token | 0（本地推理） |
| 数据安全 | 判定数据出本机 | 数据不出本机 |
| 输出方式 | 生成 token（需解析） | 0 输出 token（只读 logits） |
| 置信度 | 需额外 prompt 或 logprob | 原生校准 confidence |
| 部署依赖 | 需联网 + API key | 权重下载后永久离线 |

---

## 四、三通道执行

### 4.1 大模型 API 通道（api.py，328 行）

通过 httpx 调用 OpenAI 兼容接口，支持：

| 能力 | 说明 |
|------|------|
| 单轮调用 | `api_call`：发送 prompt，获取响应 |
| 多轮对话 | `api_followup`：在已有上下文基础上追加消息 |
| 工具调用 | `api_tool_call`：发送 prompt + tools 定义，验证模型是否正确调用工具 |
| 流式调用 | `api_stream`：stream=true，采集首 token 延迟（TTFT）+ 内容完整性 |
| 重复采样 | `repeat: N`：跑 N 次取 P95（消除单次 TTFT 的边缘波动） |
| 推理控制 | `reasoning_effort`：推理模型思考强度（low/medium/high） |
| 网络隔离 | `trust_env=False`：内网地址绕过系统代理 |

**关键设计——TTFT 语义**：推理模型（GLM-5.3-Flash / DeepSeek-V4-Flash）先输出 `reasoning_content`（思考过程），再输出 `content`（正文）。如果只认 content token，3 秒上限会把所有推理模型误判为 TTFT 超标。框架改为：首个含 `content` **或** `reasoning_content` 的 chunk 即计时。

### 4.2 Web 应用通道（web.py，147 行）

通过 Playwright 直驱系统 Chrome（无需下载浏览器），支持 nav/fill/click/wait_visible/select_option 五种声明式步骤，执行后自动采集截图 + DOM 状态。

### 4.3 Electron CDP 通道（electron.py，104 行）

通过 `connect_over_cdp` 连接已运行的 Electron 应用（需以 `--remote-debugging-port` 启动），复用 Web 通道的步骤翻译和 DOM 采集。实测连接 DSH（DeepSeek Harness）开发模式 Electron 成功，56 个可交互元素全部可操作。

---

## 五、用例 IR（唯一事实源）

### 5.1 IR 结构

```yaml
id: tc_d04_p0_001              # 必填，唯一标识
title: 多步算术                  # 必填，人类可读标题
source: "大模型测试用例集 D04-P0-001"  # 必填，原始口述文本（追溯锚点）
priority: P0                    # P0|P1|P2
channel: api                   # web | electron | api
quad: {actor: 大模型, action: 数学计算, object: 数学问题}
data_ref: d04_multi_step       # 数据引用（数据走 data/datasets.yaml，用例不内嵌）
steps:
  - {action: api_call, prompt: "$prompt"}  # $var 引用数据集字段
assertions:
  - kind: response_contains    # 确定性：关键词匹配
    keywords: ["130"]
    min_match: 1
  - kind: noul                 # Laya：语义判定兜底
    question: "模型是否正确计算出最终价格130元？只答 yes 或 no。"
    threshold: 0.9
postconditions: []
```

### 5.2 用例分布

**对话能力（93 条，18 维度）**：

| 维度 | P0 | P1 | P2 | 测什么 |
|------|-----|-----|-----|--------|
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
| stream | 3 | — | — | 首 token 延迟/内容完整/P95 稳定性 |
| creative | 1 | 1 | — | 结构化创意 |
| lang | — | 2 | — | 多语言 |
| perf | 1 | — | — | 性能基准 |

**4P12S 交付能力（9 步）**：

| 步骤 | 名称 | 判定方式 |
|------|------|----------|
| step2 | 需求登记 | 结构门禁（regex）+ Laya noul |
| step3 | PRD | 结构门禁（regex）+ Laya noul |
| step4 | 用户故事 | 结构门禁（regex）+ Laya noul |
| step5 | 技术设计 | 结构门禁（regex）+ Laya noul |
| step6 | 验证计划 | 结构门禁（regex）+ Laya noul |
| step7 | 任务拆分 | 结构门禁（regex）+ Laya noul |
| step8 | 实现 | 结构门禁 + **pytest 门禁** + 思考泄漏检测 + Laya noul |
| step9 | 集成测试 | 结构门禁 + **pytest 门禁** + 思考泄漏检测 + Laya noul |
| step10 | E2E 测试 | 结构门禁 + **pytest 门禁** + 思考泄漏检测 + Laya noul |

**Web/Electron 用例（39 条）**：Web 32 条（登录/任务列表/查询/翻页/新增）+ Electron 7 条（GienCoderWorkbench 工作台全覆盖）。

---

## 六、统一报告

### 6.1 报告结构

```
┌──────────────────────────────────────┐
│  上线结论卡（✅推荐 / ⚠️有条件 / ❌不建议）│
│  + 失败项汇总                          │
│  + 测试范围 + 判定引擎说明 + 上线规则    │
├──────────────────────────────────────┤
│  KPI 卡片（对话能力通过率 + 4P12S 通过率）│
├──────────────────────────────────────┤
│  用例明细表（每条用例）：               │
│  ├─ case_id / title / status          │
│  ├─ 断言明细（assertion/engine/value/   │
│  │   confidence/status）              │
│  ├─ 📥 请求（system+user，可折叠）     │
│  │   （仅 failed/need_review 展示）    │
│  ├─ 📤 模型返回（截断 2000 字，可折叠） │
│  │   （仅 failed/need_review 展示）    │
│  └─ 🔧 工具调用（JSON 格式化，可折叠）  │
└──────────────────────────────────────┘
```

### 6.2 上线结论规则

| 条件 | 结论 | 报告样式 |
|------|------|----------|
| P0 ≥90% 且 4P12S ≥80% | ✅ 推荐上线 | 绿色 |
| P0 ≥90% 且 4P12S ≥60% | ⚠️ 有条件上线 | 黄色 |
| 否则 | ❌ 不建议上线 | 红色 |

### 6.3 失败用例请求/返回展示

报告中 failed 和 need_review 的用例可折叠查看：
- **📥 请求**：system + user 消息全文（便于核对 prompt 是否正确）
- **📤 模型返回**：模型实际回复全文（截断 2000 字，便于人工定性失败原因）
- **🔧 工具调用**：JSON 格式化展示（便于核对工具参数）

通过的用例不展示请求/返回（报告不臃肿）。

---

## 七、实测数据

### 7.1 三个模型测试结果对比

| 指标 | DeepSeek-V4-Flash | GLM-5.3-Flash | DeepSeek-V4-Flash-0731 |
|------|-------------------|---------------|-------------------------|
| **内网地址** | 10.62.64.38:30841 | 10.62.79.254:32528 | 10.102.76.13:32751 |
| **模型类型** | 推理模型 | 推理模型 | 非推理模型 |
| **P0 对话通过率** | 89.1%（41/46） | 95.7%（44/46）* | 93.5%（43/46） |
| **4P12S 交付通过率** | 88.9%（8/9） | 33.3%（3/9） | 100%（9/9）** |
| **综合结论** | ⚠️ 有条件上线 | ❌ 不建议上线 | ✅ 推荐上线 |
| **P0 批次耗时** | 384s | 2193s（39 分钟） | 134s（2 分钟） |
| **4P12S 耗时** | 102s | 中断（504 超时 ×3） | 107s |
| **P95 TTFT** | 2359ms | 5614ms | 648ms |
| **4P12S 504 超时** | 0 步 | 3 步 | 0 步 |

> *GLM-5.3-Flash 的 P0 通过率 95.7% 是在 `reasoning_effort=low` 下取得的；不设 low 时为 91.3%。
> **DeepSeek-V4-Flash-0731 的 4P12S 100% 是人工仲裁后更新（3 条 need_review 判定为 passed）。

### 7.2 失败项定性分析

测试中发现的失败分为四类：

| 定性类别 | 含义 | 典型案例 | 处理方式 |
|----------|------|----------|----------|
| **A 框架/判定缺陷** | 断言或判定引擎 bug 造成假失败 | tc_instruct_p0_002 断言词表从安全用例误复制 | 修框架 |
| **B 用例约束偏差** | 用例约束与模型输出格式不匹配 | tc_d06 JSON schema 格式偏差（内容对但格式差） | 放宽断言 |
| **C 模型真实缺陷** | 模型行为不符合预期 | GLM-5.3-Flash 4P12S 3 步 504 网关超时 | 记录缺陷 |
| **D flaky（不稳定）** | 创意类输出波动，重放通过 | tc_creative_p0_001 重放 3/3 过 | 不阻塞 |

### 7.3 GLM-5.3-Flash 的 504 超时问题

GLM-5.3-Flash 是推理模型，每条用例先思考 30-107 秒再输出正文。4P12S 探针中 step3（PRD）、step8（实现）、step10（E2E）的思考时间超过内网网关 180 秒超时限制，返回 504。

通过 `reasoning_effort=low` 参数将思考强度降低后：
- API 响应时间从 88 秒降到 14.5 秒（快 6 倍）
- reasoning_tokens 从 50 降到 2
- step3 从 504 超时变为通过

这验证了框架的 `reasoning_effort` 参数设计——推理模型在测试场景下可用降低思考强度来避免网关超时。

### 7.4 DeepSeek-V4-Flash-0731 的 Laya 仲裁

DeepSeek-V4-Flash-0731 的 4P12S 9 步全部通过确定性门禁（结构 regex + pytest），但 3 步在 Laya 语义判定中置信度 <0.9 转人工：

| 步骤 | Laya 置信度 | 人工查看产物 | 最终判定 |
|------|------------|-------------|----------|
| step2 需求登记 | 0.82 | 产物结构完整、内容专业 | ✅ passed |
| step4 用户故事 | 0.85 | 用户故事格式规范、验收标准明确 | ✅ passed |
| step8 实现 | 0.83 | 代码正确、pytest 通过、无思考泄漏 | ✅ passed |

这说明 Laya 的 human_review 机制有效——**不是判定失败，是判定「需要人看一眼」**。人工查看后确认通过，更新报告结论从「有条件上线」升级为「推荐上线」。

---

## 八、飞书协作链路

### 8.1 全链路自动化

```
飞书口述表 → cron 每 5 分钟扫描
    │
    ├─ 识别口述类型（Web 用例 / LLM 能力测试）
    │
    ├─ LLM 能力测试：自动识别模型名 + 地址 + key
    │   → 生成 IR 批次声明 → 入库 cases/
    │   → 执行 P0 46 条 → 报告发群 → 表格写回
    │   （零人工干预，「待生成」状态即自动走全流程）
    │
    └─ Web 用例：生成 IR 草案 → 等用户改「已确认」→ 执行
       （人工卡点，防止误生成）
```

### 8.2 口述协议

完整口述句式：

> 测试<模型名>模型能力，模型地址是：<URL>，key是：<KEY>

- 模型名（已预配置）→ 用 models.yaml 预设地址 + env key
- 口述携带地址+key → 临时模型零预配置，key 不落盘
- 口述含「全量/P1」→ 跑 81 条；默认跑 P0 46 条
- 口述含维度名（如「重点测工具调用」）→ 过滤该维度

### 8.3 安全三原则

1. key 只存内存，不写入任何持久化文本
2. 经环境变量注入子进程（防 `ps` 泄漏）
3. 表格/报告中一律打码 `key:***后4位`

---

## 九、质量保障

### 9.1 框架自身测试

| 层级 | 内容 | 测试数 |
|------|------|--------|
| Layer A 框架元测试 | IR schema、判定路由、会话/证据基础设施 | 99 项 |
| Layer B 判定金标准 | 标注截图回归（VLM + Laya 引擎改动必跑） | 6 项（-m mlx） |
| Layer C 业务用例 | cases/ IR 唯一事实源 | 132 条 |

### 9.2 质量门禁

- 任何框架改动：`make verify` 全绿（lint + 99 项测试）
- 任何判定引擎变更：`pytest -m mlx`（金标准回归）必须通过
- 用例变更走 git（IR 即资产，commit 即审计记录）
- `cases/` 唯一事实源，`gen/` 派生代码禁手编

---

## 十、落地执行指南

### 10.1 全新部署（4 步 / 30 分钟）

```bash
# 1. 克隆 + 装依赖
git clone https://github.com/benkya/nlaut.git && cd nlaut
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && playwright install chromium

# 2. 下载本地判定模型权重（3.5GB，一次性）
bash scripts/setup_models.sh

# 3. 配置 API Key
cp .env.example ~/.hermes/.env && vi ~/.hermes/.env

# 4. 验证
python -m pytest -q                    # 元测试
python -m pytest -m mlx -v             # AI 引擎回归
```

### 10.2 测试一个大模型

```bash
# 对话能力 + 4P12S 统一报告
.venv/bin/python scripts/combined_report.py --model <模型名>

# 只跑对话能力
.venv/bin/python -m nlaut.cli --model <模型名> --api-only --priority P0

# 推理模型降思考强度
.venv/bin/python scripts/combined_report.py --model glm-5.3-flash --reasoning-effort low
```

### 10.3 飞书零动作测试

在飞书口述表填一行：
> 测试DeepSeek-V4-Flash-0731模型能力，模型地址是：http://10.102.76.13:32751/inference/ljcp9fuk/v1，key是：xxx

cron 自动扫描 → 跑 P0 → 报告发群 → 表格写回。全程零额外动作。

---

## 十一、总结

nlaut 框架的核心价值：

1. **判定口径统一**：三级漏斗（确定性 → VLM → Laya）+ 置信度路由，90% 用例零 AI 成本自动判定，5% 交 Laya 高置信度自动判定，5% 转人工附完整证据包
2. **Laya 引擎创新**：本地推理、0 输出 token、13ms 延迟、原生置信度——解决了传统 LLM 判定的成本、延迟、安全三重问题
3. **实测验证**：3 个大模型真实测试（推理模型 + 非推理模型），从 91.3% 到 100% 4P12S 通过率，给出了可量化、可对比、可追溯的上线结论
4. **全链路自动化**：飞书口述 → IR 生成 → 执行 → 三级判定 → 带证据链报告 → 发群，零人工干预

框架已在 GitHub 开源（benkya/nlaut），含 10 页 Wiki、安装指南、完整文档，可在新机器上 30 分钟内部署完成。
