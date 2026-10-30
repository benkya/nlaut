# Agent-as-Judge 论文方法论 → nlaut / 软件工厂门禁 移植映射

> 生成日期：2026-10-30
> 依据论文（全文已读，数字均逐条回原文核对）：
> 1. 《When AIs Judge AIs: The Rise of Agent-as-a-Judge Evaluation for LLMs》— arXiv **2508.02994**
> 2. 《AJ-Bench: Benchmarking Agent-as-a-Judge for Environment-Aware Evaluation》— arXiv **2604.18240**
> 3. 《Interaction-as-a-Judge》— arXiv **2610.17895**
> 4. 《Agent-as-a-Judge: Evaluate Agents with Agents》(Zhuge et al.) — arXiv **2410.10934v2**
>
> 移植目标仓库：`/Users/Ben/workspace/nlaut`
> 相关既有文档：`docs/agent-as-judge-poc-plan.md`（G-11 PoC 设计稿）、`docs/judge-protocol.md`（L5 宪法）

---

## 0. 结论先行

**四篇论文合起来给出的答案是：Agent-as-Judge 不是「更好的 LLM-as-Judge」，而是「换了证据来源的判定器」。** 它的全部增量来自一件事——**judge 获得了独立于被测系统自述的取证通道**。AJ-Bench 把这件事拆成三个可检验的验证维度（information acquisition / state verification / process verification），nlaut 的 `src/nlaut/judge/agent_tools.py` 已经按这三维建好了 6 个只读工具，方向是对的。

**三条最重要的可落地结论：**

1. **收益上限有天花板，且比想象的低。** AJ-Bench 全域平均 F1 只有 **0.72**（Agent-as-Judge 相对 LLM-as-Judge 平均 **+0.13**）。因此 Agent-as-Judge 在 nlaut 里**不能默认承担阻断项**，只能在确定性判定无法给出唯一答案时作为**第四级兜底 + 人工前置**。
2. **工具调用不等于取证成功。** AJ-Bench 失败模式分析（A.7）显示最主要的失败是 **(c) 误读工具输出**（多个子域 40%~83%）和 **(d) 证据正确但推理错误**（20%~68%），而 (a) 漏调工具反而最少（多数子域 ~0%~20%）。这意味着 nlaut 现有的 `agent_judge.py` **只保证工具被调用，不保证证据被正确使用**——需要补一层「证据-结论一致性」校验，而不是加大 `max_steps`。
3. **成本可控。** Zhuge et al. 实测 Agent-as-Judge 全量评估成本是人类的 **2.29%**、时间是 **2.36%**（30.58 USD / 118.43 分钟 vs 人类 1297.50 USD / 86.5 小时）。nlaut PoC 里定的「≤60s/样本（P95）」成本红线是有文献支撑的，不是拍脑袋。

---

## 1. 四篇论文各自的核心贡献

### 1.1 arXiv 2508.02994 —《When AIs Judge AIs》(综述)

一篇把「谁来评判 AI」这条线从单模型 judge → 多智能体辩论/委员会 → agent-as-judge 完整梳理一遍的综述，核心贡献不是提出新方法而是**给出方法选择的判据与反模式清单**：它指出 LLM-as-Judge 的三类结构性问题（自偏好/长度偏好、可被对抗性文本骗过、领域知识不足时给出虚假保证），指出多智能体辩论能提升对齐（ChatEval 在 TopicalChat 上 Kendall Tau 0.57 vs 单 GPT-4 的 0.52）但成本是单 judge 的 2–3 倍且有「群体思维」风险，最后给出的结论立场是——**agent-based judging 是对人工监督的补充而非替代**（"can complement (but not replace) human oversight"）。它还特别点出一个对 nlaut 直接相关的坑：**judge 与被测方同族模型时会系统性偏袒同族一方**。

### 1.2 arXiv 2604.18240 —《AJ-Bench》

第一个**专门评测 judge agent 本身能力**的基准，把「Agent-as-Judge 会用工具」从口号变成可测量的能力矩阵：155 个任务 / 516 条标注轨迹 / 60 个工具，覆盖 Search、Data System（Filesystem + Postgres）、GUI（PPT/Word/Excel）三域，围绕三个验证维度（信息获取 / 状态验证 / 过程验证）组织任务。其核心结论是**双面的**：Agent-as-Judge 相对 LLM-as-Judge 平均 F1 **+0.13**（同底座模型、仅差「是否给工具」的严格消融），但**绝对 F1 只有 0.72**，远未饱和。论文还给出了三类失败模式的定量分布、多模态输入的消融（accessibility tree / screenshot / 混合各自在不同子域优劣不同，混合输入可能引入噪声）、以及交互轮数预算的消融（**预算越大 F1 越高，小预算处增益最明显**）。它附带的 GUI 域 Agent-as-Judge prompt 是目前公开资料里最完整的一份「取证型 judge 系统提示词」，含 `JUDGEMENT BASED ON FINAL STATE`、`COMPREHENSIVE EXPLORATION REQUIRED`、`RESULT OVER PROCESS` 等关键约束。

### 1.3 arXiv 2610.17895 —《Interaction-as-a-Judge》

把证据来源从「静态读代码」升级为「**CUA 主动操作应用并留下交互轨迹**」，核心贡献是**把交互策略本身当作可消融的变量**：iterative（每步一次模型调用，最贵）、one-shot（一次规划整个动作序列，1 次调用）、heuristic（限定候选动作集内随机采样，0 次调用），并证明**交互质量直接决定判定质量**（用启发式轨迹训练的 reward model，换成迭代轨迹推理，聚合一致率从 67.95% 升到 68.85%；反向替换则从 70.07% 掉到 67.29%）。关键数字：需求验证准确率 **77.0%（code-only Agent-as-Judge）→ 81.6%（Interaction-as-Judge）**，其中 broken requirement 验证 **63.0% → 71.1%**；修复断裂需求 5 次尝试成功率 **55.4% → 75.5%**。它还给出一条对 nlaut 极重要的负面结论：**GPT 5.6 Luna 作为 judge 时，三种方案无统计差异**（它强到用不好视觉证据）——**底座模型选型不是可以随便定的**。

### 1.4 arXiv 2410.10934v2 —《Agent-as-a-Judge: Evaluate Agents with Agents》(Zhuge et al.)

