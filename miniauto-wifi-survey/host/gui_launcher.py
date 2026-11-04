"""图形界面启动器。

用 pythonw.exe 运行时不带控制台窗口，任何启动异常都写进
output/gui_error.log，并尽量弹窗提示，避免"双击没反应"。
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _report(message: str) -> None:
    log_dir = ROOT / "output"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "gui_error.log").write_text(message, encoding="utf-8")
    except OSError:
        pass
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "控制台启动失败",
            f"{message.strip().splitlines()[-1]}\n\n完整信息已写入：\n{log_dir / 'gui_error.log'}",
        )
        root.destroy()
    except Exception:
        pass


def main() -> int:
    sys.path.insert(0, str(HERE))
    try:
        from survey_gui import main as gui_main

        gui_main()
        return 0
    except Exception:
        _report(traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
