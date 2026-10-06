import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from qtx_app.auth_store import AuthStore, login_or_create_first_admin
from qtx_app.main_window import MainWindow, QuickLab3DDialog, QUICK3D_IMPORT_ERROR
from qtx_app.build_info import BUILD_ID, BUILD_LABEL, PRODUCT_VERSION
from qtx_app.release_runtime import install_unhandled_exception_hook


def main():
    print(f"[{BUILD_ID}] application root: {ROOT}")
    print(f"[{BUILD_ID}] main_window: {Path(sys.modules['qtx_app.main_window'].__file__).resolve()}")
    print(f"[{BUILD_ID}] Quick3D class: {QuickLab3DDialog}")
    if QUICK3D_IMPORT_ERROR:
        print(f"[{BUILD_ID}] Quick3D import error: {QUICK3D_IMPORT_ERROR}")
    app = QApplication(sys.argv)
    app.setApplicationName("QTX 色彩分析")
    app.setApplicationVersion(PRODUCT_VERSION)
    install_unhandled_exception_hook()
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 9))

    # HF117: optional real-session UI responsiveness probe. It is completely
    # disabled during normal use and is enabled only by the diagnostics runner.
    _ui_perf_probe = None
    try:
        from qtx_app.ui_perf_probe import install_ui_perf_probe
        _ui_perf_probe = install_ui_perf_probe(app)
    except Exception as exc:
        print(f"[{BUILD_ID}] UI performance probe unavailable: {exc}")

    auth = AuthStore()
    user = login_or_create_first_admin(auth)
    if user is None:
        return 0

    window = MainWindow(current_user=user, auth_store=auth)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
