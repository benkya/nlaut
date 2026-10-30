"""真实模块接入层（TruthSource）元测试。

核心不变量：**取证只读、失败也是一种证据、来源可审计**。
"""

from __future__ import annotations

import json

from nlaut.judge.truth_source import (
    CompositeTruthSource,
    DictTruthSource,
    HttpTruthSource,
    LogTruthSource,
    SqlTruthSource,
    TruthEvidence,
    TruthSource,
)


class TestTruthEvidence:
    def test_ok_reflects_error_absence(self):
        assert TruthEvidence(key="k", value=1, source="s").ok is True
        assert TruthEvidence(key="k", value=None, source="s", error="e").ok is False

    def test_to_dict_is_auditable(self):
        """证据必须带来源与时间——人工仲裁时要能判断可信度。"""
        d = TruthEvidence(key="task", value=[1], source="db://prod/task").to_dict()
        assert d["key"] == "task"
        assert d["source"] == "db://prod/task"
        assert d["collected_at"] > 0
        assert d["error"] is None


class TestDictTruthSource:
    def test_snapshot_existing_key(self):
        ts = DictTruthSource({"task": [{"id": 1}]})
        ev = ts.snapshot("task")
        assert ev.ok
        assert ev.value == [{"id": 1}]
        assert "task" in ev.source

    def test_missing_key_returns_error_not_exception(self):
        """取不到证据是「一种证据」，不抛异常——让 judge 能处理。"""
        ts = DictTruthSource({"task": []})
        ev = ts.snapshot("nope")
        assert not ev.ok
        assert "key_not_found" in ev.error
        assert "task" in ev.error  # 错误里带上可用 key，便于模型自纠

    def test_as_db_state_compatible(self):
        ts = DictTruthSource({"task": [{"id": 1}]})
        assert ts.as_db_state() == {"task": [{"id": 1}]}

    def test_satisfies_protocol(self):
        assert isinstance(DictTruthSource({}), TruthSource)


class TestSqlTruthSource:
    def test_select_is_executed(self):
        ts = SqlTruthSource({"task": "SELECT * FROM task"}, lambda q: [{"id": 1}])
        ev = ts.snapshot("task")
        assert ev.ok
        assert ev.value == [{"id": 1}]
        assert ev.source.startswith("db://")

    def test_non_select_is_rejected(self):
        """只读约束：拒绝执行非 SELECT 语句。"""
        executed = []

        def boom(sql):
            executed.append(sql)
            return "BOOM"

        ts = SqlTruthSource({"task": "DELETE FROM task"}, boom)
        ev = ts.snapshot("task")
        assert not ev.ok
        assert "not_a_select_statement" in ev.error
        assert executed == []  # 根本没执行

    def test_write_verbs_all_rejected(self):
        for sql in ("UPDATE t SET a=1", "INSERT INTO t VALUES(1)", "DROP TABLE t"):
            ts = SqlTruthSource({"k": sql}, lambda q: "X")
            ev = ts.snapshot("k")
            assert not ev.ok, f"{sql} 竟然被执行"
            assert "not_a_select_statement" in ev.error

    def test_executor_exception_returns_error(self):
        def boom(sql):
            raise RuntimeError("db down")

        ts = SqlTruthSource({"task": "SELECT 1"}, boom)
        ev = ts.snapshot("task")
        assert not ev.ok
        assert "RuntimeError" in ev.error

    def test_missing_query_configured(self):
        ts = SqlTruthSource({}, lambda q: None)
        ev = ts.snapshot("task")
        assert not ev.ok
        assert "no_query_configured" in ev.error


