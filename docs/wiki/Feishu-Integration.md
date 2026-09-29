# 飞书链路集成

## 概览

```
飞书口述表 → cron 5 分钟扫描 → 识别口述（模型名/地址/key）
→ 桥接（feishu_bridge.py）生成 IR 草案 → 写入 cases/ + git
→ 「已确认」状态触发执行 → 三级判定 → HTML 报告
→ 报告上传飞书群文件 → 表格写回（状态+执行结果+报告位置）
```

## 口述表

- **地址**：https://gaientech.feishu.cn/base/Yfm7bQmFeaEwYxsLYJAcQozRnah?table=tblK7ug76gQyLpVe
- **11 个字段**：口述内容 / 被测系统 / 提出人 / 优先级 / 状态 / AI追问 / IR草案 / 审核意见 / 执行结果 / 报告链接 / 报告位置

### 状态流转

```
待生成 → [桥接自动生成草案+入库] → 待审核
待审核 → [用户改「已确认」] → 已确认
已确认 → [桥接执行] → 执行中 → 已完成/失败
需澄清 → [用户补信息后改回「待生成」] → 待生成
```

### 大模型能力测试：零动作直达

口述含「测试XXX模型能力」时，**跳过确认卡点直接执行**（普通 Web 用例保留确认卡点）。

#### 完整口述句式

```
测试<模型名>模型能力，模型地址是：<URL>，key是：<KEY>
```

- 模型名已在 `config/models.yaml` 预配置 → 只写模型名即可
- 临时模型 → 地址+key 直接口述携带，无需预配置
- 写「全量」→ 跑 P0+P1（81 条）；默认 P0（45 条）
- 写「重点测工具调用」→ 过滤 tool 维度

#### key 安全三原则

1. 口述里的 key **只存内存**（不落盘）
2. 经环境变量 `LLM_API_KEY` 注入子进程（防 `ps` 泄漏）
3. 写表格/报告一律打码 `key:***后4位`

## 通知群

- **群名**：「nlaut 测试自动化通知」（公开群，搜索加入）
- **chat_id**：`oc_f0ad8ca94fed6bcb6a1990d0889617bd`
- 报告以**群文件**形式发送（非云文档，避免 `drive:drive` 权限）

## cron 脚本

`~/.hermes/scripts/nlaut_feishu_scan.sh`（凭据在仓库外）：
- 每 5 分钟调 `nlaut.feishu_bridge --once`
- 环境变量注入 `FEISHU_APP_ID/SECRET` + `CUSTOM_API_KEY`
- Mac 合盖即停摆（cron 不唤醒睡眠机器）

## 桥接代码

`src/nlaut/feishu_bridge.py`（项目最大单文件）：
- 模板库（4 个 Web 场景 + LLM 批次识别）
- `draft_from_verbal()` → 生成 IR 草案
- `_run_llm_batch()` → 零动作执行 LLM 批次
- `publish_report()` → 报告上传群文件
- `_mask_key()` → key 打码
