# Agent-as-Judge 引擎接口设计稿（L5 判定层 v0.4.0）

> 状态：**设计稿**。本文是 `src/nlaut/judge/agent_judge.py` + `agent_tools.py` 的接口规格说明与**剩余缺口**的设计，
> 不是从零新建的方案——仓库里已有可运行原型（见 §0），本文档负责把它收口成可交付的接口契约。
> 配套文档：`docs/agent-as-judge-poc-plan.md`（G-11 场景的验证与度量方案）。
>
> **所有代码引用均为实际源码**，格式 `path:line`。未实现的未来设计明确标注「待做」。

---

## 0. 基线：仓库里已有什么（先核实，不臆造）

本分支 `feature/agent-as-judge` 上已存在 Agent-as-Judge 的**可运行原型**（未提交，`git status` 显示 untracked / modified）：

| 文件 | 状态 | 内容摘要 |
| --- | --- | --- |
| `src/nlaut/judge/agent_judge.py` | untracked, 372 行 | `AgentJudge` 类：多轮 OpenAI-compatible function calling 循环、`CONFIDENCE_CAP=0.85` 封顶、降级路径、`raw["trace"]` 全程留痕 |
| `src/nlaut/judge/agent_tools.py` | untracked, 265 行 | 6 个**只读**取证工具的 schema + 实现 |
| `tests/agent_judge_test.py` | untracked, 411 行 | 30 个元测试 + 8 样本金标准集 + `-m agent` 真实端点测试 |
| `src/nlaut/ir/model.py` | **已改** | 新增 `AssertAgentVerify`（`objective` 字段）并入 `Assertion` union |
| `src/nlaut/executor/runner.py` | **已改** | `_engine_name` 加 `agent_verify → "agent"`；`_question_for` 加翻译分支 |
| `src/nlaut/judge/protocol.py` | **已改** | 新增 `register_judge_factory` 工厂型注册机制 |
| `src/nlaut/judge/__init__.py` | **已改** | `register_judge_factory("agent", _build_agent_judge)` |
| `docs/ir-schema.md` | **已改** | 断言表加 `agent_verify` 行 + 专章 |
| `pyproject.toml` | **已改** | 新增 `agent` pytest marker，默认 `addopts` 排除 |
| `cases/demo/tc_agent_verify_001.yaml` | untracked | G-11 场景示例用例 |

**实测状态（本次核实）**：
- `.venv/bin/python -m pytest tests/ -q` → **135 passed**（全绿）。
- `.venv/bin/ruff check src/nlaut/judge/agent_judge.py src/nlaut/judge/agent_tools.py src/nlaut/judge/__init__.py src/nlaut/ir/model.py src/nlaut/executor/runner.py tests/agent_judge_test.py` → **All checks passed**。
  （注：`ruff check .` 全仓报 39 errors，**全部集中在 `scripts/` 与 `tests/` 的既有文件**，`git stash` 验证为改动前既存问题，与本特性无关。）

### 0.1 L5 宪法现状（引用核实）

| 事实 | 位置 |
| --- | --- |
| `Judge` 协议只有一个方法 `judge(evidence, question) -> Verdict` | `src/nlaut/judge/protocol.py:68-70` |
| `register_judge` / `get_judge` 注册表；未注册抛 `KeyError` | `src/nlaut/judge/protocol.py:73-84` |
| **新增** `register_judge_factory(name, factory)`：按需构造，构造后缓存 | `src/nlaut/judge/protocol.py:76-90` |
| `Evidence` 是纯 pydantic v2 BaseModel，全字段带默认值 | `src/nlaut/judge/protocol.py:20-44` |
| `Question.kind = Literal["noul","choice","score"]`，`context: dict` 承载引擎专有参数 | `src/nlaut/judge/protocol.py:16,47-53` |
| `Verdict.engine` 类型是 `EngineName \| str`——运行时**已**接受任意字符串 | `src/nlaut/judge/protocol.py:17,59` |
| `EngineName = Literal["deterministic","mlx-vlm","laya","jev"]`——**`"agent"` 尚未加入**（待做）；`jev` 只有字面量无实现 | `src/nlaut/judge/protocol.py:17` |
| 路由：`deterministic` 走 `value>=0.5` 直裁；其余按 confidence 三档（<0.5 / 0.5–0.9 / ≥0.9） | `src/nlaut/judge/arbiter.py:26-42` |
| `route()` 的 0.9 阈值**硬编码**，不能靠参数调 | `src/nlaut/judge/arbiter.py:34` |
| `threshold` 参数只作用于最后一行的 `value >= threshold` | `src/nlaut/judge/arbiter.py:38` |
| `db_state` 字段在 Evidence 中已有；**执行器无写入方**（`grep db_state src/nlaut/executor/` 无命中），但 judge 层有消费方 | `src/nlaut/judge/protocol.py:32`；`src/nlaut/judge/agent_tools.py:145-158` |
| API 通道用 `httpx.Client(trust_env=False)`，`body["tools"]` 直接透传 | `src/nlaut/executor/channels/api.py:116-117,204` |
| API 通道解析 tool_calls：非流式 `choices[0].message.tool_calls`；流式按 index 累积 arguments | `src/nlaut/executor/channels/api.py:314-317,168-180` |
| 断言→引擎路由硬编码 if/元组，**漏注册会静默落到 laya** | `src/nlaut/executor/runner.py:131-145` |
| 报告层 `engine == "deterministic"` 归确定性桶，其余归 AI 判定桶 | `src/nlaut/report/render.py:49-55` |
| 判定层可用性守卫测试（httpx/socksio 缺失导致判定层整体崩，2026-10-27 事故） | `tests/judge_availability_guard.py:1-79` |

### 0.2 本设计要解决的问题（原型留下的缺口）

原型解决了「能不能跑」，**没有**解决「能不能挡住假阳性、能不能稳定、能不能上线」。逐条对应本文各节：

| # | 缺口 | 本文对应 |
| --- | --- | --- |
| G1 | 工具**无作用域白名单**（`http_probe` 任意 URL、`read_log`/`read_file` 任意路径）→ Agent 可越界 | §3.1 |
| G2 | `Evidence` 无「操作前/后快照」→ 无法表达增量，反向陷阱判不准 | §5.2 |
| G3 | `max_cost_usd` 参数**声明了但无拦截逻辑**（读 `agent_judge.py` 未见使用） | §3.3 |
| G4 | `httpx.post(timeout=)` 是**单请求超时**，非整体墙钟 | §3.3 |
| G5 | `parse_judgement` 有**关键词启发式兜底**，会把推理中间句误判为最终结论 | §3.4 |
| G6 | `value=0.5` 同时表示「解析失败」与「证据不足」，两态不可区分 | §4.1 |
| G7 | `EngineName` 未加 `"agent"`；`route()` 无 agent 专属说明 | §4.3 / §5.2 |
| G8 | 取证结果只留在 `Verdict.raw["trace"]`，**未回填 Evidence** → L5 宪法「判定基于 Evidence 作答」在 agent 引擎上不闭合 | §5.3 |
| G9 | 无「Agent 特有断言」的第二种形态（过程验证） | §7.2 |

---

## 1. (a) 引擎定位：新 Judge 实现，还是新执行通道？

### 结论：**新 Judge 实现（`AgentJudge`），不是新 Executor Channel。取证能力通过只读工具层注入。**

原型已按此实现（`src/nlaut/judge/agent_judge.py`），本文予以确认并给出理由。

### 理由

**理由 1：Judge 侧新增 = 报告层零改动。**
`docs/judge-protocol.md:48-52` 明文规定「接入新引擎的门槛：实现 Judge 协议 / 金标准回归 / **报告层零改动**」。`Verdict.raw: dict` 是「引擎原始输出，全程留痕」的通用容器（`src/nlaut/judge/protocol.py:64`），Agent 的整条工具轨迹天然落在这里。`report/render.py:49-55` 的分桶逻辑把非 deterministic 引擎全归「AI 判定」桶——`agent` 自动落入，**报告模板零改动**。

**理由 2：Executor Channel 的语义是「产生被测系统的事实」，Agent 取证是「产生判定依据」。**
`src/nlaut/executor/channels/*.py` 三个通道的 `execute(ir, ...) -> dict` 契约是：按 IR 的 steps 操作被测系统，返回 Evidence 字段（`api.py:331-346`、`electron.py:101`）。这与「judge 自己决定查什么」正交——Agent 的调查步骤**不在 IR 里**，是运行时产生的，无法预先声明为 IR steps（IR steps 是「声明式动作（禁止代码式步骤）」（`src/nlaut/ir/model.py:19`），Agent 的动作恰恰是代码式的、运行时决定的）。

**理由 3：通道层会引入「双重执行」和状态污染。**
若把 Agent 调查做成通道，则 Agent 的每次 `http_probe` 都可能再次触发被测系统，导致：
- 被测系统被测两次，postconditions 的数据还原钩子（`src/nlaut/executor/runner.py:275-295`）来不及还原 → 污染后续用例；
- 用例耗时与成本翻倍且不可预测；
- IR 里同一份 steps 被两个阶段消费，语义歧义。

**理由 4：但「工具」必须能触碰执行层/基础设施层，否则 Agent 无处取真。**
`Evidence` 是**快照**，只包含通道执行那一刻的状态。Agent 要验证「订单是否真的写库了」，得能查 db；要看「模型当时调了哪个工具」，得能翻 trace。原型已用**依赖注入**解决：`build_tool_impls(db_state=..., http_probe=..., log_reader=..., file_reader=..., trace=..., registry=...)`（`src/nlaut/judge/agent_tools.py:124-201`）——工具实现由**调用方注入**，引擎不自己去找数据源。这比「工具层自己去连 DB」更干净，也满足 §3.1 的白名单要求。

### 边界（硬约束，写进 `agent_tools.py` 的 docstring）

| 允许 | 禁止 |
| --- | --- |
| 只读查询被测系统已有状态（db 快照 / GET 探针 / 日志 / 文件 / 已发生的 tool_calls） | 任何**写**操作（POST/PUT/DELETE、UI 变更、写文件） |
| 读 `artifacts/` 下本次 run 已产生的产物 | 读**其他 case / 其他 run** 的产物（防串案） |
| 跑**只读**断言脚本 | 有副作用的脚本 |
| 读 `Evidence` 已有字段 | 修改 `Evidence` |

**唯一允许的"写"是会话日志留痕**（`SessionLog.record`，`src/nlaut/harness/session.py:23-29`），它本来就是 append-only 审计流。

### 与 L5 宪法的调和（必须同步到 `docs/judge-protocol.md`）

原宪法首行是「判定只能基于 Evidence 作答，不得访问外部状态」（`src/nlaut/judge/protocol.py:2-3`）。修订为：

> 判定基于 **Evidence + 只读取证工具返回的取证结果** 作答。
> - 除 `AgentJudge` 外，所有引擎**不得**访问外部状态（`deterministic` / `mlx-vlm` / `laya` 维持原约束）。
> - `AgentJudge` 通过只读工具访问外部状态，且工具实现**必须由调用方注入**（依赖注入），使取证范围可被白名单约束。
> - 取证轨迹必须完整写入 `Verdict.raw["trace"]`（可回放审计），并**回填** `Evidence.agent_findings`（见 §5.3）。

