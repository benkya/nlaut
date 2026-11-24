#!/usr/bin/env bash
# CeCoder 工序域用例批量运行脚本
#
# 前置（脚本不自动做，需人工确认后执行）：
#   1. CeCoder 已登录（顶栏 leading 簇只在 authed 时渲染，否则「创建任务」按钮不存在）
#   2. 决定是否重启你的 CeCoder 实例 —— 重启会丢失未保存的工作
#
# 用法：
#   ./scripts/run_cecoder_cases.sh [用例id ...]
#   ./scripts/run_cecoder_cases.sh                     # 跑全部 cases/cecoder/ 用例
#
# 环境变量：
#   CECODER_CDP_PORT   CDP 端口（默认 9222）
#   CECODER_BASE_URL   应用 webview 地址（默认 http://127.0.0.1:49791/）
#                       每次启动端口可能变，用 ./scripts/cecoder_cdp_url.sh 查

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

CDP_PORT="${CECODER_CDP_PORT:-9222}"
ALLOWED_PATHS="${NLaut_AGENT_JUDGE_ALLOWED_PATHS:-/Users/Ben/.cecoder}"

# base_url 自动探测：CeCoder 每次启动的 webview 端口都可能变。
# 用 cecoder_cdp_url.sh 探测；探测失败则回落到环境变量。
DETECTED="$(bash "$REPO/scripts/cecoder_cdp_url.sh" 2>/dev/null || true)"
BASE_URL="${CECODER_BASE_URL:-}"
if [ -z "$BASE_URL" ]; then
  BASE_URL="$(printf '%s\n' "$DETECTED" | sed -n 's/^CECODER_BASE_URL=//p' | head -1)"
fi
if [ -z "$BASE_URL" ]; then
  echo "[run] 错误: 无法探测 CeCoder 应用地址。请设 CECODER_BASE_URL。" >&2
  echo "      可先跑: ./scripts/cecoder_cdp_url.sh" >&2
  exit 2
fi
CDP_PORT="$(printf '%s\n' "$DETECTED" | sed -n 's/^CECODER_CDP_PORT=//p' | head -1)"
CDP_PORT="${CDP_PORT:-$CDP_PORT}"

# 收集用例 id：命令行给了就用给的，否则取 cases/cecoder/ 下全部
if [ "$#" -gt 0 ]; then
  CASE_ARGS=()
  for c in "$@"; do CASE_ARGS+=(--case "$c"); done
else
  CASE_ARGS=()
  for f in cases/cecoder/*.yaml; do
    id="$(basename "$f" .yaml)"
    CASE_ARGS+=(--case "$id")
  done
fi

if [ "${#CASE_ARGS[@]}" -eq 0 ]; then
  echo "[run] cases/cecoder/ 下没有用例" >&2
  exit 1
fi

# CDP 探活 + 环境前置健康检查
#
# 为什么要有健康检查（2026-11-15 实测踩坑）：
#   auth 后端不可达 → 应用进入 login 模式 → 登录遮罩拦截全部指针事件
#   → GUI 用例全部报 error(0.0s) + TimeoutError，报错信息看不出是环境问题。
#   为定位这个问题曾做三层排查（选择器 → DOM → TCP 探测 → 应用日志），耗时约 15 分钟。
#   现在脚本会在 30 秒内给出明确结论。
#
# 跳过方式：CECODER_SKIP_HEALTH_CHECK=1
if ! curl -s --max-time 5 "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1; then
  echo "[run] 错误: CDP 端口 ${CDP_PORT} 未就绪。" >&2
  echo "      启动方式: /Applications/Cecoder.app/Contents/MacOS/Cecoder \\" >&2
  echo "               --remote-debugging-port=${CDP_PORT} \\" >&2
  echo "               --user-data-dir=/tmp/cecoder_cdp_profile" >&2
  exit 2
fi

if [ "${CECODER_SKIP_HEALTH_CHECK:-0}" != "1" ]; then
  HEALTH_RC=0
  .venv/bin/python -m nlaut.harness.health \
    --cdp-port "$CDP_PORT" --base-url "$BASE_URL" || HEALTH_RC=$?
  if [ "$HEALTH_RC" -ne 0 ]; then
    # 区分两类用例：
    #   GUI 用例（点按钮）→ 登录遮罩拦截交互，必挂 → 中止
    #   纯工序域用例（读文件系统）→ 不受遮罩影响 → 照跑
    # 分类逻辑放 Python 里（bash 数组在空数组下会塞空串，容易出错）
    SELECT_JSON="$(printf '%s\n' "$@" | .venv/bin/python -c '
import json,sys
print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))
')"
    SPLIT_JSON="$(.venv/bin/python -m nlaut.harness.health --split-cases "$SELECT_JSON")"
    GUI_CASES="$(printf '%s' "$SPLIT_JSON" | .venv/bin/python -c 'import json,sys; print(" ".join(json.load(sys.stdin)["gui"]))')"
    NOGUI_CASES="$(printf '%s' "$SPLIT_JSON" | .venv/bin/python -c 'import json,sys; print(" ".join(json.load(sys.stdin)["nogui"]))')"

    if [ -n "$GUI_CASES" ]; then
      echo "" >&2
      echo "[run] 错误: 环境不可用，GUI 用例已跳过（避免逐条报 TimeoutError 浪费时间）。" >&2
      echo "      受影响用例: ${GUI_CASES}" >&2
      echo "      强制运行: CECODER_SKIP_HEALTH_CHECK=1 ./scripts/run_cecoder_cases.sh ..." >&2
      exit 3
    fi
    if [ -n "$NOGUI_CASES" ]; then
      echo "[run] 环境不可用，但所选用例不依赖 GUI（纯工序域），继续运行。" >&2
      CASE_ARGS=()
      for c in $NOGUI_CASES; do CASE_ARGS+=(--case "$c"); done
    else
      exit 3
    fi
  fi
fi

echo "[run] CDP: http://127.0.0.1:${CDP_PORT}  base-url: ${BASE_URL}"
echo "[run] ToolPolicy 放行: ${ALLOWED_PATHS}"
echo "[run] 用例数: $(( ${#CASE_ARGS[@]} / 2 ))"

export NLaut_AGENT_JUDGE_ALLOWED_PATHS="$ALLOWED_PATHS"

.venv/bin/python -m nlaut.cli \
  "${CASE_ARGS[@]}" \
  --cdp-url "http://127.0.0.1:${CDP_PORT}" \
  --base-url "$BASE_URL" \
  --report artifacts/report_cecoder_full.html

echo "[run] 报告: artifacts/report_cecoder_full.html"
