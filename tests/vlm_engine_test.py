"""VLM 引擎单元测试（纯函数，不加载模型）+ mlx 集成测试（需权重，默认跳过）。

集成测试运行方式（权重已下载时）:
    uv run pytest tests/vlm_engine_test.py -m mlx -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nlaut.judge import Evidence, Question, route
from nlaut.judge.vlm_mlx import MlxVlmJudge, parse_yes_no

pytestmark_vlm_unit = pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("yes", True), ("Yes", True), ("是", True), ("true", True),
        ("no", False), ("No", False), ("否", False), ("false", False),
        ("maybe", None), ("", None), ("我觉得可能吧", None),
    ],
)


class TestParseYesNo:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("yes", True), ("Yes", True), ("是", True), ("true", True),
            ("no", False), ("No", False), ("否", False), ("false", False),
            ("maybe", None), ("", None), ("我觉得可能吧", None),
        ],
    )
    def test_parse(self, text, expected):
        assert parse_yes_no(text) is expected


class TestMlxVlmJudgeContract:
    """不加载模型的协议契约测试。"""

    def test_wrong_kind_rejected(self):
        with pytest.raises(ValueError):
            MlxVlmJudge().judge(Evidence(case_id="t"), Question(kind="score", text="x"))

    def test_missing_screenshot_rejected(self):
        with pytest.raises(ValueError):
            MlxVlmJudge().judge(Evidence(case_id="t"), Question(kind="noul", text="x"))


@pytest.mark.mlx
class TestMlxVlmIntegration:
    """真实推理集成测试：金标准第一题 + 路由联动（约 10s）。"""

    GOLDEN = ROOT / "tests/judge_golden/images/login_success.png"

    def test_judge_and_route(self):
        ev = Evidence(case_id="tc_it_vlm_001", screenshot=str(self.GOLDEN))
        q = Question(
            kind="noul",
            text="页面是否呈现登录成功状态（出现欢迎/工作台元素，且没有错误提示）？只答 yes 或 no。",
        )
        v = MlxVlmJudge().judge(ev, q)
        assert v.engine == "mlx-vlm" and v.kind == "noul"
        assert v.raw["temperature"] == 0.0
        assert v.raw["parsed"] is True  # 金标准: 登录成功图 → yes
        assert v.value == 1.0
        route(v)  # 不崩溃即通过（auto/human 取决于 logprob 代理）
