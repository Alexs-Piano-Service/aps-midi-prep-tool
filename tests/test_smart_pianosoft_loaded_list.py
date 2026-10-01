"""The utility receives the staged list, not a fresh read of its source disk."""

import io
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import mido
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog

from aps_midi_prep_tool_app import smart_pianosoft_dialog
from aps_midi_prep_tool_app.floppy_image import DISK_FORMAT_BY_KEY, FloppyImageSession
from aps_midi_prep_tool_app.smart_pianosoft import parse_smart_pianosoft_song_catalog
from aps_midi_prep_tool_app.smart_pianosoft_workflow import scan_loaded_album
from test_mute_workflows import window
from test_smart_pianosoft import _pdisk_bytes, _psong_bytes
from test_smart_pianosoft_sync import _fingerprint_message


def _midi_bytes(note=60, *, fingerprints=True):
    track = mido.MidiTrack([mido.MetaMessage("track_name", name="Embedded filename")])
    if fingerprints:
        track.extend([_fingerprint_message(0, 1), _fingerprint_message(2, 10)])
    track.extend([
        mido.Message("note_on", note=note, velocity=80, time=960),
        mido.Message("note_off", note=note, time=480),
        mido.MetaMessage("end_of_track", time=9600),
    ])
    midi = mido.MidiFile(type=0)
    midi.tracks.append(track)
    output = io.BytesIO()
    midi.save(file=output)
    return output.getvalue()


def _album_payloads():
    return {
        "PSONG.MNG": _psong_bytes([
            ("FIRST.MID", "First catalog title"),
            ("SECOND.MID", "Second catalog title"),
            ("THIRD.MID", "Third catalog title"),
        ]),
        "PDISK.MNG": _pdisk_bytes("Catalog album title"),
        "FIRST.MID": _midi_bytes(60),
        "SECOND.MID": _midi_bytes(62),
        "THIRD.MID": _midi_bytes(64),
    }


def _load_local_snapshot(window, tmp_path, monkeypatch, *, source_kind="floppy_usb"):
    payloads = _album_payloads()
    session = FloppyImageSession(
        source_path=str(tmp_path / "disconnected-drive"), source_ext=".img",
        temp_dir=str(tmp_path / "session"), working_img_path=str(tmp_path / "snapshot.img"),
        disk_format=DISK_FORMAT_BY_KEY["ibm.720"],
        repair_result=SimpleNamespace(note="", changed=False, boot_sector_repaired=False),
        source_kind=source_kind, source_name="Previously loaded floppy",
        virtual_files=payloads,
    )
    window.image_session = session
    window.table.setSortingEnabled(False)
    window._load_image_rows(session.list_entries().entries)

    def refuse_source_read(*_args, **_kwargs):
        pytest.fail("The utility reopened the source image or floppy")

    monkeypatch.setattr(session, "_extract_from_image", refuse_source_read)
    return payloads, session


def _row_for(window, path):
    return next(row for row in window._regular_file_rows()
                if window.table.item(row, 1).text() == str(path))


def test_empty_list_leaves_manual_source_selection_available(window):
    assert window._smart_pianosoft_loaded_source() is None


@pytest.mark.parametrize("source_kind", ["image", "floppy_usb", "floppy_gw"])
def test_loaded_snapshot_retains_titles_and_track_numbers_without_source_media(
    window, tmp_path, monkeypatch, source_kind,
):
    original, session = _load_local_snapshot(window, tmp_path, monkeypatch, source_kind=source_kind)
    # Simulate user sorting, removal, an unsaved title, a staged musical edit,
    # and a long folder-export name. None requires saving the floppy first.
    window.pendingImageDeletes.add("SECOND.MID")
    window.table.removeRow(_row_for(window, "SECOND.MID"))
    window._stage_image_title_edit("FIRST.MID", "Edited catalog title")
    row = _row_for(window, "FIRST.MID")
    window.table.setItem(row, 4, window._make_title_item(
        "Edited catalog title", title_mode="midi", edited=True,
    ))
    window.pendingImageRenames["FIRST.MID"] = "NEW.MID"
    window.pendingImageExportFilenames["FIRST.MID"] = "01 - Edited catalog title.mid"
    replacement = tmp_path / "staged.mid"
    replacement.write_bytes(_midi_bytes(67))
    window.pendingImageReplacements["FIRST.MID"] = str(replacement)
    window.table.sortItems(3, Qt.DescendingOrder)

    loaded = window._smart_pianosoft_loaded_source()

    assert not loaded.error
    assert loaded.original_source == Path(session.source_path)
    assert [(track.catalog_filename, track.filename, track.title) for track in loaded.tracks] == [
        ("THIRD.MID", "THIRD.MID", "Third catalog title"),
        ("FIRST.MID", "01 - Edited catalog title.mid", "Edited catalog title"),
    ]
    assert loaded.tracks[1].midi_bytes == replacement.read_bytes()
    assert session.virtual_files == original
    # Source staging may disappear immediately after the immutable handoff.
    replacement.unlink()
    session.cleanup()
    album = scan_loaded_album(loaded, tmp_path / "utility-snapshot")
    assert album.title == "Catalog album title"
    assert [track.number for track in album.tracks] == [3, 1]
    assert [song.track_number for song in parse_smart_pianosoft_song_catalog(
        (album.source_directory / "PSONG.MNG").read_bytes(),
    )] == [1, 3]
    assert album.tracks[1].metadata.events[0].data[:2] == bytes([0x90, 67])


