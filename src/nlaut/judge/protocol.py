"""L5 宪法：Judge 统一协议。所有判定引擎实现此接口，报告层零改动。

证据链原则：判定只能基于 Evidence 作答，不得访问外部状态；
原始输出全部进 Verdict.raw，随报告留痕。

v0.2.0 扩展：Evidence 增加 llm_response / llm_response_json / tool_calls /
latency_ms / conversation 字段，支持 API 通道判定。
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

QuestionKind = Literal["noul", "choice", "score"]
EngineName = Literal["deterministic", "mlx-vlm", "laya", "jev"]


class Evidence(BaseModel):
    """判定输入的证据包：执行器产出，判定引擎只读。

    通道与字段对应：
    - web 通道：screenshot + dom_state
    - api 通道：llm_response + response + tool_calls + latency_ms + conversation
    """

    case_id: str
    screenshot: str | None = None  # artifacts 相对路径
    dom_state: dict[str, str] | None = None  # selector -> 可见文本
    response: dict | None = None  # API 原始 JSON 响应
    db_state: dict | None = None  # 关键表行数/状态
    # --- v0.2.0 API 通道新增 ---
    llm_response: str | None = None  # LLM 文本输出（assistant message content）
    llm_response_json: dict | None = None  # 解析后的 JSON（当 response_json_schema 断言时填充）
    tool_calls: list[dict] | None = None  # 工具调用列表（OpenAI tool_calls 格式）
    latency_ms: float | None = None  # 响应延迟（毫秒）
    conversation: list[dict] | None = None  # 多轮对话消息历史


class Question(BaseModel):
    """判定问题：题型 + 文本 + 可选项。context 携带引擎专有参数（selector 等）。"""

    kind: QuestionKind
    text: str
    options: list[str] | None = None  # choice 必填
    context: dict = Field(default_factory=dict)


class Verdict(BaseModel):
    """所有引擎的统一产出：值 + 自报置信度 + 证据引用 + 原始输出。"""

    engine: EngineName | str
    kind: QuestionKind
    value: float | str | int  # noul: p∈[0,1] / choice: 选项 / score: 分值
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)
    raw: dict = Field(default_factory=dict)  # 引擎原始输出，全程留痕


@runtime_checkable
class Judge(Protocol):
    def judge(self, evidence: Evidence, question: Question) -> Verdict: ...


_JUDGES: dict[str, Judge] = {}


def register_judge(name: str, judge: Judge) -> None:
    _JUDGES[name] = judge


def get_judge(name: str) -> Judge:
    try:
        return _JUDGES[name]
    except KeyError:
        raise KeyError(f"未注册判定引擎: {name}（已注册: {sorted(_JUDGES)}）") from None
