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


class _StepBase(BaseModel):
    """步骤基类：所有步骤共享 optional 语义。

    optional=True 时，执行器把「元素不存在 / 动作失败」降级为跳过（记一条
    skip 日志），不再中断整条用例。用于真实系统里「可能存在也可能不存在」
    的可选交互（如首次登录才出现的新手引导遮罩关闭按钮）。

    默认 False —— 保持既有语义不变：步骤失败即用例失败。
    """

    optional: bool = False


class StepNav(_StepBase):
    action: Literal["nav"]
    path: str


class StepFill(_StepBase):
    action: Literal["fill"]
    selector: str
    value: str  # 支持 $var 引用 data_ref 数据集字段


class StepUpload(_StepBase):
    """文件上传步骤：向 input[type=file] 塞入本地文件。

    v0.5.0 新增。背景：CeCoder 工序节点要求上传基线资产（变更资产清单、
    代码变更分析等），`fill` 无法驱动 input[type=file]——该元素不响应
    fill/type，必须用 Playwright set_input_files 直接注入文件路径。

    语义约束（执行器强制）：
    - 文件不存在 → 抛 FileNotFoundError **不静默跳过**（缺资产 = 用例该失败，
      假装传了等于伪造执行结果）；
    - 多文件要求 input 带 multiple 属性，否则 Playwright 报错——如实暴露。

    路径规则：支持 `~` 展开；相对路径按执行时 cwd（仓库根）解析。
    """

    action: Literal["upload"]
    selector: str = 'input[type=file]'
    files: list[str] = Field(min_length=1)
    timeout_ms: int = Field(default=10000, ge=100, le=60000)


class StepClick(_StepBase):
    action: Literal["click"]
    selector: str


class StepSelectOption(_StepBase):
    action: Literal["select_option"]
    selector: str
    value: str  # 下拉选项值，支持 $var


class StepWaitVisible(_StepBase):
    action: Literal["wait_visible"]
    selector: str
    timeout_ms: int = Field(default=5000, ge=100, le=60000)


class StepWaitText(_StepBase):
    """等待某段文本出现在页面上。

    真实系统的表格/列表内容由 XHR 异步渲染：等 selector 可见 ≠ 等数据就绪
    （表头可能先渲染出，行数据后到）。wait_text 等的是「数据本身出现」，
    用于消除断言与渲染之间的竞态。

    text 支持 $var 引用 data_ref 数据集字段；selector 为空时对整页 body 判断。
    """

    action: Literal["wait_text"]
    text: str
    selector: str | None = None
    timeout_ms: int = Field(default=15000, ge=100, le=60000)


class StepProcessProbe(BaseModel):
    """工序域探针步骤：在动作执行后采集工序状态快照。

    让「执行一个动作 → 观察状态迁移」成为可编排的步骤序列，
    而不只是事后扫静态快照。这是真正的状态机测试能力。

    采集结果写入 Evidence.process_probe，后续断言可据此判定状态迁移。

    v0.5.0 新增。
    """
    action: Literal["process_probe"]
    probe: Literal["snapshot", "node", "chain", "violations"] = "node"
    """采什么: node=单个节点状态（默认）; chain=整条链状态序列;
    snapshot=全量统计; violations=违规清单。"""
    task_id: str | None = None
    """限定任务；None = 全部任务（probe=chain/snapshot/violations 时用）。"""
    node: str | None = None
    """限定节点（instanceId 或 definitionId）。probe=node 时必填。"""
    root: str = "~/.cecoder/engineering-workbench"
    """工序工作台根目录。"""


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
    repeat: int = Field(default=1, ge=1, le=20)  # v0.2.7：采样次数（1-20）


