"""Agent-as-Judge 元测试 + 金标准集。

三部分：
1. 纯函数元测试（不调网络）：parse_judgement / parse_confidence / 工具实现 / 降级路径
2. 金标准样本集：8 个样本，含 2 个「表面通过但实际未落库」陷阱
3. 真实模型一致性测试（需 -m agent 标记，默认跳过）

金标准设计的核心（参考 AJ-Bench 的 trap 思路）：
- 只喂「被测系统自述成功」的 evidence，看 judge 是否被表面信号骗过
- 陷阱样本的真伪用**独立方法**验证（db_state 由测试直接构造，非模型生成）
"""

from __future__ import annotations

import json

import pytest

from nlaut.judge.agent_judge import (
    CONFIDENCE_CAP,
    AgentJudge,
    parse_confidence,
    parse_judgement,
)
from nlaut.judge.agent_tools import (
    DEFAULT_TOOLS,
    ToolPolicy,
    build_env_tool_impls,
    default_file_reader,
    default_log_reader,
)
from nlaut.judge.arbiter import route
from nlaut.judge.protocol import Evidence, Question, Verdict

# --------------------------------------------------------------------------
# 1. 纯函数元测试（无网络）
# --------------------------------------------------------------------------


class TestParseJudgement:
    def test_explicit_true_marker(self):
        assert parse_judgement("judgement: true\n理由: 已落库") is True

    def test_explicit_false_marker(self):
        assert parse_judgement("判定：不通过\n因为表为空") is False

    def test_chinese_pass_marker(self):
        assert parse_judgement("结论：通过\n证据已确认") is True

    def test_chinese_fail_marker(self):
        assert parse_judgement("结论：不通过\n数据未落库") is False

    def test_unparseable_returns_none(self):
        assert parse_judgement("我正在分析这个问题，暂时没有结论。") is None

    def test_empty_returns_none(self):
        assert parse_judgement("") is None

    def test_explicit_marker_beats_heuristic(self):
        """显式标记优先于正文关键词启发式。"""
        text = "表里看到数据齐全，但检查后发现字段不一致。judgement: false"
        assert parse_judgement(text) is False


class TestParseConfidence:
    def test_high_medium_low(self):
        assert parse_confidence("CONFIDENCE: High") == 0.85
        assert parse_confidence("CONFIDENCE: Medium") == 0.6
        assert parse_confidence("CONFIDENCE: Low") == 0.3

    def test_chinese_confidence(self):
        assert parse_confidence("置信度：高") == 0.85

    def test_numeric_confidence(self):
        assert parse_confidence("CONFIDENCE: 0.78") == pytest.approx(0.78)

    def test_default_when_absent(self):
        assert parse_confidence("没有提到置信度") == 0.6


class TestConfidenceCap:
    def test_cap_is_below_auto_pass_threshold(self):
        """硬约束：agent judge 的 confidence 必须低于 route() 的 0.9 自动裁决线。"""
        assert CONFIDENCE_CAP < 0.9

    def test_agent_verdict_never_auto_passes(self):
        """即使 agent 自报 High，route() 也必须把它送去人工仲裁。"""
        v = Verdict(
            engine="agent",
            kind="noul",
            value=1.0,
            confidence=CONFIDENCE_CAP,
            evidence_refs=["c1"],
            raw={},
        )
        decision = route(v)
        assert decision.status == "human_review"


