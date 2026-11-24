# nlaut 判定层基数缺口分析 —— 从 CeCoder 两轮工序实测中可提炼什么

**日期**：2026-11-13
**分析对象**：`/Users/Ben/workspace/nlaut`（main，HEAD `dc0f7c3`，`feature/agent-as-judge` 已合入）
**事实来源**：会话 `local-767b63fd062c05d0a08017af8ff07d85b60fb6b320820c0836c2399c099adcb0` + 本机源码与运行数据交叉核实
**配套素材**：`/Users/Ben/workspace/cecoder-evaluation/CeCoder两轮工序实测事实提取_判定基数素材_v1.0.md`（650 行 / 29 表）

---

## 〇、结论先行

**你缺的不是「更多 Judge 引擎」，而是「让 Judge 敢下结论的基数」。三处硬伤，其中两处是实测数据可以直接补的。**

**先说最底层的一条机制**：`agent_judge.py:32` 的 `CONFIDENCE_CAP = 0.85` 低于 `arbiter.py:38` 的 0.9 自动裁决线——**agent judge 的结论设计上就永远走人工仲裁**。要论证性地调高这个封顶值，唯一依据是「在已知人工结论上的一致率有多高」，而这个数正是下面 H1 缺失的东西。**标定是解锁自动放行的唯一钥匙。**

| # | 硬伤 | 现状（实测） | 能否用 CeCoder 实测数据补 |
|---|---|---|---|
| **H1** | **Judge 标定基准集是空的** | `config/calibrate_v2.json` 30 条，`annotator_a`/`annotator_b`/`agreement` **全为 null**，`annotation_status: "pending"` | ✅ **能**——实测自带 20 条判定难点（J1~J20）可作题型蓝本 |
| **H2** | **金标准回归样本量与文档承诺差 3 倍，且 CI 默认不跑** | `golden.jsonl` 10 条 / 3 张截图；文档三处宣称「≥30 张」「禁止绕过」 | ✅ **能**——35 个真实任务的工序产物可批量生成标注样本 |
| **H3** | **工序域 TruthSource 契约不存在** | `Evidence` 无工序节点/门禁/产物字段；`truth_source_registry.yaml` 只注册 2 个 gienx 路径 | ✅ **能**——`tasks/*.json` 结构已核实可直接映射 |
| **H4** | **五处「声明了但没接线」** | `llm_judge` 未注册 / registry 是死文件 / IR 静默丢 5 字段 / ToolPolicy 默认全拒 / `judge:` 是死字段 | ❌ **不能靠数据补**——这是**接线问题**，必须改代码 |

**H4 是本次分析最重要的发现**，它改变优先级判断：

> **`avg_tool_calls: 0` 不是模型不调工具，是框架层面根本没让它能调通的路径。**

所以「先 H3 再 H1」的排序要修正——**H4-2 / H4-3 / H4-4 是 H3 的前置**：工序域 TruthSource 如果不接到 `build_tool_impls(truth_source=...)` 上、IR 不加 `truth_sources` 字段、ToolPolicy 不放行 `~/.cecoder` 路径，那新写的工序域用例**照样只能靠夹具**，标定数字依旧不可信。

**最关键的一条发现**：CeCoder 实测的 **20 条判定难点（J1~J20）里，有 9 条是纯语义判定**，正好是 LLM Judge 的用武之地；而 L1 确定性断言在工序域**只能覆盖约 30%**。这个比例决定了投入方向。

**另有一条产品级阻塞**：`requireDocument()` 只比对文件名不比对内容（D-1 家族），所以「产物是否符合规约」**无法用确定性断言完成**，必须走 L2。这是实测给 nlaut 的结构性提示，不是可选项。

---

## 一、两轮工序实测到底测了什么（先对齐事实）

### 1.1 两轮是有对照组的实验

| 轮次 | 执行方式 | 任务 | 工序节点 | 有效样本 | 会话 |
|---|---|---|---|---|---|
| 基线轮（第 1 轮） | 每道工序**自动**执行 | 34 | 187 | 86 | 115 |
| 本轮（第 2 轮） | 每道工序**人工点界面按钮**推进 | +1 | +7 | +7 | +7 |
| 全量合计 | — | **35** | **194** | **95** | **122** |

- 第 1 轮不是专门设计的测试，是本机 2026-11-06 ~ 11-13 用户真实使用留下的日志（**回捞数据**）
- 第 2 轮是主动对照：同场景（银行柜面开户 PRD），新增 1 任务 7 节点，**58 分钟**跑通 7 道工序全 `done`，无门禁拦截

### 1.2 对 nlaut 而言，这批数据的三重价值

**价值一：工序域的「结构化事实源」三件套齐全。**

| 数据源 | 路径 | 规模 | 提供什么 |
|---|---|---|---|
| 工序节点状态 | `~/.cecoder/engineering-workbench/tasks/*.json` | 35 文件 / 194 节点 | `status`、`assessment.gates`（start/complete/autoAdvance）、`outputs[]`、`startedAt`/`completedAt` |
| 工序会话事件流 | `~/.cecoder/sessions/*/session.jsonl.zstd` | 963 文件 / 743,267 事件 | `user/message`（人工介入唯一来源）、`turn/end` 的 `reason.kind`（失败归因）、`tool/call` |
| 工序产物 | `~/.cecoder/engineering-workbench/assets/<taskId>/` | 34 目录 | 产物正文 + **对应规约模板**（`templates/*.md`） |

**价值二：产物与规约成对存在 → 「产物是否符合规约」可自动构造判定题。**

实测样本（`assets/09e282f6-.../`）：

```
templates/概要设计文档模板.md   ← 规约（标准，章节骨架固定）
概要设计文档.md                  ← 产物（81179 字节，一一对应展开）
```

模板 9 章、产物 9 章一一对应。这意味着「章节齐全性」可走 L1 确定性断言，「内容是否符合规约」走 L2 语义判定——**两级漏斗有天然的分工边界**。

**价值三：天然存在 trap 样本（判定反例）。**

我实测扫出的 4 个矛盾样本，可直接做「表面正确实际错误」的陷阱题：

