"""Destination preparation must not infer that a sustained note is an artifact."""

import io
from pathlib import Path

import mido
import pytest
from PySide6.QtWidgets import QApplication, QLabel

from aps_midi_prep_tool_app.eseq_converter import (
    convert_midi_bytes_to_eseq_bytes, parse_eseq_bytes,
)
from aps_midi_prep_tool_app.preparation_profiles import (
    get_preparation_medium, get_preparation_profile,
)
from test_inspection_staging import _item, _open_image as _open_inspection_image, window  # noqa: F401
from test_piano_overlap import song
from test_preparation_automatic import (  # noqa: F401
    _make_image, _open_image, _settle_import, destination_window,
)


# Different source channels can legitimately sustain the same pitch while
# another part repeats it. Smart repair intentionally removes this entire
# long note after an explicit merge, including its leading and trailing parts.
SUSTAINED_WITH_REPEATS = [
    (4, 0, 120, 60, 80),
    (5, 20, 40, 60, 70),
    (6, 60, 80, 60, 90),
]


def _note_events(data, *, native=False):
    if native:
        return [(tick, raw) for tick, _order, raw in parse_eseq_bytes(data).events
                if raw[0] & 0xF0 in (0x80, 0x90)]
    midi = mido.MidiFile(file=io.BytesIO(data))
    tick = 0
    events = []
    for message in mido.merge_tracks(midi.tracks):
        tick += message.time
        if message.type in {"note_on", "note_off"}:
            events.append((tick, bytes(message.bytes())))
    return events


def _remember_smart(window):
    window.settings.setValue(window.SETTING_PIANO_OVERLAP_MODE, "smart")
    window.settings.setValue(window.SETTING_SKIP_PIANO_OVERLAP_DIALOG, True)


def _indicator(window):
    label = window.findChild(QLabel, "overlapRepairStateLabel")
    assert label is not None
    return label


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "floppy"))
@pytest.mark.parametrize("profile", ("pianodisc_128plus", "mark_ii"))
@pytest.mark.parametrize("persisted", (False, True), ids=("selected", "remembered-destination"))
def test_automatic_preparation_preserves_legitimate_sustain_despite_remembered_smart(
    destination_window, monkeypatch, tmp_path, image_mode, profile, persisted,
):
    window = destination_window(profile, persisted=persisted)
    _remember_smart(window)
    monkeypatch.setattr(
        window, "_piano_overlap_behavior",
        lambda *_a, **_k: pytest.fail("Destination preparation must not apply overlap repair"),
    )
    source = tmp_path / "SUSTAIN.MID"
    original = song(SUSTAINED_WITH_REPEATS)
    source.write_bytes(original)
    if image_mode:
        image = _make_image(tmp_path, [source])
        original_image = image.read_bytes()
        _open_image(window, image)
    else:
        window._load_regular_files([str(source)], "Sustained note regression")
        _settle_import()

    [(row, path, kind, _filename)] = list(window._preparation_song_rows())
    staged_path = (window._pending_or_extracted_image_path(path) if image_mode
                   else window._regular_source_material_path(path))
    prepared = Path(staged_path).read_bytes()
    events = _note_events(prepared, native=kind == "eseq")
    original_events = _note_events(original)
    # Preserve every attack/release, its source channel and velocity, including
    # the long note's leading/trailing portions. E-SEQ changes the timing grid.
    assert [raw for _tick, raw in events] == [raw for _tick, raw in original_events]
    assert len(events) == 6
    assert all(first[0] < second[0] for first, second in zip(events, events[1:]))
    if kind == "midi":
        assert mido.MidiFile(file=io.BytesIO(prepared)).type == 0
        assert events == original_events
    else:
        assert kind == "eseq"
    assert source.read_bytes() == original
    if image_mode:
        assert image.read_bytes() == original_image
    assert _indicator(window).isHidden()


