"""A destination replaces automatic preparation while keeping deliberate work."""

import io
from pathlib import Path

import mido
import pytest

from test_preparation_automatic import (  # noqa: F401
    _make_image, _open_image, _settle_import, _song, destination_window,
)
from aps_midi_prep_tool_app.preparation_profiles import (
    get_preparation_medium, get_preparation_profile,
)


def _apply(window, key):
    profile = get_preparation_profile(key)
    window._apply_preparation_profile(
        profile, get_preparation_medium(profile, profile.default_medium),
    )
    _settle_import()


def _load(factory, directory, image_mode, *, count=1):
    directory.mkdir()
    originals = {}
    for index in range(count):
        source = directory / f"SONG{index + 1}.MID"
        originals[source] = _song(note=60 + index)
        source.write_bytes(originals[source])
    window = factory("custom")
    if image_mode:
        image_path = _make_image(directory, originals)
        originals[image_path] = image_path.read_bytes()
        _open_image(window, image_path)
    else:
        window._load_regular_files([str(path) for path in originals], "Imported songs")
        _settle_import()
    return window, originals


def _rows(window):
    return list(window._preparation_song_rows())


def _materials(window):
    return {
        filename: Path(window._pending_or_extracted_image_path(source) if window.is_image_mode()
                       else window._regular_source_material_path(source)).read_bytes()
        for _row, source, _kind, filename in _rows(window)
    }


def _export(window, directory):
    """Exercise the delivery writers, including title edits staged separately."""
    directory.mkdir()
    exported = {}
    for row, source, kind, filename in _rows(window):
        assert kind == "midi"
        destination = directory / filename
        if window.is_image_mode():
            window._write_image_row_to_destination(source, str(destination))
        else:
            error = window._write_listed_file_to_path(
                source, window._row_raw_title(row), str(destination),
            )
            assert error is None
        exported[filename] = destination.read_bytes()
    return exported


def _structure(data, *, ignore_titles=False):
    midi = mido.MidiFile(file=io.BytesIO(data))
    return (
        midi.type,
        midi.ticks_per_beat,
        [[message.dict() for message in track
          if not (ignore_titles and message.type == "track_name")]
         for track in midi.tracks],
    )


def _assert_sources_unchanged(originals):
    for source, original in originals.items():
        assert source.read_bytes() == original


