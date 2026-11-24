"""judge 标定双标工作流工具的测试。

v0.5.0 新增。要点：
1. Kappa 算法正确（含边际偏斜陷阱）
2. 导出 → 回填 → 统计 的往返连通
3. **不伪造人工标注** —— 工具只搬运真人填的内容
"""
import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from dual_annotation import (
    Sample,
    _as_bool,
    cohens_kappa,
    compute_stats,
    derive_mechanical,
    export_worklist,
    import_annotations,
    load_samples,
    save_samples,
)


class TestAsBool:
    @pytest.mark.parametrize(
        "raw,expect",
        [
            (True, True), (False, False),
            ("true", True), ("false", False),
            ("TRUE", True), ("False", False),
            ("1", True), ("0", False),
            ("yes", True), ("no", False),
            ("是", True), ("否", False),
            ("通过", True), ("不通过", False),
            ("成立", True), ("不成立", False),
            ("", None), (None, None), ("garbage", None),
        ],
    )
    def test_parse(self, raw, expect):
        assert _as_bool(raw) is expect


class TestCohensKappa:
    def test_perfect_agreement(self):
        assert cohens_kappa([(True, True)] * 10) == pytest.approx(1.0)

    def test_perfect_disagreement(self):
        """完全对立 → Kappa = -1。"""
        assert cohens_kappa([(True, False)] * 5 + [(False, True)] * 5) == pytest.approx(-1.0)

    def test_marginal_skew_trap(self):
        """边际偏斜陷阱：观察一致率 80%，但 Pe=0.82 → Kappa 为负。

        这是 Kappa 的已知性质（预期一致率高于观察值），不是公式错。
        """
        pairs = [(True, True)] * 8 + [(True, False)] + [(False, True)]
        k = cohens_kappa(pairs)
        assert k == pytest.approx(-1 / 9)

    def test_insufficient_samples(self):
        assert cohens_kappa([]) is None
        assert cohens_kappa([(True, True)]) is None

    def test_all_same_category(self):
        """两人全判 True → Pe=1 → Kappa 定义为 1.0（完全一致）。"""
        assert cohens_kappa([(True, True)] * 5) == pytest.approx(1.0)


class TestSampleHelpers:
    def _s(self, **kw):
        base = {
            "id": "x", "domain": "V3", "difficulty": "easy", "category": "real_pass",
            "is_trap": False, "question": "q", "claimed_success": "c",
            "gold": True, "annotator_a": None, "annotator_b": None, "agreement": None,
        }
        base.update(kw)
        return Sample(**base)

    def test_has_dual(self):
        assert self._s(annotator_a=True, annotator_b=False).has_dual is True
        assert self._s(annotator_a=True).has_dual is False

    def test_stats_distinguishes_coverage_from_dual(self):
        """A 标了、B 没标 → 必须分开报，不能只报 dual=0 让人误以为没人标。"""
        ss = [self._s(annotator_a=True, annotator_b=None)]
        st = compute_stats(ss)
        assert st["annotated_a"] == 1
        assert st["annotated_b"] == 0
        assert st["dual_annotated"] == 0
        assert st["agreement_rate"] == 0.0

    def test_stats_with_dual(self):
        ss = [
            self._s(id="a", annotator_a=True, annotator_b=True),
            self._s(id="b", annotator_a=True, annotator_b=False),
        ]
        st = compute_stats(ss)
        assert st["dual_annotated"] == 2
        assert st["agreement_rate"] == 0.5
        assert st["disagree_ids"] == ["b"]

    def test_stats_gold_comparison(self):
        ss = [self._s(gold=True, annotator_a=True, annotator_b=False)]
        st = compute_stats(ss)
        assert st["gold_vs_a_rate"] == 1.0
        assert st["gold_vs_b_rate"] == 0.0