def _load_explicit_merge(
    window, tmp_path, *, image_mode=False, native=False, notes=None, pedals=False, addition=False,
):
    original = song(SUSTAINED_WITH_REPEATS if notes is None else notes)
    if pedals:
        midi = mido.MidiFile(file=io.BytesIO(original))
        midi.tracks.append(mido.MidiTrack([
            mido.Message("control_change", channel=4, control=64, value=80),
            mido.Message("control_change", channel=4, control=64, value=0, time=120),
        ]))
        output = io.BytesIO()
        midi.save(file=output)
        original = output.getvalue()
    if native:
        original = convert_midi_bytes_to_eseq_bytes(
            original, timing_policy="preserve", pedal_policy="preserve",
        )
    source = tmp_path / ("SUSTAIN.FIL" if native else "SUSTAIN.MID")
    source.write_bytes(original)
    if image_mode:
        image, _session = _open_inspection_image(
            window, tmp_path, [] if addition else [(source, source.name)],
        )
        originals = {source: original, image: image.read_bytes()}
        key = source.name
        if addition:
            window.queue_image_additions([str(source)])
            _settle_import()
    else:
        window._load_regular_files([str(source)], "Explicit overlap repair", prepare_destination=False)
        originals = {source: original}
        key = str(source)
    return key, originals


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "floppy"))
@pytest.mark.parametrize("native", (False, True), ids=("midi", "eseq"))
@pytest.mark.parametrize("inspector", (False, True), ids=("utility", "inspector"))
def test_explicit_remembered_smart_removes_the_whole_containing_note(
    window, tmp_path, monkeypatch, image_mode, native, inspector,
):
    key, originals = _load_explicit_merge(window, tmp_path, image_mode=image_mode, native=native)
    _remember_smart(window)
    assert _indicator(window).isHidden()
    monkeypatch.setattr(
        window, "_exec_child_dialog",
        lambda *_a, **_k: pytest.fail("The remembered overlap choice must not show another dialog"),
    )
    if inspector:
        assert window._stage_inspected_midi_action(_item(window, key), "piano")["changed"]
    else:
        monkeypatch.setattr(window, "_channel_merging_options_dialog", lambda *_a: -1)
        window.show_channel_merging_utility()

    repaired = Path(_item(window, key)["path"]).read_bytes()
    events = _note_events(repaired, native=native)
    ticks = (16, 32, 48, 64) if native else (20, 40, 60, 80)
    assert [(tick, raw[:2]) for tick, raw in events] == [
        (ticks[0], b"\x90\x3c"), (ticks[1], b"\x80\x3c"),
        (ticks[2], b"\x90\x3c"), (ticks[3], b"\x80\x3c"),
    ]
    label = _indicator(window)
    assert not label.isHidden()
    assert label.text() == "Overlap repair: Smart"
    assert Path(key).name in label.toolTip()
    # Repeating a no-op cannot hide repair that is still staged.
    assert not window._stage_inspected_midi_action(_item(window, key), "piano")["changed"]
    assert label.text() == "Overlap repair: Smart"
    assert not label.isHidden()
    window.undo_last_staged_batch()
    assert label.isHidden()
    assert Path(_item(window, key)["path"]).read_bytes() == next(iter(originals.values()))
    for path, original in originals.items():
        assert path.read_bytes() == original


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "floppy"))
@pytest.mark.parametrize("behavior", ("no-overlap", "off", "cancel"))
def test_indicator_requires_applied_repair_not_a_remembered_preference(
    window, tmp_path, monkeypatch, image_mode, behavior,
):
    notes = [(4, 0, 120, 60, 80), (5, 20, 40, 64, 70)] if behavior == "no-overlap" else None
    key, originals = _load_explicit_merge(window, tmp_path, image_mode=image_mode, notes=notes)
    _remember_smart(window)
    if behavior == "off":
        window.settings.setValue(window.SETTING_PIANO_OVERLAP_MODE, "off")
    elif behavior == "cancel":
        monkeypatch.setattr(window, "_piano_overlap_behavior", lambda *_a: None)
    result = window._stage_inspected_midi_action(_item(window, key), "piano")
    assert result["changed"] is (behavior != "cancel")
    assert _indicator(window).isHidden()
    if behavior != "cancel":
        output = Path(_item(window, key)["path"]).read_bytes()
        assert len(_note_events(output)) == (4 if behavior == "no-overlap" else 6)
    for path, original in originals.items():
        assert path.read_bytes() == original


def test_optional_type0_piano_merge_reports_smart_repair(window, tmp_path, monkeypatch):
    key, _originals = _load_explicit_merge(window, tmp_path)
    _remember_smart(window)
    monkeypatch.setattr(window, "_confirm_type0_conversion", lambda *_a, **_k: True)
    window.convert_all_to_type0()
    output = Path(_item(window, key)["path"]).read_bytes()
    assert mido.MidiFile(file=io.BytesIO(output)).type == 0
    assert [tick for tick, _raw in _note_events(output)] == [20, 40, 60, 80]
    assert _indicator(window).text() == "Overlap repair: Smart"
    assert not _indicator(window).isHidden()


