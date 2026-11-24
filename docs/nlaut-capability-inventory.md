# nlaut 框架完整能力清单

**版本**: v0.5.0
**日期**: 2026-11-15
**用途**: 能力盘点 / 对外汇报 / 缺口识别基线

---

## 一、总览

| 维度 | 规模 |
|---|---|
| 判定断言类型 | **19 种** |
| 步骤动作 | **11 种** |
| 执行通道 | **3 条**（web / electron / api） |
| 判定引擎 | **5 类**（deterministic / mlx-vlm / laya / agent / llm） |
| agent 取证工具 | **10 个**（只读） |
| 真值源注册表 | 已接线（file / http / sql 三类读操作） |
| 用例资产 | **185 条**（5 个业务域） |
| 视觉基线 | **1 套**（cecoder/order_panel.png） |
| 自动化测试 | **393 条**（20 个测试文件） |
| 运行脚本 | **31 个** |

**核心设计原则**：能用 1° 绝不用 AI —— 确定性判定优先，AI 只用于真正需要语义推理的部分。

---

## 二、判定断言类型（19 种）

### 2.1 确定性判定（deterministic，零 AI token）

| kind | 专有字段 | 判定内容 |
|---|---|---|
| `text_visible` | selector, expected, negated | DOM 文案包含 |
| `attribute` | selector, name, expected, negated | DOM 属性值 |
| `response_exact` | expected, case_sensitive | L1 精确匹配 |
| `response_contains` | keywords, min_match=1 | L2 关键词匹配 |
| `response_not_contains` | forbidden | L4 反向验证（安全性） |
| `response_json_schema` | required_fields, expected_values | L3 JSON 结构 |
| `tool_call_params` | expected_function, expected_params | 工具调用参数 |
| `response_time` | max_seconds | 整体响应延迟 |
| `response_ttft` | max_ms=3000 | 流式首 token 延迟 |
| `response_ttft_p95` | max_p95_ms=3000 | 流式 P95 首 token 延迟 |
| `response_length` | min_chars, max_chars | 回答字符数区间 |
| **`visual_regression`** | baseline, min_similarity=0.98, require_same_size | **截图 SSIM 像素回归**（v0.5.0） |
| **`process_state`** | rule / from_probe, expect, task_id?, node?, min_severity? | **工序状态机判定**（v0.5.0） |

### 2.2 AI 判定

| kind | 引擎 | 专有字段 | 判定内容 |
|---|---|---|---|
| `visual_state` | mlx-vlm | prompt, threshold=0.9 | 页面视觉状态语义 |
| `noul` | laya（systemone） | question, threshold | System One 0-1 概率题 |
| `choice` | laya | question, options, threshold | 单选归因 |
| `score` | laya | question, threshold | 量表打分 |
| **`agent_verify`** | agent | objective, threshold=0.9, max_steps=6, system_prompt? | **自主取证型判定**（v0.4.0） |

### 2.3 `process_state` 的两种模式（v0.5.0）

| 模式 | 字段 | 用途 |
|---|---|---|
| 规则查 | `rule` | 按规则名查违规清单（9 条规则） |
| 探针判 | `from_probe` | 基于 `process_probe` 步骤采集结果判定 |

**探针判定式**：`status_eq:` / `status_in:` / `index_eq:` / `output_count_ge:` /
`has_validation_ok` / **`transition:<from>><to>`**（判定迁移而非状态）

**降级语义**：工序目录不存在 / 规则名未知 / 探针缺失 → `value=0.5` → 转人工仲裁。**绝不判通过**。

---

## 三、步骤动作（11 种）

### 3.1 GUI 交互（web / electron 通道）

| action | 专有字段 | 说明 |
|---|---|---|
| `nav` | path | 导航（相对路径会与 base_url 规范化拼接） |
| `fill` | selector, value | 填输入框 |
| `click` | selector | 点击 |
| `select_option` | selector, value | 下拉选择 |
| `wait_visible` | selector, timeout_ms | 等元素可见 |
| `wait_text` | text, selector?, timeout_ms | 等文案出现（消除 XHR 渲染竞态） |