---

## 2. (b) 工具集设计

### 2.1 工具清单（6 个，全部只读）

原型已实现于 `src/nlaut/judge/agent_tools.py`，本文确认其清单并补白名单（§3.1）。

| 工具名 | 用途 | AJ-Bench 维度 | 数据源 | 只读保证 | 白名单状态 |
| --- | --- | --- | --- | --- | --- |
| `query_db` | 查表行数与关键字段 | state verification | 注入的 `db_state` 快照 | 只读快照 | ✅ 表名须在 `db_state` 键内（`agent_tools.py:151-154`） |
| `http_probe` | 对 URL 发 GET，拿状态码与响应体 | state verification | httpx GET | 只读 GET | ❌ **待做**（§3.1） |
| `read_log` | 读日志文件尾部 | information acquisition | 注入的 log reader | 只读 | ❌ **待做**（§3.1） |
| `read_file` | 读指定文件 | information acquisition | 注入的 file reader | 只读 | ❌ **待做**（§3.1） |
| `inspect_trace` | 翻执行轨迹（历史 tool_calls / 对话） | process verification | 注入的 `trace` 列表 | 只读 | ✅ 只能读传入的 trace |
| `list_registry` | 列模块注册信息 | state verification | 注入的 `registry` | 只读 | ✅ 只能读传入的 registry |

> **与 AJ-Bench 三维度的对应**（`agent_tools.py:6-9` 的 docstring 已声明）：信息获取 = 查接口/查表/翻日志/读文件；状态验证 = 读当前实际状态；过程验证 = 翻执行轨迹。
>
> **注意**：`information acquisition` 是引擎的**内生能力**（judge 自己决定查什么），**不是**一种断言类型——所以 IR 层只有 `agent_verify` 一种 agent 断言（§7）。

### 2.2 OpenAI tools schema 规格（已实现，此处为契约固化）

格式对齐 `src/nlaut/executor/channels/api.py:116-117` 的 `body["tools"] = tools`——标准 OpenAI function calling 数组。原型已实现的 6 个 schema（`agent_tools.py:21-121`）：

```python
"""Agent-as-Judge 取证工具集：OpenAI tools schema 规格（契约固化）。

工具全部只读——Agent-as-Judge 只允许「查」，不允许「改」被测系统。
实现方保证只读语义；作用域白名单见 §3.1。
"""

from __future__ import annotations

from typing import Any

# 工具名常量（实现中已用字面量，此处固化契约）
TOOL_QUERY_DB = "query_db"
TOOL_HTTP_PROBE = "http_probe"
TOOL_READ_LOG = "read_log"
TOOL_READ_FILE = "read_file"
TOOL_INSPECT_TRACE = "inspect_trace"
TOOL_LIST_REGISTRY = "list_registry"

# 单个 tool_calls 条目的形状（OpenAI 标准格式）：
# {"id": str, "type": "function",
#  "function": {"name": str, "arguments": str(JSON字符串)}}
#
# ⚠️ arguments 是 JSON **字符串**，不是 dict——必须 json.loads 后取参。
#    参考既有做法 src/nlaut/judge/deterministic.py:304。
#
# 工具结果以 role="tool" 消息回灌，content 必须是 JSON **字符串**：
# {"role": "tool", "tool_call_id": str, "content": str(JSON)}
#    参考既有做法 src/nlaut/judge/agent_judge.py:260-264。

QUERY_DB_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "query_db",
        "description": "查询数据库中某张表的当前行数与关键字段内容，用于验证数据是否真的落库/落表。",
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "表名"},
                "limit": {"type": "integer", "description": "最多返回几行，默认 20"},
            },
            "required": ["table"],
        },
    },
}

HTTP_PROBE_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "http_probe",
        "description": "对给定 URL 发起只读 GET 请求，返回状态码与响应体，用于验证模块对外接口是否可用、数据是否返回。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整 URL（必须在白名单内）"},
                "timeout_s": {"type": "number", "description": "超时秒数，默认 15"},
            },
            "required": ["url"],
        },
    },
}

READ_LOG_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "read_log",
        "description": "读取日志文件尾部内容，用于验证执行过程是否留痕、是否有错误。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "日志文件路径（必须在白名单内）"},
                "tail_lines": {"type": "integer", "description": "读取末尾多少行，默认 50"},
            },
            "required": ["path"],
        },
    },
}

READ_FILE_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "读取指定文件的内容（只读），用于核对配置、契约、模块注册信息。",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径（必须在白名单内）"},
                "max_bytes": {"type": "integer", "description": "最多读取字节数，默认 8000"},
            },
            "required": ["path"],
        },
    },
}

INSPECT_TRACE_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "inspect_trace",
        "description": "查看被测系统的执行轨迹（历史 tool_calls 与对话），用于验证过程是否走了正确路径。",
        "parameters": {
            "type": "object",
            "properties": {
                "start": {"type": "integer", "description": "起始下标，默认 0"},
                "count": {"type": "integer", "description": "返回条数，默认 20"},
            },
            "required": [],
        },
    },
}

LIST_REGISTRY_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "list_registry",
        "description": "列出当前可用的模块注册信息（模块名、是否启用、注册时间），用于验证模块是否真的完成注册。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

DEFAULT_TOOLS: list[dict] = [
    QUERY_DB_TOOL,
    HTTP_PROBE_TOOL,
    READ_LOG_TOOL,
    READ_FILE_TOOL,
    INSPECT_TRACE_TOOL,
    LIST_REGISTRY_TOOL,
]
```

**实测校验**（本次已执行）：`DEFAULT_TOOLS` JSON 可序列化（5301 字节），6 个工具名与上表一致，每个 `function` 含 `name`/`description`/`parameters` 三键，`parameters.type == "object"`。原型既有测试 `test_default_tools_all_have_schemas`（`tests/agent_judge_test.py:132-136`）已覆盖基本形状。

### 2.3 工具执行契约（已实现）

原型实现于 `AgentJudge._run_tool`（`src/nlaut/judge/agent_judge.py:144-155`）：

| 场景 | 返回 | 位置 |
| --- | --- | --- |
| 工具名未注册 | `{"error": "unknown_tool:<name>", "hint": "该工具不可用，勿据此下结论"}` | `agent_judge.py:148` |
| 工具实现抛异常 | `{"error": "<ExcName>: <msg>"}`（**不冒泡**） | `agent_judge.py:151-152` |
| 正常返回 | 原样（dict/list/str/int/float/bool/None；其它类型 `str()` 化） | `agent_judge.py:153-154` |
| db_state 未注入 | `{"error": "db_state_not_provided", "hint": "本次判定未接入数据库快照"}` | `agent_tools.py:149` |
| 表不在快照内 | `{"error": "table_not_found", "available_tables": [...]}` | `agent_tools.py:151-154` |

**关键设计：工具错误不中断循环**——错误作为普通结果回灌给模型，由模型自行决定换一条取证路径。这与 `src/nlaut/judge/deterministic.py` 各分支「解析失败即产出确定性 Verdict」的风格一致；但在 agent 引擎里，**工具失败不产出 Verdict**，只产出工具错误。

**回灌格式**（`agent_judge.py:260-264`）：

```python
messages.append({
    "role": "tool",
    "tool_call_id": tc.get("id", ""),
    "content": json.dumps(result, ensure_ascii=False)[:4000],
})
```

> ⚠️ **待做**：回灌消息**缺 `name` 字段**。部分 OpenAI-compatible 端点要求 tool 消息带 `name` 以便模型对齐。`src/nlaut/executor/channels/api.py` 的既有解析只读 `function.name`，所以 nlaut 内部不受影响；但对外部端点，建议补 `"name": fname`。

### 2.4 工具可用性（原型已用「错误回灌」处理，未做启动探测）

原型**没有**启动探测——不可用的工具照样注册给模型，模型调了才拿到 `{"error": "..."}`。

**对比建议**：nlaut 已有「判定层可用性守卫」的教训（`tests/judge_availability_guard.py:8-13`：httpx 缺 socksio 让判定层整体不可用，P0 通过率从 44/46 暴跌到 18/46，**看起来像模型能力退化**）。

**建议（待做）**：在 `AgentJudge` 构造时探测工具可用性，不可用则**不进 tools 数组**，并在 `raw["tool_availability"]` 留痕。探测规则：

| 工具 | 可用条件 |
| --- | --- |
| `query_db` / `inspect_trace` / `list_registry` | 注入的对应数据源非 None |
| `http_probe` | 注入了 `http_probe` 实现 或 提供了 URL 白名单 |
| `read_log` / `read_file` | 注入了对应 reader 实现 或 提供了路径白名单 |

**但**：`build_env_tool_impls()`（`agent_tools.py:239-254`）会给 `http_probe`/`read_log`/`read_file` 装上默认实现（真实读文件、发 GET），所以默认情况下这 3 个**总是可用**。真正的白名单约束必须靠 §3.1 的作用域限制来保证——**这是白名单比探测更重要的原因**。

---

## 3. (c) 循环控制

### 3.1 工具作用域白名单（**最重要的待做项**，对应缺口 G1）

**现状**：`_run_tool` 只查 `tool_impls` 里有没有这个工具名（`agent_judge.py:146-147`），**没有任何路径/URL 约束**。`default_http_probe`（`agent_tools.py:227-236`）可以 GET 任意 URL；`default_log_reader` / `default_file_reader`（`agent_tools.py:204-224`）可以读任意路径。

**风险**（PoC 方案 §5.1 的 C3 契约）：Agent 越界去查「模块内部逻辑」——违反 G-11 的责任边界红线（R1/R2）。

**设计：引入 `ToolPolicy` 白名单对象**

```python
# src/nlaut/judge/agent_tools.py（新增）
from pydantic import BaseModel, Field


class ToolPolicy(BaseModel):
    """工具作用域白名单——把「只读」从约定升级为**可强制执行**的约束。

    只读性由「实现方保证」是不够的（agent_tools.py 的 docstring 依赖于此），
    作用域必须由框架侧强制，否则越界不可检测、不可度量。

    语义：
    - allowed_url_prefixes：空列表 = **禁止 http_probe**（不是放行全部）
    - allowed_path_prefixes：空列表 = **禁止 read_log / read_file**
    - allowed_tables：空列表 = 回退到 db_state 自身的键（即原型行为）
    """

    allowed_url_prefixes: list[str] = Field(default_factory=list)
    allowed_path_prefixes: list[str] = Field(default_factory=list)
    allowed_tables: list[str] = Field(default_factory=list)
    # 允许 Agent 读 SQL 以外的自定义只读接口（语义标注用）
    allowed_domains: list[str] = Field(default_factory=list)
```

**接入点**：工具实现构造时接收 policy，越权即返回结构化错误（**不是抛异常**，与既有 `unknown_tool` 风格一致）：

```python
def _http_probe(args: dict) -> Any:
    url = args.get("url", "")
    if not policy.allowed_url_prefixes:
        return {"error": "http_probe_not_allowed", "hint": "本次判定未授权任何 URL 探针"}
    if not any(url.startswith(p) for p in policy.allowed_url_prefixes):
        return {"error": "url_not_in_allowlist", "url": url,
                "allowed_prefixes": policy.allowed_url_prefixes}
    ...

def _read_file(args: dict) -> Any:
    path = args.get("path", "")
    if not policy.allowed_path_prefixes:
        return {"error": "read_file_not_allowed"}
    if not any(path.startswith(p) for p in policy.allowed_path_prefixes):
        return {"error": "path_not_in_allowlist", "path": path}
    ...
```

