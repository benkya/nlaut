"""视觉回归（SSIM）判定测试。

Layer A 元测试：纯函数，不依赖真实系统。
覆盖 SSIM 数值正确性 + 降级语义 + 路由行为。

关键断言：**降级态必须转人工，不能判通过**。
（2026-11-13 实测发现：arbiter 的 deterministic 分支只看 value>=0.5，
会把 value=0.5/confidence=0.5 的降级态误报为 auto_pass。）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nlaut.judge import DeterministicJudge, Evidence, Question
from nlaut.judge.arbiter import route
from nlaut.judge.visual_regression import (
    VisionUnavailableError,
    compare_images,
)

cv2 = pytest.importorskip("cv2", reason="视觉回归需要 opencv-python-headless")
np = pytest.importorskip("numpy")


@pytest.fixture
def img_pair(tmp_path):
    """生成两张可预期的图片：完全相同 / 局部差异 / 尺寸不同。"""
    base = np.zeros((200, 300, 3), dtype=np.uint8)
    base[:, :] = (30, 60, 90)
    base[50:150, 50:250] = (200, 120, 40)  # 一块色块

    p_base = tmp_path / "base.png"
    p_same = tmp_path / "same.png"
    p_diff = tmp_path / "diff.png"
    p_other = tmp_path / "other.png"

    cv2.imwrite(str(p_base), base)
    cv2.imwrite(str(p_same), base.copy())

    modified = base.copy()
    modified[60:70, 60:70] = (255, 255, 255)  # 小块像素变化
    cv2.imwrite(str(p_diff), modified)

    other = np.zeros((100, 150, 3), dtype=np.uint8)
    other[:, :] = (10, 200, 30)
    cv2.imwrite(str(p_other), other)

    return {
        "base": str(p_base),
        "same": str(p_same),
        "diff": str(p_diff),
        "other": str(p_other),
    }


class TestSSIMValues:
    """SSIM 数值正确性。"""

    def test_identical_images_score_one(self, img_pair):
        d = compare_images(img_pair["base"], img_pair["same"])
        assert d.similarity == pytest.approx(1.0, abs=1e-6)
        assert d.size_matched is True

    def test_small_pixel_change_scores_below_one(self, img_pair):
        d = compare_images(img_pair["base"], img_pair["diff"])
        assert 0.0 <= d.similarity < 1.0
        assert d.similarity > 0.9  # 小改动不该把分数打到很低

    def test_different_size_flagged(self, img_pair):
        d = compare_images(img_pair["base"], img_pair["other"])
        assert d.size_matched is False
        assert d.baseline_size == (300, 200)
        assert d.current_size == (150, 100)

    def test_missing_baseline_raises(self, img_pair, tmp_path):
        with pytest.raises(VisionUnavailableError, match="基线截图不存在"):
            compare_images(str(tmp_path / "nope.png"), img_pair["base"])

    def test_missing_current_raises(self, img_pair, tmp_path):
        with pytest.raises(VisionUnavailableError, match="当前截图不存在"):
            compare_images(img_pair["base"], str(tmp_path / "nope.png"))


class TestJudgeRouting:
    """判定路由：正常判定 + 降级语义。"""

    def _q(self, **kw):
        return Question(kind="noul", text="", context={"mode": "visual_regression", **kw})

    def test_identical_passes(self, img_pair):
        j = DeterministicJudge()
        v = j.judge(
            Evidence(case_id="t1", screenshot=img_pair["same"]),
            self._q(baseline=img_pair["base"], min_similarity=0.98),
        )
        assert v.value == 1.0
        assert v.confidence == 1.0
        assert route(v).status == "auto_pass"

    def test_below_threshold_fails(self, img_pair):
        j = DeterministicJudge()
        v = j.judge(
            Evidence(case_id="t2", screenshot=img_pair["diff"]),
            self._q(baseline=img_pair["base"], min_similarity=0.999),
        )
        assert v.value == 0.0
        assert route(v).status == "auto_fail"

    def test_size_mismatch_fails_when_required(self, img_pair):
        j = DeterministicJudge()
        v = j.judge(
            Evidence(case_id="t3", screenshot=img_pair["other"]),
            self._q(baseline=img_pair["base"], min_similarity=0.5, require_same_size=True),
        )
        assert v.value == 0.0
        assert v.raw["size_matched"] is False

    def test_size_mismatch_allowed_when_not_required(self, img_pair):
        j = DeterministicJudge()
        v = j.judge(
            Evidence(case_id="t4", screenshot=img_pair["other"]),
            self._q(baseline=img_pair["base"], min_similarity=0.5, require_same_size=False),
        )
        # 尺寸不同但允许时，按 resize 后的分数判
        assert v.raw["size_matched"] is False
        assert v.value in (0.0, 1.0)

    def test_missing_baseline_degrades_to_human_review(self, img_pair, tmp_path):
        """关键回归：基线缺失不得判通过。"""
        j = DeterministicJudge()
        v = j.judge(
            Evidence(case_id="t5", screenshot=img_pair["same"]),
            self._q(baseline=str(tmp_path / "nope.png")),
        )
        assert v.value == 0.5
        assert v.confidence == 0.5
        assert v.raw["degraded"] == "vision_unavailable"
        assert route(v).status == "human_review"

    def test_no_screenshot_degrades_to_human_review(self, img_pair):
        j = DeterministicJudge()
        v = j.judge(Evidence(case_id="t6"), self._q(baseline=img_pair["base"]))
        assert v.value == 0.5
        assert v.raw["degraded"] == "no_screenshot"
        assert route(v).status == "human_review"


class TestArbiterDegradeGuard:
    """arbiter 层的降级守卫（全局影响，单独锁住）。"""

    def test_deterministic_low_confidence_goes_human_review(self):
        from nlaut.judge.protocol import Verdict

        v = Verdict(engine="deterministic", kind="noul", value=0.5, confidence=0.5)
        assert route(v).status == "human_review"

    def test_deterministic_full_confidence_still_auto(self):
        from nlaut.judge.protocol import Verdict

        v = Verdict(engine="deterministic", kind="noul", value=0.5, confidence=1.0)
        assert route(v).status == "auto_pass"
