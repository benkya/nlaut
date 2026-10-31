# PR: Agent-as-Judge 自主取证判定（nlaut v0.4.0，4° 判定引擎）

## 背景

软件工厂集成准入门禁 **G-11「接入后模块与工作台的交互链路通（数据能进来、结果能出去）」**
本质需要 judge **自主取证**——只看通道预先塞好的 Evidence 不足以判定
「数据是否真的落库」「接口是否真的返回」。

现有三级判定漏斗（确定性 → VLM → Laya）都要求证据由通道预先准备，
无法覆盖「需要 judge 自己去查」的场景。

本 PR 新增 **4° Agent-as-Judge** 引擎：judge 自主调只读工具取证后给结论。

---

## 核心设计

### 本质差异：证据从哪来

| | 其它引擎（1°–3°） | Agent-as-Judge（4°） |
|---|---|---|
| 证据 | 通道预先塞进 `Evidence` | **judge 自己在运行时调工具去取** |
| 取证 | 无 | 7 个**只读**工具 |
| 结论 | 引擎直接给 | 模型先取证、再基于取证结果给结论 |

### 三条硬约束

1. **置信度强制封顶 0.85** —— 低于 `route()` 的 0.9 自动裁决线
   → **必定**走 `human_review`。它是「人工仲裁的取证辅助」，不是「自动放行许可」。
2. **工具只读 + 作用域白名单（默认拒绝）** —— `http_probe` / `read_file` / `read_log`
   不显式放行即拒绝；越界记入 `raw.scope_violations`。
3. **降级绝不误判为通过** —— 端点挂 / 无 key / 步数耗尽 / 超成本预算 →
   `confidence=0` + `raw.degraded` → `human_review`。

---

## 实测结果（R=5 × 20 样本 = 100 次判定）

```
总判定 100 | 判错 0 | 准确率 100.0% | 翻转率 0.0%
延迟 median=3.2s  p95=18.9s  max=39.8s
成本 单次 $0.0017
```

| 判据 | 阈值 | 实测 | 结论 |
|---|---|---|---|
| A 陷阱加权误判率 | ≤0.125 | **0**（9 陷阱类 × 5 轮 = 45 次全对） | ✅ Go |
| B 陷阱假阳性数 | =0 | **0** | ✅ Go |
| C 翻转率 | ≤5% | **0.0%** | ✅ Go |
| D 高置信区准确率 | ≥0.95 | **100%** | ✅ Go |
| E 越界率 | =0 | **0**（4/4 被拦） | ✅ Go |
| F 人工介入率 | 报告 | **100%**（设计使然） | ⚠️ |
| G P95 耗时 | ≤60s | **18.9s** | ✅ Go |

### 陷阱样本占比 45%（9/20）

20 样本覆盖 10 类，每个陷阱类都有**防失效自检**（断言陷阱真的与自述矛盾）：

| 类别 | 数量 | 作用 |
|---|---|---|
| 真实通过 / 真实失败 | 2 / 2 | 基线 |
| 表面通过实则未落库 / 表面失败实则已落库 | 2 / 2 | 抗骗 |
| 表里有数据但目标对象不在 | 1 | 需 locate 能力 |
| 多表 / 边界 / 字段语义 / 定位歧义 / 证据不足 | 2 / 3 / 2 / 2 / 2 | 语义与证据强度 |

---

## 延迟长尾的根因（压测实证，非推测）

曾观察到单次判定 130s，一度怀疑 agent 多轮累积。**压测证明不是**：

| 测量 | 结果 |
|---|---|
| 裸调用连续 20 次 | p50 **0.82s** / p90 4.09s / max 9.45s |
| 带工具连续 15 次 | p50 **0.98s** / max 6.86s |
| agent 场景连续 12 次 | p50 1.44s，**出现一次 88.49s** |
| agent 场景连续 25 次 | p50 1.42s / p95 **2.02s** / **0% 超 10s** |
| agent 判定埋点 | **100% 时间在 HTTP 调用内**，本地零开销 |

**结论**：长尾是**端点侧推理调度抖动**，与 token 数 / agent 逻辑 / 工具数量均无关
（长尾那次 `completion_token` 仅 151，与快请求同量级）。

**两层防护**：`per_call_timeout_s` 熔断（45s）+ `max_retries` 退避重试（2 次）
→ **p95 从 127.3s 降到 18.9s、max 从 129.3s 降到 39.8s**。

---

## 变更清单

