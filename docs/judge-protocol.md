# Judge 判定协议（L5 宪法）

先定协议、后定实现：`deterministic` / `mlx-vlm` / `laya` / `agent` / `jev` 五个引擎实现同一接口，
本地与云端可插拔切换。权威定义：`src/nlaut/judge/protocol.py`。

```python
class Evidence(BaseModel):     # 判定只能基于证据作答，不得访问外部状态
    case_id: str
    screenshot: str | None     # 截图路径（artifacts 相对）
    dom_state: dict | None     # 可见文本/元素状态
    response: dict | None      # API 响应
    db_state: dict | None

class Question(BaseModel):
    kind: "noul" | "choice" | "score"
    text: str
    options: list[str] | None  # choice 必填
    context: dict = {}         # 引擎专有参数（selector / mode / objective / max_steps ...）

class Verdict(BaseModel):      # 所有引擎的统一产出
    engine: str                # "deterministic" | "mlx-vlm" | "laya" | "agent" | "jev"
    kind: "noul" | "choice" | "score"
    value: float | str | int   # noul: p∈[0,1] / choice: 选项 / score: 分值
    confidence: float          # 引擎自报置信度 ∈[0,1]
    evidence_refs: list[str]
    raw: dict                  # 引擎原始输出，全程留痕进报告

class Judge(Protocol):
    def judge(self, evidence: Evidence, question: Question) -> Verdict: ...
```

## 置信度路由（arbiter）

| 条件 | 路由 |
| --- | --- |
| engine == deterministic | value ≥0.5 → auto_pass / auto_fail（零 AI 风险） |
| confidence ≥ 0.9 且满足阈值 | auto_pass / auto_fail |
| 0.5 ≤ confidence < 0.9 | human_review（人工仲裁队列，附证据包） |
| confidence < 0.5 | human_review（引擎自认不确定） |
| choice 类未提供 satisfied 映射 | human_review |
| **engine == agent** | **恒定 human_review**（置信度封顶 0.85 < 0.9，见下） |

## 题型与引擎匹配

- **Noul**（0-1 概率）是 Laya/Jev 的原生题型——校准置信度、0 输出 token，判定层选它们而非
  普通 LLM 的根本原因。
- **Choice**（≤255 选项）用于失败归因：{环境, 产品缺陷, 用例缺陷, 数据问题}。
- **Score**（有序量表）用于页面符合度评分。

## Agent-as-Judge 引擎（v0.4.0）

**它与其他引擎的本质差异不在「更聪明」，而在「证据从哪来」**：

| | 其它引擎 | agent 引擎 |
| --- | --- | --- |
| 证据 | 通道预先塞进 `Evidence`，judge 只能读 | **judge 自己在运行时调工具去取** |
| 取证 | 无 | 只读工具：`query_db` / `locate_object` / `http_probe` / `read_log` / `read_file` / `inspect_trace` / `list_registry` |
| 结论 | 引擎直接给 | 模型先取证、再基于取证结果给结论 |

判定流程（`src/nlaut/judge/agent_judge.py`）：

```
system prompt（取证约束）+ objective（判定目标）
  ↓
模型输出 tool_calls → 执行只读工具 → 结果以 role="tool" 回灌
  ↓ 循环（最多 max_steps 步）
模型不再请求工具 → 解析 judgement: true|false + CONFIDENCE
  ↓
Verdict（confidence 封顶 0.85）
```

### 三条硬约束

1. **置信度封顶 0.85**（`CONFIDENCE_CAP`）——低于 `route()` 的 0.9 自动裁决线，
   所以 agent judge 的结论**必定**进人工仲裁队列。它是「证据充分的判定」，
   不是「自动放行许可」。
2. **工具只读 + 作用域白名单**——工具实现方保证不写被测系统状态；
   且 `http_probe` / `read_file` / `read_log` 受 `ToolPolicy` 约束，
   **默认拒绝式**（不配白名单即全部拒绝），越界返回结构化拒绝并记入
   `Verdict.raw["scope_violations"]`。judge 只能验证，不能改动，也不能越界窥探。
3. **降级绝不误判为通过**——端点不可用 / 无 key / 步数耗尽 / 超成本预算 →
   `confidence=0` + `raw.degraded` → `route()` 走 `human_review`。
   **「agent judge 不可用」绝不能被读成「判定通过」**。

### 成本预算与延迟熔断（v0.4.0）

