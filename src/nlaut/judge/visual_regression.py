"""视觉回归：SSIM 像素级比对（零 AI token）。

来源与借鉴：算法与 AWT（AI Watch Tester, `aat-devqa`）的 `visual/comparator.py`
一致 —— SSIM（Wang et al. 2004）+ 纯 OpenCV 实现，不依赖 scikit-image。
本模块是**独立实现**，与 AWT 无运行时耦合，不调用其 CLI。

为什么 nlaut 需要它：
- `visual_state`（VLM）判的是「语义对不对」——文字写错但位置没变，VLM 能发现
- SSIM 判的是「像素变了没」——布局位移、样式回归、元素错位，VLM 常漏

两者不重复，是互补关系（详见 docs/awt-borrowing-analysis.md §3.1）。

依赖：opencv-python-headless + numpy。缺失时判定降级为「引擎不可用」而非误判通过。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# SSIM 参数（Wang et al. 2004，与 AWT 实现一致）
_C1 = (0.01 * 255) ** 2
_C2 = (0.03 * 255) ** 2
_WINDOW = (11, 11)
_SIGMA = 1.5

# 差异像素阈值（用于生成 diff 图，不参与 SSIM 分数计算）
_DIFF_THRESHOLD = 25


@dataclass(frozen=True)
class VisualDiff:
    """视觉比对结果。"""

    similarity: float
    """SSIM 分数 [0,1]，1.0 = 完全一致。"""

    baseline: str
    current: str
    size_matched: bool
    """两图尺寸是否一致。尺寸不同会被 resize 后再比——分数含义会退化。"""

    baseline_size: tuple[int, int] | None = None
    current_size: tuple[int, int] | None = None

    diff_image: str | None = None
    """差异高亮图路径（生成失败时为 None）。"""


class VisionUnavailableError(RuntimeError):
    """opencv / numpy 缺失，或图片无法解码。"""


def _require_cv():
    try:
        import cv2
        import numpy as np
    except ImportError as e:
        raise VisionUnavailableError(
            "视觉回归需要 opencv-python-headless 与 numpy。"
            "安装: uv add opencv-python-headless numpy"
        ) from e
    return cv2, np


def _decode(path: str, cv2, np):
    arr = np.fromfile(path, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise VisionUnavailableError(f"图片无法解码: {path}")
    return img


def _ssim_gray(a, b, cv2, np) -> float:
    """两张灰度 float64 图的 SSIM。"""
    mu_a = cv2.GaussianBlur(a, _WINDOW, _SIGMA)
    mu_b = cv2.GaussianBlur(b, _WINDOW, _SIGMA)

    mu_a_sq = mu_a * mu_a
    mu_b_sq = mu_b * mu_b
    mu_ab = mu_a * mu_b

    sigma_a_sq = cv2.GaussianBlur(a * a, _WINDOW, _SIGMA) - mu_a_sq
    sigma_b_sq = cv2.GaussianBlur(b * b, _WINDOW, _SIGMA) - mu_b_sq
    sigma_ab = cv2.GaussianBlur(a * b, _WINDOW, _SIGMA) - mu_ab

    numerator = (2 * mu_ab + _C1) * (2 * sigma_ab + _C2)
    denominator = (mu_a_sq + mu_b_sq + _C1) * (sigma_a_sq + sigma_b_sq + _C2)
    return float(np.mean(numerator / denominator))


def compare_images(
    baseline_path: str,
    current_path: str,
    *,
    diff_out: str | None = None,
) -> VisualDiff:
    """比对基线截图与当前截图，返回 SSIM 分数与差异图。

    参数
    ----
    baseline_path : 基线截图路径
    current_path  : 当前截图路径
    diff_out      : 差异高亮图输出路径（可选；生成失败不影响分数）

    尺寸处理：两图尺寸不同时按较小边 resize 后比对，并在结果里标记
    ``size_matched=False`` —— 此时分数只反映「缩放后的差异」，不能直接
    当作「界面没变」的证据。
    """
    cv2, np = _require_cv()

    if not Path(baseline_path).exists():
        raise VisionUnavailableError(f"基线截图不存在: {baseline_path}")
    if not Path(current_path).exists():
        raise VisionUnavailableError(f"当前截图不存在: {current_path}")

    a = _decode(baseline_path, cv2, np)
    b = _decode(current_path, cv2, np)
    baseline_size = (a.shape[1], a.shape[0])
    current_size = (b.shape[1], b.shape[0])
    size_matched = a.shape == b.shape

    if not size_matched:
        h = min(a.shape[0], b.shape[0])
        w = min(a.shape[1], b.shape[1])
        a = cv2.resize(a, (w, h), interpolation=cv2.INTER_AREA)
        b = cv2.resize(b, (w, h), interpolation=cv2.INTER_AREA)

    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float64)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float64)
    similarity = _ssim_gray(ga, gb, cv2, np)

    diff_path = None
    if diff_out:
        diff_path = _write_diff(a, b, diff_out, cv2, np)

    return VisualDiff(
        similarity=similarity,
        baseline=baseline_path,
        current=current_path,
        size_matched=size_matched,
        baseline_size=baseline_size,
        current_size=current_size,
        diff_image=diff_path,
    )


def _write_diff(a, b, out_path: str, cv2, np) -> str | None:
    """生成差异高亮图（当前图 + 红色标记变化像素）。"""
    try:
        diff_gray = cv2.cvtColor(cv2.absdiff(a, b), cv2.COLOR_BGR2GRAY)
        _, mask = cv2.threshold(diff_gray, _DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        mask = cv2.dilate(mask, kernel, iterations=2)
        overlay = b.copy()
        overlay[mask > 0] = (0, 0, 255)  # BGR 红
        result = cv2.addWeighted(b, 0.5, overlay, 0.5, 0)
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        ok, buf = cv2.imencode(".png", result)
        if not ok:
            return None
        out.write_bytes(bytes(buf))
        return str(out)
    except Exception:  # noqa: BLE001 — 差异图是辅助产物，失败不应影响判定
        return None


def snapshot(viewport_only: bool = True) -> None:
    """占位：基线快照的采集入口在通道层（见 docs/visual-regression.md）。"""
    raise NotImplementedError