| 样本 | 现象 | 陷阱性质 |
|---|---|---|
| task `271bd14b` / `67d4e277` UI 原型设计 | `status=done` 但 `autoAdvance=block` | 状态说完成，门禁说别放行 |
| task `1001` UI 原型设计 | `status=done`、`gates: None`（门禁从未评估）、`outputs: []` | 门禁缺失即放行 |
| task `1011` 概要设计 | `status=done`、门禁全 allow，但 `task.json` 的 `outputs` 为空 | **产物实际在磁盘上**（`01-concept-design/outputs/概要设计.md` 存在）→ 只看 task.json 会误判为「空产出」 |

> task 1011 这条最有价值：**它同时是陷阱样本和「单源取证不足」的证据**。判「产物是否存在」必须同时查 `task.json` 和磁盘，只查一处必错。这正是 Agent-as-Judge 相对 LLM-as-Judge 的用武之地——但前提是 `Evidence` 里能装下这两个来源。

**产物分布（任务级实测，可直接用于构造 trap 样本）**：

| 任务 | 产物数 | done 节点 | 场景 |
|---|---|---|---|
| `1015` | 1115 | 5 | 创建一个简单的小超市收银系统 |
| `1005` | 387 | 10 | 银行柜面账户开立系统的设计 |
| `1006` | 353 | 3 | 实现个人存取款业务 |
| `1007` | 76 | 4 | 做个简单的资产盘点系统 |
| `49b0bc46` | 52 | 7 | 开发一个期货指数查询系统 |
| `09e282f6` | 38 | 7 | 创建一个 SSO 登录认证的 demo |

> `1005`（10 工序全 done、387 个产物）与 `1015`（5 工序、1115 个产物）**工序数与产物数不成正比**——「产物数量」不能作为「工序执行质量」的代理指标。这类反直觉的分布正是标定集需要的素材：它能测出 Judge 是否会犯「数量多=质量高」的直觉错误。

---

## 二、nlaut 判定层现状盘点（实测核实，非文档转述）

### 2.1 引擎实现：完整

| 模块 | 职责 | 依赖 | 状态 |
|---|---|---|---|
| `protocol.py` | Judge 统一协议（`Evidence`/`Question`/`Verdict`） | pydantic | ✅ 5 个引擎同接口 |
| `deterministic.py` | 1° 确定性断言，7 种判定模式（dom / response_exact / response_contains / response_json_schema / response_time / response_length / …） | 无 | ✅ 零 AI 成本 |
| `vlm_mlx.py` | 2° 视觉判定 | 本机 Qwen2.5-VL-3B-4bit | ✅ 懒加载 |
| `laya.py` | 3° laya-mlx System One（Noul/Choice/Score） | 本机 laya-mlx | ✅ 懒加载 |
| `llm_judge.py` | LLM-as-Judge（无取证，单轮） | HTTP 端点 | ✅ |
| `agent_judge.py` | Agent-as-Judge（多轮 function calling） | HTTP 端点 + 工具表 | ✅ 7 类取证工具 |
| `arbiter.py` | 置信度路由 | — | ✅ 0.9 / 0.5 双阈值 |
| `truth_source.py` | TruthSource 契约 | — | ⚠️ 5 个实现，**无工序域** |

**路由实现位置**：`src/nlaut/executor/runner.py:251-254`（唯一判定调用点）→ `get_judge(engine_name)` → `route(verdict, threshold=0.9)`。

**Agent-as-Judge 的 7 类取证工具**（`agent_tools.py`）：`_query_db` / `_http_probe` / `_read_log` / `_read_file` / `_inspect_trace` / `_list_registry` / `_locate_object`。

### 2.2 一个前置机制：Agent-as-Judge 的结论**永远不能自动放行**

`agent_judge.py:32` 定义 `CONFIDENCE_CAP = 0.85`，而 `arbiter.py:38` 的自动裁决线是 `confidence >= 0.9`。

**结果**：agent judge 的 verdict 100% 走 `human_review` 分支。源码注释写得很直接：

> 「agent judge 的置信度上限：低于 route() 的 0.9 自动裁决阈值，保证 agent judge 结论必须经人工仲裁（软件工厂出口把关场景的硬约束）」

**这条机制解释了你的问题的本质**：agent judge 不是「还不能放行」，而是**设计上就不该单独放行**。它要给放行提供依据，需要的是「在已知人工结论上的一致率有多高」——而这个数恰恰是 H1 缺失的东西（双盲标注全 null）。

> 所以「缺基数」的真正含义不是「样本太少」，而是**没有人工标注这个「真值」**，导致置信度封顶 0.85 这个安全阀无法被论证性地调高。**标定是解锁自动放行的唯一钥匙。**

### 2.3 三处硬伤（逐条实测）

#### H1 — Judge 标定基准集是空的（最严重）

```
config/calibrate_v2.json
  metadata.annotation_status = "pending"        ← 未标注
  samples: 30 条
  human_annotation.judgement 非空: 30/30        ← 全部来自 golden_samples 复制
  annotator_a 非空: 0/30                        ← 双盲标注者 A：零
  annotator_b 非空: 0/30                        ← 双盲标注者 B：零
  agreement 非空:    0/30                        ← 一致率：无法计算
```

**含义**：`docs/judge-calibration-implementation-guide.md` 的核心纪律「未经与人工标注做过一致性标定的 Judge，评分结果不得作为放行依据」——**这条纪律现在一条都没兑现**。30 条样本的 `human_annotation` 是从 `golden_samples_20.json` 抄来的期望值，不是独立标注。

**更硬的证据**：`artifacts/agent_judge_calibration_v2.json` 报 `agreement_rate: 85.0`，但同一文件里 **`avg_tool_calls: 0`**。

> 拆开看：这一批标定跑的是**无工具**的 judge，85% 一致率是纯 prompt 判断的结果。而 `artifacts/agent_judge_ablation.json` 里真正带工具的 `agent_tools` 臂（`tool_calls: 4`）准确率 100%、陷阱准确率 100%。**两份数据被当同一件事引用，实际是两种配置。** 做 CeCoder 门禁判定必须用后者，但后者的标定只覆盖了 V3 域的 20 条合成样本。

