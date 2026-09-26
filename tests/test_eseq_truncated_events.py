"""Incomplete E-SEQ commands must never produce a plausible partial MIDI."""

import io
import os
from pathlib import Path

import mido
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from aps_midi_prep_tool_app.eseq_converter import (
    EseqConversionError,
    convert_eseq_bytes_to_midi_bytes,
    convert_eseq_file_to_midi_path,
    is_eseq_file,
    parse_eseq_bytes,
)
from aps_midi_prep_tool_app.eseq_inspection import inspect_eseq_header
from test_eseq_inspection import _fixture
from test_file_inspection_actions import application, dialog_factory, _item
from test_inspection_staging import window, _open_image


# A plausible, completed performance precedes every damaged command.
PRELUDE = bytes.fromhex("F1 00 95 3C 60 F3 30 85 3C 40")
COMMANDS = [
    bytes.fromhex(command) for command in (
        "F1 00", "F3 06", "F4 06 01", "FB 68 07", "F9 03 02", "FF 05",
        "85 3C 40", "95 3C 60", "A5 3C 40", "B5 40 7F", "E5 00 40", "C5 00", "D5 40",
    )
]
TRUNCATIONS = [(command, size) for command in COMMANDS for size in range(1, len(command))]
TRUNCATION_IDS = [f"{command[0]:02X}-{size - 1}-data-bytes" for command, size in TRUNCATIONS]
ERROR = "Encountered an incomplete E-SEQ event."


def _truncated(command, size, variant="fil"):
    # Leave the original length fields in place, as on a physically cut file.
    complete = bytes(_fixture(PRELUDE + command + b"\xF2", variant=variant))
    return complete[:-(len(command) - size + 1)]


@pytest.mark.parametrize("variant", ["fil", "mda", "q11"])
@pytest.mark.parametrize("command,size", TRUNCATIONS, ids=TRUNCATION_IDS)
def test_incomplete_commands_fail_parsing_inspection_and_conversion(command, size, variant):
    damaged = _truncated(command, size, variant)
    for operation in (parse_eseq_bytes, inspect_eseq_header, convert_eseq_bytes_to_midi_bytes):
        with pytest.raises(EseqConversionError) as caught:
            operation(damaged)
        assert str(caught.value) == ERROR


@pytest.mark.parametrize("variant", ["fil", "mda", "q11"])
@pytest.mark.parametrize("command", COMMANDS, ids=lambda command: f"{command[0]:02X}")
@pytest.mark.parametrize("ending", [b"", b"\xF2"])
def test_complete_equivalent_commands_still_convert(command, variant, ending):
    complete = bytes(_fixture(PRELUDE + command + ending, variant=variant))
    parsed = parse_eseq_bytes(complete)
    assert (0, 2, b"\x95\x3c\x60") in parsed.events
    assert (48, 2, b"\x85\x3c\x40") in parsed.events
    assert inspect_eseq_header(complete).end_tick == parsed.end_tick
    converted = convert_eseq_bytes_to_midi_bytes(complete)
    midi = mido.MidiFile(file=io.BytesIO(converted))
    assert midi.type == 0
    assert any(event.type == "note_on" and event.note == 60 for event in midi.tracks[0])
    # The command itself survives too (including timing and tempo commands).
    status = command[0]
    if status == 0xF3:
        assert parsed.end_tick == 54
    elif status == 0xF4:
        assert parsed.end_tick == 182
    elif status == 0xFB:
        assert parsed.tempo_factors[-1] == (48, 1000)
    elif status == 0xF9:
        assert parsed.time_signature_events[-1] == (48, 3, 2)
    elif status == 0xFF:
        assert parsed.events[-1] == (48, 1, b"\xff\x20\x01\x05")
    elif status < 0xF0:
        assert parsed.events[-1] == (48, 2, command)


@pytest.mark.parametrize("existing_destination", [False, True])
@pytest.mark.parametrize("command,size", TRUNCATIONS, ids=TRUNCATION_IDS)
def test_truncated_conversion_never_creates_or_overwrites_output(tmp_path, command, size, existing_destination):
    source = tmp_path / "DAMAGED.FIL"
    destination = tmp_path / "OUTPUT.MID"
    damaged = _truncated(command, size)
    source.write_bytes(damaged)
    assert is_eseq_file(source)  # Identification alone does not establish validity.
    if existing_destination:
        destination.write_bytes(b"existing destination")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(EseqConversionError, match="incomplete E-SEQ event"):
        convert_eseq_file_to_midi_path(source, destination)
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("command,size", TRUNCATIONS, ids=TRUNCATION_IDS)
def test_file_inspection_rejects_files_that_are_already_truncated(dialog_factory, tmp_path, command, size):
    source = tmp_path / "DAMAGED.FIL"
    damaged = _truncated(command, size)
    source.write_bytes(damaged)
    dialog = dialog_factory([_item(source)], edit_callback=lambda *_: pytest.fail("Unexpected edit"))
    assert "Could not inspect DAMAGED.FIL." in dialog.details_box.toPlainText()
    assert ERROR in dialog.details_box.toPlainText()
    assert dialog.current_midi_bytes == b""
    assert dialog.all_notes == dialog.visible_notes == []
    assert not dialog.play_button.isEnabled()
    assert not dialog.render_song_button.isEnabled()
    assert not dialog.merge_piano_button.isEnabled()
    assert not dialog.convert_type0_button.isEnabled()
    assert source.read_bytes() == damaged


@pytest.mark.parametrize("image_mode", [False, True])
@pytest.mark.parametrize("command,size", TRUNCATIONS, ids=TRUNCATION_IDS)
def test_conversion_of_already_truncated_files_does_not_stage_output(
    window, tmp_path, monkeypatch, command, size, image_mode,
):
    source = tmp_path / "DAMAGED.FIL"
    damaged = _truncated(command, size)
    source.write_bytes(damaged)
    if image_mode:
        _open_image(window, tmp_path, [(source, source.name)])
        output_dir = Path(window.image_session.patched_dir)
    else:
        window._load_regular_files([str(source)], "Truncated E-SEQ test", prepare_destination=False)
        output_dir = Path(window._ensure_midi_scratch_dir())
    before = window._staged_signature()
    before_files = set(output_dir.iterdir())
    errors = []
    monkeypatch.setattr(window, "_show_error_list", lambda *args, **kwargs: errors.append(args))
    monkeypatch.setattr(window, "_confirm_eseq_to_midi_conversion", lambda **kwargs: (True, False, False))
    window.convert_all_eseq_to_midi()
    assert errors and ERROR in str(errors)
    assert window._staged_signature() == before
    assert not window.pendingRegularConversions
    assert not window.pendingImageReplacements
    assert not window._staged_undo_stack
    assert set(output_dir.iterdir()) == before_files
    assert source.read_bytes() == damaged
