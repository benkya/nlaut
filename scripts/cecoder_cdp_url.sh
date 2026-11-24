#!/usr/bin/env bash
# 打印当前 CeCoder 的 CDP 地址与应用 webview 地址（供 run_cecoder_cases.sh 使用）
#
# 用法:
#   eval "$(./scripts/cecoder_cdp_url.sh)"   # 导出 CECODER_CDP_PORT / CECODER_BASE_URL
#   ./scripts/cecoder_cdp_url.sh             # 直接打印

set -euo pipefail

CDP_PORT="${CECODER_CDP_PORT:-9222}"
CDP_JSON="http://127.0.0.1:${CDP_PORT}/json/list"

if ! curl -s --max-time 5 "$CDP_JSON" >/dev/null 2>&1; then
  echo "CDP_PORT=${CDP_PORT}" >&2
  echo "CDP_BASE_URL=" >&2
  echo "# 错误: CDP 端口 ${CDP_PORT} 未就绪（先启动带 --remote-debugging-port 的 CeCoder）" >&2
  exit 2
fi

# 从 page 目标里取应用主界面 URL（http://127.0.0.1:<port>/ 形式）
# 注意: webview 端口动态分配、不在进程参数里。
#       两个来源，按可靠性排序：
#       1) Electron 启动日志里的 "[Cecoder] ready on http://127.0.0.1:<port>/"
#          —— 不受页面被导航走的影响，最可靠
#       2) CDP 的 page target —— 页面被导航到 file:// 时就找不到了
APP_URL=""
LOG_URL="$(grep -ohE '\[Cecoder\] ready on http://127\.0\.0\.1:[0-9]+' \
  "$HOME"/.cecoder/desktop-logs/*.log 2>/dev/null | tail -1 \
  | grep -oE 'http://127\.0\.0\.1:[0-9]+' || true)"
if [ -n "$LOG_URL" ]; then
  APP_URL="$LOG_URL"
else
  APP_URL="$(curl -s --max-time 5 "$CDP_JSON" \
    | python3 -c "
import json, sys
try:
    targets = json.load(sys.stdin)
except Exception:
    targets = []
candidates = []
for t in targets:
    url = (t.get('url') or '')
    if not url.startswith('http://127.0.0.1:'):
        continue
    rest = url[len('http://127.0.0.1:'):]
    port = rest.split('/')[0]
    if not port.isdigit() or int(port) == ${CDP_PORT}:
        continue          # 排除 CDP 自身端口
    candidates.append(url.split('?')[0].rstrip('/'))
print(candidates[0] if candidates else '')
" || true)"
fi

echo "CDP_PORT=${CDP_PORT}"
echo "CDP_BASE_URL=${APP_URL}"
echo "CECODER_CDP_PORT=${CDP_PORT}"
echo "CECODER_BASE_URL=${APP_URL}"

if [ -z "$APP_URL" ]; then
  echo "# 警告: 未找到 CeCoder 应用页面（可能已被导航到 file:// 或非 127.0.0.1 地址）" >&2
fi
