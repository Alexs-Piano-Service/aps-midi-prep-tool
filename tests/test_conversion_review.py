import io
import struct

import mido
import pytest

from aps_midi_prep_tool_app.conversion_review import ConversionReport, compare_music_bytes, inspect_music_bytes
from aps_midi_prep_tool_app.eseq_converter import (
    CC7_POLICY_DROP_EARLY_ZERO,
    CC7_POLICY_PLAYBACK_FIX_100,
    CC7_POLICY_REMOVE_STARTUP_MUTES,
    convert_eseq_bytes_to_midi_bytes,
    convert_midi_bytes_to_eseq_bytes,
    count_eseq_zero_volume_candidates,
)
from aps_midi_prep_tool_app.midi_type0_converter import _encode_vlq
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from aps_midi_prep_tool_app.xf_stripper import strip_xf_from_midi_bytes


def _track(events, end_tick):
    data = bytearray()
    last = 0
    for tick, raw in events:
        data.extend(_encode_vlq(tick - last))
        data.extend(raw)
        last = tick
    data.extend(_encode_vlq(end_tick - last) + b"\xff\x2f\x00")
    return b"MTrk" + len(data).to_bytes(4, "big") + data


def _midi(*tracks, format_type=0, division=384):
    return struct.pack(">4sIHHH", b"MThd", 6, format_type, len(tracks), division) + b"".join(tracks)


def _volume_song():
    return _midi(_track([
        (0, b"\xff\x03\x03Old"),
        (0, b"\xb0\x07\x00"),
        (0, b"\xb1\x07\x00"),
        (0, b"\xb1\x07\x50"),  # channel 2 restores volume before its note
        (0, b"\x90\x3c\x40"),
        (0, b"\x91\x40\x40"),
        (96, b"\xb0\x40\x30"),
        (192, b"\xb0\x40\x7f"),
        (384, b"\x80\x3c\x00"),
        (384, b"\x81\x40\x00"),
        (384, b"\xb0\x40\x00"),
    ], 768))


def test_summary_uses_tempo_map_for_duration_and_counts_notes_and_pedals():
    midi = _midi(_track([
        (0, b"\xff\x51\x03\x07\xa1\x20"),
        (0, b"\x92\x3c\x40"),
        (384, b"\xff\x51\x03\x0f\x42\x40"),
        (384, b"\xb2\x42\x7f"),
        (768, b"\x92\x3c\x00"),
    ], 768))

    summary = inspect_music_bytes(midi)

    assert summary.duration_seconds == pytest.approx(1.5)
    assert summary.notes == 1
    assert summary.channels == (3,)
    assert summary.pedals == {66: 1}


def test_type2_uses_each_sequences_own_tempo_and_reports_total():
    midi = _midi(
        _track([(0, b"\xff\x51\x03\x07\xa1\x20")], 384),
        _track([(0, b"\xff\x51\x03\x0f\x42\x40")], 384),
        format_type=2,
    )
    summary = inspect_music_bytes(midi)
    assert summary.duration_seconds == pytest.approx(1.5)
    assert "independent sequences, total" in summary.format


def test_eseq_conversion_removes_startup_mutes_by_default_and_can_preserve_for_analysis():
    eseq = convert_midi_bytes_to_eseq_bytes(_volume_song())
    assert count_eseq_zero_volume_candidates(eseq) == 1

    cleaned = convert_eseq_bytes_to_midi_bytes(eseq)
    cleaned_report = compare_music_bytes(eseq, cleaned)
    assert cleaned_report.after.zero_volume_events == 0
    assert not cleaned_report.notes_changed
    assert not cleaned_report.pedals_changed
    preserved = convert_eseq_bytes_to_midi_bytes(eseq, cc7_policy="preserve")
    fixed = convert_eseq_bytes_to_midi_bytes(eseq, cc7_policy=CC7_POLICY_PLAYBACK_FIX_100)
    preserved_report = compare_music_bytes(eseq, preserved)
    fixed_report = compare_music_bytes(eseq, fixed)

    assert preserved_report.before.zero_volume_events == preserved_report.after.zero_volume_events == 2
    assert not preserved_report.notes_changed
    assert not preserved_report.pedals_changed
    assert not preserved_report.channel_events_changed
    assert fixed_report.after.zero_volume_events == 1
    assert fixed_report.channel_events_changed
    assert not fixed_report.notes_changed
    assert not fixed_report.pedals_changed
    assert "Zero-volume CC7: 2 → 1" in fixed_report.to_text()


