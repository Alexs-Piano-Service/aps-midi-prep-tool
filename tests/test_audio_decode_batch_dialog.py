"""Admin batch decoding UI selection, progress and worker shutdown contracts."""

import os
from pathlib import Path
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from aps_midi_prep_tool_app import audio_decode_batch_dialog as dialog_module
from aps_midi_prep_tool_app.audio_decode_batch import BatchDecodeRecord, BatchDecodeResult
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def _wait(application, condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        application.processEvents()
        QTest.qWait(10)
    application.processEvents()
    assert condition(), "Batch decoder worker did not finish in time"


@pytest.fixture
def dialog(application, tmp_path):
    settings = QSettings(str(tmp_path / "batch.ini"), QSettings.IniFormat)
    instance = dialog_module.AudioDecodeBatchDialog(settings)
    yield instance
    if instance.is_busy:
        instance.cancel_operation()
        _wait(application, lambda: not instance.is_busy)
    instance.close()


def test_file_and_recursive_folder_pickers_preserve_roots_and_allow_removal(dialog, tmp_path, monkeypatch):
    first, second = tmp_path / "First.Wav", tmp_path / "Second.Mp3"
    tree = tmp_path / "Library"
    picker_calls = []

    def choose_files(*args):
        picker_calls.append(args)
        return [str(first), str(second)], ""

    monkeypatch.setattr(dialog_module.QFileDialog, "getOpenFileNames", choose_files)
    monkeypatch.setattr(dialog_module.QFileDialog, "getExistingDirectory", lambda *_a: str(tree))
    monkeypatch.setattr(Path, "rglob", lambda *_a: pytest.fail("UI must not scan directory trees"))
    assert not dialog.start_button.isEnabled()
    dialog.files_button.click()
    dialog.folder_button.click()
    dialog.add_sources([first, tree])
    assert dialog.sources == (first, second, tree)
    assert ";;" + dialog._t("abd.all_files") in picker_calls[0][3]
    assert not dialog.start_button.isEnabled()
    dialog.destination_edit.setText(str(tmp_path / "output"))
    assert dialog.start_button.isEnabled()
    dialog.input_list.topLevelItem(1).setSelected(True)
    assert dialog.remove_button.isEnabled()
    dialog.remove_button.click()
    assert dialog.sources == (first, tree)


@pytest.mark.parametrize("random_suffixes", (True, False))
def test_start_uses_global_output_setting_and_reports_each_outcome(
        application, dialog, tmp_path, monkeypatch, random_suffixes):
    sources = (tmp_path / "encoded.wav", tmp_path / "ordinary.mp3", tmp_path / "damaged.wav")
    folder = tmp_path / "output" / "Decoded Audio"
    records = (
        BatchDecodeRecord(sources[0], "converted", folder / "Song.mid", folder / "Song.mp3"),
        BatchDecodeRecord(sources[1], "skipped", error="No encoded control channel."),
        BatchDecodeRecord(sources[2], "failed", error="Incomplete control data.\nRecovery details remain here."),
    )
    result = BatchDecodeResult(folder, records)
    calls = []

    def run(selected, output, **kwargs):
        calls.append((selected, output, kwargs["random_suffixes"]))
        return result

    monkeypatch.setattr(dialog_module, "run_audio_decode_batch", run)
    dialog.settings.setValue("random_output_suffixes", random_suffixes)
    dialog.add_sources(sources)
    dialog.destination_edit.setText(str(tmp_path / "output"))
    dialog.start_button.click()
    _wait(application, lambda: not dialog.is_busy)
    assert calls == [(sources, tmp_path / "output", random_suffixes)]
    assert dialog.batch_result == result
    assert dialog.result_folder == folder
    assert dialog.open_button.isEnabled()
    assert dialog.results_table.topLevelItemCount() == 3
    assert dialog.status_label.text() == dialog._t("abd.summary", converted=1, skipped=1, failed=1)
    assert str(folder / "Song.mid") in dialog.details.toPlainText()
    assert str(folder / "Song.mp3") in dialog.details.toPlainText()
    dialog.results_table.setCurrentItem(dialog.results_table.topLevelItem(2))
    assert records[2].error in dialog.details.toPlainText()
    assert dialog.sources == sources
    assert dialog.start_button.isEnabled()


def test_progress_labels_distinguish_decode_from_export_and_scan_runs_off_ui_thread(
        application, dialog, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    thread_ids = []
    source = tmp_path / "tree"

    def run(_sources, _output, **kwargs):
        thread_ids.append(threading.get_ident())
        kwargs["progress"]({"phase": "decoding", "source": str(source / "song.wav"),
                            "completed": 2, "total": 5, "fraction": 0.45})
        entered.set()
        release.wait(5)
        return BatchDecodeResult(None, ())

    monkeypatch.setattr(dialog_module, "run_audio_decode_batch", run)
    dialog.add_sources([source])
    dialog.destination_edit.setText(str(tmp_path / "output"))
    try:
        dialog.start_decode()
        _wait(application, lambda: entered.is_set() and dialog.progress_bar.value() == 450)
        assert len(thread_ids) == 1 and thread_ids[0] != threading.get_ident()
        assert dialog._t("abd.decoding") in dialog.status_label.text()
        assert dialog._t("abd.file_progress", completed=2, total=5) in dialog.status_label.text()
        assert dialog.current_source.text() == str(source / "song.wav")
        assert not dialog.destination_edit.isEnabled()
        assert not dialog.files_button.isEnabled()
        dialog._progress({"phase": "exporting", "source": str(source / "song.wav"),
                          "completed": 2, "total": 5, "fraction": 0.55})
        assert dialog._t("abd.exporting") in dialog.status_label.text()
        assert dialog.progress_bar.value() == 550
        dialog.cancel_operation()
        dialog._progress({"phase": "decoding", "total": 5, "fraction": 0.9})
        assert dialog.status_label.text() == dialog._t("abd.cancelling")
        assert dialog.progress_bar.value() == 550
    finally:
        release.set()
        _wait(application, lambda: not dialog.is_busy)


def test_preflight_error_is_visible_without_success_or_output_folder(application, dialog, tmp_path, monkeypatch):
    message = "LAME is required before decoding can start.\nInstall LAME and retry."

    def run(*_args, **_kwargs):
        raise ValueError(message)

    monkeypatch.setattr(dialog_module, "run_audio_decode_batch", run)
    dialog.add_sources([tmp_path / "source.wav"])
    dialog.destination_edit.setText(str(tmp_path / "output"))
    dialog.start_decode()
    _wait(application, lambda: not dialog.is_busy)
    assert dialog.status_label.text() == dialog._t("abd.fatal")
    assert dialog.details.toPlainText() == message
    assert dialog.result_folder is None
    assert not dialog.open_button.isEnabled()
    assert dialog.start_button.isEnabled()
    assert dialog.tabs.currentIndex() == 1


def test_cancel_retains_completed_pairs_and_does_not_close_dialog(application, dialog, tmp_path, monkeypatch):
    entered = threading.Event()
    folder = tmp_path / "output"
    record = BatchDecodeRecord(tmp_path / "song.wav", "converted", folder / "song.mid", folder / "song.mp3")

    def run(_sources, _output, **kwargs):
        entered.set()
        assert kwargs["cancel"].wait(5)
        return BatchDecodeResult(folder, (record,), cancelled=True)

    monkeypatch.setattr(dialog_module, "run_audio_decode_batch", run)
    dialog.show()
    dialog.add_sources([record.source])
    dialog.destination_edit.setText(str(folder))
    dialog.start_decode()
    _wait(application, entered.is_set)
    dialog.cancel_button.click()
    _wait(application, lambda: not dialog.is_busy)
    assert dialog.isVisible()
    assert dialog.batch_result.cancelled
    assert dialog.result_folder == folder
    assert dialog.open_button.isEnabled()
    assert dialog.status_label.text() == dialog._t("abd.cancelled_summary", converted=1, skipped=0, failed=0)
    assert dialog.results_table.topLevelItemCount() == 1


@pytest.mark.parametrize("close_method", ("close", "reject", "accept", "escape"))
def test_close_waits_for_worker_exit_after_result_signal(
        application, dialog, tmp_path, monkeypatch, close_method):
    delivered, release = threading.Event(), threading.Event()

    def run(worker):
        worker.succeeded.emit(BatchDecodeResult(None, ()))
        delivered.set()
        release.wait(5)

    monkeypatch.setattr(dialog_module.AudioDecodeBatchWorker, "run", run)
    dialog.show()
    dialog.add_sources([tmp_path / "song.wav"])
    dialog.destination_edit.setText(str(tmp_path / "output"))
    finished = []
    dialog.finished.connect(finished.append)
    try:
        dialog.start_decode()
        _wait(application, lambda: delivered.is_set() and dialog.batch_result is not None)
        worker = dialog.worker
        assert dialog.is_busy and worker.isRunning()
        if close_method == "escape":
            QTest.keyClick(dialog, Qt.Key_Escape)
        else:
            getattr(dialog, close_method)()
        assert dialog.isVisible()
        assert dialog.is_busy
        assert worker._cancel.is_set()
        assert not finished
        assert dialog.status_label.text() == dialog._t("abd.close_wait")
    finally:
        release.set()
        _wait(application, lambda: not dialog.is_busy)
    assert not dialog.isVisible()
    assert finished == [QDialog.Accepted if close_method == "accept" else QDialog.Rejected]


@pytest.mark.parametrize("language", [item.code for item in SUPPORTED_LANGUAGES])
def test_dialog_translates_actions_and_summary(application, tmp_path, language):
    settings = QSettings(str(tmp_path / f"batch-{language}.ini"), QSettings.IniFormat)
    settings.setValue("language", language)
    instance = dialog_module.AudioDecodeBatchDialog(settings)
    try:
        assert instance.windowTitle() == instance._t("abd.title")
        assert not instance.windowTitle().startswith("abd.")
        assert instance.start_button.text() == instance._t("abd.start")
        assert instance.folder_button.text() == instance._t("abd.add_folder")
        instance._succeeded(BatchDecodeResult(None, (), cancelled=True))
        assert instance.status_label.text() == instance._t("abd.cancelled_summary", converted=0, skipped=0, failed=0)
        assert "{" not in instance.status_label.text()
    finally:
        instance.close()
