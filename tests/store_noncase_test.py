"""IRStore 非用例目录排除的测试。

背景（2026-11-24 实测）：upload 步骤的测试资产放在 cases/cecoder/assets/
下，IRStore 用 rglob("*.yaml") 递归扫描，把资产 YAML 当 IR 校验 →
缺 id/title/steps → ValidationError 炸掉整库加载。

框架与数据的边界：能力归框架，测试资产归用例侧。资产可能和用例同目录树，
所以扫描层必须能区分「用例」与「夹具」。
"""
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nlaut.ir.store import NON_CASE_DIRS, IRStore

CASE_YAML = """
id: tc_ok_001
title: 合法用例
source: 单测夹具
channel: web
quad: {actor: 测试, action: 跑, object: 页面, preconditions: []}
steps:
  - action: nav
    path: '/'
assertions:
  - kind: text_visible
    selector: body
    expected: ok
"""

# 故意不合 IR schema 的资产文件（只有业务字段）
ASSET_YAML = """
task: LH-RW-006
assets:
  - path: src/a.ts
    change: 改动
"""


def _write(root: Path, rel: str, content: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_assets_dir_excluded_from_ir_loading(tmp_path):
    """cases/x/assets/*.yaml 不参与 IR 校验，load_all 不炸。"""
    _write(tmp_path, "x/tc_ok_001.yaml", CASE_YAML)
    _write(tmp_path, "x/assets/变更资产清单.yaml", ASSET_YAML)

    store = IRStore(tmp_path)
    loaded = store.load_all()
    assert [ir.id for ir in loaded] == ["tc_ok_001"]
    # 注意：不能对绝对路径断言——tmp_path 目录名由测试名生成，本身可能含 assets。
    # 只看相对 root 的目录部分。
    assert all("assets" not in p.relative_to(tmp_path).parts for p in store._yaml_files())


def test_all_non_case_dirs_excluded(tmp_path):
    """NON_CASE_DIRS 里每个目录名的 yaml 都被排除。"""
    _write(tmp_path, "tc_ok_001.yaml", CASE_YAML)
    for d in NON_CASE_DIRS:
        _write(tmp_path, f"proj/{d}/f.yaml", ASSET_YAML)

    assert [ir.id for ir in IRStore(tmp_path).load_all()] == ["tc_ok_001"]


def test_case_named_assets_yaml_still_loaded(tmp_path):
    """文件名叫 assets.yaml 但在用例目录内 → 仍是用例（只排除目录名，不排文件名）。"""
    _write(tmp_path, "assets.yaml", CASE_YAML)
    assert [ir.id for ir in IRStore(tmp_path).load_all()] == ["tc_ok_001"]


def test_invalid_case_still_fails_fast(tmp_path):
    """排除资产不等于放宽校验：用例文件不合 schema 仍 fail-fast。"""
    _write(tmp_path, "tc_bad.yaml", "id: tc_bad\n")

    with pytest.raises(ValidationError):
        IRStore(tmp_path).load_all()