def test_mid_song_volume_off_is_preserved_unless_explicitly_changed():
    source = _midi(_track([
        (0, b"\xb0\x07\x28"),  # previous volume is 40, not 100
        (0, b"\x90\x3c\x40"),
        (96, b"\x80\x3c\x00"),
        (192, b"\xb0\x07\x00"),
        (240, b"\xb0\x07\x00"),  # repeated mute, potentially intentional
        (384, b"\x90\x40\x40"),
        (480, b"\x80\x40\x00"),
        (576, b"\xb0\x07\x50"),
        (672, b"\x90\x43\x40"),
        (768, b"\x80\x43\x00"),
        (864, b"\xb0\x07\x00"),  # ending mute must remain in every policy
    ], 960))
    eseq = convert_midi_bytes_to_eseq_bytes(source)
    assert count_eseq_zero_volume_candidates(eseq) == 2

    for policy, expected_volumes in (
        (CC7_POLICY_REMOVE_STARTUP_MUTES, [40, 0, 0, 80, 0]),
        ("preserve", [40, 0, 0, 80, 0]),
        (CC7_POLICY_PLAYBACK_FIX_100, [40, 100, 100, 80, 0]),
        (CC7_POLICY_DROP_EARLY_ZERO, [40, 80, 0]),
    ):
        output = convert_eseq_bytes_to_midi_bytes(eseq, cc7_policy=policy)
        messages = mido.merge_tracks(mido.MidiFile(file=io.BytesIO(output)).tracks)
        assert [message.value for message in messages
                if message.type == "control_change" and message.control == 7] == expected_volumes
        assert not compare_music_bytes(eseq, output).notes_changed


def test_startup_mute_cleanup_uses_each_parts_first_note_and_keeps_other_events():
    source = _midi(_track([
        (0, b"\xb0\x07\x00"),
        (0, b"\x91\x3c\x40"),
        (1, b"\xb1\x07\x00"),  # channel 2 already started: keep this mute
        (2, b"\x91\x40\x40"),
        (96, b"\x81\x3c\x00"),
        (96, b"\x81\x40\x00"),
        (1200, b"\xb0\x07\x00"),  # long lead-in, repeated startup mute
        (1400, b"\xb3\x07\x00"),
        (1400, b"\xb3\x07\x50"),  # startup zeros are removed even if restored
        (1400, b"\xb4\x07\x00"),
        (1500, b"\x94\x3c\x00"),  # zero-velocity note is not a first attack
        (2000, b"\x90\x3c\x40"),
        (2000, b"\x92\x3c\x40"),
        (2000, b"\xb2\x07\x00"),  # initialization at the first-note tick
        (2000, b"\x93\x3c\x40"),
        (2000, b"\x94\x3c\x40"),
        (2100, b"\x80\x3c\x00"),
        (2100, b"\x82\x3c\x00"),
        (2100, b"\x83\x3c\x00"),
        (2100, b"\x84\x3c\x00"),
        (2100, b"\xb0\x07\x00"),  # ending mute stays
    ], 2200))
    eseq = convert_midi_bytes_to_eseq_bytes(source, timing_policy="preserve")
    cleaned = convert_eseq_bytes_to_midi_bytes(eseq)
    preserved = convert_eseq_bytes_to_midi_bytes(eseq, cc7_policy="preserve")

    def events(payload):
        tick = 0
        result = []
        for message in mido.merge_tracks(mido.MidiFile(file=io.BytesIO(payload)).tracks):
            tick += message.time
            result.append((tick, message.copy(time=0)))
        return result

    actual = events(cleaned)
    assert [(message.channel, message.value) for _tick, message in actual
            if message.type == "control_change" and message.control == 7] == [(1, 0), (3, 80), (0, 0)]
    assert [(tick, message) for tick, message in actual
            if message.type != "control_change"] == [
        (tick, message) for tick, message in events(preserved) if message.type != "control_change"
    ]


def test_report_shows_removed_metadata_and_title_changes_without_claiming_note_changes():
    xf = b"\xff\x7f\x07\x43\x7b\x01\x31\x00\x7f\x7f"
    source = _midi(_track([(0, xf), (0, b"\x90\x3c\x40"), (384, b"\x80\x3c\x00")], 768))
    stripped, _ = strip_xf_from_midi_bytes(source)

    report = compare_music_bytes(source, stripped)

    assert report.removed_metadata == {"Sequencer-specific": 1}
    assert not report.notes_changed
    assert not report.pedals_changed
    assert not report.channel_events_changed
    assert report.as_dict()["before"]["notes"] == 1
    assert "Sequencer-specific: 1" in report.to_text()

    eseq = convert_midi_bytes_to_eseq_bytes(_volume_song())
    titled = convert_eseq_bytes_to_midi_bytes(eseq, title_override="Edited title")
    report = compare_music_bytes(eseq, titled)
    assert report.before.titles == ("Old",)
    assert report.after.titles == ("Edited title",)
    assert "'Old' → 'Edited title'" in report.to_text()


