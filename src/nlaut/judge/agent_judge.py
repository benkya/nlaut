"""L5 Agent 判定引擎：Agent-as-Judge（自主取证型判定器）。

与 LLM-as-Judge 的本质区别（不是"更高级"，是"证据从哪来"）：
- LLM-as-Judge：判定者只能读调用方拼进 prompt 的证据，无法自主取证。
- Agent-as-Judge：判定者自己决定查什么、用什么工具取证，可与环境交互。

实现要点（v0.4.0）:
1. 多轮 OpenAI-compatible function calling 循环：模型输出 tool_calls → 执行工具
   → 把工具结果作为 role="tool" 消息回灌 → 直到模型不再请求工具。
2. 工具白名单 + 只读约束：judge 只能调注册过的工具，工具实现方保证只读语义。
3. 置信度封顶：agent judge 的 confidence 上限为 CONFIDENCE_CAP（默认 0.85），
   保证 route() 一定把它路由到 human_review 分支——agent judge 的结论是
   「证据充分的判定」，不是「无条件的通过许可」。
4. 降级：端点不可用 / 无 key / 工具全失败时返回 degraded verdict，
   confidence=0 → route() 走 human_review，绝不误判为通过。
5. 全程留痕：raw 里保留每一步的 tool_calls / tool 返回 / 最终理由，可回放。

实测可行性（2026-10-30）：deepseek-v4-flash / deepseek-v4-pro-0813 端点
tool_calls 正常，多轮工具循环跑通（取工具→注入结果→再决策→最终结论）。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .protocol import Evidence, Question, Verdict

# agent judge 的置信度上限：低于 route() 的 0.9 自动裁决阈值，
# 保证 agent judge 结论必须经人工仲裁（软件工厂出口把关场景的硬约束）。
CONFIDENCE_CAP = 0.85

# 单次判定的默认上限：步数 / 超时 / 成本（美元，按 token 粗估）
DEFAULT_MAX_STEPS = 6
DEFAULT_TIMEOUT_S = 180.0
DEFAULT_MAX_COST_USD = 0.50

# 单次 HTTP 调用的硬上限（秒）。端点偶发长尾实测 p95 达 127s，
# 超过此值即熔断 → 本次判定降级为 confidence=0（转人工），不再等待。
DEFAULT_PER_CALL_TIMEOUT_S = 45.0

# 成本估算单价（USD / 1k token）。默认 $0.6/1M token 属保守估计；
# 仅用于 max_cost_usd 预算拦截，不是实际账单。
DEFAULT_COST_PER_1K_TOKENS_USD = 0.0006

# 端点偶发抖动的重试次数与退避基数。
# 实测：连续 25 次调用 p95=2.02s、0% 超 10s，但同一天观察到 88s 长尾
# （completion_token 仅 151，与快请求同量级 → 与 token 数无关，
#  是端点侧推理调度抖动）。重试只在抖动/故障时付出成本。
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_BACKOFF_S = 0.5

if TYPE_CHECKING:  # pragma: no cover — 仅类型引用，避免运行时循环导入
    from .agent_tools import ToolPolicy

# 模型自评置信度的解析关键词（模型可自报 High/Medium/Low）
_CONF_MAP = {
    "high": 0.85,
    "medium": 0.6,
    "low": 0.3,
    "高": 0.85,
    "中": 0.6,
    "低": 0.3,
}

# 判定结论解析
_TRUE_PAT = ("true", "yes", "通过", "成立", "符合", "是")
_FALSE_PAT = ("false", "no", "不通过", "不成立", "不符合", "否")


def parse_judgement(text: str) -> bool | None:
    """从模型最终回答里解析 true/false 判定。

    优先匹配显式标记（judgement: true / 判定：通过 / 结论：不通过），
    找不到显式标记时退化为「首个结论性段落关键词」启发式。
    无法解析返回 None → 上层降级为 human_review。
    """
    if not text:
        return None
    low = text.lower()

    # 1) 显式标记优先
    for marker in ("judgement:", "judgement：", "判定:", "判定：", "结论:", "结论：", "结果:", "结果："):
        idx = low.find(marker)
        if idx == -1:
            continue
        seg = low[idx + len(marker): idx + len(marker) + 80]
        for pat in _TRUE_PAT:
            if seg.lstrip().startswith(pat):
                return True
        for pat in _FALSE_PAT:
            if seg.lstrip().startswith(pat):
                return False

    # 2) 启发式：取前 300 字，找结论性关键词
    head = low[:300]
    true_hits = sum(head.count(p) for p in _TRUE_PAT)
    false_hits = sum(head.count(p) for p in _FALSE_PAT)
    if true_hits > false_hits and true_hits > 0:
        return True
    if false_hits > true_hits and false_hits > 0:
        return False
    return None


def parse_confidence(text: str) -> float:
    """解析模型自评置信度（High/Medium/Low 或 0-1 数字）。默认 0.6。"""
    if not text:
        return 0.6
    low = text.lower()
    for key, val in _CONF_MAP.items():
        if key in low:
            return val
    # 找 0.xx / 0.x 形式的置信度数字
    for token in low.replace(":", " ").split():
        t = token.strip(".,;|()[]{}")
        if t.startswith(("0.", "1.0")):
            try:
                return min(1.0, max(0.0, float(t)))
            except ValueError:
                continue
    return 0.6


class AgentJudge:
    """Agent-as-Judge 引擎：judge 自主决定取证路径。

    与其他引擎的差异：
    - 需要外部 LLM 端点（支持 function calling）
    - 需要工具注册表（judge 可调用的取证工具）
    - 产出 Verdict，raw 里带完整取证轨迹
    """

    engine = "agent"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        tools: list[dict] | None = None,
        tool_impls: dict[str, Callable[[dict], Any]] | None = None,
        max_steps: int = DEFAULT_MAX_STEPS,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        max_cost_usd: float = DEFAULT_MAX_COST_USD,
        temperature: float = 0.0,
        per_call_timeout_s: float = DEFAULT_PER_CALL_TIMEOUT_S,
        cost_per_1k_tokens_usd: float = DEFAULT_COST_PER_1K_TOKENS_USD,
        policy: ToolPolicy | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_s: float = DEFAULT_RETRY_BACKOFF_S,
        require_tool_call: bool = False,  # 强制要求至少调用一次工具
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.tools = tools or []
        self.tool_impls = tool_impls or {}
        self.max_steps = max_steps
        self.timeout_s = timeout_s
        self.max_cost_usd = max_cost_usd
        self.temperature = temperature
        # 单次 HTTP 调用的硬上限：端点偶发长尾（实测 p95 127s）时，
        # 不能让一次判定卡住 2 分钟——超时即降级转人工（v0.4.0 延迟熔断）。
        self.per_call_timeout_s = per_call_timeout_s
        # 成本估算：按 token 单价折算 USD（默认 0.0006 USD/1k token，
        # 即 $0.6/1M token；仅用于预算拦截，不是账单）。
        self.cost_per_1k_tokens_usd = cost_per_1k_tokens_usd
        # 作用域策略：与 tool_impls 共享同一实例（越界由工具实现记账）
        self._policy = policy
        # 端点抖动重试（正常路径不增加延迟）
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s
        # 强制工具调用（用于代码审查等场景）
        self.require_tool_call = require_tool_call

    # --- 工具执行 ---

    def _scope_violations(self) -> list[dict]:
        """取出本次判定期间记录的作用域越界（若有）。

        工具实现把越界记在共享的 ToolPolicy.violations 里（judge 层不直接
        持有 policy，通过 tool_impls 的闭包共享同一个 policy 实例）。
        """
        policy = self._policy
        if policy is None:
            return []
        return list(policy.violations)

    def _run_tool(self, name: str, args: dict) -> Any:
        """执行单个工具。未知工具 → 返回错误 dict（不抛异常，交给模型判断）。"""
        impl = self.tool_impls.get(name)
        if impl is None:
            return {"error": f"unknown_tool:{name}", "hint": "该工具不可用，勿据此下结论"}
        try:
            result = impl(args)
        except Exception as e:  # noqa: BLE001 — 工具失败不应中断整个判定
            return {"error": f"{type(e).__name__}: {e}"}
        if not isinstance(result, (dict, list, str, int, float, bool, type(None))):
            result = str(result)
        return result

    # --- HTTP 调用 ---

    def _call(self, messages: list[dict], with_tools: bool) -> tuple[dict, float]:
        """调用端点，返回 (response_json, 本次调用估算成本 USD)。

        重试策略（应对端点偶发长尾）：
        实测 2026-10-30 连续 25 次调用 p95=2.02s、0% 超 10s，
        但同一天也观察到 88s 的偶发长尾（p50=1.44s 时出现一次）。
        根因是**端点侧推理调度抖动**，与 token 数、agent 逻辑无关
        （长尾那次 completion_token 仅 151，与快请求同量级）。
        因此对超时/5xx 做**指数退避重试**：正常路径 0 重试（不增加延迟），
        只有抖动/故障时才付重试成本。
        """
        import httpx

        body: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if with_tools and self.tools:
            body["tools"] = self.tools
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url}/chat/completions"
        last_err: Exception | None = None

        for attempt in range(self.max_retries + 1):
            if attempt:
                # 指数退避：0.5s → 1s → 2s …
                time.sleep(self.retry_backoff_s * (2 ** (attempt - 1)))
            try:
                resp = httpx.post(
                    url,
                    json=body,
                    headers=headers,
                    timeout=self.per_call_timeout_s,
                    trust_env=False,
                )
            except httpx.TimeoutException as e:
                last_err = e
                continue
            except httpx.HTTPError as e:
                last_err = e
                continue

            if resp.status_code == 200:
                data = resp.json()
                return data, self._usage_cost(data)

            # 5xx 与 429 视为可重试；4xx（除 429）是请求本身有问题，重试无意义
            if resp.status_code in (429,) or 500 <= resp.status_code < 600:
                last_err = RuntimeError(
                    f"agent judge 端点 HTTP {resp.status_code}: {resp.text[:200]}"
                )
                continue
            raise RuntimeError(
                f"agent judge 端点 HTTP {resp.status_code}: {resp.text[:200]}"
            )

        raise RuntimeError(f"agent judge 端点重试 {self.max_retries} 次仍失败: {last_err}")

    def _usage_cost(self, data: dict) -> float:
        """按 usage 估算本次调用成本 USD。"""
        usage = data.get("usage") or {}
        tokens = int(usage.get("total_tokens") or 0)
        if not tokens:
            tokens = int(usage.get("prompt_tokens") or 0) + int(
                usage.get("completion_tokens") or 0
            )
        return tokens / 1000.0 * self.cost_per_1k_tokens_usd

    # --- 主判定流程 ---

    def judge(self, evidence: Evidence, question: Question) -> Verdict:
        system_prompt = question.context.get("system_prompt") or (
            "你是取证型判定器（Agent-as-Judge）。\n"
            "规则：\n"
            "1. 必须先用工具取证，再给结论；不得凭空判断。\n"
            "2. 结论基于最终/实际状态，不基于中间步骤。\n"
            "3. 探索要完整：找不到目标前，先穷举相关视图/表/接口再判失败。\n"
            "4. 无法取证时判为不确定并说明缺什么证据。\n"
            "5. **取证要高效**：能用一次工具调用拿到的证据，不要拆成多次；\n"
            "   优先查最可能直接证伪目标的那一项（如验证落库先查表）。\n"
            "   工具返回 error 或空结果时，不要反复重试同一个来源。\n"
            "6. **取证完成后立即给结论**，不要再补充额外检查。\n"
            "输出格式（严格三行，不要额外章节）：\n"
            "judgement: true|false\n"
            "理由: <基于你实际取证到的证据，一句话点明关键证据>\n"
            "CONFIDENCE: High|Medium|Low"
        )
        objective = question.text or question.context.get("objective", "")

        # max_steps 可由 IR 断言覆盖（Question.context 优先），否则用引擎默认值
        max_steps = int(question.context.get("max_steps") or self.max_steps)

        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": self._render_brief(evidence, objective)},
        ]

        trace: list[dict] = []
        tool_calls_total = 0
        rounds = 0
        cost_usd = 0.0
        t_start = time.time()

        try:
            for rounds in range(1, max_steps + 1):
                # 成本预算拦截：超预算立即降级（绝不继续烧钱）
                if cost_usd >= self.max_cost_usd:
                    return Verdict(
                        engine=self.engine,
                        kind="noul",
                        value=0.5,
                        confidence=0.0,
                        evidence_refs=[evidence.case_id],
                        raw={
                            "degraded": "max_cost_exceeded",
                            "cost_usd": round(cost_usd, 6),
                            "max_cost_usd": self.max_cost_usd,
                            "rounds": rounds,
                            "tool_calls_total": tool_calls_total,
                            "elapsed_ms": round((time.time() - t_start) * 1000, 1),
                            "trace": trace,
                            "model": self.model,
                        },
                    )

                data, call_cost = self._call(messages, with_tools=True)
                cost_usd += call_cost
                msg = (data.get("choices") or [{}])[0].get("message", {})
                tool_calls = msg.get("tool_calls") or []
                content = msg.get("content") or ""

                if not tool_calls:
                    # 模型给出最终结论
                    # 强制工具调用检查：如果要求调用工具但没有调用，降级为不确定
                    if self.require_tool_call and tool_calls_total == 0:
                        return Verdict(
                            engine=self.engine,
                            kind="noul",
                            value=0.5,
                            confidence=0.0,
                            evidence_refs=[evidence.case_id],
                            raw={
                                "degraded": "no_tool_call",
                                "reason": "强制要求调用工具，但 Agent 未调用任何工具",
                                "rounds": rounds,
                                "tool_calls_total": tool_calls_total,
                                "cost_usd": round(cost_usd, 6),
                                "elapsed_ms": round((time.time() - t_start) * 1000, 1),
                                "scope_violations": self._scope_violations(),
                                "trace": trace,
                                "model": self.model,
                            },
                        )
                    parsed = parse_judgement(content)
                    conf = min(CONFIDENCE_CAP, parse_confidence(content))
                    value = 1.0 if parsed is True else (0.0 if parsed is False else 0.5)
                    return Verdict(
                        engine=self.engine,
                        kind="noul",
                        value=value,
                        confidence=0.0 if parsed is None else conf,
                        evidence_refs=[evidence.case_id],
                        raw={
                            "judgement": parsed,
                            "confidence_raw": parse_confidence(content),
                            "reason": content[:2000],
                            "rounds": rounds,
                            "tool_calls_total": tool_calls_total,
                            "cost_usd": round(cost_usd, 6),
                            "elapsed_ms": round((time.time() - t_start) * 1000, 1),
                            "scope_violations": self._scope_violations(),
                            "trace": trace,
                            "model": self.model,
                        },
                    )

                # 执行工具并回灌
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
                    trace.append(
                        {
                            "step": rounds,
                            "tool": fname,
                            "args": args,
                            "result": result,
                        }
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": json.dumps(result, ensure_ascii=False)[:4000],
                        }
                    )

            # 步数耗尽 → 降级（不确定，转人工）
            return Verdict(
                engine=self.engine,
                kind="noul",
                value=0.5,
                confidence=0.0,
                evidence_refs=[evidence.case_id],
                raw={
                    "degraded": "max_steps_exhausted",
                    "max_steps": max_steps,
                    "rounds": rounds,
                    "tool_calls_total": tool_calls_total,
                    "cost_usd": round(cost_usd, 6),
                    "elapsed_ms": round((time.time() - t_start) * 1000, 1),
                    "scope_violations": self._scope_violations(),
                    "trace": trace,
                    "model": self.model,
                },
            )
        except Exception as e:  # noqa: BLE001 — 端点挂了等，降级不误判为通过
            return Verdict(
                engine=self.engine,
                kind="noul",
                value=0.5,
                confidence=0.0,
                evidence_refs=[evidence.case_id],
                raw={
                    "degraded": f"{type(e).__name__}: {e}",
                    "rounds": rounds,
                    "tool_calls_total": tool_calls_total,
                    "cost_usd": round(cost_usd, 6),
                    "elapsed_ms": round((time.time() - t_start) * 1000, 1),
                    "scope_violations": self._scope_violations(),
                    "trace": trace,
                    "model": self.model,
                },
            )

    @staticmethod
    def _render_brief(evidence: Evidence, objective: str) -> str:
        """把 Evidence 渲染成给 agent judge 的初始简报（它能看到的起点）。"""
        parts = [f"待判定目标: {objective}", f"用例: {evidence.case_id}"]
        if evidence.conversation:
            user_turns = [m.get("content", "") for m in evidence.conversation if m.get("role") == "user"]
            if user_turns:
                parts.append(f"用户输入: {' / '.join(t[:300] for t in user_turns)}")
        if evidence.llm_response:
            parts.append(f"被测系统输出: {evidence.llm_response[:600]}")
        if evidence.tool_calls:
            calls = "; ".join(
                f"{c.get('function', {}).get('name')}({str(c.get('function', {}).get('arguments'))[:100]})"
                for c in evidence.tool_calls
            )
            parts.append(f"被测系统已发生的工具调用: {calls}")
        if evidence.db_state:
            parts.append(f"数据库状态快照: {json.dumps(evidence.db_state, ensure_ascii=False)[:500]}")
        if evidence.dom_state:
            dom = "; ".join(f"{s}->{t[:80]}" for s, t in list(evidence.dom_state.items())[:20])
            parts.append(f"DOM 可见状态: {dom}")
        if evidence.screenshot:
            parts.append(f"截图路径: {evidence.screenshot}")
        parts.append("请用工具自行取证，然后给出 judgement: true|false 的结论。")
        return "\n".join(parts)


def build_agent_judge_from_env(
    tools: list[dict] | None = None,
    tool_impls: dict[str, Callable[[dict], Any]] | None = None,
    model_key: str = "deepseek-v4-flash",
    config_path: str | None = None,
    policy: ToolPolicy | None = None,
    max_cost_usd: float = DEFAULT_MAX_COST_USD,
    per_call_timeout_s: float = DEFAULT_PER_CALL_TIMEOUT_S,
) -> AgentJudge:
    """从 config/models.yaml + 环境变量构造 AgentJudge。

    优先读 config/models.yaml 的模型配置；api_key 从 api_key_env 指定的环境变量读取。
    端点不可用时抛异常（调用方负责降级）。

    policy: 工具作用域白名单。为 None 时用默认拒绝策略
            （http_probe / read_file / read_log 全部拒绝）。
    """
    import yaml

    if config_path is None:
        # src/nlaut/judge/agent_judge.py → 上溯 3 级到仓库根，再进 config/
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "config",
            "models.yaml",
        )
        config_path = os.path.normpath(config_path)
    if not os.path.exists(config_path):
        # 兜底：仓库根可能不是上溯 3 级（如 site-packages 安装态）
        repo_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )
        alt = os.path.join(repo_root, "config", "models.yaml")
        if os.path.exists(alt):
            config_path = alt
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    models = (cfg or {}).get("models") or {}
    if model_key not in models:
        raise KeyError(f"模型 {model_key!r} 不在 {config_path}（可用: {sorted(models)}）")
    m = models[model_key]
    base_url = m.get("base_url")
    model_id = m.get("model_id")
    if not base_url or not model_id:
        raise ValueError(f"模型 {model_key!r} 配置不完整: base_url={base_url!r} model_id={model_id!r}")
    env_name = m.get("api_key_env") or ""
    api_key = os.environ.get(env_name, "") if env_name else ""
    return AgentJudge(
        base_url=base_url,
        model=model_id,
        api_key=api_key,
        tools=tools,
        tool_impls=tool_impls,
        policy=policy,
        max_cost_usd=max_cost_usd,
        per_call_timeout_s=per_call_timeout_s,
    )