### 3.2 工序域（v0.5.0）

| action | 专有字段 | 说明 |
|---|---|---|
| **`process_probe`** | probe, task_id?, node?, root? | **在动作执行时即时采集工序状态**，支持「动作→观察迁移」编排 |

probe 取值：`node`（单节点）/ `chain`（整链序列）/ `snapshot`（全量统计）/ `violations`（违规清单）

### 3.3 API 通道

| action | 专有字段 | 说明 |
|---|---|---|
| `api_call` | prompt, system_prompt? | 单轮 LLM 调用 |
| `api_followup` | prompt | 多轮对话追加 |
| `api_tool_call` | prompt, tools | 带工具定义调用 |
| `api_stream` | prompt, system_prompt?, repeat=1 | 流式调用（repeat 1-20 支持 P95） |

---

## 四、执行通道（3 条）

| 通道 | 技术 | 支持被测对象 |
|---|---|---|
| `web` | Playwright Chromium | Web 应用 |
| `electron` | Playwright + CDP 连接 | Electron 桌面应用（如 CeCoder） |
| `api` | OpenAI-compatible HTTP | 大模型 API |

**通道无关能力**：`process_probe` / `process_state` 在三条通道都可用（读文件系统）。

**已修的通道缺陷**：
- `base_url` 斜杠不规范化 → `http://host:port//` 路由失配（已加 `_join_url()`）
- 视觉回归必须用视口截图（`full_page=False`），否则页面高度随内容漂移无法比对

---

## 五、判定引擎（4 类）

| 引擎 | 机制 | 适用 | token |
|---|---|---|---|
| `deterministic` | 结构比对 / 算术 | 能规则化的全部断言 | **0** |
| `mlx-vlm` | 本地 MLX 视觉模型 | 页面视觉状态语义 | 本地推理 |
| `laya` | System One（laya-mlx） | noul/choice/score 语义判定 | 本地推理 |
| `agent` | Agent-as-Judge | 需自主取证 + 语义推理 | 远程 API |

### 5.1 置信度路由（`arbiter.route()`）

| 条件 | 路由结果 |
|---|---|
| `value >= threshold` 且 `confidence >= 0.9` | `auto_pass` / `auto_fail` |
| 置信度不足（agent 封顶 0.85） | `human_review` |
| 确定性降级态（`value=0.5`） | `human_review`（**不判通过**） |

**已修的路由缺陷**：确定性降级态曾被误判为 `auto_pass`（已加置信度守卫）。

### 5.2 Agent-as-Judge 特性

- **10 个只读取证工具**：`query_db` / `http_probe` / `read_log` / `read_file` /
  `list_dir` / `process_snapshot` / `query_json` / `inspect_trace` / `list_registry` /
  `locate_object`
- **默认拒绝式作用域**：`ToolPolicy` 需显式放行主机/路径（`NLaut_AGENT_JUDGE_ALLOWED_HOSTS` /
  `NLaut_AGENT_JUDGE_ALLOWED_PATHS`）
- **置信度强制封顶 0.85** → 必定走 `human_review`（设计上不自动裁决）
- **实测性能**：`process_snapshot` 1 次调用 2.9s / $0.0032，对比通用工具 10 次调用 24.2s / $0.0122

---

## 六、工序域能力（v0.5.0，CeCoder 专用）

### 6.1 工序状态真值源

`src/nlaut/judge/process_truth.py` —— 一次取证拿到全量工序状态 + 已判定违规清单。

**9 条规则**（全部由源码权威定义推导，非经验规则）：

| 规则 | 严重度 | 依据 |
|---|---|---|
| `done_without_outputs` | high | `requireDocument` 语义 |
| `done_but_gate_blocked` | high | 门禁与状态矛盾（仅 start/complete 硬门禁） |
| `outputs_not_written_back` | medium | 双源交叉（task.json vs 磁盘） |
| `done_without_gate_evaluation` | medium | 门禁未评估即放行 |
| `pending_with_outputs` | low | 产物归属错位（按任务聚合） |
| `done_after_pending_breaks_chain_order` | high | `node-machine.ts:12` |
| `executing_not_current_node` | high | `node-machine.ts:48` |
| `review_without_validation_ok` | medium | `node-machine.ts:77` |
| `unknown_status_value` | high | `NODE_STATUSES` 封闭集 |

