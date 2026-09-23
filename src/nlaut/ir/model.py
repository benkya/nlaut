"""L3 资产层：用例 IR 的唯一权威定义（Pydantic v2）。

IR 是框架的事实源：gen/ 派生代码、判定问题、报告追溯全部由它驱动。
人读版 schema 见 docs/ir-schema.md；修改本文件必须同步更新该文档并跑 make verify。
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


class StepWaitVisible(BaseModel):
    action: Literal["wait_visible"]
    selector: str
    timeout_ms: int = Field(default=5000, ge=100, le=60000)


Step = Annotated[
    StepNav | StepFill | StepClick | StepWaitVisible,
    Field(discriminator="action"),
]

# ---------- assertions：每条断言必须声明判定级别与阈值 ----------


class AssertTextVisible(BaseModel):
    kind: Literal["text_visible"]
    selector: str
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


Assertion = Annotated[
    AssertTextVisible | AssertVisualState | AssertNoul | AssertChoice | AssertScore,
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
    channel: Literal["web", "electron"]
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
