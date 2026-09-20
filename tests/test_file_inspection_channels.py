import unittest

from aps_midi_prep_tool_app.main_window import (
    FileInspectionDialog,
    FluidSynthPlaybackProcess,
    MidiOutputWorker,
)


class _Button:
    def __init__(self):
        self.enabled = None

    def setEnabled(self, enabled):
        self.enabled = bool(enabled)


class FileInspectionChannelTests(unittest.TestCase):
    def test_fluidsynth_mutes_and_restores_channel_in_place(self):
        class _Process:
            available_channels = {5}
            _cc_command = staticmethod(
                FluidSynthPlaybackProcess._cc_command
            )
            _channel_mute_commands = staticmethod(
                FluidSynthPlaybackProcess._channel_mute_commands
            )

            def __init__(self):
                self.enabled_channels = {5}
                self.commands = []

            def _send_command(self, command):
                self.commands.append(command)

        process = _Process()
        FluidSynthPlaybackProcess.set_channel_enabled(
            process,
            5,
            False,
        )
        FluidSynthPlaybackProcess.set_channel_enabled(
            process,
            5,
            True,
            volume=86,
        )

        self.assertEqual(
            process.commands,
            [
                "cc 4 7 0",
                "cc 4 64 0",
                "cc 4 123 0",
                "cc 4 7 86",
            ],
        )
        self.assertEqual(process.enabled_channels, {5})

    def test_direct_midi_mutes_and_restores_recorded_channel_state(self):
        class _Output:
            def __init__(self):
                self.messages = []

            def send_message(self, message):
                self.messages.append(message)

        output = _Output()
        worker = MidiOutputWorker(
            b"",
            "Test output",
            enabled_channels={5},
            channel_levels={5: 50},
        )
        recorded_state = {
            5: {
                "bank_msb": 2,
                "bank_lsb": 3,
                "program": 7,
                "controllers": {7: 100, 10: 32},
            },
        }

        worker.set_channel_enabled(5, False)
        worker._drain_commands(
            output,
            100,
            {},
            recorded_state,
        )
        worker.set_channel_enabled(5, True)
        worker._drain_commands(
            output,
            100,
            {},
            recorded_state,
        )

        self.assertEqual(
            output.messages,
            [
                [0xB4, 64, 0],
                [0xB4, 123, 0],
                [0xB4, 0, 2],
                [0xB4, 32, 3],
                [0xC4, 7],
                [0xB4, 7, 50],
                [0xB4, 10, 32],
            ],
        )
        self.assertEqual(worker.enabled_channels, {5})

    def test_channel_checkbox_does_not_reset_active_playback(self):
        class _Dialog:
            def __init__(self):
                self.all_notes = [
                    {"channel": 5},
                    {"channel": 6},
                ]
                self.all_pedals = []
                self.current_notes = []
                self.current_pedals = []
                self.visible_notes = []
                self.visible_pedals = []
                self.midi_output_worker = object()
                self.live_synth_process = None
                self.preview_audio_path = ""
                self.preview_render_worker = None
                self.play_button = _Button()
                self.applied_live_selection = 0
                self.reset_calls = 0

            @staticmethod
            def _preview_channels():
                return {6}

            def _refresh_preview_timing(self):
                pass

            def _apply_live_channel_selection(self):
                self.applied_live_selection += 1

            def _reset_preview_for_filter_change(self):
                self.reset_calls += 1

            def _refresh_inspection_render_button(self):
                pass

        dialog = _Dialog()
        FileInspectionDialog._update_visible_channels(dialog)

        self.assertEqual(dialog.visible_notes, [{"channel": 6}])
        self.assertEqual(dialog.applied_live_selection, 1)
        self.assertEqual(dialog.reset_calls, 0)
        self.assertFalse(dialog.play_button.enabled)


if __name__ == "__main__":
    unittest.main()