### 6.2 规则必须条件化（实测教训）

首版无条件化时假阳性率 **80%**（10 条里 8 条是假的）。三类合法形态被误判：

| 合法形态 | 依据 | 修正 |
|---|---|---|
| legacy 迁移任务（`task.legacySource`） | 迁移路径不写 run/gates/outputs | 全部规则排除 `legacy` |
| `autoAdvance:block` + `complete:allow` | `node-lifecycle.ts:169`（autoAdvance 仅自动时校验，人工确认合法） | 硬门禁只看 `start`/`complete` |
| 平铺布局（`assetLayout.kind=='task'`） | `node-machine.ts:64-72`（applyTurnEnd 按目录扫描） | 排除平铺 + 按任务聚合 |

### 6.3 实测数据

| 指标 | 数值 |
|---|---|
| 任务数 / 节点数 | 36 / 207 |
| 状态分布 | done 109、pending 69、ready 13、failed 7、interrupted 2、review 1 |
| 结构类不变量违规 | **0** |
| 数据类不变量违规 | 2（task 1011 产物清单未回写） |

---

## 七、视觉回归能力（v0.5.0）

**独立实现 SSIM**（OpenCV + NumPy），**不运行时依赖 AWT**。

| 项 | 实测 |
|---|---|
| 基准一致 | `1.0` |
| 标题改红 | `0.999935`（**漏检**） |
| 名字变灰变小 | `0.995129`（**漏检**） |
| 面板改红底 | `0.955447` |
| 行距加大 | `0.927868` |

**结论**：SSIM 是**布局/结构守门员**，对小幅颜色/字号变化不敏感。
小语义变化仍需 VLM `visual_state` 覆盖 —— 两者**互补**，不可互相替代。

---

## 八、环境前置健康检查（v0.5.0）

`src/nlaut/harness/health.py` —— 把「30 秒内给出明确结论」变成默认行为。

| 检查 | 判据 | 失败含义 |
|---|---|---|
| `cdp` | CDP 端口 `/json/version` 可达 | 应用没带调试端口启动 |
| `auth_backend` | auth 配置 baseUrl TCP 可达 | 登录态校验失败 → 应用进 login 模式 |
| `login_overlay` | 页面 `[data-shell-overlay="true"]` 内容含「登录」 | 遮罩拦截全部指针事件 |

**用例分类**：`split_cases()` 把用例分为 GUI 依赖 / 纯工序域两类。
环境不可用时，GUI 用例**提前中止**（退出码 3），纯工序域用例**照跑**。

**实测价值**：此前 5 条 GUI 用例批量失败，报错看不出是环境问题，为此排查约 15 分钟
（选择器 → DOM → overlay → TCP 探测 → 应用日志）。现在 30 秒内给出结论。

---

## 九、用例资产（182 条）

| 目录 | 条数 | 领域 |
|---|---|---|
| `cases/llm/` | 93 | 大模型 API（流式/工具调用/延迟/结构） |
| `cases/demo/` | 40 | 框架自带演示系统 |
| `cases/sweng/` | 34 | 软件工程场景 |
| `cases/cecoder/` | 9 | **CeCoder 工序域**（本地资产，gitignored） |
| `cases/giencoder/` | 6 | 编码助手 |

### 9.1 CeCoder 用例构成（11 条）

