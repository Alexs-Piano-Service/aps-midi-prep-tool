import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import floppy_image, main_window
from aps_midi_prep_tool_app.pending_changes import staged_batch


def _song():
    track = (
        b"\x00\xff\x03\x07Roadmap"
        b"\x00\xff\x7f\x07\x43\x7b\x01\x31\x00\x7f\x7f"
        b"\x00\xff\x59\x02\x01\x00"
        b"\x00\x90\x3c\x40\x30\xb0\x40\x48"
        b"\x30\x80\x3c\x00\x00\xb0\x40\x00\x00\xff\x2f\x00"
    )
    return b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk" + len(track).to_bytes(4, "big") + track


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "review.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *args: settings)
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *args, **kwargs: None)
    w = main_window.MidiTitleWindow()
    yield w
    w._clear_staging_history()
    w._cleanup_midi_scratch_dir()
    if w.image_session is not None:
        w.image_session.cleanup()
    w.deleteLater()
    app.processEvents()


def _open_image(w, tmp_path, source=None):
    path = tmp_path / "music.img"
    disk_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]
    floppy_image.create_blank_floppy_image(path, disk_format)
    if source is not None:
        floppy_image._copy_host_file_into_image(path, source, "SONG.MID")
    session = floppy_image.FloppyImageSession.load(path)
    w._activate_disk_session(session, session.list_entries(), prepare_destination=False)


@pytest.mark.parametrize("mode", ("regular", "image", "addition"))
def test_xf_cleanup_then_format_conversion_reports_all_original_changes(window, tmp_path, mode):
    w = window
    source = tmp_path / "SONG.MID"
    original = _song()
    source.write_bytes(original)
    if mode == "regular":
        path = str(source)
        w._load_regular_files([path], "Synthetic conversion review", prepare_destination=False)
        w._strip_xf_from_regular_rows([(0, path)])
        w._stage_regular_row_conversion(0, path, "eseq")
        details = w.pendingRegularConversions[path]
    else:
        _open_image(w, tmp_path, source if mode == "image" else None)
        if mode == "addition":
            w.queue_image_additions([str(source)])
            path = next(iter(w.pendingImageAdditions))
            first_staged = Path(w.pendingImageAdditions[path])
        else:
            path = "SONG.MID"
        row = next(row for row in range(w.table.rowCount()) if w.table.item(row, 1).text() == path)
        w._strip_xf_from_image_rows([(row, path)])
        if mode == "addition":
            # A report must survive both source removal and cleanup of a prior
            # staging file. Undo only retains the current staged material.
            source.unlink()
            first_staged.unlink()

        @staged_batch
        def convert(window):
            window._queue_image_format_conversion(row, "eseq")

        convert(w)
        result_path = w.table.item(row, 1).text()
        details = w.imageFileInfo[result_path]
        if mode == "addition":
            assert details["change_report_baseline"] == original
            w.undo_last_staged_batch()
            restored = w.imageFileInfo[path]
            assert restored["change_report_baseline"] == original
            assert restored["change_report"]["removed_metadata"] == {"Sequencer-specific": 1}

    report = details["change_report"]
    assert not details["change_report_error"]
    assert report["before"]["format"] == "MIDI Type 0"
    assert report["after"]["format"] == "E-SEQ"
    assert report["removed_metadata"] == {"Sequencer-specific": 1, "Key Signature": 1}
    # The final report includes the requested legacy scheduling, while still
    # proving that the earlier cleanup and format conversion retained values.
    assert report["legacy_timing"]
    assert report["notes_changed"]
    assert report["pedals_changed"]
    assert report["channel_payload_changed"] is True
    assert tuple(report["pedal_channels_routed"]) == (64,)
    assert report["other_channel_payload_changed"] is False
    assert tuple(report["yamaha_pedal_controllers"]) == (64,)
    assert report["expected_channel_events_changed"] is False
    assert report["before"]["notes"] == report["after"]["notes"] == 1
    assert report["before"]["pedals"] == report["after"]["pedals"] == {64: 2}
    if source.exists():
        assert source.read_bytes() == original


def test_automatically_converted_addition_keeps_input_report_and_baseline(window, tmp_path):
    w = window
    source = tmp_path / "SONG.MID"
    source.write_bytes(_song())
    _open_image(w, tmp_path)
    w.imageEseqMode = True

    w.queue_image_additions([str(source)])

    path = next(iter(w.pendingImageAdditions))
    details = w.imageFileInfo[path]
    assert details["change_report_baseline"] == source.read_bytes()
    assert details["change_report"]["before"]["format"] == "MIDI Type 0"
    assert details["change_report"]["after"]["format"] == "E-SEQ"
    assert details["change_report"]["removed_metadata"]["Key Signature"] == 1
