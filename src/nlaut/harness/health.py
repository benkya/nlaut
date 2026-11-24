"""CeCoder 环境前置健康检查。

**为什么需要**（2026-11-15 实测踩坑）：

批量跑 CeCoder 用例时，5 条 GUI 用例全部报 `error`（0.0s）+ `TimeoutError`，
报错信息**完全看不出**是「环境问题」还是「用例问题」。
为此做了三层排查才定位到真因：

1. 用例选择器过时 → 改了仍失败
2. 按钮 DOM 结构变化 → 改了仍失败
3. 登录遮罩 `<div data-shell-overlay="true">` 拦截全部指针事件
4. 遮罩内容是「登录态校验失败，请重新登录」
5. TCP 探测 auth 后端 `10.64.17.69:30972` → TimeoutError
6. 应用自身日志 `[gienxAuth] 登录态校验失败：网络不可达`

排查耗时约 15 分钟。本模块把「30 秒内给出明确结论」变成脚本的默认行为。

**检查项**：

| 检查 | 判据 | 失败含义 |
|---|---|---|
| `cdp` | CDP 端口 `/json/version` 可达 | 应用没带 `--remote-debugging-port` 启动 |
| `auth_backend` | auth 配置里的 baseUrl TCP 可达 | 登录态校验必然失败 → 应用进 login 模式 |
| `login_overlay` | 页面内 `[data-shell-overlay="true"]` 数量 | 有遮罩 → 拦截全部指针事件 → GUI 用例必挂 |

**关键区分**：`login_overlay` 单独报出是没意义的（很多 overlay 是正常的）。
真正致命的是「遮罩内容含登录提示」或「auth 后端不可达」。
本模块因此同时读遮罩内文本做判别。
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["AUTH_CONFIG", "HealthItem", "HealthReport", "check_environment", "split_cases"]

AUTH_CONFIG = Path.home() / ".cecoder" / "cecoder-auth.json"


@dataclass
class HealthItem:
    """单项检查结果。"""

    name: str
    ok: bool
    detail: str
    fatal: bool = True
    """True = 致命（GUI 用例必挂）；False = 提示（可能仍可跑）。"""

    def line(self) -> str:
        mark = "✅" if self.ok else ("❌" if self.fatal else "⚠️ ")
        return f"  {mark} {self.name}: {self.detail}"


@dataclass
class HealthReport:
    """整体检查结果。"""

    items: list[HealthItem] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """没有致命项失败 = 环境可用。"""
        return not any(not i.ok and i.fatal for i in self.items)

    @property
    def fatal_failures(self) -> list[HealthItem]:
        return [i for i in self.items if not i.ok and i.fatal]

    def add(self, item: HealthItem) -> None:
        self.items.append(item)

    def render(self) -> str:
        lines = ["[health] CeCoder 环境前置检查:"]
        lines += [i.line() for i in self.items]
        if self.ok:
            lines.append("[health] 结果: 环境可用")
        else:
            names = ", ".join(i.name for i in self.fatal_failures)
            lines.append(f"[health] 结果: 环境不可用（致命项失败: {names}）")
        return "\n".join(lines)

    def advice(self) -> str:
        """针对失败项给出可执行的处置建议。"""
        out: list[str] = []
        for i in self.fatal_failures:
            if i.name == "cdp":
                out.append(
                    "  - CDP 未就绪。启动方式:\n"
                    "      /Applications/Cecoder.app/Contents/MacOS/Cecoder \\\n"
                    "        --remote-debugging-port=9222 --user-data-dir=/tmp/cecoder_cdp_profile"
                )
            elif i.name == "auth_backend":
                out.append(
                    "  - auth 后端不可达 → 应用会进入 login 模式，登录遮罩拦截全部交互。\n"
                    "      GUI 用例在此状态下必挂（报 TimeoutError / pointer events intercepted）。\n"
                    "      处置: 等后端恢复（内网/VPN），或改跑不依赖 GUI 的用例:\n"
                    "        ./scripts/run_cecoder_cases.sh tc_cecoder_process_001"
                )
            elif i.name == "login_overlay":
                out.append(
                    "  - 页面存在登录遮罩 → 指针事件被拦截，GUI 用例必挂。\n"
                    "      处置: 在应用里重新登录（登录态过期），或等后端恢复。"
                )
        if out:
            return "\n".join(out)
        return ""


def _check_cdp(cdp_port: int) -> HealthItem:
    """CDP 端口探活。"""
    url = f"http://127.0.0.1:{cdp_port}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read().decode("utf-8", "replace")
        try:
            ver = json.loads(body).get("Browser", "?")
        except json.JSONDecodeError:
            ver = "?"
        return HealthItem("cdp", True, f"就绪（{ver}）")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return HealthItem("cdp", False, f"端口 {cdp_port} 不可达（{type(e).__name__}）")


def _parse_base_url(raw: str) -> tuple[str, int] | None:
    """从 baseUrl 解析 (host, port)。支持带/不带协议、端口、路径。"""
    s = raw.strip()
    if "://" in s:
        s = s.split("://", 1)[1]
    s = s.split("/", 1)[0]
    if not s:
        return None
    if ":" in s:
        host, _, port_s = s.partition(":")
        try:
            return host, int(port_s)
        except ValueError:
            return None
    return s, (443 if raw.startswith("https") else 80)


def _check_auth_backend(timeout: float = 5.0) -> HealthItem:
    """auth 后端 TCP 可达性。

    后端不可达 → 应用登录态校验必然失败 → 进入 login 模式 → 遮罩拦截交互。
    这是 GUI 用例批量失败的**最常见根因**。
    """
    if not AUTH_CONFIG.is_file():
        return HealthItem(
            "auth_backend", False,
            f"读不到 auth 配置 {AUTH_CONFIG}",
        )
    try:
        data = json.loads(AUTH_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return HealthItem("auth_backend", False, f"auth 配置解析失败: {type(e).__name__}")

    base = ((data.get("identity") or {}).get("baseUrl")) or ""
    if not base:
        return HealthItem("auth_backend", False, "auth 配置里没有 identity.baseUrl")

    parsed = _parse_base_url(base)
    if not parsed:
        return HealthItem("auth_backend", False, f"无法解析 baseUrl: {base!r}")
    host, port = parsed
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return HealthItem("auth_backend", True, f"可达 {host}:{port}")
    except OSError as e:
        return HealthItem(
            "auth_backend", False,
            f"不可达 {host}:{port}（{type(e).__name__}）→ 应用会进入 login 模式",
        )


def _check_login_overlay(cdp_port: int, base_url: str) -> HealthItem:
    """页面是否存在登录遮罩。

    判据：CDP 读页面里 `[data-shell-overlay="true"]` 的数量与文本。
    遮罩内含「登录」提示 → 致命；否则视为非致命（正常 overlay）。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return HealthItem("login_overlay", True, "playwright 不可用，跳过检查")

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
            try:
                for ctx in browser.contexts:
                    for page in ctx.pages:
                        if base_url and base_url not in page.url:
                            continue
                        overlays = page.query_selector_all('[data-shell-overlay="true"]')
                        if not overlays:
                            return HealthItem("login_overlay", True, "无遮罩")
                        text = ""
                        try:
                            text = overlays[0].inner_text(timeout=3000) or ""
                        except Exception as e:  # noqa: BLE001
                            # 读文本失败不该让整个检查崩 —— 退化为「有遮罩但内容未知」
                            text = ""
                            _ = type(e).__name__
                        if "登录" in text:
                            return HealthItem(
                                "login_overlay", False,
                                f"存在登录遮罩（{text.strip().splitlines()[0][:40] if text.strip() else '含登录字样'}）",
                            )
                        return HealthItem(
                            "login_overlay", True,
                            f"有 {len(overlays)} 个遮罩但非登录遮罩",
                            fatal=False,
                        )
                return HealthItem("login_overlay", True, "未找到应用页面")
            finally:
                browser.close()
    except Exception as e:  # noqa: BLE001
        # 检查本身失败不该阻断 —— 但要提示
        return HealthItem(
            "login_overlay", True,
            f"检查未完成（{type(e).__name__}）", fatal=False,
        )


