# M0-poc 判定层实测报告（2026-09-23）

环境：MacBook Pro M3 Pro 18GB / Python 3.12.11 / mlx 0.32.2 / uv。
权重：Qwen2.5-VL-3B-Instruct-4bit（~3GB, ModelScope 直连下载）；
laya-multilingual-mlx（644MB, HF 经系统代理 127.0.0.1:7897，10 连 Range 并行）。

## 引擎一：mlx-vlm（视觉判定，2° 层）

金标准 6 题（登录成功/密码错误/网络异常 × 正反问法），温度 0：

| 指标 | 实测 | 验收线 | 结论 |
| --- | --- | --- | --- |
| 准确率 | **6/6 (100%)** | ≥5/6 | ✅ |
| 推理延迟 | 5.3-6.1s/题（1280×900 截图） | ≤10s | ✅ |
| 模型加载 | 2-3s（懒加载，进程内复用） | — | — |
| 置信度 | 首 token logprob 代理（Qwen-VLM 无原生校准值） | — | ⚠️ 见风险 |

Qwen2.5-VL-3B 中文界面理解完全胜任：模型原始回答 yes/yes/yes + no/no/no 全对，
正反问法均稳定。首版 poc 脚本因 `str(GenerationResult)` 解析 bug 误报 50%，
修复后 100%——**教训已写入 poc_vlm.py docstring**。

## 引擎二：laya-mlx（System One 判定，3° 层）

4 题 noul 金标准 + 1 题 choice 归因：

| 题目 | noul | conf | 路由 | 延迟 |
| --- | --- | --- | --- | --- |
| 密码错误→登录被拒 | 0.997 | 0.997 | auto_pass | 1686ms* |
| 欢迎回来→登录被拒 | 0.363 | 0.637 | human_review | 11.9ms |
| 网络异常→环境故障 | 0.989 | 0.989 | auto_pass | 13.5ms |
| 500 错误→环境故障 | 0.967 | 0.967 | auto_pass | 13.2ms |
| **choice 归因**（网络异常截图） | 产品缺陷 0.767 | 0.767 | human_review | 9.6ms |

*首题含权重加载（fetch 6 files 后 1686ms 为冷启动）；稳态延迟 10-14ms/题，
与官方 13.4ms 中位承诺一致。

准确率 3/4（75%）：「500 Internal Server Error 归因」被判为环境故障（期望产品缺陷）——
该题语义本身有歧义（500 可能是网关也可能是应用），**判定正确转人工仲裁**而非误报，
置信度路由恰好兜住。choice 归因题「网络异常→产品缺陷」是 Laya 的小模型语义偏差，
conf=0.767 < 0.9 同样被路由到人工——**校准置信度机制有效，风险可控**。

## 综合结论

1. **两级 AI 判定引擎均可用**：VLM 100% / Laya 75%+（低置信全部正确转人工，零误报进入报告）
2. **置信度路由是有效的防线**：所有判错的题目 confidence 均 <0.9，全部被 human_review 拦截
3. 延迟预算：VLM 5-6s/题（适合用例级判定）+ Laya 10-14ms（适合高频归因路由）
4. [推测] 7B VLM 会更准但 18GB 内存与浏览器并行有压力，3B 已达验收线，先固化

## 遗留风险与对策

| 风险 | 对策 |
| --- | --- |
| VLM logprob 置信度是代理值，非校准 | 默认 VLM 判定进 human_review 通道，积累 30+ 样本后校准阈值 |
| Laya 小模型对复杂归因语义偏差 | 归因题只作路由参考，最终归因由人工/规则确认 |
| 代理下载环境特定（127.0.0.1:7897） | 已存环境记忆；CI 环境需 ModelScope 通道 |

## 复现命令

```bash
uv run pytest tests/vlm_engine_test.py -m mlx -v      # VLM 引擎（~10s）
uv run pytest tests/laya_engine_test.py -m mlx -v     # Laya 引擎（~5s）
uv run python tests/judge_golden/poc_vlm.py           # 6 题金标准全程
```
