import struct

import pytest

from aps_midi_prep_tool_app.conversion_review import ConversionReport, compare_music_bytes, inspect_music_bytes
from aps_midi_prep_tool_app.eseq_converter import (
    CC7_POLICY_PLAYBACK_FIX_100,
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


def test_eseq_conversion_preserves_zero_volume_unless_playback_fix_is_requested():
    eseq = convert_midi_bytes_to_eseq_bytes(_volume_song())
    assert count_eseq_zero_volume_candidates(eseq) == 1

    preserved = convert_eseq_bytes_to_midi_bytes(eseq)
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


@pytest.mark.parametrize("choose_fix", [False, True])
def test_detected_volume_condition_requires_fresh_choice_even_when_prompt_was_hidden(tmp_path, monkeypatch, choose_fix):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QDialogButtonBox, QWidget
    from aps_midi_prep_tool_app.main_window import MidiTitleWindow

    app = QApplication.instance() or QApplication([])
    source = tmp_path / "song.fil"
    source.write_bytes(convert_midi_bytes_to_eseq_bytes(_volume_song()))

    class Settings:
        def value(self, key, default=None, **kwargs):
            return key == "hidden"

        def setValue(self, *args):
            pass

        def sync(self):
            pass

    class Window(QWidget):
        SETTING_ESEQ_TO_MIDI_TRIM_TITLE_SPACES = "trim"
        SETTING_SKIP_ESEQ_TO_MIDI_CONVERSION_PROMPT = "hidden"
        settings = Settings()
        _eseq_conversion_cc7_policy = CC7_POLICY_PLAYBACK_FIX_100
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
            checkbox = next(box for box in dialog.findChildren(QCheckBox) if box.text().startswith("Set these"))
            assert not checkbox.isChecked()
            checkbox.setChecked(choose_fix)
            return QDialog.Accepted

    window = Window()
    try:
        result = MidiTitleWindow._confirm_eseq_to_midi_conversion(
            window, title="Convert", message="Review", source_paths=[source],
        )
        assert result[0]
        assert window.shown
        assert window._eseq_conversion_cc7_policy == ("playback_fix_100" if choose_fix else "preserve")
    finally:
        window.close()
    assert app is not None
