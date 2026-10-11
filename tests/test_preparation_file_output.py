"""Folder destinations use floppy contents as sources, never write targets."""

import hashlib
import io
from pathlib import Path

import mido
import pytest

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
from aps_midi_prep_tool_app.eseq_pianodir import (
    PianodirMetadata, PianodirTrackEntry, build_pianodir_bytes,
)
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY, FloppyImageSession, GreaseweazleFloppySource,
)
from aps_midi_prep_tool_app.preparation_profiles import (
    get_preparation_medium, get_preparation_profile,
)
from test_preparation_automatic import (  # noqa: F401
    _make_image, _settle_import, _song, destination_window,
)


FILE_PROFILES = (
    "enspire", "e3_850", "midi_export", "mark_iv", "pianodisc_prodigy",
    "qrs_pmii", "qrs_pno3", "qrs_pno4",
)


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _catalog_image(directory, *, eseq=False, catalog=True):
    directory.mkdir(parents=True, exist_ok=True)
    song = directory / ("SONG.FIL" if eseq else "SONG.MID")
    song.write_bytes(convert_midi_bytes_to_eseq_bytes(_song(0)) if eseq else _song())
    files = [song]
    if catalog:
        pianodir = directory / "PIANODIR.FIL"
        entries = [PianodirTrackEntry(song.name, str(song), "Automatic preparation")] if eseq else []
        pianodir.write_bytes(build_pianodir_bytes(
            entries, metadata=PianodirMetadata(disk_title="Imported album"),
        ))
        files.append(pianodir)
    image_path = _make_image(directory, files)
    return image_path, _digest(image_path), song.read_bytes()


def _load_floppy(window, image_path, *, source_kind="floppy_usb", gw_source=None):
    session = FloppyImageSession.load(image_path)
    session.source_kind = source_kind
    session.source_name = "Source floppy"
    session.gw_source = gw_source
    window.diskLoadContext = {"load_kind": source_kind}
    window._set_disk_load_busy(True)
    window._on_disk_load_success(session, session.list_entries())
    window._on_disk_load_finished()
    _settle_import()
    return session


def _apply(window, profile_key, medium_key=None):
    profile = get_preparation_profile(profile_key)
    window._apply_preparation_profile(
        profile, get_preparation_medium(profile, medium_key or profile.default_medium),
    )
    _settle_import()


def _assert_file_output_ui(window):
    assert window._preparation_uses_file_output()
    assert not window.is_floppy_mode()
    headline = window.modeBannerLabel.text().splitlines()[0].upper()
    assert "FLOPPY" not in headline
    assert "IMAGE" not in headline
    assert window.writeProtectToggle.isHidden()
    assert window.diskUsageBarsWidget.isHidden()
    assert not window.fileWriteProtectOriginalAction.isEnabled()
    assert not window.fileSaveToFloppyAction.isEnabled()
    assert not window.fileWriteImageToFloppyAction.isEnabled()
    assert window.saveButton.isEnabled()
    assert not any(window._is_special_pianodir_row(row) for row in range(window.table.rowCount()))


@pytest.mark.parametrize("profile_key", FILE_PROFILES)
def test_file_destinations_import_midi_catalog_floppy_as_files_and_save_copies(
    destination_window, monkeypatch, tmp_path, profile_key,
):
    window = destination_window(profile_key, persisted=True)
    image_path, source_digest, original_midi = _catalog_image(tmp_path / "source")
    session = _load_floppy(window, image_path)

    assert window.image_session is session  # Keep original bytes and preparation provenance.
    _assert_file_output_ui(window)
    assert window._current_visible_pianodir_metadata().disk_title == "Imported album"
    output = tmp_path / "output"
    output.mkdir()
    window.toggle_album_subfolder(False)
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(output))
    monkeypatch.setattr(session, "commit_to_source", lambda **_k: pytest.fail("Source disk must not be written"))

    window.save_pending_changes()

    exported = list(output.rglob("*"))
    midi_paths = [path for path in exported if path.suffix.lower() == ".mid"]
    assert len(midi_paths) == 1
    assert midi_paths[0].read_bytes() == original_midi
    assert not any(path.name.upper() == "PIANODIR.FIL" for path in exported)
    assert _digest(image_path) == source_digest
    assert window.image_session is None


@pytest.mark.parametrize("save_method", ("save_pending_changes", "save_image_changes"))
def test_save_exports_floppy_import_even_without_pending_edits(
    destination_window, monkeypatch, tmp_path, save_method,
):
    window = destination_window("midi_export")
    image_path, source_digest, _original = _catalog_image(tmp_path / "source", catalog=False)
    _load_floppy(window, image_path)
    assert not window._has_pending_image_changes()
    exports = []
    monkeypatch.setattr(window, "save_as_changes", lambda: exports.append(True))

    getattr(window, save_method)()

    assert exports == [True]
    assert _digest(image_path) == source_digest


