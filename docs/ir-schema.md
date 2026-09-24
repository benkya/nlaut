# IR Schema（cases/*.yaml）

权威定义在 `src/nlaut/ir/model.py`（Pydantic）。本页是人读版。

```yaml
id: tc_login_001              # 必填，^tc_[a-z0-9_]+$
title: 登录-错误密码-停留登录页并提示
source: "口述: 测一下登录，密码错误的情况"   # 必填，原始输入文本（追溯锚点）
req_ref: null                 # 可选，需求编号
priority: P0                  # P0|P1|P2，默认 P1
channel: web                  # web | electron | api
quad:                         # 四元组
  actor: 登录页
  action: 提交错误密码
  object: 登录表单
  preconditions: ["用户 $user 存在且启用"]
data_ref: login_invalid_pwd   # 可选，data/ 工厂 tag；用例不内嵌数据
steps:                        # 声明式动作，min 1 条
  - {action: nav, path: "/login"}
  - {action: fill, selector: "#username", value: "$user"}
  - {action: click, selector: "button[type=submit]"}
  - {action: wait_visible, selector: ".error-msg", timeout_ms: 5000}
assertions:                   # min 1 条，每条声明判定级别
  - kind: text_visible        # 确定性：selector 文案包含
    selector: ".error-msg"
    expected: "密码错误"
  - kind: visual_state        # VLM：结构化提问 + threshold
    prompt: "页面是否呈现登录失败且仍停留在登录页？"
    threshold: 0.9
  - kind: noul                # System One：0-1 概率题
    question: "当前失败归因是否为'产品缺陷'？"
    threshold: 0.9
postconditions:
  - cleanup_tag: TEST_AUTO_   # 数据还原钩子
```

## 大模型 API 用例示例（v0.2.0）

```yaml
id: tc_d04_p0_001
title: 多步算术
source: "大模型测试用例集 v1 D04-P0-001：验证模型能正确完成多步算术（折扣+优惠券）"
req_ref: "D04-P0-001"
priority: P0
channel: api                  # API 通道：HTTP 调用 LLM
quad: {actor: 大模型, action: 数学计算, object: 数学问题}
data_ref: d04_multi_step      # prompt 走数据工厂，用例不内嵌
steps:
  - {action: api_call, prompt: "$prompt"}
assertions:
  - kind: response_contains   # 确定性：关键词匹配
    keywords: ["130"]
    min_match: 1
  - kind: noul                # AI 判定兜底
    question: "模型是否正确计算出最终价格130元（200×0.8-30=130）？只答 yes 或 no。"
    threshold: 0.9
```

## 步骤类型（steps）

| action | 字段 | 通道 | 说明 |
| --- | --- | --- | --- |
| `nav` | path | web/electron | 导航（web）/窗口切换（electron） |
| `fill` | selector, value | web | 填充；`$var` 引用 data_ref 数据集字段 |
| `click` | selector | web | 点击 |
| `select_option` | selector, value | web | 下拉选择 |
| `wait_visible` | selector, timeout_ms=5000 | web | 等待元素可见（禁止 sleep） |
| `api_call` | prompt, system_prompt? | api | 单轮 LLM 调用；`$var` 引用数据集字段 |
| `api_followup` | prompt | api | 多轮对话追加用户消息（复用上文 messages） |
| `api_tool_call` | prompt, tools | api | 带工具定义调用（OpenAI function schema） |
| `api_stream` | prompt, system_prompt? | api | 流式调用（stream=true，收完比对） |

## 断言类型（assertions）

| kind | judge 引擎 | 专有字段 | 适用 |
| --- | --- | --- | --- |
| `text_visible` | deterministic | selector, expected, negated | DOM 文案 |
| `attribute` | deterministic | selector, name, expected, negated | DOM 属性 |
| `visual_state` | vlm（mlx-vlm） | prompt, threshold=0.9 | 页面视觉状态 |
| `noul` / `choice` / `score` | systemone（laya-mlx） | question, options(choice 必填), threshold | 语义判定/归因/量表 |
| `response_exact` | deterministic | expected, case_sensitive | L1 精确匹配（数学/事实） |
| `response_contains` | deterministic | keywords, min_match=1 | L2 关键词匹配 |
| `response_not_contains` | deterministic | forbidden | L4 反向验证（安全性） |
| `response_json_schema` | deterministic | required_fields, expected_values | L3 JSON 结构 |
| `tool_call_params` | deterministic | expected_function, expected_params | 工具调用参数 |
| `response_time` | deterministic | max_seconds | 性能延迟断言 |

## 规则

1. `source` 非空——任何用例必须能追溯到输入文本。
2. `choice` 断言必须提供 `options`（校验器强制）。
3. 未列出的 action/kind 一律校验失败（Pydantic 判别联合）。
4. IR 是唯一事实源；`gen/` 代码永远可由 IR 重建。
5. **API 用例不内嵌 prompt 值**——prompt 走 `data_ref` 数据工厂（`$var` 引用），保证模型无关。
6. 确定性断言（L1-L4）优先，AI 判定（noul/choice/score）只兜底断言不可表达的场景。

## 大模型测试执行（v0.2.0）

```bash
# 单条 P0
.venv/bin/python -m nlaut.cli --model deepseek-chat --api-only --case tc_d04_p0_001

# 全量 API 通道（报告自动生成）
.venv/bin/python -m nlaut.cli --model qwen-max --api-only --report artifacts/llm_report.html

# 模型配置预设（config/models.yaml）
.venv/bin/python -m nlaut.cli --model deepseek-chat --api-only   # base_url 从预设读取
```

模型配置：`config/models.yaml`（新模型接入只需追加一个条目）。
API key 优先级：CLI `--api-key` > 环境变量 `LLM_API_KEY` > models.yaml `api_key_env` 指定的变量。