**越界记账（框架侧，不靠 Agent 自报）**：每次越权发生时，引擎在 `raw["scope_violations"]` 追加一条 `{step, tool, arg, reason}`。这是 PoC 方案 C9「越界率 = 0」指标的**唯一可信来源**——靠人工审阅 `trace` 抽样，量级不可信。

### 3.2 终止条件（原型已实现）

| 条件 | 判定 | 产出 | 位置 |
| --- | --- | --- | --- |
| **T1 显式裁决** | 模型不再请求工具（`tool_calls` 为空） | 正常 Verdict | `agent_judge.py:217-237` |
| **T2 步数耗尽** | `rounds` 达到 `max_steps` | **降级 Verdict**：`value=0.5, confidence=0.0` | `agent_judge.py:266-281` |
| **T3 端点异常** | `httpx` 异常 / HTTP != 200 | **降级 Verdict**：`value=0.5, confidence=0.0` | `agent_judge.py:282-296` |

**核心原则（原型已正确实现）：T2/T3 命中时绝不判 PASS。**
降级 Verdict 的 `confidence=0.0` → 经 `route()` 落入 `confidence < 0.5` 档 → `human_review`（`src/nlaut/judge/arbiter.py:32-33`）。

这与 `deterministic.py` 的既有语义一致：`_judge_response_ttft` 缺数据时返回 `value=0.5, confidence=0.5`，注释明写「**缺失数据 ≠ 性能不达标**」（`src/nlaut/judge/deterministic.py:367-372`）。nlaut 判定层的这个传统应保持。

### 3.3 预算参数

| 参数 | 原型值 | 位置 | 评估 |
| --- | --- | --- | --- |
| `max_steps` | 6 | `agent_judge.py:36` | **偏紧**——G-11 需「入 + 出 + 一致性」三段取证。建议 **10**（PoC 方案 §7.2） |
| `timeout_s` | 180.0 | `agent_judge.py:37` | 远大于 PoC 的 P95 ≤ 60s 目标；且是**单请求超时非整体墙钟**（缺口 G4） |
| `max_cost_usd` | 0.50 | `agent_judge.py:38` | ⚠️ **声明了但无拦截逻辑**（缺口 G3）——读 `agent_judge.py` 全文未见 `max_cost_usd` 被使用 |
| `temperature` | 0.0 | `agent_judge.py:130` | ✅ 硬编码 0，与 `vlm_mlx.py:6`「温度 0（框架硬规则：禁止随机判定）」一致 |
| 单工具结果上限 | 4000 字符 | `agent_judge.py:262` | 硬截断，未标 `truncated` |

**「任务完成误判为卡死」防护**（用户点名过的坑）：

| 防护点 | 状态 | 位置 |
| --- | --- | --- |
| 无输出 ≠ 卡死 | ✅ `parsed is None` → `value=0.5, confidence=0.0` → human_review | `agent_judge.py:226` |
| 步数耗尽 | ✅ `raw["degraded"]="max_steps_exhausted"` | `agent_judge.py:274` |
| 端点异常 | ✅ `raw["degraded"]="<Exc>: <msg>"` | `agent_judge.py:290` |
| 工具全失败 | ✅ 返回 `{"error": ...}` 而非抛异常 | `agent_judge.py:148-152` |
| **整体墙钟** | ❌ **待做**——`httpx.post(timeout=)` 只管单请求 | 缺口 G4 |

### 3.4 裁决输出格式（原型已实现 + 待改进）

原型格式（`agent_judge.py:194-197` 的 system prompt）：

```
judgement: true|false
理由: <基于你实际取证到的证据>
CONFIDENCE: High|Medium|Low
```

解析实现：`parse_judgement`（`agent_judge.py:55-87`）两段式——① 显式标记优先（`judgement:` / `判定：` / `结论：` / `结果：` 等）② 兜底「取前 300 字，找结论性关键词」启发式。`parse_confidence`（`agent_judge.py:90-106`）解析 High/Medium/Low（映射到 0.85/0.6/0.3）或 0.xx 数字，默认 0.6。

**缺口 G5：启发式兜底有真实误判风险。**
`_TRUE_PAT` 含 `"是"`、`_FALSE_PAT` 含 `"否"`（`agent_judge.py:51-52`）——模型写「表里没有看到数据，**否**定它已落库」这类推理中间句，前 300 字的关键词计数可能被误判。

**建议（待做）**：
1. **记账**：区分「显式标记命中」与「启发式命中」，写入 `raw["parse_mode"]`（`"explicit"` / `"heuristic"`）。
2. **门禁**：启发式命中占比 > 20% → 该轮结论标注「解析存在系统性脆弱性」。
3. **方向**：改用严格 JSON 输出 `{"verdict": "pass|fail|insufficient", "evidence_refs": [...], "reasoning": "..."}`，彻底消除启发式（PoC 方案 §5.2）。

> **注意与对照实验的耦合**：若改 JSON 输出格式，Agent 组与 LLM 组必须**都用同一格式**，否则测出的是「格式差异」而非「工具差异」（PoC 方案 §6.2 末段）。

### 3.5 循环骨架（原型已实现，此处为契约说明）

```python
# src/nlaut/judge/agent_judge.py:186-296（现状）
def judge(self, evidence: Evidence, question: Question) -> Verdict:
    system_prompt = question.context.get("system_prompt") or (默认取证约束 prompt)
    objective = question.text or question.context.get("objective", "")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": self._render_brief(evidence, objective)},
    ]
    trace, tool_calls_total, rounds = [], 0, 0
    try:
        for rounds in range(1, self.max_steps + 1):
            data = self._call(messages, with_tools=True)
            msg = (data.get("choices") or [{}])[0].get("message", {})
            tool_calls = msg.get("tool_calls") or []
            content = msg.get("content") or ""
            if not tool_calls:
                parsed = parse_judgement(content)
                conf = min(CONFIDENCE_CAP, parse_confidence(content))
                value = 1.0 if parsed is True else (0.0 if parsed is False else 0.5)
                return Verdict(engine=self.engine, kind="noul", value=value,
                               confidence=0.0 if parsed is None else conf, ...)
            messages.append(msg)
            for tc in tool_calls:
                tool_calls_total += 1
                fn = tc.get("function", {})
                fname = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {"_raw": fn.get("arguments", "")}
                result = self._run_tool(fname, args)
                trace.append({"step": rounds, "tool": fname, "args": args, "result": result})
                messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                 "content": json.dumps(result, ensure_ascii=False)[:4000]})
        return Verdict(... value=0.5, confidence=0.0, raw={"degraded": "max_steps_exhausted", ...})
    except Exception as e:  # noqa: BLE001 — 端点挂了等，降级不误判为通过
        return Verdict(... value=0.5, confidence=0.0, raw={"degraded": f"{type(e).__name__}: {e}", ...})
```

**契约要点**：
- 异常在 `judge()` 边界被完全兜住（`agent_judge.py:282`）——**判定层崩不得让用例记为 error**（`tests/judge_availability_guard.py` 的 2026-10-27 事故教训）。
- `_call` 用 `httpx.post(..., trust_env=False)`（`agent_judge.py:173-179`）——绕过系统代理，内网端点必需（`api.py:204` 的实测坑）。
- `_call` 对 HTTP != 200 抛 `RuntimeError`（`agent_judge.py:180-181`），被外层 except 捕获 → 降级。

**建议（待做）**：`_call` 应复用 `src/nlaut/executor/channels/api.py` 的调用模式，或把 `api.py` 的 `_call_api` 抽成公共 `call_chat()`。目前 `_call` 是**独立实现**，与 `api.py:82-208` 存在行为重复（未来 `disable_thinking` / `reasoning_effort` / 流式支持都要双份维护）。

---

## 4. (e) Verdict 产出与置信度

### 4.1 映射规则（原型已实现）

| 模型输出 | Verdict.value | Verdict.confidence | route() 结果 | 位置 |
| --- | --- | --- | --- | --- |
| `judgement: true`, conf=High | 1.0 | **0.85**（封顶） | `human_review` | `agent_judge.py:221-226` |
| `judgement: false`, conf=High | 0.0 | **0.85**（封顶） | `human_review` | 同上 |
| `judgement: true`, conf=Low(0.3) | 1.0 | 0.3 | `human_review`（<0.5 档） | 同上 |
| 解析失败（`parsed is None`） | 0.5 | **0.0** | `human_review` | `agent_judge.py:226` |
| 步数耗尽 | 0.5 | **0.0** | `human_review` | `agent_judge.py:267-280` |
| 端点异常 | 0.5 | **0.0** | `human_review` | `agent_judge.py:283-295` |

> **缺口 G6 已在原型中被部分解决**：解析失败 → `confidence=0.0`（`agent_judge.py:226`），与「证据不足」的 `confidence=0.5` 区分开。这是正确的设计，应保留并写进文档。

### 4.2 置信度封顶规则（核心决策，原型已正确实现）

**Agent judge 的 confidence 硬上限 = `CONFIDENCE_CAP = 0.85`**（`src/nlaut/judge/agent_judge.py:33`），低于 `route()` 的 0.9 自动判定阈值（`src/nlaut/judge/arbiter.py:34`）。

即：**Agent-as-Judge 在架构上永远不能自动裁决，永远进 `human_review` 队列。**

理由：
1. Agent 的 confidence 是**模型自报**的（`parse_confidence` 解析 High/Medium/Low），未经校准——连 VLM 的 logprob 代理客观量都没有（`vlm_mlx.py:37-56` 用 `exp(max(logprobs))`）。而 VLM 已经因为这个问题被限制「默认走 human_review 通道，除非 logprob 代理置信度 ≥0.9」（`vlm_mlx.py:8-11`）。Agent 自报**更不可信**。
2. Agent 判错代价更高：它有工具、能改自己的证据视图，容易被「查到了想要的东西」锚定。
3. 人机分工：Agent 的价值是**把人工仲裁的准备工作做完**（把散落的 API 响应/db 快照/trace 收敛成一份带引用的结论），**不是替代人裁决**。

> **必须在报告中诚实表述**：因 `CONFIDENCE_CAP=0.85`，agent verdict **100% 落人工仲裁**。因此「人工介入率」对当前实现必然是 100%——**Agent-as-Judge 的当前价值是「替人工做取证与初判」，不是「减少人工仲裁量」**。不得把「人工介入率」包装成收益（PoC 方案 §4.1-6 注、§7.5）。

**放开的前置条件**：Agent 引擎有金标准回归 + 校准曲线证明 `confidence≥0.9` 时准确率 ≥98%，届时把 `CONFIDENCE_CAP` 提到 0.95 并留 trace。

### 4.3 如何在现有 route() 里被正确路由到 human_review

**结论：`route()` 的判定逻辑不需要改**——agent 的 confidence 恒 < 0.9，天然落 `0.5 <= confidence < 0.9` 档 → `human_review`（`arbiter.py:34-37`）。

**但有两处必须做的加固**：

#### 加固 1：确定性引擎的直通分支加护栏（**待做**）