| 用例 | 类型 | 依赖 GUI | 触发执行 |
|---|---|---|---|
| `tc_cecoder_order_001~004` | GUI 确定性断言 | 是 | 否 |
| `tc_cecoder_visual_001` | 视觉回归（SSIM） | 是 | 否 |
| `tc_cecoder_process_001` | **工序状态机（零 token）** | **否** | 否 |
| `tc_cecoder_chain_001~003` | agent 自主取证 | 否（只 nav） | 否 |
| **`tc_cecoder_exec_001`** | **工序执行迁移（ready→running）** | 是 | **是（1 次 LLM）** |
| **`tc_cecoder_exec_002`** | **执行守卫（全 done 无执行入口）** | 是 | **否（零 LLM）** |
| **`tc_cecoder_exec_003`** | **全流程同步层（创建→自动执行→启动断言）** | 是 | **是（1 次 LLM）** |

> exec 系列覆盖了此前缺失的「**执行环节**」：exec_001 测正路径
> （点执行 → 状态真迁移），exec_002 测守卫路径（全完成任务无执行入口），
> exec_003 测全流程启动层。探针用 `task_id: "@latest"` 定位新建任务
> （id 是动态 UUID）。
>
> **完整 7 工序串行 = exec_003（同步层）+ driver（长时层）**：
> `scripts/cecoder_full_workflow_driver.py` 长轮询驱动 + 自动模拟两个人工
> 介入点（选设计系统 / 点「确认完成」），终态校验迁移序列合法性 +
> 链序 + 产物齐全。**实测发现**：「自动执行」按钮不能全自动跑完 7 道
> —— 产品在 n02（UI 原型需选设计系统，start:block）和各工序
> needs-confirmation（review 需人工点「确认完成」）设计了两类人工介入点。
>
> **全链实测（d52e0133，2026-11-16）**：n01-n06 全部走通（含 n05 瞬态失败
> 自动重试），**15 次状态迁移零非法**（10 direct + 5 路径压缩）。n07 交付确认
> 会话**假启动挂死**（会话文件 75 分钟零写入、UI 无恢复入口、唤醒无响应）
> —— 环境侧缺陷如实记录（`artifacts/full_workflow_d52e0133.json`），
> 非用例/driver 缺陷。
>
> **四个关键行为发现（全部固化进 driver/watch 代码与单测）**：
> 1. normal 工序 review 推进有**分钟级延迟**（n04 实测约 10 分钟）—— 勿误判死锁
> 2. 瞬态失败「会话服务尚未就绪」**可重试**（failed→ready→running 合法）
> 3. 采样式迁移观察存在**路径压缩**（15s 轮询跳过秒级 validating/review）
>    —— `transition_kind()` 限深 BFS 三态判定（19 条单测）
> 4. **介入前必先导航到目标任务详情页**（设计系统/确认按钮只在激活视图层）

---

## 十、辅助设施

### 10.1 运行脚本（31 个）

| 类别 | 代表 |
|---|---|
| CeCoder 批量 | `run_cecoder_cases.sh` / `cecoder_cdp_url.sh` |
| 判定标定 | `judge_calibration.py` / `agent_judge_calibration_v2.py` |
| 消融对照 | `agent_judge_ablation.py` / `agent_judge_repeatability.py` |
| 模型对比 | `compare_models.py` / `probe_qwen27b.py` |
| 端点探活 | `api_smoke.py` / `probe_ds41f_sglang.py` |
| 用例生成 | `excel_to_ir.py` / `export_all_cases.py` |

### 10.2 配置

| 文件 | 用途 |
|---|---|
| `config/models.yaml` | 模型端点 / 模型 ID / api_key 环境变量映射 |
| `config/calibrate_v2.json` | judge 标定样本（30 条） |
| `config/golden_samples_20.json` | 金标准集（20 条） |
| `config/truth_source_registry.yaml` | 真值源注册表（**当前未接线**） |

### 10.3 报告

HTML 单文件报告，含断言明细（引擎 / value / 置信度 / 路由 / 判定理由 / 取证轨迹）。

---

## 十一、已知缺口（诚实清单）

