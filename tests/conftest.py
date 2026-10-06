"""Keep Qt alive until test widgets and the offscreen clipboard are released."""
import gc

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session", autouse=True)
def qt_application():
    application = QApplication.instance() or QApplication([])
    yield application
    application.closeAllWindows()
    application.clipboard().clear()
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()