def test_indicator_survives_hidden_controls_and_preference_changes_then_clears_on_save(
    window, tmp_path,
):
    key, _originals = _load_explicit_merge(window, tmp_path)
    _remember_smart(window)
    window._stage_inspected_midi_action(_item(window, key), "piano")
    window.toggle_show_status(False)
    window.toggle_show_preparation_row(False)
    window.settings.setValue(window.SETTING_PIANO_OVERLAP_MODE, "off")
    window._refresh_preparation_ui()
    window.show()
    QApplication.processEvents()
    label = _indicator(window)
    assert label.isVisible()
    assert label.text() == "Overlap repair: Smart"
    assert not window.statusWidget.isVisible()
    assert not window.preparationBar.isVisible()
    window.save_pending_changes()
    assert label.isHidden()


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "floppy"))
def test_keep_attacks_indicator_discard_and_undo_follow_the_staged_repair(
    window, tmp_path, image_mode,
):
    key, originals = _load_explicit_merge(window, tmp_path, image_mode=image_mode)
    window.settings.setValue(window.SETTING_PIANO_OVERLAP_MODE, "retrigger")
    window.settings.setValue(window.SETTING_SKIP_PIANO_OVERLAP_DIALOG, True)
    assert window._stage_inspected_midi_action(_item(window, key), "piano")["changed"]
    repaired = Path(_item(window, key)["path"]).read_bytes()
    # Keep attacks retains the sustained note's leading portion; Smart drops it.
    assert [tick for tick, _raw in _note_events(repaired)] == [0, 20, 20, 40, 60, 80]
    label = _indicator(window)
    assert label.text() == "Overlap repair: Keep attacks"
    assert not label.isHidden()
    assert Path(key).name in label.toolTip()

    window.discard_staged_song_changes([key])
    assert label.isHidden()
    assert Path(_item(window, key)["path"]).read_bytes() == next(iter(originals.values()))
    # Discard is itself undoable; restoring the payload restores its disclosure.
    window.undo_last_staged_batch()
    assert not label.isHidden()
    assert label.text() == "Overlap repair: Keep attacks"
    assert Path(_item(window, key)["path"]).read_bytes() == repaired
    for path, original in originals.items():
        assert path.read_bytes() == original


@pytest.mark.parametrize("addition", (False, True), ids=("image-entry", "pending-addition"))
def test_image_repair_disclosure_survives_pedal_edit_and_destination_preparation(
    window, tmp_path, monkeypatch, addition,
):
    key, originals = _load_explicit_merge(
        window, tmp_path, image_mode=True, pedals=True, addition=addition,
    )
    _remember_smart(window)
    assert window._stage_inspected_midi_action(_item(window, key), "piano")["changed"]
    merged = Path(_item(window, key)["path"]).read_bytes()
    assert _indicator(window).text() == "Overlap repair: Smart"
    monkeypatch.setattr(window, "_pedal_compatibility_options_dialog", lambda *_a: {
        "repair_disklavier_pedal": False,
        "binary_pedal": True,
        "pedal_cleanup": False,
        "virtual_piano_roll_pedal": False,
        "soften_sustain_pedal": False,
        "_target_index": -1,
    })
    window.show_pedal_compatibility_utility()
    edited = Path(_item(window, key)["path"]).read_bytes()
    assert edited != merged
    assert _note_events(edited) == _note_events(merged)
    midi = mido.MidiFile(file=io.BytesIO(edited))
    assert [message.value for message in mido.merge_tracks(midi.tracks)
            if message.type == "control_change" and message.control == 64] == [127, 0]
    assert _indicator(window).text() == "Overlap repair: Smart"
    assert not _indicator(window).isHidden()

    for profile_key in ("mark_ii", "midi_export"):
        profile = get_preparation_profile(profile_key)
        window._apply_preparation_profile(
            profile, get_preparation_medium(profile, profile.default_medium),
        )
        _settle_import()
        assert _indicator(window).text() == "Overlap repair: Smart"
        assert not _indicator(window).isHidden()
    [(_row, current_key, _kind, _filename)] = list(window._preparation_song_rows())
    assert _note_events(Path(_item(window, current_key)["path"]).read_bytes()) == _note_events(edited)
    for path, original in originals.items():
        assert path.read_bytes() == original


@pytest.mark.parametrize("addition", (False, True), ids=("image-entry", "pending-addition"))
def test_replacing_image_song_does_not_inherit_old_songs_overlap_disclosure(
    window, tmp_path, monkeypatch, addition,
):
    key, originals = _load_explicit_merge(window, tmp_path, image_mode=True, addition=addition)
    _remember_smart(window)
    assert window._stage_inspected_midi_action(_item(window, key), "piano")["changed"]
    assert not _indicator(window).isHidden()
    incoming_dir = tmp_path / "replacement"
    incoming_dir.mkdir()
    incoming = incoming_dir / Path(key).name
    new_song = song([(2, 0, 120, 72, 95)])
    incoming.write_bytes(new_song)
    monkeypatch.setattr(window, "_prompt_drop_filename_conflict", lambda **_k: ("replace", True))
    window.queue_image_additions([str(incoming)])
    _settle_import()

    assert Path(_item(window, key)["path"]).read_bytes() == new_song
    assert _indicator(window).isHidden()
    assert incoming.read_bytes() == new_song
    for path, original in originals.items():
        assert path.read_bytes() == original
