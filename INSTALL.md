# nlaut 安装指南

> **目标读者**：在一台全新的 Apple Silicon Mac 上从零部署 nlaut 框架（含本地判定模型）的工程师。
> **预计耗时**：30 分钟（权重下载占大头）。
> **前提条件**：macOS + Apple Silicon（M1/M2/M3/M4）+ Python 3.11+ + ~5GB 磁盘空间。

---

## 前提条件

| 条件 | 要求 | 检查命令 |
| --- | --- | --- |
| 操作系统 | macOS Apple Silicon（M1+） | `uname -m` → `arm64` |
| Python | 3.11 或 3.12 | `python3 --version` |
| 磁盘 | ~5GB（权重 3.5GB + venv ~1GB） | `df -h ~` |
| 网络 | 能访问 GitHub +（可选）HuggingFace/ModelScope | `curl -sI github.com` |

> **Linux/Windows？** 框架的确定性断言、LLM API 测试、Web 测试可以跑，但 Laya 和 VLM 判定引擎依赖 Apple MLX，无法在非 Apple Silicon 上运行。判定会降级为「确定性 + human_review」模式。

---

## 第 1 步：克隆代码 + 安装依赖（5 分钟）

```bash
git clone https://github.com/benkya/nlaut.git
cd nlaut

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装框架 + 开发依赖
pip install -e ".[dev]"

# 安装 Playwright 浏览器驱动（Web 测试通道用）
playwright install chromium
```

**依赖清单**（`pyproject.toml` 自动管理）：

| 包 | 用途 | 备注 |
| --- | --- | --- |
| `laya-mlx` ≥0.2.0 | Laya 语义判定引擎（3°） | Apple Silicon only |
| `mlx-vlm` ≥0.7.2 | VLM 视觉判定引擎（2°） | Apple Silicon only |
| `mlx-lm` ≥0.31.3 | MLX 基础库 | Apple Silicon only |
| `playwright` ≥1.63.0 | Web 自动化执行器 | 需额外 `playwright install` |
| `httpx` ≥0.27 | LLM API 执行器 | OpenAI 兼容 |
| `pydantic` ≥2.7 | IR 数据模型 | |
| `PyYAML` ≥6.0 | 用例 IR 文件格式 | |
| `modelscope` ≥1.40.1 | VLM 权重下载（国内镜像） | 仅安装时用 |
| `hf-transfer` ≥0.1.9 | HuggingFace 加速下载 | 仅安装时用 |

---

## 第 2 步：下载本地判定模型权重（10-20 分钟，一次性）

框架有两个**本地判定模型**，推理全部跑在你机器的 M 芯片上，不连任何远程服务。

### 一键下载

```bash
# 如果 HuggingFace 直连不通（国内常见），先开代理
export https_proxy=http://127.0.0.1:7897
export http_proxy=http://127.0.0.1:7897

# 一键下载两个权重（共 ~3.5GB）
bash scripts/setup_models.sh
```

### 下载的权重

| 模型 | 用途 | 大小 | 位置 | 来源 |
| --- | --- | --- | --- | --- |
| **Qwen2.5-VL-3B-Instruct-4bit** | VLM 视觉判定（2°）：截图 yes/no | 2.9GB | `~/.cache/nlaut-models/` | ModelScope（国内直连）或 HuggingFace（走代理） |
| **laya-multilingual-mlx** | Laya 语义判定（3°）：noul/choice/score | 614MB | `~/.cache/huggingface/hub/` | HuggingFace（走代理） |

下载完后**永久离线运行**，不再需要网络。

### 手动下载（脚本失败时）

```bash
# VLM — 方式一：ModelScope（国内推荐，直连快）
python3 -c "
from modelscope import snapshot_download
snapshot_download('Qwen/Qwen2.5-VL-3B-Instruct', cache_dir='$HOME/.cache/modelscope')
"
# 然后复制到框架期望的位置
mkdir -p ~/.cache/nlaut-models/Qwen2.5-VL-3B-Instruct-4bit
cp -r ~/.cache/modelscope/Qwen/Qwen2.5-VL-3B-Instruct/* ~/.cache/nlaut-models/Qwen2.5-VL-3B-Instruct-4bit/

# VLM — 方式二：HuggingFace（需代理）
python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('Qwen/Qwen2.5-VL-3B-Instruct-4bit', local_dir='$HOME/.cache/nlaut-models/Qwen2.5-VL-3B-Instruct-4bit')
"

# Laya — 只能从 HuggingFace（需代理）
python3 -c "
import laya_mlx
laya_mlx.load('aac6fef/laya-multilingual-mlx')
"
```

### 权重怎么被加载的（代码级说明）

```python
# === Laya 语义判定（src/nlaut/judge/laya.py）===
DEFAULT_MODEL = "aac6fef/laya-multilingual-mlx"
import laya_mlx
agent = laya_mlx.load(self.model_id)
# laya_mlx.load 先查本地 HF 缓存，有就直接加载，不发网络请求
# agent.system_one(state, questions) 做一次前向传播，0 输出 token

# === VLM 视觉判定（src/nlaut/judge/vlm_mlx.py）===
DEFAULT_MODEL = Path.home() / ".cache/nlaut-models/Qwen2.5-VL-3B-Instruct-4bit"
from mlx_vlm import load
self._model, self._processor = load(self.model_path)
# mlx_vlm.load 直接读本地目录，不发网络请求
# generate() 只生成 8 个 token（yes/no），温度恒 0
```

**两个都是纯本地推理**：通过 Apple MLX 框架在 M 芯片上跑，数据不出本机。

