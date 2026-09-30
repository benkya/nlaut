# 大模型上线测试 SOP（nlaut v0.2.9）

> **适用场景**：公司 AI Agent 应用每次接入新大模型，上线前的标准化能力验证。
> **工具**：nlaut 框架（`~/workspace/nlaut`）· 93 条标准用例 · 18 维度 · 3 优先级 + 4P12S 交付能力探针
> **本文档依据**：glm-5.2、Qwen3.8-Flash-Next、DeepSeek-V4-Flash 三次真实上线测试的全流程实操（2026-09-24/29）

---

## 一、总览：一条命令的测试闭环

```
注册模型（1 分钟）→ P0 批次（~5 分钟，自动）→ 失败定性（自动+人工仲裁）
→ 上线决策（P0 100% 通过 + 风险项确认）
```

解决的问题：
1. **测试人员时间占用** → 全量 P0 自动执行 + 自动判定，人工只处理仲裁队列（通常 ≤2 条）
2. **随机测试遗漏** → 88 条标准用例统一尺子，任何模型同一套基准
3. **判定口径因人而异** → 三级判定漏斗（确定性优先 → AI 判定兜底 → 置信度路由转人工）

## 二、第一步：注册新模型（1 分钟）

在 `config/models.yaml` 的 `models:` 节追加一个条目：

```yaml
  <你的模型短名>:
    provider: internal          # internal / aliyun_maas / deepseek 等
    base_url: "http://<内网地址>:<端口>/inference/v1"   # 注意不含 /chat/completions
    api_key_env: "你的KEY环境变量名"                      # key 存 ~/.hermes/.env，不进 git
    model_id: "服务端真实模型名"                          # 如 Qwen3.8-Flash-Next
    type: local_deployment      # 或 commercial_api
    supports_streaming: true
    supports_function_calling: false  # 未经探测时标 [推测]
    context_window: 32768            # 未经确认时标 [推测]
```

然后写入 key：

```bash
echo "你的KEY环境变量名=<apikey>" >> ~/.hermes/.env
```

**冒烟验证**（30 秒，确认连通 + 响应形态）：

```bash
cd ~/workspace/nlaut && set -a; source ~/.hermes/.env; set +a
.venv/bin/python -m nlaut.cli --model <短名> --api-only \
    --case tc_d04_p0_001 --report /tmp/smoke.html
```

## 三、第二步：P0 全量批次（~5 分钟）

```bash
.venv/bin/python -m nlaut.cli --model <短名> --api-only --priority P0 \
    --report artifacts/<短名>_p0.html
```

产出：
- HTML 报告（自包含，可直接发群/邮件）：每条用例的判定引擎、置信度、耗时
- `artifacts/session/run.jsonl` 全程留痕（append-only，可回放审计）

## 四、第三步：失败定性（关键环节）

**失败 ≠ 模型有问题。** 两次真实测试的失败分布：

| 定性类别 | 含义 | 处理 |
|---------|------|------|
| **A 框架/判定缺陷** | 断言或判定引擎 bug 造成假失败 | 修框架（历史案例：JSON 数组形态误判、markdown 围栏未剥离） |
| **B 用例资产缺陷** | 用例本身不可判定（占位符数据、断言描述化） | 修用例（历史案例：prompt 写"[插入文章]"没给真实文章） |
| **C 模型真实缺陷** | 模型行为确实不符合预期 | 记录缺陷，评估是否阻塞上线 |
| **D flaky（不稳定）** | 创意类输出波动，重放通过 | 不阻塞上线，记观察项 |

**定性方法**：看报告里失败用例的 raw 判定明细 + 独立重放该用例 3 次：

```bash
.venv/bin/python -m nlaut.cli --model <短名> --api-only --case <失败用例id> --report /tmp/retry.html
```

- 重放通过 → D（flaky）
- 重放失败且响应内容正确 → A/B（查断言设计）
- 重放失败且响应内容确实错误 → C（真实缺陷）

**need_review 状态**：Laya AI 判定置信度 0.5-0.9 的正确路由行为，人工看报告里的证据包（prompt + 响应 + 置信度）裁决，不是失败。

## 五、第四步：上线决策

### 方式一：统一报告（v0.2.9 推荐）

```bash
.venv/bin/python scripts/combined_report.py --model <短名> --report artifacts/report_combined_<短名>.html
```

报告顶部自动给出 **上线结论卡**：

| 条件 | 结论 |
|------|------|
| P0 ≥95% **且** 4P12S ≥80% | ✅ 推荐上线 |
| P0 ≥90% **且** 4P12S ≥60% | ⚠️ 有条件上线（修复/观察失败项后上线） |
| 否则 | ❌ 不建议上线 |

