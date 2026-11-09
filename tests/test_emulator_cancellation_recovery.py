"""Deliver cancellation recovery information through the real worker and GUI."""

import errno
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QMessageBox

from aps_midi_prep_tool_app import emulator_image_builder as builder, main_window
from aps_midi_prep_tool_app.disk_session_worker import EmulatorImageBuildWorker
from aps_midi_prep_tool_app.floppy_image import DISK_FORMAT_BY_KEY
from aps_midi_prep_tool_app.helpers import atomic_file, file_batch
from test_emulator_image_builder import _midi_bytes


@pytest.fixture
def window(qt_application, tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    for method in ("_log_event", "_log_warning_event", "_log_error_event"):
        monkeypatch.setattr(main_window.MidiTitleWindow, method, lambda *_a, **_k: None)
    instance = main_window.MidiTitleWindow()
    instance.currentLanguage = "en"
    instance._show_centered_progress_dialog = lambda *_args: None
    instance._on_emulator_preview_requested = lambda _preview: (
        instance.emulatorImageWorker.resolve_preview_request({"action": "build"})
    )
    instance._on_emulator_overwrite_requested = lambda _paths: (
        instance.emulatorImageWorker.resolve_overwrite_request(True)
    )
    yield instance
    instance._close_emulator_image_progress()
    instance._clear_staging_history()
    instance._cleanup_midi_scratch_dir()
    instance.deleteLater()


def _observe_worker_and_dialogs(window, monkeypatch, *, cancel_before_build=False):
    signals = {"recovery": [], "cancelled": [], "failed": [], "completed": []}
    dialogs = []

    def run_synchronously(worker):
        worker.buildRecoveryAvailable.connect(signals["recovery"].append)
        worker.operationCancelled.connect(signals["cancelled"].append)
        worker.buildFailed.connect(signals["failed"].append)
        worker.buildFinished.connect(signals["completed"].append)
        if cancel_before_build:
            worker.cancel()
        worker.run()
        worker.finished.emit()

    def inspect_dialog(dialog, **_kwargs):
        assert isinstance(dialog, QMessageBox)
        dialogs.append({
            "icon": dialog.icon(),
            "text": dialog.text(),
            "informative": dialog.informativeText(),
            "details": dialog.detailedText(),
            "format": dialog.textFormat(),
        })
        return QMessageBox.Ok

    monkeypatch.setattr(EmulatorImageBuildWorker, "start", run_synchronously)
    monkeypatch.setattr(window, "_exec_child_dialog", inspect_dialog)
    return signals, dialogs


def _start_build(window, source, output, output_ext):
    window._start_emulator_image_build(
        str(source), str(output), prefix="CANC", starting_number=1,
        safety_margin_bytes=0, album_title="", output_content="midi",
        disk_format=DISK_FORMAT_BY_KEY["ibm.720"], output_ext=output_ext,
        include_subfolders=True, disk_layout="folders", shuffle=False,
        include_song_lists=False,
    )


@pytest.mark.parametrize("output_ext", ["img", "hfe"])
@pytest.mark.parametrize("restore_fails", [False, True], ids=["restored", "restore-failed"])
def test_cancel_after_first_publication_visibly_reports_recovery(
    window, tmp_path, monkeypatch, output_ext, restore_fails,
):
    source = tmp_path / "songs"
    source.mkdir()
    # Special characters in paths must remain literal in the recovery notice.
    output = tmp_path / "images & {recovery}"
    output.mkdir()
    destinations = [output / f"CANC{number:04d}.{output_ext}" for number in (1, 2)]
    originals = [b"previous first image", b"previous second image"]
    for number, (destination, original) in enumerate(zip(destinations, originals), start=1):
        album = source / f"Album {number}"
        album.mkdir()
        (album / "Song.mid").write_bytes(_midi_bytes(f"Song {number}"))
        destination.write_bytes(original)

    recovery_root = tmp_path / "recovery"
    recovery_root.mkdir()
    monkeypatch.setattr(file_batch.tempfile, "tempdir", str(recovery_root))
    if restore_fails:
        def no_hardlinks(*_args, **_kwargs):
            raise OSError(errno.EOPNOTSUPP, "Hard links are unavailable")

        # Limit the unsupported-filesystem simulation to the ownership helper.
        monkeypatch.setattr(atomic_file, "os", SimpleNamespace(**(
            vars(os) | {"name": "posix", "link": no_hardlinks}
        )))

    real_verify = builder.verify_image_payloads
    published = []

    def cancel_after_first_verification(delivered, prepared, disk_format, **kwargs):
        real_verify(delivered, prepared, disk_format, **kwargs)
        published.append((Path(delivered), Path(delivered).read_bytes()))
        window.emulatorImageWorker.cancel()
        window.emulatorImageWorker._raise_if_cancelled()

    monkeypatch.setattr(builder, "verify_image_payloads", cancel_after_first_verification)
    signals, dialogs = _observe_worker_and_dialogs(window, monkeypatch)
    _start_build(window, source, output, output_ext)

    assert len(published) == 1
    assert published[0][0] == destinations[0]
    assert destinations[1].read_bytes() == originals[1]
    assert signals["cancelled"] == []
    assert signals["failed"] == []
    assert signals["completed"] == []
    recovery, = signals["recovery"]
    assert recovery["cancelled"] is True
    assert bool(recovery["rollback_errors"]) is restore_fails
    recovery_directory = Path(recovery["recovery_directory"])
    assert recovery_directory.parent == recovery_root
    assert recovery_directory.is_dir()
    assert recovery["retained_paths"]
    assert any(Path(path).read_bytes() == published[0][1]
               for path in recovery["retained_paths"])
    manifest = json.loads((recovery_directory / "manifest.json").read_text(encoding="utf-8"))
    record = next(item for item in manifest["files"]
                  if item["destination"] == str(destinations[0]))
    assert record["published"] is True
    assert record["restored"] is not restore_fails
    assert (recovery_directory / record["original_file"]).read_bytes() == originals[0]

    message, = dialogs
    assert message["icon"] == (QMessageBox.Warning if restore_fails else QMessageBox.Information)
    assert message["format"] == Qt.PlainText
    visible_text = message["text"] + "\n" + message["informative"]
    assert str(recovery_directory) in visible_text
    assert all(path in visible_text for path in recovery["retained_paths"])
    assert recovery["message"] in message["details"]
    assert "restor" in visible_text.lower()
    status = window.status_label.text()
    assert status != window._t("emulator.cancelled.status")
    if restore_fails:
        assert not destinations[0].exists()
        assert str(destinations[0]) in visible_text
        assert "could not be restored" in status
        assert "incomplete image set" in visible_text
        assert "No restoration is needed" not in visible_text
    else:
        assert destinations[0].read_bytes() == originals[0]
        assert "original output state was restored" in status
        assert "No restoration is needed" in visible_text
        assert "inspect the restored output and retained files" in visible_text
        assert "Keep any changes you need before deleting" in visible_text
    assert window.emulatorImageProgressDialog is None
    assert window.emulatorImageWorker is None


def test_cancel_before_publication_remains_an_ordinary_cancellation(window, tmp_path, monkeypatch):
    source = tmp_path / "songs"
    source.mkdir()
    (source / "Song.mid").write_bytes(_midi_bytes("Not published"))
    output = tmp_path / "images"
    output.mkdir()
    destination = output / "CANC0001.img"
    destination.write_bytes(b"unchanged original")
    signals, dialogs = _observe_worker_and_dialogs(window, monkeypatch, cancel_before_build=True)

    _start_build(window, source, output, "img")

    assert signals["recovery"] == []
    assert signals["failed"] == []
    assert signals["completed"] == []
    assert len(signals["cancelled"]) == 1
    assert dialogs == []
    assert window.status_label.text() == window._t("emulator.cancelled.status")
    assert destination.read_bytes() == b"unchanged original"
    assert window.emulatorImageProgressDialog is None
    assert window.emulatorImageWorker is None
