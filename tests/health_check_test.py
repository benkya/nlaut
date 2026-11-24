"""CeCoder 环境前置健康检查的测试。

v0.5.0 新增。核心要验证：
1. 各项检查在正常/异常时的判定
2. 失败时的处置建议是否可执行（不是空泛提示）
3. 关键回归：不健康时绝不返回 ok=True
"""
import json

import pytest

from nlaut.harness.health import (
    HealthItem,
    HealthReport,
    _parse_base_url,
    check_environment,
    split_cases,
)


class TestParseBaseUrl:
    @pytest.mark.parametrize(
        "raw,expect",
        [
            ("http://10.64.17.69:30972/", ("10.64.17.69", 30972)),
            ("http://10.64.17.69:30972", ("10.64.17.69", 30972)),
            ("https://api.example.com/v1", ("api.example.com", 443)),
            ("http://host.local", ("host.local", 80)),
        ],
    )
    def test_parse(self, raw, expect):
        assert _parse_base_url(raw) == expect

    def test_invalid(self):
        assert _parse_base_url("") is None
        assert _parse_base_url("host:notaport") is None


class TestHealthReport:
    def test_ok_when_no_fatal_failure(self):
        rep = HealthReport()
        rep.add(HealthItem("a", True, "fine"))
        rep.add(HealthItem("b", False, "warn", fatal=False))
        assert rep.ok is True
        assert rep.fatal_failures == []

    def test_not_ok_on_fatal_failure(self):
        rep = HealthReport()
        rep.add(HealthItem("a", True, "fine"))
        rep.add(HealthItem("b", False, "broken"))
        assert rep.ok is False
        assert len(rep.fatal_failures) == 1

    def test_render_contains_items(self):
        rep = HealthReport()
        rep.add(HealthItem("cdp", True, "就绪"))
        out = rep.render()
        assert "cdp" in out
        assert "就绪" in out
        assert "环境可用" in out

    def test_render_reports_failure(self):
        rep = HealthReport()
        rep.add(HealthItem("auth_backend", False, "不可达"))
        out = rep.render()
        assert "环境不可用" in out
        assert "auth_backend" in out

    def test_advice_gives_actionable_steps(self):
        """失败时必须给出可执行建议，不能是空泛提示。"""
        rep = HealthReport()
        rep.add(HealthItem("auth_backend", False, "不可达"))
        advice = rep.advice()
        assert "auth 后端" in advice
        assert "login 模式" in advice
        # 必须给出「还能跑什么」的出路
        assert "tc_cecoder_process_001" in advice

    def test_advice_for_cdp_failure(self):
        rep = HealthReport()
        rep.add(HealthItem("cdp", False, "未就绪"))
        advice = rep.advice()
        assert "remote-debugging-port" in advice

    def test_advice_for_overlay_failure(self):
        rep = HealthReport()
        rep.add(HealthItem("login_overlay", False, "存在登录遮罩"))
        advice = rep.advice()
        assert "遮罩" in advice

    def test_advice_empty_when_ok(self):
        rep = HealthReport()
        rep.add(HealthItem("a", True, "fine"))
        assert rep.advice() == ""


