# -*- coding: utf-8 -*-
"""大模型能力批次选集守卫（cases/llm 目录即集合边界）

背景（2026-11-07 用户发现）：tc_agent_verify_001（集成准入门禁 G-11，
system_prompt「你是软件工厂的任务创建服务」）位于 cases/demo 且
channel:api + priority:P0，被 run_llm_batch 的 api+P0 全局过滤捞进
大模型能力批次——报告里出现「集成准入门禁」，语义不符且三批
need_review 拖低 P0 通过率 2.1pp（46/47 vs 46/46）。

本测试锁定：llm_suite_case_ids 的选集 == cases/llm 目录，
任何其他目录（demo/giencoder/sweng/...）的用例不得混入。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from nlaut.executor.runner import llm_suite_case_ids  # noqa: E402


def test_llm_suite_excludes_other_dirs():
    """选集不得包含 cases/llm 之外的用例（demo/giencoder/sweng 等）"""
    ids = set(llm_suite_case_ids())
    forbidden_prefixes = ("tc_agent_verify_", "tc_giencwb_", "tc_sweng_")
    leaked = [i for i in ids if i.startswith(forbidden_prefixes)]
    assert not leaked, f"大模型能力批次混入非 llm 用例: {leaked}"


def test_llm_suite_count_matches_dir():
    """选集数量 == cases/llm 目录的 yaml 文件数"""
    n_yaml = sum(1 for f in os.listdir(os.path.join(ROOT, "cases", "llm"))
                 if f.endswith((".yaml", ".yml")))
    ids = llm_suite_case_ids()
    assert len(ids) == n_yaml, f"选集 {len(ids)} != 目录文件数 {n_yaml}"


def test_p0_filter_returns_46_and_excludes_agent_verify():
    """P0 过滤：46 条且不含 tc_agent_verify_001"""
    p0 = llm_suite_case_ids(["P0"])
    assert "tc_agent_verify_001" not in p0, "tc_agent_verify_001 不得进入大模型 P0 批次"
    assert len(p0) == 46, f"P0 应为 46 条（cases/llm 口径），实际 {len(p0)}"


def test_all_ids_unique():
    """id 无重复"""
    ids = llm_suite_case_ids()
    assert len(ids) == len(set(ids)), "选集存在重复 id"