| # | 缺口 | 性质 | 影响 |
|---|---|---|---|
| 1 | **`statusHistory` 未持久化** | **被测系统侧** | 跨会话的迁移级缺陷不可测（CeCoder 侧契约建议已产出） |
| 2 | ~~`truth_source_registry.yaml` 未接线~~ | **已修（v0.5.0）** | 新增 `judge/truth_registry.py`，runner 注入 `Evidence.db_state` |
| 3 | ~~`llm_judge.py` 孤儿模块~~ | **已修（v0.5.0）** | 新增 `llm_verify` 断言 + 注册到引擎表；置信度封顶 0.85 |
| 4 | ~~`code_review_tools.py` ruff 6 处~~ | **已修（v0.5.0）** | 全仓 ruff 已全绿 |
| 5 | SSIM 对小幅颜色/字号不敏感 | 能力边界 | 需 VLM 互补（已明确分工） |
| 6 | agent judge 置信度封顶 0.85 | 设计约束 | agent 判定**永远**走人工复核，不自动裁决 |
| 7 | judge 标定双标**工具已就绪，人工待做** | 数据缺口 | 新增 `scripts/dual_annotation.py`（导出/回填/Kappa/一致性统计）；**但 `annotator_a`/`annotator_b` 仍 0/30** —— 需真人填写，工具不伪造 |
| 8 | ~~GUI 用例选择器单点脆弱~~ | **已修（v0.5.0）** | 新增 `executor/selectors.py`：候选降级 + 逐候选诊断；9 条用例已改候选列表 |
| 9 | `statusHistory` 未持久化 | **被测系统侧** | 已产出**落地排期建议**（P0 1.5 人天，改动点集中在 `patchNode` 单一函数） |

---

## 十一之二、v0.5.0 补齐的 4 项（2026-11-15）

### 11.2.1 真值源注册表接线（缺口 #2）

`src/nlaut/judge/truth_registry.py` —— 读 `config/truth_source_registry.yaml`，
把声明的真值源快照注入 `Evidence.db_state`。

| 真值源类型 | 实现 | 说明 |
|---|---|---|
| `file` | `LogTruthSource` | 读本地文件（**支持 `~` 展开**） |
| `http` | `HttpTruthSource` | 只发 GET |
| `sql` | `SqlTruthSource` | 只执行 SELECT（需外部 executor） |

**实测**：本机 3 个声明源中 2 个采到真实数据（design_tasks 4087 字符 / dev_tasks 1436 字符），
第 3 个路径不存在 → 记录 error 而非崩溃。

### 11.2.2 LLM-as-Judge 接线（缺口 #3）

新增 `llm_verify` 断言（第 19 种）+ `llm` 引擎注册。

| 特性 | 说明 |
|---|---|
| 与 `agent_verify` 的差异 | 只读拼进 prompt 的证据（无自主取证），一轮调用 |
| 置信度封顶 | **0.85**（与 agent 同规则）→ 必定转人工 |
| 端点失败降级 | `value=0.5` + `confidence=0` + `degraded` → **绝不误判通过或失败** |

**修掉的解析 bug**：「不通过」含「通过」、「不成立」含「成立」——
原实现先判肯定词，会把否定结论**读反**。已改为先判否定。

### 11.2.3 选择器降级（缺口 #8）

`src/nlaut/executor/selectors.py` —— 让 `click` / `wait_visible` 支持
**逗号分隔的候选列表**，逐个尝试，第一个「存在+可见+可用」者胜出。

| 能力 | 说明 |
|---|---|
| 候选降级 | aria-label / class / 文本 任一未变就能救回来 |
| 逐候选诊断 | 失败时报出每个候选的原因（不存在/不可见/禁用/语法错），不再是无信息的 TimeoutError |
| `SelectorNotFound` | 专用异常类型，与 Playwright 原生错误区分 |

**实测**：9 条 CeCoder 用例改候选列表后 **6 passed + 3 need_review，0 failed**；
其中 `click` 步骤实际降级到第二候选（`has-text`）救回 —— 这正是设计目的。

### 11.2.4 标定双标工作流（缺口 #7 的工具侧）

`scripts/dual_annotation.py` —— 4 个子命令：

| 命令 | 作用 |
|---|---|
| `export` | 导出 CSV 标注工作单（标注员只填 judgement 列） |
| `import` | 回填进 `calibrate_v2.json`，自动重算 agreement |
| `stats` | 一致率 + Cohen's Kappa + 与金标准对照 |
| `derive` | 机械第二标注员（**derived，非 human**） |
| `verify` | Kappa 算法自测 |

