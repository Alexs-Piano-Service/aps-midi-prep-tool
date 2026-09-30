"""Playback cleanup must agree across real conversion and delivery entry points."""

import io
import os
import mido
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.bulk_extraction import bulk_extract_images
from aps_midi_prep_tool_app.eseq_converter import (
    CC7_POLICY_PRESERVE,
    ESEQ_CONTAINER_CLAVINOVA_MDA,
    ESEQ_CONTAINER_DISKLAVIER,
    convert_eseq_bytes_to_midi_bytes,
    convert_eseq_file_to_midi_path,
    convert_midi_bytes_to_eseq_bytes,
)
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY,
    FloppyImageSession,
    create_floppy_images_from_files,
)
from aps_midi_prep_tool_app.preparation_profiles import (
    get_preparation_medium,
    get_preparation_profile,
)


def _channel_events(payload):
    tick = 0
    events = []
    song = mido.MidiFile(file=io.BytesIO(payload))
    for message in mido.merge_tracks(song.tracks):
        tick += message.time
        if not message.is_meta:
            events.append((tick, message.copy(time=0)))
    return events


def _volume_song(container, onset):
    # Three startup mutes need cleanup. The rest represent independent parts,
    # restoration before/after the first note, intentional later silence, or a
    # silent channel whose intent cannot be inferred from a sounding note.
    events = [
        (0, mido.Message("control_change", channel=0, control=7, value=0)),
        (0, mido.Message("control_change", channel=1, control=7, value=80)),
        (0, mido.Message("control_change", channel=2, control=7, value=0)),
        (0, mido.Message("control_change", channel=4, control=7, value=0)),
        (10, mido.Message("note_on", channel=3, note=65, velocity=70)),
        (20, mido.Message("control_change", channel=3, control=7, value=0)),
        (30, mido.Message("control_change", channel=2, control=7, value=90)),
        (onset, mido.Message("note_on", channel=0, note=60, velocity=73)),
        (onset, mido.Message("note_on", channel=1, note=62, velocity=77)),
        (onset, mido.Message("note_on", channel=2, note=64, velocity=81)),
        (onset, mido.Message("note_on", channel=5, note=67, velocity=85)),
        (onset, mido.Message("control_change", channel=5, control=7, value=0)),
        (onset + 12, mido.Message("control_change", channel=0, control=7, value=100)),
        (onset + 24, mido.Message("control_change", channel=1, control=7, value=0)),
        (onset + 36, mido.Message("control_change", channel=2, control=11, value=0)),
        (onset + 48, mido.Message("control_change", channel=0, control=7, value=0)),
        (onset + 60, mido.Message("control_change", channel=2, control=7, value=0)),
        (onset + 96, mido.Message("note_off", channel=0, note=60, velocity=23)),
        (onset + 96, mido.Message("note_off", channel=1, note=62, velocity=27)),
        (onset + 96, mido.Message("note_off", channel=2, note=64, velocity=31)),
        (onset + 96, mido.Message("note_off", channel=3, note=65, velocity=35)),
        (onset + 96, mido.Message("note_off", channel=5, note=67, velocity=39)),
        (onset + 120, mido.Message("control_change", channel=0, control=7, value=110)),
    ]
    song = mido.MidiFile(type=0, ticks_per_beat=384)
    track = mido.MidiTrack([
        mido.MetaMessage("track_name", name="Volume regression"),
        mido.MetaMessage("set_tempo", tempo=512820),
    ])
    previous = 0
    for tick, message in events:
        track.append(message.copy(time=tick - previous))
        previous = tick
    song.tracks.append(track)
    output = io.BytesIO()
    song.save(file=output)
    source_midi = output.getvalue()
    source = convert_midi_bytes_to_eseq_bytes(
        source_midi, container_variant=container,
        cc7_policy=CC7_POLICY_PRESERVE, timing_policy="preserve", pedal_policy="preserve",
    )
    preserved = _channel_events(convert_eseq_bytes_to_midi_bytes(
        source, cc7_policy=CC7_POLICY_PRESERVE,
    ))
    # Identify the three fixture messages explicitly, without reproducing the
    # converter's first-note algorithm to calculate the expected answer.
    startup_channels = {0, 2, 5}
    expected = []
    for tick, message in preserved:
        if (message.type == "control_change" and message.control == 7
                and message.value == 0 and message.channel in startup_channels):
            startup_channels.remove(message.channel)
        else:
            expected.append((tick, message))
    assert startup_channels == set()
    assert [(message.channel, message.value) for _tick, message in expected
            if message.type == "control_change" and message.control == 7] == [
        (1, 80), (4, 0), (3, 0), (2, 90), (0, 100),
        (1, 0), (0, 0), (2, 0), (0, 110),
    ]
    return source, source_midi, expected


