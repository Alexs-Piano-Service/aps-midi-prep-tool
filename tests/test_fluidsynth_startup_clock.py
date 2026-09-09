"""FluidSynth stays silent until its seek position and preview clock agree."""

import io
from pathlib import Path

import mido
import pytest
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window


FluidSynthPlaybackProcess = main_window.FluidSynthPlaybackProcess


def _midi_source():
    midi = mido.MidiFile(type=1, ticks_per_beat=96)
    midi.tracks.append(mido.MidiTrack([
        mido.MetaMessage("set_tempo", tempo=500000),
        mido.MetaMessage("time_signature", numerator=3, denominator=4),
        mido.MetaMessage("text", text="", time=1),
        mido.MetaMessage("end_of_track", time=959),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.Message("note_on", channel=0, note=60, velocity=90),
        mido.Message("control_change", channel=0, control=64, value=127, time=96),
        mido.Message("note_off", channel=0, note=60, time=864),
        mido.MetaMessage("end_of_track"),
    ]))
    midi.tracks.append(mido.MidiTrack([
        mido.Message("program_change", channel=4, program=40),
        mido.Message("note_on", channel=4, note=67, velocity=80, time=48),
        mido.Message("note_off", channel=4, note=67, time=96),
        mido.MetaMessage("end_of_track", time=56),
    ]))
    midi.tracks.append(mido.MidiTrack([mido.MetaMessage("end_of_track")]))
    output = io.BytesIO()
    midi.save(file=output)
    return output.getvalue()


def _absolute_messages(track):
    tick = 0
    events = []
    for message in track:
        tick += message.time
        events.append((tick, message.copy(time=0)))
    return events


def _position_line(tick, end=960):
    # Literal FluidSynth 2.4.4 shell output, including its BPM field.
    return f"player current pos:{tick}, end:{end}, bpm:120\n"


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def process(application, monkeypatch):
    clock = {"now": 10.0, "write_delay": 0.0}
    monkeypatch.setattr(main_window.time, "monotonic", lambda: clock["now"])
    scheduled = []
    monkeypatch.setattr(main_window.QTimer, "singleShot", lambda delay, callback: scheduled.append((delay, callback)))

    class Process(FluidSynthPlaybackProcess):
        def __init__(self):
            super().__init__(_midi_source(), "unused.sf2")
            self.incoming = b""
            self.writes = []
            self.started_events = []
            self.failures = []
            self._start_tick = 0
            self._startup_seek_tick = 1
            self.clock = clock
            self.scheduled = scheduled
            self.launches = 0
            self.playbackStarted.connect(lambda position, timestamp: self.started_events.append((position, timestamp)))
            self.playbackFailed.connect(self.failures.append)

        def write(self, payload):
            payload = bytes(payload)
            self.writes.append(payload.decode("utf-8"))
            if "player_cont" in self.writes[-1]:
                self.clock["now"] += self.clock["write_delay"]
            return len(payload)

        def readAllStandardOutput(self):
            payload = self.incoming
            self.incoming = b""
            return payload

        def state(self):
            return QProcess.ProcessState.Running

        def start(self):
            self.launches += 1

        def feed(self, text):
            self.incoming += text.encode("utf-8")
            self._read_process_output()

        def commands(self):
            return "".join(self.writes).splitlines()

    instance = Process()
    yield instance
    instance.startup_timer.stop()
    instance._cleanup_temporary_files()
    instance.deleteLater()
    application.processEvents()


@pytest.mark.parametrize("anchor_tick", [1, 240])
def test_anchor_is_added_to_every_track_without_shifting_music_or_sustained_notes(anchor_tick):
    source = _midi_source()
    anchored = FluidSynthPlaybackProcess._midi_with_seek_anchor(source, anchor_tick)
    before = mido.MidiFile(file=io.BytesIO(source))
    after = mido.MidiFile(file=io.BytesIO(anchored))

    assert (after.type, after.ticks_per_beat, len(after.tracks)) == (before.type, before.ticks_per_beat, 4)
    anchor = (anchor_tick, mido.MetaMessage("text", text=""))
    for old_track, new_track in zip(before.tracks, after.tracks):
        old_events = _absolute_messages(old_track)
        new_events = _absolute_messages(new_track)
        assert new_events[-1][1].type == "end_of_track"
        assert new_events[-1][0] >= max(old_events[-1][0], anchor_tick)
        old_music = [event for event in old_events if event[1].type != "end_of_track"]
        new_music = [event for event in new_events if event[1].type != "end_of_track"]
        assert new_music.count(anchor) == old_music.count(anchor) + 1
        new_music.remove(anchor)
        assert new_music == old_music
    assert [(tick, message.type) for tick, message in _absolute_messages(after.tracks[1]) if message.type in ("note_on", "note_off")] == [
        (0, "note_on"), (960, "note_off")
    ]


def test_launch_is_muted_and_config_seeks_past_a_tick_zero_note(process, monkeypatch, tmp_path):
    soundfont = tmp_path / "piano.sf2"
    soundfont.write_bytes(b"test fixture")
    process.soundfont_path = str(soundfont)
    monkeypatch.setattr(main_window, "_find_fluidsynth_command", lambda: "fluidsynth")

    process.start_playback()

    assert process.launches == 1
    arguments = process.arguments()
    assert float(arguments[arguments.index("-g") + 1]) == 0.0
    assert process._startup_seek_tick == process._start_tick + 1 == 1
    assert "player_seek 1" in Path(process._config_path).read_text().splitlines()
    staged = mido.MidiFile(process._midi_path)
    assert all((1, mido.MetaMessage("text", text="")) in _absolute_messages(track) for track in staged.tracks)
    process._on_process_started()
    assert process.commands() == ["player_stop", f"echo {process.READY_MARKER}"]
    assert process.started_events == []