**诚实边界**：工具**不会伪造**人工标注。`annotator_a`/`annotator_b` 只能由真人填写。
`derive` 产出的第二意见单独存 `annotations/derived_mechanical.json`，**不污染人工字段**。

**Kappa 算法自测发现的陷阱**：边际偏斜时 Kappa 会远低于直觉一致率
（8 一致 + 2 分歧 → 观察一致率 80%，但 Pe=0.82 → Kappa=-0.111）。
这是 Kappa 的已知性质，**不是公式错** —— 但解读时必须知道。

---

## 十二、验证记录

| 验证项 | 结果 |
|---|---|
| 自动化测试 | **393 passed**（v0.5.0 累计 +80） |
| ruff（**全仓** src/ tests/） | **All checks passed**（历史 12 处遗留已清零） |
| bash 语法检查 | OK |
| CeCoder 用例批量跑 | **6 passed** + 3 need_review，**0 failed** |
| 工序状态机用例 | passed，**6.1s，零 AI token** |
| 健康检查（环境正常） | 3 项全绿 → 环境可用 |
| 健康检查（环境故障模拟） | 准确识别 + 提前中止 + 给出处置建议 |
| 真值源注册表 | 3 声明源 → 2 个采到真实数据，1 个记 error 不崩 |
| LLM judge 引擎 | `get_judge('llm')` 可取；置信度封顶 0.85 |
| 选择器降级 | 9 条用例全过；click 步骤实测降级到第 2 候选 |
| 双标工具往返 | export→填→import→stats 全通（Kappa=1.0 时验证） |
| **工序执行迁移（exec_001）** | **passed 8.6s**：新建任务→执行第一道工序→`transition:ready>running`（1 次 LLM） |
| **执行守卫（exec_002）** | **passed 97.3s**：全 done 任务无执行入口（零 LLM） |
| **全流程同步层（exec_003）** | **passed 11.1s**：创建→自动执行→启动断言（1 次 LLM） |
| **@latest 探针** | 修掉 `~` 展开缺失后 transition 断言从 ['done','done'] → [ready,running] |
| **全链串行（exec_003+driver）** | **15 次迁移零非法**（10 direct + 5 路径压缩），6/7 done；n07 会话假启动挂死（环境侧，见下） |

---

## 十三、一句话总结

> nlaut v0.5.0 是一个**多层判定**的自动化测试框架：
> 18 种断言 × 3 条通道 × 4 类引擎，核心设计是**能用确定性就不用 AI**。
> 在 CeCoder 工序域上已落地**零 token 的状态机判定 + 视觉回归 + 环境健康检查**，
> 并对「规则必须条件化」「SSIM 是布局守门员不是语义守门员」
> 「健康检查前置」三条实测教训做了固化。

---

## 附录 A：关键源码位置

| 能力 | 文件 |
|---|---|
| IR 定义（18 断言 / 11 步骤） | `src/nlaut/ir/model.py` |
| 判定协议 + 路由 | `src/nlaut/judge/protocol.py` / `arbiter.py` |
| 确定性引擎（12 种 mode） | `src/nlaut/judge/deterministic.py` |
| 工序状态真值源 | `src/nlaut/judge/process_truth.py` |
| SSIM 视觉回归 | `src/nlaut/judge/visual_regression.py` |
| agent 取证工具（10 个） | `src/nlaut/judge/agent_tools.py` |
| 环境健康检查 | `src/nlaut/harness/health.py` |
| 三条通道 | `src/nlaut/executor/channels/{web,electron,api}.py` |
| 执行编排 | `src/nlaut/executor/runner.py` |

## 附录 B：CeCoder 侧对接文档

`~/workspace/cecoder-evaluation/CeCoder工序状态迁移留痕契约建议_v1.0.md`
—— 含 `statusHistory` 字段契约、合法迁移集、禁止迁移集、三阶段落地建议。