提出 Agent-as-Judge 框架本体并配套发布 DevAI 基准（55 个真实 AI 开发任务 / 365 条层级化用户需求 / 125 条偏好，需求构成 DAG）。框架由 **8 个模块**组成：graph、locate、read、search、retrieve、ask、memory、planning。**消融实验给出了最有工程价值的结论**：只用 ask 对齐率 65.03% → 加 graph 75.95% → 加 read **82.24%** → 加 locate **90.44%** → 加 retrieve **90.16%（不升反降）**；作者因此只保留 ask+graph+read+locate+retrieve 的组合，并明确说明 **memory 模块「有害」**（前次判断的错误会级联污染当前判断）、**planning 模块「不稳定」**、**search 模块在只有几百行代码的工作区里用不上**。同时给出：Agent-as-Judge 与人类共识对齐率 **90%** vs LLM-as-Judge **70%**；成本仅为人类的 **2.29%**；且在评估 OpenHands 时 Agent-as-Judge 甚至**优于任何单个人类评估者**，最接近三人多数票。

---

## 2. 方法论要素 → nlaut 可移植点映射表

### 2.1 Zhuge et al.（arXiv 2410.10934v2）——框架结构与消融

| # | 方法论要素 | 可移植性 | 移植到 nlaut 的位置 | 移植形态 |
|---|---|---|---|---|
| Z-1 | **ask 模块**：判断单条需求是否满足 | 高 | `src/nlaut/judge/agent_judge.py` `judge()` 终局解析 | 已实现，保留 |
| Z-2 | **read 模块**：直接读取文件内容（消融 +6.29pp：75.95→82.24） | 高 | `src/nlaut/judge/agent_tools.py` `READ_FILE_TOOL` | 已实现，一个函数 |
| Z-3 | **locate 模块**：按需求定位相关文件/对象（消融 +8.20pp：82.24→90.44，**单项最大增益**） | 高 | `agent_tools.py` 新增 `locate_object` / `find_in_db` 工具 | 一个新工具函数 |
| Z-4 | **graph 模块**：构建项目结构图（消融 +10.92pp：65.03→75.95） | 中 | `agent_tools.py` 新增 `list_registry` 的增强版 + `Evidence.module_endpoints` | 一个工具函数 + 一个 Evidence 字段 |
| Z-5 | **retrieve 模块**：从长轨迹中抽取相关片段（OpenHands 上 **不增益**，90.44→90.16） | **低** | 不移植为主能力；仅在需要时用 `inspect_trace` 翻历史 | 可选工具，非默认 |
| Z-6 | **memory 模块**：存储历史判断用于后续判断——作者实测**有害**（错误级联） | **不移植（反向移植）** | `agent_judge.py` 必须**不**把历史 Verdict 喂回 judge | 一条架构禁令 |
| Z-7 | **planning 模块**：规划后续动作——作者实测**不稳定** | **低** | 不移植为独立模块；动作选择交由底座模型在 tool_call 循环内隐式完成 | 不做 |
| Z-8 | **search 模块**：语义检索代码片段——作者实测在几百行工作区**用不上** | **低** | 仅当模块规模远超 DevAI 时才需要 | 暂不移植 |
| Z-9 | **read 支持 33 种格式**（代码/图像/视频/文档） | 中 | `agent_tools.py` `READ_FILE_TOOL` 增加 MIME/扩展名分派 | 一个函数 |
| Z-10 | **层级化需求 DAG**（DevAI 365 需求构成 DAG） | 高 | `src/nlaut/ir/model.py` 断言层：需求级判定 + 依赖感知的聚合 | 一个 IR 模型扩展 + 一个聚合函数 |
| Z-11 | **Alignment Rate 作为核心指标**（= 与人类共识一致的需求判定占比） | 高 | `tests/judge_golden/` 新增 Agent 引擎金标准回归的验收指标 | 一个指标函数 |
| Z-12 | **Judge Shift 作为辅助指标**（偏离人类共识的绝对值） | 高 | 同上，与 Alignment Rate 并列上报 | 一个指标函数 |
| Z-13 | **PR Curve 而非单一准确率**（判定是类别不平衡任务：满足需求远少于不满足） | 高 | `tests/judge_golden/` 报告层：必须同时出 precision/recall/FPR/FNR | 一组指标 + 报告改动 |
| Z-14 | **人类评估用「先独立判 → 再辩论 → 再共识」两轮制** | 高 | nlaut `human_review` 队列的人工仲裁 SOP | 一套流程（SOP 文档） |
| Z-15 | **人类个体间分歧率 10%~30%，多数票后错误率降到 6.01%** | 高 | 作为「单人评审不可靠」的量化依据，写入门禁设计说明 | 一条设计约束 + 一组基线数字 |
| Z-16 | **黑盒 vs 灰盒设定区分**（灰盒可访问人工轨迹，黑盒不可） | 高 | `Evidence` 新增 `trajectory_available` 开关；灰盒/黑盒分开统计指标 | 一个字段 + 两套报表 |
| Z-17 | **成本对比方法**：全量评估总成本/总时间 vs 人类 | 高 | `agent-as-judge-poc-plan.md` §0.3 判据 E 的现成基线（2.29%/2.36%） | 一个成本统计脚本 |

### 2.2 AJ-Bench（arXiv 2604.18240）——环境感知与验证维度

