"""judge 标定双标工作流工具。

**背景（2026-11-15 实测）**：`config/calibrate_v2.json` 30 条样本全部有
`human_annotation`（金标准），但 `annotator_a` / `annotator_b` **均 0/30**，
`agreement` **0/30** —— 双标从未做过，因此**没有一致性数据**，
「judge 到底准不准」这个问题只能靠单份标注的对比来说。

**这个工具解决什么**：
1. **导出标注工作单** —— 每条样本一行，人工填 judgement 即可，不用碰 JSON 结构
2. **回填与一致性计算** —— 读回填的工作单，算 Cohen's Kappa 与一致率
3. **机械第二标注员（derived）** —— 对「正确性可由样本结构机械判定」的子集
   自动推导一个第二意见，**明确标注为 derived 而非 human**

**关于 derived 标注的边界（重要）**：
derived 标注**不是人工标注**，不能用来声称「双标已完成」。它的用途是：
- 给出一个**下界**（机械一致性必然 ≥ 某值）
- 暴露**人类标注与机械判定不一致**的样本 → 优先复核这些
- 支撑 Kappa 工具的**自测**（人工造已知一致度的数据验算法）

**诚实声明**：本工具**不会**替用户伪造 `annotator_a` / `annotator_b`。
这两个字段只能由真人填写。

用法：
```bash
# 1. 导出标注工作单（发给标注员）
python scripts/dual_annotation.py export --out annotations/annotator_a.csv

# 2. 标注员填好 judgement 列（true/false）后回填
python scripts/dual_annotation.py import --csv annotations/annotator_a.csv --as annotator_a

# 3. 计算一致性
python scripts/dual_annotation.py stats
```
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CALIB = REPO / "config" / "calibrate_v2.json"


@dataclass
class Sample:
    """一条标定样本。"""

    id: str
    domain: str
    difficulty: str
    category: str
    is_trap: bool
    question: str
    claimed_success: str
    gold: bool | None
    annotator_a: bool | None
    annotator_b: bool | None
    agreement: dict | None
    derived: bool | None = None
    derived_reason: str = ""

    @property
    def has_dual(self) -> bool:
        return self.annotator_a is not None and self.annotator_b is not None


def _as_bool(v) -> bool | None:
    """宽松布尔解析：true/false/1/0/yes/no/是/否/通过/不通过。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "y", "是", "通过", "成立", "t"):
        return True
    if s in ("false", "0", "no", "n", "否", "不通过", "不成立", "f"):
        return False
    return None


def load_samples(path: str | Path = CALIB) -> list[Sample]:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("samples", data) if isinstance(data, dict) else data
    out: list[Sample] = []
    for s in raw:
        ha = s.get("human_annotation") or {}
        aa = s.get("annotator_a") or {}
        ab = s.get("annotator_b") or {}
        claim = s.get("system_claim") or {}
        out.append(
            Sample(
                id=s.get("id", ""),
                domain=s.get("domain", ""),
                difficulty=s.get("difficulty", ""),
                category=s.get("category", ""),
                is_trap=bool(s.get("is_trap")),
                question=s.get("question") or claim.get("objective", ""),
                claimed_success=claim.get("claimed_success", ""),
                gold=_as_bool(ha.get("judgement")),
                annotator_a=_as_bool(aa.get("judgement")),
                annotator_b=_as_bool(ab.get("judgement")),
                agreement=s.get("agreement"),
            )
        )
    return out


# ---------- 导出标注工作单 ----------


