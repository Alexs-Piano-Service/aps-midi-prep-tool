"""Floppy progress localizes app text while keeping file details literal."""

import pytest

from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from aps_midi_prep_tool_app.write_safety_messages import localize_write_message
from aps_midi_prep_tool_app.write_safety_translations import WRITE_SAFETY_TRANSLATIONS


LANGUAGES = tuple(language.code for language in SUPPORTED_LANGUAGES)
FILE_PROGRESS = "{action}\nFile {index} of {total}: {name}"
PROGRESS_STEPS = (
    "Checking floppy...",
    "Backing up files...",
    "Preparing files...",
    "Checking original files...",
    "Copying files to floppy...",
    "Verifying copied files...",
    "Removing old files...",
    "Finishing file updates...",
    "Verifying saved files...",
    "Restoring original files...",
    "Finishing floppy save...",
)
STOPPING_MESSAGE = (
    "APS is stopping the current disk operation. Please wait for it to finish, "
    "then close APS again. The operating system may need a little time to release the drive.\n\n"
    "Temporary working files will be cleaned up after disk work has stopped."
)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("source", PROGRESS_STEPS)
def test_floppy_progress_steps_are_localized(language, source):
    actual = localize_write_message(source, language)
    assert actual == translate_text(source, language)
    if language != "en":
        assert actual == WRITE_SAFETY_TRANSLATIONS[source][language]
        assert actual != source


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("name", (
    "SONG01.MID",
    "{action} <song & title> 日本語.mid",
    "Checking floppy...",
    "Copying files to floppy...\nFile 99 of 100: {name}",
    "first line\n\nlast line {index}",
))
def test_floppy_file_progress_localizes_action_and_preserves_filename(language, name):
    action = "Copying files to floppy..."
    raw = FILE_PROGRESS.format(action=action, index=3, total=12, name=name)
    actual = localize_write_message(raw, language)
    assert actual == translate_text(
        FILE_PROGRESS, language,
        action=translate_text(action, language), index=3, total=12, name=name,
    )
    assert actual.endswith(name)
    assert "3" in actual and "12" in actual
    if language != "en":
        assert actual.partition("\n")[0] != action


@pytest.mark.parametrize("language", LANGUAGES)
def test_unknown_file_progress_action_is_preserved(language):
    raw = FILE_PROGRESS.format(action="Unknown operation", index=1, total=2, name="SONG.MID")
    assert localize_write_message(raw, language) == translate_text(
        FILE_PROGRESS, language, action="Unknown operation", index=1, total=2, name="SONG.MID",
    )


@pytest.mark.parametrize("language", LANGUAGES)
def test_file_progress_requires_numeric_counts(language):
    raw = FILE_PROGRESS.format(
        action="Copying files to floppy...", index="first", total="many", name="SONG.MID",
    )
    assert localize_write_message(raw, language) == raw


@pytest.mark.parametrize("language", LANGUAGES)
def test_step_percentage_keeps_qt_placeholder(language):
    actual = localize_write_message("This step: %p%", language)
    assert actual.count("%p%") == 1
    if language != "en":
        assert actual != "This step: %p%"


@pytest.mark.parametrize("language", LANGUAGES)
def test_stopping_disk_message_is_platform_neutral_in_each_language(language):
    actual = translate_text(STOPPING_MESSAGE, language)
    assert "Windows" not in actual
    if language != "en":
        assert actual != STOPPING_MESSAGE
