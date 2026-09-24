"""Real-file lifecycle checks for inspection edits and stable song identities."""

import io
import os
from pathlib import Path

import mido
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import floppy_image, main_window
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes


def _song(title="Original title", midi_type=1):
    song = mido.MidiFile(type=midi_type, ticks_per_beat=480)
    song.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name=title),
        mido.MetaMessage("set_tempo", tempo=600000),
        mido.Message("sysex", data=(0x7E, 0x7F, 9, 1)),
        mido.MetaMessage("set_tempo", tempo=500000, time=300),
    ]))
    for channel, note, program in ((2, 60, 0), (5, 67, 40), (9, 36, 12)):
        song.tracks.append(mido.MidiTrack([
            mido.Message("control_change", channel=channel, control=0, value=1),
            mido.Message("control_change", channel=channel, control=32, value=2),
            mido.Message("program_change", channel=channel, program=program),
            mido.Message("note_on", channel=channel, note=note, velocity=73, time=120),
            mido.Message("control_change", channel=channel, control=64, value=77, time=100),
            mido.Message("pitchwheel", channel=channel, pitch=321, time=40),
            mido.Message("note_off", channel=channel, note=note, velocity=42, time=200),
            mido.Message("control_change", channel=channel, control=64, value=0, time=30),
        ]))
    if midi_type == 0:
        song.tracks = [mido.merge_tracks(song.tracks)]
    output = io.BytesIO()
    song.save(file=output)
    return output.getvalue()


def _midi(data):
    return mido.MidiFile(file=io.BytesIO(data))


def _performance(data):
    tick = 0
    result = []
    for message in mido.merge_tracks(_midi(data).tracks):
        tick += message.time
        if not message.is_meta or message.type == "set_tempo":
            result.append((tick, message.copy(time=0).dict()))
    return result


def _title(data):
    return next(message.name for track in _midi(data).tracks for message in track
                if message.type == "track_name")


def _assert_piano_result(before, after, *, midi_type):
    parsed = _midi(after)
    assert parsed.type == midi_type
    assert parsed.ticks_per_beat == _midi(before).ticks_per_beat
    source = _performance(before)
    prepared = _performance(after)
    notes = lambda events: [(tick, {key: value for key, value in message.items() if key != "channel"})
                            for tick, message in events if message["type"] in ("note_on", "note_off")]
    assert notes(prepared) == notes(source)
    assert any(message["type"] == "note_on" and message["note"] == 36 for _, message in prepared)
    assert {message["channel"] for _, message in prepared if "channel" in message} == {0}
    assert [(tick, message["program"]) for tick, message in prepared
            if message["type"] == "program_change"] == [(0, 0)]
    assert not any(message["type"] == "control_change" and message["control"] in (0, 32)
                   for _, message in prepared)
    for kind in ("set_tempo", "sysex"):
        assert [(tick, message) for tick, message in prepared if message["type"] == kind] == [
            (tick, message) for tick, message in source if message["type"] == kind]
    controls = lambda events: [(tick, message["control"], message["value"])
                               for tick, message in events
                               if message["type"] == "control_change" and message["control"] == 64]
    assert controls(prepared) == controls(source)


