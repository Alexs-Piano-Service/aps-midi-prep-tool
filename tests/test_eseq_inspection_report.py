"""Source-header facts must survive preview conversion and language changes."""

import pytest

from aps_midi_prep_tool_app.eseq_converter import convert_eseq_bytes_to_midi_bytes
from aps_midi_prep_tool_app.eseq_inspection_report import format_eseq_header_details
from aps_midi_prep_tool_app.main_window import _inspect_midi_bytes
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text

# Reuse the existing inert audio backend and real FileInspectionDialog fixture.
from test_file_inspection_actions import application, dialog_factory, _item, _midi


def _source():
    header = bytearray(0x77)
    header[0] = 0xFE
    header[7:15] = b"COM-ESEQ"
    header[0x19] = 0x40
    header[0x1B:0x1F] = (0x50).to_bytes(4, "little")
    header[0x24] = 31  # A conflicting mirror must not override normal FIL 0x33.
    header[0x33] = 0
    header[0x34:0x36] = bytes((3, 8))
    header[0x37:0x3B] = (384).to_bytes(4, "little")
    header[0x3B:0x41] = bytes.fromhex("1c0d33200c00")
    header[0x45] = 1
    header[0x4D] = 0x80  # Undecoded historical flag, not a boolean claim.
    header[0x4F] = 0x80
    header[0x50] = 1
    header[0x51] = 1
    header[0x53] = 0x41
    header[0x54:0x56] = b"\x09\x00"  # Raw header says channels 1 and 4.
    header[0x56] = 1
    header[0x57:0x77] = b"Original header title".ljust(32, b" ")
    return bytes(header) + bytes.fromhex(
        "f100 fb500f 903c50 f40003 fb7403 803c00 f2"
    )


def test_report_distinguishes_raw_header_startup_factor_and_stream_facts():
    source = _source()
    report = format_eseq_header_details(source, source_label="SONG.FIL")
    assert "Header tempo [0x33]: 0x00 → 117 BPM (512820 µs/quarter note)" in report
    assert "Tempo mirror [0x24]: 0x1F" in report
    assert "Startup tempo: 234.000 BPM (256410 µs/quarter note)" in report
    assert "Tick 0: FB 2000 → 234.000 BPM" in report
    assert "Tick 384: FB 500 → 58.500 BPM" in report
    assert "Header time signature: 3/8" in report
    assert "Ensemble / extra note parts [0x4D]: 80 — Unknown flag value" in report
    assert "Detailed pedal flag [0x51]: 01 — Set" in report
    assert "Note-channel mask [0x54]: 09 00 — 1, 4" in report
    assert "Note channels in stream: 1" in report
    assert "Counter display [0x56]: 01 — Measure" in report
    assert "Unresolved header byte [0x53]: 41 — Not interpreted" in report
    assert "Timing bytes (album-specific) [0x3B]: 1C 0D 33 20 0C 00 — Not interpreted" in report
    assert source == _source()


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_file_details_show_original_header_in_every_language_without_changing_preview(
    dialog_factory, tmp_path, language,
):
    path = tmp_path / "ORIGINAL.FIL"
    source = _source()
    path.write_bytes(source)
    dialog = dialog_factory([_item(path)], language=language,
                            edit_callback=lambda *_args: pytest.fail("Unexpected edit"))
    report = dialog.details_box.toPlainText()
    expected = format_eseq_header_details(source, source_label=path.name, language_code=language)
    assert report.startswith(expected + "\n\n" + translate_text("Decoded MIDI preview:", language))
    assert "Original header title" in report
    assert "0x33" in report and "512820" in report and "256410" in report
    assert translate_text("Unknown flag value", language) in report
    assert translate_text("Measure", language) in report
    assert not dialog.convert_type0_button.isEnabled()
    assert not dialog.merge_piano_button.isEnabled()
    midi = convert_eseq_bytes_to_midi_bytes(source, include_conversion_text=False)
    expected_preview = _inspect_midi_bytes(midi)
    assert dialog.current_midi_bytes == midi
    assert dialog.all_notes == expected_preview["notes"]
    assert dialog.current_duration == expected_preview["duration"]
    assert path.read_bytes() == source
    if language != "en":
        assert "E-SEQ file details" not in report
        assert "Unknown flag value" not in report


def test_switching_back_to_midi_clears_previous_eseq_header(dialog_factory, tmp_path):
    eseq = tmp_path / "SONG.FIL"
    midi = tmp_path / "OTHER.MID"
    eseq.write_bytes(_source())
    midi.write_bytes(_midi())
    dialog = dialog_factory([_item(eseq), _item(midi, row=1)],
                            edit_callback=lambda *_args: pytest.fail("Unexpected edit"))
    assert dialog.details_box.toPlainText().startswith("E-SEQ file details")
    # Selecting the other tree item exercises the ordinary selection callback.
    dialog.file_tree.setCurrentItem(dialog.file_tree.topLevelItem(1))
    assert "E-SEQ file details" not in dialog.details_box.toPlainText()
    assert "MIDI type: Type 1" in dialog.details_box.toPlainText()
    assert dialog.convert_type0_button.isEnabled()


def test_unfamiliar_header_layout_keeps_existing_preview_without_guessed_details(dialog_factory, tmp_path):
    source = bytearray(_source())
    source[0x19] = 0x41
    path = tmp_path / "OTHER.FIL"
    path.write_bytes(source)
    dialog = dialog_factory([_item(path)])
    assert dialog.current_midi_bytes == convert_eseq_bytes_to_midi_bytes(source, include_conversion_text=False)
    assert len(dialog.all_notes) == 1
    report = dialog.details_box.toPlainText()
    assert report.startswith("Original header details are unavailable for this E-SEQ variant.")
    assert "Note-channel mask" not in report
