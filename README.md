# nlaut — 自然语言自动化测试框架

自然语言输入 → 结构化用例 IR（`cases/`，唯一事实源）→ 派生 pytest 代码（`gen/`，可随时重建）
→ 双通道执行（Playwright / Electron CDP）→ 三级判定（确定性 → mlx-vlm 视觉 → Laya-MLX typed decision）
→ 带证据链的报告。

六层架构、路线图与判定协议见 [docs/architecture.md](docs/architecture.md)。
Agent 行为规范见 [AGENTS.md](AGENTS.md)（先读它）。

## 快速开始

```bash
uv sync        # 安装依赖（Python 3.11+）
make verify    # lint + 元测试
```

## 状态（M0 骨架）

- [x] IR 模型（Pydantic，`src/nlaut/ir/model.py`）+ 示范用例 `cases/demo/tc_login_001.yaml`
- [x] Judge 协议（`src/nlaut/judge/protocol.py`）+ 确定性判定 + 置信度路由
- [x] 会话日志（append-only JSONL）+ 证据存储
- [ ] M0 判定层 PoC：mlx-vlm / laya-mlx 实测（见 docs/architecture.md 路线图）
- [ ] M1 口述 → IR 生成闭环（pipeline/ 当前为桩）
- [ ] M2 IR → pytest/Playwright 代码生成（executor/ 当前为桩）
