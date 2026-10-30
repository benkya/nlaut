# 真实组外模块接入设计（G-11 Agent-as-Judge 落地层）

> 版本：v1.0
> 日期：2026-10-30
> 定位：把 nlaut 的 Agent-as-Judge 从「夹具验证」推进到「真实模块接入」的**接入层契约**
> 责任边界：依据《质量责任边界图 v1.2》L5 组外集成层 + 《集成准入门禁判定项清单 v1.0》G-11

---

## 一、结论先行

**接入层已实现（`src/nlaut/judge/truth_source.py`），真实模块只需实现一个 `TruthSource`。**

核心设计取舍：

| 取舍 | 选择 | 理由 |
|---|---|---|
| 证据从哪来 | **接入方提供 `TruthSource`**，不改引擎 | 不同模块真值源形态未知（DB/HTTP/日志/MQ），不能硬编码 |
| 谁做判定 | **只有 judge 判定** | G-11 只判「接入后能否正常工作」，TruthSource 只取状态不做业务判断 |
| 只读约束 | **执行方保证 + SQL 源强制 SELECT 校验** | 「取证」绝不能变成「改动被测系统」 |
| 取证失败 | **返回带 error 的证据，不抛异常** | 「取不到证据」本身是判定所需的信息，不该中断流程 |
| 可审计 | **每份证据带 source + collected_at** | 人工仲裁时要能判断「这份证据可不可信」 |

---

## 二、接入契约

### 2.1 核心接口

```python
@runtime_checkable
class TruthSource(Protocol):
    name: str

    def snapshot(self, key: str) -> TruthEvidence:
        """取某个 key 的当前状态快照。

        key 的语义由接入方定义（表名 / 接口路径 / 日志标识）。
        失败时返回带 error 的 TruthEvidence，不抛异常——
        让 judge 能把「取不到证据」当作一种证据来处理。
        """
```

### 2.2 证据结构

```python
@dataclass
class TruthEvidence:
    key: str          # 证据键（"task" / "api/task" / "app_log"）
    value: Any        # 证据值（dict / list / str ...）
    source: str       # 来源标识（"db://prod/task" / "http://...#200" / "file://..."）
    collected_at: float   # 取证时间（epoch）
    error: str | None     # None = 取证成功
```

**为什么必须带 source**：人工仲裁时看到「task 表有 1 行」，
还要知道这行是**从生产库查的**还是**从夹具快照读的**——可信度完全不同。

---

## 三、四种内置真值源

| 实现 | 适用场景 | 只读保障 |
|---|---|---|
| `DictTruthSource` | 夹具 / 内存快照（PoC 用） | 纯内存，无副作用 |
| `HttpTruthSource` | 被测模块提供查询接口 | **只发 GET** |
| `SqlTruthSource` | 直接查库 | **强制 SELECT 校验**（非 SELECT 直接拒绝，不执行） |
| `LogTruthSource` | 从日志提取状态 | 只读文件 |
| `CompositeTruthSource` | 组合多源（DB + HTTP + 日志） | 逐个委托 |

### 3.1 组合源的必要性

真实模块「接通了」通常需要**多个证据同时成立**：

```
表里有对应行  AND  接口能查到  AND  日志无错误
```

`CompositeTruthSource` 提供这个组合语义，并**汇总所有源的错误**——
当全部取不到时，judge 看到的是「所有来源都失败」而非「某一个失败」，
这直接对应门禁清单 G-11 的「数据能进来、结果能出去」双向验证。

### 3.2 SQL 只读强制

```python
if not sql.strip().lower().startswith("select"):
    return TruthEvidence(..., error="not_a_select_statement（只读约束：拒绝执行非 SELECT）")
```

**注意**：这是字符串前缀检查，不是 SQL 解析器。它挡得住
`DELETE`/`UPDATE`/`INSERT`/`DROP`/`TRUNCATE`，但挡不住
`SELECT ... INTO OUTFILE` 这类边缘情况。接入方仍需自行保证 SQL 干净。

---

## 四、接入示例

### 4.1 组外模块提供查询接口

```python
from nlaut.judge.truth_source import HttpTruthSource, CompositeTruthSource

ts = CompositeTruthSource({
    "http": HttpTruthSource(
        base_url="http://module-m.internal",
        path_map={"task": "/api/v1/task"},
    ),
    "db": SqlTruthSource(
        query_map={"task": "SELECT id, status, module FROM task WHERE module='M'"},
        executor=db.execute,
    ),
})
```

### 4.2 接到 agent judge