| # | 方法论要素 | 可移植性 | 移植到 nlaut 的位置 | 移植形态 |
|---|---|---|---|---|
| A-1 | **三验证维度**：information acquisition / state verification / process verification | 高 | `agent_tools.py` 已按此分为 `query_db`+`read_log`+`read_file`（获取）、`http_probe`+`query_db`（状态）、`inspect_trace`（过程） | 已实现，需补注释对照 |
| A-2 | **三态判定：working / broken / unverifiable** | 高 | `agent_judge.py` 现为 `value=1.0/0.0/0.5` 三态，`parse_judgement` 未解析→0.5→转人工 | 已实现，语义需对齐论文措辞 |
| A-3 | **「未验证 = 失败」的硬规则**（论文：任一 claim 不可验证 → label 0） | 高 | `agent_judge.py` 系统提示词 | 一行提示词 |
| A-4 | **GUI 域 Agent-as-Judge 系统提示词**（证据优先、最终状态判定、探索完整性、置信度三档） | 高 | `agent_judge.py` 现有 4 条提示词 → 替换为第 6 节完整模板 | 一段提示词文本（**最高优先级**） |
| A-5 | **GUI 域判定提示词**（`JUDGEMENT BASED ON FINAL STATE` / `RESULT OVER PROCESS` / `COMPREHENSIVE EXPLORATION REQUIRED`） | 高 | 同上 | 一段提示词文本 |
| A-6 | **REQUIRED BASELINE：必须先看原始轨迹，再做至少 2 次取证交互** | 高 | `agent_judge.py` `judge()`：增加「首轮强制不判」的回合约束 | 一个循环约束 |
| A-7 | **证据优先级排序**：当前/最终环境状态 > 轨迹回看 | 高 | `agent_judge.py` `_render_brief()` 与提示词 | 一段提示词 + 一处渲染 |
| A-8 | **a11y tree / screenshot / 混合三模态消融**（不同子域最优模态不同；混合可能引入噪声） | 中 | `MlxVlmJudge` 侧：`agent` 引擎的视觉取证模态需按场景选，不做混合 | 一个模态配置项 |
| A-9 | **交互轮数预算消融**（预算↑ → F1↑，小预算处增益最大） | 高 | `agent_judge.py` `DEFAULT_MAX_STEPS = 6` → 需按 PoC 实测定标；论文支持「预算不足是主要失败源」 | 一个参数 + 一条标定流程 |
| A-10 | **失败模式分类 (a)(b)(c)(d) 与占比分布** | 高 | nlaut Agent 引擎的失败归因表：`Verdict.raw` 增加 `failure_mode` 字段 | 一个字段 + 一个枚举 |
| A-11 | **FPR / FNR / Precision / Recall 全指标上报** | 高 | `tests/judge_golden/` 报告层（论文 Appendix A.8 给了完整表结构） | 一组指标 |
| A-12 | **95% 置信区间（3 次独立运行，t 分布）** | 高 | nlaut Agent PoC 的 R=5 轮次设计与 §4 指标直接对应 | 一个统计函数 |
| A-13 | **子域样本量小 → CI 变宽，细粒度结论不可靠**（PPT/Word 明显） | 高 | 门禁判定项分档：样本量小的子域禁止出细粒度结论 | 一条报告纪律 |
| A-14 | **环境回放（replay）机制**：重放轨迹到最终状态再让 judge 交互 | 高 | nlaut 需新增「测试环境快照回放 + 隔离实例」能力 | **一个新引擎（环境层）** |
| A-15 | **GUI 域部署在隔离 AWS 实例以支持并发评估** | 中 | nlaut 并发门禁评估的部署形态参考 | 一个部署方案 |
| A-16 | **Prompt 长度主要由工具协议约束引起，而非任务语义**（论文明确说明） | 中 | nlaut `agent_judge.py` 工具 schema 需完整写进提示词 | 一处提示词构造 |
| A-17 | **时间敏感任务排除**（价格/评分类 URL 快速变化→不可复现） | 中 | 门禁用例设计规则：禁止把强时效性外部数据作为判定真值源 | 一条用例规范 |
| A-18 | **MCPMark 框架（不依赖重工程化工作流）与 ReAct 框架均可实现 Agent-as-Judge** | 中 | nlaut 选型：保持 `agent_judge.py` 现有的轻量 tool_call 循环（≈ReAct 风格），不引入重编排 | 一条架构决策 |
| A-19 | **Agent-as-Judge 用弱模型可达 LLM-as-Judge 用强模型的水平** | 高 | nlaut 底座模型选型：不必为 Agent 通道单独买最贵的模型 | 一条选型建议 |
| A-20 | **推理强度提升 ≠ 工具调用能力提升**（gpt-5-mini medium>low 但 high 不升；deepseek thinking 反而更差） | 高 | nlaut `agent_judge.py` 的 `reasoning_effort` 配置：**不要默认拉高** | 一个配置项默认值 |
| A-21 | **判定 verdict 提取失败**：judge 推理出 FAIL 但落盘 PASS（token 耗尽/解析错误） | 高 | `agent_judge.py`：`Verdict.raw` 必须记 `verdict_parse_error`，且解析失败→confidence=0→`human_review` | 一个降级分支（已部分实现） |
| A-22 | **「证据正确但推理错误」失败模式**（judge 识别出规则却选择不应用） | 高 | nlaut：prompt 中必须写明「规则即基准，不得因被测方解释‘可辩护’而放宽」 | 一条提示词硬约束 |

### 2.3 Interaction-as-a-Judge（arXiv 2610.17895）——交互式取证

| # | 方法论要素 | 可移植性 | 移植到 nlaut 的位置 | 移植形态 |
|---|---|---|---|---|
| I-1 | **iterative / one-shot / heuristic 三种交互策略** | 高 | nlaut 需按判定项类型选策略：跨系统一致性→iterative；只读快照核对→one-shot；冒烟覆盖→heuristic | 一个策略枚举 + 分派 |
| I-2 | **heuristic 策略：限定候选动作集内随机采样，0 次模型调用** | 中 | `agent_tools.py` 可加一个「随机点检」工具做粗筛，但**不得用于阻断项判定** | 一个工具函数 |
| I-3 | **交互轨迹（截图序列 + 动作 + 推理）作为判定证据** | 中 | `Evidence` 新增 `interaction_trace` 字段 | 一个字段 |
| I-4 | **pairwise 判定必须做 AB/BA 双跑消除位置偏置** | 高 | 若 nlaut 引入成对比较判定（两个版本对比），必须双跑取均值 | 一个函数 |
| I-5 | **三态标签 working / broken / unverifiable + 需求发现（无需求列表时先发现再判定）** | 高 | `agent_judge.py` 输出格式扩展；需求发现可支撑「生成用例」场景 | 一段提示词 + 一个模式开关 |
| I-6 | **底座模型能力直接决定判定质量**（76.4% Luna → 81.6% Sol） | 高 | nlaut 换模型后必须重跑金标准回归，不能沿用旧结论 | 一条流程规则 |
| I-7 | **强模型可能无法利用视觉证据**（GPT 5.6 Luna 加截图反而退化） | 高 | 禁止「换更强模型就一定更好」的假设；必须实测 | 一条选型纪律 |
| I-8 | **推理时自反馈修复循环**（5 次迭代，75.5% vs 55.4%） | 高 | nlaut 可用于「门禁不通过 → 自动生成修复建议」场景 | **一个新流程** |
| I-9 | **自报准确率与实测准确率的差值**（Interaction 组的差距更小 → 假阳性率更低） | 高 | nlaut 需同时上报「judge 自报 confidence」与「金标准实测正确率」的差 | 一个校准指标 |
| I-10 | **训练时用作 reward signal（RL 后训练）** | **低** | nlaut 是测试框架不是模型训练框架；不移植 | 不做 |
| I-11 | **交互轨迹的视觉编码（视频 vs 逐帧图片）** | 低 | nlaut 现阶段用截图序列即可；视频编码暂不需要 | 暂不移植 |
| I-12 | **轨迹文本（动作 + 推理）单独贡献极小**（+0.03 / −0.03 pp） | 中 | nlaut：不必在轨迹里附模型的自由推理文本，省 token | 一处减负 |
| I-13 | **CUA 提示词中的「不得修改被测系统」硬禁令**（不得读写文件、不得改代码） | 高 | `agent_tools.py` 工具实现层已是只读；提示词需同步声明 | 一段提示词 |
| I-14 | **finish_run 终止条件：必须穷举而非自然停止** | 高 | `agent_judge.py`：步数耗尽降级逻辑已存在；需强调「未穷举就判失败」是错的 | 一条提示词 |

