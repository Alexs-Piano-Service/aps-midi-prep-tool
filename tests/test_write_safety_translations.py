"""Recovery UI translates decisions and preserves literal paths and raw logs."""

from collections import Counter
import os
from string import Formatter
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QProgressDialog

from aps_midi_prep_tool_app.main_window import MidiTitleWindow
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, tr, translate_text
from aps_midi_prep_tool_app.write_safety_messages import localize_write_message
from aps_midi_prep_tool_app.write_safety_translations import WRITE_SAFETY_TRANSLATIONS


LANGUAGES = tuple(language.code for language in SUPPORTED_LANGUAGES)
PATH = "C:/Save {folder} <original & title>/日本語.mid"
RECOVERY_GUIDANCE = "Keep this session open and use Save As Image to retain the prepared songs."
UNSUPPORTED_DIRECTORY_GUIDANCE = (
    "Windows rejected the directory listing for this floppy. "
    "Save To Floppy needs Windows file access, even when reading a disk image succeeds."
)


def _fields(template):
    return Counter((field, spec, conversion)
                   for _, field, spec, conversion in Formatter().parse(template)
                   if field is not None)


def test_write_safety_catalog_is_registered_complete_and_preserves_formatting():
    for source, copies in WRITE_SAFETY_TRANSLATIONS.items():
        assert set(copies) == set(LANGUAGES) - {"en"}, source
        for language, copy in copies.items():
            assert copy.strip() and copy != source, (source, language)
            assert _fields(copy) == _fields(source), (source, language)
            assert translate_text(source, language) == copy, (source, language)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("changed,state", (
    (True, "The floppy may be partially written. Keep the recovery copy before attempting another write."),
    (False, "APS has not changed the floppy."),
    (None, "The floppy write has not been verified."),
))
@pytest.mark.parametrize("status", ("failed", "cancelled"))
def test_floppy_guidance_localizes_each_sentence_and_preserves_recovery_path(language, changed, state, status):
    window = SimpleNamespace(
        _lt=lambda source, **fields: translate_text(source, language, **fields),
        image_session=SimpleNamespace(last_floppy_save_diagnostics={
            "status": status, "target_mutation_attempted": changed, "recovery_directory": PATH,
        }),
    )
    actual = MidiTitleWindow._floppy_operation_error_guidance(
        window, "Windows could not report filesystem space", operation="save_files", file_level=True,
    )
    assert actual == (
        translate_text(state, language) + " " + translate_text(RECOVERY_GUIDANCE, language)
        + "\n\n" + translate_text("Recovery copy: {folder}", language, folder=PATH)
    )
    assert PATH in actual
    if language != "en":
        assert state not in actual and RECOVERY_GUIDANCE not in actual


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("stage,changed", [("preflight_listing", False), ("post_write_listing", True)])
def test_unsupported_directory_guidance_explains_file_access_without_losing_recovery(language, stage, changed):
    window = SimpleNamespace(
        _lt=lambda source, **fields: translate_text(source, language, **fields),
        image_session=SimpleNamespace(last_floppy_save_diagnostics={
            "method": "windows_filesystem", "status": "failed", "stage": stage,
            "target_mutation_attempted": changed, "recovery_directory": PATH,
            stage: {"stage": "list_directory", "directory_status": "failed", "error": {"winerror": 50}},
        }),
    )
    # Classification must work with localized Windows detail text.
    actual = MidiTitleWindow._floppy_operation_error_guidance(
        window, "Die Anforderung wird nicht unterstützt.", operation="save_files", file_level=True,
    )
    for source in (UNSUPPORTED_DIRECTORY_GUIDANCE, RECOVERY_GUIDANCE):
        assert translate_text(source, language) in actual
        if language != "en":
            assert source not in actual
    assert PATH in actual
    state = ("The floppy may be partially written. Keep the recovery copy before attempting another write."
             if changed else "APS has not changed the floppy.")
    assert translate_text(state, language) in actual


