from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from .build_info import BUILD_ID, PRODUCT_VERSION
from .storage_v2 import runtime_root


def runtime_log_root() -> Path:
    """Return DG4 runtime logs, separate from business/security databases."""
    root = runtime_root() / "logs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def install_unhandled_exception_hook() -> None:
    """Persist unexpected Python exceptions and show a user-friendly message.

    This is deliberately limited to the process-level unhandled-exception hook.
    It does not change any business exception handling inside the application.
    """
    previous = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc_value, exc_tb)
            return
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = runtime_log_root() / f"crash_{stamp}.log"
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        header = (
            f"Chromatic Analysis {PRODUCT_VERSION}\n"
            f"build={BUILD_ID}\n"
            f"created_at={time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        )
        try:
            path.write_text(header + text, encoding="utf-8")
        except Exception:
            path = None
        try:
            suffix = f"\n\n错误日志：{path}" if path else ""
            QMessageBox.critical(
                None,
                "Chromatic Analysis - 程序异常",
                "程序遇到未处理的异常。已尽量保存错误日志，请保留现场并联系维护人员。" + suffix,
            )
        except Exception:
            pass
        previous(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
