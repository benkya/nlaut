# Agent-as-Judge 取证工具增强记录（2026-11-13）

**改动文件**：`src/nlaut/judge/agent_tools.py`（+172 / -2）
**触发原因**：为 CeCoder 工序域编写 agent_verify 用例时实测发现三个取证盲区，导致 agent
无法完成任务、只能反复猜文件名直到 `max_steps_exhausted` 降级。

---

## 一、三个实测盲区与对应增强

### 1. `read_file` 不展开 `~`

**现象**：agent 自然地写 `~/.cecoder/engineering-workbench/tasks/`，
`default_file_reader` 用 `open(path)` 直接打开 → 必然 `file_not_found`。

**修复**：`os.path.expanduser(path)`。`path_allowed` 早已用 `expanduser`，只漏了 reader。

### 2. 没有「列目录」能力 → agent 只能猜文件名

**现象**：agent 的 17 次工具调用里，反复尝试
`tasks/manifest.json` / `tasks/README.md` / `tasks/tasks.json` / `tasks/config.json`
等**猜出来的**文件名，全部 `file_not_found`；真正的 `index.json` 它从没试过。

**修复**：新增 `list_dir` 工具。

```python
# 只读；越界走 ToolPolicy.path_allowed
{
  "path": "~/.cecoder/engineering-workbench/tasks/",
  "max_entries": 200
}
# 返回 {path, total, returned, truncated, entries: [{name, is_dir}]}
```

### 3. `read_file` 读不完大 JSON → 关键节点在文件末尾

**现象**：工序任务快照普遍 25KB~432KB，`read_file` 默认只读 8000 字节。
实测 `1005.json`（432KB）里 `example.delivery` 节点首次出现在**第 323906 字节**处 ——
`read_file` 永远读不到它。agent 只能盲目加 `max_bytes: 100000`，仍然不够。

**修复**：新增 `query_json` 工具（读 JSON + 按字段筛选）。

```python
{
  "path": "~/.cecoder/engineering-workbench/tasks/1005.json",
  "array_path": "nodes",              # 留空则自动探测顶层数组
  "field": "definitionId",            # 筛选字段
  "equals": "example.delivery",       # 等值匹配
  "fields": ["instanceId", "status", "outputs", "assessment"],  # 只返回这些字段
  "max_items": 20
}
# 返回 {path, array_path, total_in_array, matched, returned, truncated, items}
```

---

## 二、实测效果（同一份 CeCoder 工序数据）

| 阶段 | 工具调用 | 结果 | 判定 |
|---|---|---|---|
| 增强前 | 17 次 | 全是 `file_not_found` / `scope_violation` / `db_state_not_provided` | `max_steps_exhausted` 降级（confidence=0） |
| 增强后 | 6 次 | `list_dir` → `query_json` 命中 → 读磁盘产物 | `judgement: false`，`CONFIDENCE: High` |

判定理由（agent 自主产出）：

> 任务 1001 的 done 节点 n01(example.ui-prototype) 与任务 1011 的 done 节点
> n01(example.concept-design) 的 outputs 数组均为空，但磁盘 assets/1001 下确有产物
> （01-ui-prototype、index.html、HANDOFF.md），assets/1011 下确有产物（01-concept-design），
> 产物清单未回写，两处数据源不一致。

**该判定已由人工独立核实属实**（本机实测：102 个 done 节点中 2 个 `task.json` 的
`outputs` 为空而磁盘有产物）。

---

## 三、边界与安全

- 三个工具全部走 `ToolPolicy.path_allowed` / `host_allowed`，
  **默认拒绝式**（`allowed_path_roots` 为空 → 一律 `scope_violation`）
- 只读语义，未引入任何写操作
- `list_dir` / `query_json` 的越界行为与 `read_file` 一致：返回结构化拒绝 + 记入
  `ToolPolicy.violations`，不抛异常、不中断判定
- `query_json` 用 `json.load` 解析，**大文件会全量载入内存**。
  当前工序快照最大 432KB，无风险；若未来接入 GB 级 JSON 需改用流式解析

---

## 四、配套的用例侧约束（写 agent_verify 用例时必读）

1. **大 JSON 必须用 `query_json`**，不能用 `read_file`
2. **objective 里要写清具体参数**（path / array_path / field / equals / fields），
   不要让 agent 自己猜 —— 猜的失败率极高
3. **objective 要区分 status**（`done` vs `pending`）。实测 agent 会把
   「gates 预判为 allow」误读成「已放行」，得出过强结论
4. **取证环境变量**：`NLaut_AGENT_JUDGE_ALLOWED_PATHS=/Users/Ben/.cecoder`。
   不放行则所有文件类工具一律拒绝
5. **Electron 通道的 `nav`**：不能导航到 `file://` 目录（`ERR_FILE_NOT_FOUND`），
   `about:blank` 会被 runner 拼成 `http://about:blank` 而失效 → 用恒存在的 `file://` 单文件

---

**验证**：`.venv/bin/python -m pytest` 221 项全绿；`ruff check src/nlaut/judge/agent_tools.py` 通过。