def test_note_on_zero_and_note_off_zero_are_equivalent_note_endings():
    before = _midi(_track([(0, b"\x90\x3c\x40"), (384, b"\x90\x3c\x00")], 384))
    after = _midi(_track([(0, b"\x90\x3c\x40"), (384, b"\x80\x3c\x00")], 384))

    report = compare_music_bytes(before, after)

    assert not report.notes_changed
    assert report.channel_events_changed  # encoding is still recorded separately


def test_report_includes_removed_tempo_signatures_and_sysex():
    metadata = [
        b"\xff\x51\x03\x07\xa1\x20",
        b"\xff\x58\x04\x04\x02\x18\x08",
        b"\xff\x59\x02\x01\x00",
        b"\xf0\x08\x43\x10\x4c\x00\x00\x7e\x00\xf7",
    ]
    notes = [(0, b"\x90\x3c\x40"), (384, b"\x80\x3c\x00")]
    before = _midi(_track([(0, raw) for raw in metadata] + notes, 384))
    after = _midi(_track(notes, 384))

    report = compare_music_bytes(before, after)

    assert report.before.xg_detected
    assert report.removed_metadata == {"Tempo": 1, "Time Signature": 1, "Key Signature": 1, "SysEx": 1}
    assert not report.notes_changed  # the removed explicit tempo matched MIDI's default

    eseq = convert_midi_bytes_to_eseq_bytes(before, timing_policy="preserve")
    converted = compare_music_bytes(before, eseq)
    assert "Tempo" not in converted.removed_metadata
    assert "Time Signature" not in converted.removed_metadata
    assert "SysEx" not in converted.removed_metadata
    assert converted.removed_metadata == {"Key Signature": 1}


def test_conversion_report_allows_half_tick_rounding_at_slow_tempos():
    midi = _midi(_track([
        (0, b"\xff\x51\x03\x1e\x84\x80"),  # 30 BPM
        (1, b"\x90\x3c\x40"),
        (961, b"\x80\x3c\x00"),
    ], 1920), division=960)
    eseq = convert_midi_bytes_to_eseq_bytes(midi, timing_policy="preserve")

    report = compare_music_bytes(midi, eseq)
    strict_report = compare_music_bytes(midi, eseq, tolerance_seconds=0.0001)

    assert not report.notes_changed
    assert strict_report.notes_changed


def test_legacy_report_explains_timing_without_hiding_changes_or_claiming_changed_values():
    source = _volume_song()
    converted = convert_midi_bytes_to_eseq_bytes(source)

    report = compare_music_bytes(source, converted)

    assert report.legacy_timing
    assert report.notes_changed
    assert report.pedals_changed
    assert report.channel_events_changed
    assert report.channel_payload_changed is True
    assert report.pedal_channels_routed == (64,)
    assert report.other_channel_payload_changed is False
    assert report.before.notes == report.after.notes == 2
    assert report.before.zero_volume_events == report.after.zero_volume_events == 2
    assert "Timing follows MID2ESEQ: a 117 BPM clock" in report.to_text()
    assert "Continuous pedals moved from channel 1 to channel 3: CC64." in report.to_text()
    assert report.yamaha_pedal_controllers == (64,)
    assert report.pedal_binary_events_added == 2
    assert report.expected_channel_events_changed is False
    assert "MIDI channel events match the Yamaha pedal conversion and expected timing." in report.to_text()
    assert "message values are unchanged" not in report.to_text()


def test_legacy_envelope_does_not_hide_a_changed_velocity_or_volume_fix():
    source = _volume_song()
    converted = convert_midi_bytes_to_eseq_bytes(source)
    assert converted.count(b"\x90\x3c\x40") == 1
    changed_velocity = converted.replace(b"\x90\x3c\x40", b"\x90\x3c\x41", 1)

    report = compare_music_bytes(source, changed_velocity)
    fixed_volume = compare_music_bytes(source, convert_midi_bytes_to_eseq_bytes(source, cc7_policy=CC7_POLICY_PLAYBACK_FIX_100))

    assert report.legacy_timing
    assert report.notes_changed
    assert report.channel_payload_changed is True
    assert "message values are unchanged" not in report.to_text()
    assert "MIDI channel messages changed (notes, controllers, or instruments)." in report.to_text()
    assert fixed_volume.legacy_timing
    assert fixed_volume.channel_payload_changed is True
    assert fixed_volume.before.zero_volume_events == 2
    assert fixed_volume.after.zero_volume_events == 1


