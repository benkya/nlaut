# nlaut 框架演进规划(M0.5 → M3)

> 目标：把「6 条 GienCoderWorkbench 用例 + 三级判定」从 MVP 演进到「可日用的自动化测试基础设施」。
> 写于 2026-09-23，基于 commit `eb69e86`。

---

## 0. 现状速写(基于 `eb69e86`)

| 层 | 文件 | 完成度 | 状态 |
|----|------|--------|------|
| L0 规范 | `AGENTS.md` + `docs/5 篇` | 90% | 已能指引贡献者 |
| L1 编排 | `harness/session.py` JSONL | 70% | 会话可回放,缺会话恢复/续测 |
| L2 生成 | `pipeline/` 4 文件 | **5%** | 仅占位 import,真生成靠对话 AI |
| L3 资产 | `ir/{model,store,datafactory}.py` + 31 条用例 | **85%** | IR + 数据工厂可用,缺变更追溯 |
| L4 执行 | `executor/{runner,channels/{web,electron},regen}.py` | 80% | 双通道已通,缺隔离/重试/环境矩阵 |
| L5 判定 | `judge/` 5 文件 | 85% | 三级漏斗跑通 6/6,缺 flaky 统计与自愈 |
| L6 报告 | `report/{render,evidence}.py` + `feishu_bridge.py` | 75% | HTML+截图+飞书,缺趋势/失败聚合 |

**最关键缺口**:
1. **pipeline/ 空壳** — L2 真正实现需要把"对话 AI 做的事"沉淀成可重放的代码
2. **跨测试隔离** — tc_003 反复踩坑:SPA 状态污染导致同一套用例不同批次结果不一样
3. **生成物 gen/ 不存在** — AGENTS.md 写了 "make regen" 但实际没派生代码(IR 是唯一事实源,但 IR → 执行 是 read-on-fly,不是 gen 一份)
4. **flaky/重试/环境矩阵** 完全没有 — 真实自动化一周后会撞

---

## 1. 演进路线(分 3 个可独立交付的 milestone)

### M0.5 — "能稳定跑下来"(1 周,小步快跑)

**目标**:把当前 6/6 跑测**长期稳定下来**,任何时候跑都能 6/6,不留隐藏陷阱。

| 任务 | 产出 | 工时估 |
|------|------|--------|
| **加 `state.reset()` 钩子** — 每个用例 step 0 自动执行强制重置(SPA 状态归零) | `runner.py` 加 reset 步骤,默认策略 = nav 到根 + 清 Cookie/Storage | 半天 |
| **加 flaky 统计** — 同一用例连跑 3 次,统计通过率 | `runner.py --flaky N`,产物写入 `artifacts/flaky/<case_id>.json` | 半天 |
| **加 retry 策略** — step 失败重试 N 次,带指数退避 | `runner.py` 加 retry 配置,默认 N=2 | 半天 |
| **基线用例 self-test** — 用 6 条用例作为 M0.5 是否达标的"金标" | `tests/smoke_test.py`,CI 上跑这道作为 sanity | 半天 |
| **会话续测** — 中断后能从 JSONL 恢复到断点 | `harness/session.py` 加 `--resume-from <run_id>` | 1 天 |

**验收**:`make smoke` 在 5 台不同机器/不同时间段各跑 10 次,6/6 必须每次都过。

---

### M1 — "口述能闭环"(2 周,价值最高)

**目标**:把对话 AI 干的"口述→澄清→生成 IR"沉淀成可重放代码,**让非专家也能用**。

| 任务 | 产出 | 工时估 |
|------|------|--------|
| **沉淀 `pipeline.ingest`** — 缺前置条件时主动追问(目前对话 AI 心智) | `pipeline/ingest.py` 实现澄清对话 + 追问模板 | 1 天 |
| **沉淀 `pipeline.semantic`** — 从口述抽四元组 + 引用 IR schema | `pipeline/semantic.py` 接 local-mlx 抽取 | 1 天 |
| **沉淀 `pipeline.generate`** — 四元组 + 上下文 → IR yaml | `pipeline/generate.py` 模板化生成 + LLM 补 selector/value | 2 天 |
| **沉淀 `pipeline.verify`** — 干跑不调被测系统,只看 IR 自洽性 | `pipeline/verify.py` 检查 selector 存在性/字段类型 | 1 天 |
| **飞书表格做 UI** — 业务方在飞书写口述,框架自动生成 IR 进 cases/ | `feishu_bridge.py` 监听事件 + 调用 pipeline | 2 天 |
| **口述→IR 回归金标** — 30 条历史用例反推作为端到端冒烟 | `tests/verbal_to_ir_test.py`,挂 CI | 1 天 |