`route()` 第 27 行有一条特例：

```python
if verdict.engine == "deterministic":
    ok = float(verdict.value) >= 0.5
    return RouteDecision("auto_pass" if ok else "auto_fail", ...)
```

若未来有人把 agent 的 `engine` 误填成 `"deterministic"`（复制粘贴错误），就会走「零 AI 风险直接裁决」分支，**绕过置信度检查**。

```python
# src/nlaut/judge/arbiter.py —— route() 的修改稿（待做）

def route(verdict: Verdict, threshold: float = 0.9) -> RouteDecision:
    # 防御性护栏：只有确定性引擎能走零 AI 风险的直通分支。
    # 未来新增引擎若误填 engine="deterministic"，不会绕过置信度检查。
    if verdict.engine == "deterministic" and verdict.confidence >= 0.999:
        ok = float(verdict.value) >= 0.5
        return RouteDecision(
            "auto_pass" if ok else "auto_fail", "确定性判定，value>=0.5 即裁决"
        )
    if verdict.confidence < 0.5:
        return RouteDecision("human_review", f"引擎自报不确定 confidence={verdict.confidence:.2f}")
    if verdict.confidence < 0.9:
        return RouteDecision(
            "human_review", f"置信度中带 confidence={verdict.confidence:.2f}，转人工仲裁"
        )
    ok = float(verdict.value) >= threshold
    return RouteDecision(
        "auto_pass" if ok else "auto_fail",
        f"高置信 confidence={verdict.confidence:.2f}，value={verdict.value} 对 threshold={threshold}",
    )
```

> **兼容性**：加 `confidence >= 0.999` 条件**不改变现有行为**——`DeterministicJudge` 的所有分支都返回 `confidence=1.0`（`deterministic.py:74,108,131,158,207,326,347,375,419,442`），无一例外。既有 `tests/judge_test.py:74-76` 依然通过。
>
> **但这会让「engine=deterministic 且 confidence<1.0」的 Verdict 改道 human_review**——这类 Verdict 当前**不存在**，是纯粹的防御性收紧。

#### 加固 2：agent 专属说明分支（**待做**，纯可读性）

```python
    if verdict.engine == "agent":
        return RouteDecision(
            "human_review",
            f"Agent-as-Judge 结论（自报置信度封顶 0.85 < 0.9 阈值），"
            f"value={verdict.value}，转人工仲裁",
        )
```

放在 `confidence < 0.9` 分支**之后**（当前 agent 的 confidence 恒 < 0.9，实际不会命中；保留是为了将来放开封顶时行为明确）。**注意**：这会让 `arbiter.py` 依赖 `agent_judge.py` 的常量——**应避免循环依赖**，改为在 `arbiter.py` 内定义字面量或从 `protocol.py` 共享常量。

### 4.4 Verdict.raw 的内容规格

原型已记录（`agent_judge.py:228-236`）：`judgement` / `confidence_raw` / `reason` / `rounds` / `tool_calls_total` / `trace` / `model`；降级时记录 `degraded` / `max_steps`（`agent_judge.py:268-280`）。

**待补字段**（对应缺口 G8、PoC 方案 §7.6）：

| key | 用途 | 状态 |
| --- | --- | --- |
| `trace` | 完整工具调用序列（step/tool/args/result） | ✅ 已有 |
| `parse_mode` | `"explicit"` / `"heuristic"` —— 支撑启发式占比统计 | ❌ 待做 |
| `parse_error` | 区分「判错」与「输出格式错」 | ❌ 待做 |
| `scope_violations` | **越界记账**（框架侧）—— C9 越界率的唯一可信来源 | ❌ 待做 |
| `truth_source_queried` | 取证充分性（判「通」却没查真值源 = 猜测） | ❌ 待做（可从 `trace` 推导，显式更可靠） |
| `elapsed_ms` | 成本统计 | ❌ 待做 |
| `temperature` / `max_steps` | 实验可复现 | ⚠️ 仅 `model` 已有 |
| `step_limit_hit` / `timeout_hit` | 显式化终止原因 | ⚠️ 仅有 `rounds`/`degraded` |
| `evidence_enriched` | 回填后的完整 Evidence（见 §5.3） | ❌ 待做 |

---

## 5. (d) Evidence 协议扩展

### 5.1 现状

`Evidence` 当前 12 个字段（`src/nlaut/judge/protocol.py:20-44`）全是「执行通道产出的快照」。`db_state` 虽已定义（`protocol.py:32`），但**执行器无写入方**（`grep db_state src/nlaut/executor/` 无命中）——judge 层有消费方（`agent_tools.py:145-158`、`agent_judge.py:314-315`），但通道层从不填。

**这正是 G-11 类场景的根因**：跨系统一致性判定需要真值源，而真值源从未进入 Evidence。

### 5.2 新增字段规格（完整 pydantic 代码块，实现者直接采用）

**追加到 `src/nlaut/judge/protocol.py` 的 `Evidence` 类中**（全部可选 + 默认值，不影响既有 `Evidence(case_id=ir.id, **ev_kw)` 构造，`src/nlaut/executor/runner.py:212`）。

> ⚠️ **落地注意（本次实测发现）**：`EvidenceFinding` 必须定义在 **`Evidence` 类之前**（模块级）。若只把 `Evidence` 类体贴进 `protocol.py` 而漏掉模块级的 `EvidenceFinding` 别名，pydantic v2 会在导入时抛
> `PydanticUserError: 'Evidence' is not fully defined; you should define 'EvidenceFinding', then call 'Evidence.model_rebuild()'`。
> 因此下面代码块的**前 12 行（imports + `EvidenceFinding` 别名）与 `Evidence` 类体必须一起贴**。

```python
# ---------- protocol.py 顶部（import 区）----------
from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

# Agent-as-Judge 取证记录：每次工具调用一条（审计与报告展示用）
# 结构：{"tool": str, "args": dict, "result": dict}
EvidenceFinding = dict[str, Any]


class Evidence(BaseModel):
    """判定输入的证据包：执行器产出，判定引擎只读。

    通道与字段对应：
    - web 通道：screenshot + dom_state
    - api 通道：llm_response + response + tool_calls + latency_ms + conversation

    v0.4.0 扩展：Agent-as-Judge 取证回填（agent_findings）与真值源契约。
    """

    case_id: str
    screenshot: str | None = None
    dom_state: dict[str, str] | None = None
    response: dict | None = None
    db_state: dict | None = None
    llm_response: str | None = None
    llm_response_json: dict | None = None
    tool_calls: list[dict] | None = None
    latency_ms: float | None = None
    conversation: list[dict] | None = None
    ttft_ms: float | None = None
    stream_chunks: int | None = None
    ttft_samples: list[float] = Field(default_factory=list)
    ttft_p95_ms: float | None = None

    # --- v0.4.0 Agent-as-Judge 新增 ---

    # 执行**前**的存储快照（支撑「本次操作的增量」判定）
    db_snapshot_before: dict | None = None
    # 执行**后**的存储快照
    db_snapshot_after: dict | None = None
    # 前后差分结果（新增行 / 变更行 / 计数变化）——Agent 直接拿增量证据，
    # 避免自行推算 diff 产生歧义（纯快照语义无法表达增量，见 §7 说明）
    store_diff: dict | None = None

    # 真值源显式契约：把「去哪查」从隐含约定变成显式声明
    # 结构示例：
    # {
    #   "db_tables": ["task"],
    #   "endpoints": {"task_list": "http://10.0.0.1:8080/api/task"},
    #   "server_log": "artifacts/session/server.log",
    #   "compare_keys": ["module", "task_id"],
    # }
    truth_source_spec: dict | None = None

    # 本次执行的请求/响应摘要（method/url/status/关键字段）
    # 用于区分「请求没发出去」与「发了但没落库」
    http_trace: list[dict] | None = None
    # 服务端访问日志路径（artifacts 相对路径）
    server_log: str | None = None

    # 证据采集时刻（epoch 秒）——区分「证据过期」与「证据为真」
    probe_timestamp: float | None = None

    # Agent judge 的取证记录（每次工具调用一条）。空列表 = 未启用 agent judge。
    # 由 Judge 执行后回填；通道层不填（见 §5.3）。
    agent_findings: list[EvidenceFinding] = Field(default_factory=list)

    # Agent judge 的判定元数据（供报告/审计定位）。未启用时为 None。
    agent_meta: dict[str, Any] | None = None
    # 结构：
    # {
    #   "engine": "agent",
    #   "model": "deepseek-v4.1-flash",
    #   "rounds": 4,                    # 实际轮次
    #   "max_steps": 10,
    #   "tool_calls": 3,
    #   "total_tokens": 4210,
    #   "latency_ms": 18700.0,
    #   "degraded": False,
    #   "degraded_reason": None,       # "max_steps_exhausted" | "timeout" |
    #                                 # "token_budget_exhausted" | "model_error" |
    #                                 # "inconclusive" | "parse_error"
    #   "parse_mode": "explicit",      # "explicit" | "heuristic"
    #   "scope_violations": [],        # 越界记账（框架侧）
    #   "tool_availability": {         # 工具可用性探测
    #       "query_db": {"available": True, "reason": None},
    #       "http_probe": {"available": False, "reason": "未授权 URL 探针"},
    #   },
    #   "confidence_cap": 0.85,        # 应用的置信度封顶
    #   "raw_confidence": 0.85,        # 模型自报的原始置信度（封顶前）
    # }
```

**`EngineName` 字面量扩展**（`src/nlaut/judge/protocol.py:17`）：

```python
EngineName = Literal["deterministic", "mlx-vlm", "laya", "jev", "agent"]
```

> `Verdict.engine` 的类型本就是 `EngineName | str`（`protocol.py:59`），运行时**已经**接受任意字符串。改 `EngineName` 是让类型标注与运行时一致（消除 IDE/类型检查误报），**不是功能必需项**——但应改。

**`protocol.py` 顶部 import 需补 `Any`**：当前是 `from typing import Literal, Protocol, runtime_checkable`（`protocol.py:12`）。

### 5.3 回填机制（缺口 G8：让 L5 宪法在 agent 引擎上闭合）

**问题**：`judge(evidence, question)` 的签名不返回 Evidence（`protocol.py:69-70`），Agent 的取证结果只留在 `Verdict.raw["trace"]` 里——这意味着「判定基于 Evidence 作答」在 agent 引擎上**不闭合**：结论依据（取证结果）不在 Evidence 里。

**方案：`AgentJudge` 内部持有派生 Evidence，通过 `raw["evidence_enriched"]` 携带，不改协议签名。**

```python
# src/nlaut/judge/agent_judge.py（待做）
def judge(self, evidence: Evidence, question: Question) -> Verdict:
    ...
    # 在返回 Verdict 前构造增强版 Evidence
    enriched = evidence.model_copy(update={
        "agent_findings": trace,          # trace 的每项已是 {"step","tool","args","result"}
        "agent_meta": meta,               # 见 §5.2 的结构
    })
    verdict.raw["evidence_enriched"] = enriched.model_dump(mode="json")
    return verdict
```

**为什么不改 `judge()` 签名**：改返回值为 `(Verdict, Evidence)` 会破坏全部 4 个既有引擎 + `tests/judge_test.py` + `tests/llm_ir_test.py` 的调用契约。**协议不改是硬约束**（`docs/judge-protocol.md:48-52` 的「报告层零改动」原则）。

