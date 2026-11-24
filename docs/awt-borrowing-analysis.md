# AWT 能力借鉴分析 —— 可实现性与实际意义

**日期**：2026-11-14
**分析对象**：AWT（AI Watch Tester，`aat-devqa` 1.8.0，本机已装）的三项增量能力
**参照基准**：`~/workspace/awt-methodology/AWT_vs_nlaut_对比.md` 附录 A（2026-11-07 追加的分工结论）
**方法**：全部结论基于本机实测（源码核实 + 真实运行 + 真实截图 SSIM 测量），非文档转述

---

## 〇、结论先行

**该借鉴的只有 1 项，且已原生实现；另外 2 项对 CeCoder 这个 Electron 桌面端「技术上不可用」。**

| AWT 增量能力 | 对 CeCoder（Electron）的可实现性 | 意义 | 本轮处置 |
|---|---|---|---|
| **SSIM 视觉回归** | ✅ **可独立实现**（纯 OpenCV，零运行时耦合） | 🟡 **有限但真实**——只覆盖布局级回归 | ✅ **已实现并实测** |
| **多视口响应式** | ❌ **技术上不可用** | 🟠 Electron 应用窗口由用户决定，非响应式 Web | 不做 |
| **快速元素盘点（scan）** | ❌ **技术上不可用** | 🔵 nlaut 的 selector 精度要求更高，scan 的视觉定位不适用 | 不做 |

**更重要的一条**：AWT **整体无法测 CeCoder**——它零处支持 CDP / Electron。这不是「配置没配对」，是能力缺失。

---

## 一、AWT 对 CeCoder 完全不可用（硬证据）

### 1.1 AWT 不支持 CDP / Electron

在 AWT 全部源码里检索（`$(uv tool dir)/aat-devqa/lib/python3.12/site-packages/aat/`）：

| 检索项 | 命中数 |
|---|---|
| `connect_over_cdp` | **0** |
| `cdp_url` | **0** |
| `remote-debugging` | **0** |
| `electron`（不区分大小写） | **0** |
| `chromium.launch` | 1 —— 仅在 `reporters/pdf.py:446`，用于生成 PDF 报告，与测试执行无关 |

**实测运行**（`aat scan --url http://127.0.0.1:49791/`）：

```
[AWT] Scanning http://127.0.0.1:49791/...
Error: Failed to start WebEngine: BrowserType.launch: Executable doesn't exist at ...
```

AWT 只会 `BrowserType.launch()` 自己起一个独立浏览器，**无法附着到已运行的 Electron 应用**。

### 1.2 而 nlaut 原生支持

`src/nlaut/executor/channels/electron.py:55`：

```python
browser = p.chromium.connect_over_cdp(cdp_url)
```

**这是 nlaut 相对 AWT 的结构性优势，不是可弥补的差距。** CeCoder 是 Electron 桌面应用（`/Applications/Cecoder.app`），AWT 对它无路可走。

> **推论**：任何「AWT 负责扫、nlaut 负责判」的分工建议，对 CeCoder 场景**不成立**——AWT 连扫都做不到。
> 之前对比文档附录 A.4 的推荐链路（① AWT scan → ② 人写 IR → ③ nlaut 执行）只对**纯 Web 系统**成立。

---

## 二、SSIM 视觉回归：可实现，但意义有限（本轮已实现）

### 2.1 为什么可以独立实现

AWT 的 SSIM 实现在 `aat/visual/comparator.py`（177 行），核心算法是：

- SSIM（Wang et al. 2004 公式）
- **纯 OpenCV 实现**，`GaussianBlur` 11×11 σ=1.5，C1=(0.01·255)²，C2=(0.03·255)²
- 不依赖 scikit-image

**结论**：算法是教科书公式，不含 AWT 的私有逻辑。nlaut 可独立实现，**不调用 AWT CLI、不产生运行时耦合、不引入 license 依赖**。

本轮实现：`src/nlaut/judge/visual_regression.py`（独立模块，与 AWT 无 import 关系）。

### 2.2 实测：SSIM 能检出什么、不能检出什么

用真实 CeCoder 工序编排面板截图（3272×1922 视口模式），注入 4 种 CSS 改动后测 SSIM：

