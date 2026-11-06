"""L4 Web 通道：IR steps → Playwright 动作序列（用系统 Chrome，零浏览器下载）。

步骤翻译：nav→goto / fill→fill($var 解析) / click→click / wait_visible→wait_for。
执行后采集 Evidence：截图 + 断言 selector 的可见文本（dom_state）。
禁止生成硬编码 sleep——wait_visible 自带 timeout_ms。

ruff: noqa: BLE001 —— DOM 采集与等待的宽泛 except 是刻意兜底：
元素缺失/超时降级为空证据，交由判定层裁决，不允许单点异常中断整套执行。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ...ir.model import TestCaseIR

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def resolve_vars(value: str, data: dict[str, str]) -> str:
    """把 $var 替换为数据集字段；未知变量报错（禁止静默保留）。"""
    import re

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in data:
            raise KeyError(f"数据集缺少字段 {key!r}（data_ref 提供: {sorted(data)}）")
        return data[key]

    return re.sub(r"\$([a-zA-Z_][a-zA-Z0-9_]*)", _sub, value)


def _build_dom_state(page, ir: TestCaseIR) -> dict[str, str]:
    """采集断言涉及的 selector 可见文本 / DOM 属性（1° 确定性判定的输入）。

    对 text_visible 断言:采集元素 inner_text
    对 attribute 断言:采集 element.disabled / value / getAttribute(name)
    """
    from ...ir.model import AssertAttribute

    dom: dict[str, str] = {}
    for a in ir.assertions:
        if isinstance(a, AssertAttribute):
            # 用 attribute:NAME 作为 key 避免和文本断言混淆
            key = f"{a.selector}|attr:{a.name}"
            if key in dom:
                continue
            try:
                handle = page.query_selector(a.selector)
                if handle is None:
                    print(f"[web] attribute: selector NOT FOUND: {a.selector}")
                    dom[key] = "__MISSING__"
                    continue
                if a.name == "disabled":
                    val = handle.evaluate("el => el.disabled")
                elif a.name == "value":
                    val = handle.evaluate("el => el.value")
                elif a.name == "exists":
                    # 元素存在 → "__PRESENT__"（用 None 代替 "__MISSING__" 做存在性判定）
                    val = "__PRESENT__"
                elif a.name.startswith("data:"):
                    val = handle.get_attribute("data-" + a.name[len("data:"):])
                else:
                    val = handle.get_attribute(a.name)
                # 区分「未设置 (None)」和「空字符串」
                # React 经常在 toggle 时设 data-x="" 而不是移除属性，
                # 这两种情况对断言语义都等价为「属性缺席 / 占位」
                if val is None or val == "":
                    val = "__ABSENT__"
                dom[key] = str(val)
                print(f"[web] attribute: {a.selector}.{a.name} = {dom[key]!r}")
            except Exception as e:  # noqa: BLE001
                dom[key] = ""
                print(f"[web] 采集 {a.selector}.{a.name} 失败: {type(e).__name__}")
        else:
            sel = getattr(a, "selector", None)
            if sel and sel not in dom:
                try:
                    dom[sel] = page.inner_text(sel)
                except Exception as e:  # noqa: BLE001 — 元素不存在→空文本交判定层
                    dom[sel] = ""
                    print(f"[web] 采集 {sel} 失败: {type(e).__name__}")
    return dom


def execute(
    ir: TestCaseIR,
    data: dict[str, str] | None = None,
    base_url: str = "",
    screenshot_dir: str | Path = "artifacts/screenshots",
    headless: bool = True,
    logger: Callable[[str], None] = print,
) -> dict:
    """执行一条 IR 用例，返回 Evidence 字段（截图路径 + dom_state）。

    返回 dict 直接喂 nlaut.judge.Evidence(case_id=ir.id, **result)。
    """
    from playwright.sync_api import sync_playwright

    data = data or {}
    shot_dir = Path(screenshot_dir)
    shot_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, executable_path=CHROME)
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            for idx, step in enumerate(ir.steps, 1):
                try:
                    _run_step(page, step, ir, idx, data, base_url, logger)
                except Exception as step_err:
                    # optional 步骤失败 → 降级跳过；非 optional → 原样抛出
                    if getattr(step, "optional", False):
                        logger(f"[{ir.id}] 步骤{idx} {step.action} "
                               f"{getattr(step, 'selector', '')} "
                               f"可选步骤跳过: {type(step_err).__name__}")
                        continue
                    raise

            # SPA 数据由 XHR 异步渲染：networkidle 5s 可能不够（且可能永远不 idle）。
            # 放宽到 15s，超时也继续（后续断言的 dom_state 采集会反映真实渲染结果）。
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception as idle_err:  # noqa: BLE001
                logger(f"[{ir.id}] networkidle 超时（继续采集 dom_state）: {type(idle_err).__name__}")
            screenshot = str(shot_dir / f"{ir.id}.png")
            page.screenshot(path=screenshot, full_page=True)
            dom_state = _build_dom_state(page, ir)
            return {"screenshot": screenshot, "dom_state": dom_state}
        finally:
            browser.close()


def _run_step(page, step, ir, idx: int, data: dict[str, str], base_url: str,
              logger: Callable[[str], None]) -> None:
    """执行单个 IR 步骤（Web 通道）。

    抽成独立函数，让 execute() 能按 optional 语义统一兜底异常。
    """
    if step.action == "nav":
        path = resolve_vars(step.path, data)
        url = path if path.startswith(("http", "file")) else f"{base_url}{path}"
        # 真实系统首屏资源多，load 事件可能超过 Playwright 默认 30s；
        # 放宽到 60s 并在超时后按 DOM 就绪继续（页面已可用），不中断用例。
        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
        except Exception as nav_err:  # noqa: BLE001
            logger(f"[{ir.id}] nav 超时（DOM 已就绪则继续）: {type(nav_err).__name__}")
        logger(f"[{ir.id}] 步骤{idx} nav → {url}")
    elif step.action == "fill":
        page.fill(step.selector, resolve_vars(step.value, data))
        logger(f"[{ir.id}] 步骤{idx} fill {step.selector}")
    elif step.action == "click":
        page.click(step.selector)
        logger(f"[{ir.id}] 步骤{idx} click {step.selector}")
        # click 可能触发整页导航（a 链接/表单）——等 load 完成再继续，
        # 避免后续步骤/截图截到跳转前的旧页面（竞态）
        try:
            page.wait_for_load_state("load", timeout=3000)
        except Exception as nav_err:  # noqa: BLE001 — SPA 无导航时超时属正常
            logger(f"[{ir.id}] click 后等待 load 超时（无导航，继续）: {type(nav_err).__name__}")
    elif step.action == "select_option":
        page.select_option(step.selector, resolve_vars(step.value, data))
        logger(f"[{ir.id}] 步骤{idx} select_option {step.selector} = {step.value}")
    elif step.action == "wait_visible":
        try:
            page.wait_for_selector(
                step.selector, state="visible", timeout=step.timeout_ms
            )
            logger(f"[{ir.id}] 步骤{idx} wait_visible {step.selector} ✓")
        except Exception:  # noqa: BLE001
            logger(f"[{ir.id}] 步骤{idx} wait_visible {step.selector} 超时(继续采集)")
    elif step.action == "wait_text":
        want = resolve_vars(step.text, data)
        sel = step.selector or "body"
        try:
            page.wait_for_function(
                """([sel, want]) => {
                    const el = document.querySelector(sel);
                    return !!el && (el.innerText || el.textContent || '').includes(want);
                }""",
                arg=[sel, want],
                timeout=step.timeout_ms,
            )
            logger(f"[{ir.id}] 步骤{idx} wait_text {sel} 含 {want!r} ✓")
        except Exception:  # noqa: BLE001
            logger(f"[{ir.id}] 步骤{idx} wait_text {sel} 含 {want!r} 超时(继续采集)")
    else:
        raise ValueError(f"未知步骤类型: {step.action}")
