#!/usr/bin/env bash
# DeepSeek-V4.1-Flash INT8 (sglang) 端点可用性守候 + 自动抢跑
#
# 背景：10.62.64.38:30089 的可用窗口约 20 分钟，而 P0 批次需 15-30 分钟。
#       手动等待会错过窗口 → 本脚本持续探测，一旦 200 立刻启动批次。
#       若批次中途遇 502，则记录并重试（最多 3 轮，每轮独立产物）。
set -u
cd ~/workspace/nlaut
set -a; source ~/.hermes/.env; set +a

BASE="http://10.62.48.94"   # 占位防误用
URL="http://10.62.64.38:30089/v1/models"
MAX_WAIT=120                # 最长等待 120 分钟（每 30s 一次）
MAX_RETRY=3

echo "[守候] 开始探测 $(date '+%H:%M:%S')  最长等待 ${MAX_WAIT} 分钟"

for w in $(seq 1 $((MAX_WAIT*2))); do
  code=$(curl -s -m 12 -o /dev/null -w "%{http_code}" "$URL" -H "Authorization: Bearer swardz" 2>/dev/null)
  if [ "$code" = "200" ]; then
    echo "[守候] $(date '+%H:%M:%S') 端点可用（http=200），立即启动批次"
    for attempt in $(seq 1 $MAX_RETRY); do
      echo "[守候] === 尝试 $attempt/$MAX_RETRY  $(date '+%H:%M:%S') ==="
      .venv/bin/python scripts/combined_report.py \
        --model deepseek-v4.1-flash-int8-sglang \
        --think off --timeout 600 \
        --out "artifacts/report_combined_ds41f_sglang_v4_a${attempt}.html" \
        > "/tmp/ds41f_v4_a${attempt}.log" 2>&1
      rc=$?
      echo "[守候] 尝试 $attempt 结束 rc=$rc  $(date '+%H:%M:%S')"
      # 判定本轮是否被 502 污染
      n502=$(grep -c "502 Bad Gateway" "/tmp/ds41f_v4_a${attempt}.log" 2>/dev/null || echo 0)
      np0=$(grep -oE "\[对话能力\] [0-9]+/[0-9]+" "/tmp/ds41f_v4_a${attempt}.log" 2>/dev/null | tail -1)
      echo "[守候]   502出现次数=$n502   P0结果=${np0:-未完成}"
      if [ "$n502" = "0" ] && [ -n "${np0:-}" ]; then
        echo "[守候] ✅ 本轮无 502 污染，产物有效，结束守候"
        exit 0
      fi
      echo "[守候] 本轮被 502 污染，等待端点恢复后重试"
      for i in $(seq 1 40); do
        c=$(curl -s -m 12 -o /dev/null -w "%{http_code}" "$URL" -H "Authorization: Bearer swardz" 2>/dev/null)
        [ "$c" = "200" ] && { echo "[守候] $(date '+%H:%M:%S') 端点恢复"; break; }
        sleep 30
      done
    done
    echo "[守候] 重试次数用尽，退出"
    exit 1
  fi
  echo "[守候] $(date '+%H:%M:%S') http=$code 等待中"
  sleep 30
done
echo "[守候] 等待超时，端点始终不可用"
exit 2