**验收**:业务方在飞书写「测登录错误密码」→ 框架在 30 秒内产出 `cases/<app>/tc_login_err.yaml` → 执行 → 报告。**完全无需对话 AI 介入**。

---

### M2 — "规模化 + 自愈"(3-4 周)

**目标**:从 6 条用例 → 200 条用例,从单一应用 → 3-5 个被测系统,从单次跑 → 每日回归。

| 任务 | 产出 | 工时估 |
|------|------|--------|
| **环境矩阵** — `cases/<app>/env/{dev,staging,prod}.yaml` 配置 | `executor/env.py` 按 env 选择 base-url/账号/重试策略 | 2 天 |
| **失败回传自愈** — 失败的 attribute 断言自动二次扫描 DOM,找新 selector | `judge/self_heal.py`,生成"可能新 selector 候选" + 飞书通知 | 3 天 |
| **趋势报告** — 飞书群每日推送"今日通过 X/Y,新增失败 Z 条" | `report/trend.py` 聚合 session.jsonl | 1 天 |
| **selector 字典** — 公共 selector 抽取成 `data/selectors.yaml`,用例引用 | `ir/selectors.py` 引用解析 | 1 天 |
| **多应用隔离** — `cases/<app>/<feature>/tc_*.yaml` 目录树 | `IRStore` 加 app 维度 | 1 天 |
| **并行执行** — 多 worker 跑独立用例 | `runner.py --workers N`,Playwright context pool | 2 天 |

**验收**:每天早上 9 点自动跑通 200 条用例,3 个应用,5 个环境,失败自动飞书通知 + 自动开 issue(可选)。

---

### M3 — "智能化 + 商业化"(持续)

只列方向,不估时间:

- **预测性 flaky**:基于历史数据预测哪些用例今天会 flaky,提前重跑
- **自然语言改用例**:用户说"把 tc_login_err 改成测密码为空",自动改 IR
- **可视化 dashboard**:Web UI 看趋势、查 selector、看失败聚类
- **MCP server 暴露**:让 Cursor/Claude 直接调 nlaut 跑用例
- **企业版**:多租户、用例模板市场、AI 智能推荐断言

---

## 2. 立刻建议的 5 件事(本周)

如果只能做 5 件事,优先级排序:

1. **M0.5-1 `state.reset()` 钩子** — 解决 tc_003 反复踩坑的根本问题,小投入大产出
2. **M0.5-2 flaky 统计** — 上线前必做,否则不知道你做的是不是稳定
3. **M0.5-3 retry** — 实战必做,SPA 偶发超时不可避免
4. **L3-1 沉淀 IR 变更追溯** — `git log cases/` + diff 在飞书报告里展示
5. **L6-1 报告加"上轮 vs 这轮"对比** — 让业务方一眼看出回归点

---

## 3. 不要做的事(避免范围漂移)

- ❌ 不要把 `gen/` 派生代码复活 — IR 直跑已经是更现代的方案
- ❌ 不要在 pipeline 里塞"大模型微调" — M1 用现有 mlx 推理就够
- ❌ 不要做"全自动化 PR" — AGENTS.md 的 git 卡点要保留
- ❌ 不要新增判定引擎(L5) — 三级漏斗已稳,新增应放 M3
- ❌ 不要把 session JSONL 换数据库 — JSONL 简单可读,M2 再换不迟

---

## 4. 验收标准(每 milestone 必须达成)

| 维度 | M0.5 | M1 | M2 |
|------|------|-----|-----|
| **稳定性** | 6/6 × 100 次全过 | 50/50 × 30 次全过 | 200/200 × 30 次全过 |
| **可维护** | 新增 1 条用例 < 5 分钟 | 新增 1 条用例 < 1 分钟(纯口述) | 飞书 UI 全流程 |
| **可观测** | flaky 报告 | 趋势报告 + 飞书推送 | 多应用 dashboard |
| **可隔离** | state.reset | env matrix | 多 worker |
| **可重放** | session.jsonl 可续测 | 端到端跑测可从会话恢复 | 全场景 sandbox replay |

---

## 5. 一句话总结

> **你已经有「能用的核心」,缺的是「能让别人用、能跑很久」的外壳**。
> M0.5 把外壳补上(隔离/重试/可观测),M1 把 AI 干的活沉淀成代码,M2 才能谈规模化。

下一步建议:**先做 M0.5 的 5 件事里优先级最高的 `state.reset()`**,半天工时,
立刻解决 tc_003 的跨测试污染问题。要我现在动手实现吗?