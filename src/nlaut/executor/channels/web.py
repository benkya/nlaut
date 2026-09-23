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
    """采集断言涉及的 selector 可见文本（1° 确定性判定的输入）。"""
    dom: dict[str, str] = {}
    for a in ir.assertions:
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
                if step.action == "nav":
                    path = resolve_vars(step.path, data)
                    url = path if path.startswith(("http", "file")) else f"{base_url}{path}"
                    page.goto(url)
                    logger(f"[{ir.id}] 步骤{idx} nav → {url}")
                elif step.action == "fill":
                    page.fill(step.selector, resolve_vars(step.value, data))
                    logger(f"[{ir.id}] 步骤{idx} fill {step.selector}")
                elif step.action == "click":
                    page.click(step.selector)
                    logger(f"[{ir.id}] 步骤{idx} click {step.selector}")
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
                else:
                    raise ValueError(f"未知步骤类型: {step.action}")

            page.wait_for_load_state("networkidle", timeout=5000)
            screenshot = str(shot_dir / f"{ir.id}.png")
            page.screenshot(path=screenshot, full_page=True)
            dom_state = _build_dom_state(page, ir)
            return {"screenshot": screenshot, "dom_state": dom_state}
        finally:
            browser.close()