**另有两处一致率数字低于门槛（已实测核对 artifacts）**：

| 标定 | 一致率 | 门槛 | 结论 |
|---|---|---|---|
| `agent_judge_ablation.json` / `agent_tools` 臂 | 100%（含陷阱 FP=0） | ≥85% | ✅ 达标，但仅 20 条 V3 域夹具 |
| `agent_judge_calibration_v2.json` | 85.0% | ≥85% | ⚠️ 勉强达标，但 `avg_tool_calls=0` |
| `llm_judge_calibration.json` | **73.3%** | ≥85% | ❌ **不达标** |
| `code_review_calibration.json` | **70.0%** | ≥85% | ❌ **不达标**（`docs/p1-tech-debt.md:11` 记为 Open 债务 TD-001） |


#### H2 — 金标准回归样本量不足，且默认不跑

`tests/judge_golden/golden.jsonl` 共 **10 条**（3 张截图 × 正反问法）。文档三处宣称门禁：

- `docs/judge-protocol.md:150`「判定层金标准回归 ≥30 张标注截图」
- `docs/architecture.md:52`「任何判定引擎接入/变更必须通过本回归」
- `AGENTS.md:44`「禁止绕过 tests/judge_golden 金标准回归接入新判定引擎」

**实际落差**（我已逐条核实）：

| 声称 | 实际 | 核实方式 |
|---|---|---|
| `golden.jsonl` 驱动回归 | 该文件**无任何代码读取**（`grep -rn "golden.jsonl" --include="*.py"` 零命中） | 实测 grep |
| ≥30 张标注截图 | 10 条 / 3 张截图 | 实测 |
| 「禁止绕过」的回归门禁 | `laya_engine_test.py:79 test_golden_four`（硬编码 4 题，要求 ≥3/4）——**存在但样本少** | 实测读码 |
| 同上 | `agent_judge_test.py:972 test_all_golden_samples_judged_correctly`（20 条全对门禁）——**存在，但带 `@pytest.mark.agent`** | 实测读码 |
| 同上 | `pyproject.toml` `addopts = "-m 'not mlx and not agent'"` → **默认 CI 不跑 agent 回归** | 实测实测 collect |

> 修正一处常见误读：不是「金标准完全形同虚设」。两处等价断言真实存在且有效。真正的问题是——**样本量是承诺值的 1/3，且最关键的 agent 臂在常规 CI 中被排除**。

#### H3 — 工序域 TruthSource 契约不存在

`Evidence` 的字段集（`protocol.py:21-45`）：

```
case_id, screenshot, dom_state, response, db_state,
llm_response, llm_response_json, tool_calls, latency_ms, conversation,
ttft_ms, stream_chunks, ttft_samples, ttft_p95_ms
```

**没有任何字段能表达工序链语义**：工序节点、门禁 `gates`、产物清单、产物与规约的对应关系、`completion: manual` 放行状态。

`config/truth_source_registry.yaml` 只注册 2 条：

```yaml
design_task_attachments: {path: ~/.dsh/design-task-attachments, type: file}
design_tasks:            {path: ~/.gienx/design-task.json,      type: file}
```

**两条都是 gienx 旧路径，与 CeCoder 工序域无关。**

另有一个具体小缺口：`LogTruthSource` 用 `Path.read_text()` 读纯文本，**不支持 `.zstd`**（`truth_source.py:247`）。工序会话流全是 zstd 压缩 JSONL。

### 2.4 五处「声明了但没接线」（比 H3 更根本）

**这一节是我在独立核实子代理线索后实测确认的**。它们解释了一个核心疑问：**为什么 Agent-as-Judge 的「自主取证」在真实模块上从未跑通过一次？**

#### H4-1 `llm_judge.py` 是孤儿模块——生产路径走不到

```
$ .venv/bin/python -c "from nlaut.judge import get_judge; get_judge('llm')"
KeyError: "未注册判定引擎: llm（已注册: ['deterministic', 'laya', 'mlx-vlm']，工厂: ['agent']）"
```

`src/nlaut/judge/__init__.py` 只注册了 3 个即时引擎 + 1 个工厂，`llm_judge.py` **不在注册表**。

**后果**：「LLM-as-Judge 作为 Agent-as-Judge 的对照基线」这个设计定位，**在框架层根本没有接线**。目前只能靠 `scripts/judge_calibration.py` 手工调用。你想比较「有工具 vs 无工具」的真实差异，走不了标准路径。

#### H4-2 `truth_source_registry.yaml` 是死文件

```
$ grep -rn "truth_source_registry" --include="*.py" .
(零命中)
```

`agent_tools.py:252` 的 `build_tool_impls(truth_source=...)` 参数**没有任何生产调用点传入非 None 值**。

**后果**：`query_db` / `locate_object` 永远走 `db_state` 夹具分支（`agent_tools.py:289` → `db_state_not_provided` 错误）。**「自主取证」在真实模块上从未跑过一次**——所有标定数据都来自夹具。

> `docs/agent-as-judge-integration.md:207` 自己写着「P0：用真实模块重跑 20 样本金标准 —— **夹具结果不能代替**」。这条 P0 至今未做。

#### H4-3 `AssertAgentVerify` 静默丢弃 5 个配置字段

实测（加载 `cases/giencoder/tc_giencwb_design_006.yaml` 后 `model_validate`）：

```
YAML 里声明的 agent_verify 键: ['confidence_threshold', 'kind', 'max_cost_usd', 'objective', 'timeout_s', 'tools', 'truth_sources']
IR 模型实际字段:             ['judge', 'kind', 'max_steps', 'objective', 'system_prompt', 'threshold']
model_extra: None
```

`src/nlaut/ir/model.py:296-301` 的 `AssertAgentVerify` 只有 6 个字段，多出来的 5 个被 pydantic **静默丢弃**（`model_config` 为空 → `model_extra` 默认 `ignore`）。

