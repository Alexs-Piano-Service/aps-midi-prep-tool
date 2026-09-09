import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QLabel

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.eseq_converter import EseqConversionError, convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY, FloppyImageSession, create_floppy_images_from_files,
)
from aps_midi_prep_tool_app.main_window import MidiTitleWindow
from aps_midi_prep_tool_app.preparation_profiles import get_preparation_medium, get_preparation_profile


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    instance = MidiTitleWindow()
    yield instance
    instance.pendingRegularConversions.clear()
    instance.pendingRegularRenames.clear()
    instance.pendingImageReplacements.clear()
    instance.pendingImageRenames.clear()
    instance.pendingImageDeletes.clear()
    instance.pendingGeneratePianodir = False
    instance.pendingDeletePianodir = False
    instance._confirm_discard_image_changes = lambda: True
    instance.close()
    app.processEvents()


def _apply(window, key):
    profile = get_preparation_profile(key)
    window._apply_preparation_profile(profile, get_preparation_medium(profile, "original"))


def _midi():
    track = b"\x00\x90\x3c\x40\x60\x80\x3c\x00\x00\xff\x2f\x00"
    return b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk" + len(track).to_bytes(4, "big") + track


@pytest.mark.parametrize("profile_key,target", (
    ("mark_i", "eseq"), ("mark_ii", "eseq"), ("mark_iii", "midi"), ("enspire", "midi"),
))
@pytest.mark.parametrize("image_mode", (False, True))
def test_destination_blocks_reverse_conversion_and_custom_restores_it(
    window, monkeypatch, tmp_path, profile_key, target, image_mode,
):
    source = tmp_path / ("SONG.MID" if target == "eseq" else "SONG.FIL")
    payload = _midi() if target == "eseq" else convert_midi_bytes_to_eseq_bytes(_midi())
    source.write_bytes(payload)
    if image_mode:
        image_path = tmp_path / "source.img"
        create_floppy_images_from_files(
            [(str(source), source.name)], str(image_path), "img", DISK_FORMAT_BY_KEY["ibm.720"],
        )
        session = FloppyImageSession.load(image_path)
        window._activate_disk_session(session, session.list_entries())
    else:
        window._load_regular_files([str(source)], "Loaded")
    _apply(window, profile_key)
    opposite = "midi" if target == "eseq" else "eseq"
    button = window.convertEseqToMidiButton if opposite == "midi" else window.convertMidiToEseqButton
    action = window.utilitiesEseqToMidiAction if opposite == "midi" else window.utilitiesMidiToEseqAction
    reason = window._preparation_conversion_restriction(opposite)
    assert "disabled" in reason and "Custom" in reason
    assert reason == window.preparationLabel.toolTip()
    assert "\n" not in window.preparationLabel.text()
    assert not button.isEnabled()
    assert not action.isEnabled()
    assert button.toolTip() == action.toolTip() == action.statusTip() == reason

    # Direct invocation must not reverse a staged destination conversion.
    notices = []
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *args: notices.append(args[2]))
    before = window._preparation_song_counts()
    if opposite == "midi":
        window.convert_all_eseq_to_midi()
    else:
        window.convert_all_midi_to_eseq()
    assert notices == [reason]
    assert window._preparation_song_counts() == before
    with pytest.raises(EseqConversionError, match="disabled"):
        if image_mode:
            window._queue_image_format_conversion(0, opposite)
        else:
            window._stage_regular_row_conversion(0, str(source), opposite)
    assert source.read_bytes() == payload

    _apply(window, "custom")
    assert window._preparation_conversion_restriction(opposite) == ""
    assert button.isEnabled()
    assert action.isEnabled()
    assert "disabled" not in button.toolTip()
    assert "disabled" not in window.preparationLabel.text()
    window._set_disk_load_busy(True)
    assert not button.isEnabled()
    assert not action.isEnabled()
    window._set_disk_load_busy(False)
    assert button.isEnabled()


def test_eseq_destination_disables_saved_convert_after_read_choice(window, monkeypatch):
    _apply(window, "mark_i")
    window.settings.setValue(window.SETTING_READ_FLOPPY_CONVERT_TO_MIDI, True)
    monkeypatch.setattr(main_window, "list_floppy_drives", lambda: [])
    monkeypatch.setattr(main_window, "list_greaseweazle_devices", lambda: [])

    def inspect(dialog):
        checkbox = next(item for item in dialog.findChildren(QCheckBox)
                        if item.text() == "Convert E-SEQ files to MIDI after reading")
        assert not checkbox.isEnabled()
        assert not checkbox.isChecked()
        assert "disabled" in checkbox.toolTip()
        assert any(label.text() == checkbox.toolTip() for label in dialog.findChildren(QLabel))
        return QDialog.Rejected

    window._exec_child_dialog = inspect
    assert window._choose_floppy_read_options() is None
    window.pendingFloppyReadConvertToMidi = True
    window.pendingFloppyReadLongFilenames = True
    window._convert_loaded_floppy_to_midi_after_read()
    assert not window.pendingFloppyReadConvertToMidi
    assert not window.pendingFloppyReadLongFilenames
    assert "disabled" in window.status_label.text()


@pytest.mark.parametrize("target", ("midi", "eseq"))
def test_drop_of_destination_format_ignores_conflicting_old_mode(window, tmp_path, target):
    _apply(window, "mark_i" if target == "eseq" else "mark_iii")
    source = tmp_path / ("CORRECT.FIL" if target == "eseq" else "CORRECT.MID")
    source.write_bytes(convert_midi_bytes_to_eseq_bytes(_midi()) if target == "eseq" else _midi())
    # A failed earlier preparation can leave source files in another mode.
    window.regularEseqMode = target == "midi"
    window.regularDropBatchPrepared = True
    window.regularDropBatchPromotesToEseq = False
    result = window.add_regular_file_from_drop(str(source))
    assert result["status"] == "added"
    assert not window.pendingRegularConversions
    window.imageEseqMode = target == "midi"
    assert window._image_drop_conversion_kind(str(source)) == ""
    with pytest.raises(EseqConversionError, match="disabled"):
        window._stage_image_addition_host_file(
            str(source), conversion_kind="midi" if target == "eseq" else "eseq",
        )