### 2.4 综述（arXiv 2508.02994）——选型判据与反模式

| # | 方法论要素 | 可移植性 | 移植到 nlaut 的位置 | 移植形态 |
|---|---|---|---|---|
| R-1 | **分层混合策略**：自动指标粗筛 → LLM judge 批量 → agentic judge 深挖 top 候选 → 人工终裁 | 高 | `arbiter.py` 置信度路由的扩展方向；nlaut 三级漏斗天然就是这个结构 | 一条架构原则 |
| R-2 | **judge 与被测方同族 → 系统性偏袒** | 高 | nlaut 若被测系统与 judge 同底座（如都在用 MiniMax），必须在金标准里包含同族样本并单独统计 | 一条测试纪律 + 一个分族指标 |
| R-3 | **多智能体辩论成本 2–3 倍单 judge** | 中 | nlaut 若引入「反驳者 agent」，成本预算按 2–3 倍算 | 一条预算约束 |
| R-4 | **群体思维/串通风险**（多 agent 无对抗设置时会互相附和） | 中 | 若引入多 agent，必须保证至少一个对抗角色 | 一条角色设计约束 |
| R-5 | **领域知识不足时给出虚假保证**（"false assurances"） | 高 | nlaut：Agent judge 的 verdict 必须附「已查/未查的证据清单」，未查项显式列出 | 一个输出字段 |
| R-6 | **可被对抗性文本骗过**（Zheng et al. 2025：编造的 "null" 响应可骗过 GPT-4 评审） | 高 | nlaut：被测系统输出是潜在攻击面；必须坚持「证据必须来自工具而非被测方文本」 | 一条硬规则（已有 rubric 第 3 条） |
| R-7 | **meta-evaluation 自身的局限**：高相关可能只是学了人类偏见，或对 benchmark 过拟合 | 高 | nlaut：Agent judge 的金标准必须留出**留出集**（新任务类型），定期重测 | 一套流程 |
| R-8 | **stability 问题**：含工具/多步交互的判定方差更高 | 高 | nlaut：Agent judge 必须多次运行测抖动率（PoC 已有 FlipRate 指标） | 一个指标 |
| R-9 | **agent judge 单任务最坏可耗时「数十分钟」** | 高 | nlaut：必须有墙钟超时 + 步数上限双重熔断（PoC 判据 E：≤60s P95） | 两处参数 |
| R-10 | **复现性/fairness 难保证**（agent judge 对所有被测方必须一视同仁） | 高 | nlaut：同一批被测方必须走同一取证序列，不允许按对象定制工具 | 一条测试纪律 |
| R-11 | **成本与可扩展性瓶颈**：连续评估场景需管理算力 | 中 | nlaut：Agent 通道只跑抽样复核，不跑全量 | 一条路由规则 |
| R-12 | **「弱 judge + 强辩手」优于「强 judge + 弱辩手」** | 中 | nlaut：judge 侧不必用最强模型，把预算留给取证 | 一条预算建议 |

---

## 3. 关键实验数据摘录表

| 论文 | 场景 / 指标 | LLM-as-Judge（或对照） | Agent-as-Judge | 提升幅度 | 样本量 | 数据来源章节 |
|---|---|---|---|---|---|---|
| 2410.10934v2 (Zhuge et al.) | DevAI 需求判定对齐率（OpenHands，灰盒/黑盒） | 70.76% / 60.38% | 92.07% / 90.44% | +21.31pp / +30.06pp | 365 条层级需求 | §4.2 Table 3 |
| 2410.10934v2 | 组件消融（judge 判 OpenHands） | ask 65.03% → +graph 75.95% → +read 82.24% → +locate **90.44%** → +retrieve 90.16% | — | locate 单项 +8.20pp；retrieve **−0.28pp（不增益）** | 同上 | §4.3 Table 4 |
| 2410.10934v2 | 与人类共识总体对齐率 | LLM-as-Judge 70% | Agent-as-Judge **90%** | +20pp | 3 位人类评估员共识 | §1 引言 |
| 2410.10934v2 | 评估成本 | 人类 1297.50 USD / 86.5 小时 | 30.58 USD / 118.43 分钟 = **成本的 2.29% / 时间的 2.36%** | 成本 −97.64%，时间 −97.72% | 全量 55 任务 | §4.4 |
| 2410.10934v2 | 人类评估者间一致性 | 个体两两分歧率 ~10%~30%；多数票后总错误率 6.01% | Agent-as-Judge 在 OpenHands 上优于任一人类个体（多数票 94.26%） | — | 3 位评估员 | §3.2 / Table 3 |
| 2604.18240 (AJ-Bench) | 跨三域平均 F1 | 各模型 LLM 设定 | Agent 设定 | **平均 +0.13**（同底座仅差工具） | 155 任务 / 516 轨迹 / 60 工具 | §1 / §4.2 |
| 2604.18240 | **Agent-as-Judge 绝对水平** | — | **平均 F1 = 0.72**（未饱和） | — | 同上 | §1 引言 |
| 2604.18240 | gpt-5-mini-low 同底座工具消融 | 59.00 | 72.41（MCPMark）/ ReAct 亦 > 基线 | **+13.41** | 同上 | Table 3 |
| 2604.18240 | deepseek-v3.2 同底座工具消融 | 64.49 | 77.34（MCPMark） | **+12.85** | 同上 | Table 3 |
| 2604.18240 | GUI/PPT 子域（gpt-5-mini-low） | 45.05 | 76.28（MCPMark）/ 64.86（ReAct） | **+31.23**（MCPMark） | PPT 24 任务 / 42 轨迹 | Table 3 / Table 5 |
| 2604.18240 | 失败模式分布 | — | (c) 误读工具输出占 ~30%~83%；(d) 证据对但推理错占 ~11%~68%；(a) 漏调工具多在 ~0%~27% | — | 各子域 | Appendix A.7 Table 8 |
| 2604.18240 | 判据退化 | 推理强度 medium 优于 low；high 不升；deepseek thinking 反而更差 | — | 提升推理强度 ≠ 提升判定 | — | §4.3.1 Table 4 |
| 2610.17895 | **需求验证准确率** | 77.0%（code-only Agent-as-Judge） | **81.6%**（Interaction-as-Judge） | **+4.6pp** | ArenaCorrectness：222 个游戏版本 / 412 条需求 | §1 / §4.2 |
| 2610.17895 | broken requirement 验证 | 63.0% | **71.1%** | **+8.1pp** | 同上 | §4.2 |
| 2610.17895 | working requirement 验证 | 91.0% | 92.1% | +1.1pp（两者接近） | 同上 | §4.2 |
| 2610.17895 | 判定质量随底座模型能力 | 76.4%（GPT 5.6 Luna） | 81.6%（GPT 5.6 Sol） | +5.2pp | 同上 | §1 / §4.2 |
| 2610.17895 | 人类偏好预测（in-context LLM judge） | 65.6%（code only） | **69.1%**（iterative） | **+3.5pp** | ArenaPreferences 6,858 对（13,716 网站） | §4.1 |
| 2610.17895 | 人类偏好预测（训练 reward model） | 68.2% | 70.2% | +2.0pp | 20,160 训练对 / 3,327 评估 | §1 / Appendix E.1 |
| 2610.17895 | 交互策略对判定的贡献 | heuristic 训练→heuristic 推理 67.95%；iterative 训练→iterative 推理 70.07% | 交叉：heuristic 训练→iterative 推理 **68.85%**；iterative 训练→heuristic 推理 **67.29%** | 高质量推理轨迹可补偿低质量训练轨迹（+0.90pp），反向不可 | 6,858 / 6,852 / 6,855 | Appendix F.1 Table 7 |
| 2610.17895 | **修复断裂需求（5 次尝试）** | 55.4%（code-only self-feedback） | **75.5%**（Interaction-as-Judge） | **+20.1pp** | ArenaCorrectness 的 broken 需求集 | §5.1 / §1 |
| 2610.17895 | 训练时 reward signal（RL 后训练） | code-only reward model：构建成功率 81.3% | interaction-only reward model：**88.5%** | 构建失败率 −7.2pp；生成代码量 −35.7%；胜率 +6.7pp | 14k 内部 prompt，600 评估样本 | §5.2 |
| 2610.17895 | 交互预算 | 主实验统一 **30 个动作/候选** | 同左 | — | 每候选 30 步 | §4 / Appendix A.1 |
| 2508.02994 (综述) | 多智能体辩论 vs 单 judge（TopicalChat） | 单 GPT-4 Kendall Tau 0.52 | ChatEval 多智能体 0.57 | +0.05 | — | §4 引用 ChatEval |
| 2508.02994 | 多智能体评估成本 | 单 judge 1× | 3+ agent 2–3× | 2–3× | — | §4 |
| 2508.02994 | MoA 集成（AlpacaEval 2.0 胜率，生成侧） | GPT-4 57.5% | 开源模型 MoA 65.1% | +7.6pp | — | §3 |

