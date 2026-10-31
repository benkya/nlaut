#!/usr/bin/env python3
"""标定基准集标注工具

用法:
    # 查看样本统计
    python scripts/calibrate_tool.py stats

    # 开始标注（标注者 A）
    python scripts/calibrate_tool.py annotate --annotator A

    # 开始标注（标注者 B）
    python scripts/calibrate_tool.py annotate --annotator B

    # 计算一致性
    python scripts/calibrate_tool.py agreement

    # 生成标定报告
    python scripts/calibrate_tool.py report
"""

import json
import sys
from pathlib import Path
from typing import Any

import yaml


def load_samples(path: str = "config/calibrate_v1.json") -> list[dict]:
    """加载标定基准集"""
    import json
    with open(path) as f:
        data = json.load(f)
    return data.get("samples", [])


def save_samples(samples: list[dict], path: str = "config/calibrate_v1.json"):
    """保存标注结果"""
    import json
    
    # 加载元数据
    with open(path) as f:
        data = json.load(f)
    
    # 更新样本
    data["samples"] = samples
    
    # 更新元数据
    a_done = sum(1 for s in samples if s.get("annotator_a", {}).get("judgement") is not None)
    b_done = sum(1 for s in samples if s.get("annotator_b", {}).get("judgement") is not None)
    data["metadata"]["annotator_a_completion"] = f"{a_done}/{len(samples)}"
    data["metadata"]["annotator_b_completion"] = f"{b_done}/{len(samples)}"
    
    # 计算一致性
    agreements = sum(1 for s in samples if s.get("agreement") is not None)
    if agreements > 0:
        agreed = sum(1 for s in samples if s.get("agreement") is True)
        data["metadata"]["inter_annotator_agreement"] = f"{agreed}/{agreements} = {agreed/agreements*100:.1f}%"
    
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def cmd_stats():
    """显示样本统计"""
    samples = load_samples()

    print("=" * 60)
    print("标定基准集统计")
    print("=" * 60)
    print()

    # 来源统计
    sources = {}
    for s in samples:
        src = s.get("source", "unknown")
        sources[src] = sources.get(src, 0) + 1

    print("样本来源:")
    for src, count in sorted(sources.items()):
        print(f"  {src}: {count}")

    print()

    # 难度统计
    difficulties = {}
    for s in samples:
        diff = s.get("difficulty", "unknown")
        difficulties[diff] = difficulties.get(diff, 0) + 1

    print("难度分布:")
    for diff, count in sorted(difficulties.items()):
        pct = count / len(samples) * 100
        print(f"  {diff}: {count} ({pct:.0f}%)")

    print()

    # 陷阱统计
    traps = sum(1 for s in samples if s.get("is_trap", False))
    print(f"陷阱样本: {traps} / {len(samples)} = {traps/len(samples)*100:.0f}%")
    print()

    # 标注进度
    a_done = sum(1 for s in samples if s.get("annotator_a", {}).get("judgement") is not None)
    b_done = sum(1 for s in samples if s.get("annotator_b", {}).get("judgement") is not None)

    total = len(samples)
    print("标注进度:")
    print(f"  标注者 A: {a_done}/{total}")
    print(f"  标注者 B: {b_done}/{total}")

    if a_done > 0 and b_done > 0:
        # 计算已完成的一致性
        agreed = 0
        total = 0
        for s in samples:
            a = s.get("annotator_a", {}).get("judgement")
            b = s.get("annotator_b", {}).get("judgement")
            if a is not None and b is not None:
                total += 1
                if a == b:
                    agreed += 1

        if total > 0:
            print(f"  一致性: {agreed}/{total} = {agreed/total*100:.1f}%")

    print()