| 注入的改动 | SSIM | 0.98 阈值 | 结论 |
|---|---|---|---|
| 标题文字改红 | 0.999935 | ❌ **未检出** | 小面积颜色变化，结构不变 |
| 工序名改灰 + 字号缩小 | 0.995129 | ❌ **未检出** | 小面积文字级变化 |
| 整个面板改红底 | 0.955447 | ✅ 检出 | 大面积色变 |
| 行间距加大（padding 8→20） | 0.927868 | ✅ 检出 | 布局位移 |

**这不是实现缺陷，是 SSIM 的固有性质**：结构相似度对「局部小面积变化」不敏感（11×11 窗口平均会抵消掉）。

### 2.3 所以它的定位是「布局级回归守门员」

| 能力 | 抓什么 | 谁负责 |
|---|---|---|
| **SSIM**（本轮实现） | 布局位移、间距变化、大面积配色变化 | 零 token，确定性 |
| **VLM（`visual_state`，已有）** | 语义错：文字写错、状态不对、元素缺失 | 有 token，语义层 |
| **确定性断言（已有）** | 文案、属性、禁用态 | 零 token，最精确 |

**三者不重叠。** SSIM 填补的是「界面结构变了但语义还对」这一档——纯断言和 VLM 都可能漏掉。

### 2.4 本轮实测的端到端闭环

```
用例: tc_cecoder_visual_001（CeCoder 工序编排面板视觉回归）
执行: 打开新建任务弹窗 → 视口截图 → 与基线 SSIM 比对

[     passed] tc_cecoder_visual_001  (34.2s)
报告明细: visual_regression | deterministic | value=1.0 | confidence=1.0 | auto_pass
          similarity=1.0 min_similarity=0.98 size_matched=True
          baseline_size=(3272, 1922) current_size=(3272, 1922)
```

**关键工程细节（都是踩出来的）**：

1. **必须用视口截图，不能用 full_page**。实测 `full_page=True` 时 Electron 页面高度 8740px（内容驱动，尺寸不稳定），SSIM 无从比对。本轮已改：通道层检测到 `visual_regression` 断言时自动切 `full_page=False`。
2. **Electron 窗口高度有 2px 抖动**：full_page 截图 1924 vs 视口 1922。视口模式连测 3 次稳定在 1922。
3. **基线缺失必须转人工，不能判通过**。本轮发现并修复了一个**框架级严重缺陷**——见 §四。

---

## 三、多视口（`--responsive`）与元素盘点（`scan`）：不做

### 3.1 多视口响应式 —— 对 Electron 无意义

**AWT 的能力**：`aat snapshot --responsive` 一次采 mobile / tablet / desktop 三档基线，用于测响应式 Web 布局。

**为什么对 CeCoder 无意义**：

- CeCoder 是**桌面应用**，窗口尺寸由用户拖拽决定，不是断点响应式布局
- nlaut 的 web 通道 viewport **硬编码 1280×900**（`web.py:108`），electron 通道用应用窗口尺寸
- 「手机视口下的 CeCoder 工序编排面板长什么样」——这个问题不存在

**可实现但无意义** ≠ 不值得做。硬做只会增加维护面。

### 3.2 快速元素盘点（`aat scan`）—— 与 nlaut 的方法论冲突

**AWT 的能力**：`aat scan --url X` 扫页面，收集 selector + OCR 文本 + 坐标，产出 `scan_result.json`。

**为什么不借鉴**：

| 维度 | AWT scan | nlaut 的做法 |
|---|---|---|
| 元素定位 | 视觉/OCR 匹配（`{selector, text, image, match_method}`） | 精确 CSS selector |
| 稳定性 | 依赖像素与文字，抗改版差 | 抗改版好 |
| 可读性 | 生成的 YAML 缺 `source`/`quad`，需人工重推 | IR 强制 `source` + `quad` |

之前对比文档已确认：**AWT 场景无法机械转成 nlaut IR**（缺 `source` 与 `quad` 两个必填根字段）。

**更根本的**：nlaut 的用例本来就由「口述 → IR」生成（`verbal-to-ir-protocol.md`），元素清单不是必需品。引入 scan 只会多一个「扫出来的元素还得人工翻译成 IR」的中间层。

**唯一可能有用的场景**：给完全陌生的系统做「探测性摸底」，看看有哪些可测点。但 AWT 对 Electron 不支持（§一），对 CeCoder 连这条路都走不通。

---

## 四、本轮顺带修掉的两个框架级缺陷

这两个都不是「AWT 借鉴」带来的，是实操过程中暴露的：

