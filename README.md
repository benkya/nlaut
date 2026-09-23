# nlaut — 自然语言自动化测试框架

自然语言口述 → 结构化用例 IR（`cases/`，唯一事实源）→ **真浏览器执行（Playwright 驱动系统 Chrome）**
→ **三级判定**（1°确定性断言 → 2°VLM 视觉（本地 mlx-vlm）→ 3°Laya System One（本地 laya-mlx））
→ 置信度路由（≥0.9 自动 / 0.5–0.9 人工仲裁）→ **自包含 HTML 报告**（截图+置信度+溯源内嵌）。

## MVP 已可用（2026-09-23 实测）

一条命令端到端（自动拉起本地演示被测系统 + 真实密码校验 + 真实 AI 推理）：

```bash
cd ~/workspace/nlaut
.venv/bin/python -m nlaut.cli
```

实测输出（M3 Pro，全程 ~20s）：

```
[need_review] tc_login_001  登录-错误密码-停留登录页并提示  (11.3s)
    - text_visible   deterministic  val=1.0 conf=1.0 -> auto_pass
    - visual_state   mlx-vlm        val=1.0 conf=1.0 -> auto_pass
    - noul           laya           val=0.844 conf=0.845 -> human_review   ← 保守路由，正确
[     passed] tc_login_002  登录-正确密码-进入工作台  (8.1s)
    - text_visible   deterministic  val=1.0 conf=1.0 -> auto_pass
    - visual_state   mlx-vlm        val=1.0 conf=1.0 -> auto_pass

通过 1/2 · 报告: artifacts/report.html   ← 打开即看（截图内嵌）
```

## 怎么用

### 最常用：口述让 AI 生成用例（不用写代码）

对 AI 工作分身说人话即可：`"帮我生成用例：连错五次密码，账号要锁住"`，
审核草案后说「入库执行」。完整协议见
**[docs/verbal-to-ir-protocol.md](docs/verbal-to-ir-protocol.md)**——团队成员照
该文档提供口述就能得到可执行用例。

### 命令行（已入库用例的日常执行）

| 场景 | 命令 |
| --- | --- |
| 跑全部（演示系统自动拉起） | `.venv/bin/python -m nlaut.cli` |
| 跳过 VLM（提速，只确定性+Laya） | `.venv/bin/python -m nlaut.cli --no-vlm` |
| 只跑某条 | `.venv/bin/python -m nlaut.cli --case tc_login_001` |
| 打你的真实被测系统 | `.venv/bin/python -m nlaut.cli --external --base-url http://你的地址` |
| 有头模式（看浏览器操作） | `.venv/bin/python -m nlaut.cli --headed` |
| 框架元测试 | `.venv/bin/python -m pytest`（48 项，~0.1s） |
| AI 引擎真实推理回归 | `.venv/bin/python -m pytest -m mlx -v`（需本机权重） |

### 加一条新用例（三步）

1. 写 IR：`cases/<分组>/tc_<功能>_<场景>.yaml`（模板抄 `cases/demo/tc_login_001.yaml`；
   `source` 必填=你的原始口述，追溯锚点）
2. 数据：`data/datasets.yaml` 加一个 tag（用例 `data_ref` 引用，禁止内嵌数据）
3. 跑：`.venv/bin/python -m nlaut.cli --case tc_<新id>`

### 判定级别怎么选（写断言时）

| 断言 kind | 引擎 | 适用 |
| --- | --- | --- |
| `text_visible` | 确定性（1°） | 文案/元素存在——**能用就用它**，零 AI 成本 |
| `visual_state` | VLM（2°） | 页面整体状态、渲染正确性（5-6s/题） |
| `noul` / `choice` / `score` | Laya（3°） | 概率判断/归因分类/量表（10-14ms/题） |

置信度路由：deterministic 直接裁决；AI 引擎 conf ≥0.9 自动、0.5–0.9 转人工
（报告里标 `need_review`，附完整证据）。

## 前置条件（本机已备齐）

- Python 3.12 venv（`.venv/`，uv 管理）+ playwright + mlx-vlm + laya-mlx
- 系统 Chrome（Playwright 直驱，无需下载浏览器）
- 模型权重（已下载）：`~/.cache/nlaut-models/Qwen2.5-VL-3B-Instruct-4bit`（3GB）+
  HF 缓存 `aac6fef/laya-multilingual-mlx`（644MB）
- 需联网下载模型时走系统代理 `127.0.0.1:7897`（HF 域名直连不通）

## 架构与路线图

六层架构、三级判定协议、金标准回归见 [docs/architecture.md](docs/architecture.md)。
口述→IR 生成协议见 [docs/verbal-to-ir-protocol.md](docs/verbal-to-ir-protocol.md)。
已完成：M0 骨架、M0-poc 判定层实测（VLM 6/6、Laya 误判全被路由拦截）、**MVP 端到端**、
口述→IR 生成协议（生成层为对话 AI，`pipeline/` 自动化为 M1 计划）。
未完成：M1 框架内自动生成、M2 IR→pytest 代码生成
（当前运行时直接解释 IR，无 gen/ 派生代码）、Electron 通道、RAG 历史用例库。

Agent 行为规范素材见 `docs/agent-conventions.md`（根 AGENTS.md 需本人执行
`cp docs/agent-conventions.md AGENTS.md` 启用）。

## 质量门禁

- 任何框架改动：`pytest` 全绿 + `ruff` 零告警
- 任何判定引擎变更：`pytest -m mlx`（金标准回归）必须通过
- 用例变更走 git（IR 即资产，PR 即人工审核）
