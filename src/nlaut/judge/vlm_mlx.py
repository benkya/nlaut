"""L5 视觉判定引擎：mlx-vlm（Qwen2.5-VL，本地 Apple Silicon）。

M0-poc 实测结论（2026-09-23, M3 Pro 18GB）：
- Qwen2.5-VL-3B-Instruct-4bit 金标准 6/6 全对，单题 5.3-6.1s（1280x900 截图）
- 模型加载 2-3s，权重约 3GB
- 温度 0（框架硬规则：禁止随机判定）

confidence 语义：Qwen-VLM 不输出原生校准置信度，取首 token logprob 的
exp() 作为代理（max_tokens=8 内 yes/no 是首 token）。该近似已写入
docs/judge-protocol.md 的引擎差异说明——VLM 判定默认走 human_review 通道，
除非 logprob 代理置信度 ≥0.9。
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from .protocol import Evidence, Question, Verdict

DEFAULT_MODEL = Path.home() / ".cache/nlaut-models/Qwen2.5-VL-3B-Instruct-4bit"

_YES = re.compile(r"^\s*(yes|是|对|true)", re.IGNORECASE)
_NO = re.compile(r"^\s*(no|否|错|false)", re.IGNORECASE)


def parse_yes_no(text: str) -> bool | None:
    """解析模型回答；无法解析返回 None（→ human_review）。"""
    if _YES.match(text):
        return True
    if _NO.match(text):
        return False
    return None


def confidence_from_output(output) -> float:
    """从 GenerationResult.logprobs 提取置信度代理。

    实测（Qwen2.5-VL-3B, temperature=0）: logprobs 形状为 (vocab_size,) 的
    mlx.core.array——整表给出每个 token 的 logprob，贪心采样下选中 token
    为最大值（实测 0.0 → 概率 1.0）。confidence = exp(max(logprobs))。
    旧实现 float(list(lp)[0]) 恰好取到位置 0 的噪声值且标量转换会抛错。
    """
    import mlx.core as mx

    logprobs = getattr(output, "logprobs", None)
    if logprobs is None:
        return 0.5
    try:
        arr = mx.array(logprobs)
        if arr.size == 0:
            return 0.5
        return min(1.0, math.exp(float(mx.max(arr))))
    except (TypeError, ValueError, IndexError):
        return 0.5


class MlxVlmJudge:
    """实现 Judge 协议的本地视觉判定引擎。模型懒加载（首次 judge 时载入）。"""

    engine = "mlx-vlm"

    def __init__(self, model_path: str | Path = DEFAULT_MODEL, max_tokens: int = 8) -> None:
        self.model_path = str(model_path)
        self.max_tokens = max_tokens
        self._model = None
        self._processor = None
        self._config = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        if not Path(self.model_path).exists():
            raise FileNotFoundError(
                f"VLM 权重不存在: {self.model_path}（先下载: 见 tests/judge_golden/poc_vlm.py 注释）"
            )
        from mlx_vlm import load
        from mlx_vlm.utils import load_config

        self._model, self._processor = load(self.model_path)
        self._config = load_config(self.model_path)

    def judge(self, evidence: Evidence, question: Question) -> Verdict:
        if question.kind != "noul":
            raise ValueError("MlxVlmJudge 仅支持 noul 问题（yes/no 视觉判定）")
        if not evidence.screenshot:
            raise ValueError("视觉判定需要 evidence.screenshot（截图路径）")
        self._ensure_loaded()

        from mlx_vlm import generate
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_image

        prompt = apply_chat_template(
            self._processor, self._config, prompt=question.text, num_images=1
        )
        image = load_image(str(evidence.screenshot))
        output = generate(
            self._model,
            self._processor,
            prompt,
            image,
            max_tokens=self.max_tokens,
            temperature=0.0,
        )
        text = getattr(output, "text", "") or str(output)
        parsed = parse_yes_no(text)
        value = 1.0 if parsed is True else (0.0 if parsed is False else 0.5)
        confidence = confidence_from_output(output) if parsed is not None else 0.0
        return Verdict(
            engine=self.engine,
            kind="noul",
            value=value,
            confidence=confidence,
            evidence_refs=[evidence.case_id],
            raw={
                "model": Path(self.model_path).name,
                "question": question.text[:200],
                "answer_text": text[:100],
                "parsed": parsed,
                "temperature": 0.0,
            },
        )