def cmd_annotate(annotator: str):
    """开始标注"""
    samples = load_samples()

    annotator_key = f"annotator_{annotator.lower()}"

    print("=" * 60)
    print(f"标注者 {annotator.upper()} 开始标注")
    print("=" * 60)
    print()
    print("说明:")
    print("  - 每个样本需要给出判定（true/false）")
    print("  - 判定依据：仅根据 system_claim 中的信息，结合问题")
    print("  - 不要参考 human_annotation（那是金标准，用于对比）")
    print("  - 输入 's' 跳过，'q' 退出")
    print()

    for i, s in enumerate(samples, 1):
        # 检查是否已标注
        if s.get(annotator_key, {}).get("judgement") is not None:
            continue

        print(f"[{i}/30] 样本 ID: {s['id']}")
        print(f"来源: {s['source']} | 难度: {s['difficulty']} | 陷阱: {s['is_trap']}")
        print()
        print("描述:", s["description"])
        print()

        # 显示 system_claim
        claim = s["system_claim"]
        print("被测系统自述:")
        print(f"  目标: {claim.get('objective', 'N/A')}")
        print(f"  自述: {claim.get('claimed_success', 'N/A')}")

        if "code" in claim:
            print()
            print("  代码:")
            for line in claim["code"].strip().split("\n"):
                print(f"    {line}")

        if "db_state" in claim:
            print()
            print(f"  数据库状态: {json.dumps(claim['db_state'], ensure_ascii=False)}")

        print()
        print("判定问题:", s["question"])
        print()

        # 收集输入
        while True:
            answer = input("判定结果 (true/false/s/q): ").strip().lower()

            if answer == "q":
                print("退出标注")
                return
            elif answer == "s":
                print("跳过")
                break
            elif answer in ("true", "t", "yes", "y", "1"):
                judgement = True
                break
            elif answer in ("false", "f", "no", "n", "0"):
                judgement = False
                break
            else:
                print("无效输入，请重新输入")
                continue

        if answer == "s":
            continue

        # 收集理由
        reason = input("判定理由（可选）: ").strip()

        # 更新样本
        if annotator_key not in s:
            s[annotator_key] = {}
        s[annotator_key]["judgement"] = judgement
        s[annotator_key]["reason"] = reason if reason else None

        print()
        print(f"已记录: {judgement}")
        print()

        # 检查是否需要计算一致性
        other_key = f"annotator_{'b' if annotator.lower() == 'a' else 'a'}"
        other = s.get(other_key, {}).get("judgement")
        if other is not None:
            s["agreement"] = judgement == other
            if s["agreement"]:
                print("✓ 与另一位标注者一致")
            else:
                print("✗ 与另一位标注者不一致")
                print(f"  你的判定: {judgement}")
                print(f"  对方判定: {other}")

        print("-" * 60)

    # 保存结果
    save_samples(samples)
    print("标注完成，已保存到 config/calibrate_v1.yaml")


def cmd_agreement():
    """计算一致性"""
    samples = load_samples()

    print("=" * 60)
    print("标注者间一致性分析")
    print("=" * 60)
    print()

    # 统计
    total = 0
    agreed = 0
    disagreements = []

    for s in samples:
        a = s.get("annotator_a", {}).get("judgement")
        b = s.get("annotator_b", {}).get("judgement")

        if a is not None and b is not None:
            total += 1
            if a == b:
                agreed += 1
                s["agreement"] = True
            else:
                s["agreement"] = False
                disagreements.append({
                    "id": s["id"],
                    "description": s["description"],
                    "annotator_a": a,
                    "annotator_b": b,
                })

    if total == 0:
        print("尚无双方都标注的样本")
        return

    agreement_rate = agreed / total * 100

    print(f"已完成标注: {total}/30")
    print(f"一致样本: {agreed}")
    print(f"不一致样本: {len(disagreements)}")
    print(f"一致率: {agreement_rate:.1f}%")
    print()

    if agreement_rate >= 90:
        print("✓ 一致率 ≥ 90%，符合要求")
    else:
        print("✗ 一致率 < 90%，需要对不一致样本进行仲裁")

    if disagreements:
        print()
        print("不一致样本列表:")
        for d in disagreements:
            print(f"  {d['id']}: {d['description']}")
            print(f"    标注者 A: {d['annotator_a']}")
            print(f"    标注者 B: {d['annotator_b']}")

    # 保存结果
    save_samples(samples)


