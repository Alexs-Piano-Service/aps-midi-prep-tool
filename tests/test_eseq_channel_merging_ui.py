"""Native E-SEQ channel edits work through inspection and the batch utility."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image, main_window
from aps_midi_prep_tool_app.eseq_converter import (
    ESEQ_CONTAINER_CLAVINOVA_MDA, ESEQ_CONTAINER_DISKLAVIER,
    convert_midi_bytes_to_eseq_bytes, is_eseq_file, parse_eseq_bytes,
)
from aps_midi_prep_tool_app.eseq_pianodir import read_eseq_order_key_from_file
from test_inspection_staging import window, _song, _item, _open_image


def _eseq(container, title="Original title"):
    return convert_midi_bytes_to_eseq_bytes(
        _song(title), container_variant=container, filename_hint="SONG.FIL",
        timing_policy="preserve", pedal_policy="preserve",
    )


def _assert_merged(before, after):
    original, merged = parse_eseq_bytes(before), parse_eseq_bytes(after)
    notes = lambda song: [(tick, raw[1:]) for tick, _, raw in song.events
                           if raw[0] & 0xF0 in (0x80, 0x90)]
    assert notes(merged) == notes(original)
    assert {raw[0] & 0x0F for _, _, raw in merged.events if raw[0] & 0xF0 in (0x80, 0x90)} == {0}
    assert original.end_tick == merged.end_tick
    assert original.tempo_events == merged.tempo_events
    assert original.time_signature_events == merged.time_signature_events
    assert [(tick, raw) for tick, _, raw in merged.events if raw[:1] == b"\xF0"] == [
        (tick, raw) for tick, _, raw in original.events if raw[:1] == b"\xF0"]


@pytest.mark.parametrize("container, extension", [
    (ESEQ_CONTAINER_DISKLAVIER, "FIL"), (ESEQ_CONTAINER_CLAVINOVA_MDA, "MDA"),
])
@pytest.mark.parametrize("context", ["folder", "image", "addition"])
@pytest.mark.parametrize("entrypoint", ["inspector", "utility"])
def test_eseq_merge_stages_preserves_format_undoes_and_saves(
    window, tmp_path, monkeypatch, container, extension, context, entrypoint,
):
    w = window
    source = tmp_path / f"SONG.{extension}"
    other = tmp_path / f"OTHER.{extension}"
    original = _eseq(container)
    source.write_bytes(original)
    other.write_bytes(_eseq(container, "Other song"))
    other_original = other.read_bytes()
    original_key = read_eseq_order_key_from_file(source)
    monkeypatch.setattr(w, "_show_error_list", lambda *a, **k: pytest.fail(str(a)))
    monkeypatch.setattr(w, "_show_operation_error", lambda *a, **k: pytest.fail(str(a)))
    monkeypatch.setattr(main_window.QMessageBox, "warning", lambda *a, **k: pytest.fail(str(a)))
    monkeypatch.setattr(w, "_original_write_is_allowed", lambda: True)
    if context == "folder":
        w._load_regular_files([str(source), str(other)], "E-SEQ merge test", prepare_destination=False)
        source_key = str(source)
        image = None
    else:
        entries = [(other, other.name)] + ([(source, source.name)] if context == "image" else [])
        image, _session = _open_image(w, tmp_path, entries)
        image_original = image.read_bytes()
        if context == "addition":
            w.queue_image_additions([str(source)])
        source_key = source.name
    assert w.utilitiesMergeChannelsAction.isEnabled()
    assert source_key in dict((path, row) for row, path in w._midi_rows_for_channel_merging())
    item = _item(w, source_key)
    if container == ESEQ_CONTAINER_DISKLAVIER:
        monkeypatch.setattr(w, "_prompt_for_title", lambda *a, **k: ("Edited title", True))
        (w.edit_via_dialog if context == "folder" else w.edit_image_title)(item["row"])
    depth = len(w._staged_undo_stack)

    def merge():
        if entrypoint == "inspector":
            return w._stage_inspected_midi_action(item, "piano")
        rows = w._midi_rows_for_channel_merging()
        monkeypatch.setattr(w, "_channel_merging_options_dialog", lambda _rows:
                            next(index for index, (_, path) in enumerate(rows) if path == source_key))
        w.show_channel_merging_utility()

    merge()
    staged_path = Path(_item(w, source_key)["path"])
    staged = staged_path.read_bytes()
    assert is_eseq_file(staged_path)
    _assert_merged(original, staged)
    assert read_eseq_order_key_from_file(staged_path) == original_key
    if container == ESEQ_CONTAINER_DISKLAVIER:
        assert parse_eseq_bytes(staged).title == "Edited title"
    info = (w.pendingRegularConversions[source_key] if context == "folder" else w.imageFileInfo[source_key])
    assert info["change_report"] and not info["change_report_error"]
    if context == "folder":
        assert info["target_kind"] == "eseq"
        assert str(other) not in w.pendingRegularConversions
    else:
        assert other.name not in w.pendingImageReplacements
        assert image.read_bytes() == image_original
    assert source.read_bytes() == original and other.read_bytes() == other_original
    assert len(w._staged_undo_stack) == depth + 1
    merge()
    assert len(w._staged_undo_stack) == depth + 1
    assert Path(_item(w, source_key)["path"]).read_bytes() == staged

    w.undo_last_staged_batch()
    assert Path(_item(w, source_key)["path"]).read_bytes() == original
    assert len(w._staged_undo_stack) == depth
    merge()
    if context == "folder":
        w.save_pending_changes()
        saved = source.read_bytes()
        assert (tmp_path / "backup" / source.name).read_bytes() == original
    else:
        w.save_image_changes()
        reopened = floppy_image.FloppyImageSession.load(str(image))
        try:
            saved = Path(reopened.extract_file(source.name)).read_bytes()
            assert Path(reopened.extract_file(other.name)).read_bytes() == other_original
        finally:
            reopened.cleanup()
        assert source.read_bytes() == original
    _assert_merged(original, saved)
    if container == ESEQ_CONTAINER_DISKLAVIER:
        assert parse_eseq_bytes(saved).title == "Edited title"
    assert other.read_bytes() == other_original


@pytest.mark.parametrize("image_mode", [False, True])
def test_batch_utility_merges_mixed_midi_and_eseq_songs(window, tmp_path, monkeypatch, image_mode):
    w = window
    midi = tmp_path / "STANDARD.MID"
    eseq = tmp_path / "NATIVE.FIL"
    midi.write_bytes(_song())
    eseq.write_bytes(_eseq(ESEQ_CONTAINER_DISKLAVIER))
    originals = midi.read_bytes(), eseq.read_bytes()
    if image_mode:
        image, _session = _open_image(w, tmp_path, [(midi, midi.name), (eseq, eseq.name)])
        image_original = image.read_bytes()
        keys = {midi.name, eseq.name}
    else:
        w._load_regular_files([str(midi), str(eseq)], "Mixed merge test", prepare_destination=False)
        keys = {str(midi), str(eseq)}
    assert {path for _, path in w._midi_rows_for_channel_merging()} == keys
    monkeypatch.setattr(w, "_channel_merging_options_dialog", lambda _rows: -1)
    monkeypatch.setattr(w, "_show_error_list", lambda *a, **k: pytest.fail(str(a)))

    w.show_channel_merging_utility()

    changes = w.pendingImageReplacements if image_mode else {
        path: info["temp_path"] for path, info in w.pendingRegularConversions.items()
    }
    assert set(changes) == keys
    native_path = changes[eseq.name if image_mode else str(eseq)]
    standard_path = changes[midi.name if image_mode else str(midi)]
    _assert_merged(originals[1], Path(native_path).read_bytes())
    from test_inspection_staging import _assert_piano_result
    _assert_piano_result(originals[0], Path(standard_path).read_bytes(), midi_type=1)
    assert (midi.read_bytes(), eseq.read_bytes()) == originals
    if image_mode:
        assert image.read_bytes() == image_original


def test_truncated_eseq_is_rejected_without_staging(window, tmp_path):
    w = window
    source = tmp_path / "SONG.FIL"
    source.write_bytes(_eseq(ESEQ_CONTAINER_DISKLAVIER))
    w._load_regular_files([str(source)], "Truncated E-SEQ test", prepare_destination=False)
    item = _item(w, source)
    # An inspector may remain open while another program truncates the source.
    broken = source.read_bytes()[:0x77] + b"\xF1\x00\x95\x3C"
    source.write_bytes(broken)
    before = w._staged_signature()
    with pytest.raises(ValueError, match="incomplete E-SEQ"):
        w._stage_inspected_midi_action(item, "piano")
    assert w._staged_signature() == before
    assert not w.pendingRegularConversions
    assert not w._staged_undo_stack
    assert source.read_bytes() == broken