**落盘路径**：`Verdict.raw` 本来就进报告明细；`EvidenceStore.save`（`src/nlaut/report/evidence.py:19-26`）落的是通道层的 evidence.json。若要落增强版，取 `raw["evidence_enriched"]` 重新 `Evidence.model_validate` 后再存。

### 5.4 「渲染层影响」：Laya 不得受污染

**`src/nlaut/judge/laya.py` 的 `render_state`（`laya.py:22-63`）不改动**——新字段不进入 Laya 输入，保持 Laya 行为零变化，避免污染 `tests/judge_golden/` 既有金标准回归。

Agent 引擎有独立的 `_render_brief`（`agent_judge.py:298-322`），可自行扩展新字段。

### 5.5 通道层写入（**待做**）

新增的 `db_snapshot_before` / `db_snapshot_after` / `store_diff` / `http_trace` / `server_log` / `truth_source_spec` / `probe_timestamp` **需要通道层写入**才有意义。

**注意**：`runner.py:212` 是 `Evidence(case_id=ir.id, **ev_kw)`——`ev_kw` 直接展开成 Evidence 字段。所以通道 `execute()` 的返回 dict 加同名键即可透传（**这是既有约定**：skill 记「Evidence 新字段：judge/protocol.py 加字段 → 通道 execute() 返回 dict 同名透传（api.py 曾漏透传致报告无数据）」）。

> **范围提示**：通道层写入是独立任务（涉及 web/api/electron 三个通道）。本设计的核心（Agent 引擎接口）不依赖它——Agent 可以先只在 `db_state` 快照 + 注入式工具上工作。

---

## 6. 降级路径

### 6.1 原型已实现的降级（**基本正确，但缺一级**）

原型在 `judge()` 内做**同引擎降级**（`agent_judge.py:266-296`）：
- 步数耗尽 → `value=0.5, confidence=0.0`
- 端点异常 → `value=0.5, confidence=0.0`
- 解析失败 → `value=0.5, confidence=0.0`

**这三条都正确地导向 `human_review`，绝不误判为通过。** 但**没有跨引擎降级**——引擎挂了不会退到 Laya。

### 6.2 完整降级链设计（**待做**）

```
AgentJudge.judge()
  │
  ├─ 探测失败 / 端点不可达 / 连接超时 / HTTP 4xx-5xx
  │    └─▶ 降级 1：LayaJudge（本地模型，noul 语义）
  │          └─ 本地权重缺失 / laya_mlx 未安装 / 同样异常
  │                └─▶ 降级 2：确定性判定（若 question.context 有可确定性化的 mode）
  │                      └─ 无确定性路径
  │                            └─▶ 降级 3：人工（value=0.5, confidence=0.5 → human_review）
  │
  ├─ 预算耗尽（步数/超时/token） / 输出解析失败超限
  │    └─▶ 降级 1：LayaJudge（同上）
  │
  └─ 模型明确给出 inconclusive
       └─▶ 直接人工（value=0.5, confidence=0.5）—— 不再降级 Laya
          （模型说「查不清」本身就是有效的人工触发信号，Laya 也查不清）
```

### 6.3 三条硬规则

1. **降级目标必须能处理同一个 Question。** LayaJudge 支持 `noul/choice/score` 三种 kind（`laya.py:84-101`），agent 断言的 Question 就是 `kind="noul"`（`runner.py` 的 `agent_verify` 分支）→ **天然可降级**。这是把 agent 断言的 Question.kind 定为 `"noul"` 的关键收益。
2. **降级后的 Verdict 必须写真实的 `engine` 名**（`"laya"` / `"deterministic"`），**不能写 `"agent"`**——否则 `route()` 会按 agent 的封顶逻辑处理一个根本没跑 agent 的结论。降级信息放 `raw["degraded_from"]` / `raw["degraded_to"]` / `raw["degraded_reason"]`。
3. **降级必须留痕**——报告层看到 `engine=laya` 但 `raw.degraded_from=agent` 才知道发生了什么。

### 6.4 「不产生误判为通过」的保证矩阵

| 失败模式 | 保护机制 | 状态 |
| --- | --- | --- |
| Agent 端点挂了 | 降级 Laya → 人工（**跨引擎降级待做**） | ⚠️ 目前只降级到 agent 自身 |
| Agent 步数耗尽 | `value=0.5/conf=0.0` → human_review | ✅ `agent_judge.py:266-281` |
| 工具全报错 | 模型给不出可信结论 → 必然 inconclusive 或解析失败 → 降级 | ✅ `agent_judge.py:144-155` |
| Agent 输出解析失败 | `parsed is None` → `value=0.5/conf=0.0` → human_review | ✅ `agent_judge.py:226` |
| 判定层整体不可用（httpx/socksio） | `judge()` 边界 try/except 全兜住 → 降级 Verdict，不冒泡 | ✅ `agent_judge.py:282` |
| Laya 也不可用 | `value=0.5/conf=0.5` → human_review | ⚠️ 跨引擎降级待做 |

**最后一行是当前最大的缺口**：agent 端点挂了且 Laya 也不可用时，应该由 **runner 层**兜底，而不是让 agent 引擎自己硬扛。

### 6.5 建议的实现位置

跨引擎降级**不应**放在 `AgentJudge` 内部（会造成 judge 层依赖 laya 层 + 循环依赖风险）。建议放在 **runner 层**：

```python
# src/nlaut/executor/runner.py（待做）
judge = get_judge(engine_name)
try:
    verdict = judge.judge(evidence, _question_for(a, ir))
except Exception as e:  # noqa: BLE001 — 判定层崩不得让用例记为 error
    verdict = _fallback_verdict(engine_name, evidence, _question_for(a, ir), e)
```

`_fallback_verdict` 的降级顺序：`agent → laya → deterministic → 人工（value=0.5/conf=0.5）`。

> **注**：`runner.py:331` 已有 `except Exception` 兜住单用例崩溃（`run_store` 层），但那是把用例记为 `status: "error"`——**不是** `human_review`。两者的区别重要：`error` 会被报告层算作 failed（`render.py:59`：`failed = sum(r["status"] in ("failed", "error"))`），而 `human_review` 是 `need_review`。**判定层不可用不应让用例看起来「失败」**——这正是 2026-10-27 事故被误读为「模型能力退化」的机制。

---

## 7. (f) IR 断言类型

### 7.1 现状：已实现 `agent_verify`

原型已在 `src/nlaut/ir/model.py:254-278` 实现 `AssertAgentVerify` 并加入 `Assertion` union：

```python
class AssertAgentVerify(BaseModel):
    """Agent-as-Judge 断言：judge 自主取证后判定目标是否成立。"""
    kind: Literal["agent_verify"]
    objective: str = Field(min_length=1)  # 判定目标（judge 的取证任务描述）
    threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    max_steps: int = Field(default=6, ge=1, le=20)
    system_prompt: str | None = None  # 可选：覆盖默认取证约束 prompt
    judge: Literal["agent"] = "agent"
```

并已接入 `runner.py` 的 `_engine_name`（`runner.py:155-157`）与 `_question_for`（`runner.py:127-137`）。

### 7.2 是否需要第二种 kind？—— **建议暂不新增 `agent_trace`**

PoC 方案 §7.5「路径 B」讨论过 `integration_link` 这类断言，但最终原型选了通用的 `agent_verify`。本文**认同这个选择**，理由：

1. **`agent_verify` 的 `objective` 已经是自由文本**——过程验证完全可以写成 objective（「模型是否按序调用了 get_weather 然后 get_forecast？」），不需要第二个 kind。
2. **kind 越少，漏注册风险越低**。skill 明确记录：漏 runner 注册会**静默路由到 laya 引擎产出误导性结果**。少一个 kind 就少一个漏注册点。
3. **判别联合是拒绝面**——新增 kind 会扩大 IR 校验面，需同步 `docs/ir-schema.md`、`gen/` 派生代码校验器、`tests/ir_model_test.py`。

**但若未来确需区分**，推荐的做法是**在 `agent_verify` 上加可选字段**而非新增 kind：

```python
class AssertAgentVerify(BaseModel):
    """Agent-as-Judge 断言：judge 自主取证后判定目标是否成立。"""
    kind: Literal["agent_verify"]
    objective: str = Field(min_length=1)
    threshold: float = Field(default=0.9, ge=0.0, le=1.0)
    max_steps: int = Field(default=6, ge=1, le=20)
    system_prompt: str | None = None
    # --- v0.4.0 补强（待做）---
    allowed_tools: list[str] = Field(default_factory=list)
    """限定 judge 可用的工具子集（缺省 = 全部可用工具）。
    对应 AJ-Bench 的信息获取能力可控性；也是 scope 白名单的一层。"""
    fallback_mode: Literal[
        "response_exact", "response_contains", "response_not_contains",
        "response_json_schema", "tool_call_params", "response_time",
        "response_length",
    ] | None = None
    """Agent 不可用时的确定性降级路径（声明式，保证有 fallback）。"""
    fallback_params: dict[str, str] = Field(default_factory=dict)
```

`allowed_tools` 的翻译（`runner.py` 的 `_question_for` 分支，**待做**）：

```python
    if a.kind == "agent_verify":
        return Question(
            kind="noul",
            text=a.objective,
            context={
                "system_prompt": a.system_prompt,
                "max_steps": a.max_steps,
                "allowed_tools": a.allowed_tools,     # 待做
                "fallback_mode": a.fallback_mode,     # 待做
                "fallback_params": a.fallback_params, # 待做
            },
        )
```

### 7.3 新增断言类型的完整注册链（skill 记录，缺一即隐性 bug）

| # | 位置 | 原型状态 |
| --- | --- | --- |
| 1 | `src/nlaut/ir/model.py`：新 `Assert*` 模型类 + 加入 `Assertion` 判别 union | ✅ 已做 |
| 2 | `src/nlaut/judge/deterministic.py`：`judge()` 加 mode 分支 | ⛔ **不适用**——agent 断言不走确定性引擎。新引擎在 `judge/agent_judge.py` |
| 3 | `src/nlaut/executor/runner.py`：`_engine_name` 的 kind 分支 + `_question_for` 的 context 构造分支 | ✅ 已做 |
| 4 | `docs/ir-schema.md` 断言表加行 + 说明 | ✅ 已做 |
| 5 | 元测试放 `tests/llm_ir_test.py` 对应类内 | ⚠️ **待做**——当前元测试在 `tests/agent_judge_test.py`，但 `ir_model_test.py` 未覆盖 `agent_verify` |

> **必须补的元测试（对应 skill 链第 5 步）**：
> ```python
> class TestAgentAssertionIR:
>     def test_agent_verify_assertion_ok(self):
>         ir = TestCaseIR.model_validate(_base_api_ir() | {
>             "assertions": [{"kind": "agent_verify", "objective": "订单是否真的落库？"}]
>         })
>         assert isinstance(ir.assertions[0], AssertAgentVerify)
>
>     def test_unknown_assertion_kind_still_rejected(self):
>         """判别联合拒绝面不因新增 kind 而松动。"""
> ```
>
> 以及**漏注册防护测试**（直测 `_engine_name`，不靠间接覆盖）：
> ```python
> def test_agent_verify_routes_to_agent_engine():
>     from nlaut.executor.runner import _engine_name
>     a = AssertAgentVerify(kind="agent_verify", objective="x")
>     assert _engine_name(a) == "agent"
> ```

