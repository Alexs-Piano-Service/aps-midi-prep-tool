"""Compatible songs retain their performance; naming edits remain separate."""

import io
from pathlib import Path

import mido
import pytest

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes, parse_eseq_bytes
from aps_midi_prep_tool_app.floppy_image import DISK_FORMAT_BY_KEY, FloppyImageSession
from aps_midi_prep_tool_app.pending_changes_dialog import PendingChangesDialog
from aps_midi_prep_tool_app.preparation_profile_dialog import PreparationProfileDialog
from aps_midi_prep_tool_app.preparation_profiles import PIANO_PROFILES, get_preparation_profile
from test_preparation_automatic import (
    _make_image, _open_image, _settle_import, _song, destination_window,  # noqa: F401
)
from test_preparation_title_spacing import _apply


def _performance(midi_type, title="Compatible performance"):
    """Expose accidental timing, routing, merge, pedal, and mute changes."""
    song = mido.MidiFile(type=midi_type, ticks_per_beat=960)
    conductor = mido.MidiTrack([
        mido.MetaMessage("time_signature", numerator=3, denominator=4),
        mido.MetaMessage("set_tempo", tempo=600001),
        mido.MetaMessage("track_name", name=title, time=19),
        mido.MetaMessage("text", text="  Keep   metadata spacing  ", time=23),
        mido.Message("sysex", data=(0x43, 0x10, 0x4C, 0, 0, 0x7E, 0)),
        mido.MetaMessage("set_tempo", tempo=500003, time=1001),
        mido.MetaMessage("end_of_track", time=333),
    ])
    piano = mido.MidiTrack([
        mido.Message("program_change", channel=0, program=3),
        # A compatible native MIDI must not receive E-SEQ startup cleanup.
        mido.Message("control_change", channel=0, control=7, value=0),
        mido.Message("note_on", channel=0, note=60, velocity=73, time=127),
        mido.Message("control_change", channel=0, control=64, value=97, time=31),
        mido.Message("note_on", channel=0, note=60, velocity=51, time=111),
        mido.Message("note_off", channel=0, note=60, velocity=34, time=49),
        mido.Message("control_change", channel=0, control=66, value=79, time=17),
        mido.Message("note_off", channel=0, note=60, velocity=22, time=331),
        mido.Message("control_change", channel=0, control=64, value=0, time=45),
        mido.Message("control_change", channel=0, control=66, value=0, time=13),
    ])
    accompaniment = mido.MidiTrack([
        mido.Message("control_change", channel=5, control=0, value=2),
        mido.Message("control_change", channel=5, control=32, value=4),
        mido.Message("program_change", channel=5, program=40),
        mido.Message("control_change", channel=5, control=67, value=91, time=59),
        mido.Message("note_on", channel=5, note=67, velocity=81, time=123),
        mido.Message("pitchwheel", channel=5, pitch=-127, time=67),
        mido.Message("control_change", channel=5, control=11, value=103, time=73),
        mido.Message("note_off", channel=5, note=67, velocity=39, time=417),
        mido.Message("control_change", channel=5, control=67, value=0, time=29),
    ])
    song.tracks = [conductor, piano, accompaniment]
    if midi_type == 0:
        song.tracks = [mido.merge_tracks(song.tracks)]
    output = io.BytesIO()
    song.save(file=output)
    return output.getvalue()


def _midi_performance(data):
    """Compare every non-title event at its absolute tick, within its track."""
    song = mido.MidiFile(file=io.BytesIO(data))
    tracks = []
    for track in song.tracks:
        tick, events = 0, []
        for message in track:
            tick += message.time
            if message.type != "track_name":
                events.append((tick, message.copy(time=0).dict()))
        tracks.append(events)
    return song.type, song.ticks_per_beat, tracks


def _load_compatible(window, source, tmp_path, image_mode):
    if image_mode:
        image = _make_image(tmp_path, [source])
        original_image = image.read_bytes()
        _open_image(window, image)
        return source.name, (image, original_image)
    window._load_regular_files([str(source)], "Compatible song")
    _settle_import()
    return str(source), None


