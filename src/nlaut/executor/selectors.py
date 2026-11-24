"""选择器降级策略：让 GUI 用例在 UI 改版时不至于全线崩溃。

**问题**（2026-11-15 实测）：CeCoder 批量跑 GUI 用例时 5 条全挂，
报错是 `TimeoutError` + `pointer events intercepted`。排查花了三层
（选择器 → DOM → overlay → TCP → 应用日志）才定位到真因是**登录遮罩**，
但过程中**误改过选择器**（把 `aria-label="创建任务"` 改成
`:has-text("新建任务")`），而真因与选择器无关。

**这次误改暴露的真问题**：选择器是**单点脆弱**的 ——
`button[aria-label="创建任务"]` 一旦 app 改文案/i18n 就全线失效，
且失败信息（TimeoutError）完全看不出是「选择器过时」还是「环境问题」。

**解法**：不追求「找一个永不过时的选择器」（不存在），而是
**让选择器可降级 + 让失败可诊断**：

1. **多候选选择器**：一个逻辑目标给多个候选（语义 → class → 文本），
   逐个尝试，第一个命中的就用。
2. **失败可诊断**：全都不命中时，报错信息列出**每个候选各自的失败原因**
   （不存在 / 不可见 / 被遮挡），而不是笼统的 TimeoutError。

**为什么不能只换成 class 名**：`egw-*` class 同样会随改版变，
而且 `.egw-card-more` 这类 class 语义更弱（不知道它是「更多操作」按钮）。
所以是**分层降级**而不是**替换**。

用法（IR 里写候选列表）：
```yaml
- action: click
  selector: 'button[aria-label="更多操作"], .egw-card-more, button:has-text("更多操作")'
```
配合 `nlaut` 通道的自动降级：`selector` 支持逗号分隔的候选列表，
按顺序尝试；全部失败时报出逐候选诊断。

或在 Python 侧用工具函数：
```python
from nlaut.executor.selectors import pick_selector
pick_selector(page, ['button[aria-label="更多操作"]', '.egw-card-more'])
```
"""
from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "SelectorNotFound",
    "SelectorProbe",
    "pick_selector",
    "probe_selectors",
    "split_candidates",
]


class SelectorNotFound(RuntimeError):
    """所有候选选择器都不可用。

    与 Playwright 原生 TimeoutError 区分开：这个异常**带逐候选诊断**，
    让人一眼看出是「元素不存在」「不可见」「被禁用」还是「选择器语法错」。

    2026-11-15 实测：GUI 用例批量失败时只报 TimeoutError，
    完全看不出是选择器过时还是环境问题 —— 这个异常就是补这个洞。
    """


@dataclass
class SelectorProbe:
    """单个候选选择器的探测结果。"""

    selector: str
    found: bool
    visible: bool
    enabled: bool
    count: int
    reason: str = ""

    @property
    def usable(self) -> bool:
        return self.found and self.visible and self.enabled

    def line(self) -> str:
        if self.usable:
            return f"    ✅ {self.selector} → 命中（count={self.count}）"
        return f"    ❌ {self.selector} → {self.reason}"


@dataclass
class SelectorProbeResult:
    """多候选探测的汇总。"""

    probes: list[SelectorProbe] = field(default_factory=list)

    @property
    def chosen(self) -> SelectorProbe | None:
        for p in self.probes:
            if p.usable:
                return p
        return None

    @property
    def ok(self) -> bool:
        return self.chosen is not None

    def render(self) -> str:
        """逐候选诊断 —— 失败时让人一眼看出是「不存在」还是「被遮挡」。"""
        lines = [f"  选择器诊断（{len(self.probes)} 个候选）:"]
        lines += [p.line() for p in self.probes]
        if not self.ok:
            lines.append("    → 全部候选均不可用")
        return "\n".join(lines)


def split_candidates(selector: str) -> list[str]:
    """把逗号分隔的候选列表拆开（不处理 selector 内部的逗号，如 :has-text）。"""
    out: list[str] = []
    depth = 0
    buf: list[str] = []
    quote = ""
    for ch in selector:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


def probe_selectors(page, selector: str) -> SelectorProbeResult:
    """逐个探测候选选择器，返回可诊断的结果。"""
    result = SelectorProbeResult()
    for cand in split_candidates(selector):
        try:
            loc = page.locator(cand)
            count = loc.count()
        except Exception as e:  # noqa: BLE001 — 选择器语法错误也算探测结果
            result.probes.append(
                SelectorProbe(cand, False, False, False, 0, f"选择器无效（{type(e).__name__}）")
            )
            continue
        if count == 0:
            result.probes.append(SelectorProbe(cand, False, False, False, 0, "元素不存在"))
            continue
        try:
            first = loc.first
            visible = first.is_visible()
            enabled = first.is_enabled()
        except Exception as e:  # noqa: BLE001
            result.probes.append(
                SelectorProbe(cand, True, False, False, count, f"属性读取失败（{type(e).__name__}）")
            )
            continue
        if not visible:
            result.probes.append(SelectorProbe(cand, True, False, enabled, count, "存在但不可见"))
            continue
        if not enabled:
            result.probes.append(SelectorProbe(cand, True, True, False, count, "可见但被禁用"))
            continue
        result.probes.append(SelectorProbe(cand, True, True, True, count))
    return result


def pick_selector(page, candidates: str | list[str]) -> str | None:
    """选出第一个可用的候选选择器。都没有则返回 None。

    用法：
    ```python
    sel = pick_selector(page, ['button[aria-label="更多操作"]', '.egw-card-more'])
    if sel is None:
        raise RuntimeError(probe_selectors(page, ','.join(cands)).render())
    page.click(sel)
    ```
    """
    if isinstance(candidates, str):
        candidates = split_candidates(candidates)
    result = probe_selectors(page, ", ".join(candidates))
    chosen = result.chosen
    return chosen.selector if chosen else None
