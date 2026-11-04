"""让 Windows 控制台正确显示中文。

Python 在 Windows 上默认按 cp936（GBK）写 stdout，如果终端被切到 UTF-8
代码页（chcp 65001），中文就会变成乱码；反过来也一样。这里读取控制台
实际代码页，把输出流对准它。
"""

from __future__ import annotations

import sys


def fix_console_encoding() -> None:
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                continue
            import ctypes

            codepage = ctypes.windll.kernel32.GetConsoleOutputCP()
            encoding = "utf-8" if codepage == 65001 else f"cp{codepage}"
            stream.reconfigure(encoding=encoding, errors="replace")
        except Exception:
            pass