---

## 第 3 步：配置 API Key（2 分钟）

```bash
# 复制模板
cp .env.example ~/.hermes/.env

# 编辑填入你要测的大模型 key
vi ~/.hermes/.env
```

`.env.example` 内容（按需配，不测的模型不用填）：

```bash
# 阿里云百炼（glm-5.2/glm-5.3/glm-5/qwen3.7-max 共用）
CUSTOM_API_KEY=

# DeepSeek 官方 API
DEEPSEEK_API_KEY=

# 阿里 DashScope
DASHSCOPE_API_KEY=

# 内网模型（按实际地址配）
DEEPSEEK_V4_FLASH_KEY=
QWEN38_FLASH_KEY=

# 飞书集成（可选）
FEISHU_APP_ID=
FEISHU_APP_SECRET=
```

**key 安全三原则**：
1. key 只存 `~/.hermes/.env`（仓库外），不进 git（`.gitignore` 已排除 `.env`）
2. 运行时经环境变量注入子进程（防 `ps` 泄漏）
3. 报告/表格中一律打码 `key:***后4位`

---

## 第 4 步：验证安装（1 分钟）

```bash
# ① 元测试全绿（99 项，不依赖权重，秒级）
python -m pytest -q

# ② 真实 MLX 推理回归（需第 2 步权重已下载）
python -m pytest -m mlx -v

# ③ 跑一条 LLM 能力测试（需第 3 步 key 已配）
set -a; source ~/.hermes/.env; set +a
python -m nlaut.cli --model glm-5.2 --api-only --case tc_d04_p0_001

# ④ 统一报告（对话能力 + 4P12S 合并）
python scripts/combined_report.py --model glm-5.2
```

全部通过 = 安装成功。

---

## 常见问题

### Q1: `pip install` 报 mlx 相关包安装失败

**A**: mlx 系列包只支持 Apple Silicon（arm64）。检查 `uname -m` 是否为 `arm64`。如果是 Intel Mac 或 Linux/Windows，这些包装不上——框架的其他功能（确定性断言、LLM API 测试、Web 测试）仍可用，但 Laya/VLM 判定不可用。

### Q2: `setup_models.sh` 下载 VLM 权重失败

**A**: 脚本先试 ModelScope（国内直连），失败后试 HuggingFace（需代理）。如果两个都失败：
1. 确认代理可用：`curl -x http://127.0.0.1:7897 -sI https://huggingface.co`
2. 手动下载（见上方「手动下载」节）

### Q3: Laya 权重下载失败

**A**: Laya 只能从 HuggingFace 下载（`aac6fef/laya-multilingual-mlx`）。确保代理可用，然后：
```bash
export https_proxy=http://127.0.0.1:7897
python3 -c "import laya_mlx; laya_mlx.load('aac6fef/laya-multilingual-mlx')"
```

### Q4: 不想下载权重，能用框架吗？

**A**: 能。用 `--no-vlm` 跳过 VLM 判定，Laya 会在首次调用时报 `FileNotFoundError`——此时所有 AI 判定转 `human_review`。确定性断言（1°）不受影响，大部分 LLM 测试用例仍可正常跑。

### Q5: 内网模型连接失败

**A**: 内网地址（如 `10.62.64.38`）需要绕过系统代理。框架的 API 通道已设 `trust_env=False`，但如果系统级代理拦截了内网 IP，在环境变量里加：
```bash
export NO_PROXY=127.0.0.1,localhost,10.62.64.38
```

### Q6: 飞书链路怎么配

**A**: 飞书集成是可选功能。需要：
1. 在[飞书开放平台](https://open.feishu.cn)创建应用，获取 App ID 和 App Secret
2. 填入 `~/.hermes/.env`
3. 给应用授权：bitable 读写 + 消息发送权限
4. cron 脚本模板在 `~/.hermes/scripts/nlaut_feishu_scan.sh`（不在仓库里，凭据在仓库外）

详见 [飞书集成 Wiki](https://github.com/benkya/nlaut/wiki/Feishu-Integration)。

---

## 环境变量速查

| 变量名 | 用途 | 必填 | 来源 |
| --- | --- | --- | --- |
| `CUSTOM_API_KEY` | 阿里云百炼（glm/qwen 系列） | 按需 | [百炼控制台](https://bailian.console.aliyun.com) |
| `DEEPSEEK_API_KEY` | DeepSeek 官方 API | 按需 | [DeepSeek 平台](https://platform.deepseek.com) |
| `DASHSCOPE_API_KEY` | 阿里 DashScope | 按需 | [DashScope](https://dashscope.aliyun.com) |
| `DEEPSEEK_V4_FLASH_KEY` | 内网 DeepSeek-V4-Flash | 按需 | 内网运维提供 |
| `QWEN38_FLASH_KEY` | 内网 Qwen3.8-Flash-Next | 按需 | 内网运维提供 |
| `FEISHU_APP_ID` | 飞书应用 ID | 可选 | 飞书开放平台 |
| `FEISHU_APP_SECRET` | 飞书应用密钥 | 可选 | 飞书开放平台 |
| `https_proxy` | HF 权重下载代理 | 可选 | 本机代理地址 |
| `NO_PROXY` | 内网地址绕代理 | 可选 | 内网 IP 段 |

---

## 卸载

```bash
# 删权重
rm -rf ~/.cache/nlaut-models/
rm -rf ~/.cache/huggingface/hub/models--aac6fef--laya-multilingual-mlx/

# 删项目
rm -rf ~/workspace/nlaut/

# 删环境变量
rm ~/.hermes/.env
```
