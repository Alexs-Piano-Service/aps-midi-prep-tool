import os
from pathlib import Path
from string import Formatter
from types import MethodType

from aps_midi_prep_tool_app.main_window import MidiTitleWindow
from aps_midi_prep_tool_app.message_catalog import (
    MESSAGES,
    SAVE_AS_IMAGE_ALBUM_SUBFOLDER_MESSAGE_IDS,
    SUPPORTED_LANGUAGES,
    tr,
    translate_text,
)


class FakeAction:
    def __init__(self, checked):
        self.checked = checked

    def isChecked(self):
        return self.checked


class FakeWindow:
    def __init__(self, *, enabled, metadata_available=True):
        self.fileCreateImageAlbumSubfolderAction = FakeAction(enabled)
        self._metadata_available = metadata_available
        self._image_album_subfolder_enabled = MethodType(
            MidiTitleWindow._image_album_subfolder_enabled,
            self,
        )

    def _album_subfolder_metadata_available(self):
        return self._metadata_available

    def _album_subfolder_name(self):
        return "CAT-001 Customer Album"

    def _t(self, message_id, **kwargs):
        return tr(message_id, "en", **kwargs)


def test_save_as_image_album_subfolder_is_off_by_default():
    assert MidiTitleWindow.DEFAULT_IMAGE_EXPORT_ALBUM_SUBFOLDER is False


def test_save_as_image_output_is_routed_through_album_subfolder(tmp_path):
    window = FakeWindow(enabled=True)
    selected_path = os.fspath(tmp_path / "customer_disk.img")

    output_path = MidiTitleWindow._image_output_with_album_subfolder(
        window,
        selected_path,
    )

    assert Path(output_path) == (
        tmp_path / "CAT-001 Customer Album" / "customer_disk.img"
    )
    assert MidiTitleWindow._save_as_image_album_subfolder_note(
        window,
        selected_path,
        output_path,
    ) == "Saved image in album subfolder: CAT-001 Customer Album"


def test_save_as_image_output_stays_selected_when_option_cannot_apply(tmp_path):
    selected_path = os.fspath(tmp_path / "customer_disk.img")

    disabled_window = FakeWindow(enabled=False)
    assert MidiTitleWindow._image_output_with_album_subfolder(
        disabled_window,
        selected_path,
    ) == selected_path

    missing_metadata_window = FakeWindow(enabled=True, metadata_available=False)
    output_path = MidiTitleWindow._image_output_with_album_subfolder(
        missing_metadata_window,
        selected_path,
    )
    assert output_path == selected_path
    assert "no album title or catalog number" in (
        MidiTitleWindow._save_as_image_album_subfolder_note(
            missing_metadata_window,
            selected_path,
            output_path,
        )
    )


def test_album_subfolder_note_preserves_case_on_case_insensitive_system(tmp_path, monkeypatch):
    window = FakeWindow(enabled=True)
    monkeypatch.setattr(os.path, "normcase", lambda path: path.lower())
    selected = tmp_path / "Disk.img"
    output = tmp_path / "Billy Joel Album" / "Disk.img"
    note = MidiTitleWindow._save_as_image_album_subfolder_note(window, str(selected), str(output))
    assert note == "Saved image in album subfolder: Billy Joel Album"
    assert MidiTitleWindow._save_as_image_album_subfolder_note(
        window, str(selected), str(selected).upper(),
    ) == ""


def test_save_as_image_album_subfolder_messages_cover_every_language():
    supported_codes = {language.code for language in SUPPORTED_LANGUAGES}
    formatter = Formatter()
    for message_id in SAVE_AS_IMAGE_ALBUM_SUBFOLDER_MESSAGE_IDS:
        assert set(MESSAGES[message_id]) == supported_codes
        expected_fields = {
            field_name
            for _literal, field_name, _format_spec, _conversion in formatter.parse(
                MESSAGES[message_id]["en"]
            )
            if field_name
        }
        for language_code in supported_codes:
            translation = MESSAGES[message_id][language_code]
            assert translation.strip()
            translated_fields = {
                field_name
                for _literal, field_name, _format_spec, _conversion in formatter.parse(
                    translation
                )
                if field_name
            }
            assert translated_fields == expected_fields

    assert translate_text(
        "Create Album Subfolder for Save As Image",
        "bg",
    ) == "Създаване на подпапка за албума при запис като образ"