@pytest.mark.parametrize("override", [
    {"preflight_listing": {"stage": "query_free_space", "directory_status": "complete", "error": {"winerror": 50}}},
    {"preflight_listing": {"stage": "list_directory", "directory_status": "failed", "error": {"winerror": 5}}},
    {"preflight_listing": {"stage": "stat_file", "directory_status": "failed", "error": {"winerror": 50}}},
    {"stage": "stage_file"},
    {"method": "raw"},
    {"status": "cancelled"},
])
def test_other_failures_do_not_claim_windows_directory_access_is_unsupported(override):
    diagnostics = {
        "method": "windows_filesystem", "status": "failed", "stage": "preflight_listing",
        "target_mutation_attempted": False,
        "preflight_listing": {"stage": "list_directory", "directory_status": "failed", "error": {"winerror": 50}},
        **override,
    }
    window = SimpleNamespace(
        _lt=lambda source, **fields: source.format(**fields),
        image_session=SimpleNamespace(last_floppy_save_diagnostics=diagnostics),
    )
    actual = MidiTitleWindow._floppy_operation_error_guidance(
        window, "[WinError 50]", operation="save_files", file_level=True,
    )
    assert UNSUPPORTED_DIRECTORY_GUIDANCE not in actual


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("source,fields", (
    ("Could not verify file attributes on the floppy: {path}", {"path": PATH}),
    ("Floppy verification failed: file attributes differ for {path}.", {"path": PATH}),
    ("Safe floppy saving needs {needed} free root-directory entries; only {available} are available. No files were changed. Save As Image, then write a backed-up or spare disk instead.",
     {"needed": "3", "available": "2"}),
    ("Staged floppy file verification failed: {path}", {"path": PATH}),
    ("Floppy verification failed: contents differ for {path}.", {"path": PATH}),
    ("Safe floppy saving needs {needed} bytes of staging space; only {available} are available. No files were changed. Save As Image, then write a backed-up or spare disk instead.",
     {"needed": "4096", "available": "1024"}),
    ("Windows could not report filesystem space for {root} ({api}): {error}",
     {"root": "A:/", "api": "GetDiskFreeSpaceW", "error": "[WinError 50] Unknown operating-system detail {value}"}),
    ("Could not inspect song {name}: {error}", {"name": PATH, "error": "Invalid MIDI header."}),
    ("Emulator image export failed: {error}\n\nSome output files could not be restored: {files}\nRecovery copies and a manifest have been retained in:\n{folder}",
     {"error": "Invalid MIDI header.", "files": PATH, "folder": "C:/Recovery {folder} & backup"}),
))
def test_write_diagnostics_localize_around_literal_user_data(language, source, fields):
    raw = source.format(**fields)
    localized_fields = dict(fields)
    if "error" in localized_fields:
        localized_fields["error"] = translate_text(localized_fields["error"], language)
    actual = localize_write_message(raw, language)
    assert actual == translate_text(source, language, **localized_fields)
    for field, value in fields.items():
        if field != "error":
            assert value in actual
    if language != "en":
        assert actual != raw


@pytest.mark.parametrize("language", LANGUAGES)
def test_unknown_details_remain_verbatim(language):
    raw = "Unknown I/O result: " + PATH
    assert localize_write_message(raw, language) == raw
    # Do not substitute arbitrary prose into a byte count diagnostic.
    malformed = "Safe floppy saving needs many bytes of staging space; only none are available. No files were changed. Save As Image, then write a backed-up or spare disk instead."
    assert localize_write_message(malformed, language) == malformed


@pytest.mark.parametrize("language", LANGUAGES)
def test_remaining_staging_names_are_shown_in_localized_failure_guidance(language):
    names = ["APS12345.TMP", "APSABCDE.TMP"]
    window = SimpleNamespace(
        _lt=lambda source, **fields: translate_text(source, language, **fields),
        image_session=SimpleNamespace(last_floppy_save_diagnostics={
            "status": "failed", "target_mutation_attempted": True,
            "recovery_directory": PATH, "staging_files_remaining": names,
        }),
    )
    actual = MidiTitleWindow._floppy_operation_error_guidance(window, "Disk changed", operation="save_files")
    assert translate_text("Temporary files may remain on the floppy: {files}", language, files=", ".join(names)) in actual
    assert all(name in actual for name in names)


@pytest.mark.parametrize("language", LANGUAGES)
def test_pending_attribute_restoration_is_shown_in_failure_guidance(language):
    window = SimpleNamespace(
        _lt=lambda source, **fields: translate_text(source, language, **fields),
        image_session=SimpleNamespace(last_floppy_save_diagnostics={
            "status": "failed", "target_mutation_attempted": True,
            "attributes_pending": ["SONG.FIL"], "recovery_directory": PATH,
        }),
    )
    actual = MidiTitleWindow._floppy_operation_error_guidance(window, "Disk changed", operation="save_files")
    assert translate_text("Original file attributes could not be restored: {files}", language, files="SONG.FIL") in actual


@pytest.mark.parametrize("language", LANGUAGES)
def test_write_error_display_translates_but_logs_remain_original(language):
    shown, logs = [], []
    window = SimpleNamespace(
        _language_code=lambda: language,
        _lt=lambda source, **fields: translate_text(source, language, **fields),
        _t=lambda source, **fields: tr(source, language, **fields),
        _clean_error_detail=str,
        _ensure_sentence=lambda text: text,
        _log_error_event=lambda *args, **fields: logs.append(fields),
        _show_reportable_error_message=lambda _icon, _title, message: shown.append(message),
    )
    source = "Floppy verification failed: contents differ for {path}."
    raw = source.format(path=PATH)
    MidiTitleWindow._show_operation_error(window, "Save Failed", "Save Failed", raw, guidance="")
    assert translate_text(source, language, path=PATH) in shown[0]
    assert logs[0]["detail"] == raw


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("source,fields", (
    ("Copying {path} to floppy...", {"path": PATH}),
    ("Requesting administrator approval for direct floppy write...", {}),
    ("Verifying delivered contents: {name}...", {"name": PATH}),
))
def test_write_progress_translates_runtime_paths(language, source, fields):
    application = QApplication.instance() or QApplication([])
    window = SimpleNamespace(
        _lt=lambda text: translate_text(text, language),
        _language_code=lambda: language,
    )
    progress = QProgressDialog()
    try:
        MidiTitleWindow._set_progress_dialog_message(window, progress, source.format(**fields))
        assert progress.labelText() == translate_text(source, language, **fields)
    finally:
        progress.close()
        application.processEvents()