class TestToolImpls:
    def test_query_db_returns_rows_and_count(self):
        impls = build_env_tool_impls(db_state={"task": [{"id": 1}, {"id": 2}]})
        res = impls["query_db"]({"table": "task"})
        assert res["row_count"] == 2
        assert len(res["rows"]) == 2

    def test_query_db_unknown_table_lists_available(self):
        impls = build_env_tool_impls(db_state={"task": []})
        res = impls["query_db"]({"table": "nope"})
        assert res["error"] == "table_not_found"
        assert "task" in res["available_tables"]

    def test_query_db_without_db_state_reports_error(self):
        impls = build_env_tool_impls()
        res = impls["query_db"]({"table": "task"})
        assert res["error"] == "db_state_not_provided"

    def test_inspect_trace_paginates(self):
        impls = build_env_tool_impls(trace=[{"step": i} for i in range(10)])
        res = impls["inspect_trace"]({"start": 2, "count": 3})
        assert res["total"] == 10
        assert len(res["returned"]) == 3
        assert res["returned"][0]["step"] == 2

    def test_read_log_missing_file_is_not_exception(self):
        out = default_log_reader("/nonexistent/path/x.log", 10)
        assert "log_not_found" in out

    def test_read_file_missing_file_is_not_exception(self):
        out = default_file_reader("/nonexistent/path/x.txt", 100)
        assert "file_not_found" in out

    def test_default_tools_all_have_schemas(self):
        for t in DEFAULT_TOOLS:
            assert t["type"] == "function"
            assert "name" in t["function"]
            assert "parameters" in t["function"]

    def test_locate_object_finds_matching_row(self):
        """locate_object：按关键词精确定位目标记录。"""
        impls = build_env_tool_impls(
            db_state={"task": [{"id": 1001, "name": "agent_x"}, {"id": 1002, "name": "other"}]}
        )
        res = impls["locate_object"]({"table": "task", "keyword": "agent_x"})
        assert res["matched"] == 1
        assert res["total_rows"] == 2
        assert res["matches"][0]["id"] == 1001

    def test_locate_object_returns_zero_when_absent(self):
        """目标不存在时 matched=0 —— 这是区分「有数据」与「有目标对象」的关键。"""
        impls = build_env_tool_impls(db_state={"task": [{"id": 1001, "name": "other"}]})
        res = impls["locate_object"]({"table": "task", "keyword": "agent_x"})
        assert res["matched"] == 0
        assert res["total_rows"] == 1  # 表里有数据，但目标不在

    def test_locate_object_fields_projection(self):
        impls = build_env_tool_impls(db_state={"task": [{"id": 1, "status": "ok", "secret": "x"}]})
        res = impls["locate_object"]({"table": "task", "keyword": "1", "fields": ["id", "status"]})
        assert set(res["matches"][0].keys()) == {"id", "status"}

    def test_locate_object_unknown_table(self):
        impls = build_env_tool_impls(db_state={"task": []})
        res = impls["locate_object"]({"table": "nope", "keyword": "x"})
        assert res["error"] == "table_not_found"

    def test_locate_object_requires_keyword(self):
        impls = build_env_tool_impls(db_state={"task": [{"id": 1}]})
        res = impls["locate_object"]({"table": "task", "keyword": ""})
        assert res["error"] == "keyword_required"


class TestToolPolicy:
    """工具作用域白名单（P0 安全项，v0.4.0）。

    背景：无限制时 `http_probe` 可 GET 任意内网地址、`read_file` 可读任意路径
    （含密钥文件）。软件工厂场景下这是实质风险，必须默认拒绝 + 显式放行。
    """

    def test_default_policy_denies_everything(self):
        """默认拒绝：空策略下所有 host / 路径都不放行。"""
        pol = ToolPolicy()
        assert pol.host_allowed("http://10.0.0.1:8080/x") is False
        assert pol.path_allowed("/etc/passwd") is False
        assert pol.path_allowed("/Users/Ben/.ssh/id_rsa") is False

    def test_exact_host_allowed(self):
        pol = ToolPolicy(allowed_hosts={"api.internal.corp"})
        assert pol.host_allowed("http://api.internal.corp/v1") is True
        assert pol.host_allowed("http://evil.com/v1") is False

    def test_wildcard_subdomain_allowed(self):
        pol = ToolPolicy(allowed_hosts={"*.gientech.com"})
        assert pol.host_allowed("http://token-api.gientech.com/x") is True
        assert pol.host_allowed("http://gientech.com/x") is True  # 裸域也匹配
        assert pol.host_allowed("http://evilgientech.com/x") is False

    def test_path_root_allowed(self):
        pol = ToolPolicy(allowed_path_roots={"/tmp/logs"})
        assert pol.path_allowed("/tmp/logs/app.log") is True
        assert pol.path_allowed("/tmp/logs") is True
        assert pol.path_allowed("/tmp/other/app.log") is False

    def test_path_traversal_blocked(self):
        """路径穿越必须被拦（realpath 归一后再比对）。"""
        pol = ToolPolicy(allowed_path_roots={"/tmp/logs"})
        assert pol.path_allowed("/tmp/logs/../../etc/passwd") is False

    def test_violation_is_recorded(self):
        pol = ToolPolicy()
        res = pol.record_violation("http_probe", "http://evil.com", "host 不在允许列表")
        assert res["error"] == "scope_violation"
        assert len(pol.violations) == 1
        assert pol.violations[0]["tool"] == "http_probe"

    def test_http_probe_rejected_when_out_of_scope(self):
        """越界 http_probe 返回结构化拒绝，且不真正发请求。"""
        called = []

        def fake_probe(url, timeout):
            called.append(url)
            return {"status": 200}

        impls = build_env_tool_impls(http_probe=fake_probe, policy=ToolPolicy())
        res = impls["http_probe"]({"url": "http://evil.com/secret"})
        assert res["error"] == "scope_violation"
        assert called == []  # 未真正发请求

    def test_http_probe_allowed_when_in_scope(self):
        def fake_probe(url, timeout):
            return {"status": 200, "url": url}

        impls = build_env_tool_impls(
            http_probe=fake_probe,
            policy=ToolPolicy(allowed_hosts={"api.internal.corp"}),
        )
        res = impls["http_probe"]({"url": "http://api.internal.corp/x"})
        assert res["status"] == 200

    def test_read_file_rejected_when_out_of_scope(self):
        impls = build_env_tool_impls(policy=ToolPolicy())
        res = impls["read_file"]({"path": "/Users/Ben/.ssh/id_rsa"})
        assert res["error"] == "scope_violation"

    def test_read_file_allowed_within_root(self, tmp_path):
        target = tmp_path / "app.log"
        target.write_text("hello", encoding="utf-8")
        impls = build_env_tool_impls(policy=ToolPolicy(allowed_path_roots={str(tmp_path)}))
        res = impls["read_file"]({"path": str(target)})
        assert "hello" in res

    def test_read_log_rejected_when_out_of_scope(self):
        impls = build_env_tool_impls(policy=ToolPolicy())
        res = impls["read_log"]({"path": "/var/log/app.log"})
        assert res["error"] == "scope_violation"

    def test_from_env_parses_lists(self, monkeypatch):
        monkeypatch.setenv("NLaut_AGENT_JUDGE_ALLOWED_HOSTS", "a.corp, *.b.corp")
        monkeypatch.setenv("NLaut_AGENT_JUDGE_ALLOWED_PATHS", "/tmp/a, /var/log")
        pol = ToolPolicy.from_env()
        assert pol.allowed_hosts == {"a.corp", "*.b.corp"}
        assert pol.allowed_path_roots == {"/tmp/a", "/var/log"}


