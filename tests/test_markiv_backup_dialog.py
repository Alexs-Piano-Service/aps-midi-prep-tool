"""Mark IV utility workflows and worker lifecycle through the Qt interface."""

import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit

from aps_midi_prep_tool_app import markiv_backup_dialog as dialog_module
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, tr


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


def _wait(application, condition, *, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        application.processEvents()
        QTest.qWait(10)
    application.processEvents()
    assert condition(), "The Mark IV worker did not finish in time"


def _midi():
    track = b"\x00\x90\x3c\x50\x60\x80\x3c\x00\x00\xff\x2f\x00"
    return b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk" + len(track).to_bytes(4, "big") + track


@pytest.fixture
def volume(tmp_path):
    source = tmp_path / "source"
    album = source / "songs" / "user" / "001"
    album.mkdir(parents=True)
    original = convert_midi_bytes_to_eseq_bytes(_midi(), title_override="Legacy performance")
    song = album / "SONG.FIL"
    song.write_bytes(original)
    return source, song, original


@pytest.fixture
def dialog(application, tmp_path):
    settings = QSettings(str(tmp_path / "markiv.ini"), QSettings.IniFormat)
    instance = dialog_module.MarkIVBackupDialog(settings, refresh_on_open=False)
    yield instance
    if instance.is_busy:
        instance.cancel_operation()
        _wait(application, lambda: not instance.is_busy)
    instance.close()
    application.processEvents()


def test_immediate_close_prevents_deferred_drive_discovery(application, tmp_path, monkeypatch):
    discoveries = []
    monkeypatch.setattr(dialog_module, "discover_mounted_sources",
                        lambda **_kwargs: discoveries.append(True) or [])
    settings = QSettings(str(tmp_path / "immediate-close.ini"), QSettings.IniFormat)
    instance = dialog_module.MarkIVBackupDialog(settings, refresh_on_open=True)
    try:
        instance.close()
        application.processEvents()
        QTest.qWait(10)
        assert not discoveries
        assert not instance.is_busy
    finally:
        if instance.is_busy:
            instance.cancel_operation()
            _wait(application, lambda: not instance.is_busy)
        instance.close()


@pytest.mark.parametrize("convert,keep_originals", ((False, False), (True, True), (True, False)))
def test_scan_backup_and_verify_copied_volume(application, dialog, volume, tmp_path, monkeypatch,
                                            convert, keep_originals):
    source, song, original = volume
    assert not dialog.convert_checkbox.isChecked()
    assert not dialog.backup_button.isEnabled()
    dialog.source_combo.setEditText(str(source))
    dialog.destination_edit.setText(str(tmp_path / "backups"))
    dialog.convert_checkbox.setChecked(convert)
    dialog.keep_originals_checkbox.setChecked(keep_originals)

    dialog.start_scan()
    _wait(application, lambda: not dialog.is_busy)
    assert dialog.plan is not None, dialog.details.toPlainText()
    assert dialog.album_table.topLevelItemCount() == 1
    assert len(dialog.plan.files) == 1
    assert dialog.backup_button.isEnabled()
    assert "SONG" in dialog.details.toPlainText()
    assert "E-SEQ" in dialog.details.toPlainText()
    album_text = dialog.details.toPlainText()

    dialog.start_backup()
    _wait(application, lambda: not dialog.is_busy)
    assert dialog.result_folder is not None, dialog.details.toPlainText()
    result = Path(dialog.result_folder)
    assert result.is_dir()
    assert not (result / ".incomplete").exists(), dialog.details.toPlainText()
    copied = list(result.rglob("*.FIL"))
    assert len(copied) == int(not convert or keep_originals)
    if copied:
        assert copied[0].read_bytes() == original
    assert song.read_bytes() == original
    midis = [path for path in result.rglob("*") if path.suffix.lower() == ".mid"]
    assert len(midis) == int(convert)
    if convert:
        assert midis[0].read_bytes().startswith(b"MThd")
    assert dialog.open_button.isEnabled()

    monkeypatch.setattr(dialog_module.QFileDialog, "getExistingDirectory", lambda *_args, **_kwargs: str(result))
    dialog.verify_existing_backup()
    _wait(application, lambda: not dialog.is_busy)
    assert dialog.status_label.text() == dialog._t("markiv.verified"), dialog.details.toPlainText()
    assert dialog.details.toPlainText() == album_text
    assert dialog._t("markiv.verified") not in album_text


def test_keep_originals_option_follows_conversion_and_remembers_choice(application, dialog):
    assert dialog.keep_originals_checkbox.isChecked()
    assert not dialog.keep_originals_checkbox.isEnabled()
    dialog.convert_checkbox.setChecked(True)
    assert dialog.keep_originals_checkbox.isEnabled()
    assert dialog.keep_originals_checkbox.isChecked()
    dialog.keep_originals_checkbox.setChecked(False)
    dialog.convert_checkbox.setChecked(False)
    assert not dialog.keep_originals_checkbox.isEnabled()
    dialog.convert_checkbox.setChecked(True)
    assert not dialog.keep_originals_checkbox.isChecked()
    dialog._save_settings()
    reopened = dialog_module.MarkIVBackupDialog(dialog.settings, refresh_on_open=False)
    try:
        assert reopened.convert_checkbox.isChecked()
        assert reopened.keep_originals_checkbox.isEnabled()
        assert not reopened.keep_originals_checkbox.isChecked()
    finally:
        reopened.close()


@pytest.mark.parametrize("saved_suffixes", (None, True, False))
def test_backup_uses_persisted_global_suffix_preference_and_reads_updates(
        application, dialog, volume, tmp_path, monkeypatch, saved_suffixes):
    if saved_suffixes is not None:
        dialog.settings.setValue("random_output_suffixes", saved_suffixes)
    dialog.settings.sync()
    settings = QSettings(dialog.settings.fileName(), QSettings.IniFormat)
    reopened = dialog_module.MarkIVBackupDialog(settings, refresh_on_open=False)
    forwarded = []
    backup = dialog_module.run_backup

    def record_backup(plan, target, **kwargs):
        forwarded.append(kwargs["random_suffixes"])
        return backup(plan, target, **kwargs)

    monkeypatch.setattr(dialog_module, "run_backup", record_backup)
    expected = saved_suffixes is not False
    try:
        reopened.plan = dialog_module.scan_library(volume[0])
        reopened.destination_edit.setText(str(tmp_path / "backups"))
        reopened.start_backup()
        _wait(application, lambda: not reopened.is_busy)
        assert forwarded == [expected]
        assert reopened.result_folder is not None

        settings.setValue("random_output_suffixes", not expected)
        reopened.start_backup()
        _wait(application, lambda: not reopened.is_busy)
        assert forwarded == [expected, not expected]
    finally:
        if reopened.is_busy:
            reopened.cancel_operation()
            _wait(application, lambda: not reopened.is_busy)
        reopened.close()


@pytest.mark.parametrize("profile,expected", (
    ("enspire", True), ("mark_iv", False), ("mark_iii", False),
    ("e3_850", False), ("midi_export", False), ("custom", False),
    ("pianodisc_prodigy", False), ("unknown-profile", False),
))
def test_audio_split_default_follows_target_on_open(application, tmp_path, profile, expected):
    settings = QSettings(str(tmp_path / "audio-target.ini"), QSettings.IniFormat)
    settings.setValue("preparation_profile", profile)
    # A prior global preference must never override a new destination default.
    settings.setValue("markiv_backup_split_audio", not expected)
    instance = dialog_module.MarkIVBackupDialog(settings, refresh_on_open=False)
    try:
        assert instance.split_audio_checkbox.isChecked() is expected
        assert instance.split_audio_checkbox.isEnabled()
        key = "markiv.audio_target_enabled" if expected else "markiv.audio_target_disabled"
        target = dialog_module.get_preparation_profile(profile)
        assert instance.audio_target_note.text() == instance._t(key, target=instance.lt(target.label))
        assert instance.split_audio_note.text() == instance._t("markiv.split_audio_note")
        assert instance.split_audio_checkbox.toolTip() == instance._t("markiv.split_audio_tooltip")
    finally:
        instance.close()


def test_audio_split_manual_override_is_separate_from_eseq_and_resets_for_new_target(application, dialog):
    assert not dialog.split_audio_checkbox.isChecked()
    dialog.split_audio_checkbox.setChecked(True)
    dialog.convert_checkbox.setChecked(True)
    dialog.keep_originals_checkbox.setChecked(False)
    dialog.convert_checkbox.setChecked(False)
    assert dialog.split_audio_checkbox.isEnabled()
    assert dialog.split_audio_checkbox.isChecked()
    dialog._save_settings()
    dialog.settings.setValue("preparation_profile", "mark_iv")
    reopened = dialog_module.MarkIVBackupDialog(dialog.settings, refresh_on_open=False)
    try:
        assert not reopened.split_audio_checkbox.isChecked()
    finally:
        reopened.close()
    dialog.settings.setValue("preparation_profile", "enspire")
    reopened = dialog_module.MarkIVBackupDialog(dialog.settings, refresh_on_open=False)
    try:
        assert reopened.split_audio_checkbox.isChecked()
        assert not reopened.convert_checkbox.isChecked()
        assert not reopened.keep_originals_checkbox.isChecked()
    finally:
        reopened.close()


@pytest.mark.parametrize("split_audio", (False, True))
@pytest.mark.parametrize("random_suffixes", (False, True))
def test_audio_split_option_reaches_backup_worker(application, dialog, volume, tmp_path,
                                                monkeypatch, split_audio, random_suffixes):
    source, _song, _original = volume
    dialog.source_combo.setEditText(str(source))
    dialog.destination_edit.setText(str(tmp_path / "backups"))
    dialog.start_scan()
    _wait(application, lambda: not dialog.is_busy)
    calls = []

    def backup(plan, target, **kwargs):
        calls.append((plan, target, kwargs))
        return SimpleNamespace(folder=target, status="complete", copied=1, verified=1,
                               converted=0, decoded=int(split_audio), audio_skipped=0, errors=[])

    monkeypatch.setattr(dialog_module, "run_backup", backup)
    dialog.settings.setValue("random_output_suffixes", random_suffixes)
    dialog.split_audio_checkbox.setChecked(split_audio)
    dialog.start_backup()
    _wait(application, lambda: not dialog.is_busy)
    assert len(calls) == 1
    plan, target, options = calls[0]
    assert plan is dialog.plan
    assert target == tmp_path / "backups"
    assert options["split_audio"] is split_audio
    assert options["random_suffixes"] is random_suffixes
    assert options["convert_eseq"] is False
    assert options["keep_originals"] is True
    assert dialog.split_audio_checkbox.isEnabled()
    summary = dialog._t("markiv.audio_summary", decoded=int(split_audio), skipped=0)
    assert (summary in dialog.status_label.text()) is split_audio


def test_audio_decode_progress_counts_attempts_including_skips(application, dialog):
    dialog._progress({"event": "audio_decode", "completed": 3, "audio_total": 4,
                      "decoded": 1, "audio_skipped": 2})
    assert dialog.status_label.text() == dialog._t("markiv.decoding_audio")
    assert dialog.progress_label.text() == dialog._t("markiv.file_progress", completed=3, total=4)
    assert dialog.progress_bar.value() == 750


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
@pytest.mark.parametrize("status", ("complete", "incomplete", "cancelled"))
def test_audio_split_result_summary_is_localized(application, dialog, tmp_path, language, status):
    dialog.language_code = language
    dialog._operation = "backup"
    dialog.plan = SimpleNamespace(warnings=[])
    dialog.split_audio_checkbox.setChecked(True)
    result = SimpleNamespace(folder=tmp_path, status=status, verified=5, converted=1,
                             decoded=2, audio_skipped=3, errors=[])
    dialog._succeeded(result)
    summary = dialog.status_label.text()
    assert summary == (dialog._t("markiv." + status, verified=5, converted=1) + " "
                       + dialog._t("markiv.audio_summary", decoded=2, skipped=3))
    assert "{" not in summary


def test_album_selection_shows_matching_files_even_with_duplicate_album_names(
        application, dialog, volume, monkeypatch):
    from aps_midi_prep_tool_app.markiv_backup.metadata import MetadataCatalog

    source, _song, original = volume
    second = source / "songs/user/002/SECOND.FIL"
    second.parent.mkdir()
    second.write_bytes(original)
    warning = "Catalog contains a missing title"
    catalog = MetadataCatalog(
        albums={f"songs/user/{number}": {"title": "Shared album"} for number in ("001", "002")},
        warnings=[warning],
    )
    monkeypatch.setattr("aps_midi_prep_tool_app.markiv_backup.metadata.load_metadata", lambda _: catalog)
    dialog.source_combo.setEditText(str(source))
    dialog.start_scan()
    _wait(application, lambda: not dialog.is_busy)
    assert dialog.album_table.topLevelItemCount() == 2
    details = dialog.details.toPlainText()
    assert "SONG" in details
    assert "SECOND" not in details
    assert warning not in details
    assert not dialog.report_button.isHidden()
    assert warning in dialog._report_messages

    dialog.album_table.setCurrentItem(dialog.album_table.topLevelItem(1))
    details = dialog.details.toPlainText()
    assert "SECOND" in details
    assert "SONG" not in details
    assert warning not in details
    expanded = dialog._album_text(dialog.album_table.currentItem(), expanded=True)
    assert "songs/user/002/SECOND.FIL" in expanded
    assert "Shared album (2)/SECOND.FIL" in expanded

    dialog.source_combo.setEditText(str(source / "elsewhere"))
    assert not dialog.details.toPlainText()


def test_conversion_notes_use_content_and_follow_options_without_reading_on_selection(
        application, dialog, volume, monkeypatch):
    from aps_midi_prep_tool_app.markiv_backup import library

    source, song, original = volume
    album = song.parent
    (album / "AUDIO.WAV").write_bytes(b"RIFF audio")
    (album / "NORMAL.MID").write_bytes(_midi())
    (album / "MISNAMED.FIL").write_bytes(_midi())
    (album / "PACKAGE.pspg").write_bytes(original)
    (album / "UNKNOWN.BIN").write_bytes(original)
    (album / "cover.bmp").write_bytes(b"album artwork")
    dialog.source_combo.setEditText(str(source))
    dialog.start_scan()
    _wait(application, lambda: not dialog.is_busy)
    assert {file.source for file in dialog.plan.files if file.convertible_eseq} == {
        "songs/user/001/SONG.FIL", "songs/user/001/UNKNOWN.BIN",
    }

    def no_read(*_args):
        pytest.fail("Album selection or option changes must not read source files on the GUI thread")

    monkeypatch.setattr(library, "is_eseq_file", no_read)
    keep = dialog._t("markiv.will_convert_keep")
    replace = dialog._t("markiv.will_convert_replace")
    assert keep not in dialog.details.toPlainText()
    dialog.convert_checkbox.setChecked(True)
    text = dialog.details.toPlainText()
    assert text.count(keep) == 2
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if keep in line:
            assert "SONG" in lines[index - 1] or "UNKNOWN" in lines[index - 1]
    assert "cover.bmp" not in text
    assert dialog._t("markiv.album_assets") in text

    dialog.keep_originals_checkbox.setChecked(False)
    assert dialog.details.toPlainText().count(replace) == 2
    assert keep not in dialog.details.toPlainText()
    dialog.convert_checkbox.setChecked(False)
    assert replace not in dialog.details.toPlainText()


def test_double_click_opens_clicked_album_tracks_and_further_details(application, dialog, volume, monkeypatch):
    from aps_midi_prep_tool_app.markiv_backup.metadata import MetadataCatalog

    source, _song, original = volume
    second = source / "songs/user/002"
    second.mkdir()
    (second / "ALPHA.FIL").write_bytes(original)
    (second / "ZETA.FIL").write_bytes(original)
    (second / "cover.bmp").write_bytes(b"artwork")
    catalog = MetadataCatalog(
        albums={"songs/user/002": {"title": "Other album", "subtitle": "Album subtitle", "album_id": 2}},
        tracks={
            "songs/user/002/ALPHA.FIL": {"title": "First filename", "track_number": 2},
            "songs/user/002/ZETA.FIL": {"title": "First track", "track_number": 1,
                                      "artist": "Performer", "copyright": "Rights holder", "song_id": 5},
        },
    )
    monkeypatch.setattr("aps_midi_prep_tool_app.markiv_backup.metadata.load_metadata", lambda _: catalog)
    dialog.source_combo.setEditText(str(source))
    dialog.convert_checkbox.setChecked(True)
    dialog.start_scan()
    _wait(application, lambda: not dialog.is_busy)
    captured = []

    def inspect(child):
        captured.append(child)
        assert child.objectName() == "markivAlbumDetails"
        assert "Other album" in child.windowTitle()
        editor = child.findChild(QPlainTextEdit)
        assert editor.isReadOnly()
        text = editor.toPlainText()
        assert "Album subtitle" in text
        assert text.index("01. First track") < text.index("02. First filename")
        assert "Performer" in text and "Rights holder" in text
        assert "songs/user/002/ZETA.FIL" in text
        assert "Other album/01 - First track.FIL" in text
        assert "cover.bmp" in text
        assert "songs/user/001/SONG.FIL" not in text
        assert text.count(dialog._t("markiv.will_convert_keep")) == 2
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec", inspect)
    dialog.show()
    application.processEvents()
    item = dialog.album_table.topLevelItem(1)
    point = dialog.album_table.visualItemRect(item).center()
    QTest.mouseClick(dialog.album_table.viewport(), Qt.LeftButton, pos=point)
    QTest.mouseDClick(dialog.album_table.viewport(), Qt.LeftButton, pos=point)
    application.processEvents()
    assert len(captured) == 1


def test_warnings_and_errors_open_report_without_replacing_album_details(application, dialog, volume, monkeypatch):
    source, _song, _original = volume
    dialog.source_combo.setEditText(str(source))
    dialog.start_scan()
    _wait(application, lambda: not dialog.is_busy)
    details = dialog.details.toPlainText()
    dialog._failed("Destination is full")
    assert dialog.details.toPlainText() == details
    assert not dialog.report_button.isHidden()
    captured = []

    def inspect(child):
        assert child.objectName() == "markivBackupReportDetails"
        text = child.findChild(QPlainTextEdit).toPlainText()
        captured.append(text)
        assert "Destination is full" in text
        assert details not in text
        return QDialog.Accepted

    monkeypatch.setattr(QDialog, "exec", inspect)
    dialog.report_button.click()
    assert len(captured) == 1
    dialog._operation = "verify"
    dialog._succeeded(["Backup file changed"])
    assert dialog.details.toPlainText() == details
    assert dialog._report_messages == ["Backup file changed"]
    dialog._succeeded([])
    assert dialog.report_button.isHidden()
    assert dialog.details.toPlainText() == details


def test_editing_source_discards_previous_scan(application, dialog, volume, tmp_path):
    source, _song, _original = volume
    dialog.source_combo.setEditText(str(source))
    dialog.destination_edit.setText(str(tmp_path / "backups"))
    dialog.start_scan()
    _wait(application, lambda: not dialog.is_busy)
    assert dialog.plan is not None
    assert dialog.backup_button.isEnabled()

    dialog.source_combo.setEditText(str(tmp_path / "other-source"))

    assert dialog.plan is None
    assert dialog.album_table.topLevelItemCount() == 0
    assert not dialog.backup_button.isEnabled()


@pytest.mark.parametrize("close_method", ("close", "escape"))
def test_closing_waits_for_worker_cancellation(application, dialog, volume, monkeypatch, close_method):
    source, _song, _original = volume
    entered = threading.Event()
    release = threading.Event()
    cancellation = []
    scan = dialog_module.scan_library

    def blocked_scan(path, progress=None, cancel=None):
        cancellation.append(cancel)
        entered.set()
        release.wait(10)
        return scan(path, progress=progress, cancel=cancel)

    monkeypatch.setattr(dialog_module, "scan_library", blocked_scan)
    dialog.source_combo.setEditText(str(source))
    dialog.show()
    dialog.start_scan()
    try:
        _wait(application, entered.is_set)
        assert dialog.is_busy
        assert not dialog.source_combo.isEnabled()
        assert not dialog.convert_checkbox.isEnabled()
        assert not dialog.keep_originals_checkbox.isEnabled()
        assert not dialog.split_audio_checkbox.isEnabled()
        assert not dialog.backup_button.isEnabled()
        if close_method == "close":
            dialog.close()
        else:
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
        application.processEvents()
        assert cancellation[0].is_set()
        assert dialog.is_busy
        assert dialog.isVisible()
    finally:
        release.set()
        _wait(application, lambda: not dialog.is_busy)
    _wait(application, lambda: not dialog.isVisible())


def test_close_during_backup_waits_for_file_cleanup_and_cancelled_manifest(
        application, dialog, volume, tmp_path, monkeypatch):
    source, song, original = volume
    entered = threading.Event()
    release = threading.Event()
    cancellation = []
    backup = dialog_module.run_backup

    def blocked_backup(plan, target, progress=None, cancel=None, **kwargs):
        cancellation.append(cancel)

        def copying(event):
            if event.get("bytes_done", 0) and not entered.is_set():
                entered.set()
                release.wait(10)
            progress(event)

        return backup(plan, target, progress=copying, cancel=cancel, **kwargs)

    monkeypatch.setattr(dialog_module, "run_backup", blocked_backup)
    dialog.source_combo.setEditText(str(source))
    dialog.destination_edit.setText(str(tmp_path / "backups"))
    dialog.start_scan()
    _wait(application, lambda: not dialog.is_busy)
    dialog.show()
    dialog.start_backup()
    try:
        _wait(application, entered.is_set)
        dialog.close()
        application.processEvents()
        assert cancellation[0].is_set()
        assert dialog.is_busy
        assert dialog.isVisible()
    finally:
        release.set()
        _wait(application, lambda: not dialog.is_busy)
    _wait(application, lambda: not dialog.isVisible())
    assert dialog.result_folder is not None
    manifest = json.loads((dialog.result_folder / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "cancelled"
    assert (dialog.result_folder / ".incomplete").exists()
    assert not list(dialog.result_folder.rglob("*.partial"))
    assert song.read_bytes() == original


@pytest.fixture
def window(application, tmp_path, monkeypatch):
    from aps_midi_prep_tool_app import main_window

    settings = QSettings(str(tmp_path / "main.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(dialog_module.MarkIVBackupDialog, "refresh_drives", lambda _self: None)
    instance = main_window.MidiTitleWindow()
    yield instance
    instance.close()
    application.processEvents()


def test_utility_menu_opens_without_loaded_songs(application, window):
    opened = []

    def inspect(child, **kwargs):
        opened.append(child)
        assert isinstance(child, dialog_module.MarkIVBackupDialog)
        assert kwargs.get("resize_to_contents") is False
        assert not child.convert_checkbox.isChecked()
        assert child.windowTitle() == tr("markiv.title", window.currentLanguage)
        assert child.convert_checkbox.text() == tr("markiv.convert", window.currentLanguage)
        child.close()
        return QDialog.DialogCode.Rejected

    window._exec_child_dialog = inspect
    assert window.table.rowCount() == 0
    assert window.utilitiesMarkIVBackupAction.isEnabled()
    for language in SUPPORTED_LANGUAGES:
        window.currentLanguage = language.code
        window._refresh_translated_ui()
        assert window.utilitiesMarkIVBackupAction.text().replace("&", "") == tr("markiv.action", language.code)
        assert window.utilitiesMarkIVBackupAction.toolTip() == tr("markiv.tooltip", language.code)
    window.utilitiesMarkIVBackupAction.trigger()
    assert len(opened) == 1


def test_closing_main_window_keeps_backup_worker_alive(application, window, volume, monkeypatch):
    source, _song, _original = volume
    entered = threading.Event()
    release = threading.Event()
    cancellation = []
    scan = dialog_module.scan_library

    def blocked_scan(path, progress=None, cancel=None):
        cancellation.append(cancel)
        entered.set()
        release.wait(10)
        return scan(path, progress=progress, cancel=cancel)

    monkeypatch.setattr(dialog_module, "scan_library", blocked_scan)
    child = dialog_module.MarkIVBackupDialog(window.settings, window, refresh_on_open=False)
    window.markivBackupDialog = child
    window.show()
    child.source_combo.setEditText(str(source))
    child.show()
    child.start_scan()
    try:
        _wait(application, entered.is_set)
        window.close()
        application.processEvents()
        assert cancellation[0].is_set()
        assert window.isVisible()
        assert child.isVisible()
        assert child.is_busy
    finally:
        release.set()
        _wait(application, lambda: not child.is_busy)
    _wait(application, lambda: not child.isVisible())
    assert window.isVisible()
