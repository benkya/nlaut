# 判定层金标准回归（Layer B）

本目录存放人工标注的截图集与期望判定。**任何判定引擎接入/变更必须通过本回归**，
否则 AI 误报直接进入业务报告——这是 L5 的门禁，禁止绕过。

## 结构

```
judge_golden/
├── golden.jsonl        # {image, question, kind, expected, engine}
├── images/             # 截图（3 场景 × 正反问法）
├── fixtures/pages/     # 截图对应的 HTML 源（Chrome --headless 生成，可复现）
└── poc_vlm.py / poc_laya.py   # M0-poc 实测脚本
```

## M0-poc 验收口径

| 引擎 | 题量 | 通过线 | 附加指标 |
| --- | --- | --- | --- |
| vlm (Qwen2.5-VL-3B-4bit) | 6 题（3 正 3 反） | 准确率 ≥5/6 | 单题推理 ≤10s，温度 0 |
| laya (multilingual) | 4 题（2 正 2 反） | 准确率 4/4 | confidence 语义可路由（≥0.9 auto / 0.5-0.9 human） |

扩充路径（M1）：每场景补边界样本（空表单/账号锁定/加载中），凑满 30 张。
