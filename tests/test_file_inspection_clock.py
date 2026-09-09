"""The preview clock follows current audio, even after delayed GUI delivery."""

from types import SimpleNamespace

import pytest
from PySide6.QtMultimedia import QMediaPlayer

from aps_midi_prep_tool_app import main_window


class _ClockDialog:
    _audio_source_to_preview_ms = main_window.FileInspectionDialog._audio_source_to_preview_ms
    _sync_playback_clock = main_window.FileInspectionDialog._sync_playback_clock
    _smooth_playback_is_active = main_window.FileInspectionDialog._smooth_playback_is_active
    _smoothed_playback_position_ms = main_window.FileInspectionDialog._smoothed_playback_position_ms
    _on_player_position_changed = main_window.FileInspectionDialog._on_player_position_changed
    _refresh_playback_position = main_window.FileInspectionDialog._refresh_playback_position
    _on_player_playback_state_changed = main_window.FileInspectionDialog._on_player_playback_state_changed

    def __init__(self, *, rendered_tempo=100, tempo=100):
        self._rendered_tempo_percent = rendered_tempo
        self.tempo = tempo
        self._midi_playback_clock_active = False
        self._live_synth_clock_active = False
        self._updating_playback_rate = False
        self._pending_audio_start = None
        self.source_position_ms = 10000
        self._playback_clock_position_ms = 10000
        self._playback_clock_started_at = 100.0
        self.playing = True
        self.timer_running = True
        self.displayed = None
        self.player = SimpleNamespace(
            position=lambda: self.source_position_ms,
            playbackState=lambda: (
                QMediaPlayer.PlaybackState.PlayingState if self.playing
                else QMediaPlayer.PlaybackState.StoppedState
            ),
        )
        self.playback_timer = SimpleNamespace(
            stop=lambda: setattr(self, "timer_running", False),
            start=lambda: setattr(self, "timer_running", True),
            isActive=lambda: self.timer_running,
        )

    def _preview_tempo_percent(self):
        return self.tempo

    def _preview_duration(self):
        return 120.0

    def _set_playback_position_display(self, position):
        self.displayed = position


def test_delayed_audio_position_signal_does_not_rewind_current_cursor(monkeypatch):
    monkeypatch.setattr(main_window.time, "monotonic", lambda: 101.0)
    dialog = _ClockDialog()
    dialog.source_position_ms = 11000
    # Audio and the interpolated visual clock are already at 11 seconds;
    # the GUI only now receives an older backend notification.
    dialog._on_player_position_changed(10000)
    assert dialog._smoothed_playback_position_ms() == 11000
    assert dialog._playback_clock_started_at == 100.0


@pytest.mark.parametrize("rendered_tempo, tempo, expected", [(100, 100, 15000), (100, 50, 30000), (50, 100, 7500)])
def test_timer_recovers_audio_position_without_waiting_for_a_signal(monkeypatch, rendered_tempo, tempo, expected):
    monkeypatch.setattr(main_window.time, "monotonic", lambda: 100.0)
    dialog = _ClockDialog(rendered_tempo=rendered_tempo, tempo=tempo)
    dialog.source_position_ms = 15000
    dialog._refresh_playback_position()
    assert dialog.displayed == expected


def test_small_backend_position_steps_keep_smooth_interpolation(monkeypatch):
    monkeypatch.setattr(main_window.time, "monotonic", lambda: 100.125)
    dialog = _ClockDialog()
    dialog.source_position_ms = 10050
    dialog._refresh_playback_position()
    assert dialog.displayed == 10125
    assert dialog._playback_clock_started_at == 100.0


def test_real_backward_seek_reanchors_to_the_current_player_position(monkeypatch):
    monkeypatch.setattr(main_window.time, "monotonic", lambda: 101.0)
    dialog = _ClockDialog()
    dialog.source_position_ms = 2000
    dialog._on_player_position_changed(2000)
    assert dialog._smoothed_playback_position_ms() == 2000


@pytest.mark.parametrize("clock", ["_live_synth_clock_active", "_midi_playback_clock_active"])
def test_idle_audio_player_signals_do_not_reset_another_output_clock(monkeypatch, clock):
    monkeypatch.setattr(main_window.time, "monotonic", lambda: 101.0)
    dialog = _ClockDialog()
    setattr(dialog, clock, True)
    dialog.playing = False
    dialog.source_position_ms = 0
    dialog._on_player_position_changed(0)
    dialog._on_player_playback_state_changed(QMediaPlayer.PlaybackState.StoppedState)
    assert dialog._smoothed_playback_position_ms() == 11000
    assert dialog.timer_running
    assert dialog.displayed is None
