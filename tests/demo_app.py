"""MVP 本地被测系统：一个真实的登录服务（HTTP + 会话 + 密码校验）。

跑法: .venv/bin/python -m tests.demo_app  (默认 127.0.0.1:8307)
用途: 给 cases/demo/tc_login_001.yaml 提供真实被测对象。
正确的登录: zhangpeng / Right@123（见 data/datasets.yaml login_valid）
"""

from __future__ import annotations

import http.server
import threading
import urllib.parse

USERS = {"zhangpeng": "Right@123"}  # 演示账号，正确密码与 datasets.yaml 对齐

PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">
<style>
 body { font-family: -apple-system, sans-serif; background:#f5f6f8; margin:0;
        display:flex; justify-content:center; align-items:center; height:100vh; }
 .card { background:#fff; border-radius:12px; padding:40px; width:360px;
         box-shadow:0 4px 16px rgba(0,0,0,.08); }
 h1 { font-size:20px; text-align:center; margin:0 0 24px; }
 label { display:block; font-size:13px; color:#555; margin:12px 0 4px; }
 input { width:100%; box-sizing:border-box; padding:10px; border:1px solid #d0d3d9;
         border-radius:6px; font-size:14px; }
 .error-msg { background:#fdecea; color:#c0392b; border:1px solid #f5b7b1;
              border-radius:6px; padding:10px 12px; font-size:13px; margin-top:16px; }
 .banner { background:#e8f7ee; color:#1a7f37; border:1px solid #b7e4c7; border-radius:8px;
           padding:12px; font-size:16px; font-weight:600; text-align:center; }
 button { width:100%; margin-top:16px; padding:12px; background:#2563eb; color:#fff;
          border:0; border-radius:6px; font-size:15px; cursor:pointer; }
 .menu li { padding:10px 12px; border-radius:6px; background:#f0f2f5; margin-bottom:8px;
            font-size:14px; list-style:none; }
</style></head><body><div class="card">__BODY__</div></body></html>"""

LOGIN_FORM = """<h1>账号登录</h1>
<form method="POST" action="/login">
 <label for="username">用户名</label><input id="username" name="username" type="text">
 <label for="password">密码</label><input id="password" name="password" type="password">
 <button type="submit">登 录</button>
</form>"""


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

    def do_GET(self) -> None:
        if self.path in ("/", "/login"):
            self._send(200, PAGE.replace("__BODY__", LOGIN_FORM))
        else:
            self._send(404, PAGE.replace("__BODY__", "<h1>404 页面不存在</h1>"))

    def do_POST(self) -> None:
        if self.path != "/login":
            self._send(404, PAGE.replace("__BODY__", "<h1>404</h1>"))
            return
        length = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        user = (form.get("username") or [""])[0].strip()
        pwd = (form.get("password") or [""])[0]
        if not user or not pwd:
            self._send(200, PAGE.replace("__BODY__", LOGIN_FORM
                        + "<div class='error-msg'>⚠ 请输入用户名和密码</div>"))
        elif user in USERS and USERS[user] == pwd:
            self._send(200, PAGE.replace("__BODY__", """<div class='banner'>✓ 登录成功，欢迎回来</div>
<h1 style='text-align:center'>工作台</h1>
<p style='text-align:center;color:#666;font-size:14px'>""" + user + """ · 管理员</p>
<ul class='menu'><li>📊 空间概览</li><li>📁 我的文件</li><li>🤖 智能体广场</li></ul>"""))
        else:
            self._send(200, PAGE.replace("__BODY__", LOGIN_FORM
                        + "<div class='error-msg'>⚠ 用户名或密码错误，请重新输入</div>"))


def start(port: int = 8307) -> tuple[str, threading.Thread]:
    """启动被测系统，返回 (base_url, thread)。"""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return f"http://127.0.0.1:{port}", t


if __name__ == "__main__":
    url, t = start()
    print(f"被测系统已启动: {url}  (Ctrl+C 停止)")
    try:
        while True:
            import time
            time.sleep(3600)
    except KeyboardInterrupt:
        print("已停止")