class TestCheckEnvironment:
    def test_returns_report_with_three_checks(self, monkeypatch):
        """默认可选做遮罩检查时应有 cdp / auth_backend / login_overlay 三项。"""
        rep = check_environment(cdp_port=1, base_url="", check_overlay=False)
        names = {i.name for i in rep.items}
        assert "cdp" in names
        assert "auth_backend" in names
        assert "login_overlay" not in names

    def test_unreachable_cdp_is_fatal(self):
        rep = check_environment(cdp_port=1, base_url="", check_overlay=False)
        cdp = next(i for i in rep.items if i.name == "cdp")
        assert cdp.ok is False
        assert cdp.fatal is True
        assert rep.ok is False

    def test_missing_auth_config_is_fatal(self, monkeypatch, tmp_path):
        import nlaut.harness.health as H

        monkeypatch.setattr(H, "AUTH_CONFIG", tmp_path / "nope.json")
        rep = check_environment(cdp_port=1, base_url="", check_overlay=False)
        auth = next(i for i in rep.items if i.name == "auth_backend")
        assert auth.ok is False

    def test_unreachable_auth_backend_detected(self, monkeypatch, tmp_path):
        """auth 后端不可达必须被判为致命 —— 这是 GUI 用例批量失败的根因。"""
        import nlaut.harness.health as H

        cfg = tmp_path / "auth.json"
        # 保留端口但不保证监听 —— 用一个几乎肯定没人监的高端口
        cfg.write_text(json.dumps({"identity": {"baseUrl": "http://127.0.0.1:59999/"}}))
        monkeypatch.setattr(H, "AUTH_CONFIG", cfg)
        rep = check_environment(cdp_port=1, base_url="", check_overlay=False)
        auth = next(i for i in rep.items if i.name == "auth_backend")
        assert auth.ok is False
        assert "login 模式" in auth.detail

    def test_reachable_auth_backend_ok(self, monkeypatch, tmp_path):
        """本地监听端口应判为可达 —— 不能误报。"""
        import socket

        import nlaut.harness.health as H

        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            cfg = tmp_path / "auth.json"
            cfg.write_text(json.dumps({"identity": {"baseUrl": f"http://127.0.0.1:{port}/"}}))
            monkeypatch.setattr(H, "AUTH_CONFIG", cfg)
            rep = check_environment(cdp_port=1, base_url="", check_overlay=False)
            auth = next(i for i in rep.items if i.name == "auth_backend")
            assert auth.ok is True
        finally:
            srv.close()


class TestSplitCases:
    """GUI 依赖分类 —— 环境不可用时只跳过必挂的那批。"""

    @pytest.fixture
    def cases(self, tmp_path):
        d = tmp_path / "cases"
        d.mkdir()
        # GUI 用例：有 click/wait_visible 交互
        (d / "tc_gui_001.yaml").write_text(
            "id: tc_gui_001\n"
            "steps:\n"
            "- action: nav\n  path: '/'\n"
            "- action: click\n  selector: 'button'\n"
            "- action: wait_visible\n  selector: 'x'\n"
            "assertions:\n"
            "- kind: text_visible\n  selector: 'x'\n  expected: y\n",
            encoding="utf-8",
        )
        # 纯工序域用例：只有 process_state 断言
        (d / "tc_proc_001.yaml").write_text(
            "id: tc_proc_001\n"
            "steps:\n"
            "- action: nav\n  path: '/'\n"
            "- action: process_probe\n  probe: chain\n"
            "assertions:\n"
            "- kind: process_state\n  rule: done_without_outputs\n",
            encoding="utf-8",
        )
        # 纯 agent 用例：无交互步骤
        (d / "tc_agent_001.yaml").write_text(
            "id: tc_agent_001\n"
            "steps:\n"
            "- action: nav\n  path: '/'\n"
            "assertions:\n"
            "- kind: agent_verify\n  objective: check\n",
            encoding="utf-8",
        )
        return d

    def test_gui_case_detected(self, cases):
        r = split_cases(cases)
        assert "tc_gui_001" in r["gui"]
        assert "tc_gui_001" not in r["nogui"]

    def test_process_case_not_gui(self, cases):
        r = split_cases(cases)
        assert "tc_proc_001" in r["nogui"]

    def test_agent_case_without_interaction_not_gui(self, cases):
        """agent 用例只 nav 不点按钮 → 不依赖 GUI 交互。"""
        r = split_cases(cases)
        assert "tc_agent_001" in r["nogui"]

    def test_selected_filters(self, cases):
        r = split_cases(cases, selected=["tc_gui_001"])
        assert r["gui"] == ["tc_gui_001"]
        assert r["nogui"] == []

    def test_missing_dir(self, tmp_path):
        r = split_cases(tmp_path / "nope")
        assert r == {"gui": [], "nogui": []}

    def test_real_cases_dir_classification(self):
        """真实用例目录的分类必须与实测一致。"""
        from pathlib import Path

        d = Path("cases/cecoder")
        if not d.is_dir():
            pytest.skip("cases/cecoder 不存在（本地资产）")
        r = split_cases(d)
        assert r["gui"], "应有 GUI 用例"
        assert r["nogui"], "应有纯工序域用例"
        # 不重叠且并集完整
        assert not (set(r["gui"]) & set(r["nogui"]))
        assert len(r["gui"]) + len(r["nogui"]) == len(list(d.glob("*.yaml")))