@pytest.fixture
def window(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "inspection.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_window.QMessageBox, "question", lambda *_args, **_kwargs: pytest.fail("Unexpected confirmation"))
    w = main_window.MidiTitleWindow()
    yield w
    w._clear_staging_history()
    w._cleanup_midi_scratch_dir()
    if w.image_session is not None:
        w.image_session.cleanup()
    w.deleteLater()
    application.processEvents()


def _load_folder(w, tmp_path):
    paths = (tmp_path / "A.MID", tmp_path / "Z.MID")
    for index, path in enumerate(paths):
        path.write_bytes(_song("Original title" if not index else "Other song"))
    w._load_regular_files([str(path) for path in paths], "Inspection test", prepare_destination=False)
    return paths


def _item(w, source_path):
    return next(item for item in w._inspection_items() if item["source_path"] == str(source_path))


def _edit_title(w, source, monkeypatch, title="Edited title"):
    row = _item(w, source)["row"]
    monkeypatch.setattr(w, "_prompt_for_title", lambda *_args, **_kwargs: (title, True))
    w.edit_via_dialog(row)
    assert w.pendingEdits[str(source)] == title


def test_folder_actions_use_latest_stage_preserve_title_and_undo_each_action(window, tmp_path, monkeypatch):
    w = window
    source, other = _load_folder(w, tmp_path)
    originals = (source.read_bytes(), other.read_bytes())
    _edit_title(w, source, monkeypatch)
    original_item = _item(w, source)
    depth = len(w._staged_undo_stack)

    type0_result = w._stage_inspected_midi_action(original_item, "type0")
    type0 = Path(type0_result["item"]["path"]).read_bytes()
    assert type0_result["changed"]
    assert _midi(type0).type == 0 and len(_midi(type0).tracks) == 1
    assert _performance(type0) == _performance(originals[0])
    assert _title(type0) == "Edited title"
    assert set(w.pendingRegularConversions) == {str(source)}
    assert len(w._staged_undo_stack) == depth + 1

    # The caller's path still names the original MIDI. Identity must resolve
    # the latest staged Type 0 payload and its cumulative title edit.
    piano_result = w._stage_inspected_midi_action(original_item, "piano")
    piano = Path(piano_result["item"]["path"]).read_bytes()
    _assert_piano_result(type0, piano, midi_type=0)
    assert _title(piano) == "Edited title"
    assert (source.read_bytes(), other.read_bytes()) == originals
    assert len(w._staged_undo_stack) == depth + 2
    report = w.pendingRegularConversions[str(source)]["change_report"]
    assert report["before"]["format"] == "MIDI Type 1"
    assert report["after"]["format"] == "MIDI Type 0"
    assert tuple(report["before"]["channels"]) == (3, 6, 10)
    assert tuple(report["after"]["channels"]) == (1,)
    assert report["before"]["notes"] == report["after"]["notes"] == 3

    w.undo_last_staged_batch()
    assert Path(_item(w, source)["path"]).read_bytes() == type0
    w.undo_last_staged_batch()
    assert not w.pendingRegularConversions
    assert w.pendingEdits == {str(source): "Edited title"}
    w.undo_last_staged_batch()
    assert not w.pendingEdits
    assert Path(_item(w, source)["path"]).read_bytes() == originals[0]
    assert not w._staged_undo_stack
    assert (source.read_bytes(), other.read_bytes()) == originals


def test_folder_save_commits_only_selected_staged_song_with_cumulative_title(window, tmp_path, monkeypatch):
    w = window
    source, other = _load_folder(w, tmp_path)
    original, other_original = source.read_bytes(), other.read_bytes()
    _edit_title(w, source, monkeypatch)
    item = _item(w, source)
    w._stage_inspected_midi_action(item, "type0")
    result = w._stage_inspected_midi_action(item, "piano")
    staged = Path(result["item"]["path"]).read_bytes()
    assert source.read_bytes() == original

    w.save_pending_changes()

    assert source.read_bytes() == staged
    assert other.read_bytes() == other_original
    assert (tmp_path / "backup" / source.name).read_bytes() == original
    assert not w.pendingRegularConversions and not w.pendingEdits
    assert not w._staged_undo_stack
    assert _title(source.read_bytes()) == "Edited title"
    _assert_piano_result(original, source.read_bytes(), midi_type=0)


def test_piano_action_preserves_type1_and_idempotent_actions_add_no_undo(window, tmp_path):
    w = window
    source, other = _load_folder(w, tmp_path)
    original = source.read_bytes()
    first = w._stage_inspected_midi_action(_item(w, source), "piano")
    merged = Path(first["item"]["path"]).read_bytes()
    _assert_piano_result(original, merged, midi_type=1)
    assert len(_midi(merged).tracks) == len(_midi(original).tracks)
    depth = len(w._staged_undo_stack)

    result = w._stage_inspected_midi_action(first["item"], "piano")
    assert result["changed"] is False
    assert len(w._staged_undo_stack) == depth
    assert Path(result["item"]["path"]).read_bytes() == merged

    converted = w._stage_inspected_midi_action(result["item"], "type0")
    depth = len(w._staged_undo_stack)
    unchanged = w._stage_inspected_midi_action(converted["item"], "type0")
    assert unchanged["changed"] is False
    assert len(w._staged_undo_stack) == depth
    assert source.read_bytes() == original
    assert str(other) not in w.pendingRegularConversions


def test_stale_row_after_sort_resolves_source_identity_and_not_row_or_material_path(window, tmp_path):
    w = window
    source, other = _load_folder(w, tmp_path)
    original, other_original = source.read_bytes(), other.read_bytes()
    stale = _item(w, source)
    w.table.setSortingEnabled(True)
    w.table.sortItems(3, Qt.DescendingOrder)
    assert _item(w, source)["row"] != stale["row"]
    stale["path"] = str(other)  # The material snapshot must not direct the edit.

    result = w._stage_inspected_midi_action(stale, "type0")

    assert result["item"]["source_path"] == str(source)
    assert result["item"]["row"] == _item(w, source)["row"]
    assert set(w.pendingRegularConversions) == {str(source)}
    assert _performance(Path(result["item"]["path"]).read_bytes()) == _performance(original)
    assert source.read_bytes() == original and other.read_bytes() == other_original


def test_replaced_folder_list_rejects_old_item_without_staging_new_row(window, tmp_path):
    w = window
    source, other = _load_folder(w, tmp_path)
    stale = _item(w, source)
    w._load_regular_files([str(other)], "New list", prepare_destination=False)
    before = w._staged_signature()

    with pytest.raises(ValueError, match="no longer available"):
        w._stage_inspected_midi_action(stale, "type0")

    assert w._staged_signature() == before
    assert not w._staged_undo_stack
    assert _midi(other.read_bytes()).type == 1


def _open_image(w, tmp_path, sources, *, name="music.img"):
    path = tmp_path / name
    floppy_image.create_blank_floppy_image(path, floppy_image.DISK_FORMAT_BY_KEY["ibm.720"])
    for source, image_name in sources:
        floppy_image._copy_host_file_into_image(path, source, image_name)
    session = floppy_image.FloppyImageSession.load(path)
    w._activate_disk_session(session, session.list_entries(), prepare_destination=False)
    return path, session


@pytest.mark.parametrize("addition", (False, True))
def test_image_and_pending_addition_keep_originals_and_cumulative_review_through_undo(window, tmp_path, addition):
    w = window
    source = tmp_path / "SONG.MID"
    other = tmp_path / "OTHER.MID"
    original = _song()
    source.write_bytes(original)
    other.write_bytes(_song("Other image song"))
    entries = [(other, "OTHER.MID")] + ([] if addition else [(source, "SONG.MID")])
    image, session = _open_image(w, tmp_path, entries)
    image_original = image.read_bytes()
    if addition:
        w.queue_image_additions([str(source)])
        source_key = next(iter(w.pendingImageAdditions))
    else:
        source_key = "SONG.MID"
    w.pendingImageTitleEdits[source_key] = "Edited image title"
    item = _item(w, source_key)
    original_stage = Path(item["path"]).read_bytes()
    assert item["session_id"] == id(session)
    depth = len(w._staged_undo_stack)

    first = w._stage_inspected_midi_action(item, "type0")
    type0 = Path(first["item"]["path"]).read_bytes()
    assert _midi(type0).type == 0
    assert _performance(type0) == _performance(original)
    result = w._stage_inspected_midi_action(item, "piano")
    merged = Path(result["item"]["path"]).read_bytes()
    _assert_piano_result(original, merged, midi_type=0)
    assert _title(merged) == "Edited image title"
    details = w.imageFileInfo[source_key]
    assert not details["change_report_error"]
    assert details["change_report"]["before"]["format"] == "MIDI Type 1"
    assert details["change_report"]["after"]["format"] == "MIDI Type 0"
    assert tuple(details["change_report"]["before"]["channels"]) == (3, 6, 10)
    assert tuple(details["change_report"]["after"]["channels"]) == (1,)
    review = next(row for row in w._pending_review_rows() if row[0] == source_key)
    assert "Type 1" in review[4] and "Type 0" in review[4]
    assert "Edited image title" in review[2]
    assert "OTHER.MID" not in w.pendingImageReplacements
    assert image.read_bytes() == image_original and source.read_bytes() == original
    assert len(w._staged_undo_stack) == depth + 2

    w.undo_last_staged_batch()
    assert Path(_item(w, source_key)["path"]).read_bytes() == type0
    w.undo_last_staged_batch()
    assert Path(_item(w, source_key)["path"]).read_bytes() == original_stage
    assert w.pendingImageTitleEdits[source_key] == "Edited image title"
    assert not w.pendingImageReplacements
    if addition:
        assert source_key in w.pendingImageAdditions
    assert image.read_bytes() == image_original and source.read_bytes() == original


def test_replaced_image_session_rejects_same_filename_from_stale_item(window, tmp_path):
    w = window
    source = tmp_path / "SONG.MID"
    source.write_bytes(_song())
    first_path, first_session = _open_image(w, tmp_path, [(source, "SONG.MID")], name="first.img")
    stale = _item(w, "SONG.MID")
    second_path, second_session = _open_image(w, tmp_path, [(source, "SONG.MID")], name="second.img")
    assert first_session is not second_session
    original_images = first_path.read_bytes(), second_path.read_bytes()
    before = w._staged_signature()

    with pytest.raises(ValueError, match="no longer available"):
        w._stage_inspected_midi_action(stale, "piano")

    assert w._staged_signature() == before
    assert not w._staged_undo_stack
    assert (first_path.read_bytes(), second_path.read_bytes()) == original_images


@pytest.mark.parametrize("kind,action,match", (
    ("type2", "type0", "format 2"),
    ("eseq", "type0", "require a MIDI file"),
    ("malformed", "type0", "Corrupt|truncated|malformed|missing"),
    ("malformed", "piano", "Corrupt|truncated|malformed|missing"),
))
def test_invalid_sources_raise_without_pending_changes_or_undo(window, tmp_path, kind, action, match):
    w = window
    source = tmp_path / ("SONG.FIL" if kind == "eseq" else "SONG.MID")
    original = _song(midi_type=2 if kind == "type2" else 1)
    if kind == "eseq":
        original = convert_midi_bytes_to_eseq_bytes(original)
    source.write_bytes(original)
    w._load_regular_files([str(source)], "Invalid action fixture", prepare_destination=False)
    item = _item(w, source)
    if kind == "malformed":
        # An inspector can outlive an external edit of its file.
        original = original[:25]
        source.write_bytes(original)
    before = w._staged_signature()

    with pytest.raises(ValueError, match=match):
        w._stage_inspected_midi_action(item, action)

    assert w._staged_signature() == before
    assert not w.pendingRegularConversions and not w.pendingEdits
    assert not w._staged_undo_stack
    assert source.read_bytes() == original