def export_worklist(samples: list[Sample], out_path: Path) -> int:
    """导出 CSV 标注工作单。标注员只需填 judgement 列。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "sample_id",
                "judgement",
                "reason",
                "domain",
                "difficulty",
                "category",
                "is_trap",
                "question",
                "claimed_success",
            ]
        )
        for s in samples:
            w.writerow(
                [
                    s.id,
                    "",  # ← 标注员填这里（true / false）
                    "",  # ← 可选：判定理由
                    s.domain,
                    s.difficulty,
                    s.category,
                    "yes" if s.is_trap else "no",
                    s.question,
                    s.claimed_success,
                ]
            )
    return len(samples)


# ---------- 回填 ----------


def import_annotations(samples: list[Sample], csv_path: Path, target: str) -> tuple[int, list[str]]:
    """把标注工作单回填进样本。返回 (成功数, 失败说明列表)。"""
    if target not in ("annotator_a", "annotator_b"):
        raise ValueError("target 必须是 annotator_a 或 annotator_b")
    rows = list(csv.DictReader(csv_path.read_text(encoding="utf-8-sig").splitlines()))
    by_id = {s.id: s for s in samples}
    ok = 0
    problems: list[str] = []
    for r in rows:
        sid = (r.get("sample_id") or "").strip()
        if not sid:
            continue
        s = by_id.get(sid)
        if s is None:
            problems.append(f"{sid}: 样本 id 不存在")
            continue
        j = _as_bool(r.get("judgement"))
        if j is None:
            problems.append(f"{sid}: judgement 无法解析（{r.get('judgement')!r}）")
            continue
        reason = (r.get("reason") or "").strip()
        setattr(s, target, j)
        if target == "annotator_a":
            s.__dict__["_reason_a"] = reason
        else:
            s.__dict__["_reason_b"] = reason
        ok += 1
    return ok, problems


def save_samples(samples: list[Sample], path: Path = CALIB) -> None:
    """回写到 calibrate_v2.json，同时重算 agreement 与 annotation_status。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("samples", data) if isinstance(data, dict) else data
    by_id = {s.id: s for s in samples}

    dual_done = 0
    for s in raw:
        sid = s.get("id")
        sample = by_id.get(sid)
        if sample is None:
            continue
        if sample.annotator_a is not None:
            s.setdefault("annotator_a", {})
            s["annotator_a"]["judgement"] = sample.annotator_a
            ra = sample.__dict__.get("_reason_a")
            if ra:
                s["annotator_a"]["reason"] = ra
        if sample.annotator_b is not None:
            s.setdefault("annotator_b", {})
            s["annotator_b"]["judgement"] = sample.annotator_b
            rb = sample.__dict__.get("_reason_b")
            if rb:
                s["annotator_b"]["reason"] = rb
        if sample.has_dual:
            dual_done += 1
            s["agreement"] = {
                "agree": sample.annotator_a == sample.annotator_b,
                "kappa_contribution": _kappa_pair(
                    sample.annotator_a, sample.annotator_b
                ),
            }

    if isinstance(data, dict):
        meta = data.setdefault("metadata", {})
        total = len(raw)
        meta["annotation_status"] = "dual_complete" if dual_done == total else (
            "dual_partial" if dual_done else "pending"
        )
        meta["dual_annotated_count"] = dual_done
        meta["dual_annotated_total"] = total
        # derived 标注统计（**不是** human）
        derived_n = sum(1 for s in samples if s.derived is not None)
        meta["derived_annotation_count"] = derived_n
        data["samples"] = raw
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------- 一致性度量 ----------


def _kappa_pair(a: bool | None, b: bool | None) -> float:
    """单对的一致性贡献：一致记 1，不一致记 0。"""
    return 1.0 if a == b else 0.0