失败和转人工的用例可折叠查看**请求（system+user 消息）和模型返回全文**，便于人工定性。

### 方式二：分步决策（v0.2.7 传统）

| 判据 | 标准 |
|------|------|
| P0 通过率 | **100%**（need_review 人工裁决后计） |
| flaky 项 | 不阻塞，进观察清单 |
| 响应延迟 | 参考值：商用 API P95 ≤5s / 内网 ≤10s（性能用例 D18 量化） |
| 安全用例 | D07 全部 4 条必须通过（拒绝炸弹/隐私/SQL注入/无偏见） |

决策通过后，把本次报告归档：`artifacts/report_combined_<短名>.html` + git 提交，作为该模型的基线。下次模型升级重跑同一套，diff 基线即回归测试。

## 六、实测数据参考（三次真实上线的基线）

| 指标 | glm-5.2（商用API） | Qwen3.8-Flash-Next（内网） | DeepSeek-V4-Flash（内网） |
|------|-------------------|---------------------------|---------------------------|
| P0 通过率 | 40/41（97.6%） | 40/41（97.6%） | 41/46（89.1%） |
| 4P12S 通过率 | — | — | 8/9（88.9%） |
| 综合结论 | — | — | ⚠️ 有条件上线 |
| 平均延迟 | 17.7s | **5.5s（快 3.2 倍）** | 8.4s |
| 差异项 | tc_ctx_p0_001 转人工（laya conf 0.83） | tc_creative_p0_001 flaky（重放 3/3 过） | tc_d06/instruct 格式偏差 + step10 E2E 转人工 |
| 特性 | JSON 输出对象形态 | JSON 输出数组形态（判定引擎已容错） | 推理模型（reasoning_content + 思考泄漏） |

## 七、已知坑（踩过的，写死在框架里）

1. 内网 IP 绕系统代理：api 通道 `trust_env=False` 勿改回
2. shell 先 source key 否则报缺 Key
3. 大模型测试必带 `--api-only`（否则拉起浏览器跑 UI 用例）
4. 创意类失败先重放再定性
5. JSON 数组形态（Qwen 风格）非缺陷，判定引擎已容错

## 八、P95 TTFT 维度（v0.2.7 新增）

**背景**：单次 `response_ttft` 在 3000ms 阈值附近偶发 flaky（实测 qwen3.8-flash-next 单次跑 1.2s、2.7s、5.8s 各一次）。
业务调用方应按 P95 监控而非单次断言。

**用法**：用例 yaml 声明 `repeat: N`（1-20）+ `response_ttft_p95` 断言：

```yaml
steps:
- action: api_stream
  prompt: 用一句话介绍 Python。
  repeat: 5   # 跑 5 次取 P95
assertions:
- kind: response_ttft_p95
  max_p95_ms: 3000
```

**算子**：`nlaut.metrics.percentile.p95(samples)` 排序取最末位（N >= 1 时等价 ceil(N*0.95)-1 = N-1）。
**Evidence 字段**：`ttft_samples: list[float]` + `ttft_p95_ms: float | None`（`api_stream repeat=N` 自动填充）。
**报告渲染**：laya `render_state` 输出 `P95 TTFT: <value>ms（N 次采样）` + `最近一次 TTFT: <value>ms`。
**判定缺失降级**：ttft_samples 与 ttft_p95_ms 都缺时 value=0.5/conf=0.5 转人工（同 response_ttft）。

**示例 baseline**（qwen3.8-flash-next，2026-09-24 实测）：5 次采样 P95=990ms，单次最大值~1.5s。

## 九、扩展

- **P1 批次**：`--priority P0,P1`（36 条更深能力用例）
- **统一报告**：`scripts/combined_report.py --model <名>`（对话能力 + 4P12S 交付能力合并，含上线结论卡 + 失败用例请求/返回展示）
- **模型对比**：`scripts/compare_models.py` 生成两模型 diff 报告
- **新用例来源**：Excel 用例集（桌面 `大模型测试用例集_v1_20260924.xlsx`）→ `scripts/excel_to_ir.py` 批量转 IR
- **团队协作**：飞书口述表 → 桥接自动生成 IR → 执行 → 报告发群（nlaut 飞书链路已闭环）
- **4P12S 交付能力探针**：`scripts/four_p12s_probe.py --model <名>`（9 步端到端：需求→PRD→设计→编码→测试，含真实 pytest 门禁 + 思考泄漏检测）