def test_report_serialization_keeps_legacy_timing_and_loads_previous_report_schema():
    source = _volume_song()
    report = compare_music_bytes(source, convert_midi_bytes_to_eseq_bytes(source))
    saved = report.as_dict()

    assert ConversionReport.from_dict(saved) == report
    saved.pop("legacy_timing")
    saved.pop("channel_payload_changed")
    saved.pop("pedal_channels_routed")
    saved.pop("other_channel_payload_changed")
    for field in ("yamaha_pedal_controllers", "pedal_binary_events_added", "pedal_duplicate_events_removed", "expected_channel_events_changed"):
        saved.pop(field)
    saved.pop("all_sound_off_removed")
    previous = ConversionReport.from_dict(saved)

    assert previous.legacy_timing is False
    assert previous.channel_payload_changed is None
    assert previous.all_sound_off_removed is False
    assert previous.notes_changed and previous.channel_events_changed
    assert "message values are unchanged" not in previous.to_text()


@pytest.mark.parametrize("code", [language.code for language in SUPPORTED_LANGUAGES])
def test_report_explains_lost_all_sound_off_in_every_language(code):
    before = _midi(_track([(0, b"\x90\x3c\x64"), (384, b"\xb0\x78\x00")], 384))
    after = _midi(_track([(0, b"\x90\x3c\x64"), (384, b"\x80\x3c\x00")], 384))
    report = compare_music_bytes(before, after)
    source = "All Sound Off (CC120) commands were removed; immediate muting may be lost."

    assert report.all_sound_off_removed
    assert translate_text(source, code) in report.to_text(code)
    if code != "en":
        assert source not in report.to_text(code)
    unchanged = compare_music_bytes(before, before)
    assert not unchanged.all_sound_off_removed
    assert translate_text(source, code) not in unchanged.to_text(code)


def test_preserved_timing_is_not_described_as_mid2eseq_compatibility():
    source = _volume_song()
    converted = convert_midi_bytes_to_eseq_bytes(source, timing_policy="preserve", pedal_policy="preserve")

    report = compare_music_bytes(source, converted)

    assert not report.legacy_timing
    assert not report.notes_changed
    assert not report.channel_events_changed
    assert report.channel_payload_changed is False
    assert "MID2ESEQ" not in report.to_text()


@pytest.mark.parametrize("hidden", [False, True])
def test_startup_cleanup_is_automatic_even_when_conversion_prompt_is_hidden(tmp_path, monkeypatch, hidden):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QDialogButtonBox, QLabel, QWidget
    from aps_midi_prep_tool_app.main_window import MidiTitleWindow

    app = QApplication.instance() or QApplication([])

    class Settings:
        def value(self, key, default=None, **kwargs):
            return hidden if key == "hidden" else default

        def setValue(self, *args):
            pass

        def sync(self):
            pass

    class Window(QWidget):
        SETTING_ESEQ_TO_MIDI_TRIM_TITLE_SPACES = "trim"
        SETTING_SKIP_ESEQ_TO_MIDI_CONVERSION_PROMPT = "hidden"
        settings = Settings()
        _eseq_conversion_cc7_policy = "preserve"
        shown = False

        def _long_midi_filenames_enabled(self):
            return False

        def _set_long_midi_filenames_enabled(self, value):
            pass

        def _lt(self, text, **values):
            return text.format(**values)

        def _t(self, key):
            return key

        def _make_dialog_button_box(self, buttons, dialog):
            return QDialogButtonBox(buttons, dialog)

        def _exec_child_dialog(self, dialog):
            self.shown = True
            assert any("recommended" in label.text() for label in dialog.findChildren(QLabel))
            preservation = dialog.findChild(QCheckBox, "preserveOriginalVolumeControls")
            assert preservation is not None
            assert not preservation.isChecked()
            return QDialog.Accepted

    window = Window()
    try:
        result = MidiTitleWindow._confirm_eseq_to_midi_conversion(
            window, title="Convert", message="Review",
        )
        assert result[0]
        assert window.shown is not hidden
        assert window._eseq_conversion_cc7_policy == CC7_POLICY_REMOVE_STARTUP_MUTES
        # This is the policy used for the staged conversion, including when the
        # previously hidden dialog returns without asking any questions.
        eseq = convert_midi_bytes_to_eseq_bytes(_volume_song())
        converted = convert_eseq_bytes_to_midi_bytes(eseq, cc7_policy=window._eseq_conversion_cc7_policy)
        assert inspect_music_bytes(converted).zero_volume_events == 0
    finally:
        window.close()
    assert app is not None