> **口径提示（重要，勿误引）**：综述 2508.02994 §2.3 写「agent judge 与人类多数票仅 0.3% 不一致，单 LLM judge 有 31% 不一致」，并在 §4 写「与 5 位人类专家多数票近乎完全一致」。这两个数字**与 Zhuge et al. v2 原文 Table 3 不完全对应**——v2 原文是 **3 位**人类评估员（231a / 38bb / cn9o），且其 Table 3 给出的 Judge Shift 是 0.27%、31.24%、31.42% 等**逐需求项数值**。综述的「0.3% / 31%」更像是把这两个数值当作聚合量来叙述。**引用时请以 Zhuge et al. 原文 Table 3 为准**，并注明评审员是 3 位。

---

## 4. 软件工厂 16 项门禁适用性预判表

> **前置说明**：G-01~G-16 的完整条目原文在《集成准入门禁判定项清单 v1.0》（外部文档，不在 nlaut 仓库内）。下表依据 `docs/agent-as-judge-poc-plan.md` 中已明确引用的 G-11 原文、G-10~G-12 的注释语义（"判定的是接入后能否正常工作，不是模块内部功能是否正确"），以及 G-13/G-14 的存在性，按「门禁项类别」推演。**凡本表未在 nlaut 仓库中找到原文的条目，其判定项文字为按类别推断，落地前须以《清单 v1.0》原文校准。** 已核实的条目标 ✅ 原文引用。

| 门禁 | 判定项（依据/推断） | 建议 judge 类型 | 理由（论文依据） |
|---|---|---|---|
| **G-01** | （按类别推断：文档/需求结构完整性） | **确定性** | 结构性校验可用 regex/pytest 精确表达；AJ-Bench 指出规则型验证器「在窄域内有效」，软件工厂的结构门禁正是窄域 |
| **G-02** | （推断：产出物存在性/命名/格式） | **确定性** | 同上；Zhuge et al. 的 read 模块之所以增益大，恰因结构类断言应由确定性层终结，不该浪费 Agent 取证预算 |
| **G-03** | （推断：配置/契约一致性） | **确定性** | `deterministic` 已有 `response_json_schema` / `tool_call_params` 模式可覆盖 |
| **G-04** | （推断：静态代码规范 / lint 类） | **确定性** | 零 AI 成本、零幻觉面（`deterministic.py` 文档定位） |
| **G-05** | （推断：单元测试通过） | **确定性** | pytest 断言是精确真值源 |
| **G-06** | （推断：接口契约符合性） | **确定性**（+ LLM Judge 复核） | 契约字段可枚举；`response_json_schema` 覆盖 |
| **G-07** | （推断：性能指标达标） | **确定性** | `response_time` / `response_ttft_p95` 模式已在 `deterministic.py` 实现 |
| **G-08** | （推断：UI 视觉呈现正确） | **LLM Judge（mlx-vlm）** | 单张截图的静态视觉状态——nlaut 既有 `MlxVlmJudge` 金标准 6/6 的定位正是此类；Zhuge et al. 消融显示 read/locate 类能力对静态内容无增益 |
| **G-09** | （推断：端到端流程可达 / 冒烟） | **LLM Judge** | 轨迹已足够、无需跨系统取证；用 Laya noul 批量过筛 |
| **G-10** ✅ | 「核心入口可达」（原文引用见 PoC §0.2 R1 注释） | **确定性**（dom 模式） | PoC §2.1 已判定：A1 入口可达由 `deterministic`（dom 模式）覆盖，Agent 无增量 |
| **G-11** ✅ | 「接入后模块与工作台的交互链路通（数据能进来、结果能出去）」 | **Agent-as-Judge** | **唯一确证需要 Agent 的门禁**。PoC §2 拆出 A1~A5 五个原子断言，其中 A2/A3 部分覆盖、A4/A5 完全无覆盖；且必须查独立真值源（DB 直查）以突破「响应体自述成功」陷阱——这正是 AJ-Bench「state verification」维度与 Zhuge et al. read+locate 增益的组合场景 |
| **G-12** ✅ | 跨组协同（原文注释："判定的是接入后能否正常工作"） | **Agent-as-Judge** | 同 G-11 的跨系统一致性语义，需独立取证通道 |
| **G-13** ✅ | （PoC §1.2 列为「不做」范围，推断为更复杂的跨系统协同判定） | **Agent-as-Judge**（抽样复核） | 同族语义，但按 PoC §6.4 判读规则：Agent 成本 > 10× LLM 且增益 < 15pp 时只做 P0 阻断项的抽样复核 |
| **G-14** ✅ | （PoC §1.2 列为「不做」范围） | **Agent-as-Judge**（抽样复核） | 同上 |
| **G-15** | （推断：主观质量 / 可用性 / 体验类） | **人工**（+ LLM Judge 预筛） | 综述 §5.4 明确：教育/医疗等主观维度需多角色视角；且人类个体间分歧率 10%~30%（Zhuge et al.），单 LLM judge 在主观维度不可靠。**Interaction-as-Judge 的 CUA 交互轨迹可用于预筛**（65.6%→69.1% 证明交互对偏好判断有增益），但终裁仍归人工 |
| **G-16** | （推断：上线前最终确认 / 阻断放行） | **人工** | 综述结论立场是 agent-as-judge "complement (but not replace) human oversight"；Zhuge et al. 也只是"与人类评估者可比"，从未主张完全替代。**阻断放行必须留人工终裁** |