@pytest.fixture
def window(monkeypatch, tmp_path):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    settings = QSettings(str(tmp_path / "mute-workflows.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *_a, **_k: None)
    instance = main_window.MidiTitleWindow()
    errors = []
    dialogs = []
    monkeypatch.setattr(instance, "_show_error_list", lambda *a, **k: errors.append((a, k)))
    monkeypatch.setattr(instance, "_show_operation_error", lambda *a, **k: errors.append((a, k)))
    monkeypatch.setattr(instance, "_offer_post_load_sequence_conversions", lambda: None)

    def accept_dialog(dialog):
        dialogs.append(dialog.windowTitle())
        return QDialog.Accepted

    monkeypatch.setattr(instance, "_exec_child_dialog", accept_dialog)
    instance.mute_workflow_errors = errors
    instance.mute_workflow_dialogs = dialogs
    yield instance
    instance._clear_staging_history()
    instance._cleanup_midi_scratch_dir()
    if instance.image_session is not None:
        instance.image_session.cleanup()
    instance.deleteLater()
    app.processEvents()


def _apply(window, profile_key):
    profile = get_preparation_profile(profile_key)
    window._apply_preparation_profile(
        profile, get_preparation_medium(profile, profile.default_medium),
    )


def _make_image(source, directory):
    directory.mkdir()
    path = directory / "volume.img"
    create_floppy_images_from_files(
        [(str(source), source.name)], str(path), "img", DISK_FORMAT_BY_KEY["ibm.720"],
    )
    return path


WORKFLOWS = (
    "file-conversion", "interactive", "hidden-dialog", "automatic-files",
    "automatic-image", "image-interactive", "floppy-after-read", "bulk-extraction",
)


@pytest.mark.parametrize("workflow", WORKFLOWS)
@pytest.mark.parametrize("container,extension", (
    (ESEQ_CONTAINER_DISKLAVIER, "FIL"),
    (ESEQ_CONTAINER_CLAVINOVA_MDA, "MDA"),
))
@pytest.mark.parametrize("onset", (48, 960), ids=("short-lead-in", "long-lead-in"))
def test_all_midi_workflows_remove_only_startup_mutes(
    window, monkeypatch, tmp_path, workflow, container, extension, onset,
):
    original, _source_midi, expected = _volume_song(container, onset)
    source = tmp_path / f"SOURCE.{extension}"
    source.write_bytes(original)
    output = tmp_path / "output"
    image_path = None
    image_bytes = None
    if workflow in {"automatic-image", "image-interactive", "floppy-after-read", "bulk-extraction"}:
        image_path = _make_image(source, tmp_path / "images")
        image_bytes = image_path.read_bytes()

    if workflow == "file-conversion":
        output.mkdir()
        convert_eseq_file_to_midi_path(source, output / "converted.mid")
    elif workflow == "bulk-extraction":
        result = bulk_extract_images(
            image_path.parent, output, convert_eseq=True,
            job_record_path=output / "job.json",
        )
        assert result.errors == ()
        assert result.files_converted == 1
    else:
        # A stale previous policy must not override the actual user workflow.
        window._eseq_conversion_cc7_policy = CC7_POLICY_PRESERVE
        if image_path is not None:
            session = FloppyImageSession.load(image_path)
            window._on_disk_load_success(session, session.list_entries())
            window._on_disk_load_finished()
        else:
            window._load_regular_files([str(source)], "Imported E-SEQ")
        if workflow.startswith("automatic"):
            _apply(window, "enspire")
        elif workflow == "floppy-after-read":
            window.pendingFloppyReadConvertToMidi = True
            window.pendingFloppyReadLongFilenames = False
            window._convert_loaded_floppy_to_midi_after_read()
        else:
            window.settings.setValue(
                window.SETTING_SKIP_ESEQ_TO_MIDI_CONVERSION_PROMPT,
                workflow == "hidden-dialog",
            )
            window.convert_all_eseq_to_midi()
            assert bool(window.mute_workflow_dialogs) is (workflow != "hidden-dialog")
        QTest.qWait(20)
        monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(output))
        window.save_as_changes()
        assert not window.mute_workflow_errors

    midi_outputs = [path for path in output.rglob("*") if path.suffix.lower() == ".mid"]
    assert len(midi_outputs) == 1
    assert _channel_events(midi_outputs[0].read_bytes()) == expected
    assert source.read_bytes() == original
    if image_path is not None:
        assert image_path.read_bytes() == image_bytes


@pytest.mark.parametrize("profile_key", ("midi_export", "enspire", "pianodisc_128plus"))
def test_automatic_preparation_does_not_repair_intentional_midi_startup_volume(
    window, monkeypatch, tmp_path, profile_key,
):
    _eseq, original, _expected = _volume_song(ESEQ_CONTAINER_DISKLAVIER, 960)
    source = tmp_path / "SOURCE.MID"
    source.write_bytes(original)
    window._load_regular_files([str(source)], "Imported MIDI")
    _apply(window, profile_key)
    output = tmp_path / "output"
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(output))

    window.save_as_changes()

    midi_outputs = [path for path in output.rglob("*") if path.suffix.lower() == ".mid"]
    assert len(midi_outputs) == 1
    assert not window.mute_workflow_errors
    assert _channel_events(midi_outputs[0].read_bytes()) == _channel_events(original)
    assert source.read_bytes() == original