def test_deleted_catalog_is_not_resurrected_from_session_cache(window, tmp_path, monkeypatch):
    _load_local_snapshot(window, tmp_path, monkeypatch)
    window.pendingImageDeletes.add("PSONG.MNG")
    window.table.removeRow(_row_for(window, "PSONG.MNG"))

    loaded = window._smart_pianosoft_loaded_source()

    assert "PSONG.MNG is missing" in loaded.error
    assert not loaded.tracks


def test_replacement_without_fingerprints_is_rejected_by_worker(window, tmp_path, monkeypatch):
    _load_local_snapshot(window, tmp_path, monkeypatch)
    replacement = tmp_path / "ordinary.mid"
    replacement.write_bytes(_midi_bytes(fingerprints=False))
    window.pendingImageReplacements["FIRST.MID"] = str(replacement)
    loaded = window._smart_pianosoft_loaded_source()

    assert not loaded.error  # MIDI validation belongs to the background worker.
    with pytest.raises(ValueError, match="fingerprint"):
        scan_loaded_album(loaded, tmp_path / "rejected-snapshot")
    assert not (tmp_path / "rejected-snapshot").exists()


def test_folder_list_uses_catalog_titles_and_unsaved_midi_edits(window, tmp_path):
    source = tmp_path / "album"
    source.mkdir()
    original = _album_payloads()
    for name, payload in original.items():
        (source / name).write_bytes(payload)
    # The bulk extractor's descriptive filename still identifies CD slot 3.
    third = source / "03 - Third catalog title.mid"
    (source / "THIRD.MID").rename(third)
    first = source / "FIRST.MID"
    window._load_regular_files([str(third), str(first)], "Loaded", prepare_destination=False)
    window.pendingEdits[str(first)] = "An edited title"
    row = _row_for(window, first)
    window.table.setItem(row, 4, window._make_title_item(
        "An edited title", title_mode="midi", edited=True,
    ))
    window.pendingRegularRenames[str(first)] = "Current filename.mid"
    window.table.item(row, 3).setText("Current filename.mid")

    loaded = window._smart_pianosoft_loaded_source()
    album = scan_loaded_album(loaded, tmp_path / "snapshot")

    assert not loaded.error
    assert loaded.original_source == source
    by_number = {track.number: track for track in album.tracks}
    assert by_number[1].display_filename == "Current filename.mid"
    assert by_number[1].title == "An edited title"
    assert by_number[3].title == "Third catalog title"
    copied = mido.MidiFile(by_number[1].midi_path)
    assert [message.name for message in copied.tracks[0] if message.type == "track_name"] == ["An edited title"]
    assert first.read_bytes() == original["FIRST.MID"]


@pytest.mark.parametrize("problem", ["ordinary", "mixed", "missing", "unrelated"])
def test_nonempty_invalid_list_blocks_stale_remembered_source(window, tmp_path, problem):
    source = tmp_path / "album"
    source.mkdir()
    first = source / "FIRST.MID"
    first.write_bytes(_midi_bytes())
    paths = [str(first)]
    if problem != "ordinary":
        (source / "PSONG.MNG").write_bytes(_psong_bytes([("FIRST.MID", "First")]))
    if problem == "mixed":
        second = tmp_path / "SECOND.MID"
        second.write_bytes(_midi_bytes())
        paths.append(str(second))
    elif problem == "unrelated":
        second = source / "OTHER.MID"
        second.write_bytes(_midi_bytes())
        paths.append(str(second))
    window._load_regular_files(paths, "Loaded", prepare_destination=False)
    if problem == "missing":
        first.unlink()
    window.settings.setValue("sps_source", "remembered-other-album")

    loaded = window._smart_pianosoft_loaded_source()

    assert loaded is not None and loaded.error
    assert not loaded.tracks
    assert "remembered-other-album" not in loaded.label


def test_menu_passes_current_list_snapshot_to_utility(window, tmp_path, monkeypatch):
    monkeypatch.setattr(window, "ENABLE_SMART_PIANOSOFT_UTILITY", True)
    _load_local_snapshot(window, tmp_path, monkeypatch)
    captured = []

    class TestDialog(QDialog):
        def __init__(self, settings, parent, *, loaded_source):
            super().__init__(parent)
            captured.append(loaded_source)

    monkeypatch.setattr(smart_pianosoft_dialog, "SmartPianoSoftDialog", TestDialog)
    monkeypatch.setattr(window, "_exec_child_dialog", lambda *_args, **_kwargs: QDialog.Accepted)
    window.show_smart_pianosoft_utility()

    assert len(captured) == 1 and not captured[0].error
    assert len(captured[0].tracks) == 3
    assert window.smartPianoSoftDialog is None


def test_loaded_floppy_retains_device_identity_without_reading_device(window, tmp_path, monkeypatch):
    _original, session = _load_local_snapshot(window, tmp_path, monkeypatch)
    real_stat = os.stat
    device_number = 0x0800

    def source_stat(path, *args, **kwargs):
        if str(path) == session.source_path:
            return SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=device_number)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", source_stat)

    loaded = window._smart_pianosoft_loaded_source()

    assert not loaded.error
    assert loaded.source_device == device_number