def _save_prepared_song(window, monkeypatch, tmp_path, image_mode):
    """Exercise real save commands, then independently read the saved payload."""
    [(_row, _path, _kind, filename)] = list(window._preparation_song_rows())
    if image_mode:
        output = tmp_path / "delivered.img"
        monkeypatch.setattr(window, "_prompt_for_save_image_options", lambda **_k: (
            str(output), "img", DISK_FORMAT_BY_KEY["ibm.720"],
        ))
        monkeypatch.setattr(window, "_show_save_as_image_complete", lambda *_a, **_k: None)
        window.save_image_as()
        saved = FloppyImageSession.load(output)
        try:
            return Path(saved.extract_file(filename)).read_bytes()
        finally:
            saved.cleanup()
    output = tmp_path / "delivered"
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(output))
    window.save_as_changes()
    [saved] = list(output.rglob(filename))
    return saved.read_bytes()


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
@pytest.mark.parametrize("profile_key,midi_type", [
    (profile.key, midi_type)
    for profile in PIANO_PROFILES if profile.song_format == "midi"
    for midi_type in (profile.midi_types or (0, 1))
])
def test_already_compatible_midi_preserves_actual_events_through_saved_delivery(
    destination_window, monkeypatch, tmp_path, image_mode, profile_key, midi_type,
):
    window = destination_window("custom")
    source = tmp_path / "READY.MID"
    original = _performance(midi_type)
    source.write_bytes(original)
    path, image_source = _load_compatible(window, source, tmp_path, image_mode)

    preview = _preview_rows(window, profile_key)
    assert "Convert songs" not in [label for label, _before, _after in preview]
    _apply(window, profile_key)
    _settle_import()

    assert not window.pendingRegularConversions
    assert not window.pendingImageReplacements
    assert not window.pendingEdits
    assert not window.pendingImageTitleEdits
    assert path not in window._pending_song_paths()
    material = (window._pending_or_extracted_image_path(path) if image_mode
                else window._regular_source_material_path(path))
    assert _midi_performance(Path(material).read_bytes()) == _midi_performance(original)

    delivered = _save_prepared_song(window, monkeypatch, tmp_path, image_mode)
    assert _midi_performance(delivered) == _midi_performance(original)
    assert delivered == original
    assert source.read_bytes() == original
    if image_source:
        image, original_image = image_source
        assert image.read_bytes() == original_image


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
@pytest.mark.parametrize("profile_key", ("mark_i", "mark_ii"))
def test_already_compatible_native_eseq_preserves_timed_events_through_saved_delivery(
    destination_window, monkeypatch, tmp_path, image_mode, profile_key,
):
    window = destination_window("custom")
    source = tmp_path / "READY.FIL"
    original = convert_midi_bytes_to_eseq_bytes(
        _performance(0), cc7_policy="preserve", timing_policy="preserve", pedal_policy="preserve",
    )
    source.write_bytes(original)
    path, image_source = _load_compatible(window, source, tmp_path, image_mode)

    preview = _preview_rows(window, profile_key)
    assert "Convert songs" not in [label for label, _before, _after in preview]
    _apply(window, profile_key)
    _settle_import()

    assert not window.pendingRegularConversions
    assert not window.pendingImageReplacements
    assert not window.pendingEdits
    assert not window.pendingImageTitleEdits
    assert path not in window._pending_song_paths()
    material = (window._pending_or_extracted_image_path(path) if image_mode
                else window._regular_source_material_path(path))
    assert parse_eseq_bytes(Path(material).read_bytes()) == parse_eseq_bytes(original)

    delivered = _save_prepared_song(window, monkeypatch, tmp_path, image_mode)
    # Catalog generation may update ordering in the E-SEQ header. Compare the
    # native stream, including absolute ticks, raw channel events, tempo and
    # meter, instead of converting the saved song to another format to inspect it.
    assert parse_eseq_bytes(delivered) == parse_eseq_bytes(original)
    assert source.read_bytes() == original
    if image_source:
        image, original_image = image_source
        assert image.read_bytes() == original_image