class TestCostBudget:
    """成本预算拦截（P0，v0.4.0）——max_cost_usd 必须真的生效。"""

    def test_cost_budget_blocks_second_call(self):
        """预算为 0 时，第一次调用后即降级（不再发起第二次调用）。"""
        calls = []

        class FakeJudge(AgentJudge):
            def _call(self, messages, with_tools):
                calls.append(1)
                # 第一次返回工具调用，第二次若被调用说明预算没拦住
                if len(calls) == 1:
                    return (
                        {
                            "choices": [
                                {
                                    "message": {
                                        "content": "",
                                        "tool_calls": [
                                            {
                                                "id": "t1",
                                                "type": "function",
                                                "function": {"name": "q", "arguments": "{}"},
                                            }
                                        ],
                                    }
                                }
                            ],
                            "usage": {"total_tokens": 1000},
                        },
                        0.0006,  # 本次调用成本 USD
                    )
                raise AssertionError("预算未拦截，发起了第 2 次调用")

        judge = FakeJudge(
            base_url="http://127.0.0.1:1/v1",
            model="dummy",
            tool_impls={"q": lambda a: {"ok": True}},
            max_cost_usd=0.0001,  # 0.0006 > 0.0001 → 下轮即拦截
            max_steps=5,
        )
        v = judge.judge(Evidence(case_id="cost-1"), Question(kind="noul", text="验证落库"))
        assert v.confidence == 0.0
        assert v.raw["degraded"] == "max_cost_exceeded"
        assert len(calls) == 1

    def test_cost_is_accumulated_in_verdict(self):
        class FakeJudge(AgentJudge):
            def _call(self, messages, with_tools):
                return (
                    {
                        "choices": [{"message": {"content": "judgement: true\nCONFIDENCE: High"}}],
                        "usage": {"total_tokens": 500},
                    },
                    0.0003,
                )

        judge = FakeJudge(
            base_url="http://127.0.0.1:1/v1",
            model="dummy",
            tool_impls={},
        )
        v = judge.judge(Evidence(case_id="cost-2"), Question(kind="noul", text="x"))
        assert v.raw["cost_usd"] > 0
        assert v.confidence > 0


class TestPerCallTimeout:
    """单次调用熔断（P1 延迟治理，v0.4.0）。"""

    def test_per_call_timeout_default_is_bounded(self):
        """默认单次调用超时必须远小于整体超时（防端点长尾卡死）。"""
        from nlaut.judge.agent_judge import (
            DEFAULT_PER_CALL_TIMEOUT_S,
            DEFAULT_TIMEOUT_S,
        )

        assert DEFAULT_PER_CALL_TIMEOUT_S < DEFAULT_TIMEOUT_S
        assert DEFAULT_PER_CALL_TIMEOUT_S <= 60.0

    def test_timeout_is_passed_to_http_call(self):
        judge = AgentJudge(base_url="http://x/v1", model="m", per_call_timeout_s=12.5)
        assert judge.per_call_timeout_s == 12.5


