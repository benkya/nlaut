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
from ..selectors import SelectorNotFound, probe_selectors, split_candidates
from .web import _build_dom_state, _do_upload, resolve_vars


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

            probe_results: list = []
            for idx, step in enumerate(ir.steps, 1):
                try:
                    _run_step(page, step, ir, idx, data, base_url, logger, probe_results)
                except Exception as step_err:
                    # optional 步骤失败 → 降级跳过；非 optional → 原样抛出
                    if getattr(step, "optional", False):
                        logger(f"[{ir.id}] 步骤{idx} {step.action} "
                               f"{getattr(step, 'selector', '')} "
                               f"可选步骤跳过: {type(step_err).__name__}")
                        continue
                    raise

            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception as idle_err:  # noqa: BLE001 — Electron SPA 可能永远不 idle
                logger(f"[{ir.id}] networkidle 超时（Electron SPA，继续）: {type(idle_err).__name__}")

            screenshot = str(shot_dir / f"{ir.id}.png")
            # 视觉回归必须用视口截图：full_page 的高度随内容变化，
            # SSIM 比对时尺寸不一致只能 resize，掩盖布局位移。
            # 有 visual_regression 断言时自动切视口模式。
            full_page = not any(getattr(a, "kind", "") == "visual_regression" for a in ir.assertions)
            page.screenshot(path=screenshot, full_page=full_page)
            dom_state = _build_dom_state(page, ir)
            result = {"screenshot": screenshot, "dom_state": dom_state}
            if probe_results:
                result["process_probes"] = (
                    probe_results[0] if len(probe_results) == 1 else probe_results
                )
            return result
        finally:
            # connect_over_cdp 的 close() 只断开连接，不杀 app 进程
            browser.close()


def _join_url(base_url: str, path: str) -> str:
    """拼接 base_url 与相对路径，规范化斜杠。

    旧实现直接 `f"{base_url}{path}"`，base_url 带尾斜杠 + path 带首斜杠
    会拼出 `http://host:port//` —— 应用路由不匹配，页面空白，
    后续步骤全部超时（2026-11-13 实测踩坑）。
    """
    base = base_url.rstrip("/")
    if not path:
        return base + "/"
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def _run_step(page, step, ir, idx: int, data: dict[str, str], base_url: str,
              logger: Callable[[str], None],
              probe_results: list | None = None) -> None:
    """执行单个 IR 步骤（Electron 通道）。

    抽成独立函数，让 execute() 能按 optional 语义统一兜底异常。
    """
    if step.action == "nav":
        path = resolve_vars(step.path, data)
        url = path if path.startswith(("http", "file")) else _join_url(base_url, path)
        page.goto(url)
        logger(f"[{ir.id}] 步骤{idx} nav → {url}")
    elif step.action == "fill":
        sel = resolve_vars(step.selector, data)
        page.fill(sel, resolve_vars(step.value, data))
        logger(f"[{ir.id}] 步骤{idx} fill {sel}")
    elif step.action == "upload":
        # 复用 Web 通道的文件注入（set_input_files 两通道同 API）
        _do_upload(page, step, ir, idx, data, logger)
    elif step.action == "click":
        sel = resolve_vars(step.selector, data)
        # 候选降级：selector 可写逗号分隔的多个候选，逐个尝试
        # （UI 改版时 aria-label / class / 文本任一未变就能救回来）
        candidates = split_candidates(sel)
        if len(candidates) > 1:
            probe = probe_selectors(page, sel)
            if probe.chosen is None:
                raise SelectorNotFound(
                    f"click 步骤{idx} 的所有候选选择器均不可用:\n{probe.render()}"
                )
            sel = probe.chosen.selector
        page.click(sel)
        logger(f"[{ir.id}] 步骤{idx} click {sel}")
        try:
            page.wait_for_load_state("load", timeout=3000)
        except Exception as nav_err:  # noqa: BLE001 — SPA 无导航时超时属正常
            logger(f"[{ir.id}] click 后等待 load 超时（无导航，继续）: {type(nav_err).__name__}")
    elif step.action == "select_option":
        sel = resolve_vars(step.selector, data)
        page.select_option(sel, resolve_vars(step.value, data))
        logger(f"[{ir.id}] 步骤{idx} select_option {sel}")
    elif step.action == "wait_visible":
        sel = resolve_vars(step.selector, data)
        try:
            page.wait_for_selector(
                sel, state="visible", timeout=step.timeout_ms
            )
            logger(f"[{ir.id}] 步骤{idx} wait_visible {sel} ✓")
        except Exception:  # noqa: BLE001
            logger(f"[{ir.id}] 步骤{idx} wait_visible {sel} 超时(继续采集)")
    elif step.action == "wait_text":
        want = resolve_vars(step.text, data)
        sel = resolve_vars(step.selector or "body", data)
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
    elif step.action == "process_probe":
        # 工序域探针：立即采集当前工序状态（支持「动作 → 观察迁移」序列）
        from ...judge.process_truth import collect_probe

        try:
            result = collect_probe(
                root=getattr(step, "root", "~/.cecoder/engineering-workbench"),
                probe=getattr(step, "probe", "node"),
                task_id=getattr(step, "task_id", None),
                node=getattr(step, "node", None),
            )
        except OSError as e:
            result = {"probe": getattr(step, "probe", "node"),
                      "error": f"{type(e).__name__}: {e}"}
        if probe_results is not None:
            probe_results.append(result)
        logger(f"[{ir.id}] 步骤{idx} process_probe {result.get('probe')} ✓")
    else:
        raise ValueError(f"未知步骤类型: {step.action}")