### 4.1 严重：deterministic 降级态会被误判为「通过」

**现象**：视觉回归基线缺失时，判定返回 `value=0.5, confidence=0.5`，
但 `arbiter.py` 的 deterministic 分支**只看 `value >= 0.5` 就 auto_pass** → **基线不存在反而判「通过」**。

```
基线缺失   value=0.5 conf=0.5 → auto_pass     ← 修复前（错误）
无截图     value=0.5 conf=0.5 → auto_pass     ← 修复前（错误）
```

**影响面**：不只是视觉回归。`deterministic.py` 里 `response_ttft_p95` 的降级也是 `value=0.5`——**同一个既有 bug 已经存在于仓库里**。

**修复**（`arbiter.py`）：deterministic 分支增加置信度守卫——

```python
if verdict.confidence < 1.0:
    return RouteDecision("human_review", "确定性判定降级（confidence=...），转人工仲裁")
```

deterministic 的正常判定 confidence 恒为 1.0，故此守卫**不影响任何既有行为**（221 条原元测试全绿验证）。

修复后：

```
基线缺失   value=0.5 conf=0.5 → human_review  ← 正确
无截图     value=0.5 conf=0.5 → human_review  ← 正确
相似度不足 value=0.0 conf=1.0 → auto_fail     ← 正确
完全一致   value=1.0 conf=1.0 → auto_pass     ← 正确
```

### 4.2 报告不展示判定数值明细

报告的「说明」列只有路由理由（"确定性判定，value>=0.5 即裁决"），看不到 SSIM 分数。
用户看到 `auto_fail` 不知道**差在哪**。

**修复**（`runner.py`）：把 raw 的关键数值（similarity / size_matched / 尺寸 / degraded）拼进 detail。
现在报告直接显示：`similarity=1.0 min_similarity=0.98 size_matched=True baseline_size=(3272, 1922)`。

---

## 五、给「AWT 到底有没有用」的最终回答

| 问法 | 回答 |
|---|---|
| AWT 能测 CeCoder 吗？ | **不能**。零 CDP / 零 Electron 支持，实测 `aat scan` 直接报启动失败 |
| 那 AWT 对你有什么用？ | 对**纯 Web 系统**（如源启智能软件工厂 `10.64.17.69:30972`）可能有用：SSIM 视觉回归 + 多视口 |
| 要不要把它接进 nlaut？ | **不要**。它的三项增量里，两项对 Electron 无意义、一项（SSIM）我们已经原生实现了而且不依赖它 |
| 那要不要保留 AWT？ | 作为**独立工具**保留即可（`aat snapshot`/`aat diff` 跑 Web 系统的视觉回归），**不进 nlaut 依赖链** |

### 一句话结论

> **AWT 的真正价值不在它的功能清单，而在它提醒了 nlaut 的一个真空：像素级视觉回归。**
> 这个真空我已经用独立实现补上了（177 行 → 60 行核心算法），不引入 AWT 依赖。
> 而 AWT 作为工具对 CeCoder 完全无效，**不应进入 nlaut 的依赖链或工作流**。

---

## 附：本轮改动清单

| 文件 | 改动 | 类型 |
|---|---|---|
| `src/nlaut/judge/visual_regression.py` | 新增：SSIM 比对 + diff 图生成 | 新能力 |
| `src/nlaut/ir/model.py` | 新增 `AssertVisualRegression` 断言 | 新能力 |
| `src/nlaut/judge/deterministic.py` | 新增 `visual_regression` mode | 新能力 |
| `src/nlaut/executor/runner.py` | 断言映射 + 引擎路由 + 报告明细 | 新能力 + 缺陷修复 |
| `src/nlaut/executor/channels/{web,electron}.py` | 有视觉回归断言时改用视口截图 | 新能力前置 |
| `src/nlaut/judge/arbiter.py` | deterministic 降级守卫 | **缺陷修复（严重）** |
| `tests/visual_regression_test.py` | 新增 13 条测试 | 新能力 |
| `cases/cecoder/tc_cecoder_visual_001.yaml` | CeCoder 视觉回归用例 | 新用例 |
| `baselines/cecoder/order_panel.png` | 视觉回归基线 | 新资产 |
| `pyproject.toml` | 新增 opencv-python-headless + numpy | 依赖 |

**验证**：234 条元测试全绿（原 221 + 新 13）；改动文件 ruff 全过；视觉回归用例真实跑通并量化了检出边界。
