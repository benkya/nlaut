"""L5 判定引擎桩（M0-poc 里程碑实测后实现）：mlx-vlm 视觉判定。

设计要点（实测前先锁定）：
- 模型：Qwen 系 VLM 4bit（mlx-vlm），采样温度 0
- 输入：Evidence.screenshot + 断言 prompt
- 输出：Verdict(kind="noul", value=p, confidence=自报)，raw 保留完整回答
- 门禁：接入前必须通过 tests/judge_golden 金标准回归
"""

from .protocol import Evidence, Question, Verdict


class MlxVlmJudge:
    engine = "mlx-vlm"

    def __init__(self, model: str = "mlx-community/Qwen2.5-VL-7B-Instruct-4bit") -> None:
        self.model = model

    def judge(self, evidence: Evidence, question: Question) -> Verdict:
        raise NotImplementedError(
            "M0-poc: 先装 mlx-vlm 并用标注截图集实测准确率/延迟/内存，再实现本引擎"
        )
