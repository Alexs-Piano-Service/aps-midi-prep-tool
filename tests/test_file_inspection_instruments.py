import unittest

from aps_midi_prep_tool_app.main_window import (
    FileInspectionDialog,
    FluidSynthPlaybackProcess,
    MidiOutputWorker,
    _filter_midi_bytes_to_channels,
    _recorded_program_state_at_seconds,
)
from aps_midi_prep_tool_app.midi_type0_converter import (
    _encode_vlq,
    _parse_midi_chunks,
    _parse_track_events,
)


def _type_zero_midi(events):
    track = bytearray()
    previous_tick = 0
    for tick, raw in events:
        track.extend(_encode_vlq(tick - previous_tick))
        track.extend(raw)
        previous_tick = tick
    track.extend(b"\x00\xFF\x2F\x00")
    return (
        b"MThd"
        + (6).to_bytes(4, "big")
        + (0).to_bytes(2, "big")
        + (1).to_bytes(2, "big")
        + (96).to_bytes(2, "big")
        + b"MTrk"
        + len(track).to_bytes(4, "big")
        + bytes(track)
    )


def _track_events(midi_bytes):
    _header_end, _format_type, _track_count, chunks = (
        _parse_midi_chunks(midi_bytes)
    )
    track = next(chunk for chunk in chunks if chunk["id"] == b"MTrk")
    events, _end_tick = _parse_track_events(
        midi_bytes[track["data_start"]:track["data_end"]]
    )
    return [(tick, raw) for tick, _order, raw in events]


class FileInspectionInstrumentTests(unittest.TestCase):
    def test_realtime_fluidsynth_uses_channel_program_commands(self):
        self.assertEqual(
            FluidSynthPlaybackProcess._program_command(1, 40),
            "select 0 1 0 40",
        )
        self.assertEqual(
            FluidSynthPlaybackProcess._program_command(10, 8),
            "select 9 1 128 8",
        )

    def test_recorded_program_state_can_be_restored_mid_song(self):
        source = _type_zero_midi(
            [
                (0, bytes([0xB0, 0, 2])),
                (0, bytes([0xB0, 32, 3])),
                (0, bytes([0xC0, 7])),
                (96, bytes([0xC0, 40])),
                (192, bytes([0x90, 60, 100])),
            ]
        )

        self.assertEqual(
            _recorded_program_state_at_seconds(source, 1, 0.25),
            (259, 7),
        )
        self.assertEqual(
            _recorded_program_state_at_seconds(source, 1, 0.75),
            (259, 40),
        )

    def test_realtime_soundfont_program_change_is_sent_immediately(self):
        class _Combo:
            def currentData(self):
                return 40

        class _LiveSynth:
            def __init__(self):
                self.calls = []

            def set_program(self, channel, program, *, bank=None):
                self.calls.append((channel, program, bank))

        class _Button:
            def setEnabled(self, _enabled):
                pass

        class _Dialog:
            def __init__(self):
                self.channel_program_combos = {5: _Combo()}
                self.channel_program_overrides = {}
                self.midi_output_worker = None
                self.live_synth_process = _LiveSynth()
                self._live_recorded_program_channels = set()
                self.preview_audio_path = ""
                self.preview_render_worker = None
                self.visible_notes = [object()]
                self.play_button = _Button()

        dialog = _Dialog()
        FileInspectionDialog._on_channel_program_changed(dialog, 5)

        self.assertEqual(dialog.channel_program_overrides, {5: 40})
        self.assertEqual(
            dialog.live_synth_process.calls,
            [(5, 40, None)],
        )

    def test_rendered_fallback_instrument_change_does_not_swap_source(self):
        class _Combo:
            def currentData(self):
                return 40

        class _Button:
            def setEnabled(self, _enabled):
                pass

        class _Label:
            def setText(self, _text):
                pass

            def setVisible(self, _visible):
                pass

        class _Dialog:
            def __init__(self):
                self.channel_program_combos = {5: _Combo()}
                self.channel_program_overrides = {}
                self.midi_output_worker = None
                self.live_synth_process = None
                self.preview_audio_path = "current-preview.wav"
                self.preview_render_worker = None
                self._preview_audio_stale = False
                self.preview_progress_label = _Label()
                self.visible_notes = [object()]
                self.play_button = _Button()
                self.t = lambda text: text

            def _smooth_playback_is_active(self):
                return True

        dialog = _Dialog()
        FileInspectionDialog._on_channel_program_changed(dialog, 5)

        self.assertEqual(dialog.channel_program_overrides, {5: 40})
        self.assertTrue(dialog._preview_audio_stale)
        self.assertEqual(dialog.preview_audio_path, "current-preview.wav")

    def test_preview_override_replaces_programs_on_only_one_channel(self):
        source = _type_zero_midi(
            [
                (0, bytes([0xC0, 0])),
                (0, bytes([0xC1, 5])),
                (12, bytes([0xC0, 40])),
                (24, bytes([0x90, 60, 100])),
                (48, bytes([0x80, 60, 0])),
            ]
        )

        preview = _filter_midi_bytes_to_channels(
            source,
            {1, 2},
            program_overrides={1: 24},
        )
        events = _track_events(preview)
        program_events = [
            (tick, raw)
            for tick, raw in events
            if raw and (raw[0] & 0xF0) == 0xC0
        ]

        self.assertEqual(
            program_events,
            [
                (0, bytes([0xC0, 24])),
                (0, bytes([0xC1, 5])),
            ],
        )
        self.assertIn((0, bytes([0xB0, 0, 0])), events)
        self.assertIn((0, bytes([0xB0, 32, 0])), events)
        self.assertIn((24, bytes([0x90, 60, 100])), events)
        self.assertIn((48, bytes([0x80, 60, 0])), events)

    def test_invalid_preview_overrides_are_ignored(self):
        source = _type_zero_midi([(0, bytes([0xC0, 7]))])

        preview = _filter_midi_bytes_to_channels(
            source,
            {1},
            program_overrides={0: 10, 1: 128, 17: 20},
        )

        self.assertIn((0, bytes([0xC0, 7])), _track_events(preview))

    def test_midi_output_worker_switches_and_restores_program_live(self):
        class _Output:
            def __init__(self):
                self.messages = []

            def send_message(self, message):
                self.messages.append(message)

        output = _Output()
        worker = MidiOutputWorker(
            b"",
            "Test output",
            program_overrides={1: 40},
        )
        overrides = {1: 40}
        recorded_state = {
            1: {"bank_msb": 2, "bank_lsb": 3, "program": 7},
        }

        worker.set_program_override(1, 48)
        tempo = worker._drain_commands(
            output,
            100,
            overrides,
            recorded_state,
        )
        worker.set_program_override(1, None)
        worker._drain_commands(
            output,
            tempo,
            overrides,
            recorded_state,
        )

        self.assertEqual(
            output.messages,
            [
                [0xB0, 0, 0],
                [0xB0, 32, 0],
                [0xC0, 48],
                [0xB0, 0, 2],
                [0xB0, 32, 3],
                [0xC0, 7],
            ],
        )
        self.assertEqual(overrides, {})


if __name__ == "__main__":
    unittest.main()