def _preview_rows(window, profile_key):
    profile = get_preparation_profile(profile_key)
    dialog = PreparationProfileDialog(
        window.settings, profile.key, profile.default_medium, window,
        song_counts=window._preparation_song_counts(),
    )
    try:
        return [tuple(dialog.changes_table.item(row, column).text() for column in range(3))
                for row in range(dialog.changes_table.rowCount())]
    finally:
        dialog.close()


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
@pytest.mark.parametrize("profile_key", ("enspire", "e3_850", "midi_export"))
@pytest.mark.parametrize("midi_type", (0, 1))
def test_compatible_title_cleanup_is_reported_only_as_a_title_edit(
    destination_window, monkeypatch, tmp_path, image_mode, profile_key, midi_type,
):
    window = destination_window("custom")
    source = tmp_path / "TITLE.MID"
    original = _performance(midi_type, "  Moon    River  ")
    source.write_bytes(original)
    if image_mode:
        image = _make_image(tmp_path, [source])
        original_image = image.read_bytes()
        _open_image(window, image)
        path = source.name
    else:
        window._load_regular_files([str(source)], "Title cleanup review")
        _settle_import()
        path = str(source)

    preview = _preview_rows(window, profile_key)
    assert "Trim Title Spaces" in [label for label, _before, _after in preview]
    assert "Convert songs" not in [label for label, _before, _after in preview]
    _apply(window, profile_key)
    _settle_import()

    assert not window.pendingRegularConversions
    assert not window.pendingImageReplacements
    edits = window.pendingImageTitleEdits if image_mode else window.pendingEdits
    assert edits[path] == "Moon River"
    [(review_path, _before, _after, kind, detail)] = window._pending_review_rows()
    assert review_path == path
    assert "→" not in kind
    assert detail == ""
    dialog = PendingChangesDialog(window)
    try:
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 2).text() == window._pending_text("title_changed")
        assert "  Moon    River  " in dialog.details.toPlainText()
        assert "Moon River" in dialog.details.toPlainText()
    finally:
        dialog.close()
    delivered = _save_prepared_song(window, monkeypatch, tmp_path, image_mode)
    assert _midi_performance(delivered) == _midi_performance(original)
    exported = mido.MidiFile(file=io.BytesIO(delivered))
    assert [message.name for track in exported.tracks for message in track
            if message.type == "track_name"] == ["Moon River"]
    assert source.read_bytes() == original
    if image_mode:
        assert image.read_bytes() == original_image


@pytest.mark.parametrize("profile_key,midi_type", (
    ("pianodisc_128plus", 0), ("pianodisc_228cfx", 0), ("qrs_chili", 1),
))
@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
def test_compatible_filename_cleanup_is_reported_only_as_a_rename(
    destination_window, monkeypatch, tmp_path, profile_key, midi_type, image_mode,
):
    window = destination_window("custom")
    source = tmp_path / "A long performance filename.mid"
    original = _performance(midi_type)
    source.write_bytes(original)
    path, image_source = _load_compatible(window, source, tmp_path, image_mode)

    preview = _preview_rows(window, profile_key)
    assert ("Filenames", "Descriptive filenames", "DOS 8.3 · 1 to rename") in preview
    assert "Convert songs" not in [label for label, _before, _after in preview]
    _apply(window, profile_key)
    _settle_import()

    assert not window.pendingRegularConversions
    assert not window.pendingImageReplacements
    assert not window.pendingEdits
    assert not window.pendingImageTitleEdits
    renames = window.pendingImageRenames if image_mode else window.pendingRegularRenames
    assert set(renames) == {path}
    assert window._validate_image_filename(renames[path], enforce_dos83=True) is None
    [(_path, _before, _after, kind, detail)] = window._pending_review_rows()
    assert "→" not in kind
    assert detail == ""
    dialog = PendingChangesDialog(window)
    try:
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 2).text() == window._pending_text("filename_changed")
    finally:
        dialog.close()
    delivered = _save_prepared_song(window, monkeypatch, tmp_path, image_mode)
    assert _midi_performance(delivered) == _midi_performance(original)
    assert delivered == original
    assert source.read_bytes() == original
    if image_source:
        image, original_image = image_source
        assert image.read_bytes() == original_image


def test_mixed_collection_reports_only_required_conversion_and_cleanup(
    destination_window, tmp_path,
):
    window = destination_window("custom")
    sources = [tmp_path / name for name in ("TYPE1.MID", "Long compatible name.mid", "READY.MID")]
    originals = [_song(1), _song(0), _song(0)]
    for source, data in zip(sources, originals):
        source.write_bytes(data)
    window._load_regular_files([str(source) for source in sources], "Mixed preparation review")
    _settle_import()

    preview = _preview_rows(window, "pianodisc_128plus")
    assert [after for label, _before, after in preview if label == "Convert songs"] == [
        "1 · MIDI → SMF0 (staged)",
    ]
    assert [after for label, _before, after in preview if label == "Filenames"] == ["DOS 8.3 · 1 to rename"]
    _apply(window, "pianodisc_128plus")
    _settle_import()

    assert set(window.pendingRegularConversions) == {str(sources[0])}
    assert set(window.pendingRegularRenames) == {str(sources[1])}
    report = window.pendingRegularConversions[str(sources[0])]["change_report"]
    assert not report["notes_changed"]
    assert not report["pedals_changed"]
    assert not report["channel_events_changed"]
    dialog = PendingChangesDialog(window)
    try:
        assert dialog.table.rowCount() == 2
        summaries = {row[0]: dialog.change_summary(row) for row in dialog.rows}
        assert summaries[str(sources[0])] == "MIDI Type 1 → MIDI Type 0"
        assert summaries[str(sources[1])] == window._pending_text("filename_changed")
        assert str(sources[2]) not in summaries
    finally:
        dialog.close()
    assert [source.read_bytes() for source in sources] == originals
