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
]


class TestGoldenSampleIntegrity:
    def test_sample_count(self):
        assert len(GOLDEN_SAMPLES) == 9

    def test_all_categories_present(self):
        cats = {s["category"] for s in GOLDEN_SAMPLES}
        assert cats == {
            "real_pass",
            "real_fail",
            "trap_surface_pass",
            "trap_surface_fail",
            "trap_locate",
        }

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
