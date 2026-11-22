"""Inspection previews follow headphones, including a pending audio crossfade."""

from aps_midi_prep_tool_app import main_window
from test_audio_output_routing import _FakeAudioDevice
from test_file_inspection_actions import _SilentPlayer, application, dialog_factory


class _LoadingPlayer(_SilentPlayer):
    def mediaStatus(self):
        return self.MediaStatus.LoadingMedia


def test_inspection_changes_output_without_resetting_song_position(dialog_factory):
    dialog = dialog_factory([])
    dialog.player.setPosition(12345)
    dialog.audio_output.setVolume(.23)

    dialog.audio_output_follower._devices.use("headphones")

    assert dialog.audio_output.device().identifier == "headphones"
    assert dialog.audio_output.volume() == .23
    assert dialog.player.position() == 12345


def test_inspection_crossfade_outputs_follow_the_same_device(dialog_factory, monkeypatch, tmp_path):
    dialog = dialog_factory([])
    monkeypatch.setattr(main_window, "QMediaPlayer", _LoadingPlayer)
    dialog._begin_audio_handoff(str(tmp_path / "new.wav"), "piano", 100,
                               str(tmp_path / "old.wav"), 100)
    pending_output = dialog._audio_handoff["new_output"]
    dialog.audio_output_follower._devices.use("headphones")

    assert dialog.audio_output.device() == pending_output.device() == _FakeAudioDevice("headphones")
    assert pending_output.volume() == 0
    dialog._cancel_audio_handoff(remove_new_path=True)
    dialog.audio_output_follower._devices.use("speakers")
    assert dialog.audio_output.device().identifier == "speakers"
    assert pending_output.device().identifier == "headphones"


def test_inspection_refreshes_default_before_loading_another_preview(dialog_factory, tmp_path):
    dialog = dialog_factory([])
    # Loading media stays asynchronous, as it does in QMediaPlayer. This path
    # should select the new output before playback begins.
    dialog.player.mediaStatus = lambda: dialog.player.MediaStatus.LoadingMedia
    dialog.preview_audio_path = str(tmp_path / "preview.wav")
    (tmp_path / "preview.wav").touch()
    dialog.audio_output_follower._devices.default = _FakeAudioDevice("headphones")

    dialog._start_preview_playback(position_ms=500)

    assert dialog.audio_output.device().identifier == "headphones"
    assert dialog._pending_audio_start["base_position_ms"] == 500
