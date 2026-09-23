# Judge 判定协议（L5 宪法）

先定协议、后定实现：`deterministic` / `mlx-vlm` / `laya` / `jev` 四个引擎实现同一接口，
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

class Verdict(BaseModel):      # 所有引擎的统一产出
    engine: str                # "deterministic" | "mlx-vlm" | "laya" | "jev"
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

## 题型与引擎匹配

- **Noul**（0-1 概率）是 Laya/Jev 的原生题型——校准置信度、0 输出 token，判定层选它们而非
  普通 LLM 的根本原因。
- **Choice**（≤255 选项）用于失败归因：{环境, 产品缺陷, 用例缺陷, 数据问题}。
- **Score**（有序量表）用于页面符合度评分。

## 接入新引擎的门槛

1. 实现 `Judge` 协议（一个类）。
2. 通过 `tests/judge_golden/` 金标准回归（≥30 张标注截图，准确率 + 校准曲线）。
3. 报告层零改动——证据链由协议保证。