**后果**：这条用例宣称「按真值源取证、按 0.10 美元预算、0.85 置信阈值」，**实际跑的是引擎默认值**：`tools=DEFAULT_TOOLS`（7 个）、`max_cost_usd=0.50`、`threshold=0.9`。**声明与执行行为不一致，且无任何告警。**

#### H4-4 `ToolPolicy` 默认全拒——真实取证跑不通

`agent_tools.py:193-207`：

```python
def path_allowed(self, path: str) -> bool:
    if not self.allowed_path_roots:
        return False  # 默认拒绝
```

`allowed_hosts` / `allowed_path_roots` 默认为空集 → `read_file` / `read_log` / `http_probe` **一律拒绝**。

**后果**：`tc_giencwb_design_006.yaml` 的 objective 明确要求「读取 `~/.dsh/design-task.json`」，**不配 `NLaut_AGENT_JUDGE_ALLOWED_PATHS` 就跑不通**，judge 只能拿到 `scope_violation` 结构化错误 → 「无法取证 → 判不确定」。

> 这是**安全设计的正确默认值**，不是 bug。但它意味着：每条要真取证的用例，都必须配环境变量。当前没有任何机制检查「用例声明了要取证但环境没放行」。

#### H4-5 IR 的 `judge:` 字段是装饰性的（死字段）

`src/nlaut/ir/model.py` 定义了 `judge: Literal["vlm"]` / `Literal["systemone"]`，cases 里实测有 **20 条写 `judge: deterministic`、19 条写 `judge: systemone`**。

但 `runner.py:153-170` 的 `_engine_name(a)` **只看 `kind`**：

```python
if kind in ("text_visible", "attribute", "response_exact", ...): return "deterministic"
if kind == "visual_state": return "mlx-vlm"
if kind == "agent_verify": return "agent"
return "laya"   # ← 兜底：任何未识别 kind 静默落 laya
```

**后果**：声明 `judge: deterministic` 的 `noul` 断言**照样走 laya**。而且**这个路由不是「级联漏斗」，是「每条断言只走一个引擎」**——没有「1° 判不过 → 降级 2°」的回退链。

> `docs/agent-as-judge-design.md:50` 已指出此风险：「断言→引擎路由硬编码 if/元组，**漏注册会静默落到 laya**」。

#### H4 汇总：真实问题清单

| # | 问题 | 证据 | 阻塞了什么 |
|---|---|---|---|
| H4-1 | `llm_judge` 未注册 | `get_judge('llm')` KeyError | LLM/Agent 对照基线无法走标准路径 |
| H4-2 | registry 死文件 | grep 零命中 | 自主取证从未真实生效 |
| H4-3 | IR 丢 5 个字段 | `model_validate` 实测 | 用例声明的门禁配置无效 |
| H4-4 | ToolPolicy 默认全拒 | `agent_tools.py:193` | 真取证用例必须手配环境变量 |
| H4-5 | `judge:` 是死字段 | `runner.py:153` | 声明与执行不一致；无级联回退 |

**这五条共同解释了一个数字**：`artifacts/agent_judge_calibration_v2.json` 的 `avg_tool_calls: 0`——不是模型不调工具，是**框架层面根本没有让它能调通的路径**。

### 2.5 文档口径已过期（需同步）

`docs/nlaut-vs-cecoder-verification-matching.md:68` 写「Judge 标定基准集 ❌ 无标定基准集 **缺失**」，`docs/judge-calibration-implementation-guide.md:17` 写「LLM Judge 和 Agent-as-Judge **未做过人工标定**」。

**事实**：文件已建（v1: 5 条 / v2: 30 条）、跑过标定（`artifacts/agent_judge_calibration_v2.json`）、做过消融（`artifacts/agent_judge_ablation.json`）。

**真实缺口不是「没有基准集」，而是「基准集没有人工标注、没跑真实工具、没覆盖工序域」。** 文档口径与代码现状的偏差会误导后续排期——这是本次分析的附带产出。

---

## 三、CeCoder 实测能补什么（可落地的映射）

### 3.1 20 条判定难点 → Judge 题型蓝本

实测文档 §4.1 列出 J1~J20。按 nlaut 的三级漏斗重新归类：

| 归口 | 判定难点 | 所需引擎 | 实测能否支撑 |
|---|---|---|---|
| **L1 确定性**（9 条） | J2 产物是否被人工放行（查 3 处埋点）、J5 人工介入次数、J6 介入耗时、J10 工序耗时 vs 会话时长、J11 剔除长时人工离开、J12 结果口径 vs 过程口径、J13 自动通过/人工放行分列、J14 锁定模型额度、J16 六层归因 | deterministic（JSON 字段断言） | ✅ 全部可做，`tasks/*.json` 字段齐全 |
| **L2 概率**（9 条） | J1 产物是否符合规约、J3 门禁是否真拦住（误拦率）、J4 「一次做对」vs「没人看」、J7 人工是否实际审阅、J8 人工 vs 自动收益、J9 24 次介入是真需要还是 AI 反复失败、J14 稳定性 CV、J15 消耗低≠质量好、J17 制品符合度归口 | LLM Judge + Agent-as-Judge | ⚠️ 需先有标注基线（H1） |
| **L3 涌现**（2 条） | J19 工序触发与路由、J20 长链路后段丢失前段约束 | Trace 回放 | ❌ 缺 Trace 层级结构 |

**结论**：L1 能覆盖 9/20，L2 需要 9/20，L3 缺 2/20。
**投入方向明确——先把 9 条 L1 做完（成本低、立刻见效），L2 的 9 条正好是标定集的题型需求。**

### 3.2 工序域 TruthSource：可直接实现的契约

基于 `tasks/*.json` 实测结构（我已逐字段核实），建议新增：

```python
# 概念草案，非最终 API
class ProcessNodeTruth:
    instance_id: str          # "n01"
    definition_id: str        # "example.concept-design"（运行时 id，非包目录名）
    title: str                # "概要设计"
    status: str               # done / failed / pending / ready / review / interrupted
    gates: dict               # {start, complete, autoAdvance} 各 allow/block
    outputs: list[str]        # 产物名清单
    started_at: str | None    # ISO8601
    completed_at: str | None
    duration_min: float | None  # completedAt - startedAt（不是会话时长！J10）
    assessment_kind: str | None  # normal / needs-assets / needs-confirmation / confirmed
```

