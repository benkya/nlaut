# P1: 代码审查场景优化方案

**版本**: v1.0  
**日期**: 2026-10-31  
**状态**: 设计中

---

## 一、问题定义

### 1.1 当前问题

代码审查场景（后 10 个样本）两个 Judge 表现都差：

| Judge 类型 | 一致率 | 误判数 |
|---|---|---|
| LLM Judge | 50% (5/10) | 5 |
| Agent-as-Judge | 50% (5/10) | 5 |

### 1.2 误判样本分析

| 样本 | 类型 | 金标准 | 两者判定 | 问题 |
|---|---|---|---|---|
| calibrate_022 | 安全-参数化查询 | True | False | 过严 |
| calibrate_024 | 性能-O(n)实现 | True | False | 过严 |
| calibrate_026 | 边界-None处理 | True | False | 过严 |
| calibrate_028 | 并发-线程安全 | True | False | 过严 |
| calibrate_030 | 资源-with语句 | True | False | 过严 |

**共同特点**：
- 金标准都是 True（正确实现）
- 两者都判为 False（过严）
- 说明：代码审查场景需要更细致的分析，而非简单判否

---

## 二、根因分析

### 2.1 为什么两个 Judge 都过严？

**原因 1：Prompt 缺少代码审查专业知识**

当前 Prompt 设计用于数据落库验证：
```
验证：模块 M 接入后任务数据真的落库了（表 task）
```

代码审查需要不同的视角：
- 安全性
- 性能
- 边界条件
- 并发安全
- 资源管理

**原因 2：缺少代码分析工具**

当前 Agent 工具只有：
- `query_db`：查询数据库
- `read_file`：读取文件
- `read_log`：读取日志
- `http_probe`：HTTP 探测

缺少：
- AST 分析
- 测试执行
- 安全扫描
- 性能分析

**原因 3：证据呈现方式**

代码审查需要：
- 代码结构分析
- 测试用例执行结果
- 安全扫描报告
- 性能指标

---

## 三、解决方案

### 3.1 代码审查专用工具

#### 工具 1：AST 分析（analyze_code_structure）

**功能**：解析代码结构，识别潜在问题

**输入**：
```json
{
  "code": "def search_user(name):\n    query = f\"SELECT * FROM users WHERE name='{name}'\"\n    return db.execute(query)",
  "check_type": "security"  // security / performance / boundary / concurrency / resource
}
```

**输出**：
```json
{
  "issues": [
    {
      "type": "sql_injection",
      "severity": "high",
      "line": 2,
      "description": "直接拼接用户输入到 SQL 语句，存在注入风险",
      "recommendation": "使用参数化查询"
    }
  ],
  "analysis": {
    "functions": ["search_user"],
    "parameters": ["name"],
    "external_calls": ["db.execute"]
  }
}
```

#### 工具 2：测试执行（execute_test_cases）

**功能**：运行测试用例，验证边界条件

**输入**：
```json
{
  "code": "def concat(a, b):\n    return a + b",
  "test_cases": [
    {"input": ["hello", "world"], "expected": "helloworld"},
    {"input": [null, "world"], "expected": null}
  ]
}
```

**输出**：
```json
{
  "results": [
    {"input": ["hello", "world"], "expected": "helloworld", "actual": "helloworld", "passed": true},
    {"input": [null, "world"], "expected": null, "actual": "TypeError", "passed": false}
  ],
  "summary": {
    "total": 2,
    "passed": 1,
    "failed": 1
  }
}
```

#### 工具 3：安全扫描（scan_security_issues）

**功能**：检测常见安全漏洞

**检测项**：
- SQL 注入
- XSS
- 命令注入
- 路径遍历
- 敏感信息泄露

**输入**：
```json
{
  "code": "def search_user(name):\n    query = f\"SELECT * FROM users WHERE name='{name}'\"\n    return db.execute(query)"
}
```

**输出**：
```json
{
  "vulnerabilities": [
    {
      "type": "sql_injection",
      "severity": "high",
      "line": 2,
      "description": "用户输入直接拼接到 SQL 语句",
      "cwe": "CWE-89"
    }
  ]
}
```

#### 工具 4：性能分析（analyze_performance）

**功能**：分析算法复杂度

**输入**：
```json
{
  "code": "def find_duplicates(arr):\n    result = []\n    for i in range(len(arr)):\n        for j in range(i+1, len(arr)):\n            if arr[i] == arr[j]:\n                result.append(arr[i])\n    return result"
}
```

**输出**：
```json
{
  "time_complexity": "O(n²)",
  "space_complexity": "O(n)",
  "issues": [
    {
      "type": "nested_loop",
      "severity": "medium",
      "description": "嵌套循环导致 O(n²) 复杂度，大数组性能不可接受",
      "recommendation": "使用哈希表优化到 O(n)"
    }
  ]
}
```

### 3.2 代码审查专用 Prompt

```
你是代码质量判定器（Code-as-Judge）。

审查维度：
1. **安全性**：是否存在注入、XSS、命令注入等漏洞
2. **性能**：算法复杂度是否可接受（O(1) < O(log n) < O(n) < O(n log n) < O(n²)）
3. **边界**：是否处理了空值、空列表、极端输入
4. **并发**：是否线程安全（竞态条件、死锁）
5. **资源**：是否正确管理资源（文件句柄、数据库连接）

判定规则：
- 有安全漏洞 → 判否
- 性能不可接受（O(n²) 或更差且输入规模大）→ 判否
- 未处理边界条件 → 判否
- 存在竞态条件 → 判否
- 资源泄漏 → 判否
- 其他情况 → 判是

工具使用：
1. 先用 analyze_code_structure 获取代码结构
2. 用 scan_security_issues 检查安全性
3. 用 analyze_performance 检查性能
4. 用 execute_test_cases 验证边界条件

输出格式：
judgement: true|false
理由: <基于你实际分析的结果，一句话说明>
CONFIDENCE: High|Medium|Low
```

---

## 四、实施方案

### 4.1 第一阶段：工具实现

| 工具 | 优先级 | 预估工时 | 实现方式 |
|---|---|---|---|
| execute_test_cases | P0 | 2 天 | Python AST + exec |
| scan_security_issues | P0 | 2 天 | 规则引擎 + bandit 集成 |
| analyze_code_structure | P1 | 3 天 | Python ast 模块 |
| analyze_performance | P1 | 3 天 | 静态分析 + 符号执行 |

### 4.2 第二阶段：Prompt 优化

- 设计代码审查专用 Prompt
- 针对每个维度设计检查规则
- 添加示例和反例

### 4.3 第三阶段：标定验证

- 使用后 10 个样本验证
- 目标：一致率 ≥ 85%

---

## 五、预期效果

| 维度 | 当前 | 目标 |
|---|---|---|
| 安全判定 | 50% | 90% |
| 性能判定 | 50% | 85% |
| 边界判定 | 50% | 85% |
| 并发判定 | 50% | 80% |
| 资源判定 | 50% | 90% |
| **整体** | **50%** | **85%** |

---

## 六、下一步行动

1. **立即**：设计代码审查工具接口
2. **本周**：实现 execute_test_cases 工具
3. **下周**：实现 scan_security_issues 工具
4. **持续**：迭代优化 Prompt

---

**文档版本**：v1.0  
**生成时间**：2026-10-31