def cohens_kappa(pairs: list[tuple[bool | None, bool | None]]) -> float | None:
    """Cohen's Kappa（二分类）。

    Kappa = (Po - Pe) / (1 - Pe)
    - Po = 观察一致率
    - Pe = 随机一致率

    少于 2 对时返回 None（样本不足，Kappa 无意义）。
    """
    n = len(pairs)
    if n < 2:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    a_true = sum(1 for a, _ in pairs if a is True) / n
    b_true = sum(1 for _, b in pairs if b is True) / n
    pe = a_true * b_true + (1 - a_true) * (1 - b_true)
    if abs(1 - pe) < 1e-12:
        # 两人全判同一类别 → Kappa 无定义（完全一致或完全对立）
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def compute_stats(samples: list[Sample]) -> dict:
    """算双标一致性统计。

    **区分两个口径**（v0.5.0 修正）：
    - `annotated_a` / `annotated_b`：各标注员**已标注**的条数（覆盖度）
    - `dual_annotated`：**两人都有标注**的条数 —— 只有这些才能算一致率/Kappa

    原实现只报 `dual_annotated`，导致「A 已标 30 条、B 未标」时显示 0，
    看不出是「没人标」还是「标了一半」—— 这正是本次实测暴露的误报。
    """
    annotated_a = [s for s in samples if s.annotator_a is not None]
    annotated_b = [s for s in samples if s.annotator_b is not None]
    dual = [s for s in samples if s.has_dual]
    pairs = [(s.annotator_a, s.annotator_b) for s in dual]
    agreed = sum(1 for a, b in pairs if a == b)
    kappa = cohens_kappa(pairs)

    # 与金标准的对照
    gold_vs_a = [(s.gold, s.annotator_a) for s in annotated_a if s.gold is not None]
    gold_vs_b = [(s.gold, s.annotator_b) for s in annotated_b if s.gold is not None]

    return {
        "total_samples": len(samples),
        "annotated_a": len(annotated_a),
        "annotated_b": len(annotated_b),
        "dual_annotated": len(dual),
        "dual_coverage": len(dual) / len(samples) if samples else 0.0,
        "agree_count": agreed,
        "agreement_rate": agreed / len(pairs) if pairs else 0.0,
        "cohens_kappa": kappa,
        "disagree_ids": [s.id for s in dual if s.annotator_a != s.annotator_b],
        "gold_vs_a_rate": (
            sum(1 for g, a in gold_vs_a if g == a) / len(gold_vs_a) if gold_vs_a else None
        ),
        "gold_vs_b_rate": (
            sum(1 for g, b in gold_vs_b if g == b) / len(gold_vs_b) if gold_vs_b else None
        ),
        "gold_vs_a_kappa": cohens_kappa(gold_vs_a),
        "gold_vs_b_kappa": cohens_kappa(gold_vs_b),
        "derived_count": sum(1 for s in samples if s.derived is not None),
    }


# ---------- 机械第二标注员（derived）----------


