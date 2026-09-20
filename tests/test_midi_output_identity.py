"""Hot-plugged MIDI devices must never inherit another device's selection."""

import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton, QWidget

from aps_midi_prep_tool_app import main_window


BACKEND = 3
NOTE = [0x90, 60, 100]


class _Dialog(QWidget):
    refresh_midi_outputs = main_window.FileInspectionDialog.refresh_midi_outputs
    _start_midi_output_playback = main_window.FileInspectionDialog._start_midi_output_playback

    def __init__(self):
        super().__init__()
        self.output_combo = QComboBox(self)
        self.play_button = QPushButton(self)
        self.refresh_outputs_button = QPushButton(self)
        self.midi_output_worker = None
        self.errors = []
        self.started = []

    t = staticmethod(lambda text: text)
    _on_output_changed = lambda self: None
    _available_preview_channels = lambda self: {1}
    _filtered_midi_bytes_for_preview = lambda self, **kwargs: b"test fixture"
    _slider_base_seconds = lambda self: 0
    _preview_tempo_percent = lambda self: 100
    _effective_channel_program_overrides = lambda self, channels: {}
    _preview_channels = lambda self: {1}
    _effective_channel_levels = lambda self, channels: {}
    _on_midi_output_finished = lambda self: None

    def _on_midi_output_started(self, *args):
        self.started.append(args)

    def _on_midi_output_failed(self, message):
        self.errors.append(message)


class _Output:
    def __init__(self, ports, *, current_api=BACKEND, after_open=None):
        self.ports = list(ports)
        self.current_api = current_api
        self.after_open = after_open
        self.opened = []
        self.messages = []
        self.closed = False

    def get_current_api(self):
        return self.current_api

    def get_ports(self):
        return list(self.ports)

    def open_port(self, index):
        self.opened.append((index, self.ports[index]))
        if self.after_open is not None:
            self.ports = list(self.after_open)

    def send_message(self, message):
        self.messages.append(message)

    def close_port(self):
        self.closed = True


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def environment(application, monkeypatch):
    outputs = []
    state = SimpleNamespace(
        ports=["USB MIDI interface", "Disklavier", "Virtual MIDI port"],
        backend=BACKEND,
        after_open=None,
        warnings=[],
        requested_backends=[],
        actual_backend=BACKEND,
    )
    monkeypatch.setattr(
        main_window, "_midi_output_ports",
        lambda: ([(state.backend, name) for name in state.ports], ""),
    )
    monkeypatch.setattr(
        main_window.QMessageBox, "warning",
        lambda parent, title, message: state.warnings.append((title, message)),
    )
    monkeypatch.setattr(main_window, "_midi_output_events", lambda data: [(0, bytes(NOTE))])
    # Exercise the real worker and its signals without timing dependence.
    monkeypatch.setattr(main_window.MidiOutputWorker, "start", lambda worker: worker.run())

    def midi_out(*, rtapi=None):
        state.requested_backends.append(rtapi)
        output = _Output(
            state.ports, current_api=state.actual_backend, after_open=state.after_open,
        )
        outputs.append(output)
        return output

    monkeypatch.setitem(sys.modules, "rtmidi", SimpleNamespace(MidiOut=midi_out))
    dialog = _Dialog()
    dialog.refresh_midi_outputs()
    dialog.output_combo.setCurrentIndex(2)
    assert dialog.output_combo.currentData() == ("midi", BACKEND, "Disklavier")
    yield dialog, state, outputs
    dialog.close()
    dialog.deleteLater()
    application.processEvents()


def test_refresh_and_playback_keep_selected_device_after_reordering(environment):
    dialog, state, outputs = environment
    state.ports = ["Disklavier", "Virtual MIDI port"]

    dialog.refresh_midi_outputs()
    assert dialog.output_combo.currentData() == ("midi", BACKEND, "Disklavier")
    assert dialog.output_combo.currentText() == "Disklavier"
    dialog._start_midi_output_playback()

    assert outputs[0].opened == [(0, "Disklavier")]
    assert NOTE in outputs[0].messages
    assert outputs[0].closed
    assert state.requested_backends == [BACKEND]
    assert not state.warnings and not dialog.errors


def test_playback_resolves_device_reordered_since_selection_without_refresh(environment):
    dialog, state, outputs = environment
    state.ports = ["Disklavier", "Virtual MIDI port", "USB MIDI interface"]

    dialog._start_midi_output_playback()

    assert outputs[0].opened == [(0, "Disklavier")]
    assert NOTE in outputs[0].messages
    assert not dialog.errors


@pytest.mark.parametrize("ports, message_fragment", [
    (["USB MIDI interface", "Virtual MIDI port"], "no longer available"),
    (["Disklavier", "Disklavier", "Virtual MIDI port"], "shared by multiple devices"),
])
def test_refresh_falls_back_to_audio_when_identity_is_missing_or_ambiguous(
    environment, ports, message_fragment,
):
    dialog, state, outputs = environment
    state.ports = ports

    dialog.refresh_midi_outputs()
    dialog._start_midi_output_playback()

    assert dialog.output_combo.currentData() == ("audio", -1)
    assert message_fragment in state.warnings[0][1]
    assert "Audio preview has been selected" in state.warnings[0][1]
    assert not outputs
    for row in range(1, dialog.output_combo.count()):
        item = dialog.output_combo.model().item(row)
        if ports.count(item.text()) > 1:
            assert not item.isEnabled()
            assert "Multiple MIDI outputs" in item.toolTip()


@pytest.mark.parametrize("ports, message_fragment", [
    (["USB MIDI interface", "Virtual MIDI port"], "no longer available"),
    (["Disklavier", "Disklavier", "Virtual MIDI port"], "Multiple MIDI outputs"),
])
def test_playback_refuses_missing_or_duplicate_identity_without_refresh(
    environment, ports, message_fragment,
):
    dialog, state, outputs = environment
    state.ports = ports

    dialog._start_midi_output_playback()

    assert message_fragment in dialog.errors[0]
    assert not outputs[0].opened
    assert not outputs[0].messages
    assert outputs[0].closed
    assert not dialog.started


def test_change_during_open_closes_port_without_sending_any_message(environment):
    dialog, state, outputs = environment
    state.after_open = ["Disklavier", "Virtual MIDI port"]

    dialog._start_midi_output_playback()

    assert "list changed while opening" in dialog.errors[0]
    assert outputs[0].opened == [(1, "Disklavier")]
    assert not outputs[0].messages
    assert outputs[0].closed
    assert not dialog.started


def test_same_name_on_a_different_backend_does_not_inherit_selection(environment):
    dialog, state, outputs = environment
    state.backend += 1

    dialog.refresh_midi_outputs()

    assert dialog.output_combo.currentData() == ("audio", -1)
    assert "no longer available" in state.warnings[0][1]
    assert not outputs


def test_worker_refuses_backend_substitution(environment):
    dialog, state, outputs = environment
    state.actual_backend += 1

    dialog._start_midi_output_playback()

    assert "no longer available" in dialog.errors[0]
    assert not outputs[0].opened
    assert not outputs[0].messages


def test_enumeration_records_backend_and_exact_name(monkeypatch):
    output = _Output(["Disklavier", "Disklavier", "USB MIDI"])
    monkeypatch.setitem(sys.modules, "rtmidi", SimpleNamespace(MidiOut=lambda: output))

    ports, error = main_window._midi_output_ports()

    assert error == ""
    assert ports == [(BACKEND, "Disklavier"), (BACKEND, "Disklavier"), (BACKEND, "USB MIDI")]