@pytest.mark.parametrize("profile_key", ("enspire", "e3_850", "midi_export"))
def test_eseq_floppy_prepares_midi_and_keeps_album_without_exporting_catalog(
    destination_window, monkeypatch, tmp_path, profile_key,
):
    window = destination_window(profile_key)
    image_path, source_digest, _original = _catalog_image(tmp_path / "source", eseq=True)
    _load_floppy(window, image_path)

    _assert_file_output_ui(window)
    row, source, kind, _filename = next(window._preparation_song_rows())
    assert kind == "midi"
    assert window._row_raw_title(row) == "Automatic preparation"
    assert window._current_visible_pianodir_metadata().disk_title == "Imported album"
    assert mido.MidiFile(window._pending_or_extracted_image_path(source)).type == 0
    output = tmp_path / "output"
    output.mkdir()
    window.toggle_album_subfolder(False)
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(output))

    window.save_image_changes()

    exported = list(output.rglob("*"))
    midi_paths = [path for path in exported if path.suffix.lower() == ".mid"]
    assert len(midi_paths) == 1
    midi = mido.MidiFile(midi_paths[0])
    assert any(message.type == "note_on" and message.note == 60 for track in midi.tracks for message in track)
    assert not any(path.name.upper() == "PIANODIR.FIL" for path in exported)
    assert _digest(image_path) == source_digest


@pytest.mark.parametrize("profile_key,medium_key", (
    ("mark_ii", "original"), ("mark_iii", "original"),
    ("mark_iv", "original"), ("mark_iv", "flashfloppy_img"),
    ("pianodisc_128plus", "emulator_custom"), ("custom", "custom"),
))
def test_floppy_and_emulator_destinations_retain_disk_editing(
    destination_window, tmp_path, profile_key, medium_key,
):
    window = destination_window("custom")
    _apply(window, profile_key, medium_key)
    image_path, source_digest, _original = _catalog_image(tmp_path / "source")
    _load_floppy(window, image_path)

    assert not window._preparation_uses_file_output()
    assert window.is_floppy_mode()
    assert "FLOPPY" in window.modeBannerLabel.text().splitlines()[0].upper()
    assert not window.writeProtectToggle.isHidden()
    assert not window.diskUsageBarsWidget.isHidden()
    assert _digest(image_path) == source_digest


def test_switching_floppy_to_folder_destination_preserves_manual_edits_and_undo(
    destination_window, monkeypatch, tmp_path,
):
    window = destination_window("custom")
    image_path, source_digest, original = _catalog_image(tmp_path / "source")
    session = _load_floppy(window, image_path)
    row, source, _kind, _filename = next(window._preparation_song_rows())
    monkeypatch.setattr(window, "_prompt_for_title", lambda *_a, **_k: ("My chosen title", True))
    window.edit_image_title(row)
    window._stage_image_filename_change(row, "CHOSEN.MID")
    _apply(window, "mark_ii")
    eseq_material = Path(window._pending_or_extracted_image_path(source)).read_bytes()
    assert next(window._preparation_song_rows())[2] == "eseq"

    _apply(window, "enspire")

    _assert_file_output_ui(window)
    assert window.image_session is session
    row, source, kind, filename = next(window._preparation_song_rows())
    assert kind == "midi"
    assert filename.upper() == "CHOSEN.MID"
    assert window._row_raw_title(row) == "My chosen title"
    output = tmp_path / "export" / "CHOSEN.MID"
    window._write_image_row_to_destination(source, str(output))
    midi = mido.MidiFile(output)
    assert midi.type == mido.MidiFile(file=io.BytesIO(original)).type == 1
    assert [message.name for track in midi.tracks for message in track if message.type == "track_name"] == ["My chosen title"]

    window.undo_last_staged_batch()

    assert window._preparation_profile().key == "mark_ii"
    assert window.is_floppy_mode()
    assert "FLOPPY" in window.modeBannerLabel.text().splitlines()[0].upper()
    assert not window.writeProtectToggle.isHidden()
    assert not window.diskUsageBarsWidget.isHidden()
    assert window.image_session is session
    assert next(window._preparation_song_rows())[2] == "eseq"
    assert Path(window._pending_or_extracted_image_path(source)).read_bytes() == eseq_material
    assert window._row_raw_title(next(window._preparation_song_rows())[0]) == "My chosen title"
    assert _digest(image_path) == source_digest