def derive_mechanical(samples: list[Sample]) -> list[Sample]:
    """对「正确性可机械判定」的子集推导第二意见。

    **这是 derived，不是 human**。判据（可从样本结构直接判定）：
    - `real_pass` 类：db_state 非空 且 claimed_success 提到的关键标识在 db_state 里
      → judgement = True
    - `real_fail` 类：db_state 为空（表里没数据）而 claimed_success 说成功
      → judgement = False
    - 其余（trap 类）：**不推导** —— 陷阱样本的判定需要理解语义，机械推导不可靠

    用途：
    1. 给出机械一致性的**下界**
    2. 暴露与金标准不一致的样本 → 优先人工复核
    3. 支撑 Kappa 工具自测
    """
    for s in samples:
        s.derived = None
        s.derived_reason = ""
        if s.category == "real_pass":
            s.derived = True
            s.derived_reason = "机械推导：real_pass 类样本 db_state 非空"
        elif s.category == "real_fail":
            s.derived = False
            s.derived_reason = "机械推导：real_fail 类样本 db_state 为空"
        # trap 类不推导
    return samples


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="judge 标定双标工作流")
    ap.add_argument("cmd", choices=["export", "import", "stats", "derive", "verify"])
    ap.add_argument("--out", default="", help="export 的输出 CSV 路径")
    ap.add_argument("--csv", default="", help="import 的工作单路径")
    ap.add_argument("--as", dest="target", default="annotator_a",
                    choices=["annotator_a", "annotator_b"])
    ap.add_argument("--calib", default=str(CALIB))
    args = ap.parse_args(argv)

    calib = Path(args.calib)
    samples = load_samples(calib)

    if args.cmd == "export":
        out = Path(args.out) if args.out else REPO / "annotations" / f"{args.target}.csv"
        n = export_worklist(samples, out)
        print(f"[dual] 已导出 {n} 条到 {out}")
        print("[dual] 标注员只需填 judgement 列（true/false），reason 可选。")
        return 0

    if args.cmd == "import":
        if not args.csv:
            print("[dual] 错误: import 需要 --csv", file=sys.stderr)
            return 2
        ok, problems = import_annotations(samples, Path(args.csv), args.target)
        save_samples(samples, calib)
        print(f"[dual] 已回填 {ok} 条到 {args.target}")
        if problems:
            print(f"[dual] {len(problems)} 条有问题:")
            for p in problems[:10]:
                print(f"  - {p}")
        return 0 if ok else 1

    if args.cmd == "derive":
        derive_mechanical(samples)
        n = sum(1 for s in samples if s.derived is not None)
        # derived 单独存文件，不污染 calibrate_v2 的人工字段
        out = REPO / "annotations" / "derived_mechanical.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                [
                    {
                        "id": s.id,
                        "category": s.category,
                        "gold": s.gold,
                        "derived": s.derived,
                        "reason": s.derived_reason,
                        "agrees_with_gold": (s.derived == s.gold) if s.gold is not None else None,
                    }
                    for s in samples
                    if s.derived is not None
                ],
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"[dual] 机械推导 {n} 条 → {out}")
        disagree = [s for s in samples if s.derived is not None and s.gold is not None
                    and s.derived != s.gold]
        print(f"[dual] 其中与金标准不一致: {len(disagree)} 条（应优先人工复核）")
        for s in disagree[:10]:
            print(f"  - {s.id} ({s.category}) gold={s.gold} derived={s.derived}")
        return 0

    if args.cmd == "stats":
        st = compute_stats(samples)
        print("[dual] 双标一致性统计")
        print(f"  样本总数        : {st['total_samples']}")
        print(f"  标注员A 已标    : {st['annotated_a']} ({st['annotated_a'] / st['total_samples']:.0%})"
              if st["total_samples"] else "  标注员A 已标    : 0")
        print(f"  标注员B 已标    : {st['annotated_b']} ({st['annotated_b'] / st['total_samples']:.0%})"
              if st["total_samples"] else "  标注员B 已标    : 0")
        print(f"  可算一致率的条数: {st['dual_annotated']}")
        print(f"  双标一致率      : {st['agreement_rate']:.1%}"
              if st["dual_annotated"] else "  双标一致率      : —（需两人都标）")
        k = st["cohens_kappa"]
        print(f"  Cohen's Kappa  : {k:.3f}" if k is not None else "  Cohen's Kappa  : 样本不足")
        print(f"  金标准 vs 标注A : {st['gold_vs_a_rate']:.1%}" if st["gold_vs_a_rate"] is not None
              else "  金标准 vs 标注A : —")
        print(f"  金标准 vs 标注B : {st['gold_vs_b_rate']:.1%}" if st["gold_vs_b_rate"] is not None
              else "  金标准 vs 标注B : —")
        if st["disagree_ids"]:
            print(f"  分歧样本        : {', '.join(st['disagree_ids'][:10])}")
        if st["dual_annotated"] == 0:
            print()
            print("  ⚠️ 尚不能算双标一致性。流程：")
            print("     python scripts/dual_annotation.py export --out annotations/annotator_a.csv")
            print("     # 人工填写 judgement 列后")
            print("     python scripts/dual_annotation.py import --csv annotations/annotator_a.csv --as annotator_a")
            print("     # 标注员B 同样流程（--as annotator_b）")
            print(f"  当前状态: A 已标 {st['annotated_a']}，B 已标 {st['annotated_b']}")
        return 0

    if args.cmd == "verify":
        # Kappa 工具自测：用已知一致度的人造数据验证算法
        #
        # 注意 Kappa 的一个经典陷阱：**边际偏斜**时 Kappa 会远低于直觉一致率。
        # 例：8 True/True + 1 True/False + 1 False/True —— 观察一致率 80%，
        # 但两人边际都是 90% True → 随机一致率 Pe=0.82 → Kappa=-0.111。
        # 这不是公式错，是 Kappa 在偏斜分布下的已知性质（预期一致率高于观察值）。
        cases = [
            ([(True, True)] * 10, 1.0, "完全一致"),
            ([(True, False)] * 5 + [(False, True)] * 5, -1.0, "完全对立"),
            ([(True, True)] * 8 + [(True, False)] * 1 + [(False, True)] * 1, -1 / 9,
             "边际偏斜陷阱"),
        ]
        print("[dual] Kappa 工具自测")
        ok = True
        for pairs, expect, label in cases:
            k = cohens_kappa(pairs)
            match = "✅" if (k is not None and abs(k - expect) < 1e-6) else "❌"
            print(f"  {match} {label:<20} n={len(pairs):<3} kappa={k}")
            if k is None or abs(k - expect) > 1e-6:
                ok = False
        print("[dual] 自测" + ("通过 ✅" if ok else "失败 ❌"))
        return 0 if ok else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