def check_environment(
    cdp_port: int = 9222,
    base_url: str = "",
    check_overlay: bool = True,
) -> HealthReport:
    """跑完整的前置健康检查。"""
    rep = HealthReport()
    rep.add(_check_cdp(cdp_port))
    rep.add(_check_auth_backend())
    if check_overlay:
        rep.add(_check_login_overlay(cdp_port, base_url))
    return rep


def split_cases(cases_dir: str | Path = "cases/cecoder",
                selected: list[str] | None = None) -> dict[str, list[str]]:
    """把用例分成「依赖 GUI」与「纯工序域（不依赖 GUI）」两类。

    分类判据（读 YAML 原文，不做完整解析 —— 只看步骤与断言）：
    - 步骤里有交互动作（click / wait_visible / fill / select_option）→ 依赖 GUI
    - 断言里全是 `process_state`（读文件系统）→ 不依赖 GUI

    **为什么要分**：登录遮罩只拦截 GUI 交互，不影响读文件系统的工序域断言。
    环境不可用时，GUI 用例必挂（TimeoutError），纯工序域用例照跑。

    返回 {"gui": [...], "nogui": [...]}；selected 非空时只在该集合内分类。
    """
    import re

    d = Path(cases_dir)
    if not d.is_dir():
        return {"gui": [], "nogui": []}

    gui: list[str] = []
    nogui: list[str] = []
    for f in sorted(d.glob("*.yaml")):
        cid = f.stem
        if selected and cid not in selected:
            continue
        text = f.read_text(encoding="utf-8")
        has_interaction = bool(
            re.search(r"^\s*- action: (click|wait_visible|fill|select_option)\b", text, re.MULTILINE)
        )
        # 只用 process_state 断言 → 不依赖 GUI
        assertion_kinds = set(re.findall(r"^\s*- kind: (\w+)", text, re.MULTILINE))
        only_process = bool(assertion_kinds) and assertion_kinds <= {"process_state"}
        if has_interaction and not only_process:
            gui.append(cid)
        else:
            nogui.append(cid)
    return {"gui": gui, "nogui": nogui}


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：0 = 可用，1 = 不可用。"""
    import argparse

    ap = argparse.ArgumentParser(description="CeCoder 环境前置健康检查")
    ap.add_argument("--cdp-port", type=int, default=9222)
    ap.add_argument("--base-url", default="")
    ap.add_argument("--no-overlay", action="store_true", help="跳过遮罩检查")
    ap.add_argument("--split-cases", metavar="JSON",
                    help="按 JSON 数组的用例 id 分类（GUI / 纯工序域），输出 JSON")
    ap.add_argument("--cases-dir", default="cases/cecoder")
    args = ap.parse_args(argv)

    if args.split_cases is not None:
        selected = json.loads(args.split_cases) or None
        print(json.dumps(split_cases(args.cases_dir, selected), ensure_ascii=False))
        return 0

    rep = check_environment(args.cdp_port, args.base_url, not args.no_overlay)
    print(rep.render())
    if not rep.ok:
        advice = rep.advice()
        if advice:
            print("\n[health] 处置建议:")
            print(advice)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