### 新增（4 引擎 → 5 引擎）

| 文件 | 内容 |
|---|---|
| `src/nlaut/judge/agent_judge.py` | Agent 引擎：多轮 function calling、成本预算、熔断、重试、降级 |
| `src/nlaut/judge/agent_tools.py` | 7 个只读工具 + `ToolPolicy` 作用域白名单 |
| `src/nlaut/judge/truth_source.py` | 真实模块接入层：`TruthSource` 协议 + 5 种实现 |
| `tests/agent_judge_test.py` | 20 样本金标准 + 元测试 |
| `tests/truth_source_test.py` | 接入层元测试（25 项） |
| `scripts/agent_judge_repeatability.py` | 一致率度量脚本 |
| `scripts/agent_judge_ablation.py` | Agent vs LLM Judge 消融对照脚本 |
| `cases/demo/tc_agent_verify_001.yaml` | G-11 示例用例 |
| `docs/agent-as-judge-*.md` | 4 份文档（设计 / PoC 方案 / 论文移植 / 接入层） |

### 修改

| 文件 | 变更 |
|---|---|
| `src/nlaut/ir/model.py` | 新增 `AssertAgentVerify`（`kind=agent_verify`）+ union 注册 |
| `src/nlaut/executor/runner.py` | `_engine_name` / `_question_for` 路由接线；提取 agent trace |
| `src/nlaut/judge/protocol.py` | `EngineName` 加 `"agent"`；新增 `register_judge_factory` |
| `src/nlaut/judge/__init__.py` | 注册 `"agent"`（懒加载工厂） |
| `src/nlaut/report/render.py` | `_render_agent_trace`——取证轨迹进 HTML 报告 |
| `pyproject.toml` | 版本 0.4.0；新增 `agent` pytest marker |
| `README.md` / `docs/*` / `docs/wiki/*` | 文档同步 |

### 顺带清理

**全仓 `ruff check .` 从 39 errors → 0**（改动前既存，非本特性引入）：

- 14 项安全自动修复 + 7 个脚本 `chmod +x`（EXE001 的正确修法）
- BLE001：探针/守卫脚本的盲捕获加 `noqa` + 理由（有意为之，未改行为）
- RUF046 / SIM115 / S112 / F841 / PLC0206 / RUF012：逐个按语义修正
- `parse_ds41f_p0.py`：`except Exception` → `except json.JSONDecodeError`（更精确）

---

## 质量门禁

```
ruff check .                    → All checks passed!（零告警）
pytest tests/                   → 205 passed, 5 deselected
```

---

## 复现

```bash
cd ~/workspace/nlaut

# 一致率度量（20 样本 × 5 轮）
.venv/bin/python scripts/agent_judge_repeatability.py --repeats 5

# 消融对照（Agent vs LLM Judge，同模型同 rubric 只差能否取证）
.venv/bin/python scripts/agent_judge_ablation.py --repeats 3

# 真实端点集成测试
.venv/bin/python -m pytest tests/agent_judge_test.py -m agent -v
```

---

## 边界与限制（务必 review）

1. **不是自动阻断项** —— AJ-Bench 实测 Agent-as-Judge 绝对 F1 仅 **0.72**
   （相对 LLM Judge +0.13）。本实现因置信度封顶 0.85，**100% 走人工仲裁**。
   定位是「**替人工完成取证**」，价值在证据质量，不在减少人工。
2. **未在真实组外模块上验证** —— 当前 20 样本用 `db_state` 夹具快照。
   真实接入需先确认 6 项（见 `docs/agent-as-judge-integration.md` §6），
   尤其**字段语义基线**——那是最大的假阳性来源。
3. **端点长尾未根治理** —— 熔断+重试只是兜底，根因在服务端调度，需运维侧确认。
4. **SQL 只读是字符串前缀检查** —— 挡得住 `DELETE`/`UPDATE`/`DROP`/`TRUNCATE`，
   挡不住 `SELECT ... INTO OUTFILE` 这类边缘情况，接入方仍需保证 SQL 干净。

---

## 建议的 review 重点

1. `src/nlaut/judge/agent_judge.py` 的**降级路径**——「不可用」绝不能被读成「通过」
2. `src/nlaut/judge/agent_tools.py` 的 **`ToolPolicy`** 默认拒绝语义
3. `src/nlaut/judge/truth_source.py` 的**只读约束**是否足够
4. `tests/agent_judge_test.py` 的**陷阱样本真伪**是否站得住