### 7.4 `docs/ir-schema.md` 已补的内容（核实）

原型已加断言表行 + 一个专章（`## Agent-as-Judge 判定（agent_verify，v0.4.0）`），包含判定目标 / 可用工具 / 终止条件 / 置信度 / 路由结果 / 降级 + 环境变量（`NLaut_AGENT_JUDGE_MODEL` / `NLaut_AGENT_JUDGE_TOOLS`）。**符合「文档同步」约定。**

---

## 8. (h) 元测试设计（参照 AJ-Bench 三维度）

### 8.1 参照 AJ-Bench 的维度映射

AJ-Bench（ACL 2026 Findings，aj-bench.github.io）用 155 任务 / 516 条标注轨迹，用 **F1** 评估 judge agent，覆盖三能力：

| AJ-Bench 维度 | nlaut 内的对应 | 测什么 | 原型状态 |
| --- | --- | --- | --- |
| **information acquisition**（外部搜索/工具获取信息） | Agent 是否主动调工具、调的对不对 | 工具调用轨迹的正确性 | ⚠️ 部分（工具单测有，循环级缺） |
| **state verification**（工具辅助检查环境状态） | `agent_verify`：查 db/快照后判状态对不对 | 状态判定准确率 | ✅ 有金标准样本 |
| **process verification**（检查关键动作与执行步骤） | `inspect_trace` 工具 + 过程类 objective | 过程判定准确率 | ❌ 缺样本 |

**AJ-Bench 的两条实证结论直接决定本设计**：
1. **扩大交互预算稳定提升 F1**（尤其 GUI 域敏感）→ 故 `max_steps` 建议从 6 提到 10（§3.3）。
2. **更强的推理能力 ≠ 更强的工具调用与决策能力**；思考 effort 提高不一定更好 → 故 confidence **不因推理深度提升**，只认模型自报值且封顶（§4.2）。

### 8.2 原型已有的元测试（`tests/agent_judge_test.py`，实测 30 passed）

| 测试类 | 覆盖 | 行数 |
| --- | --- | --- |
| `TestParseJudgement` | 显式标记 / 中文标记 / 不可解析 / 显式优先于启发式 | `39-61` |
| `TestParseConfidence` | High/Medium/Low / 中文 / 数字 / 缺省 | `64-77` |
| `TestConfidenceCap` | **封顶 < 0.9** + **agent 永不 auto_pass** | `80-96` |
| `TestToolImpls` | query_db 命中/未命中/无快照、trace 分页、文件缺失不抛异常、schema 形状 | `99-136` |
| `TestDegradation` | 端点不可达 → confidence=0 + degraded + human_review；未知工具不崩；工具异常被捕获 | `139-181` |
| `TestGoldenSampleIntegrity` | **样本数 / 四类配额 / expected 是 bool / 陷阱真的与自述矛盾** | `286-327` |
| `TestGoldenSampleEvidence` | evidence 构造 / 陷阱带误导性自述 | `330-343` |
| `TestLiveAgentJudge`（`-m agent`） | 真实端点跑金标准 + 陷阱识破 | `351-411` |

**特别值得肯定的一点**（`tests/agent_judge_test.py:298-314`）：`test_trap_samples_actually_differ_from_claimed` 断言「自述成功 ⇒ 实际未落库」必须成立，否则陷阱是假的——直接引用了 nlaut 历史踩过的 CSS override 假阳性坑。这是**金标准真伪自检**，应保持并扩展到新增样本。

### 8.3 缺失的元测试类（**待做**）

#### 类 1：`TestAgentJudgeInformationAcquisition` —— 信息获取（循环级）

原型只测了工具**函数**（`TestToolImpls`），**没测循环**——「Agent 会不会主动调工具、调的对不对」完全没覆盖。

```python
class TestAgentJudgeInformationAcquisition:
    """维度 1：Agent 是否主动、正确地取证（AJ-Bench: information acquisition）。

    用假模型端点（monkeypatch AgentJudge._call）返回预设 tool_calls 序列，
    验证引擎的调度逻辑——不触网。
    """

    def test_agent_calls_tool_and_uses_result(self):
        """Agent 应当调 query_db 并把结果纳入结论。"""
        # 假模型第 1 轮返回 tool_call(query_db)，第 2 轮返回 "judgement: true"

    def test_agent_answers_without_any_tool_call(self):
        """模型第一轮就给结论、不调工具 → 允许但必须在 raw 留痕
        （取证充分性指标的数据来源）。"""

    def test_unknown_tool_name_returns_error_envelope_not_crash(self):
        """模型幻觉出不存在的工具名 → 工具返回 ok:false，循环继续，不崩。"""

    def test_tool_result_truncation_is_marked(self):
        """工具结果超长 → 截断且有 truncated 标记（当前实现是硬截断，无标记——待做）。"""

    def test_trace_records_every_tool_call_in_order(self):
        """raw["trace"] 必须完整、按序、可回放（审计硬要求）。"""
```

#### 类 2：`TestAgentJudgeStateVerification` —— 状态验证

```python
class TestAgentJudgeStateVerification:
    """维度 2：状态判定准确性（AJ-Bench: state verification）。"""

    def test_verdict_pass_when_db_state_matches(self):
        """db_state 命中 → value=1.0"""

    def test_verdict_fail_when_db_state_contradicts(self):
        """db_state 不符 → value=0.0"""

    def test_missing_evidence_leads_to_inconclusive_not_pass(self):
        """证据不足 → 绝不判 PASS —— 误判为通过是最高优先级防线。"""

    def test_contradictory_evidence_leads_to_inconclusive(self):
        """db 说 paid 但响应说失败 → 矛盾证据 → 转人工，不许二选一硬判。"""
```

#### 类 3：`TestAgentJudgeProcessVerification` —— 过程验证

```python
class TestAgentJudgeProcessVerification:
    """维度 3：过程判定（AJ-Bench: process verification）。"""

    def test_trace_tool_call_order_verified(self):
        """Agent 能从 inspect_trace 里读出调用顺序并据此判定。"""

    def test_missing_trace_step_leads_to_inconclusive(self):
        """trace 缺关键步骤 → 转人工，不许假设它执行了。"""
```

#### 类 4：`TestAgentJudgeScopePolicy` —— 作用域白名单（**待做，对应 §3.1**）

```python
class TestAgentJudgeScopePolicy:
    def test_http_probe_rejects_url_outside_allowlist(self):
        """越权 URL → 返回结构化 error，不发请求。"""

    def test_read_file_rejects_path_outside_allowlist(self):
        """越权路径 → 返回结构化 error，不读文件。"""

    def test_violations_are_recorded_in_raw(self):
        """越界必须由框架侧记账（raw["scope_violations"]），不靠 Agent 自报。"""
```

#### 类 5：`TestAgentJudgeDegradationChain` —— 跨引擎降级（**待做，对应 §6**）

```python
class TestAgentJudgeDegradationChain:
    def test_endpoint_down_falls_back_to_laya(self):
        """Agent 端点 500 → engine == 'laya'，raw.degraded_from == 'agent'。"""

    def test_laya_also_down_falls_back_to_human(self):
        """Agent + Laya 都不可用 → value=0.5/conf=0.5 → human_review。"""

    def test_degraded_engine_name_is_real_engine(self):
        """降级后 engine 字段是真实引擎名，不是 'agent'。"""

    def test_judge_layer_exception_does_not_become_case_error(self):
        """判定层崩不得让用例记为 error（2026-10-27 事故教训）。"""
```

#### 类 6：`TestArbiterGuard` —— 路由护栏（**待做，对应 §4.3**）

```python
class TestArbiterGuard:
    def test_deterministic_route_unchanged_by_guard(self):
        """护栏不破坏 deterministic 既有路由（tests/judge_test.py 行为不变）。"""

    def test_mislabeled_engine_does_not_bypass_confidence_check(self):
        """engine 误填 'deterministic' 的 agent verdict 不能被直通。"""
```

### 8.4 元测试的 mock 边界（严格遵守）

| 允许 mock | 禁止 mock |
| --- | --- |
| `AgentJudge._call`（假模型端点） | `route()`（要真跑，验证路由行为） |
| `JudgeToolkit` 的外部 IO（db 快照、文件读、HTTP） | `DeterministicJudge.judge()`（要真跑） |
| `time.monotonic`（控制超时） | `Evidence` 构造（用真实 pydantic 模型） |

> **skill 铁律**：「禁止在 Layer A/B 测试中访问任何非本地环境数据」——`-m agent` 的真实端点测试必须默认跳过（`pyproject.toml` 的 `addopts = "-q -m 'not mlx and not agent'"` 已配）。

### 8.5 Layer B：金标准回归

按 `docs/judge-protocol.md:48-52` 的门槛「通过 `tests/judge_golden/` 金标准回归（≥30 张标注截图，准确率 + 校准曲线）」。

**现状**：Agent 侧有 8 样本金标准（`tests/agent_judge_test.py:205-283`），四类配额齐备（real_pass / real_fail / trap_surface_pass / trap_surface_fail），**但只有 8 个**——不足以作为上线门槛（PoC 方案 §3.6 明确指出）。

**门禁设计**：

| 门禁 | 阈值 | 理由 |
| --- | --- | --- |
| **陷阱假阳性数（FP）** | **= 0** | 放行坏模块 = 门禁失效。**这是与 AJ-Bench 不同处**——nlaut 的 agent 永远不自动裁决，FP 的代价是「给出错误结论误导人工」，但仍必须为 0 |
| 陷阱假阴性数（FN） | ≤ 1 | 误挡合格模块，代价较低但也要压 |
| 样本级一致率（SCA） | ≥ 0.90 | 对齐 PoC 方案 §4.4 |
| J1 翻转率（同 Evidence 重复 5 轮） | ≤ 5% | 抖动率过高则置信度不可信 |
| 问法一致性（正反问法配对） | 100% | 同一场景反问得出不同答案 = 缺陷，不受其他指标补偿 |
| 校准（conf=0.85 桶准确率） | ≥ 0.95 | 置信度失真则人工仲裁队列质量不可信 |
| **取证充分性** | **100%** | 判「通」却没查真值源 = 猜测 |
| 越界率 | **= 0** | 违反责任边界红线 |
| 样本量 | ≥ 30 | `docs/judge-protocol.md` 的接入门槛 |

**「金标准真伪自检」必须保留并扩展**——`test_trap_samples_actually_differ_from_claimed`（`tests/agent_judge_test.py:298-314`）的做法：陷阱样本的真值由测试**直接构造**（非模型生成），并断言陷阱**真的**与自述矛盾。

---

## 9. (i) 与现有三级漏斗的层级关系

### 9.1 现状漏斗（`docs/architecture.md:43-53`）

