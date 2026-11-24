"""upload 步骤（v0.5.0）的测试。

背景：CeCoder 工序节点要求上传基线资产，`fill` 不支持 input[type=file]
（LH-RW-006/007 因此标 blocked）。本步骤类型用 set_input_files 填缺口。

覆盖：
1. IR schema：StepUpload 校验（files 必填非空、selector 默认值、Step 联合判别）
2. 通道执行：_do_upload 正确注入文件、~ 展开与 $var 解析、缺文件抛错不静默
3. web / electron 两个 _run_step 均能分发 upload 分支
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from nlaut.executor.channels import electron, web
from nlaut.ir.model import StepUpload


def _ir():
    """_run_step 只依赖 ir.id，用轻量替身避免构造完整合法 IR（噪音大）。"""
    from types import SimpleNamespace

    return SimpleNamespace(id="tc_upload_x")


class FakePage:
    """记录 set_input_files 调用的 Playwright page 替身。"""

    def __init__(self):
        self.calls: list[tuple[str, list[str], int]] = []

    def set_input_files(self, selector, files, timeout):
        self.calls.append((selector, list(files), timeout))


# ---------- 1. IR schema ----------


def test_upload_schema_requires_files():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        StepUpload(action="upload", files=[])  # files 至少 1 项
    with pytest.raises(ValidationError):
        StepUpload()  # 缺 files 字段


def test_upload_schema_defaults():
    s = StepUpload(action="upload", files=["~/a.txt"])
    assert s.action == "upload"
    assert s.selector == "input[type=file]"  # 默认选择器
    assert s.timeout_ms == 10000
    assert s.optional is False


def test_upload_in_step_union():
    """action 判别：YAML 里 action: upload 能被 Step 联合正确解析。"""
    from pydantic import TypeAdapter

    from nlaut.ir.model import Step

    step = TypeAdapter(Step).validate_python(
        {"action": "upload", "files": ["data/变更资产清单.yaml"]}
    )
    assert isinstance(step, StepUpload)
    assert step.selector == "input[type=file]"


# ---------- 2. 通道执行 ----------


def test_do_upload_injects_files(tmp_path):
    f1 = tmp_path / "变更资产清单.yaml"
    f1.write_text("a: 1", encoding="utf-8")
    f2 = tmp_path / "影响脑图.json"
    f2.write_text("{}", encoding="utf-8")

    page = FakePage()
    step = StepUpload(action="upload", files=[str(f1), str(f2)])
    _do_upload_checked(page, step)
    assert page.calls == [("input[type=file]", [str(f1), str(f2)], 10000)]


def test_do_upload_expands_home_and_vars(tmp_path, monkeypatch):
    real = tmp_path / "real.md"
    real.write_text("x", encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    # HOME 下的文件 + $var 引用
    (tmp_path / "proj").mkdir()
    proj_file = tmp_path / "proj" / "程序设计文档.md"
    proj_file.write_text("doc", encoding="utf-8")

    page = FakePage()
    step = StepUpload(action="upload", files=["~/proj/$doc"])
    _do_upload_checked(page, step, data={"doc": "程序设计文档.md"})
    assert page.calls[0][1] == [str(proj_file)]


def test_do_upload_missing_file_raises(tmp_path):
    page = FakePage()
    step = StepUpload(action="upload", files=[str(tmp_path / "不存在.md")])
    with pytest.raises(FileNotFoundError):
        _do_upload_checked(page, step)
    assert page.calls == []  # 未注入任何文件


def _do_upload_checked(page, step, data=None, chan=web):
    """统一走通道的 _run_step 分发（而不是直接调 _do_upload），验证接线。"""
    ir = _ir()
    logs: list[str] = []
    if chan is web:
        web._run_step(page, step, ir, 1, data or {}, "", logs.append)
    else:
        electron._run_step(page, step, ir, 1, data or {}, "", logs.append)
    assert any("upload" in l for l in logs)  # 执行留痕
    return page


def test_run_step_dispatch_electron(tmp_path):
    """electron 通道同样分发 upload（复用 web._do_upload）。"""
    f = tmp_path / "doc.md"
    f.write_text("x", encoding="utf-8")
    page = FakePage()
    step = StepUpload(action="upload", selector='input[name=attachment]', files=[str(f)])
    _do_upload_checked(page, step, chan=electron)
    assert page.calls[0][0] == 'input[name=attachment]'


def test_upload_optional_step_degrades(tmp_path):
    """optional=True 时缺文件降级跳过（由 execute 的 optional 兜底层负责）。

    这里只验证：_do_upload 抛 FileNotFoundError、上层 optional 分支捕获类型正确。
    """
    page = FakePage()
    step = StepUpload(action="upload", files=[str(tmp_path / "缺.md")], optional=True)
    with pytest.raises(FileNotFoundError):
        _do_upload_checked(page, step)