def cmd_report():
    """生成标定报告"""
    samples = load_samples()

    print("=" * 60)
    print("标定报告生成")
    print("=" * 60)

    # 检查标注状态
    a_done = sum(1 for s in samples if s.get("annotator_a", {}).get("judgement") is not None)
    b_done = sum(1 for s in samples if s.get("annotator_b", {}).get("judgement") is not None)

    if a_done < 30 or b_done < 30:
        print()
        print("警告: 标注尚未完成")
        print(f"  标注者 A: {a_done}/30")
        print(f"  标注者 B: {b_done}/30")
        print()
        print("请先完成双盲标注")
        return

    # 计算一致性
    agreed = sum(1 for s in samples if s.get("agreement") is True)
    total = 30
    agreement_rate = agreed / total * 100

    # 生成报告
    report = f"""# Judge 标定报告

**生成时间**: {Path("config/calibrate_v1.yaml").stat().st_mtime}

---

## 一、标定概览

- **标定基准集**: calibrate_v1.yaml（30 样本）
- **标注者 A 完成度**: 30/30
- **标注者 B 完成度**: 30/30
- **标注者间一致率**: {agreement_rate:.1f}%

---

## 二、样本统计

| 维度 | 数量 | 占比 |
|---|---|---|
| **总样本** | 30 | 100% |
| 简单 | {sum(1 for s in samples if s['difficulty'] == 'easy')} | {sum(1 for s in samples if s['difficulty'] == 'easy')/30*100:.0f}% |
| 中等 | {sum(1 for s in samples if s['difficulty'] == 'medium')} | {sum(1 for s in samples if s['difficulty'] == 'medium')/30*100:.0f}% |
| 困难 | {sum(1 for s in samples if s['difficulty'] == 'hard')} | {sum(1 for s in samples if s['difficulty'] == 'hard')/30*100:.0f}% |
| 陷阱样本 | {sum(1 for s in samples if s['is_trap'])} | {sum(1 for s in samples if s['is_trap'])/30*100:.0f}% |

---

## 三、标注者一致性

| 指标 | 值 |
|---|---|
| 一致样本 | {agreed} |
| 不一致样本 | {total - agreed} |
| **一致率** | **{agreement_rate:.1f}%** |
| 判定标准 | ≥ 90% 通过 |

"""

    if agreement_rate >= 90:
        report += "**结论**: ✓ 一致率达标，可用于 Judge 标定\n"
    else:
        report += "**结论**: ✗ 一致率未达标，需对不一致样本进行仲裁\n"

    # 不一致样本详情
    disagreements = [s for s in samples if s.get("agreement") is False]
    if disagreements:
        report += "\n---\n\n## 四、不一致样本详情\n\n"
        for s in disagreements:
            report += f"### {s['id']}: {s['description']}\n\n"
            report += f"- **标注者 A 判定**: {s['annotator_a']['judgement']}\n"
            report += f"- **标注者 B 判定**: {s['annotator_b']['judgement']}\n"
            report += f"- **金标准**: {s['human_annotation']['judgement']}\n"
            report += f"- **理由**: {s['human_annotation']['reason']}\n\n"

    # 下一步
    report += """---

## 五、下一步

1. **已完成**: 双盲标注 + 一致性计算
2. **如一致率 < 90%**: 引入第三标注者对不一致样本仲裁
3. **生成金标准**: 将一致样本 + 仲裁结果作为最终金标准
4. **Judge 标定**: 让 LLM Judge / Agent-as-Judge 对金标准判定
5. **计算 Judge 一致率**: 与金标准对比，需 ≥ 85%

---

**报告生成时间**: 2026-10-31
"""

    # 保存报告
    report_path = Path("artifacts/judge_calibration_report.md")
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(report)

    print()
    print(f"报告已生成: {report_path}")
    print()
    print(report)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    if cmd == "stats":
        cmd_stats()
    elif cmd == "annotate":
        if len(sys.argv) < 3:
            print("用法: python scripts/calibrate_tool.py annotate --annotator A|B")
            return
        annotator = sys.argv[3] if sys.argv[2] == "--annotator" else sys.argv[2]
        cmd_annotate(annotator)
    elif cmd == "agreement":
        cmd_agreement()
    elif cmd == "report":
        cmd_report()
    else:
        print(f"未知命令: {cmd}")
        print(__doc__)


if __name__ == "__main__":
    main()