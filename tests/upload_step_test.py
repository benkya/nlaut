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


class FakeFileChooser:
    def __init__(self):
        self.files: list[str] | None = None

    def set_files(self, paths):
        self.files = list(paths)


class _FakeContext:
    """expect_file_chooser 的上下文管理器替身。"""

    def __init__(self, chooser, raise_on_exit=False):
        self._chooser = chooser
        self._raise = raise_on_exit
        self.value = chooser

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._raise:
            raise TimeoutError("no file chooser")
        return False


class _FakeLocator:
    def __init__(self, page, sel):
        self.page = page
        self.sel = sel

    def count(self):
        return self.page.match_counts.get(self.sel, 1)


class FakePage:
    """记录 set_input_files / click / filechooser 的 Playwright page 替身。"""

    def __init__(self, match_counts=None, chooser_ok=True):
        self.calls: list[tuple[str, list[str], int]] = []
        self.clicks: list[str] = []
        self.match_counts = match_counts or {}
        self.chooser = FakeFileChooser()
        self._chooser_ok = chooser_ok

    def set_input_files(self, selector, files, timeout):
        self.calls.append((selector, list(files), timeout))

    def locator(self, sel):
        return _FakeLocator(self, sel)

    def click(self, sel):
        self.clicks.append(sel)

    def expect_file_chooser(self, timeout):
        return _FakeContext(self.chooser, raise_on_exit=not self._chooser_ok)


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


# ---------- 3. trigger 模式（真机实测：页面有多个隐藏 input 时必须用） ----------


def test_upload_trigger_mode_uses_file_chooser(tmp_path):
    """trigger 模式：点按钮触发 filechooser，再 set_files。"""
    f1 = tmp_path / "变更资产清单.yaml"
    f1.write_text("a: 1", encoding="utf-8")
    f2 = tmp_path / "变更资产清单.json"
    f2.write_text("{}", encoding="utf-8")

    page = FakePage()
    step = StepUpload(
        action="upload",
        trigger='button:has-text("上传附件")',
        files=[str(f1), str(f2)],
    )
    _do_upload_checked(page, step)
    assert page.clicks == ['button:has-text("上传附件")']
    assert page.chooser.files == [str(f1), str(f2)]
    assert page.calls == []  # trigger 模式不走 set_input_files


def test_upload_trigger_not_firing_raises(tmp_path):
    """按钮点了没触发 filechooser → 明确报错（不静默当成功）。"""
    f = tmp_path / "x.md"
    f.write_text("x", encoding="utf-8")
    page = FakePage(chooser_ok=False)
    step = StepUpload(action="upload", trigger='button:has-text("上传附件")', files=[str(f)])
    with pytest.raises(RuntimeError, match="未触发文件选择器"):
        _do_upload_checked(page, step)


def test_upload_multi_match_raises_with_hint(tmp_path):
    """selector 命中多个 → 报错并提示改用 trigger（真机 3 个 file input 的场景）。"""
    f = tmp_path / "x.md"
    f.write_text("x", encoding="utf-8")
    page = FakePage(match_counts={"input[type=file]": 3})
    step = StepUpload(action="upload", files=[str(f)])
    with pytest.raises(RuntimeError, match="命中 3 个元素"):
        _do_upload_checked(page, step)
    assert page.calls == []


def test_upload_zero_match_raises_with_hint(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("x", encoding="utf-8")
    page = FakePage(match_counts={"input[type=file]": 0})
    step = StepUpload(action="upload", files=[str(f)])
    with pytest.raises(RuntimeError, match="未命中任何元素"):
        _do_upload_checked(page, step)


def test_upload_schema_trigger_field():
    s = StepUpload(action="upload", trigger='button:has-text("上传")', files=["a.md"])
    assert s.trigger == 'button:has-text("上传")'
    assert StepUpload(action="upload", files=["a.md"]).trigger is None


# ---------- 4. 数据集注入（能力归框架、数据归用例，禁止硬编码资产） ----------


def test_upload_files_from_dataset_vars(tmp_path):
    """files 里的 $var 由 data_ref 数据集解析——用例不内嵌资产路径。"""
    a1 = tmp_path / "资产一.yaml"
    a1.write_text("a: 1", encoding="utf-8")
    a2 = tmp_path / "资产二.json"
    a2.write_text("{}", encoding="utf-8")

    page = FakePage()
    step = StepUpload(action="upload", files=["$asset_1", "$asset_2"])
    _do_upload_checked(
        page, step, data={"asset_1": str(a1), "asset_2": str(a2)}
    )
    assert page.calls[0][1] == [str(a1), str(a2)]


def test_upload_unknown_dataset_var_raises(tmp_path):
    """数据集缺字段 → KeyError（不静默留空路径）。"""
    page = FakePage()
    step = StepUpload(action="upload", files=["$asset_missing"])
    with pytest.raises(KeyError):
        _do_upload_checked(page, step, data={"asset_1": "x"})


def test_upload_trigger_supports_vars(tmp_path):
    """trigger 选择器本身也支持 $var（不同环境按钮文案可能不同）。"""
    f = tmp_path / "a.md"
    f.write_text("x", encoding="utf-8")
    page = FakePage()
    step = StepUpload(action="upload", trigger="$upload_btn", files=[str(f)])
    _do_upload_checked(page, step, data={"upload_btn": 'button:has-text("上传附件")'})
    assert page.clicks == ['button:has-text("上传附件")']