```python
from nlaut.judge.agent_judge import build_agent_judge_from_env
from nlaut.judge.agent_tools import DEFAULT_TOOLS, ToolPolicy, build_env_tool_impls

policy = ToolPolicy(
    allowed_hosts={"module-m.internal"},
    allowed_path_roots={"/var/log/module-m"},
)
impls = build_env_tool_impls(policy=policy, truth_source=ts)
judge = build_agent_judge_from_env(
    tools=DEFAULT_TOOLS, tool_impls=impls, policy=policy,
)
```

**不需要改引擎任何一行代码** —— `query_db` / `locate_object` 自动优先走真值源。

### 4.3 证据来源自动进报告

真值源取到的证据会带 `source` 与 `collected_at`：

```json
{
  "table": "task",
  "source": "db://prod/task",
  "collected_at": 1793352278.66,
  "row_count": 1,
  "rows": [{"id": 1005, "module": "M"}]
}
```

这些会出现在报告的「🔍 Agent 取证轨迹」表里（见 `docs/judge-protocol.md`）。

---

## 五、门禁 G-11 的原子断言映射

G-11 原文：「接入后模块与工作台的交互链路通（数据能进来、结果能出去）」

拆成 judge 可判的原子断言（每条都要有真值源支撑）：

| # | 原子断言 | 真值源 | 判定形态 |
|---|---|---|---|
| G-11-a | 模块已注册 | `list_registry` / `registry` | `module` 在注册表且 `enabled=true` |
| G-11-b | **数据能进来** | DB 表 / 接口查询 | 目标 key 存在对应行（`locate_object` 精确到目标） |
| G-11-c | **结果能出去** | 接口查询 / HTTP 探针 | 接口能返回该条记录且字段正确 |
| G-11-d | 无残留错误 | 日志 | 日志中无该模块相关的 ERROR |
| G-11-e | 未破坏既有功能 | 回归套件 | **不在本 judge 范围**（属 G-13 回归项） |

**边界纪律（不可越线）**：
- G-11 只判「**接入后能否正常工作**」，**不判模块内部功能正确性**
  （模块内部由所在组自担，属 L4 组外功能层）
- G-11-e 不由 agent judge 判——那是确定性回归套件的活

---

## 六、接入前必须确认的 6 项

真实接入前，接入方（模块所在组）与 PAA 组需共同确认：

| # | 待确认项 | 为什么重要 | 归属 |
|---|---|---|---|
| 1 | **真值源形态**（DB / HTTP / 日志 / 其它） | 决定用哪个 TruthSource 实现 | 模块所在组提供 |
| 2 | **只读账号权限** | 不能用写权限账号取证 | 模块所在组提供 |
| 3 | **环境隔离**（能否连生产） | G-11 门禁常在 SIT 跑 | PAA 组 + 运维 |
| 4 | **数据脱敏** | 证据会进报告 | 模块所在组 |
| 5 | **字段语义基线**（`status=created` 还是 `=1`） | 决定断言怎么写；字段语义错 = 假阳性 | PAA 组 + 模块所在组 |
| 6 | **目标对象标识**（用 id 还是业务键） | 决定 `locate_object` 的 keyword | PAA 组 |

> ⚠️ **第 5 项是最大的假阳性来源**：本框架的 H1/H2 样本专门测这个
> （`status=pending` 不算 created、`module` 大小写敏感）。
> 接入时若字段语义没对齐基线，judge 会**正确地**按错误的基线判否。

---

## 七、代码位置

| 文件 | 内容 |
|---|---|
| `src/nlaut/judge/truth_source.py` | `TruthSource` 协议 + 5 种实现（264 行） |
| `src/nlaut/judge/agent_tools.py` | `query_db` / `locate_object` 优先走真值源 |
| `tests/truth_source_test.py` | 25 项元测试（只读约束 / 失败即证据 / 来源可审计） |
| `tests/agent_judge_test.py` | `TestTruthSourceIntegration` 5 项（接入优先级 / 回落到 db_state） |

---

## 八、下一步

| 优先级 | 事项 | 产出 |
|---|---|---|
| P0 | 选定 1 个真实组外模块做接入试点 | 该模块的 `TruthSource` 实现 + 字段语义基线 |
| P0 | 用真实模块重跑 20 样本金标准 | 真实环境下的准确率 / 翻转率（夹具结果不能代替） |
| P1 | 把真值源配置外置（YAML/环境变量） | 接入方自助配置，不改代码 |
| P1 | 证据脱敏钩子 | 接入方自定义脱敏函数 |
| P2 | 多模块组合判定（模块 A 的数据要流到模块 B） | 跨模块数据流验证（G-12） |
