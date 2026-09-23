"""L5 判定层：统一 Judge 协议 + 引擎注册表 + 置信度路由。

先定协议、后定实现：deterministic / mlx-vlm / laya / jev 实现同一接口，可插拔切换。
协议规范见 docs/judge-protocol.md。
"""

from .arbiter import RouteDecision, route
from .deterministic import DeterministicJudge
from .protocol import Evidence, Judge, Question, Verdict, get_judge, register_judge
from .vlm_mlx import MlxVlmJudge

register_judge("deterministic", DeterministicJudge())
register_judge("mlx-vlm", MlxVlmJudge())  # 模型懒加载，import 零开销

__all__ = [
    "DeterministicJudge",
    "Evidence",
    "Judge",
    "MlxVlmJudge",
    "Question",
    "RouteDecision",
    "Verdict",
    "get_judge",
    "register_judge",
    "route",
]
