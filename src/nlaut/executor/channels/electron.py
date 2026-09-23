"""L4 electron 通道桩（M2）：IR steps → CDP/Electron 动作序列。

已验证的坑位（实现时必须遵守）：
1. PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1（用 App 自带 Electron，免 Chromium 下载）
2. _electron.launch(args=["--no-sandbox"])
3. IDE 插件 webview CDP 不可达 → 降级 AX-tree
4. e2e 探针（__e2eAppReady 等）prod 构建通常保留，可作就绪信号
"""

from ..ir.model import TestCaseIR


def execute(ir: TestCaseIR) -> None:
    raise NotImplementedError("M2: electron 通道待实现")