Step = Annotated[
    StepNav | StepFill | StepUpload | StepClick | StepSelectOption | StepWaitVisible | StepWaitText
    | StepProcessProbe | StepApiCall | StepApiFollowup | StepApiToolCall | StepApiStream,
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


class AssertResponseTtftP95(BaseModel):
    """P95 首 token 延迟断言：跑 N 次取 P95 与阈值比对（替代单次 response_ttft 的边缘波动）。

    判定逻辑：evidence.ttft_p95_ms（由 api_stream 重复采样后计算）≤ max_p95_ms；
    若 ttft_p95_ms 缺失但 ttft_samples 存在，则用算子重算；
    全部缺失时转人工（value=0.5 conf=0.5）。
    v0.2.7 TASK-002 新增（D14 流式维度扩展）。
    """
    kind: Literal["response_ttft_p95"]
    max_p95_ms: float = Field(default=3000.0, ge=100.0)
    judge: Literal["deterministic"] = "deterministic"


class AssertVisualRegression(BaseModel):
    """视觉回归断言：当前截图 vs 基线截图的 SSIM 像素级比对。

    与 `visual_state`（VLM）的分工——两者不重复：
    - `visual_state` 判「语义对不对」（文字写错了但位置没变 → VLM 能发现）
    - 本断言判「像素变了没」（布局位移 / 样式回归 / 元素错位 → VLM 常漏）

    零 AI token：纯 OpenCV 计算 SSIM（Wang et al. 2004）。

    基线缺失时**不判通过**，而是降级为「不确定」转人工 —— 没有基线就不该有结论。
    v0.5.0 新增。
    """
    kind: Literal["visual_regression"]
    baseline: str = Field(min_length=1)  # 基线截图路径（相对仓库根或绝对路径）
    min_similarity: float = Field(default=0.98, ge=0.0, le=1.0)
    """SSIM 分数下限。低于此值判不通过。默认 0.98 —— 像素级回归必须严。"""
    require_same_size: bool = True
    """尺寸不一致时是否直接判不通过。默认 True —— resize 会掩盖布局位移。"""
    judge: Literal["deterministic"] = "deterministic"


class AssertProcessState(BaseModel):
    """工序状态机断言：读工序工作台快照，按声明的规则集判定。零 AI token。

    与 `agent_verify` 的分工——两者不可混：
    - 本断言判「结构对不对」（状态、门禁、链序、迁移合法性）——纯比对，确定性
    - `agent_verify` 判「语义对不对」（如「这份交付报告的血缘链是否真覆盖了需求→设计→程序」）

    **为什么不下沉成 agent 的 objective**：那是让模型推理算术，
    违反「能用 1° 绝不用 AI」。状态机判定是纯结构比对，必须走确定性代码。

    规则名对应 `nlaut.judge.process_truth` 里的规则（见 `RULES`）。
    v0.5.0 新增。
    """
    kind: Literal["process_state"]
    rule: str = ""
    """规则名，如 done_without_outputs / chain_order / single_current_node。

    与 ``from_probe`` 二选一：填了 from_probe 就按探针判，不查规则。"""
    expect: Literal["none", "any"] = "none"
    """期望: none=条件必须成立（无违规即通过）; any=条件必须不成立（反守门员）。"""
    task_id: str | None = None
    """限定任务；None = 全部任务。"""
    node: str | None = None
    """限定节点（instanceId 或 definitionId）；None = 全部。"""
    exclude_legacy: bool = True
    """排除 legacy 迁移任务（迁移路径天然无 run/gates/outputs）。"""
    exclude_flat_layout: bool = True
    """排除平铺布局（多工序共享任务根目录，产物归属天然错位）。"""
    min_severity: Literal["high", "medium", "low", "all"] = "medium"
    """只关心不低于该严重度的违规（low 是观察项，默认不阻断）。"""
    from_probe: str | None = None
    """改为基于 `process_probe` 步骤采集的结果判定，而非全量规则查。

    可选值:
    - ``status_eq:<值>`` —— 探针节点状态等于该值（如 status_eq:running）
    - ``status_in:<v1,v2>`` —— 状态在集合内
    - ``index_eq:<n>`` —— 探针节点序号等于 n
    - ``output_count_ge:<n>`` —— 产物数 ≥ n
    - ``has_validation_ok`` —— 探针节点的 validation_ok 为真

    与 ``rule`` 二选一：填了 from_probe 就按探针判，不查规则。"""
    judge: Literal["deterministic"] = "deterministic"


class AssertLlmVerify(BaseModel):
    """LLM-as-Judge 判定：证据已完整时的一轮 LLM 判定。

    与 `agent_verify` 的本质差异：
    - **本断言**：judge 只能读调用方拼进 prompt 的证据（Evidence.db_state /
      llm_response），无法自主调工具取证 —— 一轮调用，无多轮循环
    - `agent_verify`：judge 自己调只读工具取证，多轮循环

    适用：证据已经完整、只需要一次语义判断的场景，以及作为
    `agent_verify` 的**对照基线**（同样证据下谁判得更准）。

    降级：端点不可用 / 无 key / 响应无法解析 → `confidence=0` + `raw.degraded`，
    **绝不误判为通过**。

    v0.5.0 新增（原 llm_judge.py 是孤儿模块，生产路径走不到）。
    """
    kind: Literal["llm_verify"]
    question: str = Field(min_length=1)
    """判定问题，如「系统自述已完成，实际数据里是否真的有记录？」"""
    threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    judge: Literal["llm"] = "llm"


class AssertAgentVerify(BaseModel):
    """Agent-as-Judge 断言：judge 自主取证后判定目标是否成立。

    与其他断言的本质差异：**证据由 judge 自己去取**，而不是由通道预先塞进 Evidence。
    judge 可调用的只读工具集在运行时注入（query_db / http_probe / read_log /
    read_file / inspect_trace / list_registry）。

    判定结果置信度被强制封顶到 agent_judge.CONFIDENCE_CAP（0.85），
    因此 route() 必定把它路由到 human_review —— agent judge 的结论是
    「证据充分的判定」，不是「自动放行许可」。

    v0.4.0 新增。
    """
    kind: Literal["agent_verify"]
    objective: str = Field(min_length=1)  # 判定目标（judge 的取证任务描述）
    threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    max_steps: int = Field(default=6, ge=1, le=20)
    system_prompt: str | None = None  # 可选：覆盖默认取证约束 prompt
    judge: Literal["agent"] = "agent"


Assertion = Annotated[
    AssertTextVisible | AssertAttribute | AssertVisualState | AssertNoul | AssertChoice | AssertScore
    | AssertResponseExact | AssertResponseContains | AssertResponseNotContains
    | AssertResponseJsonSchema | AssertToolCallParams | AssertResponseTime
    | AssertResponseTtft | AssertResponseLength | AssertResponseTtftP95
    | AssertVisualRegression | AssertProcessState | AssertLlmVerify | AssertAgentVerify,
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
