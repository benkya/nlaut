"""L6 报告渲染桩（M3 里程碑实现）。

要求：分层统计（确定性断言 vs AI 判定的通过率/误报/漏报），
每条 AI 判定附证据链（截图 + 引擎原始输出 + 置信度 + source 追溯）。
"""

from __future__ import annotations

from pathlib import Path


def render_html(results: list[dict]) -> Path:
    raise NotImplementedError("M3: HTML 报告渲染待实现")
