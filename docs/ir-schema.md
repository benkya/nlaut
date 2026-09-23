# IR Schema（cases/*.yaml）

权威定义在 `src/nlaut/ir/model.py`（Pydantic）。本页是人读版。

```yaml
id: tc_login_001              # 必填，^tc_[a-z0-9_]+$
title: 登录-错误密码-停留登录页并提示
source: "口述: 测一下登录，密码错误的情况"   # 必填，原始输入文本（追溯锚点）
req_ref: null                 # 可选，需求编号
priority: P0                  # P0|P1|P2，默认 P1
channel: web                  # web | electron
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

## 步骤类型（steps）

| action | 字段 | 说明 |
| --- | --- | --- |
| `nav` | path | 导航（web）/窗口切换（electron） |
| `fill` | selector, value | 填充；`$var` 引用 data_ref 数据集字段 |
| `click` | selector | 点击 |
| `wait_visible` | selector, timeout_ms=5000 | 等待元素可见（禁止 sleep） |

## 断言类型（assertions）

| kind | judge 引擎 | 专有字段 |
| --- | --- | --- |
| `text_visible` | deterministic | selector, expected, negated |
| `visual_state` | vlm（mlx-vlm） | prompt, threshold=0.9 |
| `noul` / `choice` / `score` | systemone（laya-mlx） | question, options(choice 必填), threshold |

## 规则

1. `source` 非空——任何用例必须能追溯到输入文本。
2. `choice` 断言必须提供 `options`（校验器强制）。
3. 未列出的 action/kind 一律校验失败（Pydantic 判别联合）。
4. IR 是唯一事实源；`gen/` 代码永远可由 IR 重建。
