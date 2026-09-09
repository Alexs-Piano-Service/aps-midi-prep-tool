"""Exercise controller requirements through preview, packing, and the UI worker."""

import io
import os
from pathlib import Path

import mido
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.disk_session_worker import EmulatorImageBuildWorker
from aps_midi_prep_tool_app.emulator_image_builder import build_emulator_disk_images
from aps_midi_prep_tool_app.eseq_converter import (
    convert_eseq_bytes_to_midi_bytes,
    convert_midi_bytes_to_eseq_bytes,
)
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY,
    FloppyImageError,
    FloppyImageSession,
)
from aps_midi_prep_tool_app.main_window import MidiTitleWindow
from aps_midi_prep_tool_app.preparation_profiles import (
    get_preparation_medium,
    get_preparation_profile,
)


def _song_bytes(title, midi_type=1):
    song = mido.MidiFile(type=midi_type, ticks_per_beat=480)
    song.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name=title),
        mido.MetaMessage("set_tempo", tempo=600000),
        mido.MetaMessage("set_tempo", tempo=500000, time=360),
    ]))
    for channel, note, program in ((0, 60, 0), (5, 67, 40)):
        song.tracks.append(mido.MidiTrack([
            mido.Message("program_change", channel=channel, program=program),
            mido.Message("note_on", channel=channel, note=note, velocity=79, time=120),
            mido.Message("control_change", channel=channel, control=64, value=77, time=100),
            mido.Message("control_change", channel=channel, control=66, value=127, time=20),
            mido.Message("note_off", channel=channel, note=note, velocity=42, time=360),
            mido.Message("control_change", channel=channel, control=64, value=0, time=30),
            mido.Message("control_change", channel=channel, control=66, value=0),
        ]))
    if midi_type == 0:
        song.tracks = [mido.merge_tracks(song.tracks)]
    output = io.BytesIO()
    song.save(file=output)
    return output.getvalue()


def _midi(data):
    return mido.MidiFile(file=io.BytesIO(data))


def _performance(data):
    song = _midi(data)
    tick = 0
    events = []
    for message in mido.merge_tracks(song.tracks):
        tick += message.time
        if not message.is_meta or message.type == "set_tempo":
            events.append((tick, message.copy(time=0).dict()))
    return song.ticks_per_beat, events


def _title(data):
    return next(message.name for track in _midi(data).tracks for message in track
                if message.type == "track_name")


def _image_payloads(image_path):
    session = FloppyImageSession.load(image_path)
    try:
        return {
            entry.path: Path(session.extract_file(entry.path)).read_bytes()
            for entry in session.list_entries().entries
            if entry.path.upper().endswith(".MID")
        }
    finally:
        session.cleanup()


def test_type0_build_prepares_before_preview_and_preserves_delivered_performance(tmp_path):
    source = tmp_path / "songs"
    source.mkdir()
    originals = {
        "Type one.mid": _song_bytes("Type one"),
        "Already zero.mid": _song_bytes("Already zero", midi_type=0),
        "From ESEQ.fil": convert_midi_bytes_to_eseq_bytes(
            _song_bytes("From ESEQ", midi_type=0), title_override="From ESEQ",
        ),
    }
    for filename, data in originals.items():
        (source / filename).write_bytes(data)
    expected_performances = {
        "Type one": _performance(originals["Type one.mid"]),
        "Already zero": _performance(originals["Already zero.mid"]),
        "From ESEQ": _performance(convert_eseq_bytes_to_midi_bytes(originals["From ESEQ.fil"])),
    }
    preview_payloads = {}

    def approve(preview):
        for disk in preview.disks:
            for song in disk.songs:
                data = Path(song.local_path).read_bytes()
                assert _midi(data).type == 0
                assert len(_midi(data).tracks) == 1
                assert _title(data) == song.title
                assert _performance(data) == expected_performances[song.title]
                preview_payloads[song.image_path] = data
        return {"action": "build"}

    result = build_emulator_disk_images(
        source, tmp_path / "images", output_content="midi", output_ext="img",
        disk_format=DISK_FORMAT_BY_KEY["ibm.720"], require_midi_type0=True,
        review_callback=approve,
    )

    assert result.files_prepared == 3
    assert result.converted_files == 2  # Type 1 and E-SEQ; each song counts once.
    assert result.images_created == 1
    assert result.contents_verified
    assert len(preview_payloads) == 3
    assert _image_payloads(result.output_paths[0]) == preview_payloads
    by_title = {_title(data): data for data in preview_payloads.values()}
    assert by_title["Already zero"] == originals["Already zero.mid"]
    assert {path.name: path.read_bytes() for path in source.iterdir()} == originals


