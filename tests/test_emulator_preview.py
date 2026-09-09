import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

from aps_midi_prep_tool_app.disk_session_worker import EmulatorImageBuildWorker
from aps_midi_prep_tool_app.emulator_image_builder import (
    EmulatorAlbumPreview, EmulatorBuildPreview, EmulatorDiskPreview, _PreparedSong,
    build_emulator_disk_images,
)
from aps_midi_prep_tool_app.emulator_preview_dialog import EmulatorPreviewDialog
from aps_midi_prep_tool_app.floppy_image import DISK_FORMAT_BY_KEY, FloppyImageSession, FloppyOperationCancelled
from aps_midi_prep_tool_app.midi_metadata import read_first_title_from_midi


def _midi(title):
    title = title.encode("ascii")
    track = b"\x00\xff\x03" + bytes([len(title)]) + title + b"\x00\x90\x3c\x40\x60\x80\x3c\x00\x00\xff\x2f\x00"
    return b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60MTrk" + len(track).to_bytes(4, "big") + track


def _source(tmp_path):
    source = tmp_path / "source"
    for album in ("Alpha", "Beta", "Gamma"):
        folder = source / album
        folder.mkdir(parents=True)
        (folder / "song.mid").write_bytes(_midi(album))
    (source / "Alpha" / "INDEX.csv").write_text("filename,title\nsong.mid,Index title\n", encoding="utf-8")
    return source


def test_review_rebuilds_exclusions_order_and_titles_before_any_output_is_committed(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "output"
    original_beta = (source / "Beta" / "song.mid").read_bytes()
    previews = []

    def review(preview):
        assert list(output.iterdir()) == []
        previews.append(preview)
        if len(previews) == 1:
            assert len(preview.disks) == 3
            assert preview.disks[0].songs[0].title == "Index title"
            assert preview.disks[0].songs[0].title_source == "INDEX.csv"
            return {
                "action": "revise",
                "included_folders": [str(source / "Beta"), str(source / "Alpha")],
                "album_titles": {str(source / "Beta"): "New album"},
                "title_overrides": {str(source / "Beta" / "song.mid"): "Corrected song"},
            }
        assert len(preview.disks) == 2
        song = preview.disks[0].songs[0]
        assert song.source_path == str(source / "Beta" / "song.mid")
        assert song.album_title == "New album"
        assert song.title == "Corrected song"
        assert song.title_source == "Preview edit"
        assert song.conversion_report.notes_changed is False
        assert song.conversion_report.pedals_changed is False
        assert song.conversion_report.after.notes == 1
        assert preview.albums[-1].included is False
        return {"action": "build"}

    result = build_emulator_disk_images(
        source, output, output_ext="img", output_content="midi", disk_layout="folders",
        disk_format=DISK_FORMAT_BY_KEY["ibm.720"], include_song_lists=True, review_callback=review,
    )
    assert len(previews) == 2
    assert result.images_created == 2
    assert result.contents_verified
    assert (source / "Beta" / "song.mid").read_bytes() == original_beta
    session = FloppyImageSession.load(result.output_paths[0])
    try:
        song_entry = next(entry for entry in session.list_entries().entries if entry.path.upper().endswith(".MID"))
        assert read_first_title_from_midi(session.extract_file(song_entry.path)) == "Corrected song"
    finally:
        session.cleanup()
    song_list = Path(result.song_list_path).read_text(encoding="utf-8")
    assert "New album" in song_list
    assert "Corrected song" in song_list
    assert "Gamma" not in song_list


def test_cancel_preview_keeps_existing_output_untouched(tmp_path):
    source = _source(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    original = output / "DSKA0000.img"
    original.write_bytes(b"original output")
    with pytest.raises(FloppyOperationCancelled):
        build_emulator_disk_images(
            source, output, output_ext="img", output_content="midi", review_callback=lambda _preview: None,
            overwrite_existing=True,
        )
    assert original.read_bytes() == b"original output"
    assert list(output.iterdir()) == [original]


def _preview():
    songs = (
        _PreparedSong("/source/Alpha/a.mid", "00A.MID", "/temp/a.mid", "A", album_title="Alpha"),
        _PreparedSong("/source/Beta/b.mid", "01B.MID", "/temp/b.mid", "B", album_title="Beta"),
    )
    return EmulatorBuildPreview(
        "/source", "/output",
        (EmulatorDiskPreview("/output/DSKA0001.img", 600000, songs),),
        (EmulatorAlbumPreview("/source/Alpha", "Alpha", 1, True, "Folder name"),
         EmulatorAlbumPreview("/source/Beta", "Beta", 1, True, "Folder name")),
        (), {}, {}, "midi", "folders",
    )


def test_preview_requires_repacking_after_title_order_or_exclusion_edits():
    app = QApplication.instance() or QApplication([])
    dialog = EmulatorPreviewDialog(_preview())
    try:
        assert dialog.build_button.isEnabled()
        dialog.songs_table.item(0, 3).setText("Edited A")
        assert not dialog.build_button.isEnabled()
        dialog.albums_table.setCurrentCell(1, 1)
        dialog.up_button.click()
        assert dialog.albums_table.item(0, 1).text() == "Beta"
        dialog.albums_table.item(1, 0).setCheckState(Qt.Unchecked)
        dialog.albums_table.item(0, 1).setText("New Beta")
        dialog.update_button.click()
        assert dialog.result() == QDialog.Accepted
        assert dialog.decision == {
            "action": "revise", "included_folders": ["/source/Beta"],
            "album_titles": {"/source/Beta": "New Beta"},
            "title_overrides": {"/source/Alpha/a.mid": "Edited A"},
        }
    finally:
        dialog.close()


def test_worker_preview_handshake_passes_decision_and_honors_cancellation():
    worker = EmulatorImageBuildWorker(
        "source", "output", prefix="DSKA", starting_number=1, safety_margin_bytes=0,
        album_title="", disk_format=DISK_FORMAT_BY_KEY["ibm.720"], output_ext="img",
    )
    worker.previewRequested.connect(lambda _preview: worker.resolve_preview_request({"action": "build"}))
    assert worker._request_preview(_preview()) == {"action": "build"}
    worker.cancel()
    with pytest.raises(FloppyOperationCancelled):
        worker._request_preview(_preview())
