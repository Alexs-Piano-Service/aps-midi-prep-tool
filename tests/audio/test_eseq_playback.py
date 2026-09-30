"""Render converted E-SEQ files and check the music in their actual audio.

These small, original phrases are assembled directly in Yamaha's on-disk
format. They deliberately avoid the application's E-SEQ writer and parsers
so matching encoder/decoder mistakes cannot make a round trip pass.
"""

import pytest

from aps_midi_prep_tool_app.eseq_converter import convert_eseq_file_to_midi_path


# All three notes last half a second. The second part enters much later than
# the first, and every rest is long enough for a piano's release to finish.
_NOTE_WINDOWS = ((0.30, 0.55, 60), (1.55, 1.80, 64), (2.80, 3.05, 67))
_QUIET_WINDOWS = ((0.02, 0.18), (1.30, 1.45), (2.55, 2.70), (4.40, 4.65))


def _word14(value):
    assert 0 <= value <= 0x3FFF
    return bytes((value & 0x7F, value >> 7))


def _delay(ticks):
    return b"\xF4" + _word14(ticks)


def _eseq(kind, stream):
    """Create FIL/Q11/MDA headers without APS templates or helper functions."""
    size = {"FIL": 0x77, "Q11": 0x200, "MDA": 0x57}[kind]
    header = bytearray(size)
    header[0] = 0xFE
    header[3:7] = (size + len(stream)).to_bytes(4, "little")
    header[7:15] = b"COM-ESEQ"
    header[0x17:0x1F] = bytes.fromhex("80 00 40 00 50 00 00 00")
    header[0x1F:0x23] = len(stream).to_bytes(4, "little")
    header[0x27:0x32] = b"AUDIO   FIL"
    header[0x34:0x36] = b"\x04\x04"
    # Byte 31 represents 60 BPM. Give the unused tempo field a different
    # value so picking the wrong field changes audible timing.
    header[0x24] = 88 if kind == "FIL" else 31
    header[0x33] = 31 if kind == "FIL" else 88
    if kind != "MDA":
        header[0x57:0x77] = b"Original audio test phrase".ljust(32, b" ")
    if kind == "Q11":
        header[0x0F:0x17] = b"Q11V1.00"
        header[0x19] = 0x21
        header[0x77:0x200] = bytes(index % 128 for index in range(0x189))
    elif kind == "MDA":
        header[:7] = bytes.fromhex("FE 00 00 FF FF 00 00")
        header[0x17:0x1F] = bytes.fromhex("80 00 21 00 30 00 00 00")
        header[0x1F:0x23] = (size + len(stream)).to_bytes(4, "little")
    return bytes(header) + stream


def _phrase(*, changing_tempo=False):
    # E-SEQ uses 384 ticks/quarter. At 60 BPM, 96 ticks = 0.25 s.
    stream = (
        b"\xF1\x00"
        b"\xC0\x00\xC1\x00"  # Piano programs on both parts.
        b"\xB0\x07\x00\xB1\x07\x00"  # Yamaha startup mutes.
        b"\xF3\x60\x90\x3C\x64"  # C4, 0.25 s.
        b"\xB0\x07\x00"  # Same-tick mute after onset is startup too.
        + _delay(192) + b"\x80\x3C\x00"  # Note off, 0.75 s.
    )
    if changing_tempo:
        # FB factors are relative to the initial tempo. Half speed makes
        # 144 ticks last 0.75 s and 96 ticks last 0.5 s.
        stream += b"\xFB" + _word14(500)
        stream += _delay(144) + b"\x90\x40\x64"
        stream += _delay(96) + b"\x80\x40\x00"
        # Double INITIAL speed: 576 ticks = 0.75 s, 384 ticks = 0.5 s.
        stream += b"\xFB" + _word14(2000)
        stream += _delay(576) + b"\x91\x43\x64"
        stream += b"\xB1\x07\x00"
        stream += _delay(384) + b"\x81\x43\x00"
        stream += _delay(1152)  # 1.5 s of final rest.
    else:
        stream += _delay(288) + b"\x90\x40\x64"  # E4, 1.50 s.
        stream += _delay(192) + b"\x80\x40\x00"  # Off, 2.00 s.
        stream += _delay(288) + b"\x91\x43\x64"  # G4, 2.75 s.
        stream += b"\xB1\x07\x00"  # Each part has its own startup.
        stream += _delay(192) + b"\x81\x43\x00"  # Off, 3.25 s.
        stream += _delay(576)  # Final rest ends at 4.75 s.
    return stream + b"\xF2"