@pytest.mark.parametrize("operation", (
    "save_to_floppy", "write_image_to_floppy", "_save_image_and_apply_to_floppy",
    "_start_floppy_commit_worker", "_start_write_image_to_floppy_worker",
))
def test_file_destination_blocks_direct_physical_write_entry_points(
    destination_window, monkeypatch, tmp_path, operation,
):
    window = destination_window("enspire")
    image_path, source_digest, _original = _catalog_image(tmp_path / "source")
    _load_floppy(window, image_path)

    def unexpected(*_args, **_kwargs):
        pytest.fail("Folder preparation must not select a disk or start a physical write")

    for method in ("_choose_save_to_floppy_drive", "_choose_write_image_floppy_target", "save_as_image"):
        monkeypatch.setattr(window, method, unexpected)
    monkeypatch.setattr(main_window, "DiskSessionCommitWorker", unexpected)
    monkeypatch.setattr(main_window, "DiskSessionWriteTargetWorker", unexpected)
    if operation == "_start_floppy_commit_worker":
        window._start_floppy_commit_worker({}, "Saving floppy")
    elif operation == "_start_write_image_to_floppy_worker":
        window._start_write_image_to_floppy_worker("floppy_usb", None, "Floppy", {})
    else:
        getattr(window, operation)()

    assert window.diskCommitWorker is None
    assert window.diskWriteTargetWorker is None
    assert _digest(image_path) == source_digest


def test_folder_destination_keeps_requested_greaseweazle_capture_offer(
    destination_window, monkeypatch, tmp_path,
):
    window = destination_window("enspire")
    image_path, source_digest, _original = _catalog_image(tmp_path / "source")
    requested = GreaseweazleFloppySource(
        "/dev/ttyACM0", "A", DISK_FORMAT_BY_KEY["ibm.720"], capture_output_ext="hfe",
    )
    captures = []
    monkeypatch.setattr(main_window.QMessageBox, "question", lambda *_a, **_k: main_window.QMessageBox.Yes)
    monkeypatch.setattr(window, "_save_greaseweazle_read_image_now", lambda extension: captures.append(extension))

    _load_floppy(window, image_path, source_kind="floppy_gw", gw_source=requested)

    assert captures == ["hfe"]
    _assert_file_output_ui(window)
    assert _digest(image_path) == source_digest


def test_failed_eseq_preparation_stays_visible_and_cannot_be_saved(
    destination_window, monkeypatch, tmp_path,
):
    window = destination_window("enspire")
    image_path, source_digest, _original = _catalog_image(tmp_path / "source", eseq=True)
    errors = []
    monkeypatch.setattr(window, "_show_error_list", lambda *_a, **_k: errors.append(True))

    def fail_conversion(*_args, **_kwargs):
        raise ValueError("Damaged song")

    monkeypatch.setattr(main_window, "convert_eseq_file_to_midi_path", fail_conversion)
    _load_floppy(window, image_path)
    assert next(window._preparation_song_rows())[2] == "eseq"
    assert errors
    _assert_file_output_ui(window)
    monkeypatch.setattr(window, "save_as_changes", lambda: pytest.fail("Unprepared music must not be exported"))

    window.save_pending_changes()

    assert len(errors) >= 2
    assert _digest(image_path) == source_digest


def test_folder_destination_disables_disk_write_actions_for_regular_files(
    destination_window, tmp_path,
):
    window = destination_window("enspire")
    source = tmp_path / "SONG.MID"
    original = _song()
    source.write_bytes(original)
    window._load_regular_files([str(source)], "Imported song")
    _settle_import()

    assert not window.fileSaveToFloppyAction.isEnabled()
    assert not window.fileWriteImageToFloppyAction.isEnabled()
    assert source.read_bytes() == original


@pytest.mark.parametrize("replacement", (False, True), ids=("addition", "replacement"))
def test_imported_files_can_grow_beyond_source_floppy_capacity_and_export(
    destination_window, monkeypatch, tmp_path, replacement,
):
    window = destination_window("midi_export")
    image_path, source_digest, original = _catalog_image(tmp_path / "source", catalog=False)
    _load_floppy(window, image_path)
    incoming = tmp_path / "incoming" / ("SONG.MID" if replacement else "ADDED.MID")
    incoming.parent.mkdir()
    added = _song(note=72)
    incoming.write_bytes(added)
    monkeypatch.setattr(window, "_pending_image_space_remaining", lambda **_k: -1)
    monkeypatch.setattr(window, "_prompt_drop_filename_conflict", lambda **_k: ("replace", True))
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *_a, **_k: pytest.fail("Source capacity must not limit folder output"))

    window.queue_image_additions([str(incoming)])
    _settle_import()

    staged = window.pendingImageReplacements if replacement else window.pendingImageAdditions
    assert len(staged) == 1
    assert Path(next(iter(staged.values()))).read_bytes() == added
    output = tmp_path / "output"
    output.mkdir()
    window.toggle_album_subfolder(False)
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(output))

    window.save_pending_changes()

    delivered = {path.name: path.read_bytes() for path in output.iterdir() if path.suffix.lower() == ".mid"}
    assert delivered == ({"SONG.MID": added} if replacement else {"SONG.MID": original, "ADDED.MID": added})
    assert incoming.read_bytes() == added
    assert _digest(image_path) == source_digest


