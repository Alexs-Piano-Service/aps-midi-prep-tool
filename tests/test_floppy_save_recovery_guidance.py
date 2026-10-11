"""Expose incomplete disk restoration without losing safe recovery guidance."""

from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app.main_window import MidiTitleWindow
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text


LANGUAGES = tuple(language.code for language in SUPPORTED_LANGUAGES)
RECOVERY_PATH = "/tmp/APS recovery {folder}/save-test"
RESTORE_WARNING = "APS could not finish restoring the floppy after the save stopped."
RESTORE_ERROR = (
    "Could not extract APS013AC.TMP from image: plain_io read/write: Input/output error\n"
    "file read: Invalid argument {device}"
)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("session_attribute", ["image_session", "lastFileSaveSession"])
def test_disabled_backups_explain_originals_are_not_available(language, session_attribute):
    guidance = MidiTitleWindow._floppy_operation_error_guidance(
        _window({
            "status": "failed", "target_mutation_attempted": True,
            "backup_originals": False, "recovery_directory": RECOVERY_PATH,
        }, language=language, session_attribute=session_attribute),
        "Copy failed", operation="save_files", file_level=True,
    )
    assert translate_text(
        "Original-file backups were disabled; original files cannot be restored automatically.", language,
    ) in guidance
    assert translate_text(RESTORE_WARNING, language) not in guidance
    assert RECOVERY_PATH in guidance


def _window(diagnostics, *, language="en", session_attribute="image_session"):
    return SimpleNamespace(
        _lt=lambda source, **fields: translate_text(source, language, **fields),
        **{session_attribute: SimpleNamespace(last_floppy_save_diagnostics=diagnostics)},
    )


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("status", ["failed", "cancelled"])
@pytest.mark.parametrize("session_attribute", ["image_session", "lastFileSaveSession"])
def test_incomplete_restoration_shows_actual_error_and_preserves_recovery_guidance(
    language, status, session_attribute,
):
    diagnostics = {
        "method": "posix_mtools", "stage": "stage_file", "status": status,
        "target_mutation_attempted": True, "files_staged": 7,
        "files_removed": 0, "files_copied": 0,
        "recovery_directory": RECOVERY_PATH,
        "restoration": {
            "status": "incomplete", "restored": [],
            "error": {"type": "FloppyImageError", "message": RESTORE_ERROR, "errno": None},
        },
    }

    actual = MidiTitleWindow._floppy_operation_error_guidance(
        _window(diagnostics, language=language, session_attribute=session_attribute),
        "Operation cancelled.", operation="save_files", file_level=True,
    )

    for source in (
        "The floppy may be partially written. Keep the recovery copy before attempting another write.",
        "Keep this session open and use Save As Image to retain the prepared songs.",
        RESTORE_WARNING,
    ):
        assert translate_text(source, language) in actual
        if language != "en":
            assert source not in actual
    assert translate_text("Recovery error: {error}", language, error=RESTORE_ERROR) in actual
    assert translate_text("Recovery copy: {folder}", language, folder=RECOVERY_PATH) in actual
    assert RESTORE_ERROR in actual
    assert RECOVERY_PATH in actual


@pytest.mark.parametrize("restoration", [None, {}, {"status": "checking"}, {"status": "complete"}])
def test_restoration_warning_requires_reported_incomplete_restoration(restoration):
    actual = MidiTitleWindow._floppy_operation_error_guidance(
        _window({"status": "cancelled", "restoration": restoration}),
        "Operation cancelled.", operation="save_files",
    )

    assert RESTORE_WARNING not in actual
    assert "Recovery error:" not in actual


@pytest.mark.parametrize("error", [None, {}, {"message": "  "}])
def test_incomplete_restoration_without_error_message_still_warns(error):
    actual = MidiTitleWindow._floppy_operation_error_guidance(
        _window({
            "status": "cancelled", "recovery_directory": RECOVERY_PATH,
            "restoration": {"status": "incomplete", "error": error},
        }),
        "Operation cancelled.", operation="save_files",
    )

    assert RESTORE_WARNING in actual
    assert "Recovery error:" not in actual
    assert RECOVERY_PATH in actual


@pytest.mark.parametrize("file_level", [False, True])
def test_raw_image_guidance_only_includes_restoration_when_save_is_file_level(file_level):
    actual = MidiTitleWindow._floppy_operation_error_guidance(
        _window({
            "status": "cancelled",
            "restoration": {"status": "incomplete", "error": {"message": RESTORE_ERROR}},
        }),
        "Operation cancelled.", operation="write_image", file_level=file_level,
    )

    assert (RESTORE_WARNING in actual) is file_level
    assert (RESTORE_ERROR in actual) is file_level