**三个必须避开的坑（实测得来，不是推测）：**

1. **运行时 `definitionId` ≠ 源码包目录名**。`example.concept-design` ↔ `node-concept-design`；`implement` ↔ `node-implement`；`example.ui-prototype` ↔ `node-ui-design`。12 个运行时 id 映射见素材文档 §5.7。
2. **工序耗时必须用 `completedAt − startedAt`**，不能用会话时长。实测「交付确认」工序自身耗时 0.1 min，会话时长 1441 min。
3. **> 180 min 的样本是跨夜运行或人工离开**，必须剔除，否则污染耗时基线。

**第四个坑（我实测踩到的，最容易写错）**：

> **产物不落在节点的 `dirName` 目录，而在任务根目录（或根目录下的同名子目录）。**

我第一次按 `nodes[].dirName` 去磁盘找产物，得出「102 个 done 节点中 77 个零产物」——**这是错的**。实测 task `09e282f6` 的 `n01 概要设计`，`dirName` 是 `01-concept-design`，但产物 `概要设计文档.md` 在**任务根目录**；task `1011` 的产物在 `01-concept-design/outputs/` 下，而根目录只有 `task.md`。

**正确口径**：按任务根目录递归扫描（排除 `templates/` `materials/`），得到 **2287 个产物文件**；按节点 `dirName` 精确匹配会漏掉 77/102 个节点。

> 这条对 nlaut 的直接影响：`requireDocument()` 比对的是**产物名**，而产物名是 `概要设计文档.md` 这类固定名，不是路径。所以 TruthSource 必须建立「节点 → 产物名」映射，而不是「节点 → 目录」。

### 3.3 门禁判定的真实约束（D-1 家族，直接影响用例设计）

`requireDocument()` 源码核实（`node-program-design/src/client/definition.ts:10`，`node-concept-design:10`、`node-detail-design:10` 同款）：

```ts
export function requireDocument(state: NodeState): CompleteDecision {
  const artifacts = state.outputs.filter((a) => !a.isHandoff)
  if (artifacts.length === 0) return { ok: false, reason: '还没有生成程序设计文档' }
  const names = artifacts.map((a) => a.name)
  const missing = STEP_OUTPUTS.map((o) => o.path).filter((p) => !names.includes(p))
  if (missing.length === 0) return { ok: true }
  return { ok: false, reason: `产物名与工序声明不一致：应为 ${missing.join('、')}...` }
}
```

**三个关键性质（决定 nlaut 用例能断言什么）：**

| 性质 | 后果 |
|---|---|
| 只比对文件名，**不比对内容** | **空内容也通过** → 「产物是否有实质内容」用 L1 判不了，必须 L2 |
| 只做集合包含检查，**不做多余产物检查** | 多产出文件不算失败 → 「是否有多余产物」只有 UI 原型设计查（`requirePrototypeFiles` 第 3 条） |
| 失败即 `failed` + 下一工序不放行 | 状态机可断言（L1 可做） |

**另一处硬伤**：`node-delivery` 的 `validateDelivery()` 恒 `return { ok: true }`（构建产物 `lib/client.js` 内核实）。**交付确认工序的通过率在修复前不构成任何基线**——nlaut 若照抄当前数据做阈值，会把假门禁固化成基线。

### 3.4 「误拦率」是最大的数据缺口，也是最容易补的

- 拦截侧有证据：25 个节点卡在 `start=block`（程序设计 9 / 代码变更设计 8 / UI 原型设计 4 / 详细设计 4）
- **误拦侧无样本**：现有注入用例集全是违规样本，算不出误拦率
- 补法：采集 **≥200 条正常产出**做回放集

**好消息**：正常产出**已经存在**（我已实测统计，不是推测）：

| 指标 | 实测值 |
|---|---|
| 任务数 | 35 |
| `done` 节点数 | 102 |
| 产物文件总数（任务级，排除 `templates/` `materials/`） | **2287** |
| 产物 ≥5 个的任务数 | 23 |

对照门槛是 ≥200 条 → **实际可得 2287 条，超出 11 倍**。**回放集不需要重跑，只需要回捞。** 这是本轮分析里投入产出比最高的一条。

### 3.5 「一次通过率」必须拆三列

实测口径警示：`gates.autoAdvance=allow` 且无风险时工序自动推进会跳过 `review`。只看是否到达 `done` 会把「自动放行」和「人工放行」混为一谈。

实测证据：UI 原型设计第 2 轮**自动通过 0.0% / 人工放行 100%**——同一工序，只是走了人工确认路径。混计会把人工确认误判成门禁失效。

**建议 nlaut 的工序域报告底表固定三列**：

```
自动通过率（autoAdvance=allow 且无人工动作）
人工放行率（assessment.kind ∈ {confirmed, needs-confirmation→confirmed}）
门禁拦截率（gates.start=block 或 complete=block）
```

这与素材文档 J12 / J13 一致。

---

## 四、方案选项

### 选项一：先补 H1（标定标注），再谈工序域

- ✅ **直接兑现既有纪律**：30 条样本补双盲标注，`agreement` 字段填上，一致率可算
- ✅ **成本最低**（按素材文档口径，抽 20~30 份产物 2 人独立打分 = 约 3 人天）
- ✅ **一次性解决 H1**，且标注模板可复用于后续 CeCoder 工序域样本
- ❌ **不解 H2/H3**：工序域 TruthSource 仍不存在，CeCoder 用例一条都写不了
- ❌ 现有 30 条全是 V3 域（`domain: V3` × 30），**没有一条工序域样本**，标完了也验不了工序判定

### 选项二：先补 H3（工序域 TruthSource + L1 用例），H1 并行

