"""Utility options, background work, cancellation, and pending-edit isolation."""

from pathlib import Path
import threading

import pytest

from aps_midi_prep_tool_app import boot_sector_dialog
from aps_midi_prep_tool_app.boot_sector_dialog import BootSectorRepairDialog
from aps_midi_prep_tool_app.boot_sector_repair import ImageRepairBatch
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from test_boot_sector_repair import protect, song_image, visible_song_image
from test_inspection_staging import _edit_title, _load_folder, window  # noqa: F401

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QLabel


def wait_for_worker(dialog):
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    if dialog.worker is not None:
        dialog.worker.finished.connect(loop.quit)
        timer.start(10000)
        loop.exec()
        timer.stop()
    QApplication.processEvents()
    if dialog.worker is not None:
        dialog.worker.requestInterruption()
        dialog.worker.wait(10000)
        QApplication.processEvents()
        pytest.fail("Boot repair worker did not finish in time")


@pytest.mark.parametrize("kind", ["blank", "omitted"])
def test_utility_repairs_selected_file_without_applying_staged_song_edits(window, tmp_path, monkeypatch, kind):
    song, _ = _load_folder(window, tmp_path)
    _edit_title(window, song, monkeypatch)
    staged = window._staged_signature()
    original_song = song.read_bytes()
    source, data, geometry = song_image(tmp_path)
    original = protect(data, geometry, kind)
    source.write_bytes(original)

    def execute(dialog, *, resize_to_contents):
        assert not resize_to_contents
        dialog.path_edit.setText(str(source))
        dialog.run_button.click()
        assert not dialog.run_button.isEnabled()
        wait_for_worker(dialog)
        assert dialog.run_button.isEnabled()
        assert dialog.results[0].repaired
        assert "1 changed" in dialog.status_label.text()
        dialog.reject()

    monkeypatch.setattr(window, "_exec_child_dialog", execute)
    window.utilitiesRepairBootSectorAction.trigger()
    assert source.read_bytes()[512:] == visible_song_image(data, geometry)[512:]
    assert not list(tmp_path.glob("*.bak*"))
    assert song.read_bytes() == original_song
    assert window._staged_signature() == staged


def test_defaults_and_file_folder_browsing(window, tmp_path, monkeypatch):
    source, _, _ = song_image(tmp_path)
    dialog = BootSectorRepairDialog(window, str(tmp_path))
    assert not dialog.scope_combo.currentData()
    assert not dialog.path_edit.text()
    assert not dialog.target_combo.currentData()
    assert not dialog.backup_check.isChecked()
    assert not dialog.recursive_check.isChecked()
    assert not dialog.recursive_check.isEnabled()
    assert "Nalbantov" in dialog.target_combo.itemText(dialog.target_combo.findData("hfe"))
    monkeypatch.setattr(boot_sector_dialog.QFileDialog, "getOpenFileName", lambda *_args: (str(source), ""))
    dialog.browse_button.click()
    assert dialog.path_edit.text() == str(source)
    dialog.scope_combo.setCurrentIndex(1)
    assert dialog.recursive_check.isEnabled()
    assert dialog.path_edit.text() == str(tmp_path)
    subdir = tmp_path / "folder"
    subdir.mkdir()
    monkeypatch.setattr(boot_sector_dialog.QFileDialog, "getExistingDirectory", lambda *_args: str(subdir))
    dialog.browse_button.click()
    assert dialog.path_edit.text() == str(subdir)
    dialog.reject()


