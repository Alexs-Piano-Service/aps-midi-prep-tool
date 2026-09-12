"""Real-file Save As overwrite confirmation and transactional publication."""

import copy
import io
import os
from pathlib import Path

import mido
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.pending_changes import staged_batch


def _song(title, note):
    song = mido.MidiFile(type=1, ticks_per_beat=480)
    song.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name=title),
        mido.MetaMessage("set_tempo", tempo=600000),
        mido.MetaMessage("set_tempo", tempo=500000, time=300),
    ]))
    for channel, program in ((2, 40), (9, 12)):
        song.tracks.append(mido.MidiTrack([
            mido.Message("program_change", channel=channel, program=program),
            mido.Message("note_on", channel=channel, note=note, velocity=73, time=120),
            mido.Message("control_change", channel=channel, control=64, value=77, time=80),
            mido.Message("note_off", channel=channel, note=note, velocity=42, time=280),
            mido.Message("control_change", channel=channel, control=64, value=0, time=30),
        ]))
    output = io.BytesIO()
    song.save(file=output)
    return output.getvalue()


def _midi(data):
    return mido.MidiFile(file=io.BytesIO(data))


def _performance(data):
    tick, events = 0, []
    for message in mido.merge_tracks(_midi(data).tracks):
        tick += message.time
        if not message.is_meta or message.type == "set_tempo":
            events.append((tick, message.copy(time=0).dict()))
    return events


def _title(data):
    return next(message.name for track in _midi(data).tracks for message in track
                if message.type == "track_name")


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "save-as.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_window.QMessageBox, "question", lambda *_args, **_kwargs: pytest.fail("Unexpected question"))
    w = main_window.MidiTitleWindow()
    w.album_subfolder_checkbox.setChecked(False)
    w._test_errors = []
    monkeypatch.setattr(w, "_show_error_list", lambda *args, **kwargs: w._test_errors.append((args, kwargs)))
    monkeypatch.setattr(w, "_show_operation_error", lambda *args, **kwargs: w._test_errors.append((args, kwargs)))
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *args, **kwargs: w._test_errors.append((args, kwargs)))
    yield w
    w._clear_staging_history()
    w._cleanup_midi_scratch_dir()
    if w.image_session is not None:
        w.image_session.cleanup()
    w.deleteLater()
    app.processEvents()


def _load(w, tmp_path):
    folder = tmp_path / "songs"
    folder.mkdir()
    paths = (folder / "A.MID", folder / "B.MID")
    for path, title, note in zip(paths, ("Song A", "Song B"), (60, 67)):
        path.write_bytes(_song(title, note))
    w._load_regular_files([str(path) for path in paths], "Save As test", prepare_destination=False)
    w.table.setSortingEnabled(False)
    return paths


def _item(w, path):
    return next(item for item in w._inspection_items() if item["source_path"] == str(path))


def _stage_title(w, path, monkeypatch, title="Edited song A"):
    monkeypatch.setattr(w, "_prompt_for_title", lambda *_args, **_kwargs: (title, True))
    w.edit_via_dialog(_item(w, path)["row"])
    assert w.pendingEdits[str(path)] == title


def _pending(w):
    return {
        "titles": copy.deepcopy(w.pendingEdits),
        "conversions": copy.deepcopy(w.pendingRegularConversions),
        "renames": copy.deepcopy(w.pendingRegularRenames),
        "listed": copy.deepcopy(w.listedFileInfo),
        "undo": tuple(id(entry) for entry in w._staged_undo_stack),
        "rows": [(w.table.item(row, 1).text(), w._regular_row_output_filename(row))
                 for row in w._regular_file_rows()],
    }


def _tree(folder):
    return {str(path.relative_to(folder)): path.read_bytes()
            for path in folder.rglob("*") if path.is_file()}


