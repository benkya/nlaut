#!/bin/bash
# Laya model.safetensors 并行分段下载（10 连接 Range 请求）
set -u
export https_proxy=http://127.0.0.1:7897 http_proxy=http://127.0.0.1:7897
BLOBDIR=~/.cache/huggingface/hub/models--aac6fef--laya-multilingual-mlx
URL="https://huggingface.co/aac6fef/laya-multilingual-mlx/resolve/main/model.safetensors"
TOTAL=643835426
N=10
CHUNK=$(( TOTAL / N ))
mkdir -p "$BLOBDIR/parts"

pids=()
for i in $(seq 0 $((N-1))); do
  START=$(( i * CHUNK ))
  if [ $i -eq $((N-1)) ]; then END=$(( TOTAL - 1 )); else END=$(( START + CHUNK - 1 )); fi
  LEN=$(( END - START + 1 ))
  ( for r in $(seq 1 30); do
      GOT=$(stat -f%z "$BLOBDIR/parts/part_$i" 2>/dev/null || echo 0)
      [ "$GOT" -ge "$LEN" ] && break
      # 显式计算剩余区间，curl 下到 .tmp 后追加（-C - 与 -r 组合会污染文件，禁用）
      REMAIN_START=$(( START + GOT ))
      curl -sL --max-time 240 -r "${REMAIN_START}-${END}" -o "$BLOBDIR/parts/part_$i.tmp" "$URL" 2>/dev/null || true
      TSZ=$(stat -f%z "$BLOBDIR/parts/part_$i.tmp" 2>/dev/null || echo 0)
      if [ "$TSZ" -gt 0 ]; then cat "$BLOBDIR/parts/part_$i.tmp" >> "$BLOBDIR/parts/part_$i" 2>/dev/null; fi
      rm -f "$BLOBDIR/parts/part_$i.tmp"
      sleep 1
    done ) &
  pids+=($!)
done

for p in "${pids[@]}"; do wait "$p"; done

# 校验 + 拼接
ok=1
for i in $(seq 0 $((N-1))); do
  START=$(( i * CHUNK ))
  if [ $i -eq $((N-1)) ]; then END=$(( TOTAL - 1 )); else END=$(( START + CHUNK - 1 )); fi
  LEN=$(( END - START + 1 ))
  GOT=$(stat -f%z "$BLOBDIR/parts/part_$i" 2>/dev/null || echo 0)
  [ "$GOT" -ne "$LEN" ] && ok=0 && echo "part_$i 不完整: $GOT/$LEN"
done

if [ "$ok" -eq 1 ]; then
  cat "$BLOBDIR/parts"/part_* > "$BLOBDIR/blobs/model.safetensors.full"
  FINAL=$(stat -f%z "$BLOBDIR/blobs/model.safetensors.full")
  if [ "$FINAL" -eq "$TOTAL" ]; then
    SNAPDIR=$(find "$BLOBDIR/snapshots" -type d -maxdepth 1 | head -1)
    mv "$BLOBDIR/blobs/model.safetensors.full" "$BLOBDIR/blobs/7fc5834af4d8fdfb268d272a9d1a66e5819a0daac98241651c4c888cc43adff1"
    ln -sf "$BLOBDIR/blobs/7fc5834af4d8fdfb268d272a9d1a66e5819a0daac98241651c4c888cc43adff1" "$SNAPDIR/model.safetensors"
    rm -rf "$BLOBDIR/parts"
    echo "LAYA_WEIGHTS_OK total=$FINAL"
  else
    echo "LAYA_WEIGHTS_BAD total=$FINAL expected=$TOTAL"
  fi
else
  echo "PARTS_INCOMPLETE"
fi