def test_build_without_type0_requirement_preserves_type1_bytes(tmp_path):
    source = tmp_path / "songs"
    source.mkdir()
    original = _song_bytes("Type one stays")
    (source / "SONG.MID").write_bytes(original)

    result = build_emulator_disk_images(
        source, tmp_path / "images", output_content="midi", output_ext="img",
        disk_format=DISK_FORMAT_BY_KEY["ibm.720"],
    )

    payload, = _image_payloads(result.output_paths[0]).values()
    assert payload == original
    assert _midi(payload).type == 1
    assert result.converted_files == 0


@pytest.mark.parametrize("kind, expected_error", (
    ("type2", "format 2"),
    ("recovery_filler", "unreadable data"),
))
def test_unpreparable_midi_does_not_replace_existing_delivery(tmp_path, kind, expected_error):
    source = tmp_path / "songs"
    source.mkdir()
    if kind == "type2":
        original = _song_bytes("Independent sequences", midi_type=2)
    else:
        track = b"-=[BAD SECTOR]=-" * 32 + b"\x00\xff\x2f\x00"
        original = (b"MThd\x00\x00\x00\x06\x00\x01\x00\x01\x01\xe0"
                    + b"MTrk" + len(track).to_bytes(4, "big") + track)
    (source / "SONG.MID").write_bytes(original)
    output = tmp_path / "images"
    output.mkdir()
    existing = output / "DSKA0000.img"
    existing.write_bytes(b"previous delivery")
    previews = []

    with pytest.raises(FloppyImageError, match=expected_error):
        build_emulator_disk_images(
            source, output, prefix="DSKA", output_content="midi", output_ext="img",
            disk_format=DISK_FORMAT_BY_KEY["ibm.720"], require_midi_type0=True,
            overwrite_existing=True,
            review_callback=lambda preview: previews.append(preview) or {"action": "build"},
        )

    assert previews == []
    assert list(output.iterdir()) == [existing]
    assert existing.read_bytes() == b"previous delivery"
    assert (source / "SONG.MID").read_bytes() == original


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("profile_key, expected_type", (
    ("pianodisc_128plus", 0),
    ("pianodisc_228cfx", 0),
    ("qrs_chili", 1),
))
def test_selected_controller_reaches_real_worker_and_delivered_image(
    application, monkeypatch, tmp_path, profile_key, expected_type,
):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    source = tmp_path / "songs"
    source.mkdir()
    original = _song_bytes("Worker performance")
    (source / "SONG.MID").write_bytes(original)
    window = MidiTitleWindow()
    results, failures, worker_flags, preview_types = [], [], [], []

    def run_synchronously(worker):
        worker_flags.append(worker.require_midi_type0)
        worker.run()  # The actual worker passes options to the actual builder.
        worker.finished.emit()

    def approve(preview):
        for disk in preview.disks:
            preview_types.extend(_midi(Path(song.local_path).read_bytes()).type for song in disk.songs)
        window.emulatorImageWorker.resolve_preview_request({"action": "build"})

    monkeypatch.setattr(EmulatorImageBuildWorker, "start", run_synchronously)
    window._on_emulator_preview_requested = approve
    window._on_emulator_image_success = results.append
    window._on_emulator_image_failure = failures.append
    window._show_centered_progress_dialog = lambda *_args: None
    try:
        profile = get_preparation_profile(profile_key)
        window._apply_preparation_profile(profile, get_preparation_medium(profile, "original"))
        window._start_emulator_image_build(
            str(source), str(tmp_path / "images"), prefix="DSKA", starting_number=0,
            safety_margin_bytes=0, album_title="", output_content="midi",
            disk_format=DISK_FORMAT_BY_KEY[profile.disk_format_key], output_ext="img",
            include_subfolders=False, shuffle=False, include_song_lists=False,
        )
        assert failures == []
        assert worker_flags == [expected_type == 0]
        assert preview_types == [expected_type]
        assert len(results) == 1
        payload, = _image_payloads(results[0].output_paths[0]).values()
        assert _midi(payload).type == expected_type
        assert _performance(payload) == _performance(original)
        assert _title(payload) == "Worker performance"
        assert (source / "SONG.MID").read_bytes() == original
    finally:
        window._close_emulator_image_progress()
        window.close()