### 4.1 门禁 judge 分层的整体判读

| judge 类型 | 覆盖门禁 | 依据 |
|---|---|---|
| 确定性 | G-01~G-07、G-10 | 精确可判定，无幻觉面，零 AI 成本 |
| LLM Judge | G-08、G-09 | 静态/单轮判断；Zhuge et al. 消融证明无工具的 judge 在这类任务上已够用 |
| Agent-as-Judge | G-11、G-12、G-13、G-14 | 需要**独立于被测系统自述**的取证通道（DB 直查、日志、只读接口） |
| 人工 | G-15、G-16（+ 全部 Agent 判定的 `human_review` 兜底队列） | 主观性与最终放行责任不可转移 |

**一条必须写进设计说明的定量事实**：Agent-as-Judge 的绝对 F1 只有 **0.72**（AJ-Bench）。即使在它最擅长的域，也不具备单独承担阻断项的精度。因此在 nlaut 的 `arbiter.py` 置信度路由里，**Agent 通道当前应保持"永不自动放行"**——现有 `agent_judge.py` 的 `CONFIDENCE_CAP` 封顶设计（低于 auto_pass 阈值，`tests/agent_judge_test.py::TestConfidenceCap` 有断言）与此结论一致，**不应在后续迭代中放宽**。

---

## 5. 风险与反模式清单

| # | 反模式 | 为什么有害 | 来源论文 |
|---|---|---|---|
| N-1 | **给 judge 喂「被测方自述」当真值**（如接口返回 `{code:200, msg:"保存成功"}`、前端 toast "保存成功"） | 这类证据与真值同源，无法证伪；AJ-Bench 的误判模式 (d) 显示 judge 会"识别出正确规则却不应用"，因为它相信了被测方的解释 | 2604.18240 (A.7/A.9)、2410.10934v2 |
| N-2 | **让 judge 复用历史判断（memory 模块）** | Zhuge et al. 实测 memory 模块**有害**：前次判断的错误会级联污染当前判断，收益为负 | 2410.10934v2 §4.1 |
| N-3 | **给 judge 加规划模块或过度编排** | 作者实测 planning 模块的过程**不稳定**；引入独立规划层等于引入新的不可控点 | 2410.10934v2 §4.1 |
| N-4 | **在几百行规模的小工作区上做语义检索（search 模块）** | 作者明说当前工作区规模用不上 search 模块；过度工具化只增加 token 与误用面 | 2410.10934v2 §4.1 |
| N-5 | **假设「推理强度越高 → 判定越准」** | AJ-Bench 实测：gpt-5-mini medium 优于 low，但 high 不升；deepseek-v3.2 开 thinking 反而更差（77.34→77.11）。**更强的内在推理 ≠ 更会调工具** | 2604.18240 §4.3.1 |
| N-6 | **judge 与被测系统同族模型却不做分族统计** | 综述明确指出 LLM judge 会「一致地偏袒同族 backbone 一方」；同族 judge 评同族被测方 = 自证 | 2508.02994 §6 |
| N-7 | **在多 agent 辩论中不设对抗角色** | 无对抗设置时 agent 会互相附和、趋同，产生「群体思维/串通」，收益趋近于单 agent | 2508.02994 §2.2 / §6 |
| N-8 | **把 Agent-as-Judge 当作 LLM-as-Judge 的直接升级并默认自动放行** | AJ-Bench 绝对 F1 仅 0.72；Zhuge et al. 的 90% 对齐率是「对齐人类共识」而非「判定正确率」，两者不可混用 | 2604.18240 §1、2410.10934v2 §4.2 |
| N-9 | **用单一准确率评估判定质量** | 判定任务严重类别不平衡（需求满足远少于不满足）；Zhuge et al. 因此主张必须看 PR Curve，AJ-Bench 因此报 FPR/FNR/Precision/Recall 全套 | 2410.10934v2 §4.2、2604.18240 Appendix A.8 |
| N-10 | **忽略位置偏置（只跑一次成对比较）** | Interaction-as-Judge 明确要求每场对战 AB/BA 双跑取均值以抵消位置偏置 | 2610.17895 §3.2 |
| N-11 | **只看「工作台界面」判定交互式应用是否可用** | 代码能编译但行为可能失效（动画不跟随移动、场景切换卡死）；Interaction-as-Judge 的核心论据就是静态检查会漏掉人一眼可见的失败 | 2610.17895 §1 |
| N-12 | **在时敏数据上建立门禁真值**（价格、评分、时效性 URL） | AJ-Bench 明确排除此类任务：它们既不能测试 judge 能力，又破坏环境可复现性 | 2604.18240 §3.1.1 / Limitations |
| N-13 | **给 judge 写操作权限（改数据、提交表单）** | Interaction-as-Judge 的 CUA 提示词明文禁止读写文件与改代码；nlaut `agent_tools.py` 的只读语义是硬约束 | 2610.17895 Appendix H |
| N-14 | **把"没找到证据"当成"任务失败"** | AJ-Bench 的正确语义是 unverifiable（三态之一），不是 broken；且必须先穷举所有相关视图/表/接口再判失败（COMPREHENSIVE EXPLORATION REQUIRED） | 2604.18240 GUI 域判定提示词 |
| N-15 | **在样本量小的子域上出细粒度结论** | AJ-Bench 的 95% CI 显示 PPT/Word 子域区间明显更宽，细粒度差异不可靠 | 2604.18240 Appendix A.6 |
| N-16 | **只用静态证据（代码）判定交互式应用** | code-only Agent-as-Judge 验证 broken requirement 仅 63.0%，加交互后 71.1% | 2610.17895 §4.2 |
| N-17 | **认为换更强的底座模型就能提升判定质量** | Interaction-as-Judge 发现 GPT 5.6 Luna 作为 judge 时三种方案无统计差异——它强到用不好视觉证据 | 2610.17895 §4.1 |
| N-18 | **让 judge 在证据不足时仍给出确定性结论** | AJ-Bench 失败案例 (a)：judge 推理出 FAIL 但落盘 PASS（token 耗尽/解析错误）；综述亦警告领域知识不足时产生 "false assurances" | 2604.18240 A.9、2508.02994 §6 |