def test_delayed_output_starts_clock_at_resume_write_not_launch_or_write_completion(process):
    process.start_seconds = 2.0
    process._start_tick = 384
    process._startup_seek_tick = 385
    process._on_process_started()
    process.clock["now"] = 40.0
    process.clock["write_delay"] = 7.0

    process.feed(_position_line(390) + f"{process.READY_MARKER}\n")

    assert process.started_events == [(2000, 40.0)]
    assert process.clock["now"] == 47.0
    assert "player_seek -6" in process.commands()
    assert process.commands()[-1] == "player_cont"
    assert process._ready
    assert not process.startup_timer.isActive()


def test_echoed_commands_and_partial_markers_do_not_release_audio(process):
    process.feed(f"> echo {process.READY_MARKER}\necho {process.READY_MARKER}\n")
    assert not process._ready
    assert process.started_events == []
    process.feed("player current po")
    process.feed(f"s:1, end:960, bpm:120\n{process.READY_MARKER[:-2]}")
    assert not process._ready
    assert process.commands() == []
    process.feed(process.READY_MARKER[-2:] + "\n")
    assert process._ready
    assert len(process.started_events) == 1


@pytest.mark.parametrize("position,end", [(0, 960), (1, 0)])
def test_unloaded_or_unreached_seek_retries_while_muted(process, position, end):
    process.set_volume_percent(75)
    process.feed(_position_line(position, end) + f"{process.READY_MARKER}\n")

    assert process.started_events == []
    assert not process._ready
    assert process.commands() == ["player_cont"]
    assert len(process.scheduled) == 1
    delay, callback = process.scheduled.pop()
    assert delay == 25
    callback()
    assert process.commands()[-2:] == ["player_stop", f"echo {process.READY_MARKER}"]

    process.feed(_position_line(2) + f"{process.READY_MARKER}\n")
    assert process._ready
    assert len(process.started_events) == 1
    assert process.commands()[-1] == "player_cont"
    assert f"gain {process._gain_for_percent(75):.4f}" in process.commands()


def test_last_position_before_marker_wins(process):
    process.feed(_position_line(0, 0) + _position_line(2) + f"{process.READY_MARKER}\n")
    assert process._ready
    assert "player_seek -2" in process.commands()
    assert len(process.started_events) == 1


def test_pending_controls_and_latest_tempo_and_gain_precede_resume(process):
    process.program_overrides = {3: 10}
    process.available_channels = {1, 3, 5, 7}
    process.enabled_channels = {1, 3, 5}
    process.set_program(3, 41)
    process.set_program(5, 40)
    process.set_tempo_percent(50)
    process.set_volume_percent(60)
    process.set_tempo_percent(125)
    assert process.commands() == []

    process.feed(_position_line(1) + f"{process.READY_MARKER}\n")

    commands = process.commands()
    assert commands[:2] == ["reset", "player_seek -1"]
    assert "select 4 1 0 40" in commands
    assert "select 2 1 0 10" in commands
    assert commands.index("reset") < commands.index("select 2 1 0 10")
    assert commands.index("select 2 1 0 10") < commands.index("select 2 1 0 41")
    assert commands[3:6] == ["cc 6 7 0", "cc 6 64 0", "cc 6 123 0"]
    assert commands[-3:] == [
        process._tempo_command(125), f"gain {process._gain_for_percent(60):.4f}", "player_cont",
    ]
    assert process._pending_commands == []
    assert len(process.started_events) == 1


def test_duplicate_marker_after_success_does_not_restart_or_reset_clock(process):
    process.feed(_position_line(1) + f"{process.READY_MARKER}\n")
    original_writes = list(process.writes)
    original_start = list(process.started_events)
    process.clock["now"] += 1.0

    process.feed(_position_line(80) + f"{process.READY_MARKER}\n")
    process._probe_startup_position()

    assert process.writes == original_writes
    assert process.started_events == original_start


def test_late_ready_or_retry_after_stop_cannot_restart_playback(process):
    process.stop_playback()
    written_at_stop = list(process.writes)

    process.feed(_position_line(1) + f"{process.READY_MARKER}\n")
    process._probe_startup_position()

    assert process.writes == written_at_stop
    assert process.started_events == []
    assert not process._ready


def test_marker_without_valid_position_fails_and_stops(process):
    process.feed(f"player_stop failed\n{process.READY_MARKER}\n")

    assert len(process.failures) == 1
    assert process._stopping
    assert process.started_events == []
    assert process.commands()[-2:] == ["reset", "quit"]


def test_startup_timeout_stops_muted_retry_without_clock_activation(process):
    process.feed(_position_line(0, 0) + f"{process.READY_MARKER}\n")
    process._on_startup_timeout()

    assert process._stopping
    assert len(process.failures) == 1
    assert process.started_events == []
    assert process.commands()[-2:] == ["reset", "quit"]