def _convert(tmp_path, kind, stream, **options):
    source = tmp_path / ("phrase.MDA" if kind == "MDA" else "phrase.FIL")
    destination = tmp_path / "phrase.mid"
    source.write_bytes(_eseq(kind, stream))
    convert_eseq_file_to_midi_path(source, destination, **options)
    return destination


def _assert_music(audio):
    for start, end, note in _NOTE_WINDOWS:
        level = audio.rms(start, end)
        assert level > 0.001, (
            f"Expected audible note {note} at {start:.2f}–{end:.2f} s; "
            f"rendered RMS was {level:.6f}"
        )


def _assert_phrase(audio):
    assert audio.duration >= 4.65, "Playback ended before the final rest"
    _assert_music(audio)
    for start, end, note in _NOTE_WINDOWS:
        audio.assert_pitch(start, end, note)
    for start, end in _QUIET_WINDOWS:
        level = audio.rms(start, end)
        assert level < 0.0002, (
            f"Expected a quiet rest at {start:.2f}–{end:.2f} s; "
            f"rendered RMS was {level:.6f} (stuck note or incorrect timing)"
        )


@pytest.mark.parametrize("kind", ("FIL", "Q11", "MDA"))
def test_converted_eseq_plays_the_expected_piano_phrase(tmp_path, render_midi, kind):
    midi = _convert(tmp_path, kind, _phrase())
    audio = render_midi(midi)

    _assert_phrase(audio)
    if kind == "FIL":
        # Prove the spectral check rejects a wrong note in otherwise valid,
        # audible audio; nonzero output alone cannot satisfy the test.
        with pytest.raises(AssertionError, match="Expected audible pitch"):
            audio.assert_pitch(0.30, 0.55, 61)


def test_relative_tempo_changes_preserve_audible_note_timing(tmp_path, render_midi):
    midi = _convert(tmp_path, "FIL", _phrase(changing_tempo=True))

    # Deliberately the same real-time phrase despite different E-SEQ ticks.
    _assert_phrase(render_midi(midi))


def test_later_musical_mute_and_volume_restore_remain_audible(tmp_path, render_midi):
    stream = (
        b"\xF1\x00\xC0\x00\xB0\x07\x00"
        + _delay(96) + b"\x90\x3C\x64"
        + _delay(192) + b"\x80\x3C\x00\xB0\x07\x00"
        + _delay(288) + b"\x90\x40\x64"  # Intentionally silent E4.
        + _delay(192) + b"\x80\x40\x00\xB0\x07\x64"
        + _delay(288) + b"\x90\x43\x64"  # Volume restored for G4.
        + _delay(192) + b"\x80\x43\x00"
        + _delay(576) + b"\xF2"
    )
    audio = render_midi(_convert(tmp_path, "FIL", stream))

    for start, end, note in (_NOTE_WINDOWS[0], _NOTE_WINDOWS[2]):
        assert audio.rms(start, end) > 0.001
        audio.assert_pitch(start, end, note)
    assert audio.rms(1.55, 1.80) < 0.0002, "Later musical mute was removed"


def test_audio_assertion_rejects_notes_left_silent_by_startup_mutes(tmp_path, render_midi):
    midi = _convert(tmp_path, "FIL", _phrase(), cc7_policy="preserve")
    audio = render_midi(midi)

    # A syntactically valid MIDI still contains all notes, but preserving
    # Yamaha's zero channel volume makes its rendered performance silent.
    with pytest.raises(AssertionError, match="Expected audible note"):
        _assert_music(audio)
    for start, end, _note in _NOTE_WINDOWS:
        assert audio.rms(start, end) < 0.0002
