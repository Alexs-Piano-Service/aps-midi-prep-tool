"""Keep Qt objects within the lifetime of the tests that create them."""

import gc
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qt_application():
    # Retain one application throughout the run. Do not explicitly destroy it:
    # PySide owns its shutdown and other session fixtures may still reference it.
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _clean_up_test_widgets(qt_application):
    # Broader-scoped fixtures are already initialized. Retaining the wrappers
    # also prevents Python IDs from being reused during this test.
    existing_widgets = set(qt_application.topLevelWidgets())
    yield

    created_widgets = [
        widget for widget in qt_application.topLevelWidgets()
        if widget not in existing_widgets
    ]
    for widget in created_widgets:
        # close() can open discard prompts and does not normally destroy a
        # widget. Hide it and request deletion after its test fixture finishes.
        widget.hide()
        widget.deleteLater()

    # processEvents() alone leaves DeferredDelete queued outside a Qt event
    # loop. Dispatch only deletions here, not timers left behind by a test whose
    # monkeypatches have already been undone.
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    if created_widgets:
        gc.collect()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