| 机制 | 默认值 | 行为 |
| --- | --- | --- |
| `max_cost_usd` | 0.50 USD | 每轮调用累计成本（按 token 折算）；超预算立即降级 `max_cost_exceeded` |
| `per_call_timeout_s` | 45s | 单次 HTTP 调用硬上限；超时即熔断降级 |
| `max_retries` / `retry_backoff_s` | 2 / 0.5s | 超时 / 5xx / 429 指数退避重试（4xx 不重试——请求本身有问题） |
| `max_steps` | 6（IR 可覆盖） | 取证步数上限；耗尽降级 `max_steps_exhausted` |

> 成本单价为**保守估算**（默认 $0.6/1M token），用于预算拦截，不是账单。

### 延迟长尾的根因（2026-10-30 实测定位）

曾观察到单次判定 130s，一度怀疑是 agent 多轮累积。**压测证明不是**：

| 测量 | 结果 |
| --- | --- |
| 裸调用连续 20 次 | p50 **0.82s** / p90 4.09s / max 9.45s |
| 带工具连续 15 次 | p50 **0.98s** / max 6.86s（prompt_tok 恒定 270） |
| agent 场景模拟连续 12 次 | p50 1.44s，但**出现一次 88.49s** |
| agent 场景模拟连续 25 次 | p50 1.42s / p95 **2.02s** / **0% 超 10s** |
| agent 判定埋点（4 样本） | **100% 时间在 HTTP 调用内**，本地零开销；rounds 越多越慢（prompt token 1239→1409→1608） |

**结论**：长尾是**端点侧推理调度抖动**，与 token 数、agent 逻辑、工具数量均无关
（长尾那次 `completion_token` 仅 151，与快请求同量级）。

**因此采取两层防护**：
1. `per_call_timeout_s` 熔断 —— 45s 后不再空等
2. `max_retries` 退避重试 —— 抖动时重试即吸收（正常路径 0 重试，不增加延迟）

> ⚠️ 端点本身仍偶发 88s 级长尾，**熔断只是兜底**。根治理需找运维侧确认
> 推理调度策略，或准备备用端点（`deepseek-v4-pro-0813` 实测 tool_calls 正常）。

### 实测基线（2026-10-30，R=5 × 20 样本）

- 端点：`deepseek-v4-flash`（内网，`config/models.yaml`）
- 金标准：**20 样本**（陷阱类占 45%）× 5 轮 = **100 次判定**
- 结果：**100/100 判对 · 翻转率 0.0%**（20 样本各 5 次判定完全一致）
- 延迟：中位 **3.2s** / p95 **18.9s** / max 39.8s（退避重试生效后；加重试前 p95 为 127.3s）
- 成本：单次判定 **$0.0017**
- 降级 2 次（均来自证据本就不足的 G3 / J1-J2 样本——**是校准良好的证据**，
  判定结果仍全部正确，只是置信度诚实反映了证据强度）
- 复现：`scripts/agent_judge_repeatability.py --repeats 5`

### 环境变量

| 变量 | 作用 |
| --- | --- |
| `NLaut_AGENT_JUDGE_MODEL` | 模型 key（默认 `deepseek-v4-flash`） |
| `NLaut_AGENT_JUDGE_TOOLS` | 逗号分隔工具名子集（「有无工具」消融对照） |
| `NLaut_AGENT_JUDGE_ALLOWED_HOSTS` | `http_probe` 允许的 host（支持 `*.corp` 与 `*`） |
| `NLaut_AGENT_JUDGE_ALLOWED_PATHS` | `read_file` / `read_log` 允许的目录根 |

### 工程提示：取证效率直接决定成本与稳定性

初版 system prompt 下单样本耗时达 **290s**（agent 会反复重试同一个取证来源、
把 6 步预算用满）。加入以下约束后降到 **2.3–5s**：

- 「能用一次工具调用拿到的证据，不要拆成多次」
- 「工具返回 error 或空结果时，不要反复重试同一个来源」
- 「取证完成后立即给结论，不要再补充额外检查」

## 接入新引擎的门槛

1. 实现 `Judge` 协议（一个类）。
2. 通过 `tests/judge_golden/` 金标准回归（≥30 张标注截图，准确率 + 校准曲线）。
3. 报告层零改动——证据链由协议保证。
4. **需要运行时上下文的引擎**（如 agent）用 `register_judge_factory(name, factory)` 注册，
   import 时不构造，首次 `get_judge(name)` 时才构造。