@pytest.mark.parametrize("image_mode", (False, True), ids=("regular-files", "image"))
def test_clavinova_container_preparation_keeps_volume_until_midi_export(
    window, monkeypatch, tmp_path, image_mode,
):
    original, _source_midi, expected = _volume_song(ESEQ_CONTAINER_CLAVINOVA_MDA, 960)
    source = tmp_path / "SOURCE.MDA"
    source.write_bytes(original)
    image_path = None
    if image_mode:
        image_path = _make_image(source, tmp_path / "images")
        image_bytes = image_path.read_bytes()
        session = FloppyImageSession.load(image_path)
        window._on_disk_load_success(session, session.list_entries())
        window._on_disk_load_finished()
    else:
        window._load_regular_files([str(source)], "Imported Clavinova song")
    _apply(window, "mark_ii")
    prepared = tmp_path / "prepared"
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(prepared))

    window.save_as_changes()

    native_outputs = [
        path for path in prepared.rglob("*")
        if path.suffix.lower() == ".fil" and path.name.upper() != "PIANODIR.FIL"
    ]
    assert len(native_outputs) == 1
    # A container change is not a playback export: preserving here is correct.
    assert _channel_events(convert_eseq_bytes_to_midi_bytes(
        native_outputs[0].read_bytes(), cc7_policy=CC7_POLICY_PRESERVE,
    )) == _channel_events(convert_eseq_bytes_to_midi_bytes(
        original, cc7_policy=CC7_POLICY_PRESERVE,
    ))

    # The subsequent public MIDI conversion must clean up those preserved
    # startup commands and agree with exporting the original MDA directly.
    window.disengage_preparation_profile()
    window._eseq_conversion_cc7_policy = CC7_POLICY_PRESERVE
    window.convert_all_eseq_to_midi()
    output = tmp_path / "midi-export"
    monkeypatch.setattr(main_window.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(output))
    window.save_as_changes()

    midi_outputs = [path for path in output.rglob("*") if path.suffix.lower() == ".mid"]
    assert len(midi_outputs) == 1
    assert not window.mute_workflow_errors
    assert _channel_events(midi_outputs[0].read_bytes()) == expected
    assert source.read_bytes() == original
    if image_path is not None:
        assert image_path.read_bytes() == image_bytes