def _choose_destination(monkeypatch, folder, *, yes, before_answer=None):
    questions = []
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_args: str(folder))

    def answer(*args, **kwargs):
        questions.append((args, kwargs))
        assert args[3] == main_window.QMessageBox.Yes | main_window.QMessageBox.No
        assert args[4] == main_window.QMessageBox.No
        if before_answer is not None:
            before_answer()
        return main_window.QMessageBox.Yes if yes else main_window.QMessageBox.No

    monkeypatch.setattr(main_window.QMessageBox, "question", answer)
    return questions


def test_same_folder_accepts_staged_type0_and_title_after_one_confirmation(window, tmp_path, monkeypatch):
    w = window
    source, other = _load(w, tmp_path)
    originals = {path: path.read_bytes() for path in (source, other)}
    _stage_title(w, source, monkeypatch)
    w._stage_inspected_midi_action(_item(w, source), "type0")
    pending = _pending(w)

    def still_unpublished():
        assert {path: path.read_bytes() for path in originals} == originals
        assert not (source.parent / "backup").exists()
        assert _pending(w) == pending

    questions = _choose_destination(monkeypatch, source.parent, yes=True, before_answer=still_unpublished)
    w.save_as_changes()

    assert len(questions) == 1 and not w._test_errors
    assert source.name in questions[0][0][2] and other.name in questions[0][0][2]
    assert _midi(source.read_bytes()).type == 0
    assert _title(source.read_bytes()) == "Edited song A"
    assert _performance(source.read_bytes()) == _performance(originals[source])
    assert _performance(other.read_bytes()) == _performance(originals[other])
    assert {path.name: (path.parent / "backup" / path.name).read_bytes() for path in originals} == {
        path.name: data for path, data in originals.items()
    }
    assert set(w.listedFileInfo) == {str(source), str(other)}
    assert not w.pendingEdits and not w.pendingRegularConversions and not w._staged_undo_stack


def test_decline_preserves_every_byte_pending_conversion_and_working_undo(window, tmp_path, monkeypatch):
    w = window
    source, _other = _load(w, tmp_path)
    _stage_title(w, source, monkeypatch)
    w._stage_inspected_midi_action(_item(w, source), "type0")
    staged = Path(w.pendingRegularConversions[str(source)]["temp_path"])
    staged_bytes = staged.read_bytes()
    original_tree, pending = _tree(source.parent), _pending(w)
    questions = _choose_destination(monkeypatch, source.parent, yes=False)

    w.save_as_changes()

    assert len(questions) == 1 and not w._test_errors
    assert _tree(source.parent) == original_tree
    assert _pending(w) == pending and staged.read_bytes() == staged_bytes
    assert not (source.parent / "backup").exists()
    w.undo_last_staged_batch()
    assert not w.pendingRegularConversions
    assert w.pendingEdits == {str(source): "Edited song A"}
    assert _tree(source.parent) == original_tree


def test_swap_output_names_reads_both_original_songs_before_publishing(window, tmp_path, monkeypatch):
    w = window
    first, second = _load(w, tmp_path)
    original_first, original_second = first.read_bytes(), second.read_bytes()

    @staged_batch
    def swap(window):
        window._stage_regular_row_pending_rename(_item(window, first)["row"], str(first), second.name)
        window._stage_regular_row_pending_rename(_item(window, second)["row"], str(second), first.name)

    swap(w)
    questions = _choose_destination(monkeypatch, first.parent, yes=True)
    w.save_as_changes()

    assert len(questions) == 1 and not w._test_errors
    assert _title(first.read_bytes()) == "Song B"
    assert _title(second.read_bytes()) == "Song A"
    assert _performance(first.read_bytes()) == _performance(original_second)
    assert _performance(second.read_bytes()) == _performance(original_first)
    assert (first.parent / "backup" / first.name).read_bytes() == original_first
    assert (second.parent / "backup" / second.name).read_bytes() == original_second
    assert not w.pendingRegularRenames and not w._staged_undo_stack


