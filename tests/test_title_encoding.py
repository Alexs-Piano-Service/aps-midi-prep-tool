import json
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLineEdit

from aps_midi_prep_tool_app import main_window, midi_metadata
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.preparation_profiles import get_preparation_medium, get_preparation_profile
from aps_midi_prep_tool_app.startup_config import StartupConfigError, load_startup_config


def _midi_bytes(title_bytes):
    track = b"\x00\xff\x03" + bytes([len(title_bytes)]) + title_bytes
    track += b"\x00\x90\x3c\x40\x60\x80\x3c\x00\x00\xff\x2f\x00"
    return b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk" + len(track).to_bytes(4, "big") + track


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    instance = main_window.MidiTitleWindow()
    yield instance
    instance._confirm_discard_image_changes = lambda: True
    instance.close()
    app.processEvents()


@pytest.mark.parametrize("profile,mode,screen,accepted", [
    ("midi_export", "midi", False, True),
    ("e3_850", "midi", False, True),
    ("enspire", "midi", False, True),
    ("mark_ii_xg", "midi", False, False),
    ("pianodisc_128plus", "midi", False, False),
    ("midi_export", "eseq", False, False),
    ("midi_export", "smart_pianosoft", False, False),
    ("unsure", "midi", False, False),
    ("custom", "midi", False, True),
    ("custom", "midi", True, False),
])
def test_destination_title_character_policy(profile, mode, screen, accepted):
    window = SimpleNamespace(
        settings=SimpleNamespace(value=lambda _key, _default: profile),
        format_disklavier_checkbox=SimpleNamespace(isChecked=lambda: screen),
        _language_code=lambda: "en",
    )
    error = main_window.MidiTitleWindow._validate_title_for_destination(window, "Beyoncé Live", mode)
    assert (error is None) is accepted
    assert main_window.MidiTitleWindow._validate_title_for_destination(window, "Beyonce Live", mode) is None


@pytest.mark.parametrize("title", ["東京", "Song\nTitle", "Song\x85Title", "Song 😀"])
def test_modern_title_policy_still_rejects_unwritable_or_control_characters(title):
    assert midi_metadata.validate_title_input(title, legacy=False)


@pytest.mark.parametrize("profile,accepted", [("midi_export", True), ("mark_ii_xg", False)])
def test_accented_title_editor_and_export_use_destination_policy(window, tmp_path, monkeypatch, profile, accepted):
    source = tmp_path / "SONG.MID"
    source.write_bytes(_midi_bytes("Beyoncé".encode("latin1")))
    original = source.read_bytes()
    window.settings.setValue("preparation_profile", profile)
    window._load_regular_files([str(source)], "Loaded", prepare_destination=False)
    window.format_disklavier_checkbox.setChecked(False)

    def edit_dialog(dialog):
        field = dialog.findChild(QLineEdit, "songTitleEditor")
        assert field.text() == "Beyoncé"
        field.setText("Beyoncé Live")
        assert dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).isEnabled() is accepted
        return QDialog.Accepted if accepted else QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", edit_dialog)
    window.edit_via_dialog(window._find_regular_row_for_path(str(source)))
    assert window.pendingEdits == ({str(source): "Beyoncé Live"} if accepted else {})
    destination = tmp_path / "export.mid"
    assert window._write_listed_file_to_path(str(source), window._row_raw_title(0), str(destination)) is None
    assert source.read_bytes() == original
    if accepted:
        assert midi_metadata.read_first_title_from_midi(destination) == "Beyoncé Live"
        window.settings.setValue("preparation_profile", "mark_ii_xg")
        before = destination.read_bytes()
        assert "ASCII" in window._write_listed_file_to_path(str(source), "Beyoncé Live", str(destination))
        assert destination.read_bytes() == before
    else:
        assert destination.read_bytes() == original


@pytest.mark.parametrize("mode", ["midi", "eseq"])
def test_japanese_display_no_op_editor_and_export_preserve_bytes(window, tmp_path, monkeypatch, mode):
    title = "  日本の歌  "
    payload = title.encode("shift_jis")
    original = _midi_bytes(payload)
    if mode == "eseq":
        original = convert_midi_bytes_to_eseq_bytes(original)
        # Preserve a noncanonical padding area too, not just the visible title.
        original = original[:0x57] + payload + b"\x00" + b"X" * (31 - len(payload)) + original[0x77:]
    source = tmp_path / ("SONG.MID" if mode == "midi" else "SONG.FIL")
    source.write_bytes(original)
    window.settings.setValue("preparation_profile", "midi_export")
    window._set_title_display_encoding("shift_jis")
    window._load_regular_files([str(source)], "Loaded", prepare_destination=False)
    window.format_disklavier_checkbox.setChecked(False)
    row = window._find_regular_row_for_path(str(source))
    raw_title = window._row_raw_title(row)
    displayed_title = title.rstrip(" ") if mode == "eseq" else title
    assert window.table.item(row, 4).text() == displayed_title
    assert raw_title.encode("latin1") == displayed_title.encode("shift_jis")
    assert not window._row_title_spacing_needs_trim(row)

    def accept_original(dialog):
        assert dialog.findChild(QLineEdit, "songTitleEditor").text() == displayed_title
        assert dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).isEnabled()
        return QDialog.Accepted

    monkeypatch.setattr(window, "_exec_child_dialog", accept_original)
    window.edit_via_dialog(row)
    assert not window.pendingEdits
    destination = tmp_path / ("copy.mid" if mode == "midi" else "copy.fil")
    assert window._write_listed_file_to_path(str(source), raw_title, str(destination)) is None
    assert source.read_bytes() == destination.read_bytes() == original