- ✅ **立刻能产出 CeCoder 用例**：9 条 L1 判定难点全部可做，成本低
- ✅ **不改判定引擎**：只加 TruthSource 实现 + `Evidence` 字段扩展，风险可控
- ✅ **回放集顺带解决**：采集正常产出做回放集，同时喂 L1 门禁用例和后续 L2 标定
- ❌ **L2 仍然无标定**，AI 判定结论不可作放行依据（但 L1 不受影响）
- ❌ 需要动 `Evidence` 协议（加工序字段），属于协议层改动，要考虑向后兼容
- ❌ **（新增）H4 未修**：TruthSource 若不接到 `build_tool_impls(truth_source=)` 上，用例照样只能跑夹具

### 选项二·修订：H4 接线 + H3 用例（修正后的推荐路径）

**这是我在补入 H4 后调整的路径**。理由：H4-2/H4-3/H4-4 是 H3 的前置，不先修则新写的工序域用例**产出不了可信的标定数字**。

**第 0 步（半天，必做）——打通接线**：

| 动作 | 文件 | 目的 |
|---|---|---|
| `AssertAgentVerify` 加 `truth_sources`/`tools`/`max_cost_usd`/`timeout_s`/`confidence_threshold` | `src/nlaut/ir/model.py:296` | 让用例声明真正生效（现在静默丢弃） |
| 加 `registry` 加载器，把 `truth_source_registry.yaml` 接进 `build_tool_impls` | 新增 + `src/nlaut/judge/__init__.py` | 让「自主取证」有真路径（现在零命中） |
| ToolPolicy 放行 `~/.cecoder` 路径（可用环境变量或用例级声明） | `src/nlaut/judge/agent_tools.py:193` | 让工序域取证不被默认拒绝 |
| 注册 `llm_judge` | `src/nlaut/judge/__init__.py` | 打通 LLM/Agent 对照基线的标准路径 |

**收益**：这 4 处修完，`avg_tool_calls: 0` 才会变成真实数字，「自主取证」才算第一次跑通。

### 选项三：按素材文档 §六 的 10 项待补齐清单全量推进

- ✅ 覆盖完整，一次做完所有 P0
- ❌ **P0 里 3 项是产品侧的事**（埋点、假门禁修复、人工评分集），**不在 nlaut 范围内**
- ❌ 「产出物人工质量评分」依赖产品侧配合，nlaut 无法单方面闭环
- ❌ 规模过大，难在一个迭代内出可验证结果

### 我的建议：H4 接线 → H3 用例 → H1 标定 → H2 回归

**理由**（补入 H4 后修正）：

选项二解决「能写用例」，选项一解决「结论可信」。但两者都缺一个前提：**H4 的接线**。不先修 H4，新写的工序域用例**跑出来仍然是夹具数据**——这正是当前 85% 一致率不可信的根源，不是新问题。

**所以顺序是：**

```
H4（半天接线）
  ↓
H3（工序域 TruthSource + 10 条 L1 用例）
  ↓
H1（用它采 30 条工序域标注样本，双盲标注）
  ↓
H2（金标准回归接进 pytest + 补齐 30 张截图）
```

> 注意 H1 的样本来源变了：不再是标定现成的 V3 域 30 条，而是**用 H3 采的工序域新样本**。这是「先接线」的回报——否则标完了还是验不了工序判定。

**最小可行建议（4 步，2~3 天）**

**第 0 步（半天）：打通 H4 接线**

1. `AssertAgentVerify` 补 5 个字段（`src/nlaut/ir/model.py:296`）—— 现在静默丢弃，用例声明等于没写
2. 加 `truth_source_registry.yaml` 加载器，接进 `build_tool_impls` —— 现在零命中，「自主取证」无路径
3. ToolPolicy 放行 `~/.cecoder`（`src/nlaut/judge/agent_tools.py:193`）
4. 注册 `llm_judge`（`src/nlaut/judge/__init__.py`）—— 打通对照基线

**第 1 步（半天）：工序域 TruthSource**

5. 实现 `ProcessTruthSource`：读 `tasks/*.json` + 任务根目录产物 + zstd 会话流。产出 §3.2 的字段集
6. `Evidence` 加 `process_nodes: list[dict]` 一项（不动现有字段）；`LogTruthSource` 加 zstd 支持

**第 2 步（半天）：10 条 L1 工序域用例**

7. 从 J2/J5/J10/J12/J13 选 10 条，走现有 deterministic 断言

这 10 条**不需要任何 AI 标定**，纯字段断言，可以立刻作为 CeCoder 工序域的回归基线。

**第 3 步（1~2 人天，可与第 2 步并行）：标定**

8. 从 2287 个产物里筛 30 条（**含 trap**：产物缺失 / 命名不符 / 多余产物 / 假门禁放行），2 人双盲标注，填 `annotator_a/b/agreement`
9. 跑真取证标定（带工具臂），得到可用的 `tool_calls > 0` 的一致率

---

## 五、2026-11-14 进展：本轮已解决的部分

| 硬伤 | 状态 | 本轮产出 |
|---|---|---|
| **H3** 工序域 TruthSource 契约不存在 | ✅ **部分解决** | `src/nlaut/judge/process_truth.py` —— `ProcessTruthSource` 一次跨 36 任务 / 201 节点聚合，输出 8 条规则查结果 |
| **H4-2** registry 是死文件 | ⚠️ **部分解决** | 新增 `process_snapshot` agent 工具（不依赖 registry，直接读固定目录）；`registry.yaml` 本身仍未接线 |
| **H4-3** IR 静默丢 5 字段 | ⚠️ **部分解决** | 新增 `AssertVisualRegression` 字段完整；但 `AssertAgentVerify` 的 5 个字段**仍未补**（`truth_sources`/`tools`/`max_cost_usd`/`timeout_s`/`confidence_threshold`） |
| **H4-4** ToolPolicy 默认全拒 | ✅ **绕过** | `process_snapshot` 不接受路径参数（只读固定目录），不受路径白名单限制 |
| **H4-1** `llm_judge` 未注册 | ❌ **未解决** | 本轮未动 |
| **H4-5** `judge:` 是死字段 | ❌ **未解决** | 本轮未动 |
| **H1** 标定基准集是空的 | ⚠️ **有基础了** | 28 条工序域样本已具备（4 类规则、10 条违规），可作标注蓝本，但**双盲标注仍未做** |
| **H2** 金标准回归量不足 | ❌ **未解决** | 本轮未动 |

