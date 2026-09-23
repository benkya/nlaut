# 判定层金标准回归（Layer B）

本目录存放人工标注的截图集与期望判定。**任何判定引擎接入/变更必须通过本回归**，
否则 AI 误报直接进入业务报告——这是 L5 的门禁，禁止绕过。

结构（M0-poc 建立时生效）：

```
judge_golden/
├── golden.jsonl        # {image, question, expected_kind, expected_value, label}
└── images/             # 截图（登录成功/失败/边界各 10 张起步）
```

统计口径：准确率、校准曲线（置信度高但判错 = 校准失败，不允许上生产）。
