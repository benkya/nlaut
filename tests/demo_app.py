"""MVP 本地被测系统：一个真实业务逻辑的登录页面（HTTP + 会话 + 密码校验 + 锁定规则）。

跑法: .venv/bin/python tests/demo_app.py  (默认 127.0.0.1:8307)
用途: 给 cases/demo/ 提供真实被测对象。

业务规则（真实校验，非静态页）：
1. 空用户名/空密码 → "请输入用户名和密码"
2. 密码错误 → "用户名或密码错误"（模糊提示，与真实系统一致）
3. 密码正确 → 登录成功页（欢迎横幅 + 工作台菜单）
4. 同一账号 60 秒内连错 3 次 → 锁定（60 秒后自动解锁）

账号（与 data/datasets.yaml 对齐）:
    zhangpeng / Right@123   正常账号
    testlock  / Lock@123    专供锁定场景（每次测试前重置）
"""

from __future__ import annotations

import http.server
import threading
import time
import urllib.parse

USERS = {"zhangpeng": "Right@123", "testlock": "Lock@123"}

# 登录失败锁定（演示业务规则：60 秒内连错 3 次锁定，60 秒后自动解锁）
_LOCK_THRESHOLD = 3
_LOCK_SECONDS = 60.0
_FAILED: dict[str, list[float]] = {}


def reset_lock_state() -> None:
    """重置全部锁定计数（测试前置钩子，保证用例可重复执行）。"""
    _FAILED.clear()


def _is_locked(user: str) -> bool:
    fails = [t for t in _FAILED.get(user, []) if time.time() - t < _LOCK_SECONDS]
    _FAILED[user] = fails
    return len(fails) >= _LOCK_THRESHOLD


def _record_failure(user: str) -> None:
    if user in USERS:  # 未知用户名不计数（模糊提示策略的一部分）
        _FAILED.setdefault(user, []).append(time.time())


PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">
<title>工作台 · 登录</title>
<style>
 body { font-family: -apple-system, 'PingFang SC', sans-serif; background:#f5f6f8; margin:0;
        display:flex; flex-direction:column; justify-content:center; align-items:center;
        height:100vh; }
 .brand { text-align:center; color:#9aa0a6; font-size:13px; margin:0 0 12px; letter-spacing:2px; }
 .card { background:#fff; border-radius:12px; padding:40px; width:360px;
         box-shadow:0 4px 16px rgba(0,0,0,.08); }
 h1 { font-size:20px; text-align:center; margin:0 0 24px; color:#1f2328; }
 label { display:block; font-size:13px; color:#555; margin:12px 0 4px; }
 input { width:100%; box-sizing:border-box; padding:10px; border:1px solid #d0d3d9;
         border-radius:6px; font-size:14px; }
 input:focus { outline:none; border-color:#2563eb; }
 .error-msg { background:#fdecea; color:#c0392b; border:1px solid #f5b7b1;
              border-radius:6px; padding:10px 12px; font-size:13px; margin-top:16px; }
 .banner { background:#e8f7ee; color:#1a7f37; border:1px solid #b7e4c7; border-radius:8px;
           padding:12px; font-size:16px; font-weight:600; text-align:center; }
 .locked { background:#fff7e0; color:#b8860b; border:1px solid #e8d48b; border-radius:8px;
           padding:12px; font-size:15px; font-weight:600; text-align:center; }
 button { width:100%; margin-top:16px; padding:12px; background:#2563eb; color:#fff;
          border:0; border-radius:6px; font-size:15px; cursor:pointer; }
 button:hover { background:#1d4ed8; }
 .menu { padding:0; margin:16px 0 0; }
 .menu li { padding:10px 12px; border-radius:6px; background:#f0f2f5; margin-bottom:8px;
            font-size:14px; list-style:none; }
</style></head><body><p class="brand">N L A U T · D E M O</p><div class="card">__BODY__</div></body></html>"""

LOGIN_FORM = """<h1>账号登录</h1>
<form method="POST" action="/login">
 <label for="username">用户名</label><input id="username" name="username" type="text" autocomplete="off">
 <label for="password">密码</label><input id="password" name="password" type="password">
 <button type="submit">登 录</button>
</form>"""

WORKSPACE = """<div class='banner'>✓ 登录成功，欢迎回来</div>
<h1 style='text-align:center'>工作台</h1>
<p style='text-align:center;color:#666;font-size:14px'>{user} · 管理员</p>
<ul class='menu'><li>📊 空间概览</li><li>📁 我的文件</li><li>🤖 智能体广场</li></ul>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *a):
        pass

    def _send(self, code: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _page(self, body: str, code: int = 200) -> None:
        self._send(code, PAGE.replace("__BODY__", body))

    def do_GET(self) -> None:
        if self.path in ("/", "/login"):
            self._page(LOGIN_FORM)
        else:
            self._page("<h1>404 页面不存在</h1>", code=404)

    def do_POST(self) -> None:
        if self.path != "/login":
            self._page("<h1>404</h1>", code=404)
            return
        length = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        user = (form.get("username") or [""])[0].strip()
        pwd = (form.get("password") or [""])[0]

        if not user or not pwd:
            self._page(LOGIN_FORM + "<div class='error-msg'>⚠ 请输入用户名和密码</div>")
            return
        if _is_locked(user):
            self._page(LOGIN_FORM
                       + "<div class='locked'>🔒 失败次数过多，账号已临时锁定，请 1 分钟后再试</div>")
            return
        if user in USERS and USERS[user] == pwd:
            _FAILED.pop(user, None)  # 成功即清零（与真实系统一致）
            self._page(WORKSPACE.replace("{user}", user))
        else:
            _record_failure(user)
            self._page(LOGIN_FORM
                       + "<div class='error-msg'>⚠ 用户名或密码错误，请重新输入</div>")


def start(port: int = 8307) -> tuple[str, threading.Thread]:
    """启动被测系统，返回 (base_url, thread)。"""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return f"http://127.0.0.1:{port}", t


if __name__ == "__main__":
    url, t = start()
    print(f"被测系统已启动: {url}  (Ctrl+C 停止)")
    print("正常账号: zhangpeng / Right@123 · 锁定演示账号: testlock / Lock@123")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("已停止")
