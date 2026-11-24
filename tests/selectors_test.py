"""选择器降级策略的测试。

v0.5.0 新增。核心：
1. `split_candidates` 正确拆分候选（不误拆 selector 内部的逗号）
2. `probe_selectors` 给出**逐候选诊断**（区分不存在/不可见/禁用）
3. `pick_selector` 按顺序降级
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nlaut.executor.selectors import (
    SelectorNotFound,
    pick_selector,
    probe_selectors,
    split_candidates,
)


class FakeLocator:
    """最小化的 Playwright locator 替身。"""

    def __init__(self, sel, page):
        self.sel = sel
        self.page = page

    def count(self):
        if self.sel in self.page.bad_selectors:
            raise ValueError(f"invalid selector: {self.sel}")
        return self.page.counts.get(self.sel, 0)

    @property
    def first(self):
        return self

    def is_visible(self):
        return self.page.visible.get(self.sel, True)

    def is_enabled(self):
        return self.page.enabled.get(self.sel, True)


class FakePage:
    def __init__(self, counts=None, visible=None, enabled=None, bad_selectors=None):
        self.counts = counts or {}
        self.visible = visible or {}
        self.enabled = enabled or {}
        self.bad_selectors = bad_selectors or []

    def locator(self, sel):
        return FakeLocator(sel, self)


class TestSplitCandidates:
    def test_single(self):
        assert split_candidates(".foo") == [".foo"]

    def test_multiple(self):
        assert split_candidates(".a, .b, .c") == [".a", ".b", ".c"]

    def test_no_space(self):
        assert split_candidates(".a,.b") == [".a", ".b"]

    def test_comma_inside_has_text(self):
        """`:has-text("a,b")` 里的逗号不能被拆开。"""
        got = split_candidates('.a:has-text("x,y"), .b')
        assert got == ['.a:has-text("x,y")', ".b"]

    def test_comma_inside_parens(self):
        got = split_candidates("div:not(.a, .b), .c")
        assert got == ["div:not(.a, .b)", ".c"]

    def test_quoted_comma(self):
        got = split_candidates("button[title='a,b'], .c")
        assert got == ["button[title='a,b']", ".c"]

    def test_empty_filtered(self):
        assert split_candidates("  ,  , .a  ,") == [".a"]


class TestProbeSelectors:
    def test_picks_first_usable(self):
        page = FakePage(counts={".a": 0, ".b": 2})
        r = probe_selectors(page, ".a, .b")
        assert r.ok
        assert r.chosen.selector == ".b"

    def test_all_missing(self):
        page = FakePage(counts={})
        r = probe_selectors(page, ".a, .b")
        assert not r.ok
        assert r.chosen is None
        assert all("不存在" in p.reason for p in r.probes)

    def test_invisible_reported(self):
        page = FakePage(counts={".a": 1}, visible={".a": False})
        r = probe_selectors(page, ".a")
        assert not r.ok
        assert "不可见" in r.probes[0].reason

    def test_disabled_reported(self):
        page = FakePage(counts={".a": 1}, enabled={".a": False})
        r = probe_selectors(page, ".a")
        assert not r.ok
        assert "禁用" in r.probes[0].reason

    def test_invalid_selector_reported(self):
        page = FakePage(bad_selectors={".bad"})
        r = probe_selectors(page, ".bad")
        assert not r.ok
        assert "无效" in r.probes[0].reason

    def test_render_shows_each_candidate(self):
        page = FakePage(counts={".a": 0, ".b": 1})
        out = probe_selectors(page, ".a, .b").render()
        assert ".a" in out
        assert ".b" in out
        assert "不存在" in out
        assert "命中" in out

    def test_render_when_all_fail(self):
        out = probe_selectors(FakePage(), ".a, .b").render()
        assert "全部候选均不可用" in out


class TestPickSelector:
    def test_returns_first_usable(self):
        page = FakePage(counts={".a": 0, ".b": 1})
        assert pick_selector(page, [".a", ".b"]) == ".b"

    def test_returns_none_when_all_fail(self):
        assert pick_selector(FakePage(), [".a", ".b"]) is None

    def test_accepts_string(self):
        page = FakePage(counts={".a": 0, ".b": 1})
        assert pick_selector(page, ".a, .b") == ".b"


class TestSelectorNotFound:
    def test_is_runtime_error(self):
        """必须是 RuntimeError —— 通道的 optional 兜底逻辑依赖异常类型。"""
        assert issubclass(SelectorNotFound, RuntimeError)

    def test_message_carries_diagnosis(self):
        err = SelectorNotFound("click 步骤1 的所有候选选择器均不可用:\n  选择器诊断")
        assert "诊断" in str(err)
