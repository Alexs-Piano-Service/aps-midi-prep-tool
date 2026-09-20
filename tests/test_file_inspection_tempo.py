import unittest

from aps_midi_prep_tool_app.main_window import (
    FluidSynthPlaybackProcess,
    MidiOutputWorker,
    _inspect_midi_bytes,
    _midi_meta_payload,
    _midi_channel_color,
    _midi_output_events,
    _midi_tick_for_seconds,
    _normalized_tempo_percent,
    _scale_midi_tempo_bytes,
    _scale_preview_timed_items,
    _tick_seconds_converter,
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


class FileInspectionTempoTests(unittest.TestCase):
    def test_type_one_conductor_tempo_matches_visual_rendered_and_rebased_live_timing(self):
        conductor = _type_zero_midi([
            (0, b"\xFF\x51\x03" + (428571).to_bytes(3, "big")),
        ])[14:]
        performance = _type_zero_midi([
            (0, b"\x90\x3C\x50"), (1024, b"\x80\x3C\x00"),
            (4096, b"\x90\x40\x48"), (4352, b"\x80\x40\x00"),
        ])[14:]
        source = (b"MThd\x00\x00\x00\x06\x00\x01\x00\x02\x01\x00"
                  + conductor + performance)
        inspected = _inspect_midi_bytes(source)
        self.assertEqual(len(inspected["notes"]), 2)
        self.assertAlmostEqual(inspected["duration"], 7.285707)
        rebased_events = _midi_output_events(
            FluidSynthPlaybackProcess._tempo_rebased_midi_bytes(source)
        )
        # Independent 140 BPM expectations at 256 PPQN. A default-tempo
        # override would incorrectly put the second onset at 8 seconds.
        expected_at_normal_speed = (0.0, 1.714284, 6.857136, 7.285707)
        for percent, expected_multiplier in ((100, 2.0), (50, 1.0)):
            with self.subTest(tempo_percent=percent):
                visual_notes = _scale_preview_timed_items(inspected["notes"], percent)
                visual_times = [note[key] for note in visual_notes
                                for key in ("start_sec", "end_sec")]
                rendered_events = _midi_output_events(_scale_midi_tempo_bytes(source, percent))
                command = FluidSynthPlaybackProcess._tempo_command(percent)
                live_multiplier = float(command.split()[1])
                self.assertEqual(live_multiplier, expected_multiplier)
                self.assertEqual([raw for _, raw in rebased_events],
                                 [raw for _, raw in rendered_events])
                self.assertEqual(len(rendered_events), 4)
                for index, base_seconds in enumerate(expected_at_normal_speed):
                    expected = base_seconds * 100 / percent
                    self.assertAlmostEqual(visual_times[index], expected)
                    self.assertAlmostEqual(rendered_events[index][0], expected)
                    self.assertAlmostEqual(rebased_events[index][0] / live_multiplier, expected)

    def test_explicit_initial_tempo_controls_inspected_note_times_and_duration(self):
        for mpqn in (250000, 444444, 1000000):
            with self.subTest(mpqn=mpqn):
                source = _type_zero_midi([
                    (0, b"\xFF\x51\x03" + mpqn.to_bytes(3, "big")),
                    (96, bytes([0x90, 60, 100])),
                    (192, bytes([0x80, 60, 0])),
                ])

                inspected = _inspect_midi_bytes(source)
                playback = _midi_output_events(source)

                self.assertAlmostEqual(inspected["notes"][0]["start_sec"], mpqn / 1000000)
                self.assertAlmostEqual(inspected["notes"][0]["end_sec"], 2 * mpqn / 1000000)
                self.assertAlmostEqual(inspected["duration"], 2 * mpqn / 1000000)
                self.assertAlmostEqual(inspected["notes"][0]["start_sec"], playback[0][0])
                self.assertAlmostEqual(inspected["notes"][0]["end_sec"], playback[1][0])

    def test_last_source_tempo_wins_when_changes_share_a_tick(self):
        for first, final in ((800000, 250000), (250000, 800000)):
            with self.subTest(first=first, final=final):
                convert = _tick_seconds_converter(
                    [(0, 500000), (0, first), (0, final), (96, 1000000), (96, 300000)], 96,
                )

                self.assertEqual(convert(0), 0)
                self.assertAlmostEqual(convert(96), final / 1000000)
                self.assertAlmostEqual(convert(192), final / 1000000 + 0.3)

    def test_future_tempo_change_leaves_default_tempo_before_its_tick(self):
        convert = _tick_seconds_converter([(96, 250000)], 96)

        self.assertAlmostEqual(convert(48), 0.25)
        self.assertAlmostEqual(convert(96), 0.5)
        self.assertAlmostEqual(convert(192), 0.75)

    def test_all_midi_channels_have_distinct_legend_colors(self):
        colors = [
            _midi_channel_color(channel).name()
            for channel in range(1, 17)
        ]

        self.assertEqual(len(set(colors)), 16)
        self.assertEqual(_midi_channel_color(1).name(), colors[0])
        self.assertEqual(_midi_channel_color(16).name(), colors[-1])

    def test_tempo_percentage_accepts_very_slow_preview_values(self):
        self.assertEqual(_normalized_tempo_percent(5), 5)
        self.assertEqual(_normalized_tempo_percent(10), 10)
        self.assertEqual(_normalized_tempo_percent(1), 5)
        self.assertEqual(_normalized_tempo_percent(500), 400)

    def test_realtime_fluidsynth_uses_tempo_multiplier_commands(self):
        self.assertEqual(
            FluidSynthPlaybackProcess._tempo_command(5),
            "player_tempo_int 0.100001",
        )
        self.assertEqual(
            FluidSynthPlaybackProcess._tempo_command(10),
            "player_tempo_int 0.200000",
        )
        self.assertEqual(
            FluidSynthPlaybackProcess._tempo_command(175),
            "player_tempo_int 3.500000",
        )
        self.assertEqual(
            FluidSynthPlaybackProcess._tempo_command(400),
            "player_tempo_int 8.000000",
        )

    def test_realtime_fluidsynth_rebases_preview_tempo(self):
        source = _type_zero_midi(
            [
                (0, b"\xFF\x51\x03\x07\xA1\x20"),
                (96, bytes([0x90, 60, 100])),
            ]
        )

        rebased = FluidSynthPlaybackProcess._tempo_rebased_midi_bytes(
            source
        )
        tempo_events = [
            int.from_bytes(payload, "big")
            for _tick, raw in _track_events(rebased)
            for meta_type, payload in [_midi_meta_payload(raw)]
            if meta_type == 0x51
        ]

        self.assertEqual(tempo_events, [1000000])

    def test_tempo_percentage_scales_every_recorded_tempo(self):
        source = _type_zero_midi(
            [
                (0, b"\xFF\x51\x03\x07\xA1\x20"),
                (96, b"\xFF\x51\x03\x0F\x42\x40"),
                (96, bytes([0x90, 60, 100])),
                (192, bytes([0x80, 60, 0])),
            ]
        )

        faster = _scale_midi_tempo_bytes(source, 200)
        tempo_events = [
            (tick, int.from_bytes(payload, "big"))
            for tick, raw in _track_events(faster)
            for meta_type, payload in [_midi_meta_payload(raw)]
            if meta_type == 0x51
        ]
        timed_channel_events = _midi_output_events(faster)

        self.assertEqual(tempo_events, [(0, 250000), (96, 500000)])
        self.assertEqual(
            [raw for _seconds, raw in timed_channel_events],
            [bytes([0x90, 60, 100]), bytes([0x80, 60, 0])],
        )
        self.assertAlmostEqual(timed_channel_events[0][0], 0.25)
        self.assertAlmostEqual(timed_channel_events[1][0], 0.75)

    def test_source_seconds_convert_to_ticks_across_tempo_changes(self):
        source = _type_zero_midi(
            [
                (0, b"\xFF\x51\x03\x07\xA1\x20"),
                (96, b"\xFF\x51\x03\x0F\x42\x40"),
                (192, bytes([0x90, 60, 100])),
            ]
        )

        self.assertEqual(_midi_tick_for_seconds(source, 0.25), 48)
        self.assertEqual(_midi_tick_for_seconds(source, 0.75), 120)

    def test_tempo_percentage_scales_the_default_midi_tempo(self):
        source = _type_zero_midi(
            [
                (96, bytes([0x90, 64, 90])),
                (192, bytes([0x80, 64, 0])),
            ]
        )

        slower = _scale_midi_tempo_bytes(source, 50)
        tempo_events = [
            (tick, int.from_bytes(payload, "big"))
            for tick, raw in _track_events(slower)
            for meta_type, payload in [_midi_meta_payload(raw)]
            if meta_type == 0x51
        ]
        timed_channel_events = _midi_output_events(slower)

        self.assertEqual(tempo_events, [(0, 1000000)])
        self.assertAlmostEqual(timed_channel_events[0][0], 1.0)
        self.assertAlmostEqual(timed_channel_events[1][0], 2.0)

    def test_visual_timing_is_scaled_without_mutating_source_notes(self):
        notes = [{"start_sec": 2.0, "end_sec": 5.0, "pitch": 60}]

        scaled = _scale_preview_timed_items(notes, 200)

        self.assertEqual(scaled[0]["start_sec"], 1.0)
        self.assertEqual(scaled[0]["end_sec"], 2.5)
        self.assertEqual(notes[0]["start_sec"], 2.0)
        self.assertEqual(notes[0]["end_sec"], 5.0)

    def test_midi_output_worker_accepts_live_tempo_changes(self):
        class _Output:
            def send_message(self, _message):
                pass

        worker = MidiOutputWorker(b"", "Test output", tempo_percent=100)
        worker.set_tempo_percent(10)

        tempo = worker._drain_commands(
            _Output(),
            100,
            {},
            {},
        )

        self.assertEqual(tempo, 10)


if __name__ == "__main__":
    unittest.main()
