"""Exercise Qt fixture teardown across real pytest test boundaries."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


LIFECYCLE_SUITE = """
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QWidget
from shiboken6 import isValid

retained = []
callbacks = []


@pytest.fixture(scope="module")
def shared_widget(qt_application):
    widget = QWidget()
    yield widget
    assert isValid(widget), "module fixture was destroyed by per-test cleanup"
    widget.deleteLater()


def test_01_leave_closed_and_deferred_widgets(qt_application, shared_widget):
    closed = QWidget()
    child = QDialog(closed)
    closed.cycle = child
    child.cycle = closed
    closed.close()

    deferred = QWidget()
    deferred.cycle = deferred
    deferred.deleteLater()
    qt_application.processEvents()
    assert isValid(deferred), "the reproduction must leave DeferredDelete pending"

    # Keep explicit references, so ordinary Python collection cannot make the
    # next test pass without the fixture disposing of these C++ objects.
    retained.extend((closed, child, deferred))
    QTimer.singleShot(0, lambda: callbacks.append("timer escaped its test"))


def test_02_previous_widgets_are_deleted(qt_application, shared_widget):
    assert isValid(shared_widget)
    assert QApplication.instance() is qt_application
    assert isValid(qt_application)
    assert not callbacks, "cleanup dispatched an unrelated timer"
    assert all(not isValid(widget) for widget in retained), "test widgets survived teardown"


def test_03_module_fixture_still_survives(qt_application, shared_widget):
    assert isValid(shared_widget)
    assert QApplication.instance() is qt_application
"""


@pytest.mark.parametrize("with_cleanup", [False, True], ids=["baseline", "shared-cleanup"])
def test_widgets_are_disposed_between_tests_without_destroying_shared_fixtures(tmp_path, with_cleanup):
    suite = tmp_path / "test_lifetime.py"
    suite.write_text(textwrap.dedent(LIFECYCLE_SUITE), encoding="utf-8")
    conftest = tmp_path / "conftest.py"
    if with_cleanup:
        conftest.write_text(Path(__file__).with_name("conftest.py").read_text(encoding="utf-8"), encoding="utf-8")
    else:
        conftest.write_text(textwrap.dedent("""
            import pytest
            from PySide6.QtWidgets import QApplication

            @pytest.fixture(scope="session")
            def qt_application():
                return QApplication.instance() or QApplication([])
        """), encoding="utf-8")

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--confcutdir", str(tmp_path), str(suite)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )
    output = completed.stdout + completed.stderr
    if with_cleanup:
        assert completed.returncode == 0, output
        assert "3 passed" in output
    else:
        assert completed.returncode == 1, output
        assert "test widgets survived teardown" in output
        assert "1 failed, 2 passed" in output