```
断言（IR）
   │
   ├─ 1° 确定性 (deterministic) ─── 零 AI 成本，微秒级，confidence=1.0
   │     selector 文案 / JSON schema / tool_call 参数 / 延迟 / 长度
   │     ↓ 未覆盖
   ├─ 2° 视觉 (mlx-vlm) ────────── 本地推理，单图秒级，yes/no
   │     页面状态 / 渲染 / 语义文案（需看图）
   │     ↓ 未覆盖
   ├─ 3° System One (laya) ─────── 本地 13.4ms，noul/choice/score
   │     语义判定 / 失败归因 / 量表评分
   │     ↓
   └─ route() → auto_pass / auto_fail / human_review
        conf≥0.9 自动 | 0.5–0.9 人工 | <0.5 不确定
```

### 9.2 加入 Agent-as-Judge 后的漏斗（文字版）

```
断言（IR）
   │
   ├─ 1° 确定性 (deterministic)  ── 零 AI 成本，confidence=1.0，value≥0.5 直裁
   │     ├ response_exact / response_contains / response_not_contains
   │     ├ response_json_schema / tool_call_params
   │     └ response_time / response_ttft / response_ttft_p95 / response_length
   │           ↓ 仍覆盖不到（快照不足以判定）
   ├─ 2° 视觉 (mlx-vlm)  ───────── 本地 Qwen2.5-VL，只看图，yes/no，max_tokens=8
   │     └ visual_state
   │           ↓ 仍覆盖不到
   ├─ 3° System One (laya)  ───── 本地 laya-mlx，0 输出 token，noul/choice/score
   │     └ noul / choice / score
   │           ↓ 仍覆盖不到（快照里没有真值源）
   └─ 4° Agent-as-Judge (agent)  ── 云端/内网 LLM + 只读取证工具（多轮）
         └ agent_verify（自主取证后判定）
           │
           ├─ 内部复用 1° 确定性：把取到的证据交给 DeterministicJudge 复核
           │  （Agent 只决定「查什么」，「怎么算」仍由确定性代码说了算）
           ├─ 裁决 confidence 封顶 0.85  ⟶  route() ⟶ human_review（永不自动）
           └─ 降级链：agent 挂了 → laya → deterministic → 人工
```

### 9.3 四条协同原则

**原则 1：Agent-as-Judge 是「4° 补漏层」，不是「3° 替代层」。**
现有三级漏斗**优先**。能用 `response_contains` 表达的，绝不写 `agent_verify`。理由：确定性判定零成本、零幻觉、置信度 1.0；Agent 判定要花钱、要联网、会自报不确定。
→ 落地约束：`docs/ir-schema.md` 规则 6「确定性断言优先，AI 判定只兜底断言不可表达的场景」对 agent 同样适用。

**原则 2：Agent 与 Laya 不是竞争关系，是「取证深度 vs 判定速度」的分工。**
- Laya：只看快照（Evidence 已有字段），毫秒级，本地，0 输出 token。
- Agent：主动去取快照里没有的事实（db、trace、文件），秒级~十秒级，云端。
→ **Agent 的独特价值 = information acquisition**（AJ-Bench 明确指出这是 Agent-as-Judge 相对 LLM-as-Judge 的核心增益）。

**原则 3：Agent 内部复用 1° 确定性做复核。**
让 Agent 把取到的证据交给 `DeterministicJudge` 复核——这样「Agent 的自由发挥」被约束在「选择查什么」，而「怎么算」仍由确定性代码说了算。**这是把幻觉面从「判定逻辑」压缩到「取证选择」的架构手段。**
（原型尚未实现此工具，是 §2.1 的建议补项。）

**原则 4（成本闸门）：Agent 只在「前三级都判不了」时被唤醒。**
`runner.py:215-225` 是「一个断言 → 一个引擎」的 1:1 映射，**没有级联**。本设计**明确不做级联**——级联会让「哪一级判的」在报告里失去意义，且与「每条断言声明 judge 级别」（`docs/ir-schema.md:76`）的现有约定冲突。是否用 Agent 由 IR 作者显式声明（`agent_verify`）。

---

## 10. 实施路线图

> 状态标注：**✅ 已完成**（原型已实现并通过测试）/ **⚠️ 待做**（设计已定，未实现）。

### Phase 1 — 核心引擎与注册链（**✅ 大部分已完成**）

**产出物**
1. ✅ `src/nlaut/judge/agent_judge.py`：`AgentJudge`（循环、封顶、降级、trace）。
2. ✅ `src/nlaut/judge/agent_tools.py`：6 个只读工具 schema + 注入式实现。
3. ✅ `src/nlaut/ir/model.py`：`AssertAgentVerify` + `Assertion` union。
4. ✅ `src/nlaut/executor/runner.py`：`_engine_name` + `_question_for` 分支。
5. ✅ `src/nlaut/judge/protocol.py`：`register_judge_factory` 工厂注册机制。
6. ✅ `src/nlaut/judge/__init__.py`：`register_judge_factory("agent", _build_agent_judge)`（**懒加载**，保持 import 零开销）。
7. ✅ `docs/ir-schema.md`：断言表行 + 专章。
8. ✅ `pyproject.toml`：`agent` marker。
9. ✅ `cases/demo/tc_agent_verify_001.yaml`：G-11 示例用例。
10. ✅ `tests/agent_judge_test.py`：30 个元测试 + 8 样本金标准。

**验收标准（本次已核实）**
- ✅ `.venv/bin/python -m pytest tests/ -q` → **135 passed**。
- ✅ `ruff check` 对 6 个相关文件 → **All checks passed**。
- ✅ `get_judge("agent")` 走工厂路径，不在 import 期构造（`judge/__init__.py:26-52`）。

**风险**
- ⚠️ `ruff check .` 全仓 39 errors **是改动前既存**（`git stash` 验证），集中在 `scripts/` 与 `tests/`——但**收尾时需清理**，否则「ruff 零告警」门禁过不了。

---

### Phase 2 — 作用域白名单与契约加固（**待做，优先级最高**）

**产出物**
1. `src/nlaut/judge/agent_tools.py`：新增 `ToolPolicy`（§3.1）+ 三个工具的白名单检查 + `raw["scope_violations"]` 记账。
2. `src/nlaut/judge/agent_judge.py`：`raw` 补 `parse_mode` / `scope_violations` / `elapsed_ms` / `temperature` / `max_steps` / `step_limit_hit`（§4.4）。
3. `src/nlaut/judge/agent_judge.py`：整体墙钟超时（缺口 G4）+ `max_cost_usd` 拦截逻辑（缺口 G3）。
4. `src/nlaut/judge/agent_judge.py`：回灌消息补 `name` 字段（§2.3）。
5. `src/nlaut/judge/protocol.py`：`EngineName` 加 `"agent"` + `Evidence` 加新字段（§5.2）。
6. `src/nlaut/judge/agent_judge.py`：`raw["evidence_enriched"]` 回填（§5.3，缺口 G8）。
7. `src/nlaut/judge/arbiter.py`：确定性直通分支护栏（§4.3）。
8. `src/nlaut/ir/model.py`：`AssertAgentVerify` 加 `allowed_tools` / `fallback_mode` / `fallback_params`（§7.2）。
9. `src/nlaut/executor/runner.py`：`_question_for` 翻译新字段。

**验收标准**
- 越权 URL/路径 → 返回结构化 error，**不发请求/不读文件**；`raw["scope_violations"]` 有记录。
- `Evidence.model_dump_json()` / `model_validate_json()` 往返通过（新字段向后兼容）。
- `tests/judge_test.py:74-76`（deterministic 路由）仍绿——护栏不改变既有行为。
- 新增元测试类 `TestAgentJudgeScopePolicy` / `TestArbiterGuard` 全绿。
- ruff + pytest 全绿。

**风险**
- **回填 Evidence 会改变 `Verdict.raw` 体积**（trace 已在 raw 里，evidence_enriched 又带一遍）→ 报告 HTML 变大。建议 `evidence_enriched` 只带**引用**（finding 索引），不带全文；或只落 `EvidenceStore`，不进 raw。

---

### Phase 3 — 降级链与 CLI 接入（**待做**）

**产出物**
1. `src/nlaut/executor/runner.py`：`judge()` 调用处 try/except + `_fallback_verdict`（agent → laya → deterministic → 人工）（§6.5）。
2. `src/nlaut/cli.py`：`--agent-judge-model` / `--agent-judge-base-url` / `--agent-judge-api-key` / `--agent-max-steps` 参数，`api_config` 透传 `agent_judge` 子字典。
3. `src/nlaut/judge/agent_judge.py`：支持从运行时配置覆盖 `max_steps`（IR 的 `a.max_steps` 已透传到 `question.context`，但引擎**当前未消费**——需补）。

**验收标准**
- 模拟 agent 端点 500 → `engine == "laya"`，`raw.degraded_from == "agent"`。
- agent + Laya 都挂 → `value=0.5/conf=0.5` → `route()` → `human_review`，**且用例状态是 `need_review` 不是 `error`**。
- `judge()` 内注入异常 → 不冒泡，返回降级 Verdict。
- **配置透传链端到端实测**（api → runner → cli → 引擎），不能只看单测。

**风险**
- **配置透传链断链**是 nlaut 已知坑（skill 记：「新增任何推理参数都走同一条路径」——`api.py` → `runner.py` → `cli.py` → 报告脚本）。本次透传 4 段，必须端到端实测。
- **降级到 Laya 会引入 judge 层跨引擎依赖**——建议放 runner 层而非 agent 引擎内部，避免循环依赖。

---

### Phase 4 — 金标准扩充与实测（**待做**）

**产出物**
1. `tests/agent_judge_test.py`：`GOLDEN_SAMPLES` 从 8 → 30+（PoC 方案建议 16，含 E1~E8 的双源真值/差分/日志通道样本）。
2. 差分机制：`Evidence.db_snapshot_before` / `db_snapshot_after` / `store_diff` + `query_db` 支持增量查询（§5.2）。
3. 正反问法配对样本（对齐 `tests/judge_golden/golden.jsonl` 的既有做法）。
4. 重复轮次 R=5 的抖动度量脚本（J1/J2/J3 三层判别）。
5. 通道层写入 `db_state` / `http_trace` / `server_log`（§5.5）。

**验收标准**
- 陷阱 FP = 0，FN ≤ 1（§8.5 门禁表）。
- 样本量 ≥ 30（`docs/judge-protocol.md` 门槛）。
- 「金标准真伪自检」扩展到全部陷阱样本。
- `pytest -m agent` 真实端点跑通，无降级。

**风险**
- **不能用单模型自身输出来生成金标准**（自证循环）——必须人工或用确定性脚本生成真值。
- 通道层写入涉及 3 个通道，改动面大——可先只做 api 通道。

---

### Phase 5 — 报告层增强（**待做，非阻塞**）

**产出物**
1. `src/nlaut/report/render.py:49-55`：KPI 分桶把 `agent` 单列（现在 agent 混进「AI 判定」桶）。
2. `src/nlaut/report/render.py`：agent 判定展示「取证轨迹」（工具调用列表 + 每步结论 + 降级标记）。

**风险**：低。报告层对 `raw` 是整体渲染，加内容不破坏既有模板。

---

### Phase 6 — 封顶放开评估（**远期，需数据支撑**）

**前置条件**：Phase 4 的金标准集上，confidence ≥ 0.9 区间的准确率 ≥ 98%，且有校准曲线。

**产出物**：`CONFIDENCE_CAP` 从 0.85 提到 0.95（或放开）+ `docs/judge-protocol.md` 同步。

