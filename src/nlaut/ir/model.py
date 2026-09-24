"""L3 资产层：用例 IR 的唯一权威定义（Pydantic v2）。

IR 是框架的事实源：gen/ 派生代码、判定问题、报告追溯全部由它驱动。
人读版 schema 见 docs/ir-schema.md；修改本文件必须同步更新该文档并跑 make verify。

v0.2.0 扩展：新增 API 通道步骤（api_call/api_followup/api_tool_call/api_stream）
和 LLM 响应断言（response_exact/response_contains/response_not_contains/
response_json_schema/tool_call_params/response_time）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field

# ---------- steps：声明式动作（禁止代码式步骤，保证换通道不漂移） ----------


class StepNav(BaseModel):
    action: Literal["nav"]
    path: str


class StepFill(BaseModel):
    action: Literal["fill"]
    selector: str
    value: str  # 支持 $var 引用 data_ref 数据集字段


class StepClick(BaseModel):
    action: Literal["click"]
    selector: str


class StepSelectOption(BaseModel):
    action: Literal["select_option"]
    selector: str
    value: str  # 下拉选项值，支持 $var


class StepWaitVisible(BaseModel):
    action: Literal["wait_visible"]
    selector: str
    timeout_ms: int = Field(default=5000, ge=100, le=60000)


# --- API 通道步骤（v0.2.0 新增）---


class StepApiCall(BaseModel):
    """单轮 API 调用：发送 prompt 到 LLM，获取响应。"""
    action: Literal["api_call"]
    prompt: str  # 支持 $var 引用 data_ref 数据集字段
    system_prompt: str | None = None  # 可选 system prompt


class StepApiFollowup(BaseModel):
    """多轮对话后续消息：在已有对话上下文基础上追加用户消息并再次调用。"""
    action: Literal["api_followup"]
    prompt: str


class StepApiToolCall(BaseModel):
    """带工具定义的 API 调用：发送 prompt + tools 定义，验证模型是否正确调用工具。"""
    action: Literal["api_tool_call"]
    prompt: str
    tools: list[dict]  # OpenAI function calling schema


class StepApiStream(BaseModel):
    """流式 API 调用：stream=true，验证流式输出完整性和首 token 延迟。"""
    action: Literal["api_stream"]
    prompt: str
    system_prompt: str | None = None


Step = Annotated[
    StepNav | StepFill | StepClick | StepSelectOption | StepWaitVisible
    | StepApiCall | StepApiFollowup | StepApiToolCall | StepApiStream,
    Field(discriminator="action"),
]

# ---------- assertions：每条断言必须声明判定级别与阈值 ----------


class AssertTextVisible(BaseModel):
    kind: Literal["text_visible"]
    selector: str
    expected: str
    negated: bool = False
    judge: Literal["deterministic"] = "deterministic"


class AssertAttribute(BaseModel):
    """DOM 属性断言：直接读 element.getAttribute(name) 或 element.disabled / element.value 等。

    name 支持普通属性名（class/id/aria-*）以及三个特殊 alias：
    - 'disabled' → element.disabled (返回 boolean)
    - 'value'    → element.value (输入框文本)
    - 'data:KEY' → element.getAttribute('data-KEY') (data-* 属性)
    """

    kind: Literal["attribute"]
    selector: str
    name: str
    expected: str
    negated: bool = False
    judge: Literal["deterministic"] = "deterministic"


class AssertVisualState(BaseModel):
    kind: Literal["visual_state"]
    prompt: str
    threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    judge: Literal["vlm"] = "vlm"


class AssertNoul(BaseModel):
    kind: Literal["noul"]
    question: str
    threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    judge: Literal["systemone"] = "systemone"


class AssertChoice(BaseModel):
    """choice 断言必须提供 options（校验器强制，防止开放式归因）。"""

    kind: Literal["choice"]
    question: str
    options: list[str] = Field(min_length=1)
    threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    judge: Literal["systemone"] = "systemone"


class AssertScore(BaseModel):
    kind: Literal["score"]
    question: str
    threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    judge: Literal["systemone"] = "systemone"


# --- LLM 响应断言（v0.2.0 新增）---


class AssertResponseExact(BaseModel):
    """L1 精确匹配：LLM 响应与 expected 完全匹配（数学/事实问答）。

    判定逻辑：strip 后字符串等值比较，默认大小写不敏感。
    """
    kind: Literal["response_exact"]
    expected: str
    case_sensitive: bool = False
    judge: Literal["deterministic"] = "deterministic"


class AssertResponseContains(BaseModel):
    """L2 关键词匹配：LLM 响应中包含至少 min_match 个关键词。

    判定逻辑：关键词在响应文本中出现（大小写不敏感），命中数 >= min_match 则通过。
    """
    kind: Literal["response_contains"]
    keywords: list[str] = Field(min_length=1)
    min_match: int = Field(default=1, ge=1)
    judge: Literal["deterministic"] = "deterministic"


class AssertResponseNotContains(BaseModel):
    """L4 反向验证：LLM 响应中不包含任何 forbidden 中的词（安全性测试）。

    判定逻辑：forbidden 中任意词出现在响应中即失败。
    """
    kind: Literal["response_not_contains"]
    forbidden: list[str] = Field(min_length=1)
    judge: Literal["deterministic"] = "deterministic"


class AssertResponseJsonSchema(BaseModel):
    """L3 JSON 结构验证：LLM 响应为合法 JSON 且包含 required_fields。

    判定逻辑：
    1. 响应可解析为 JSON
    2. required_fields 中所有字段存在
    3. expected_values 中所有键值匹配
    """
    kind: Literal["response_json_schema"]
    required_fields: list[str] = Field(default_factory=list)
    expected_values: dict[str, str] = Field(default_factory=dict)
    judge: Literal["deterministic"] = "deterministic"


class AssertToolCallParams(BaseModel):
    """工具调用参数验证：模型调用了 expected_function 且参数匹配 expected_params。

    判定逻辑：
    1. evidence.tool_calls 中存在 function.name == expected_function 的调用
    2. expected_params 中所有键值与模型实际参数匹配（字符串等值）
    """
    kind: Literal["tool_call_params"]
    expected_function: str
    expected_params: dict[str, str] = Field(default_factory=dict)
    judge: Literal["deterministic"] = "deterministic"


class AssertResponseTime(BaseModel):
    """性能断言：响应时间不超过 max_seconds。

    判定逻辑：evidence.latency_ms <= max_seconds * 1000。
    """
    kind: Literal["response_time"]
    max_seconds: float = Field(default=30.0, ge=0.1)
    judge: Literal["deterministic"] = "deterministic"


class AssertResponseTtft(BaseModel):
    """流式性能断言：首 token 延迟（TTFT）不超过 max_ms。

    判定逻辑：evidence.ttft_ms <= max_ms（仅流式调用有 ttft_ms；缺失时转人工）。
    v0.2.4 Phase 5 新增（D14 流式维度）。
    """
    kind: Literal["response_ttft"]
    max_ms: float = Field(default=3000.0, ge=100.0)
    judge: Literal["deterministic"] = "deterministic"


class AssertResponseLength(BaseModel):
    """长度断言：回答字符数在 [min_chars, max_chars] 区间（含标点、含空白）。

    判定逻辑：min_chars <= len(evidence.llm_response) <= max_chars。
    v0.2.4 Phase 5 新增（D15 字数约束的确定性表达，替代"恰50字"的不可判定描述）。
    """
    kind: Literal["response_length"]
    min_chars: int = Field(default=1, ge=0)
    max_chars: int = Field(default=10000, ge=1)
    judge: Literal["deterministic"] = "deterministic"


Assertion = Annotated[
    AssertTextVisible | AssertAttribute | AssertVisualState | AssertNoul | AssertChoice | AssertScore
    | AssertResponseExact | AssertResponseContains | AssertResponseNotContains
    | AssertResponseJsonSchema | AssertToolCallParams | AssertResponseTime
    | AssertResponseTtft | AssertResponseLength,
    Field(discriminator="kind"),
]


class Quad(BaseModel):
    """语义四元组：pipeline.semantic 的产出、generate 的输入。"""

    actor: str
    action: str
    object: str
    preconditions: list[str] = Field(default_factory=list)


class Postcondition(BaseModel):
    cleanup_tag: str | None = None  # 数据还原钩子，如 TEST_AUTO_


class TestCaseIR(BaseModel):
    """一条测试用例的中间表示。source 是追溯锚点，禁止为空。"""

    __test__ = False  # pydantic 模型，禁止 pytest 误收集

    id: str = Field(pattern=r"^tc_[a-z0-9_]+$")
    title: str = Field(min_length=1)
    source: str = Field(min_length=1)
    req_ref: str | None = None
    priority: Literal["P0", "P1", "P2"] = "P1"
    channel: Literal["web", "electron", "api"]
    quad: Quad
    data_ref: str | None = None
    steps: list[Step] = Field(min_length=1)
    assertions: list[Assertion] = Field(min_length=1)
    postconditions: list[Postcondition] = Field(default_factory=list)

    @classmethod
    def from_yaml_file(cls, path: str | Path) -> TestCaseIR:
        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), allow_unicode=True, sort_keys=False)