---

## 6. 可以直接抄的 Judge Prompt 模板（中文，软件工厂场景）

> **来源与改造说明**
> - 骨架取自 AJ-Bench GUI 域 Agent-as-Judge 系统提示词（Figure 19）+ 用户提示词（Figure 20）+ 判定提示词（Figure 23）。
> - 证据获取约束与「不得修改被测系统」取自 Interaction-as-Judge 的 iterative CUA 提示词（Appendix H.1）。
> - 三态（pass/fail/insufficient）与「界面提示不构成充分证据」取自 nlaut `docs/agent-as-judge-poc-plan.md` §6.2 的统一评分卡（已与用户口径对齐）。
> - **本节是 `src/nlaut/judge/agent_judge.py` 现有 4 行系统提示词（"必须先用工具取证…"）的直接升级版**，可整体替换 `judge()` 中的 `system_prompt` 默认值。

### 6.1 模板 A：系统提示词（取证型 Judge）

```
你是取证型判定器（Agent-as-Judge）。你的唯一职责是：针对给定的判定目标，
通过只读工具独立取证，然后给出结论。你不评价被测系统的设计好坏，只回答
"判定目标在当前实际状态下是否成立"。

【硬规则（不可协商）】
1. 必须取证，禁止凭空判断。在给出结论前，你至少要执行 2 次工具调用，
   并且必须包含至少 1 次对"独立真值源"的查询（模块侧存储 / 服务端日志 /
   模块只读接口），而不是只看被测系统自己的响应体、页面提示或提示文案。
2. 只读。你的全部工具都是只读的。禁止提交表单、修改数据、写入或修改任何文件、
   改变被测系统的状态，禁止运行任何有副作用的命令。如果某个动作会改变被测系统
   状态，那就不要做它——改为读取能证明该动作结果的独立证据。
3. 结论基于最终/实际状态，不基于中间步骤。取证过程中看到的中间态可能包含
   后来被纠正的错误。你的结论必须基于最终状态或独立真值源。
4. 结果优于过程。判定目标描述的是要达成的结果，不是实现路径。如果判定目标
   提到了某个具体功能名、菜单名或接口，但实际状态已经达成了该核心目标，
   就应判定为成立——多条实现路径可达同一结果时，不要因"没看到那个按钮"而判失败。
5. 探索要完整。在判定"不成立"之前，你必须穷举所有可能藏有目标对象的位置：
   所有相关的表、视图、页签、标签、弹窗、接口路由。目标对象很可能不在你
   最初看到的那一处。跳过某个未检查的位置就判失败，是无效判定。
6. 不猜测。取不到真值证据时，输出 insufficient 并明确写出"缺什么证据"，
   不要用推测填补。证据不足时给"不成立"是错的，给"成立"更是错的。
7. 引用具体证据。理由中必须写明你实际查了什么、看到什么值（表名、行数、
   字段、接口状态码、日志行），不得复述判定目标本身作为证据。
8. 规则即基准。若你已识别出判定所依据的规则，即使被测系统给出了看似合理的
   解释，你也必须按规则判定，不得因为"它的解释可辩护"而放宽标准。
9. 只判定被要求的事。不做判定目标之外的额外评估，不提改进建议，不给修复方案。

【判定目标】
{objective}

【已知的证据入口】
{evidence_brief}

【可用工具】
{tool_schemas}

【输出格式（严格）】
结论后只输出以下三行，不要有任何多余内容：

judgement: true | false | insufficient
理由: <你实际取证到的具体证据（表/接口/日志/状态码/字段值）>
CONFIDENCE: High | Medium | Low
```

### 6.2 模板 B：判定输出格式（含置信度自评与证据清单）

```
judgement: true
理由: 查 module_task 表得 31 行，其中 id='T031' 且 name='季度汇报'，与提交请求
      的 payload 完全一致；查服务端访问日志 g11_server.log 存在对应 POST 记录；
      工作台详情页回显的 result_text 与模块侧结果表 module_task_result 中该行
      的 result_text 逐字段一致。
CONFIDENCE: High
```

**解析要点（对齐 `agent_judge.py` 的 `parse_judgement` / `parse_confidence`）**
- `judgement` 取值三态：`true` / `false` / `insufficient`
- `insufficient` → `value=0.5, confidence=0.0` → `route()` 落 `human_review`
- `CONFIDENCE: High|Medium|Low` → 映射为置信度数值；**当前实现应保持封顶，低于
  `route()` 的 auto 阈值**，使 Agent 判定永不自动放行（理由见 §4.1 与 N-8）

### 6.3 模板 C：交互式（CUA）判定补充段 —— 当判定目标是"交互式应用是否可用"

> 源自 Interaction-as-Judge：代码能编译不代表行为正确。

```
【交互取证要求】
如果判定目标涉及交互行为（点击、输入、拖拽、播放、动画、场景切换），
你必须实际操作被测系统来取证，不得仅凭代码或截图推断：

1. 先完整读一遍被测系统的原始执行轨迹，理解它做过什么；然后再动手取证。
2. 用真实用户的方式操作：进入真实功能、填真实数据、点真实按钮、
   走完主流程、观察被测系统是否有响应。
3. 每次动作后检查响应。屏幕像是卡住时，不要重复同一个输入——
   换一种动作，或等待一小段时间来确认被测系统是不是真的没响应。
4. 触发确认态、空态、"完成"态时不要停止，继续探索你还没试过的
   功能、视图和输入项。只有确实没有可探索项时才结束。
5. 遇到浏览器页面或应用"看起来坏了"时，正确的做法是换一个动作继续试，
   而不是去改被测系统的代码或配置——被测系统是只读的观察对象。
6. 判定结论时以交互后的最终状态为准。若中途步骤有错误但最终状态达成了
   判定目标，判定为成立。
```