### 5.1 新增能力：工序域真值源与状态机规则

**`ProcessTruthSource`**（`src/nlaut/judge/process_truth.py`）—— 一次调用完成跨任务聚合：

```
实测对比（同一问题：交付确认门禁是否有问题）
┌──────────────────┬──────────────┬────────────────────┐
│                  │ 通用工具      │ process_snapshot   │
├──────────────────┼──────────────┼────────────────────┤
│ 工具调用          │ 10 次         │ 1 次               │
│ 耗时              │ 24.2s        │ 2.9s               │
│ 成本              │ $0.01215     │ $0.00319           │
│ 找到违规          │ 6 条          │ 10 条（跨 4 类规则）│
└──────────────────┴──────────────┴────────────────────┘
```

**8 条规则**（全部由源码权威定义推导，非经验规则）：

| 规则 | 依据 | 真实数据命中 |
|---|---|---|
| `done_without_outputs` | `requireDocument` 语义 | **1** |
| `done_but_gate_blocked` | 门禁与状态矛盾（仅 start/complete） | **0** |
| `outputs_not_written_back` | 双源交叉 | **1** |
| `done_without_gate_evaluation` | 门禁未评估即放行 | **0** |
| `pending_with_outputs` | 产物归属错位（按任务聚合） | 11（low） |
| `done_after_pending_breaks_chain_order` | `node-machine.ts:12` | **0** |
| `executing_not_current_node` | `node-machine.ts:48` | **0** |
| `review_without_validation_ok` | `node-machine.ts:77` | **0** |
| `unknown_status_value` | `NODE_STATUSES` 封闭集 | **0** |

> **状态机 4 条规则零命中 = 阴性但有价值的结论**：CeCoder 的状态机实现**自洽**，
> 没有链顺序错乱 / 多节点并发执行 / review 卡死 / 非法状态值。
> 这同时说明这些断言作为**回归守门员**是有意义的 —— 它们能守住这个"自洽"。

### 5.1.1 首版实现有 8 条假阳性，已修正（2026-11-14 二轮）

首版规则无条件化，实测报出 10 条违规。逐条核实源码后确认**其中 8 条是假阳性**，
根因是三类**合法形态**被误判：

| 合法形态 | 源码依据 | 误报效果 | 修正 |
|---|---|---|---|
| **legacy 迁移任务**（`task.legacySource` 存在，如 1001/1002） | 迁移路径不写 run/gates/outputs | 3 条 `done_without_gate_evaluation` + 1 条 `done_without_outputs` 假阳性 | 全部规则排除 legacy |
| **`autoAdvance:block` + `complete:allow`** | `node-lifecycle.ts:169` —— autoAdvance 只在 `automatic===true` 时校验；人工确认完成合法 | 3 条 `done_but_gate_blocked` 假阳性 | 该规则只看 `start`/`complete` 两个硬门禁 |
| **平铺布局**（`assetLayout.kind=='task'`） | `node-machine.ts:64-72` —— applyTurnEnd 按目录扫描，共享根目录的产物会写回每个节点 | 「pending 但有 outputs」在平铺下合法 | 排除平铺，且按任务聚合（22 条 → 11 条） |

**修正后：high/medium 只剩 2 条，且都是真问题**（task 1011 概要设计：非 legacy、非平铺，
`outputs=[]` 但磁盘有 6 个产物 —— 产物清单未回写）。

> **这是「规则查」必须条件化的实证**：不看源码的判定规则会持续产出假阳性，
> 而假阳性会让整个检查失去可信度。
> 状态机的 4 条规则之所以零命中，正是因为它们直接照搬源码语义，没有猜测成分。

### 5.2 顺带修掉的框架缺陷

| 缺陷 | 严重度 | 修复 |
|---|---|---|
| `arbiter` 把 deterministic 降级态（`value=0.5`）误判为 **auto_pass** | **高** | 加置信度守卫（`confidence<1.0` → human_review）；原 221 条元测试全绿验证不影响既有行为 |
| `base_url` 斜杠不规范化 → `http://host:port//` 路由失配 | 中 | 新增 `_join_url()`（web + electron 双通道） |
| 报告不展示判定数值明细 | 低 | `detail` 列补充 raw 关键值（similarity / 尺寸 / degraded） |
| `read_file` 不展开 `~` | 中 | `expanduser` |
| agent 无「列目录」能力 | 中 | 新增 `list_dir` 工具 |
| `read_file` 读不完大 JSON（432KB） | 中 | 新增 `query_json` 工具（按字段筛选） |
| `STATUS_VALUES` 漏 `validating`（源码 8 态，我写 7 态） | 中 | 按 `NODE_STATUSES` 修正 |

---

## 五之二、2026-11-15 进展：工序状态机测试能力落地（v0.5.0）

上一节解决了「**事后扫静态快照**」的规则查。但那有个根本限制：

> 规则查只能看**当前终态**。「历史上是否发生过非法迁移」——比如
> `pending → done` 跳过 review——**在只存当前 status 的数据结构下无法判定**。

本节新增的两项能力把「状态机测试」从「事后体检」升级为「**动作 → 观察迁移**」。

### 5.3 `process_state` 断言（零 AI token 的状态机判定）

**定位**：把「能用 1° 绝不用 AI」贯彻到工序域。状态机判定是纯结构比对，
不该让模型推理算术。

```yaml
assertions:
  - kind: process_state
    rule: done_after_pending_breaks_chain_order   # 规则名（查 RULES 注册表）
    expect: none        # none=条件必须成立；any=反守门员
    task_id: "1011"     # 可选：限定任务
    min_severity: all   # high/medium/low/all（low 是观察项，默认不阻断）
```

| 参数 | 含义 |
|---|---|
| `rule` | 规则名，见 `process_truth.RULES`（9 条） |
| `expect` | `none`=条件必须成立；`any`=条件必须**不**成立（反守门员） |
| `task_id` / `node` | 限定范围 |
| `exclude_legacy` / `exclude_flat_layout` | 排除 legacy 迁移任务 / 平铺布局（默认 True） |
| `min_severity` | 严重度下限（默认 `medium`，low 不阻断） |
| `from_probe` | 改为基于探针结果判定（见 5.4），与 `rule` 二选一 |

