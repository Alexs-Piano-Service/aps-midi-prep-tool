"""A failed GUI assertion must not turn worker cleanup into a process abort."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap


def test_failed_boot_dialog_test_stops_worker_before_deleting_parent(tmp_path):
    test_file = tmp_path / "test_failed_boot_dialog.py"
    test_file.write_text(textwrap.dedent("""
        import threading
        import time

        import pytest

        from conftest import qt_application, _clean_up_test_widgets
        from test_boot_sector_dialog import window, _finish_boot_repair_workers
        from aps_midi_prep_tool_app import boot_sector_dialog
        from aps_midi_prep_tool_app.boot_sector_repair import ImageRepairBatch

        def test_assertion_after_worker_start(window, monkeypatch):
            entered = threading.Event()

            def batch(_source, **options):
                entered.set()
                while not options["cancel_callback"]():
                    time.sleep(0.005)
                from pathlib import Path
                Path(__file__).with_suffix(".stopped").write_text("worker stopped")
                return ImageRepairBatch((), cancelled=True)

            monkeypatch.setattr(boot_sector_dialog, "repair_boot_sector_batch", batch)
            dialog = boot_sector_dialog.BootSectorRepairDialog(window)
            dialog.path_edit.setText("unused mocked input")
            dialog.run_button.click()
            assert entered.wait(5), "Worker did not enter the mocked batch"
            pytest.fail("intentional assertion after worker started")
    """), encoding="utf-8")
    repository = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    # Preserve runtime import paths when the parent test run uses an isolated
    # PySide build with dependencies provided by the system Python packages.
    environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([
        str(repository), str(repository / "tests"), *sys.path,
    ]))
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(test_file)],
        cwd=repository, env=environment, capture_output=True, text=True, timeout=20,
    )
    report = result.stdout + result.stderr
    assert result.returncode == 1, report
    assert "intentional assertion after worker started" in report
    assert "1 failed" in report
    assert "QThread: Destroyed" not in report
    assert test_file.with_suffix(".stopped").read_text() == "worker stopped"
