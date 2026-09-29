#!/usr/bin/env bash
# nlaut 本地判定模型权重下载脚本
# 在全新机器上运行一次即可，权重下载到本地后离线运行
set -euo pipefail

echo "=== nlaut 本地模型权重下载 ==="
echo ""

# 代理（HF 直连不通时用，按需取消注释）
# export https_proxy=http://127.0.0.1:7897
# export http_proxy=http://127.0.0.1:7897

VLM_DIR="${HOME}/.cache/nlaut-models/Qwen2.5-VL-3B-Instruct-4bit"
LAYA_CACHE="${HOME}/.cache/huggingface/hub/models--aac6fef--laya-multilingual-mlx"

# ---------- 1. Qwen2.5-VL-3B（视觉判定引擎，2.9GB）----------
if [ -d "${VLM_DIR}" ] && [ -f "${VLM_DIR}/model.safetensors" ]; then
    echo "[1/2] Qwen2.5-VL-3B-Instruct-4bit 已存在，跳过"
else
    echo "[1/2] 下载 Qwen2.5-VL-3B-Instruct-4bit（2.9GB）..."
    mkdir -p "${VLM_DIR}"

    # 方式一：ModelScope（国内推荐，直连快）
    if command -v python3 &>/dev/null; then
        echo "  尝试 ModelScope 下载..."
        python3 -c "
from modelscope import snapshot_download
path = snapshot_download('Qwen/Qwen2.5-VL-3B-Instruct', revision='master', cache_dir='${HOME}/.cache/modelscope')
print(f'下载到: {path}')
" && cp -r "${HOME}/.cache/modelscope/Qwen/Qwen2.5-VL-3B-Instruct/"* "${VLM_DIR}/" \
          && echo "  ModelScope 下载完成" \
          || { echo "  ModelScope 失败，尝试 HuggingFace..."; false; }
    fi

    # 方式二：HuggingFace（需代理或直连）
    if [ ! -f "${VLM_DIR}/model.safetensors" ]; then
        echo "  尝试 HuggingFace 下载..."
        python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('Qwen/Qwen2.5-VL-3B-Instruct-4bit', local_dir='${VLM_DIR}')
print(f'下载到: {VLM_DIR}')
" && echo "  HuggingFace 下载完成" \
          || { echo "  HuggingFace 也失败！请手动下载或配置代理"; exit 1; }
    fi
fi
echo ""

# ---------- 2. laya-multilingual-mlx（语义判定引擎，614MB）----------
if [ -d "${LAYA_CACHE}" ]; then
    echo "[2/2] laya-multilingual-mlx 已存在，跳过"
else
    echo "[2/2] 下载 laya-multilingual-mlx（614MB）..."
    python3 -c "
import laya_mlx
laya_mlx.load('aac6fef/laya-multilingual-mlx')
print('laya 下载完成（HF 缓存）')
" && echo "  下载完成" \
       || { echo "  下载失败！请确认代理配置或手动下载 aac6fef/laya-multilingual-mlx"; exit 1; }
fi

echo ""
echo "=== 下载完成 ==="
echo "  VLM:  ${VLM_DIR} ($(du -sh ${VLM_DIR} 2>/dev/null | cut -f1))"
echo "  Laya: ${LAYA_CACHE} ($(du -sh ${LAYA_CACHE} 2>/dev/null | cut -f1))"
echo ""
echo "验证: .venv/bin/python -m pytest -m mlx -v"