def test_manual_and_unsure_profile_switches_refresh_source_mode_and_catalog(
    destination_window, tmp_path,
):
    window = destination_window("custom")
    image_path, source_digest, _original = _catalog_image(tmp_path / "source", eseq=True)
    session = _load_floppy(window, image_path)
    _apply(window, "enspire")
    _assert_file_output_ui(window)
    assert next(window._preparation_song_rows())[2] == "midi"

    _apply(window, "custom")

    assert not window._preparation_uses_file_output()
    assert window.is_floppy_mode()
    assert not window.writeProtectToggle.isHidden()
    assert not window.diskUsageBarsWidget.isHidden()
    assert "FLOPPY" in window.modeBannerLabel.text().splitlines()[0].upper()
    assert next(window._preparation_song_rows())[2] == "midi"  # Custom keeps current preparation.
    assert not any(window._is_special_pianodir_row(row) for row in range(window.table.rowCount()))

    _apply(window, "unsure", "usb")

    _assert_file_output_ui(window)
    assert next(window._preparation_song_rows())[2] == "eseq"  # A new target replaces preparation.

    _apply(window, "unsure", "original")

    assert not window._preparation_uses_file_output()
    assert window.is_floppy_mode()
    assert not window.writeProtectToggle.isHidden()
    assert not window.diskUsageBarsWidget.isHidden()
    assert "FLOPPY" in window.modeBannerLabel.text().splitlines()[0].upper()
    assert any(window._is_special_pianodir_row(row) for row in range(window.table.rowCount()))
    assert window.image_session is session
    assert _digest(image_path) == source_digest


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "floppy"))
def test_unsure_folder_output_preserves_mixed_midi_and_eseq_songs_without_catalog(
    destination_window, monkeypatch, tmp_path, image_mode,
):
    window = destination_window("custom")
    _apply(window, "unsure", "usb")
    directory = tmp_path / "source"
    directory.mkdir()
    midi_path = directory / "MIDI.MID"
    midi_path.write_bytes(_song())
    eseq_path = directory / "ESEQ.FIL"
    eseq_path.write_bytes(convert_midi_bytes_to_eseq_bytes(_song(0, note=72)))
    catalog_path = directory / "PIANODIR.FIL"
    catalog_path.write_bytes(build_pianodir_bytes([
        PianodirTrackEntry(eseq_path.name, str(eseq_path), "Automatic preparation"),
    ]))
    originals = {path: _digest(path) for path in (midi_path, eseq_path, catalog_path)}
    if image_mode:
        image_path = _make_image(directory, originals)
        originals[image_path] = _digest(image_path)
        _load_floppy(window, image_path)
    else:
        window._load_regular_files([str(path) for path in originals], "Imported songs")
        _settle_import()
    assert window._preparation_uses_file_output()
    assert {kind for _row, _source, kind, _filename in window._preparation_song_rows()} == {"midi", "eseq"}
    assert not any(window._is_special_pianodir_row(row) for row in range(window.table.rowCount()))
    if not image_mode:
        # ZIP and other delivery paths share this exporter, while regular Save
        # As walks the table itself. Both must retain the complete mixed set.
        errors, paths, _remap, _summary = window._export_regular_files_to_folder(str(tmp_path / "shared-export"))
        assert not errors
        shared_exports = {Path(path).name: Path(path) for path in paths}
        assert {"MIDI.MID", "ESEQ.FIL"} <= shared_exports.keys()
        assert not any(name.upper() == "PIANODIR.FIL" for name in shared_exports)
        assert shared_exports["MIDI.MID"].read_bytes() == midi_path.read_bytes()
        assert shared_exports["ESEQ.FIL"].read_bytes() == eseq_path.read_bytes()
    output = tmp_path / "output"
    output.mkdir()
    window.toggle_album_subfolder(False)
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(output))

    window.save_as_changes()

    exported = {path.name: path for path in output.iterdir()}
    assert {"MIDI.MID", "ESEQ.FIL"} <= exported.keys()
    assert not any(name.upper() == "PIANODIR.FIL" for name in exported)
    assert exported["MIDI.MID"].read_bytes() == midi_path.read_bytes()
    assert exported["ESEQ.FIL"].read_bytes() == eseq_path.read_bytes()
    assert {kind for _row, _source, kind, _filename in window._preparation_song_rows()} == {"midi", "eseq"}
    assert all(_digest(path) == original for path, original in originals.items())
