"""A complete split image export must preserve preexisting companion paths."""

import io
from pathlib import Path

import mido
import pytest

from aps_midi_prep_tool_app.floppy_image import DISK_FORMAT_BY_KEY, FloppyImageSession
from test_save_as_overwrite import window


def _large_song(title):
    song = mido.MidiFile(type=0)
    song.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name=title),
        mido.MetaMessage("text", text="x" * 400_000),
        mido.Message("note_on", note=60, velocity=80),
        mido.Message("note_off", note=60, time=480),
    ]))
    stream = io.BytesIO()
    song.save(file=stream)
    return stream.getvalue()


def _prepare_export(window, tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    originals = {name: _large_song(title) for name, title in (
        ("FIRST.MID", "First song"), ("SECOND.MID", "Second song"),
    )}
    for name, data in originals.items():
        (source / name).write_bytes(data)
    window._load_regular_files(
        [str(source / name) for name in originals], "Split export", prepare_destination=False,
    )
    window.fileCreateImageAlbumSubfolderAction.setChecked(False)
    window.settings.setValue(window.SETTING_HIDE_SAVE_AS_IMAGE_COMPLETE_DIALOG, True)
    output = tmp_path / "exports" / "Concert.img"
    output.parent.mkdir()
    monkeypatch.setattr(window, "_prompt_for_save_image_options", lambda **_kwargs: (
        str(output), "img", DISK_FORMAT_BY_KEY["ibm.720"],
    ))
    return source, output, originals


def _complete_split_export(window, output):
    # Exercise actual staging, image packing/publication, companion copies, and
    # reloading of the GUI's current file list. Only file-picker input is mocked.
    result = window.save_as_image()
    assert not window._test_errors
    assert result == tuple(str(output.with_name(f"Concert_{number:02d}.img"))
                           for number in (1, 2))
    assert not output.exists()
    assert not window.is_image_mode()
    listed = [Path(window.table.item(row, 1).text()) for row in window._regular_file_rows()]
    assert len(listed) == 2
    context_directories = {path.parent for path in listed}
    assert len(context_directories) == 1
    context = context_directories.pop()
    assert context.parent == output.parent
    assert context.name.startswith("Concert_files")
    assert context.is_dir() and not context.is_symlink()
    assert all(path.is_file() for path in listed)
    companion_bytes = {path.name: path.read_bytes() for path in listed}

    delivered = {}
    for image in result:
        session = FloppyImageSession.load(image)
        try:
            for entry in session.list_entries().entries:
                if entry.path.upper().endswith(".MID"):
                    assert entry.path not in delivered
                    delivered[entry.path] = Path(session.extract_file(entry.path)).read_bytes()
        finally:
            session.cleanup()
    assert delivered == companion_bytes
    return tuple(Path(path) for path in result), context, companion_bytes


def _tree(directory):
    return {str(path.relative_to(directory)): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


@pytest.mark.parametrize("existing_kind", ["directory", "symlink", "file", "broken_symlink"])
def test_split_export_preserves_existing_companion_path(
    window, tmp_path, monkeypatch, existing_kind,
):
    source, output, originals = _prepare_export(window, tmp_path, monkeypatch)
    occupied = output.with_name("Concert_files")
    occupied_suffix = output.with_name("Concert_files_2")
    occupied_suffix.mkdir()
    (occupied_suffix / "FIRST.MID").write_bytes(b"Preserve an earlier numbered companion set")
    original_suffix_tree = _tree(occupied_suffix)
    target = None
    if existing_kind in ("directory", "symlink"):
        target = occupied if existing_kind == "directory" else tmp_path / "unrelated files"
        target.mkdir()
        # The matching filename catches following the symlink and overwriting
        # target contents even when deletion of the symlink itself is refused.
        (target / "FIRST.MID").write_bytes(b"The only copy of an unrelated song")
        (target / "notes").mkdir()
        (target / "notes" / "sentinel.txt").write_bytes(b"Keep these handwritten notes")
        original_tree = _tree(target)
    if existing_kind in ("symlink", "broken_symlink"):
        if target is None:
            target = tmp_path / "absent symlink target"
        try:
            occupied.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("Directory symlinks are unavailable on this platform")
        original_link = occupied.readlink()
    elif existing_kind == "file":
        occupied.write_bytes(b"An unrelated file at the companion path")

    _images, context, companions = _complete_split_export(window, output)

    assert context not in (occupied, occupied_suffix)
    assert _tree(occupied_suffix) == original_suffix_tree
    assert companions == originals
    assert {name: (source / name).read_bytes() for name in originals} == originals
    if existing_kind in ("directory", "symlink"):
        assert _tree(target) == original_tree
    if existing_kind in ("symlink", "broken_symlink"):
        assert occupied.is_symlink()
        assert occupied.readlink() == original_link
        if existing_kind == "broken_symlink":
            assert not target.exists()
    elif existing_kind == "file":
        assert occupied.read_bytes() == b"An unrelated file at the companion path"


def test_repeat_split_export_preserves_edited_earlier_companion_files(
    window, tmp_path, monkeypatch,
):
    source, output, originals = _prepare_export(window, tmp_path, monkeypatch)
    first_images, first_context, first_companions = _complete_split_export(window, output)
    assert first_context == output.with_name("Concert_files")
    assert first_companions == originals

    edited_companion = first_context / "FIRST.MID"
    edited_companion.write_bytes(_large_song("Edited outside APS"))
    (first_context / "only-copy.txt").write_bytes(b"Preserve notes added after export")
    earlier_contents = _tree(first_context)
    window.pendingEdits[str(edited_companion)] = "Next export title"

    # Numbered output images correctly reject overwrite. Move the first set to
    # an archive so the repeated export reaches companion creation again.
    archive = tmp_path / "archive"
    archive.mkdir()
    archived = {}
    for image in first_images:
        archived[image.name] = image.read_bytes()
        image.rename(archive / image.name)

    _images, second_context, second_companions = _complete_split_export(window, output)

    assert second_context != first_context
    assert _tree(first_context) == earlier_contents
    assert second_companions["FIRST.MID"] != earlier_contents["FIRST.MID"]
    exported_song = mido.MidiFile(file=io.BytesIO(second_companions["FIRST.MID"]))
    assert next(message.name for track in exported_song.tracks for message in track
                if message.type == "track_name") == "Next export title"
    assert second_companions["SECOND.MID"] == originals["SECOND.MID"]
    assert {name: (source / name).read_bytes() for name in originals} == originals
    assert {name: (archive / name).read_bytes() for name in archived} == archived