def _edit_first_title(window, monkeypatch, title):
    row = _rows(window)[0][0]
    monkeypatch.setattr(window, "_prompt_for_title", lambda *_a, **_k: (title, True))
    (window.edit_image_title if window.is_image_mode() else window.edit_via_dialog)(row)
    assert window._row_raw_title(_rows(window)[0][0]) == title


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
@pytest.mark.parametrize("first_profile", ("pianodisc_128plus", "mark_ii"))
def test_preparing_directly_for_b_matches_a_then_b_without_conversion_round_trip(
    destination_window, tmp_path, image_mode, first_profile,
):
    direct, direct_originals = _load(destination_window, tmp_path / "direct", image_mode)
    switched, switched_originals = _load(destination_window, tmp_path / "switched", image_mode)

    _apply(direct, "midi_export")
    _apply(switched, first_profile)
    row, source, kind, _filename = _rows(switched)[0]
    if first_profile == "mark_ii":
        assert kind == "eseq"
    else:
        material = (switched._pending_or_extracted_image_path(source) if image_mode
                    else switched._regular_source_material_path(source))
        assert mido.MidiFile(material).type == 0

    _apply(switched, "midi_export")

    direct_files = _export(direct, tmp_path / "direct-export")
    switched_files = _export(switched, tmp_path / "switched-export")
    assert switched_files == direct_files
    assert _structure(next(iter(switched_files.values()))) == _structure(_song())
    _assert_sources_unchanged(direct_originals)
    _assert_sources_unchanged(switched_originals)


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
@pytest.mark.parametrize("edit_between_profiles", (False, True), ids=("edit-before", "edit-between"))
def test_switching_destination_preserves_explicit_title_without_preserving_lossy_conversion(
    destination_window, monkeypatch, tmp_path, image_mode, edit_between_profiles,
):
    window, originals = _load(destination_window, tmp_path / "music", image_mode)
    title = "My deliberate title"
    if not edit_between_profiles:
        _edit_first_title(window, monkeypatch, title)
    _apply(window, "mark_ii")
    assert _rows(window)[0][2] == "eseq"
    if edit_between_profiles:
        _edit_first_title(window, monkeypatch, title)

    _apply(window, "midi_export")

    assert window._row_raw_title(_rows(window)[0][0]) == title
    exported = next(iter(_export(window, tmp_path / "export").values()))
    midi = mido.MidiFile(file=io.BytesIO(exported))
    assert [message.name for track in midi.tracks for message in track
            if message.type == "track_name"] == [title]
    assert _structure(exported, ignore_titles=True) == _structure(_song(), ignore_titles=True)
    _assert_sources_unchanged(originals)


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
@pytest.mark.parametrize("custom_action", ("dialog", "disengage"))
def test_custom_keeps_automatic_preparation_and_deliberate_title(
    destination_window, monkeypatch, tmp_path, image_mode, custom_action,
):
    window, originals = _load(destination_window, tmp_path / "music", image_mode)
    _apply(window, "mark_ii")
    _edit_first_title(window, monkeypatch, "Keep this title")

    before = _materials(window)
    generate_catalog = window.pendingGeneratePianodir
    if custom_action == "dialog":
        _apply(window, "custom")
    else:
        window.disengage_preparation_profile()
        _settle_import()

    assert window._row_raw_title(_rows(window)[0][0]) == "Keep this title"
    assert _rows(window)[0][2] == "eseq"
    assert _materials(window) == before
    assert window.pendingGeneratePianodir == generate_catalog
    _assert_sources_unchanged(originals)


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
def test_destination_change_preserves_explicit_playback_order(
    destination_window, tmp_path, image_mode,
):
    window, originals = _load(destination_window, tmp_path / "music", image_mode, count=3)
    _apply(window, "mark_ii")
    assert window._supports_eseq_reordering()
    initial = _rows(window)
    first_path = initial[0][1]
    window.table.setCurrentCell(initial[0][0], 4)
    window.move_selected_eseq_row(1)
    ordered_paths = [source for _row, source, _kind, _filename in _rows(window)]
    assert ordered_paths == [initial[1][1], first_path, initial[2][1]]

    _apply(window, "midi_export")

    assert [source for _row, source, _kind, _filename in _rows(window)] == ordered_paths
    exported = _export(window, tmp_path / "export")
    assert [mido.MidiFile(file=io.BytesIO(data)).type for data in exported.values()] == [1, 1, 1]
    _assert_sources_unchanged(originals)


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
def test_reset_keeps_a_type0_conversion_explicitly_requested_before_preparation(
    destination_window, tmp_path, image_mode,
):
    window, originals = _load(destination_window, tmp_path / "music", image_mode)
    inspected = window._inspection_items()[0]
    result = window._stage_inspected_midi_action(inspected, "type0")
    assert result["changed"]
    deliberate = Path(result["item"]["path"]).read_bytes()
    assert mido.MidiFile(file=io.BytesIO(deliberate)).type == 0
    _apply(window, "mark_ii")

    window.reset_preparation()

    exported = next(iter(_export(window, tmp_path / "export").values()))
    assert _structure(exported) == _structure(deliberate)
    _assert_sources_unchanged(originals)


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
def test_undoing_a_destination_change_restores_its_preparation_layer(
    destination_window, tmp_path, image_mode,
):
    window, originals = _load(destination_window, tmp_path / "music", image_mode)
    _apply(window, "mark_ii")
    first_preparation = _materials(window)
    assert _rows(window)[0][2] == "eseq"
    _apply(window, "midi_export")
    assert next(iter(_export(window, tmp_path / "before-undo").values())) == _song()

    window.undo_last_staged_batch()

    assert window._preparation_profile().key == "mark_ii"
    assert _rows(window)[0][2] == "eseq"
    assert _materials(window) == first_preparation

    _apply(window, "midi_export")

    assert next(iter(_export(window, tmp_path / "after-undo").values())) == _song()
    _assert_sources_unchanged(originals)


