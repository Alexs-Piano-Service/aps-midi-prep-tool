"""Default-device changes update outputs without changing playback intent."""

from dataclasses import dataclass
import pytest

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal

from aps_midi_prep_tool_app import audio_output_routing


@dataclass(frozen=True)
class _FakeAudioDevice:
    identifier: str = ""

    def isNull(self):
        return not self.identifier


class _FakeMediaDevices(QObject):
    audioOutputsChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.default = _FakeAudioDevice("speakers")

    def defaultAudioOutput(self):
        return self.default

    def use(self, name):
        self.default = _FakeAudioDevice(name)
        self.audioOutputsChanged.emit()


class _FakeAudioOutput(QObject):
    def __init__(self, parent=None, *, device="speakers"):
        super().__init__(parent)
        self._device = _FakeAudioDevice(device)
        self.device_changes = []
        self.volume = .7
        self.muted = False

    def device(self):
        return self._device

    def setDevice(self, device):
        self.device_changes.append(device.identifier)
        self._device = device


class _FakePlayer(QObject):
    def __init__(self, output, *, state="playing"):
        super().__init__()
        self.output = output
        self.output_changes = []
        self.state = state
        self.position = 14321
        self.source = "prepared-preview.wav"

    def audioOutput(self):
        return self.output

    def setAudioOutput(self, output):
        self.output_changes.append(output)
        self.output = output


def _follower(monkeypatch):
    monkeypatch.setattr(audio_output_routing, "QMediaDevices", _FakeMediaDevices)
    return audio_output_routing.DefaultAudioOutputFollower()


def test_system_default_changes_rebind_all_active_outputs(monkeypatch):
    follower = _follower(monkeypatch)
    active = _FakeAudioOutput()
    crossfade = _FakeAudioOutput()
    crossfade.volume = 0
    active.muted = True
    follower.add_output(active)
    follower.add_output(crossfade)

    follower._devices.use("headphones")

    assert active.device().identifier == crossfade.device().identifier == "headphones"
    assert active.volume == .7 and active.muted is True
    assert crossfade.volume == 0


def test_unrelated_device_event_does_not_reset_current_output(monkeypatch):
    follower = _follower(monkeypatch)
    output = _FakeAudioOutput()
    follower.add_output(output)
    follower._devices.use("speakers")
    follower.refresh()
    assert output.device_changes == []


def test_refresh_before_play_catches_a_missed_device_notification(monkeypatch):
    follower = _follower(monkeypatch)
    output = _FakeAudioOutput()
    follower.add_output(output)
    follower._devices.default = _FakeAudioDevice("headphones")
    follower.refresh()
    assert output.device_changes == ["headphones"]


def test_no_device_waits_for_reconnection_without_setting_invalid_output(monkeypatch):
    follower = _follower(monkeypatch)
    output = _FakeAudioOutput()
    follower.add_output(output)
    follower._devices.use("")
    assert output.device_changes == []
    follower._devices.use("headphones")
    assert output.device_changes == ["headphones"]


@pytest.mark.parametrize("state", ["playing", "paused", "stopped"])
def test_same_device_reconnection_reopens_sink_without_changing_playback(monkeypatch, state):
    follower = _follower(monkeypatch)
    output = _FakeAudioOutput()
    player = _FakePlayer(output, state=state)
    follower.add_output(output, player)
    follower._devices.use("")
    follower._devices.use("speakers")
    assert player.output_changes == [None, output]
    assert player.state == state
    assert player.position == 14321
    assert player.source == "prepared-preview.wav"
    assert output.volume == .7
    follower._devices.use("speakers")
    follower.refresh()
    assert player.output_changes == [None, output]


def test_reconnection_does_not_reattach_an_intentionally_detached_player(monkeypatch):
    follower = _follower(monkeypatch)
    output = _FakeAudioOutput()
    player = _FakePlayer(output)
    follower.add_output(output, player)
    follower._devices.use("")
    player.setAudioOutput(None)
    follower._devices.use("speakers")
    assert player.output_changes == [None]


def test_removed_outputs_are_not_updated_and_can_be_added_again(monkeypatch):
    follower = _follower(monkeypatch)
    output = _FakeAudioOutput()
    follower.add_output(output)
    follower.remove_output(output)
    follower._devices.use("headphones")
    assert output.device_changes == []
    follower.add_output(output)
    assert output.device_changes == ["headphones"]
    assert len(follower._outputs) == 1


def test_deleted_crossfade_outputs_are_forgotten(monkeypatch):
    follower = _follower(monkeypatch)
    output = _FakeAudioOutput()
    follower.add_output(output)
    output.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not follower._outputs
    follower._devices.use("headphones")