def test_encoding_change_keeps_sorted_row_identity_and_undo_uses_current_encoding(window, tmp_path, monkeypatch):
    titles = {"ONE.MID": "日本", "TWO.MID": "東京", "THREE.MID": "音楽"}
    for name, title in titles.items():
        (tmp_path / name).write_bytes(_midi_bytes(title.encode("shift_jis")))
    window.settings.setValue("preparation_profile", "midi_export")
    window._load_regular_files([str(tmp_path / name) for name in titles], "Loaded", prepare_destination=False)
    window.table.setSortingEnabled(True)
    window.table.sortItems(4, Qt.AscendingOrder)
    monkeypatch.setattr(window, "_prompt_for_title", lambda *_args, **_kwargs: ("Beyoncé", True))
    row = window._find_regular_row_for_path(str(tmp_path / "ONE.MID"))
    window.edit_via_dialog(row)
    window._set_title_display_encoding("shift_jis")
    row = window._find_regular_row_for_path(str(tmp_path / "ONE.MID"))
    assert window.table.item(row, 4).text() == "Beyoncé"
    window.undo_last_staged_batch()
    for name, title in titles.items():
        row = window._find_regular_row_for_path(str(tmp_path / name))
        assert window.table.item(row, 4).text() == title
        assert window._row_raw_title(row).encode("latin1") == title.encode("shift_jis")


def test_switch_to_legacy_destination_retains_incompatible_edit_for_correction(window, tmp_path, monkeypatch):
    source = tmp_path / "SONG.MID"
    original = _midi_bytes(b"Original")
    source.write_bytes(original)
    window.settings.setValue("preparation_profile", "midi_export")
    window._load_regular_files([str(source)], "Loaded", prepare_destination=False)
    window._stage_trimmed_title_for_row(0, "Beyoncé Live", "midi")
    errors = []
    monkeypatch.setattr(window, "_show_error_list", lambda *_args, **_kwargs: errors.extend(_args[2]))
    profile = get_preparation_profile("mark_ii")
    window._apply_preparation_profile(profile, get_preparation_medium(profile, "original"))
    assert not window.pendingRegularConversions
    assert window.pendingEdits == {str(source): "Beyoncé Live"}
    assert any("ASCII" in error for error in errors)
    assert source.read_bytes() == original


def test_removed_image_title_does_not_block_save_validation(window, monkeypatch):
    window.settings.setValue("preparation_profile", "mark_ii_xg")
    window.imageFileInfo["SONG.MID"] = {
        "title": "Beyoncé Live", "title_mode": "midi", "title_edited": True,
    }
    window.pendingImageDeletes.add("SONG.MID")
    warnings = []
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *_args: warnings.append(_args[2]))
    assert window._validate_pending_image_titles()
    assert not warnings
    window.pendingImageDeletes.clear()
    assert not window._validate_pending_image_titles()
    assert "ASCII" in warnings[0]


def test_explicit_decoding_never_guesses_and_keeps_invalid_bytes():
    raw = b"\x82".decode("latin1")
    assert midi_metadata.title_for_display(raw, "shift_jis") == "\ufffd"
    assert raw.encode("latin1") == b"\x82"
    assert midi_metadata.title_for_display("Beyoncé") == "Beyoncé"
    with pytest.raises(ValueError, match="Unsupported title display encoding"):
        midi_metadata.title_for_display(raw, "utf-8")


def test_startup_encoding_choice_is_explicit_and_validated(tmp_path):
    config = tmp_path / "aps-midi-prep-tool.json"
    config.write_text(json.dumps({"title_display_encoding": "shift_jis"}))
    assert load_startup_config(config)["title_display_encoding"] == "shift_jis"
    config.write_text(json.dumps({"title_display_encoding": "guess"}))
    with pytest.raises(StartupConfigError, match="title_display_encoding"):
        load_startup_config(config)