### 6.4 模板 D：nlaut 判定层可直接复用的 rubric 骨架（G-11 跨系统链路通）

> 与 `docs/agent-as-judge-poc-plan.md` §6.2 的统一评分卡逐字一致，**LLM-as-Judge 组与
> Agent-as-Judge 组必须收到逐字相同的这段文本**——工具是 Agent 组的额外能力，
> 不是"故意给 LLM 组出难题"。报告里必须写明这一点。

```
判定任务（G-11）：接入后模块与工作台的交互链路是否通（数据能进来、结果能出去）？

判定规则：
1. 只判断"集成链路是否通"，不判断接入模块内部功能逻辑是否正确。
2. 判定"通"必须有两类证据：(a) 数据确实进入了接入模块的存储；
   (b) 模块的处理结果确实回流到了工作台。
3. 界面提示、toast 文案、HTTP 响应体的 code=200 都不构成"链路通"的充分证据。
   必须独立查询模块侧的存储/接口/日志来交叉验证。
4. 若无法取到模块侧真值证据，输出 insufficient，不要猜测。
5. 结论必须在 [pass, fail, insufficient] 三态之一。

输出格式（严格 JSON，无多余文字）：
{"verdict": "pass|fail|insufficient", "evidence_refs": [...], "reasoning": "..."}
```

### 6.5 模板 E：Agent judge 结论反向校验（自检 checklist）

> 用于在 `Verdict.raw` 中留痕、以及人工仲裁队列的快速复核。条目直接对应
> AJ-Bench 的失败模式 (a)(b)(c)(d) 与 `JUDGEMENT BASED ON FINAL STATE` 约束。

| 检查项 | 对应失败模式 | 通过标准 |
|---|---|---|
| 工具调用数 ≥ 2 | (a) 漏调工具 | `raw.tool_calls_total >= 2` |
| 含真值源查询 | (a) | trace 中至少 1 次 `query_db` / `read_log` / `http_probe` 打向模块侧 |
| 未依赖被测方自述 | — | 理由中不含"接口返回 200"/"页面提示成功"作为唯一依据 |
| 结论基于最终态 | (d) | 理由引用的是最终状态或差分，不是中间步骤 |
| 穷举过相关位置 | (d) | 理由中列出了检查过的表/视图/接口 |
| 规则未被放宽 | (d) | 理由中未出现"解释可辩护""虽然不完美但…"等放宽表述 |
| 结论可解析 | (a) | `parse_judgement` 返回非 None；解析失败必须落 `human_review` |

---

## 7. 移植落地建议（按优先级）

| 优先级 | 动作 | 依据 | 预计工作量 |
|---|---|---|---|
| **P0** | 用 §6.1 模板 A 替换 `agent_judge.py` 现有 4 行系统提示词 | AJ-Bench GUI 域提示词是公开资料中最完整的取证型 judge 提示词 | 半小时，零回归风险（`tests/agent_judge_test.py` 已覆盖解析路径） |
| **P0** | 增加 §6.5 的失败模式自检字段（`Verdict.raw["failure_mode"]`） | AJ-Bench A.7 显示 (c)(d) 是主因，需可观测 | 1 个字段 + 1 个枚举 |
| **P1** | 给 `agent_tools.py` 补 `locate_object`（定位相关表/视图/文件） | Zhuge et al. 消融中 locate 是单项最大增益（+8.20pp） | 1 个工具函数 |
| **P1** | 禁止 judge 复用历史 Verdict（把 memory 语义写进架构约束） | Zhuge et al. 实测 memory 模块有害 | 1 条架构禁令 + 1 个测试断言 |
| **P1** | `Evidence` 补 `db_snapshot_before` / `db_snapshot_after` / `store_diff` | PoC §3.5.3 已论证 `db_state` 无法表达前后差分 | 3 个字段 + 1 处 diff 计算 |
| **P2** | 按门禁项分派交互策略（iterative / one-shot / heuristic） | Interaction-as-Judge §3.1 | 1 个策略枚举 + 1 个分派函数 |
| **P2** | 引入环境回放与隔离实例（A-14） | AJ-Bench §3.5：回放最终状态到独立环境再让 judge 交互 | 新环境层，工作量大 |
| **P2** | 在金标准中**分族统计**（judge 与被测方同族 / 异族） | 综述 §6：同族偏袒 | 1 个分族指标 + 样本集扩充 |
| **P3** | 扩展为多 agent（加对抗性 Critic） | 综述 §2.2；成本按 2–3× 预算 | 1 个新角色 + 成本预算 |

---

## 8. 未解决问题与诚实声明

1. **G-01~G-16 的完整原文不在 nlaut 仓库内**（在《集成准入门禁判定项清单 v1.0》）。§4 表中仅 **G-10/G-11/G-12/G-13/G-14** 的语义有仓库内文档（`docs/agent-as-judge-poc-plan.md`）作为依据，其余条目是**按门禁类别推断**，落地前必须以清单原文校准。
2. **四篇论文的绝对性能都远低于"可作阻断项"的水平**。AJ-Bench 平均 F1 = 0.72 是最直接的反证。本文的全部映射设计都建立在「Agent 通道永不自动放行、只做兜底与抽样复核」这一前提上；**任何放宽 `CONFIDENCE_CAP` 的提案都应先回答 N-8**。
3. **综述与原文存在数字口径差异**（见 §3 口径提示）。凡引用 0.3%/31%/5 位专家，应回到 Zhuge et al. v2 原文 Table 3 核对。
4. **成本数字的适用范围有限**：Zhuge et al. 的 2.29% 是「全量 55 任务 DevAI 评估」的均值，且其 Judge 用的是 gpt-4o-2024-05-13。nlaut 的真实成本取决于本地/云端模型选型与工具实现，**不能直接沿用 2.29% 这个比例**，只能沿用「Agent 通道成本低于人类 1~2 个数量级」这一定性结论。
5. **Interaction-as-Judge 的 GUI/游戏结论向 B 端后台系统的外推未经论文验证**——其数据集全部是 Web 应用与游戏。迁移到软件工厂的集成门禁时需重新做 PoC，不可直接引用 77.0%→81.6% 这个提升幅度作为预期收益。