class TestHttpTruthSource:
    def test_missing_path_configured(self):
        ts = HttpTruthSource("http://x", {})
        ev = ts.snapshot("task")
        assert not ev.ok
        assert "no_path_configured" in ev.error

    def test_path_key_placeholder(self):
        ts = HttpTruthSource("http://x", {"/api/{key}": "/api/{key}"})
        assert ts.path_map["/api/{key}"] == "/api/{key}"

    def test_connection_error_returns_error(self, monkeypatch):
        """网络失败是「一种证据」，不抛异常。"""
        import httpx

        def boom(*a, **kw):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr("httpx.get", boom)
        ts = HttpTruthSource("http://x", {"task": "/task"})
        ev = ts.snapshot("task")
        assert not ev.ok
        assert "ConnectError" in ev.error

    def test_non_200_returns_error(self, monkeypatch):
        class FakeResp:
            status_code = 503
            text = "unavailable"

            def json(self):
                raise ValueError

        monkeypatch.setattr("httpx.get", lambda *a, **kw: FakeResp())
        ts = HttpTruthSource("http://x", {"task": "/task"})
        ev = ts.snapshot("task")
        assert not ev.ok
        assert "HTTP 503" in ev.error

    def test_json_body_parsed(self, monkeypatch):
        class FakeResp:
            status_code = 200

            def json(self):
                return {"rows": [{"id": 1}]}

            @property
            def text(self):
                return json.dumps({"rows": [{"id": 1}]})

        monkeypatch.setattr("httpx.get", lambda *a, **kw: FakeResp())
        ts = HttpTruthSource("http://x", {"task": "/task"})
        ev = ts.snapshot("task")
        assert ev.ok
        assert ev.value == {"rows": [{"id": 1}]}


class TestLogTruthSource:
    def test_tail_extracted(self, tmp_path):
        f = tmp_path / "app.log"
        f.write_text("\n".join(f"line{i}" for i in range(500)), encoding="utf-8")
        ts = LogTruthSource({"app": str(f)}, tail_lines=10)
        ev = ts.snapshot("app")
        assert ev.ok
        assert "line499" in ev.value
        assert "line489" not in ev.value  # 只取尾部

    def test_custom_parser_used(self, tmp_path):
        f = tmp_path / "app.log"
        f.write_text("ERROR: boom\nERROR: boom2\n", encoding="utf-8")
        ts = LogTruthSource({"app": str(f)}, parser=lambda t: t.count("ERROR"))
        ev = ts.snapshot("app")
        assert ev.value == 2

    def test_parser_exception_returns_error(self, tmp_path):
        f = tmp_path / "app.log"
        f.write_text("x", encoding="utf-8")

        def boom(t):
            raise ValueError("bad parse")

        ts = LogTruthSource({"app": str(f)}, parser=boom)
        ev = ts.snapshot("app")
        assert not ev.ok
        assert "parser_failed" in ev.error

    def test_missing_file_returns_error(self):
        ts = LogTruthSource({"app": "/nonexistent/x.log"})
        ev = ts.snapshot("app")
        assert not ev.ok
        assert "FileNotFoundError" in ev.error or "OSError" in ev.error


class TestCompositeTruthSource:
    def test_falls_back_to_second_source(self):
        ts = CompositeTruthSource(
            {
                "db": DictTruthSource({"task": [{"id": 1}]}),
                "http": HttpTruthSource("http://x", {}),
            }
        )
        ev = ts.snapshot("task")
        assert ev.ok
        assert ev.value == [{"id": 1}]

    def test_all_fail_aggregates_errors(self):
        ts = CompositeTruthSource(
            {
                "db": DictTruthSource({}),
                "http": HttpTruthSource("http://x", {}),
            }
        )
        ev = ts.snapshot("task")
        assert not ev.ok
        assert "all_sources_failed" in ev.error
        assert "db:" in ev.error
        assert "http:" in ev.error

    def test_snapshot_all(self):
        ts = CompositeTruthSource({"db": DictTruthSource({"a": [1], "b": [2]})})
        out = ts.snapshot_all(["a", "b"])
        assert set(out) == {"a", "b"}
        assert out["a"].ok and out["b"].ok


class TestTruthSourceIsReadOnly:
    """所有真值源都不允许写被测系统。"""

    def test_no_write_methods_exposed(self):
        for src in (
            DictTruthSource({}),
            HttpTruthSource("http://x", {}),
            SqlTruthSource({}, lambda q: None),
            LogTruthSource({}),
        ):
            for attr in ("write", "update", "delete", "insert", "execute_write", "commit"):
                assert not hasattr(src, attr), f"{type(src).__name__} 暴露了写操作 {attr}"

    def test_sql_source_only_accepts_select(self):
        """SQL 源唯一的执行入口对非 SELECT 直接拒绝（已在上文验证）。"""
        ts = SqlTruthSource({"k": "TRUNCATE t"}, lambda q: "X")
        assert not ts.snapshot("k").ok
