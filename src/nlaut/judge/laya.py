"""L5 判定引擎桩（M0-poc 后实现）：laya-mlx typed decision（本地 System One）。

Noul/Choice/Score 是 Laya 的原生题型（0-1 概率 + 校准置信度 + 0 输出 token）。
jev.py 将来实现同一协议：云/本地切换零改动。
"""

from .protocol import Evidence, Question, Verdict


class LayaJudge:
    engine = "laya"

    def judge(self, evidence: Evidence, question: Question) -> Verdict:
        raise NotImplementedError("M0-poc: pip install laya-mlx 实测后实现")
