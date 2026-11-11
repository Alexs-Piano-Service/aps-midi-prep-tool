"""Split exports preserve existing paths and report companion-copy failures."""

import errno
import io
from pathlib import Path

import mido
import pytest

from aps_midi_prep_tool_app import floppy_image, main_window
from aps_midi_prep_tool_app.floppy_image import DISK_FORMAT_BY_KEY, FloppyImageSession
from test_save_as_overwrite import _pending, _stage_title, _title, window


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


def _prepare_export(window, tmp_path, monkeypatch, *, output_ext="img"):
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
    output = tmp_path / "exports" / f"Concert.{output_ext}"
    output.parent.mkdir()
    monkeypatch.setattr(window, "_prompt_for_save_image_options", lambda **_kwargs: (
        str(output), output_ext, DISK_FORMAT_BY_KEY["ibm.720"],
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


_HFE_TEST_HEADER = b"TEST HFE CONTAINER\0"


def _fake_hfe_converter(source, destination, format_key, **_kwargs):
    assert format_key == "ibm.720"
    payload = Path(source).read_bytes()
    if Path(destination).suffix == ".hfe":
        payload = _HFE_TEST_HEADER + payload
    else:
        assert payload.startswith(_HFE_TEST_HEADER)
        payload = payload[len(_HFE_TEST_HEADER):]
    Path(destination).write_bytes(payload)
    return ""


def _delivered_files(images, tmp_path):
    delivered = {}
    for image in images:
        image = Path(image)
        if image.suffix == ".hfe":
            payload = image.read_bytes()
            assert payload.startswith(_HFE_TEST_HEADER)
            image = tmp_path / "readback.img"
            image.write_bytes(payload[len(_HFE_TEST_HEADER):])
        session = FloppyImageSession.load(str(image))
        try:
            for entry in session.list_entries().entries:
                if entry.path.upper().endswith(".MID"):
                    assert entry.path not in delivered
                    delivered[entry.path] = Path(session.extract_file(entry.path)).read_bytes()
        finally:
            session.cleanup()
    return delivered


def _capture_completion_dialogs(window, monkeypatch):
    warnings, completions = [], []
    monkeypatch.setattr(window, "_show_reportable_error_message", lambda *args, **kwargs:
                        warnings.append((args, kwargs)))
    monkeypatch.setattr(window, "_show_save_as_image_complete", lambda *args, **kwargs:
                        completions.append((args, kwargs)))
    return warnings, completions


def _assert_companion_warning(window, warnings, completions, images, error):
    assert not window._test_errors
    assert not completions
    assert len(warnings) == 1
    args, kwargs = warnings[0]
    assert args[:2] == (main_window.QMessageBox.Warning, "Images Created with Companion Warning")
    message = args[2]
    assert all(str(path) in message for path in images)
    assert str(error) in message
    assert "Use the completed images listed above" in message
    assert "Save As" in message and "folder with enough free space" in message
    assert "choose a different output name or folder" in message
    assert kwargs.get("plain_text") is True
    # A suppressed success dialog must not suppress this actionable warning.
    assert window.settings.value(window.SETTING_HIDE_SAVE_AS_IMAGE_COMPLETE_DIALOG, type=bool)
    return message


@pytest.mark.parametrize("output_ext", ["img", "hfe"])
@pytest.mark.parametrize("cleanup_fails", [False, True], ids=["removed", "retained"])
def test_second_companion_copy_enospc_reports_images_preserves_state_and_allows_retry(
    window, tmp_path, monkeypatch, output_ext, cleanup_fails,
):
    source, output, originals = _prepare_export(
        window, tmp_path, monkeypatch, output_ext=output_ext,
    )
    if output_ext == "hfe":
        monkeypatch.setattr(floppy_image, "_gw_convert", _fake_hfe_converter)
    _stage_title(window, source / "FIRST.MID", monkeypatch, "Prepared export title")
    pending = _pending(window)
    warnings, completions = _capture_completion_dialogs(window, monkeypatch)

    occupied = output.with_name("Concert_files")
    occupied.mkdir()
    (occupied / "FIRST.MID").write_bytes(b"Keep an earlier companion file")
    occupied_file = output.with_name("Concert_files_2")
    occupied_file.write_bytes(b"Keep a file at a suffixed companion path")
    target = tmp_path / "unrelated songs"
    target.mkdir()
    (target / "FIRST.MID").write_bytes(b"Keep the symlink target")
    linked = output.with_name("Concert_files_3")
    dangling = output.with_name("Concert_files_4")
    missing_target = tmp_path / "missing songs"
    link_snapshots, fallback_trees = {}, {}
    for path, link_target in ((linked, target), (dangling, missing_target)):
        try:
            path.symlink_to(link_target, target_is_directory=True)
        except (OSError, NotImplementedError):
            # Keep the ENOSPC coverage when Windows lacks symlink privileges.
            path.mkdir()
            (path / "keep.txt").write_bytes(b"Keep this occupied companion folder")
            fallback_trees[path] = _tree(path)
        else:
            link_snapshots[path] = path.readlink()
    earlier_files, target_files = _tree(occupied), _tree(target)
    new_context = output.with_name("Concert_files_5")
    images = tuple(output.with_name(f"Concert_{number:02d}.{output_ext}") for number in (1, 2))
    real_copy, real_rmtree = main_window.shutil.copy2, main_window.shutil.rmtree
    copy_error = OSError(errno.ENOSPC, "No space left on destination")
    cleanup_error = OSError(errno.EIO, "Companion cleanup failed")
    prepared, copies, cleanup_attempts = {}, [], []
    fail = True

    def copy_with_second_failure(source_path, destination, *args, **kwargs):
        destination = Path(destination)
        if fail and destination.parent == new_context:
            assert all(image.is_file() for image in images)
            prepared[destination.name] = Path(source_path).read_bytes()
            copies.append(destination)
            if len(copies) == 2:
                destination.write_bytes(prepared[destination.name][:137])
                raise copy_error
        return real_copy(source_path, destination, *args, **kwargs)

    def cleanup(directory, *args, **kwargs):
        if Path(directory) == new_context:
            cleanup_attempts.append(Path(directory))
            if cleanup_fails:
                raise cleanup_error
        return real_rmtree(directory, *args, **kwargs)

    monkeypatch.setattr(main_window.shutil, "copy2", copy_with_second_failure)
    monkeypatch.setattr(main_window.shutil, "rmtree", cleanup)

    result = window.save_as_image()

    assert result == tuple(map(str, images))
    assert len(copies) == 2 and cleanup_attempts == [new_context]
    assert _pending(window) == pending and not window.is_image_mode()
    assert window.settings.value(window.SETTING_SAVE_AS_LOCATION) == str(output.parent)
    message = _assert_companion_warning(window, warnings, completions, images, copy_error)
    assert str(new_context) in message
    if cleanup_fails:
        assert new_context.is_dir()
        assert set(new_context.iterdir()) == set(copies)
        assert copies[0].read_bytes() == prepared[copies[0].name]
        assert copies[1].read_bytes() == prepared[copies[1].name][:137]
        assert str(cleanup_error) in message
        assert copy_error.export_context_retained_directory == str(new_context)
        assert copy_error.export_context_cleanup_error is cleanup_error
    else:
        assert not new_context.exists()
        assert copy_error.export_context_removed_directory == str(new_context)
    assert _delivered_files(images, tmp_path) == prepared
    assert _title(prepared["FIRST.MID"]) == "Prepared export title"
    assert prepared["SECOND.MID"] == originals["SECOND.MID"]
    image_bytes = {path: path.read_bytes() for path in images}

    # Follow the warning's retry advice without relocating the delivered images.
    fail = False
    retry = output.with_name(f"ConcertRetry.{output_ext}")
    monkeypatch.setattr(window, "_prompt_for_save_image_options", lambda **_kwargs: (
        str(retry), output_ext, DISK_FORMAT_BY_KEY["ibm.720"],
    ))
    retry_images = window.save_as_image()

    assert retry_images == tuple(str(retry.with_name(f"ConcertRetry_{number:02d}.{output_ext}"))
                                 for number in (1, 2))
    assert not window._test_errors and len(warnings) == 1 and len(completions) == 1
    assert _delivered_files(retry_images, tmp_path) == prepared
    assert not window.pendingEdits
    assert {Path(path).parent for path in window.listedFileInfo} == {
        retry.with_name("ConcertRetry_files"),
    }
    assert {path: path.read_bytes() for path in images} == image_bytes
    assert _tree(occupied) == earlier_files and _tree(target) == target_files
    assert occupied_file.read_bytes() == b"Keep a file at a suffixed companion path"
    for path, original_link in link_snapshots.items():
        assert path.is_symlink() and path.readlink() == original_link
    for path, original_tree in fallback_trees.items():
        assert _tree(path) == original_tree
    if linked in link_snapshots:
        assert linked.samefile(target)
    assert not missing_target.exists()
    assert {name: (source / name).read_bytes() for name in originals} == originals


def test_companion_mkdir_failure_reports_completed_images_without_cleanup(
    window, tmp_path, monkeypatch,
):
    source, output, originals = _prepare_export(window, tmp_path, monkeypatch)
    pending = _pending(window)
    warnings, completions = _capture_completion_dialogs(window, monkeypatch)
    context = output.with_name("Concert_files")
    images = tuple(output.with_name(f"Concert_{number:02d}.img") for number in (1, 2))
    error = OSError(errno.ENOSPC, "No space for companion directory", str(context))
    real_mkdir, real_rmtree = main_window.os.mkdir, main_window.shutil.rmtree
    attempted = []

    def mkdir(directory, *args, **kwargs):
        if Path(directory) == context:
            assert all(image.is_file() for image in images)
            attempted.append(Path(directory))
            raise error
        return real_mkdir(directory, *args, **kwargs)

    def cleanup(directory, *args, **kwargs):
        assert Path(directory) != context
        return real_rmtree(directory, *args, **kwargs)

    monkeypatch.setattr(main_window.os, "mkdir", mkdir)
    monkeypatch.setattr(main_window.shutil, "rmtree", cleanup)

    result = window.save_as_image()

    assert result == tuple(map(str, images))
    assert attempted == [context] and not context.exists()
    _assert_companion_warning(window, warnings, completions, images, error)
    assert _pending(window) == pending and not window.is_image_mode()
    assert _delivered_files(images, tmp_path) == originals
    assert {name: (source / name).read_bytes() for name in originals} == originals


def test_companion_failure_preserves_replacement_directory_at_claimed_path(
    window, tmp_path, monkeypatch,
):
    source, output, originals = _prepare_export(window, tmp_path, monkeypatch)
    pending = _pending(window)
    warnings, completions = _capture_completion_dialogs(window, monkeypatch)
    context = output.with_name("Concert_files")
    displaced = tmp_path / "displaced companion folder"
    images = tuple(output.with_name(f"Concert_{number:02d}.img") for number in (1, 2))
    error = OSError(errno.ENOSPC, "No space left on destination")
    real_copy, real_rmtree = main_window.shutil.copy2, main_window.shutil.rmtree
    copies = []

    def copy_with_replacement(source_path, destination, *args, **kwargs):
        destination = Path(destination)
        if destination.parent == context:
            copies.append(destination)
            if len(copies) == 2:
                destination.write_bytes(Path(source_path).read_bytes()[:137])
                # Another actor moved our folder and populated its former path.
                context.rename(displaced)
                context.mkdir()
                (context / "only-copy.txt").write_bytes(b"Preserve this replacement")
                raise error
        return real_copy(source_path, destination, *args, **kwargs)

    def cleanup(directory, *args, **kwargs):
        assert Path(directory) not in (context, displaced)
        return real_rmtree(directory, *args, **kwargs)

    monkeypatch.setattr(main_window.shutil, "copy2", copy_with_replacement)
    monkeypatch.setattr(main_window.shutil, "rmtree", cleanup)

    result = window.save_as_image()

    assert result == tuple(map(str, images))
    assert len(copies) == 2
    message = _assert_companion_warning(window, warnings, completions, images, error)
    assert str(context) in message and "folder changed during export" in message
    assert error.export_context_retained_directory == str(context)
    assert _tree(context) == {"only-copy.txt": b"Preserve this replacement"}
    assert _tree(displaced) == {
        copies[0].name: originals[copies[0].name],
        copies[1].name: originals[copies[1].name][:137],
    }
    assert _pending(window) == pending and not window.is_image_mode()
    assert _delivered_files(images, tmp_path) == originals
    assert {name: (source / name).read_bytes() for name in originals} == originals