**风险**：**误判为通过的风险随放开线性上升**。在 FP 不能稳定压到 0 之前**不得放开**。

---

## 11. 设计决策速查表

| # | 决策 | 理由 | 状态 |
| --- | --- | --- | --- |
| 1 | 新增 Judge 实现（`AgentJudge`），**不**新增 Executor Channel | Agent 的调查步骤运行时产生，无法进 IR；报告层零改动；通道化会双重执行污染被测系统 | ✅ 已实现 |
| 2 | 取证能力放在**只读工具层** + 依赖注入 | 「查什么」由 judge 决定、「数据从哪来」由调用方注入；只读是硬边界 | ✅ 已实现 |
| 3 | 工具**作用域白名单**（`ToolPolicy`）+ 框架侧越界记账 | 只读性由「实现方保证」不够；越界必须可自动检测才可度量（PoC C9） | ⚠️ **待做** |
| 4 | `Confidence` 硬封顶 0.85，**永不自动裁决** | Agent 自报 confidence 无校准（连 VLM 的 logprob 代理都没有）；封顶让 Agent 定位为「人工仲裁的预处理器」而非「自动裁决者」 | ✅ 已实现 |
| 5 | 新增 `agent_verify` **一个** kind（不再加 `agent_trace`） | `objective` 已是自由文本，过程验证可写入其中；kind 越少漏注册风险越低；判别联合是拒绝面 | ✅ 已实现 |
| 6 | 取证结果回填 `Evidence.agent_findings` | 保住「判定基于 Evidence 作答」的 L5 宪法；证据链闭合性不变 | ⚠️ **待做** |
| 7 | Judge 协议签名不变 | 改 `judge()` 返回值会破坏 4 个引擎 + 现有元测试；用 `raw["evidence_enriched"]` 承载回填 | ✅ 设计已定 |
| 8 | 降级链 agent → laya → deterministic → 人工，放在 **runner 层** | 避免 judge 层跨引擎循环依赖；「查不清」不等于「通过」 | ⚠️ **待做** |
| 9 | 降级后 `engine` 写真实引擎名 | 写错会让 `route()` 走错分支 | ⚠️ **待做** |
| 10 | `route()` 加 `confidence >= 0.999` 护栏 | 防未来有人把 agent 的 engine 误填成 "deterministic" 而绕过置信度检查；不改变既有行为（deterministic 恒 1.0） | ⚠️ **待做** |
| 11 | 用**工厂注册**（`register_judge_factory`）而非实例注册 | `AgentJudge.__init__` 需要 `base_url`/`model`；import 期构造会触发 models.yaml 读取且 key 时机不对 | ✅ 已实现 |
| 12 | 金标准**真值由测试直接构造** + 防陷阱失效自检 | nlaut 历史踩过 CSS override 假阳性坑；「5/5 全对」前必须先独立验证金标准真伪 | ✅ 已实现 |

---

## 附录 A：新增/修改文件清单

| 文件 | 动作 | Phase | 状态 |
| --- | --- | --- | --- |
| `src/nlaut/judge/agent_judge.py` | 新增：`AgentJudge` | 1 | ✅ |
| `src/nlaut/judge/agent_tools.py` | 新增：6 个只读工具 | 1 | ✅ |
| `src/nlaut/judge/agent_tools.py` | 改：`ToolPolicy` 白名单 | 2 | ⚠️ |
| `src/nlaut/judge/agent_judge.py` | 改：raw 补字段 / 墙钟 / 成本拦截 / evidence 回填 | 2 | ⚠️ |
| `src/nlaut/judge/protocol.py` | 改：`Evidence` 新字段 + `EngineName` 加 `"agent"` | 2 | ⚠️ |
| `src/nlaut/judge/arbiter.py` | 改：`route()` 护栏 + agent 说明分支 | 2 | ⚠️ |
| `src/nlaut/ir/model.py` | 改：`AssertAgentVerify` 加 `allowed_tools` / `fallback_*` | 2 | ⚠️ |
| `src/nlaut/executor/runner.py` | 改：`_question_for` 新字段 + 降级链 | 2–3 | ⚠️ |
| `src/nlaut/cli.py` | 改：agent judge 参数 | 3 | ⚠️ |
| `src/nlaut/judge/__init__.py` | 改：注册 "agent"（工厂） | 1 | ✅ |
| `src/nlaut/ir/model.py` | 改：`AssertAgentVerify` + union | 1 | ✅ |
| `docs/ir-schema.md` | 改：断言表行 + 专章 | 1 | ✅ |
| `docs/judge-protocol.md` | 改：宪法措辞 + agent 引擎差异说明 | 2 | ⚠️ |
| `tests/agent_judge_test.py` | 新增：元测试 + 8 样本金标准 | 1 | ✅ |
| `tests/agent_judge_test.py` | 改：扩到 30+ 样本 + 新测试类 | 4 | ⚠️ |
| `tests/llm_ir_test.py` / `tests/ir_model_test.py` | 改：`TestAgentAssertionIR` + 漏注册防护 | 2 | ⚠️ |
| `pyproject.toml` | 改：`agent` marker | 1 | ✅ |
| `cases/demo/tc_agent_verify_001.yaml` | 新增：G-11 示例 | 1 | ✅ |
| `src/nlaut/report/render.py` | 改：agent 分桶 + 取证轨迹展示 | 5 | ⚠️ |
| `config/models.yaml` | 改：agent judge 模型预设（默认 `deepseek-v4-flash` 已存在） | 3 | ⚠️ |

## 附录 B：引用索引（全部为实际读取核实）

| 引用 | 位置 |
| --- | --- |
| Judge Protocol 定义 | `src/nlaut/judge/protocol.py:68-70` |
| `register_judge` / `get_judge` | `src/nlaut/judge/protocol.py:73-84` |
| `register_judge_factory`（新增） | `src/nlaut/judge/protocol.py:76-90` |
| Evidence 定义 | `src/nlaut/judge/protocol.py:20-44` |
| `db_state` 字段（执行器无写入方） | `src/nlaut/judge/protocol.py:32` |
| Question / QuestionKind | `src/nlaut/judge/protocol.py:16,47-53` |
| Verdict / EngineName | `src/nlaut/judge/protocol.py:17,56-64` |
| L5 宪法「不得访问外部状态」 | `src/nlaut/judge/protocol.py:2-3` |
| `route()` 全部分支 | `src/nlaut/judge/arbiter.py:26-42` |
| `route()` 的 0.9 硬编码 | `src/nlaut/judge/arbiter.py:34` |
| deterministic 10 种 mode 分派 | `src/nlaut/judge/deterministic.py:33-58` |
| deterministic 缺失数据 → 转人工语义 | `src/nlaut/judge/deterministic.py:367-372` |
| deterministic tool_call arguments 的 json.loads | `src/nlaut/judge/deterministic.py:304` |
| laya Question → 三题型映射 | `src/nlaut/judge/laya.py:84-101` |
| laya `render_state`（不改） | `src/nlaut/judge/laya.py:22-63` |
| vlm 温度 0 硬规则 / logprob 置信度代理 | `src/nlaut/judge/vlm_mlx.py:6,37-56` |
| 引擎懒加载注册 | `src/nlaut/judge/__init__.py:13-15` |
| `register_judge_factory("agent", ...)` | `src/nlaut/judge/__init__.py:59` |
| `_build_agent_judge` 工厂函数 | `src/nlaut/judge/__init__.py:26-52` |
| `AgentJudge` 类 / `__init__` 签名 | `src/nlaut/judge/agent_judge.py:109-140` |
| `CONFIDENCE_CAP = 0.85` | `src/nlaut/judge/agent_judge.py:33` |
| `DEFAULT_MAX_STEPS` / `TIMEOUT_S` / `MAX_COST_USD` | `src/nlaut/judge/agent_judge.py:36-38` |
| `parse_judgement` 两段式 | `src/nlaut/judge/agent_judge.py:55-87` |
| `parse_confidence` | `src/nlaut/judge/agent_judge.py:90-106` |
| `_run_tool` 错误信封 | `src/nlaut/judge/agent_judge.py:144-155` |
| `_call` httpx（trust_env=False） | `src/nlaut/judge/agent_judge.py:159-182` |
| `judge()` 主循环 | `src/nlaut/judge/agent_judge.py:186-296` |
| 降级 Verdict（步数耗尽 / 端点异常） | `src/nlaut/judge/agent_judge.py:266-296` |
| `_render_brief` | `src/nlaut/judge/agent_judge.py:298-322` |
| `build_agent_judge_from_env` | `src/nlaut/judge/agent_judge.py:325-372` |
| 6 个工具 schema | `src/nlaut/judge/agent_tools.py:21-121` |
| `build_tool_impls` 依赖注入 | `src/nlaut/judge/agent_tools.py:124-201` |
| `_query_db` 只读快照 | `src/nlaut/judge/agent_tools.py:145-158` |
| `default_http_probe` / `default_log_reader` / `default_file_reader` | `src/nlaut/judge/agent_tools.py:204-236` |
| `build_env_tool_impls` | `src/nlaut/judge/agent_tools.py:239-254` |
| `AssertAgentVerify` | `src/nlaut/ir/model.py:254-278` |
| `Assertion` 判别 union | `src/nlaut/ir/model.py:280-288` |
| IR steps 禁代码式声明 | `src/nlaut/ir/model.py:19` |
| `_question_for` 的 agent_verify 分支 | `src/nlaut/executor/runner.py:127-137` |
| `_engine_name` 的 agent_verify 分支 | `src/nlaut/executor/runner.py:156-158` |
| judge 调用 + route + threshold | `src/nlaut/executor/runner.py:215-243` |
| `Evidence(case_id=ir.id, **ev_kw)` 展开 | `src/nlaut/executor/runner.py:212` |
| runner 单用例崩溃兜底（记为 error） | `src/nlaut/executor/runner.py:329-338` |
| api tools 透传 body | `src/nlaut/executor/channels/api.py:116-117` |
| api `disable_thinking` / `reasoning_effort` | `src/nlaut/executor/channels/api.py:118-121` |
| api httpx `trust_env=False` | `src/nlaut/executor/channels/api.py:204` |
| api tool_calls 解析 | `src/nlaut/executor/channels/api.py:314-317,168-180` |
| SessionLog append-only + replay | `src/nlaut/harness/session.py:14-39` |
| EvidenceStore 落盘 | `src/nlaut/report/evidence.py:15-29` |
| 报告 KPI 分桶 / error 计入 failed | `src/nlaut/report/render.py:45-59` |
| 接入新引擎三门槛 | `docs/judge-protocol.md:48-52` |
| 三级漏斗现状 | `docs/architecture.md:43-53` |
| IR 断言表 / 规则 | `docs/ir-schema.md:73-98` |
| 判定层可用性守卫事故记录 | `tests/judge_availability_guard.py:1-79` |
| 现有元测试风格（协议 + 路由） | `tests/judge_test.py:25-91` |
| 现有元测试风格（IR + 确定性模式） | `tests/llm_ir_test.py:93-372` |
| agent 元测试 + 金标准集 | `tests/agent_judge_test.py:1-411` |
| 防陷阱失效自检 | `tests/agent_judge_test.py:298-327` |
| G-11 PoC 方案（指标定义 + Go 门槛） | `docs/agent-as-judge-poc-plan.md` |