class TestRetryOnEndpointJitter:
    """端点抖动重试（P1 延迟治理，v0.4.0）。

    实测根因：连续 25 次调用 p95=2.02s、0% 超 10s，
    但同一天观察到 88s 长尾，且长尾那次 completion_token 仅 151
    （与快请求同量级）→ 与 token 数、agent 逻辑无关，是**端点侧调度抖动**。
    故对超时/5xx/429 做指数退避重试。
    """

    def test_default_retries_configured(self):
        judge = AgentJudge(base_url="http://x/v1", model="m")
        assert judge.max_retries == 2
        assert judge.retry_backoff_s == 0.5

    def test_timeout_is_retried(self, monkeypatch):
        """首次超时 → 重试后成功，判定不应失败。"""
        import httpx

        judge = AgentJudge(
            base_url="http://x/v1",
            model="m",
            max_retries=2,
            retry_backoff_s=0.01,
        )
        calls = []

        class FakeResp:
            status_code = 200

            def json(self):
                return {
                    "choices": [{"message": {"content": "judgement: true"}}],
                    "usage": {"total_tokens": 100},
                }

        def fake_post(url, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise httpx.TimeoutException("slow")
            return FakeResp()

        monkeypatch.setattr("httpx.post", fake_post)
        _data, cost = judge._call([{"role": "user", "content": "x"}], with_tools=False)
        assert len(calls) == 2  # 首次超时 + 1 次重试成功
        assert cost > 0

    def test_5xx_is_retried_then_gives_up(self, monkeypatch):
        """5xx 重试到上限后抛错 → 上层降级为 confidence=0。"""
        judge = AgentJudge(
            base_url="http://x/v1",
            model="m",
            max_retries=2,
            retry_backoff_s=0.01,
        )
        calls = []

        class FakeResp:
            status_code = 503
            text = "unavailable"

        def fake_post(url, **kwargs):
            calls.append(1)
            return FakeResp()

        monkeypatch.setattr("httpx.post", fake_post)
        with pytest.raises(RuntimeError, match="重试"):
            judge._call([{"role": "user", "content": "x"}], with_tools=False)
        assert len(calls) == 3  # 首次 + 2 次重试

    def test_4xx_is_not_retried(self, monkeypatch):
        """4xx（如 401）是请求本身有问题，重试无意义 → 立即抛错。"""
        judge = AgentJudge(
            base_url="http://x/v1",
            model="m",
            max_retries=2,
            retry_backoff_s=0.01,
        )
        calls = []

        class FakeResp:
            status_code = 401
            text = "unauthorized"

        def fake_post(url, **kwargs):
            calls.append(1)
            return FakeResp()

        monkeypatch.setattr("httpx.post", fake_post)
        with pytest.raises(RuntimeError, match="401"):
            judge._call([{"role": "user", "content": "x"}], with_tools=False)
        assert len(calls) == 1  # 未重试

    def test_normal_path_does_not_retry(self, monkeypatch):
        """正常 200 不重试（不增加延迟）。"""
        judge = AgentJudge(base_url="http://x/v1", model="m", max_retries=2)
        calls = []

        class FakeResp:
            status_code = 200

            def json(self):
                return {
                    "choices": [{"message": {"content": "judgement: true"}}],
                    "usage": {"total_tokens": 100},
                }

        def fake_post(url, **kwargs):
            calls.append(1)
            return FakeResp()

        monkeypatch.setattr("httpx.post", fake_post)
        judge._call([{"role": "user", "content": "x"}], with_tools=False)
        assert len(calls) == 1


class TestDegradation:
    def test_unreachable_endpoint_degrades_to_zero_confidence(self):
        """端点不可用 → confidence=0 + degraded 标记，绝不误判为通过。"""
        judge = AgentJudge(
            base_url="http://127.0.0.1:1/v1",  # 必然连不上
            model="dummy",
            api_key="",
            tools=DEFAULT_TOOLS,
            tool_impls=build_env_tool_impls(),
            max_steps=2,
            timeout_s=3,
        )
        v = judge.judge(
            Evidence(case_id="degrade-1"),
            Question(kind="noul", text="验证落库"),
        )
        assert v.confidence == 0.0
        assert "degraded" in v.raw
        assert v.value == 0.5
        # 必须路由到人工，不能自动通过
        assert route(v).status == "human_review"

    def test_unknown_tool_returns_error_not_crash(self):
        judge = AgentJudge(
            base_url="http://127.0.0.1:1/v1",
            model="dummy",
            tools=DEFAULT_TOOLS,
            tool_impls={},
        )
        res = judge._run_tool("query_db", {"table": "x"})
        assert res["error"].startswith("unknown_tool")

    def test_tool_exception_is_captured(self):
        def boom(args):
            raise RuntimeError("db down")

        judge = AgentJudge(
            base_url="http://127.0.0.1:1/v1",
            model="dummy",
            tool_impls={"query_db": boom},
        )
        res = judge._run_tool("query_db", {})
        assert "RuntimeError" in res["error"]


# --------------------------------------------------------------------------
# 2. 金标准样本集（8 样本，含 2 个陷阱）
# --------------------------------------------------------------------------


def _evidence_for(sample: dict) -> Evidence:
    """按样本构造 Evidence。

    关键设计：evidence.llm_response 里放**被测系统自述成功**的话，
    无论真实结果如何 —— 这样 judge 若只看表面信号就会被骗过（陷阱生效）。
    """
    return Evidence(
        case_id=sample["case_id"],
        llm_response=sample.get("claimed_success", "操作已完成，任务已提交成功。"),
        db_state=sample.get("db_state"),
        tool_calls=sample.get("tool_calls"),
        dom_state=sample.get("dom_state"),
        conversation=[{"role": "user", "content": sample["objective"]}],
    )


GOLDEN_SAMPLES: list[dict] = [
    # --- A 类：真实通过 ---
    {
        "case_id": "A1-real-pass",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）",
        "claimed_success": "已成功创建任务，任务 ID 1001。",
        "db_state": {"task": [{"id": 1001, "status": "created", "module": "M"}]},
        "expected": True,
        "category": "real_pass",
    },
    {
        "case_id": "A2-real-pass-multi-row",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）",
        "claimed_success": "已成功创建任务，任务 ID 1002。",
        "db_state": {
            "task": [
                {"id": 1001, "status": "created", "module": "M"},
                {"id": 1002, "status": "created", "module": "M"},
            ]
        },
        "expected": True,
        "category": "real_pass",
    },
    # --- B 类：真实失败 ---
    {
        "case_id": "B1-real-fail-empty-table",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）",
        "claimed_success": "已成功创建任务，任务 ID 1003。",
        "db_state": {"task": []},
        "expected": False,
        "category": "real_fail",
    },
    {
        "case_id": "B2-real-fail-table-missing",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）",
        "claimed_success": "已成功创建任务，任务 ID 1004。",
        "db_state": {"other_table": [{"id": 1}]},
        "expected": False,
        "category": "real_fail",
    },
    # --- C 类：陷阱 —— 表面通过但实际未落库 ---
    {
        "case_id": "C1-trap-claimed-but-not-persisted",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）",
        "claimed_success": "已成功创建任务，任务 ID 1005。",
        "db_state": {"task": []},
        "expected": False,
        "category": "trap_surface_pass",
        "note": "被测系统自述成功，但表为空 —— LLM Judge 只看 llm_response 会判错",
    },
    {
        "case_id": "C2-trap-partial-persist-wrong-module",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）",
        "claimed_success": "已成功创建任务，任务 ID 1006。",
        "db_state": {"task": [{"id": 1006, "status": "created", "module": "OTHER"}]},
        "expected": False,
        "category": "trap_surface_pass",
        "note": "表里确有行，但 module 字段不是 M —— 需读字段而非只看行数",
    },
    # --- D 类：反向陷阱 —— 表面失败但实际已落库 ---
    {
        "case_id": "D1-trap-claimed-fail-but-persisted",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）",
        "claimed_success": "创建失败：数据库连接超时。",
        "db_state": {"task": [{"id": 1007, "status": "created", "module": "M"}]},
        "expected": True,
        "category": "trap_surface_fail",
        "note": "自述失败但实际落库 —— 需以实际状态为准，不被自述带偏",
    },
    {
        "case_id": "D2-trap-partial-fail-but-persisted",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）",
        "claimed_success": "部分成功：任务创建了，但后续同步失败。",
        "db_state": {"task": [{"id": 1008, "status": "created", "module": "M"}]},
        "expected": True,
        "category": "trap_surface_fail",
        "note": "自述部分失败，但落库这一项确实成立",
    },
    # --- E 类：定位陷阱 —— 表里有数据，但目标对象不在 ---
    {
        "case_id": "E1-trap-table-has-rows-target-missing",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）。本次创建的任务 ID 是 9999，请确认该 ID 对应的记录确实存在且 module 为 M。",
        "claimed_success": "已成功创建任务，任务 ID 9999。",
        "db_state": {"task": [{"id": 1001, "status": "created", "module": "M"}]},
        "expected": False,
        "category": "trap_locate",
        "note": "表里有 M 的行，但 ID 9999 不存在 —— 只看行数会误判为真，需 locate 能力",
    },
    # --- F 类：多表 / 跨表场景（数据在别的表里，不在 task 表） ---
    {
        "case_id": "F1-real-fail-data-in-other-table",
        "objective": "验证：模块 M 接入后任务数据真的落库到 task 表。",
        "claimed_success": "已成功创建任务，任务 ID 1010。",
        "db_state": {"task_log": [{"id": 1010, "module": "M"}]},
        "expected": False,
        "category": "multi_table",
        "note": "数据在 task_log 表，task 表没有 —— 不能因为「库里有 M 的数据」就判真",
    },
    {
        "case_id": "F2-real-pass-both-tables",
        "objective": "验证：模块 M 接入后任务数据真的落库到 task 表。",
        "claimed_success": "已成功创建任务，任务 ID 1011。",
        "db_state": {
            "task": [{"id": 1011, "status": "created", "module": "M"}],
            "task_log": [{"id": 1011, "module": "M"}],
        },
        "expected": True,
        "category": "multi_table",
        "note": "task 与 task_log 都有 → 落库成立",
    },
    # --- G 类：边界场景（空列表 / 空 dict / 计数为 0） ---
    {
        "case_id": "G1-real-fail-empty-list-not-null",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）。",
        "claimed_success": "已成功创建任务，任务 ID 1012。",
        "db_state": {"task": []},
        "expected": False,
        "category": "boundary",
        "note": "空列表（非 None）—— 空与缺失是两种情况",
    },
    {
        "case_id": "G2-real-fail-row-count-zero",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）。",
        "claimed_success": "已成功创建任务，任务 ID 1013。",
        "db_state": {"task": {"row_count": 0}},
        "expected": False,
        "category": "boundary",
        "note": "计数为 0 的 dict 形态（非 list）",
    },
    {
        "case_id": "G3-real-pass-row-count-only",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）。",
        "claimed_success": "已成功创建任务，任务 ID 1014。",
        "db_state": {"task": {"row_count": 3}},
        "expected": True,
        "category": "boundary",
        "note": "只有计数字段形态 → 落库成立（表非空）",
    },
    # --- H 类：字段语义陷阱（行存在但关键字段不对） ---
    {
        "case_id": "H1-trap-row-exists-but-status-pending",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）。要求 module 为 M 且 status 为 created。",
        "claimed_success": "已成功创建任务，任务 ID 1015。",
        "db_state": {"task": [{"id": 1015, "status": "pending", "module": "M"}]},
        "expected": False,
        "category": "field_semantics",
        "note": "行存在、module 也对，但 status 是 pending 不是 created —— 需读字段语义",
    },
    {
        "case_id": "H2-trap-row-exists-but-module-lowercase",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）。要求 module 字段为 M。",
        "claimed_success": "已成功创建任务，任务 ID 1016。",
        "db_state": {"task": [{"id": 1016, "status": "created", "module": "m"}]},
        "expected": False,
        "category": "field_semantics",
        "note": "module 是小写 m，与要求的 M 不一致 —— 大小写敏感判定",
    },
    # --- I 类：多行 / 定位歧义 ---
    {
        "case_id": "I1-real-pass-target-among-many",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）。本次创建的任务 ID 是 1020，请确认该记录存在且 module 为 M。",
        "claimed_success": "已成功创建任务，任务 ID 1020。",
        "db_state": {
            "task": [
                {"id": 1019, "status": "created", "module": "OTHER"},
                {"id": 1020, "status": "created", "module": "M"},
                {"id": 1021, "status": "created", "module": "OTHER"},
            ]
        },
        "expected": True,
        "category": "locate_ambiguity",
        "note": "多行混杂，目标 1020 确实是 M —— locate 必须精确到目标而非任意行",
    },
    {
        "case_id": "I2-trap-target-among-many-wrong-module",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）。本次创建的任务 ID 是 1022，请确认该记录存在且 module 为 M。",
        "claimed_success": "已成功创建任务，任务 ID 1022。",
        "db_state": {
            "task": [
                {"id": 1021, "status": "created", "module": "M"},
                {"id": 1022, "status": "created", "module": "OTHER"},
            ]
        },
        "expected": False,
        "category": "locate_ambiguity",
        "note": "表里有 M 的行但目标 1022 是 OTHER —— 定位到别的行就误判为真",
    },
    # --- J 类：证据不足 / 不可判定（应判不确定 → 转人工） ---
    {
        "case_id": "J1-uncertain-no-db-evidence",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）。",
        "claimed_success": "已成功创建任务，任务 ID 1030。",
        "db_state": None,
        "expected": False,
        "category": "insufficient_evidence",
        "note": "完全没有 db_state 证据 → 无法证实 → 判否（不得凭空判真）",
    },
    {
        "case_id": "J2-uncertain-claimed-only",
        "objective": "验证：模块 M 接入后任务数据真的落库了（表 task）。",
        "claimed_success": "已成功创建任务，任务 ID 1031，已确认写入。",
        "db_state": None,
        "expected": False,
        "category": "insufficient_evidence",
        "note": "只有自述、零工具证据 —— 这正是 LLM-as-Judge 会被骗的场景",
    },
]


class TestGoldenSampleIntegrity:
    def test_sample_count(self):
        assert len(GOLDEN_SAMPLES) == 20

    def test_all_categories_present(self):
        cats = {s["category"] for s in GOLDEN_SAMPLES}
        assert cats == {
            "real_pass",
            "real_fail",
            "trap_surface_pass",
            "trap_surface_fail",
            "trap_locate",
            "multi_table",
            "boundary",
            "field_semantics",
            "locate_ambiguity",
            "insufficient_evidence",
        }

    def test_trap_sample_count_is_at_least_half(self):
        """陷阱样本必须占足够比例 —— 否则「高准确率」没有意义。

        真实 agent 的价值全在陷阱样本上（能不能识破表面信号）。
        """
        traps = [
            s
            for s in GOLDEN_SAMPLES
            if s["category"].startswith("trap") or s["category"] in ("field_semantics", "locate_ambiguity")
        ]
        assert len(traps) >= 9
        assert len(traps) / len(GOLDEN_SAMPLES) >= 0.45

    def test_expected_is_bool(self):
        for s in GOLDEN_SAMPLES:
            assert isinstance(s["expected"], bool)

    def test_trap_samples_actually_differ_from_claimed(self):
        """陷阱样本必须**真的**与自述矛盾——否则陷阱是假的。

        这是 nlaut 历史踩过的坑（CSS override 失效导致「同图」假阳性）：
        报告「5/5 全对」前必须先独立验证金标准真伪。
        """
        for s in GOLDEN_SAMPLES:
            if s["category"] != "trap_surface_pass":
                continue
            claimed = "已成功" in s["claimed_success"]
            table = (s.get("db_state") or {}).get("task", [])
            actually_persisted = any(
                row.get("module") == "M" for row in table
            )
            assert claimed and not actually_persisted, (
                f"{s['case_id']} 陷阱失效：自述成功={claimed} 实际落库={actually_persisted}"
            )

    def test_reverse_trap_samples_actually_contradict_claim(self):
        for s in GOLDEN_SAMPLES:
            if s["category"] != "trap_surface_fail":
                continue
            claimed_fail = ("失败" in s["claimed_success"]) or ("超时" in s["claimed_success"])
            table = (s.get("db_state") or {}).get("task", [])
            actually_persisted = any(
                row.get("module") == "M" for row in table
            )
            assert claimed_fail and actually_persisted, (
                f"{s['case_id']} 反向陷阱失效：自述失败={claimed_fail} 实际落库={actually_persisted}"
            )

    def test_locate_trap_is_genuinely_tricky(self):
        """定位陷阱必须真的「表里有 M 的行，但目标 ID 不在」。

        否则该样本退化成一个普通失败样本，测不出 locate 能力的价值。
        """
        s = next(x for x in GOLDEN_SAMPLES if x["category"] == "trap_locate")
        table = (s.get("db_state") or {}).get("task", [])
        # 表非空
        assert table, "定位陷阱失效：表为空，退化成普通失败样本"
        # 表里有 module=M 的行（只看行数会误判为真）
        assert any(row.get("module") == "M" for row in table), (
            "定位陷阱失效：表里没有 M 的行，locate 能力无用武之地"
        )
        # 但目标 ID 不存在
        assert not any(str(row.get("id")) == "9999" for row in table), (
            "定位陷阱失效：目标 ID 9999 竟然存在"
        )
        # 自述成功（制造表面信号）
        assert "成功" in s["claimed_success"]
        # 期望判否
        assert s["expected"] is False

    def test_multi_table_samples_are_consistent(self):
        """多表样本：F1 数据在别的表（应判否），F2 两表都有（应判真）。"""
        f1 = next(x for x in GOLDEN_SAMPLES if x["case_id"].startswith("F1"))
        f2 = next(x for x in GOLDEN_SAMPLES if x["case_id"].startswith("F2"))
        # F1: task 表必须不存在或为空
        assert "task" not in (f1.get("db_state") or {}) or not f1["db_state"]["task"]
        assert f1["expected"] is False
        # F2: task 表必须有行
        assert (f2.get("db_state") or {}).get("task")
        assert f2["expected"] is True

    def test_field_semantics_traps_are_real(self):
        """字段语义陷阱：行存在但关键字段值不符 —— 必须真的不符。"""
        h1 = next(x for x in GOLDEN_SAMPLES if x["case_id"].startswith("H1"))
        row = h1["db_state"]["task"][0]
        assert row["module"] == "M"  # module 对
        assert row["status"] != "created"  # status 不对 → 陷阱成立
        assert h1["expected"] is False

        h2 = next(x for x in GOLDEN_SAMPLES if x["case_id"].startswith("H2"))
        row2 = h2["db_state"]["task"][0]
        assert row2["module"] != "M"  # 大小写不同 → 陷阱成立
        assert h2["expected"] is False

    def test_insufficient_evidence_samples_have_no_db_state(self):
        """证据不足样本：db_state 必须真的缺失 —— 否则就退化成普通样本。"""
        for s in GOLDEN_SAMPLES:
            if s["category"] != "insufficient_evidence":
                continue
            assert s.get("db_state") is None, f"{s['case_id']} 证据不足样本却有 db_state"
            assert s["expected"] is False, f"{s['case_id']} 证据不足样本不应判真"
            assert "成功" in s["claimed_success"], f"{s['case_id']} 缺表面信号，测不出抗骗能力"

    def test_locate_ambiguity_samples_have_both_kinds(self):
        """定位歧义：I1 目标在、M 也对；I2 目标在、但 module 不对。

        两者的差别只在目标行 —— 若两条样本真值相同则陷阱无效。
        """
        i1 = next(x for x in GOLDEN_SAMPLES if x["case_id"].startswith("I1"))
        i2 = next(x for x in GOLDEN_SAMPLES if x["case_id"].startswith("I2"))
        assert i1["expected"] is True
        assert i2["expected"] is False
        # 两条都必须「表里同时有 M 和非 M 的行」——这才是定位歧义
        for s in (i1, i2):
            mods = {r.get("module") for r in s["db_state"]["task"]}
            assert "M" in mods and "OTHER" in mods, f"{s['case_id']} 缺少 M/OTHER 混合行"


class TestGoldenSampleEvidence:
    def test_evidence_builds(self):
        for s in GOLDEN_SAMPLES:
            ev = _evidence_for(s)
            assert ev.case_id == s["case_id"]
            assert ev.llm_response

    def test_trap_samples_have_misleading_claim(self):
        """陷阱样本必须带「表面通过/失败」的信号，否则测不出能力差异。"""
        for s in GOLDEN_SAMPLES:
            if s["category"] == "trap_surface_pass":
                assert "成功" in s["claimed_success"]
            if s["category"] == "trap_surface_fail":
                assert "失败" in s["claimed_success"]


# --------------------------------------------------------------------------
# 3. 真实模型一致性测试（-m agent，默认跳过）
# --------------------------------------------------------------------------


@pytest.mark.agent
class TestLiveAgentJudge:
    """真实端点跑 Agent-as-Judge。需要：deepseek-v4-flash 的 key 已配置。

    运行：.venv/bin/python -m pytest tests/agent_judge_test.py -m agent -v
    """

    @staticmethod
    def _judge(sample: dict) -> Verdict:
        from nlaut.judge.agent_judge import build_agent_judge_from_env

        tools = [t for t in DEFAULT_TOOLS]
        impls = build_env_tool_impls(db_state=sample.get("db_state"))
        judge = build_agent_judge_from_env(
            tools=tools,
            tool_impls=impls,
            model_key="deepseek-v4-flash",
        )
        return judge.judge(
            _evidence_for(sample),
            Question(kind="noul", text=sample["objective"]),
        )

    @pytest.mark.agent
    def test_all_golden_samples_judged_correctly(self):
        """金标准全对才算通过（不做部分给分——这是门禁判定，不是评分）。"""
        results = []
        for s in GOLDEN_SAMPLES:
            v = self._judge(s)
            got = float(v.value) >= 0.5
            results.append(
                {
                    "case_id": s["case_id"],
                    "category": s["category"],
                    "expected": s["expected"],
                    "got": got,
                    "confidence": v.confidence,
                    "degraded": "degraded" in v.raw,
                }
            )
        wrong = [r for r in results if r["expected"] != r["got"]]
        degraded = [r for r in results if r["degraded"]]
        report = json.dumps(
            {"results": results, "wrong": len(wrong), "degraded": len(degraded)},
            ensure_ascii=False,
            indent=2,
        )
        print(report)
        assert not degraded, f"出现降级（端点/工具不可用），本轮结果不可信：\n{report}"
        assert not wrong, f"金标准判错 {len(wrong)} 例：\n{report}"

    @pytest.mark.agent
    def test_trap_samples_are_caught(self):
        """陷阱样本是核心价值：必须被 agent judge 识破。"""
        traps = [s for s in GOLDEN_SAMPLES if s["category"].startswith("trap")]
        wrong = []
        for s in traps:
            v = self._judge(s)
            if (float(v.value) >= 0.5) != s["expected"]:
                wrong.append(s["case_id"])
        assert not wrong, f"陷阱样本未被识破: {wrong}"