class TestRoundTrip:
    def _calib(self, tmp_path, n=3):
        p = tmp_path / "calib.json"
        samples = []
        for i in range(n):
            samples.append({
                "id": f"c{i}",
                "domain": "V3",
                "difficulty": "easy",
                "is_trap": False,
                "category": "real_pass",
                "question": f"问题{i}",
                "system_claim": {"claimed_success": "成功"},
                "human_annotation": {"judgement": True, "reason": "金标准"},
                "annotator_a": {"judgement": None},
                "annotator_b": {"judgement": None},
                "agreement": None,
            })
        p.write_text(json.dumps({"samples": samples, "metadata": {}}, ensure_ascii=False),
                     encoding="utf-8")
        return p

    def test_export_produces_fillable_csv(self, tmp_path):
        p = self._calib(tmp_path)
        samples = load_samples(p)
        out = tmp_path / "a.csv"
        n = export_worklist(samples, out)
        assert n == 3
        rows = list(csv.DictReader(out.read_text(encoding="utf-8-sig").splitlines()))
        assert len(rows) == 3
        assert rows[0]["judgement"] == ""  # 留给标注员填
        assert rows[0]["sample_id"] == "c0"

    def test_export_import_stats_roundtrip(self, tmp_path):
        p = self._calib(tmp_path)
        samples = load_samples(p)
        out = tmp_path / "a.csv"
        export_worklist(samples, out)

        # 模拟标注员填写
        rows = list(csv.DictReader(out.read_text(encoding="utf-8-sig").splitlines()))
        for r in rows:
            r["judgement"] = "true"
            r["reason"] = "标注员A判断"
        filled = tmp_path / "filled.csv"
        with filled.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

        samples = load_samples(p)
        ok, problems = import_annotations(samples, filled, "annotator_a")
        assert ok == 3
        assert problems == []
        save_samples(samples, p)

        reloaded = load_samples(p)
        assert all(s.annotator_a is True for s in reloaded)
        st = compute_stats(reloaded)
        assert st["annotated_a"] == 3
        assert st["dual_annotated"] == 0  # B 还没标

    def test_import_rejects_unknown_id(self, tmp_path):
        p = self._calib(tmp_path)
        samples = load_samples(p)
        bad = tmp_path / "bad.csv"
        bad.write_text(
            "sample_id,judgement,reason\nnonexistent,true,x\n",
            encoding="utf-8-sig",
        )
        ok, problems = import_annotations(samples, bad, "annotator_a")
        assert ok == 0
        assert len(problems) == 1
        assert "不存在" in problems[0]

    def test_import_rejects_unparseable_judgement(self, tmp_path):
        p = self._calib(tmp_path)
        samples = load_samples(p)
        bad = tmp_path / "bad.csv"
        bad.write_text(
            "sample_id,judgement,reason\nc0,也许,x\n",
            encoding="utf-8-sig",
        )
        ok, problems = import_annotations(samples, bad, "annotator_a")
        assert ok == 0
        assert "无法解析" in problems[0]

    def test_save_updates_metadata_status(self, tmp_path):
        p = self._calib(tmp_path)
        samples = load_samples(p)
        for s in samples:
            s.annotator_a = True
            s.annotator_b = True
        save_samples(samples, p)
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["metadata"]["annotation_status"] == "dual_complete"
        assert data["metadata"]["dual_annotated_count"] == 3
        assert data["samples"][0]["agreement"]["agree"] is True


class TestDerivedMechanical:
    def _s(self, category, gold):
        return Sample(
            id="x", domain="V3", difficulty="easy", category=category,
            is_trap=False, question="q", claimed_success="c",
            gold=gold, annotator_a=None, annotator_b=None, agreement=None,
        )

    def test_real_pass_derived_true(self):
        ss = derive_mechanical([self._s("real_pass", True)])
        assert ss[0].derived is True

    def test_real_fail_derived_false(self):
        ss = derive_mechanical([self._s("real_fail", False)])
        assert ss[0].derived is False

    def test_trap_not_derived(self):
        """陷阱样本判定需要理解语义 —— 机械推导不可靠，必须不推导。"""
        ss = derive_mechanical([self._s("trap", True)])
        assert ss[0].derived is None

    def test_derived_mismatch_flagged(self):
        """derived 与金标准不一致要被暴露 —— 这是它的核心用途。"""
        ss = derive_mechanical([self._s("real_pass", False)])  # 金标准说假
        assert ss[0].derived is True
        assert ss[0].derived != ss[0].gold


class TestOfficialFileIntegrity:
    def test_official_calibration_has_no_fake_annotations(self):
        """正式标定文件里不能有伪造的人工标注。

        若本工具被误用来「自动填」人工字段，这里会检测出来。
        """
        calib = Path(__file__).resolve().parent.parent / "config" / "calibrate_v2.json"
        if not calib.is_file():
            pytest.skip("标定文件不存在")
        data = json.loads(calib.read_text(encoding="utf-8"))
        for s in data.get("samples", []):
            for field in ("annotator_a", "annotator_b"):
                reason = (s.get(field) or {}).get("reason") or ""
                assert "模拟" not in reason, f"{s.get('id')}.{field} 含模拟标注"
                assert "测试" not in reason, f"{s.get('id')}.{field} 含测试标注"