def test_selected_options_drive_real_background_batch_and_report_failures(window, tmp_path):
    source, data, geometry = song_image(tmp_path)
    original = protect(data, geometry, "blank")
    source.write_bytes(original)
    nested = tmp_path / "nested"
    nested.mkdir()
    other = nested / "other.img"
    other.write_bytes(original)
    (tmp_path / "bad.img").write_bytes(b"Bad image")
    dialog = BootSectorRepairDialog(window, str(source))
    dialog.scope_combo.setCurrentIndex(1)
    dialog.recursive_check.setChecked(True)
    dialog.backup_check.setChecked(True)
    dialog.target_combo.setCurrentIndex(dialog.target_combo.findData("ima"))
    dialog.run_button.click()
    wait_for_worker(dialog)
    for path in (source, other):
        assert path.read_bytes()[512:] == visible_song_image(data, geometry)[512:]
        assert path.with_suffix(".ima").read_bytes() == path.read_bytes()
        assert Path(str(path) + ".bak").read_bytes() == original
    assert len(dialog.results) == 3
    assert "2 changed, 0 unchanged, 1 failed" in dialog.status_label.text()
    assert "Repaired and converted" in dialog.report.toPlainText()
    assert "Failed:" in dialog.report.toPlainText()
    assert "Backup:" in dialog.report.toPlainText()
    dialog.reject()


@pytest.mark.parametrize("close_method", ["reject", "close"])
def test_closing_running_dialog_requests_cancellation_and_keeps_worker_alive(window, tmp_path, monkeypatch, close_method):
    entered = threading.Event()
    finish = threading.Event()

    def batch(_source, **options):
        entered.set()
        finish.wait(10)
        return ImageRepairBatch((), cancelled=options["cancel_callback"]())

    monkeypatch.setattr(boot_sector_dialog, "repair_boot_sector_batch", batch)
    dialog = BootSectorRepairDialog(window)
    dialog.path_edit.setText(str(tmp_path))
    dialog.show()
    dialog.run_button.click()
    try:
        assert entered.wait(5)
        getattr(dialog, close_method)()
        assert dialog.isVisible()
        assert dialog.worker is not None
        assert dialog.worker.isInterruptionRequested()
    finally:
        finish.set()
        wait_for_worker(dialog)
    assert "Operation cancelled." in dialog.status_label.text()
    dialog.reject()
    assert not dialog.isVisible()


def test_cancelling_without_run_leaves_source_untouched(window, tmp_path):
    source, data, geometry = song_image(tmp_path)
    original = protect(data, geometry, "blank")
    source.write_bytes(original)
    dialog = BootSectorRepairDialog(window, str(source))
    assert dialog.path_edit.text() == str(source)
    dialog.reject()
    assert source.read_bytes() == original
    assert not list(tmp_path.glob("*.bak*"))


def test_utility_is_available_without_loaded_songs_and_disabled_while_busy(window):
    assert not window._inspection_items()
    assert window.utilitiesRepairBootSectorAction in window.diskMenu.actions()
    assert window.utilitiesRepairBootSectorAction.isEnabled()
    window.choose_button.setEnabled(False)
    window._update_menu_actions()
    assert not window.utilitiesRepairBootSectorAction.isEnabled()


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_utility_translates_on_language_change(window, language):
    window._set_language(language)
    action = window.utilitiesRepairBootSectorAction
    label = translate_text("Repair Yamaha Boot Sector...", language)
    assert label in action.text().replace("&", "")
    assert action.toolTip() == translate_text(
        "Repair boot sectors and make files visible, with optional backups, format conversion, and folder processing.", language,
    )
    dialog = BootSectorRepairDialog(window)
    assert dialog.windowTitle() == label
    description = (
        "Repair images in place and make files visible by clearing hidden and system flags. "
        "File contents are preserved. "
        "Changing format also creates a converted file beside the repaired original."
    )
    assert translate_text(description, language) in [item.text() for item in dialog.findChildren(QLabel)]
    if language != "en":
        assert translate_text(description, language) != description
    for widget, text in ((dialog.backup_check, "Create a backup before repairing"),
                         (dialog.recursive_check, "Include subfolders"), (dialog.run_button, "Repair")):
        assert widget.text() == translate_text(text, language)
    dialog.reject()


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_recovery_explains_visibility_changes_in_every_language(window, monkeypatch, language):
    window._set_language(language)
    description = (
        "Recovered images clear hidden and system file flags so disk browsers can show the files. "
        "File contents are unchanged by this step."
    )

    def execute(dialog):
        note = dialog.findChild(QLabel, "recoveryVisibilityNote")
        assert note is not None
        assert note.text() == translate_text(description, language)
        if language != "en":
            assert note.text() != description
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", execute)
    window.recover_damaged_image_dialog()
