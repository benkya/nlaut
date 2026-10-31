"""LLM-as-Judge：无自主取证能力的判定器。

与 Agent-as-Judge 的本质区别：
- 只能读调用方拼进 prompt 的证据
- 无法调用工具自主取证
- 一轮调用，无多轮循环

适用于：
- 证据已经完整的场景
- 需要快速判定的场景
- 作为 Agent-as-Judge 的对照基线
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from .protocol import Evidence, Question, Verdict


class LLMJudge:
    """LLM-as-Judge 判定器"""

    def __init__(
        self,
        base_url: str = None,
        api_key: str = None,
        model: str = "deepseek-chat",
        timeout: float = 30.0,
    ):
        self.base_url = base_url or os.getenv("DEEPSEEK_V4_FLASH_URL", "http://10.62.64.38:30089/v1")
        self.api_key = api_key or os.getenv("DEEPSEEK_V4_FLASH_KEY", "sk-dummy")
        self.model = model
        self.timeout = timeout

    def _build_prompt(self, evidence: Evidence, question: Question) -> str:
        """构建判定 prompt"""
        # 提取证据信息
        db_state = evidence.db_state or {}
        claimed_success = evidence.llm_response or "N/A"
        
        prompt = f"""你是一个质量判定器，需要判定被测系统的自述是否正确。

【系统自述】
{claimed_success}

【判定问题】
{question.text}

【数据库状态】
```json
{json.dumps(db_state, ensure_ascii=False, indent=2)}
```

请根据以上信息，判定系统自述是否正确。

输出格式（严格遵守）：
judgement: true 或 false
理由: 一句话说明判定依据
置信度: High/Medium/Low

示例：
judgement: false
理由: 系统自述成功，但数据库状态显示表为空
置信度: High
"""
        return prompt

    def judge(self, evidence: Evidence, question: Question) -> Verdict:
        """判定"""
        start_time = time.time()
        prompt = self._build_prompt(evidence, question)
        
        # 调用 LLM API
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.0,  # 确定性输出
                    },
                )
                response.raise_for_status()
                result = response.json()
        except Exception as e:
            # 失败时返回降级 verdict
            return Verdict(
                engine="llm",
                kind=question.kind,
                value=0.0,
                confidence=0.0,
                evidence_refs=[],
                raw={"error": str(e), "degraded": True},
            )
        
        # 解析响应
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        
        # 解析判定结果
        judgement = self._parse_judgement(content)
        confidence = self._parse_confidence(content)
        
        elapsed = time.time() - start_time
        
        return Verdict(
            engine="llm",
            kind=question.kind,
            value=1.0 if judgement else 0.0,
            confidence=confidence,
            evidence_refs=[],
            raw={
                "content": content,
                "elapsed_s": elapsed,
                "model": self.model,
            },
        )

    def _parse_judgement(self, text: str) -> bool:
        """解析判定结果"""
        if not text:
            return False
        
        lower = text.lower()
        
        # 查找 judgement 标记
        for marker in ("judgement:", "judgement：", "判定:", "判定："):
            idx = lower.find(marker)
            if idx != -1:
                seg = lower[idx + len(marker): idx + len(marker) + 20]
                if any(p in seg for p in ("true", "yes", "通过", "成立", "是")):
                    return True
                if any(p in seg for p in ("false", "no", "不通过", "不成立", "否")):
                    return False
        
        # 启发式
        if any(p in lower[:300] for p in ("true", "yes", "通过", "成立")):
            return True
        if any(p in lower[:300] for p in ("false", "no", "不通过", "不成立")):
            return False
        
        return False

    def _parse_confidence(self, text: str) -> float:
        """解析置信度"""
        if not text:
            return 0.5
        
        lower = text.lower()
        
        # 查找置信度标记
        for marker in ("置信度:", "置信度：", "confidence:"):
            idx = lower.find(marker)
            if idx != -1:
                seg = lower[idx + len(marker): idx + len(marker) + 20]
                if "high" in seg or "高" in seg:
                    return 0.85
                if "medium" in seg or "中" in seg:
                    return 0.6
                if "low" in seg or "低" in seg:
                    return 0.3
        
        return 0.6  # 默认中等置信度