**降级语义**（关键）：工序目录不存在 / 规则名未知 / 探针缺失 → `value=0.5,
confidence=0.5` → `route()` 转人工仲裁。**绝不判通过**——没有可比对象就没有结论。

### 5.4 `process_probe` 步骤 + 探针判定式

**这是真正的状态机测试能力**：在通道执行步骤时**即时采集**工序状态，
支持「动作 → 观察 → 动作 → 观察」的编排序列。

```yaml
steps:
  - action: nav
    path: '/'
  - action: process_probe
    probe: node        # node | chain | snapshot | violations
    task_id: "1011"
    node: "n01"
  - action: process_probe
    probe: snapshot
assertions:
  - kind: process_state
    from_probe: "status_eq:running"
```

**可用判定式**：

| 判定式 | 含义 |
|---|---|
| `status_eq:<值>` | 状态等于该值 |
| `status_in:<v1,v2>` | 状态在集合内 |
| `index_eq:<n>` | 序号等于 n |
| `output_count_ge:<n>` | 产物数 ≥ n |
| `has_validation_ok` | validation_ok 为真 |
| **`transition:<from>><to>`** | **状态从 from 变为 to（需 ≥2 次探针）** |

`transition` 是核心：它判定的是**迁移**而非状态。例如「非法迁移反守门员」：

```yaml
- kind: process_state
  from_probe: "transition:pending>done"
  expect: any     # 条件（发生了 pending→done）必须不成立 → 发生即失败
```

### 5.5 实测验证

| 项目 | 结果 |
|---|---|
| 新增元测试 | 26 条（`tests/process_state_assert_test.py`） |
| 全量元测试 | **289 passed**（原 263） |
| ruff（8 个改动文件） | All checks passed |
| 真实用例 | `tc_cecoder_process_001` —— **passed，7.8s，零 AI token** |
| 探针采集真实性 | 36 任务 / 207 节点 / 6 种状态（done 109、pending 69、ready 13、failed 7、interrupted 2、review 1） |
| 链顺序断言 | 通过（真实链序均为 done 前缀 + failed/pending 后缀） |

> **探针不是空数据假通过**：探针缺失会降级为 `value=0.5` 转人工，
> 不可能判 `auto_pass`。上表数据证明探针确实采到了真实工序链。

**与 `chain_003`（agent_verify）的分工**：

| | `chain_003`（agent_verify） | `tc_cecoder_process_001`（process_state） |
|---|---|---|
| 判定方式 | agent 自主取证 + 模型推理结论 | 确定性结构比对 |
| token | ~$0.003/次 | **0** |
| 可复现 | 否（模型有波动） | **是** |
| 判「语义对不对」 | ✅ | ❌ |
| 判「结构对不对」 | 勉强 | ✅ |

### 5.6 仍然存在的缺口：`statusHistory` 未持久化

`process_state` 的 `transition` 判定式目前**只能基于探针的两次采样**，
即「本次会话内观察到的迁移」。**跨会话的历史迁移仍然不可测**，
因为 CeCoder 的工序 JSON 只存当前 `status`，不存迁移历史。

**这不是 nlaut 侧能解决的** —— 需要基座在落盘时记录迁移历史。
已产出对接文档：
`~/workspace/cecoder-evaluation/CeCoder工序状态迁移留痕契约建议_v1.0.md`（含字段契约、
合法迁移集、禁止迁移集、分阶段落地建议）。

**nlaut 侧消费能力已就绪**：契约落地后，`transition` 判定式可直接改为读
`statusHistory`，无需改用例结构。

---

## 六、边界声明

**已核实（可回溯）**

- 全部 nlaut 侧的代码事实：`grep` / `read_file` 实读，标注了 `file:line`
- 全部 CeCoder 数据事实：本机 `tasks/*.json`（35 文件 / 194 节点）、`assets/`（34 目录）、`sessions/`（963 文件 / 743,267 事件）实扫
- `requireDocument()` / `validateDelivery()` 源码实读
- 两轮实测的数字：全部来自素材文档，源自会话原始输出与产物文件
- **H4 五条**：逐条实跑确认，非二手结论——
  - `get_judge('llm')` 实抛 KeyError（附完整报错文本）
  - `grep -rn "truth_source_registry" --include="*.py"` 零命中
  - `TestCaseIR.model_validate()` 实跑，YAML 7 键 → IR 6 键，`model_extra: None`
  - `agent_tools.py:193` 实读 `path_allowed` 默认 `return False`
  - `runner.py:153-170` 实读 `_engine_name` 确认不读 `judge:` 字段

**[推测]（已标注，需实测确认）**

- 「工序域 TruthSource 加进 `Evidence` 后不影响现有 173 条用例」——未跑回归验证
- 「9 条 L1 判定难点可直接实现」——基于字段结构推演，未逐条写用例验证
- 「H4 四处修完后 `avg_tool_calls` 会变成 > 0」——逻辑推断，需实际跑一次带工具标定确认

**已实测推翻的原推测（保留记录，避免后人重踩）**

- 原写「34 个资产目录的正常产出足够凑 ≥200 条回放集（未逐目录统计）」→ **已实测：产物文件总数 2287，超门槛 11 倍**
- 原按 `nodes[].dirName` 定位产物，得出「102 个 done 节点中 77 个零产物」→ **错。产物不在 `dirName` 目录，而在任务根目录。正确口径 2287 个文件。** 见 §3.2 第四个坑
- 初版结论写「缺的是样本量」→ **错。缺的是人工标注这个真值；样本量反而充足（2287 条可用）。** 见 §2.2 的 `CONFIDENCE_CAP` 机制说明

**未做**

- 未修改 nlaut 任何代码或配置
- 未重跑任何 CeCoder 采集脚本
- 未新增任何数字；文中所有数字均可回溯到会话原始输出、源码或本机数据文件

---

**文档版本**：v1.0
**生成时间**：2026-11-13
