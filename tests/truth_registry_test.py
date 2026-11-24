"""真值源注册表 + LLM-as-Judge 接线的测试。

v0.5.0 新增。这两个模块原先都是死代码：
- `config/truth_source_registry.yaml` 声明了真值源但没人读
- `llm_judge.py` 实现了 LLMJudge 但没注册进引擎表，生产路径走不到

本测试锁定接线后的行为，防止再次退化成死代码。
"""
import json

import pytest

from nlaut.executor.runner import _engine_name
from nlaut.ir.model import TestCaseIR
from nlaut.judge import get_judge
from nlaut.judge.llm_judge import LLMJudge
from nlaut.judge.truth_registry import (
    RegistryEntry,
    TruthRegistry,
    collect_db_state,
    load_registry,
    registry_enabled,
)

# ---------- 真值源注册表 ----------


class TestLoadRegistry:
    def test_missing_file_returns_empty(self, tmp_path):
        reg = load_registry(tmp_path / "nope.yaml")
        assert reg.entries == {}
        assert reg.source_path.endswith("nope.yaml")

    def test_loads_entries(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text(
            "a:\n  type: file\n  path: /tmp/a.txt\n  description: 甲\n"
            "b:\n  type: http\n  base_url: http://x\n  path_map: {k: /api/k}\n",
            encoding="utf-8",
        )
        reg = load_registry(p)
        assert set(reg.entries) == {"a", "b"}
        assert reg.get("a").type == "file"
        assert reg.get("a").description == "甲"
        assert reg.get("b").path_map == {"k": "/api/k"}

    def test_unknown_fields_go_to_extras(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text("a:\n  type: file\n  path: /tmp/a\n  custom: 42\n", encoding="utf-8")
        reg = load_registry(p)
        assert reg.get("a").extras == {"custom": 42}

    def test_non_dict_entry_skipped(self, tmp_path):
        p = tmp_path / "r.yaml"
        p.write_text("a:\n  type: file\n  path: /tmp/a\nbad: just-a-string\n", encoding="utf-8")
        reg = load_registry(p)
        assert "bad" not in reg.entries


class TestCollectDbState:
    def test_collects_file_source(self, tmp_path):
        target = tmp_path / "data.json"
        target.write_text(json.dumps({"x": 1}), encoding="utf-8")
        p = tmp_path / "r.yaml"
        p.write_text(f"k:\n  type: file\n  path: {target}\n", encoding="utf-8")
        reg = load_registry(p)
        db = collect_db_state(reg)
        assert "k" in db
        assert db["k"]["error"] is None
        assert "x" in str(db["k"]["value"])

    def test_missing_file_records_error_not_crash(self, tmp_path):
        """取证失败不中断 —— 记录带 error，让 judge 把「取不到」当一种证据。"""
        p = tmp_path / "r.yaml"
        p.write_text("k:\n  type: file\n  path: /nope/nonexistent.txt\n", encoding="utf-8")
        reg = load_registry(p)
        db = collect_db_state(reg)
        assert db["k"]["error"] is not None
        assert "FileNotFoundError" in db["k"]["error"]

    def test_tilde_expanded(self, tmp_path, monkeypatch):
        """`~` 必须展开 —— 否则 FileNotFoundError（实测踩坑）。"""
        home = tmp_path / "home"
        home.mkdir()
        (home / "f.txt").write_text("hello", encoding="utf-8")
        monkeypatch.setenv("HOME", str(home))
        p = tmp_path / "r.yaml"
        p.write_text("k:\n  type: file\n  path: ~/f.txt\n", encoding="utf-8")
        reg = load_registry(p)
        db = collect_db_state(reg)
        assert db["k"]["error"] is None
        assert "hello" in str(db["k"]["value"])

    def test_empty_registry_returns_empty(self):
        assert collect_db_state(TruthRegistry()) == {}

    def test_large_value_truncated(self, tmp_path):
        big = tmp_path / "big.txt"
        big.write_text("x" * 50000, encoding="utf-8")
        p = tmp_path / "r.yaml"
        p.write_text(f"k:\n  type: file\n  path: {big}\n", encoding="utf-8")
        reg = load_registry(p)
        db = collect_db_state(reg, max_bytes=1000)
        val = str(db["k"]["value"])
        assert len(val) < 3000
        assert "截断" in val

    def test_sql_source_needs_executor(self):
        """sql 型没 executor 时应跳过而不是抛异常 —— 注册表不该让用例跑不起来。"""
        reg = TruthRegistry(entries={"k": RegistryEntry(key="k", type="sql", path="SELECT 1")})
        out = reg.build_sources()
        assert "k" not in out

    def test_sql_source_with_executor(self):
        reg = TruthRegistry(entries={"k": RegistryEntry(key="k", type="sql", path="SELECT 1")})
        out = reg.build_sources({"k": lambda sql: [{"n": 1}]})
        assert "k" in out
        ev = out["k"].snapshot("k")
        assert ev.ok
        assert ev.value == [{"n": 1}]


class TestRegistryEnabled:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("NLaut_TRUTH_REGISTRY", raising=False)
        assert registry_enabled() is True

    @pytest.mark.parametrize("v", ["0", "off", "false", "no", "OFF"])
    def test_can_be_disabled(self, monkeypatch, v):
        monkeypatch.setenv("NLaut_TRUTH_REGISTRY", v)
        assert registry_enabled() is False


# ---------- LLM-as-Judge 接线 ----------


class TestLlmJudgeWiring:
    def test_registered_in_engine_table(self):
        """llm 引擎必须可从 get_judge 拿到 —— 否则又是孤儿模块。"""
        j = get_judge("llm")
        assert isinstance(j, LLMJudge)

    def test_llm_verify_routes_to_llm_engine(self):
        raw = {
            "id": "tc_llm_001",
            "title": "x",
            "source": "unit-test",
            "quad": {"actor": "t", "action": "a", "object": "o"},
            "channel": "api",
            "steps": [{"action": "api_call", "prompt": "x"}],
            "assertions": [{"kind": "llm_verify", "question": "q"}],
        }
        ir = TestCaseIR.model_validate(raw)
        assert _engine_name(ir.assertions[0]) == "llm"

    def test_llm_verify_assertion_parses(self):
        raw = {
            "id": "tc_llm_002",
            "title": "x",
            "source": "unit-test",
            "quad": {"actor": "t", "action": "a", "object": "o"},
            "channel": "api",
            "steps": [{"action": "api_call", "prompt": "x"}],
            "assertions": [{"kind": "llm_verify", "question": "系统自述成功，实际有数据吗？"}],
        }
        ir = TestCaseIR.model_validate(raw)
        a = ir.assertions[0]
        assert a.kind == "llm_verify"
        assert a.threshold == 0.9
        assert a.judge == "llm"


class TestLlmJudgeBehaviour:
    def _judge(self, **kw):
        return LLMJudge(base_url="http://127.0.0.1:1", api_key="k", timeout=1.0, **kw)

    def test_confidence_capped(self):
        """LLM 自报 High 也要封顶 —— 否则会自动裁决。"""
        j = self._judge()
        assert j.CONFIDENCE_CAP == 0.85
        assert j._parse_confidence("置信度: High") == 0.85
        assert j._parse_confidence("置信度: Medium") == 0.6

    def test_endpoint_failure_degrades_not_fails(self):
        """端点不可达 → value=0.5（不确定）+ degraded，不能是 0.0。"""
        from nlaut.judge import Evidence, Question

        j = self._judge()
        v = j.judge(Evidence(case_id="t"), Question(kind="noul", text="q"))
        assert v.value == 0.5
        assert v.confidence == 0.0
        assert v.raw.get("degraded") is True
        # 关键：不能被 route 判成 auto_pass
        from nlaut.judge.arbiter import route

        assert route(v).status == "human_review"

    def test_parse_judgement_variants(self):
        j = self._judge()
        assert j._parse_judgement("judgement: true\n理由: 有数据") is True
        assert j._parse_judgement("judgement: false\n理由: 无数据") is False
        assert j._parse_judgement("判定：不通过") is False
        assert j._parse_judgement("") is False
