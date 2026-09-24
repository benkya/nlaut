# 大模型上线测试 SOP（nlaut v0.2.1）

> **适用场景**：公司 AI Agent 应用每次接入新大模型，上线前的标准化能力验证。
> **工具**：nlaut 框架（`~/workspace/nlaut`）· 88 条标准用例 · 18 维度 · 3 优先级
> **本文档依据**：glm-5.2 与 Qwen3.8-Flash-Next 两次真实上线测试的全流程实操（2026-09-24）

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

| 判据 | 标准 |
|------|------|
| P0 通过率 | **100%**（need_review 人工裁决后计） |
| flaky 项 | 不阻塞，进观察清单 |
| 响应延迟 | 参考值：商用 API P95 ≤5s / 内网 ≤10s（性能用例 D18 量化） |
| 安全用例 | D07 全部 4 条必须通过（拒绝炸弹/隐私/SQL注入/无偏见） |

决策通过后，把本次报告归档：`artifacts/<短名>_p0.html` + git 提交，作为该模型的基线。下次模型升级（如 Qwen3.8-Flash-Next → Next2）重跑同一套，diff 基线即回归测试。

## 六、实测数据参考（两次真实上线的基线）

| 指标 | glm-5.2（商用API） | Qwen3.8-Flash-Next（内网） |
|------|-------------------|---------------------------|
| P0 通过率 | 40/41（97.6%） | 40/41（97.6%） |
| 平均延迟 | 17.7s | **5.5s（快 3.2 倍）** |
| 差异项 | tc_ctx_p0_001 转人工（laya conf 0.83） | tc_creative_p0_001 flaky（重放 3/3 过） |
| 特性 | JSON 输出对象形态 | JSON 输出数组形态（判定引擎已容错） |

## 七、已知坑（踩过的，写死在框架里）

1. **内网 IP 必须绕过系统代理**：api 通道已内置 `trust_env=False`，勿改回
2. **shell 必须先 source key**：`set -a; source ~/.hermes/.env; set +a`，否则报"缺少 API Key"
3. **`--api-only` 会过滤 web 用例**：跑大模型测试必须带（否则会拉起浏览器跑 UI 用例）
4. **key 永远不进 git**：`config/models.yaml` 只写环境变量名，值在 `~/.hermes/.env`
5. **创意类用例天然波动**：失败先重放 3 次再定性，勿直接判模型缺陷

## 八、扩展

- **P1 批次**：`--priority P0,P1`（36 条更深能力用例）
- **模型对比**：`scripts/compare_models.py` 生成两模型 diff 报告
- **新用例来源**：Excel 用例集（桌面 `大模型测试用例集_v1_20260924.xlsx`）→ `scripts/excel_to_ir.py` 批量转 IR
- **团队协作**：飞书口述表 → 桥接自动生成 IR → 执行 → 报告发群（nlaut 飞书链路已闭环）
