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
2. **工具只读**——工具实现方保证不写被测系统状态；judge 只能验证，不能改动。
3. **降级绝不误判为通过**——端点不可用 / 无 key / 步数耗尽 → `confidence=0` +
   `raw.degraded` → `route()` 走 `human_review`。**「agent judge 不可用」绝不能被读成「判定通过」**。

### 实测基线（2026-10-30）

- 端点：`deepseek-v4-flash`（内网，`config/models.yaml`）
- 金标准：9 样本 × 3 轮 = 27 次判定 → **27/27 判对，0 降级，翻转率 0%**
- 延迟：中位 **2.9s**，p95 见 `artifacts/agent_judge_repeatability.json`
- 复现：`scripts/agent_judge_repeatability.py --repeats 3`

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