@pytest.mark.parametrize("image_mode", (False, True), ids=("regular-drop", "image-addition"))
@pytest.mark.parametrize("first_profile", ("pianodisc_128plus", "mark_ii"))
def test_files_added_after_preparation_keep_their_own_unprepared_sources(
    destination_window, tmp_path, image_mode, first_profile,
):
    window, originals = _load(destination_window, tmp_path / "music", image_mode)
    _apply(window, first_profile)
    incoming = tmp_path / "ADDED.MID"
    incoming_original = _song(note=72)
    incoming.write_bytes(incoming_original)
    if image_mode:
        window.queue_image_additions([str(incoming)])
    else:
        window.prepare_regular_file_drop([str(incoming)])
        result = window.add_regular_file_from_drop(str(incoming))
        window.finish_regular_file_drop([result])
    _settle_import()
    assert len(_rows(window)) == 2
    if first_profile == "mark_ii":
        assert {kind for _row, _source, kind, _filename in _rows(window)} == {"eseq"}
    else:
        assert {mido.MidiFile(file=io.BytesIO(data)).type
                for data in _materials(window).values()} == {0}

    _apply(window, "midi_export")

    assert _export(window, tmp_path / "export") == {
        "SONG1.MID": _song(), "ADDED.MID": incoming_original,
    }
    assert incoming.read_bytes() == incoming_original
    _assert_sources_unchanged(originals)


def test_partial_regular_save_commits_only_successful_preparation(
    destination_window, monkeypatch, tmp_path,
):
    window, originals = _load(destination_window, tmp_path / "music", False, count=2)
    _apply(window, "pianodisc_128plus")
    first, second = tuple(originals)
    assert set(window.pendingRegularConversions) == {str(first), str(second)}
    window.backup_checkbox.setChecked(False)
    write_file = window._write_listed_file_to_path
    errors = []

    def fail_second(source, *args, **kwargs):
        if source == str(second):
            return "SONG2.MID: simulated write failure"
        return write_file(source, *args, **kwargs)

    monkeypatch.setattr(window, "_write_listed_file_to_path", fail_second)
    monkeypatch.setattr(window, "_show_error_list", lambda *args, **_k: errors.append(args))

    window.save_pending_changes()

    assert errors and errors[0][0] == "Save Failed"
    assert set(window.pendingRegularConversions) == {str(second)}
    committed = first.read_bytes()
    assert mido.MidiFile(file=io.BytesIO(committed)).type == 0
    assert second.read_bytes() == originals[second]
    monkeypatch.setattr(window, "_write_listed_file_to_path", write_file)

    window.reset_preparation()

    assert _export(window, tmp_path / "export") == {
        first.name: committed, second.name: originals[second],
    }
    assert not window.pendingRegularConversions
    assert first.read_bytes() == committed
    assert second.read_bytes() == originals[second]


@pytest.mark.parametrize("context", ("folder", "image", "image-addition"))
@pytest.mark.parametrize("edit_between_profiles", (False, True), ids=("rename-before", "rename-between"))
@pytest.mark.parametrize("last_profile", ("midi_export", "reset"))
def test_destination_change_preserves_manual_filename_and_original_material(
    destination_window, monkeypatch, tmp_path, context, edit_between_profiles, last_profile,
):
    window, originals = _load(destination_window, tmp_path / "music", context != "folder")
    expected = {"MANUAL.MID": _song()}
    if context == "image-addition":
        incoming = tmp_path / "ADDED.MID"
        originals[incoming] = _song(note=72)
        incoming.write_bytes(originals[incoming])
        window.queue_image_additions([str(incoming)])
        _settle_import()
        expected = {"SONG1.MID": _song(), "MANUAL.MID": originals[incoming]}

    def rename():
        # Image additions change their hidden path when converted or renamed;
        # select the latest row by its current visible filename each time.
        row = next(row for row, _source, _kind, filename in _rows(window)
                   if context != "image-addition" or filename.startswith("ADDED"))
        suffix = ".FIL" if edit_between_profiles else ".MID"
        monkeypatch.setattr(
            window, "_prompt_for_image_filename", lambda *_a, **_k: ("MANUAL" + suffix, True),
        )
        (window.edit_image_filename if window.is_image_mode() else window.edit_regular_filename)(row)
        assert "MANUAL" + suffix in {filename for _row, _source, _kind, filename in _rows(window)}

    if not edit_between_profiles:
        rename()
    _apply(window, "mark_ii")
    assert {kind for _row, _source, kind, _filename in _rows(window)} == {"eseq"}
    if edit_between_profiles:
        rename()
    assert {filename for _row, _source, _kind, filename in _rows(window)} == {
        str(Path(filename).with_suffix(".FIL")) for filename in expected
    }

    if last_profile == "reset":
        window.reset_preparation()
    else:
        _apply(window, last_profile)

    assert _export(window, tmp_path / "export") == expected
    _assert_sources_unchanged(originals)


