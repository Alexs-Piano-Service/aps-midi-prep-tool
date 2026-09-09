import inspect
import unittest
from unittest.mock import patch

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer

from aps_midi_prep_tool_app.main_window import (
    FileInspectionDialog,
    FluidSynthPlaybackProcess,
)


class _ButtonStub:
    def __init__(self, enabled):
        self.enabled = enabled

    def isEnabled(self):
        return self.enabled

    def setEnabled(self, enabled):
        self.enabled = bool(enabled)


class _SliderStub:
    def __init__(self, value=0, maximum=1000):
        self._value = value
        self._maximum = maximum

    def value(self):
        return self._value

    def maximum(self):
        return self._maximum


class _InspectionPlaybackStub:
    def __init__(self, *, playing=False, midi_worker=None, play_enabled=True):
        self.playing = playing
        self.midi_output_worker = midi_worker
        self.live_synth_process = None
        self.play_button = _ButtonStub(play_enabled)
        self.play_calls = 0
        self.stop_calls = 0

    def _smooth_playback_is_active(self):
        return self.playing

    def _play_current_file(self):
        self.play_calls += 1

    def _stop_playback(self):
        self.stop_calls += 1


class FileInspectionPlaybackTests(unittest.TestCase):
    def test_realtime_fluidsynth_waits_for_stopped_position_before_audible_start(self):
        class _Process:
            READY_MARKER = FluidSynthPlaybackProcess.READY_MARKER
            tempo_percent = 50
            _tempo_command = staticmethod(
                FluidSynthPlaybackProcess._tempo_command
            )

            def __init__(self):
                self.commands = ""
                self._ready = False
                self._stopping = False

            def _probe_startup_position(self):
                FluidSynthPlaybackProcess._probe_startup_position(self)

            def write(self, payload):
                self.commands += bytes(payload).decode("utf-8")

        process = _Process()
        FluidSynthPlaybackProcess._on_process_started(process)

        self.assertEqual(
            process.commands.splitlines(),
            [
                "player_stop",
                "echo APS_MIDI_PREVIEW_READY",
            ],
        )

    def test_realtime_fluidsynth_is_configured_before_first_note(self):
        class _Process:
            _start_tick = 96
            _startup_seek_tick = 97
            tempo_percent = 75
            program_overrides = {5: 40}
            _tempo_command = staticmethod(
                FluidSynthPlaybackProcess._tempo_command
            )
            _program_command = staticmethod(
                FluidSynthPlaybackProcess._program_command
            )

        self.assertEqual(
            FluidSynthPlaybackProcess._startup_config_commands(
                _Process()
            ),
            [
                "player_tempo_int 1.500000",
                "player_seek 97",
                "select 4 1 0 40",
            ],
        )

    def test_live_program_tracking_is_initialized_before_use(self):
        source = inspect.getsource(FileInspectionDialog.__init__)
        initialization = (
            "self._live_recorded_program_channels = set()"
        )
        self.assertIn(initialization, source)
        self.assertNotIn(
            "self._live_recorded_program_channels.clear()",
            source[:source.index(initialization)],
        )

    def test_realtime_fluidsynth_commands_use_live_tempo_and_programs(self):
        self.assertEqual(
            FluidSynthPlaybackProcess._tempo_command(10),
            "player_tempo_int 0.200000",
        )
        self.assertEqual(
            FluidSynthPlaybackProcess._program_command(5, 40),
            "select 4 1 0 40",
        )

    def test_space_starts_enabled_playback(self):
        dialog = _InspectionPlaybackStub()

        FileInspectionDialog._toggle_playback(dialog)

        self.assertEqual(dialog.play_calls, 1)
        self.assertEqual(dialog.stop_calls, 0)

    def test_space_stops_active_audio_or_midi_playback(self):
        for dialog in (
            _InspectionPlaybackStub(playing=True),
            _InspectionPlaybackStub(midi_worker=object(), play_enabled=False),
        ):
            with self.subTest(dialog=dialog):
                FileInspectionDialog._toggle_playback(dialog)
                self.assertEqual(dialog.play_calls, 0)
                self.assertEqual(dialog.stop_calls, 1)

    def test_space_does_nothing_when_playback_is_unavailable(self):
        dialog = _InspectionPlaybackStub(play_enabled=False)

        FileInspectionDialog._toggle_playback(dialog)

        self.assertEqual(dialog.play_calls, 0)
        self.assertEqual(dialog.stop_calls, 0)

    def test_live_tempo_change_keeps_the_current_audio_source_position(self):
        class _Player:
            def __init__(self):
                self.rate = None

            def position(self):
                return 5000

            def playbackState(self):
                return QMediaPlayer.PlaybackState.PlayingState

            def setPlaybackRate(self, rate):
                self.rate = rate

        class _Dialog:
            def __init__(self):
                self._last_tempo_percent = 100
                self._rendered_tempo_percent = 100
                self._updating_playback_rate = False
                self.preview_audio_path = "already-loaded.wav"
                self.midi_output_worker = None
                self.live_synth_process = None
                self.player = _Player()
                self.position_slider = _SliderStub(250)
                self.current_duration = 20.0
                self.visible_notes = [object()]
                self.play_button = _ButtonStub(True)
                self.synced_position = None
                self.displayed_position = None

            def _smooth_playback_is_active(self):
                return True

            def _preview_duration_for_tempo(self, tempo):
                return self.current_duration * 100.0 / tempo

            def _refresh_preview_timing(self):
                pass

            def _sync_playback_clock(self, position):
                self.synced_position = position

            def _set_playback_position_display(self, position):
                self.displayed_position = position

            def _finish_playback_rate_update(self):
                pass

        dialog = _Dialog()
        with patch(
            "aps_midi_prep_tool_app.main_window.QTimer.singleShot"
        ) as single_shot:
            FileInspectionDialog._on_tempo_percent_changed(dialog, 10)

        self.assertEqual(dialog.player.rate, 0.1)
        self.assertEqual(dialog.synced_position, 50000)
        self.assertEqual(dialog.displayed_position, 50000)
        single_shot.assert_called_once()

    def test_live_synth_tempo_change_sends_command_without_source_swap(self):
        class _LiveSynth:
            def __init__(self):
                self.tempos = []

            def set_tempo_percent(self, tempo):
                self.tempos.append(tempo)

        class _Player:
            def playbackState(self):
                return QMediaPlayer.PlaybackState.StoppedState

        class _Dialog:
            def __init__(self):
                self._last_tempo_percent = 100
                self._rendered_tempo_percent = 100
                self._updating_playback_rate = False
                self.preview_audio_path = ""
                self.midi_output_worker = None
                self.live_synth_process = _LiveSynth()
                self.player = _Player()
                self.position_slider = _SliderStub(250)
                self.current_duration = 20.0
                self.visible_notes = [object()]
                self.play_button = _ButtonStub(True)
                self.synced_position = None
                self.displayed_position = None

            def _smooth_playback_is_active(self):
                return True

            def _smoothed_playback_position_ms(self):
                return 5000

            def _preview_duration_for_tempo(self, tempo):
                return self.current_duration * 100.0 / tempo

            def _refresh_preview_timing(self):
                pass

            def _sync_playback_clock(self, position):
                self.synced_position = position

            def _set_playback_position_display(self, position):
                self.displayed_position = position

        dialog = _Dialog()
        FileInspectionDialog._on_tempo_percent_changed(dialog, 10)

        self.assertEqual(dialog.live_synth_process.tempos, [10])
        self.assertEqual(dialog.synced_position, 50000)
        self.assertEqual(dialog.displayed_position, 50000)

    def test_loaded_instrument_preview_seeks_before_resuming(self):
        expected_path = "/tmp/replacement-preview.wav"

        class _Player:
            def __init__(self):
                self.rate = None
                self.position_ms = None
                self.play_calls = 0

            def source(self):
                return QUrl.fromLocalFile(expected_path)

            def setPlaybackRate(self, rate):
                self.rate = rate

            def setPosition(self, position_ms):
                self.position_ms = position_ms

            def play(self):
                self.play_calls += 1

        class _Dialog:
            def __init__(self):
                self._pending_audio_start = {
                    "path": expected_path,
                    "base_position_ms": 8000,
                    "autoplay": True,
                    "old_path": "",
                }
                self._closing = False
                self._rendered_tempo_percent = 100
                self.player = _Player()
                self.synced_position = None
                self.displayed_position = None

            def _preview_tempo_percent(self):
                return 50

            def _sync_playback_clock(self, position):
                self.synced_position = position

            def _set_playback_position_display(self, position):
                self.displayed_position = position

            def _apply_loaded_audio_position(self, base_position_ms, *, autoplay):
                FileInspectionDialog._apply_loaded_audio_position(
                    self,
                    base_position_ms,
                    autoplay=autoplay,
                )

        dialog = _Dialog()
        FileInspectionDialog._complete_pending_audio_start(
            dialog,
            expected_path,
        )

        self.assertIsNone(dialog._pending_audio_start)
        self.assertEqual(dialog.player.rate, 0.5)
        self.assertEqual(dialog.player.position_ms, 8000)
        self.assertEqual(dialog.synced_position, 16000)
        self.assertEqual(dialog.displayed_position, 16000)
        self.assertEqual(dialog.player.play_calls, 1)

    def test_audio_playback_prefers_realtime_fluidsynth(self):
        class _Dialog:
            def __init__(self):
                self.visible_notes = [object()]
                self.preview_render_worker = None
                self.live_starts = 0
                self.render_starts = 0

            def _using_midi_output(self):
                return False

            def _can_use_live_fluidsynth(self):
                return True

            def _start_live_fluidsynth_playback(self):
                self.live_starts += 1

            def _start_audio_preview_render(self, *, autoplay):
                del autoplay
                self.render_starts += 1

        dialog = _Dialog()
        FileInspectionDialog._play_current_file(dialog)

        self.assertEqual(dialog.live_starts, 1)
        self.assertEqual(dialog.render_starts, 0)


if __name__ == "__main__":
    unittest.main()