@pytest.mark.parametrize("yes", [False, True])
def test_existing_unlisted_destination_also_requires_confirmation(window, tmp_path, monkeypatch, yes):
    w = window
    source, other = _load(w, tmp_path)
    originals = {path: path.read_bytes() for path in (source, other)}
    _stage_title(w, source, monkeypatch)
    destination = tmp_path / "export"
    destination.mkdir()
    existing = destination / source.name
    existing.write_bytes(b"An existing unlisted file must not vanish silently")
    old_output = existing.read_bytes()
    pending = _pending(w)
    questions = _choose_destination(monkeypatch, destination, yes=yes)

    w.save_as_changes()

    assert len(questions) == 1 and existing.name in questions[0][0][2]
    assert not w._test_errors
    assert {path: path.read_bytes() for path in originals} == originals
    if yes:
        assert _title(existing.read_bytes()) == "Edited song A"
        # Existing backup policy keeps regular-file backups in the current
        # context's backup folder, even for an external Save As destination.
        assert (source.parent / "backup" / existing.name).read_bytes() == old_output
        assert set(w.listedFileInfo) == {str(destination / path.name) for path in originals}
    else:
        assert _tree(destination) == {existing.name: old_output}
        assert _pending(w) == pending


def test_destination_folder_symlink_alias_uses_confirmation_and_preserves_alias(window, tmp_path, monkeypatch):
    w = window
    source, other = _load(w, tmp_path)
    original = source.read_bytes()
    _stage_title(w, source, monkeypatch)
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(source.parent, target_is_directory=True)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable.")
        raise
    questions = _choose_destination(monkeypatch, alias, yes=True)

    w.save_as_changes()

    assert len(questions) == 1 and not w._test_errors
    assert alias.is_symlink()
    assert _title(source.read_bytes()) == "Edited song A"
    assert (source.parent / "backup" / source.name).read_bytes() == original
    assert {Path(path).resolve() for path in w.listedFileInfo} == {source, other}


def test_duplicate_output_names_are_blocked_before_any_question_or_write(window, tmp_path, monkeypatch):
    w = window
    first, second = _load(w, tmp_path)
    w._stage_regular_row_pending_rename(_item(w, second)["row"], str(second), first.name)
    original_tree, pending = _tree(first.parent), _pending(w)
    questions = _choose_destination(monkeypatch, first.parent, yes=True)

    w.save_as_changes()

    assert not questions and w._test_errors
    assert _tree(first.parent) == original_tree and _pending(w) == pending


def test_preparation_failure_never_prompts_or_changes_existing_files(window, tmp_path, monkeypatch):
    w = window
    source, other = _load(w, tmp_path)
    _stage_title(w, source, monkeypatch)
    original_tree, pending = _tree(source.parent), _pending(w)
    questions = _choose_destination(monkeypatch, source.parent, yes=True)
    real_write = w._write_listed_file_to_path

    def fail_second(path, title, destination, **kwargs):
        if path == str(other):
            return "Injected preparation failure"
        return real_write(path, title, destination, **kwargs)

    monkeypatch.setattr(w, "_write_listed_file_to_path", fail_second)
    w.save_as_changes()

    assert not questions and w._test_errors
    assert "Injected preparation failure" in repr(w._test_errors)
    assert _tree(source.parent) == original_tree and _pending(w) == pending


def test_commit_failure_restores_published_files_and_retains_pending_state(window, tmp_path, monkeypatch):
    from aps_midi_prep_tool_app.helpers import file_batch

    w = window
    source, other = _load(w, tmp_path)
    _stage_title(w, source, monkeypatch)
    w._stage_inspected_midi_action(_item(w, source), "type0")
    originals, pending = {path: path.read_bytes() for path in (source, other)}, _pending(w)
    questions = _choose_destination(monkeypatch, source.parent, yes=True)
    atomic_write = file_batch.atomic_write_bytes
    writes = []

    def fail_second(destination, payload, **kwargs):
        path = Path(destination)
        writes.append(path)
        if path == other and writes.count(other) == 1:
            assert source.read_bytes() != originals[source]
            raise OSError("Injected publication failure")
        return atomic_write(destination, payload, **kwargs)

    monkeypatch.setattr(file_batch, "atomic_write_bytes", fail_second)
    w.save_as_changes()

    assert len(questions) == 1 and w._test_errors
    assert "Injected publication failure" in repr(w._test_errors)
    assert writes[:2] == [source, other] and writes.count(source) >= 2
    assert {path: path.read_bytes() for path in originals} == originals
    assert _pending(w) == pending
    assert Path(w.pendingRegularConversions[str(source)]["temp_path"]).is_file()


