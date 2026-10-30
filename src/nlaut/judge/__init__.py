"""L5 判定层：统一 Judge 协议 + 引擎注册表 + 置信度路由。

先定协议、后定实现：deterministic / mlx-vlm / laya / agent 实现同一接口，可插拔切换。
协议规范见 docs/judge-protocol.md。
"""

import os

from .arbiter import RouteDecision, route
from .deterministic import DeterministicJudge
from .laya import LayaJudge
from .protocol import (
    Evidence,
    Judge,
    Question,
    Verdict,
    get_judge,
    register_judge,
    register_judge_factory,
)
from .vlm_mlx import MlxVlmJudge

register_judge("deterministic", DeterministicJudge())
register_judge("mlx-vlm", MlxVlmJudge())  # 模型懒加载，import 零开销
register_judge("laya", LayaJudge())  # 同上：首次 judge 时才加载权重


def _build_agent_judge() -> Judge:
    """构造 Agent-as-Judge 引擎（工厂模式，首次 get_judge("agent") 时调用）。

    模型端点从 config/models.yaml 读取，key 从 api_key_env 指定的环境变量读取。
    取证工具集为只读工具，默认开启；可用 NLaut_AGENT_JUDGE_TOOLS 环境变量
    覆盖为逗号分隔的工具名子集（用于做「有无工具」的消融对照实验）。

    构造失败（端点缺失 / 模型未配置）时抛出 —— 由调用方决定降级策略，
    不在这里静默返回 None（静默降级会让「agent judge 不可用」被误读成
    「agent judge 判定通过」）。
    """
    from .agent_judge import build_agent_judge_from_env
    from .agent_tools import DEFAULT_TOOLS, ToolPolicy, build_env_tool_impls

    model_key = os.environ.get("NLaut_AGENT_JUDGE_MODEL", "deepseek-v4-flash")

    tool_names = os.environ.get("NLaut_AGENT_JUDGE_TOOLS")
    if tool_names:
        wanted = {t.strip() for t in tool_names.split(",") if t.strip()}
        tools = [t for t in DEFAULT_TOOLS if t["function"]["name"] in wanted]
    else:
        tools = list(DEFAULT_TOOLS)

    # 作用域策略：默认拒绝式（http_probe / read_file / read_log 全部拒绝），
    # 由 NLaut_AGENT_JUDGE_ALLOWED_HOSTS / _ALLOWED_PATHS 显式放行。
    policy = ToolPolicy.from_env()

    impls = build_env_tool_impls(policy=policy)
    return build_agent_judge_from_env(
        tools=tools,
        tool_impls=impls,
        model_key=model_key,
        policy=policy,
    )


register_judge_factory("agent", _build_agent_judge)

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
    "register_judge_factory",
    "route",
]
