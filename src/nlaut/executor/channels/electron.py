"""L4 Electron 通道：IR steps → Playwright CDP 动作序列。

通过 connect_over_cdp 连接已运行的 Electron 应用（需以 --remote-debugging-port 启动），
复用 Web 通道的步骤翻译（nav/fill/click/wait_visible/select_option）和 DOM 采集。

三种接入方式：
1. connect_over_cdp（本实现）：app 已运行且开了 CDP 端口 → 连接、操作、断开
2. _electron.launch（未实现）：由框架启动 app → 生命周期全程控制（M2 扩展）
3. webview HTTP（兼容）：app 开了 webview HTTP 端口但没开 CDP → 当 Web 页面打
   （此场景用 channel: web 即可，本通道不重复）

已验证的坑位（实现时必须遵守）：
1. connect_over_cdp 的 browser.close() 只断开连接，不杀 app 进程
2. app 可能有多 page（多窗口/多 tab），默认取 contexts[0].pages[0]
3. Electron 自定义协议（dsh-app://）page.goto 可直达
4. IDE 插件 webview CDP 不可达 → 降级为 webview HTTP（用 channel: web）
5. e2e 探针（__e2eAppReady 等）prod 构建通常保留，可作就绪信号
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ...ir.model import TestCaseIR
from .web import _build_dom_state, resolve_vars


def execute(
    ir: TestCaseIR,
    data: dict[str, str] | None = None,
    cdp_url: str = "",
    base_url: str = "",
    screenshot_dir: str | Path = "artifacts/screenshots",
    logger: Callable[[str], None] = print,
) -> dict:
    """执行一条 IR 用例（Electron CDP 通道），返回 Evidence 字段。

    与 web.execute() 签名兼容，额外接受 cdp_url。
    返回 dict 直接喂 nlaut.judge.Evidence(case_id=ir.id, **result)。
    """
    from playwright.sync_api import sync_playwright

    if not cdp_url:
        raise ValueError(
            "Electron 通道需要 cdp_url（如 http://127.0.0.1:9222）。"
            "用 --cdp-url 指定，或以 --remote-debugging-port=9222 启动 Electron app。"
        )

    data = data or {}
    shot_dir = Path(screenshot_dir)
    shot_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(cdp_url)
        try:
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            pages = ctx.pages
            # 有 nav 步骤 → 用已有页面或新建页面导航
            # 无 nav 步骤 → 取已有页面（app 已加载的场景）
            page = pages[0] if pages else ctx.new_page()

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
                    try:
                        page.wait_for_load_state("load", timeout=3000)
                    except Exception as nav_err:  # noqa: BLE001 — SPA 无导航时超时属正常
                        logger(f"[{ir.id}] click 后等待 load 超时（无导航，继续）: {type(nav_err).__name__}")
                elif step.action == "select_option":
                    page.select_option(step.selector, resolve_vars(step.value, data))
                    logger(f"[{ir.id}] 步骤{idx} select_option {step.selector}")
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

            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception as idle_err:  # noqa: BLE001 — Electron SPA 可能永远不 idle
                logger(f"[{ir.id}] networkidle 超时（Electron SPA，继续）: {type(idle_err).__name__}")

            screenshot = str(shot_dir / f"{ir.id}.png")
            page.screenshot(path=screenshot, full_page=True)
            dom_state = _build_dom_state(page, ir)
            return {"screenshot": screenshot, "dom_state": dom_state}
        finally:
            # connect_over_cdp 的 close() 只断开连接，不杀 app 进程
            browser.close()