def test_discarding_prepared_image_song_keeps_its_identity_and_original_format(
    destination_window, tmp_path,
):
    window, originals = _load(destination_window, tmp_path / "music", True, count=2)
    _apply(window, "mark_ii")
    source = _rows(window)[0][1]

    window.discard_staged_song_changes([source])

    assert window.imageFileInfo[source]["title_mode"] == "midi"
    _apply(window, "midi_export")

    assert source not in window.pendingImageDeletes
    assert _export(window, tmp_path / "export") == {
        "SONG1.MID": _song(note=60), "SONG2.MID": _song(note=61),
    }
    _assert_sources_unchanged(originals)


def test_destination_switch_restores_loaded_eseq_catalog_metadata(destination_window, tmp_path):
    from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes
    from aps_midi_prep_tool_app.eseq_pianodir import (
        PianodirMetadata, PianodirTrackEntry, build_pianodir_bytes,
    )

    song = tmp_path / "SONG1.FIL"
    song.write_bytes(convert_midi_bytes_to_eseq_bytes(_song(), filename_hint=song.name))
    catalog = tmp_path / "PIANODIR.FIL"
    catalog.write_bytes(build_pianodir_bytes(
        [PianodirTrackEntry(song.name, str(song), "Automatic preparation")],
        PianodirMetadata(disk_title="Original Album"),
    ))
    window = destination_window("custom")
    window._load_regular_files([str(song), str(catalog)], "Imported songs")
    _settle_import()
    expected = window.loadedRegularPianodirMetadata

    _apply(window, "midi_export")
    window.reset_preparation()

    assert window.loadedRegularPianodirMetadata == expected
    assert window.imagePianodirTitleEdit.text() == "Original Album"
    assert not window._regular_pianodir_metadata_changed()


@pytest.mark.parametrize("context", ("folder", "image", "image-addition"))
@pytest.mark.parametrize("undo_edit", (False, True), ids=("keep-edit", "undo-edit"))
def test_manual_pedal_edit_precedes_automatic_type0_preparation(
    destination_window, tmp_path, context, undo_edit,
):
    window, originals = _load(destination_window, tmp_path / "music", context != "folder")
    filename, note = "SONG1.MID", 60
    if context == "image-addition":
        incoming = tmp_path / "ADDED.MID"
        filename, note = incoming.name, 72
        originals[incoming] = _song(note=note)
        incoming.write_bytes(originals[incoming])
        window.queue_image_additions([str(incoming)])
        _settle_import()
    _apply(window, "pianodisc_128plus")
    selected = [(row, source) for row, source, _kind, name in _rows(window) if name == filename]
    history_count = len(window._staged_undo_stack)

    utility = (window._apply_pedal_compatibility_to_image_rows if window.is_image_mode()
               else window._apply_pedal_compatibility_to_regular_rows)
    utility(selected, {"binary_pedal": True})

    assert len(window._staged_undo_stack) == history_count + 1
    assert {mido.MidiFile(file=io.BytesIO(data)).type for data in _materials(window).values()} == {0}
    if undo_edit:
        window.undo_last_staged_batch()
    window.reset_preparation()
    output = _export(window, tmp_path / "export")[filename]
    expected = mido.MidiFile(file=io.BytesIO(_song(note=note)))
    if not undo_edit:
        for track in expected.tracks:
            for message in track:
                if message.type == "control_change" and message.control == 66 and message.value == 98:
                    message.value = 127
    serialized = io.BytesIO()
    expected.save(file=serialized)
    assert _structure(output) == _structure(serialized.getvalue())
    _assert_sources_unchanged(originals)


def test_noop_manual_utility_keeps_preparation_and_undo_history(destination_window, tmp_path):
    window, _originals = _load(destination_window, tmp_path / "music", False)
    _apply(window, "pianodisc_128plus")
    before = _materials(window)
    history_count = len(window._staged_undo_stack)
    sorting_enabled = window.table.isSortingEnabled()

    window._apply_pedal_compatibility_to_regular_rows(
        [(row, source) for row, source, _kind, _name in _rows(window)], {},
    )

    assert len(window._staged_undo_stack) == history_count
    assert _materials(window) == before
    assert window.table.isSortingEnabled() == sorting_enabled
    window.reset_preparation()
    assert _export(window, tmp_path / "export") == {"SONG1.MID": _song()}