def test_overwrite_respects_disabled_backups(window, tmp_path, monkeypatch):
    w = window
    source, _other = _load(w, tmp_path)
    _stage_title(w, source, monkeypatch)
    w.backup_checkbox.setChecked(False)
    questions = _choose_destination(monkeypatch, source.parent, yes=True)

    w.save_as_changes()

    assert len(questions) == 1 and not w._test_errors
    assert _title(source.read_bytes()) == "Edited song A"
    assert not (source.parent / "backup").exists()


@pytest.mark.parametrize("yes", [False, True])
def test_auxiliary_only_collisions_join_the_same_prepared_batch(window, tmp_path, monkeypatch, yes):
    w = window
    source, other = _load(w, tmp_path)
    _stage_title(w, source, monkeypatch)
    w.fileCreateTagSidecarsAction.setChecked(True)
    w.settings.setValue(w.SETTING_WRITE_METADATA_SUMMARY, True)
    destination = tmp_path / "export"
    destination.mkdir()
    sidecar = destination / "A.tags.txt"
    summary = destination / "metadata_summary.txt"
    sidecar.write_bytes(b"Previous tags")
    summary.write_bytes(b"Previous summary")
    before, pending = _tree(destination), _pending(w)
    questions = _choose_destination(monkeypatch, destination, yes=yes)

    w.save_as_changes()

    assert len(questions) == 1 and not w._test_errors
    assert sidecar.name in questions[0][0][2] and summary.name in questions[0][0][2]
    if not yes:
        assert _tree(destination) == before and _pending(w) == pending
        return
    assert "TIT2=Edited song A" in sidecar.read_text(encoding="utf-8-sig")
    summary_text = summary.read_text()
    assert "Edited song A" in summary_text and "Song B" in summary_text
    assert "aps_save_as" not in summary_text
    assert (source.parent / "backup" / sidecar.name).read_bytes() == before[sidecar.name]
    assert (source.parent / "backup" / summary.name).read_bytes() == before[summary.name]
    assert set(w.listedFileInfo) == {str(destination / path.name) for path in (source, other)}


def test_eseq_catalog_is_confirmed_and_published_with_edited_song(window, tmp_path, monkeypatch):
    from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
    from aps_midi_prep_tool_app.eseq_pianodir import PianodirTrackEntry, build_pianodir_bytes

    w = window
    folder = tmp_path / "eseq"
    folder.mkdir()
    songs = [folder / "A.FIL", folder / "B.FIL"]
    for path, title, note in zip(songs, ("Song A", "Song B"), (60, 67)):
        path.write_bytes(convert_midi_bytes_to_eseq_bytes(_song(title, note)))
    catalog = folder / "PIANODIR.FIL"
    catalog.write_bytes(build_pianodir_bytes([
        PianodirTrackEntry(path.name, str(path), title)
        for path, title in zip(songs, ("Song A", "Song B"))
    ], disk_title="Test album"))
    originals = {path: path.read_bytes() for path in [*songs, catalog]}
    w._load_regular_files([str(path) for path in [*songs, catalog]], "E-SEQ overwrite", prepare_destination=False)
    _stage_title(w, songs[0], monkeypatch)
    questions = _choose_destination(monkeypatch, folder, yes=True)

    w.save_as_changes()

    assert len(questions) == 1 and not w._test_errors
    assert all(path.name in questions[0][0][2] for path in originals)
    assert b"Edited song A" in catalog.read_bytes()
    assert b"Edited song A" in songs[0].read_bytes()
    assert all((folder / "backup" / path.name).read_bytes() == original
               for path, original in originals.items())
    assert w.regularHasPianodir and not w.pendingEdits and not w._staged_undo_stack
