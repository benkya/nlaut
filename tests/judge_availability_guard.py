# -*- coding: utf-8 -*-
"""判定层可用性守卫（防止依赖缺失导致静默误判为模型失败）

背景（2026-10-27 实测事故）：
  DeepSeek-V4.1-Flash INT8 (sglang) 重跑批次的 P0 通过率从 44/46 暴跌到 18/46，
  看起来像模型能力退化。实际根因是 **httpx 缺 socksio**：
    环境变量 all_proxy=socks5://127.0.0.1:7897 存在时，httpx 在
    **初始化 client 阶段**就抛 ImportError，连 metadata 请求都没发出去。
    Laya judge（HF 权重）因此完全不可用 → 判定层崩 → 用例判定数为 0 / 无 run_end
    → runner 记为 failed/error → **被误读为模型能力问题**。

  判别特征：同一用例里确定性断言（response_contains 等）auto_pass，
            但 Laya noul 断言缺失，或整个 case 无 assertion_judged 事件。

本测试确保：
  1. socksio 依赖存在（httpx SOCKS 支持）
  2. Laya judge 在当前环境下能实际完成一次判定
"""
from __future__ import annotations

import os
import sys

import pytest


def test_socksio_installed():
    """httpx 的 SOCKS 代理支持依赖 socksio，缺失会让判定层整体不可用"""
    try:
        import socksio  # noqa: F401
    except ImportError as e:  # pragma: no cover
        pytest.fail(
            "socksio 未安装 —— Laya judge 会因 httpx 初始化失败而崩溃，"
            "表现为 P0 大量用例判定数为 0 / 无 run_end，被误判为模型能力退化。\n"
            "修复: uv pip install --python .venv/bin/python socksio\n"
            f"原始错误: {e}"
        )


def test_httpx_proxy_env_supported():
    """若环境设置了 SOCKS 代理，必须能 import socksio，否则 httpx client 建不起来"""
    proxy = os.environ.get("all_proxy") or os.environ.get("ALL_PROXY") or ""
    if proxy.startswith("socks"):
        try:
            import socksio  # noqa: F401
        except ImportError:
            pytest.fail(
                f"检测到 SOCKS 代理 {proxy} 但 socksio 未安装，"
                "httpx 无法初始化 → Laya judge 不可用")


def test_laya_judge_can_judge():
    """Laya judge 必须能完成一次真实判定（权重在本地 HF 缓存）"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, os.path.join(root, "src"))
    try:
        from nlaut.judge.protocol import Evidence, Question, get_judge
    except Exception as e:  # pragma: no cover
        pytest.fail(f"无法导入 judge 协议: {e}")

    try:
        judge = get_judge("laya")
    except Exception as e:  # pragma: no cover
        pytest.fail(f"get_judge('laya') 失败: {type(e).__name__}: {e}")

    ev = Evidence(case_id="guard_probe", llm_response="机器学习是让计算机从数据中自动学习规律的方法。")
    q = Question(text="这段文字是否在描述机器学习？只答 yes 或 no。", kind="noul")
    try:
        verdict = judge.judge(ev, q)
    except Exception as e:  # pragma: no cover
        pytest.fail(
            f"Laya judge 判定失败（判定层不可用，会让 P0 大量用例被误记为 failed）: "
            f"{type(e).__name__}: {e}\n"
            "常见根因: socksio 缺失 / HF 权重未缓存 / 代理不可达"
        )
    assert verdict is not None
    val = getattr(verdict, "value", None)
    assert val is not None, "judge 返回 None"
    assert 0.0 <= float(val) <= 1.0, f"noul 值越界: {val}"