def test_undo_all_after_partial_save_does_not_revive_discarded_filename(
    destination_window, monkeypatch, tmp_path,
):
    window, originals = _load(destination_window, tmp_path / "music", False, count=2)
    monkeypatch.setattr(window, "_prompt_for_image_filename", lambda *_a, **_k: ("MYNAME.MID", True))
    window.edit_regular_filename(_rows(window)[1][0])
    _apply(window, "pianodisc_128plus")
    second = tuple(originals)[1]
    writer = window._write_listed_file_to_path
    monkeypatch.setattr(window, "_write_listed_file_to_path", lambda source, *a, **k:
                        "simulated failure" if source == str(second) else writer(source, *a, **k))
    monkeypatch.setattr(window, "_show_error_list", lambda *_a, **_k: None)

    window.save_pending_changes()
    window.undo_all_staged_changes()
    assert not window._pending_song_paths()
    window.reset_preparation()

    assert not window._pending_song_paths()
    assert not window.pendingRegularRenames
    assert second.read_bytes() == originals[second]


def test_image_save_establishes_a_committed_preparation_baseline(destination_window, tmp_path):
    from aps_midi_prep_tool_app.floppy_image import FloppyImageSession

    window, originals = _load(destination_window, tmp_path / "music", True)
    image_path = next(path for path in originals if path.suffix == ".img")
    _apply(window, "pianodisc_128plus")
    prepared = _materials(window)
    assert mido.MidiFile(file=io.BytesIO(prepared["SONG1.MID"])).type == 0
    window.toggle_original_write_protection(False)

    window.save_image_changes()

    assert window._preparation_layer is None
    assert not window._staged_undo_stack
    assert not window.pendingImageReplacements
    saved = FloppyImageSession.load(image_path)
    try:
        assert Path(saved.extract_file("SONG1.MID")).read_bytes() == prepared["SONG1.MID"]
    finally:
        saved.cleanup()

    window.reset_preparation()

    assert _export(window, tmp_path / "export") == prepared
    assert image_path.read_bytes() != originals[image_path]
    for path, original in originals.items():
        if path != image_path:
            assert path.read_bytes() == original


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
def test_manual_utility_handles_mixed_original_formats(destination_window, tmp_path, image_mode):
    from aps_midi_prep_tool_app.eseq_converter import convert_midi_bytes_to_eseq_bytes

    first = tmp_path / "FIRST.MID"
    first.write_bytes(_song())
    second = tmp_path / "SECOND.FIL"
    second.write_bytes(convert_midi_bytes_to_eseq_bytes(_song(note=72), filename_hint=second.name))
    originals = {path: path.read_bytes() for path in (first, second)}
    window = destination_window("custom")
    if image_mode:
        _open_image(window, _make_image(tmp_path, originals))
    else:
        window._load_regular_files([str(path) for path in originals], "Mixed songs")
        _settle_import()
    _apply(window, "pianodisc_128plus")
    history_count = len(window._staged_undo_stack)
    utility = (window._apply_pedal_compatibility_to_image_rows if image_mode
               else window._apply_pedal_compatibility_to_regular_rows)

    utility([(row, source) for row, source, _kind, _name in _rows(window)], {"binary_pedal": True})

    assert len(window._staged_undo_stack) == history_count + 1
    window.reset_preparation()
    output = _export(window, tmp_path / "export")
    assert len(output) == 2
    assert mido.MidiFile(file=io.BytesIO(output[first.name])).type == 1
    for data in output.values():
        values = [message.value for track in mido.MidiFile(file=io.BytesIO(data)).tracks
                  for message in track if message.type == "control_change" and message.control == 66]
        assert values == [127, 0]
    _assert_sources_unchanged(originals)


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
def test_manual_utility_follows_selected_song_after_row_reordering(destination_window, tmp_path, image_mode):
    window, _originals = _load(destination_window, tmp_path / "music", image_mode, count=2)
    _apply(window, "pianodisc_128plus")
    first, second = _rows(window)
    selected = [(first[0], first[1])]
    window.table.setSortingEnabled(False)
    window._move_table_row(first[0], second[0])
    assert _rows(window)[0][1] != selected[0][1]
    utility = (window._apply_pedal_compatibility_to_image_rows if image_mode
               else window._apply_pedal_compatibility_to_regular_rows)

    utility(selected, {"binary_pedal": True})
    window.reset_preparation()

    output = _export(window, tmp_path / "export")
    for filename, expected in ((first[3], 127), (second[3], 98)):
        values = [message.value for track in mido.MidiFile(file=io.BytesIO(output[filename])).tracks
                  for message in track if message.type == "control_change" and message.control == 66]
        assert values == [expected, 0]
