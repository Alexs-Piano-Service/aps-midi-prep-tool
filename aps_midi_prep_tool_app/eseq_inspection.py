"""Read-only E-SEQ header facts, separated from presentation and translation.

Normal FIL status fields follow the recovered Mark IV paths. Their boolean
outputs do not establish the meaning of historical high-bit values. Q11 and
MDA reuse parts of this area for different layouts and must not inherit these
interpretations. Factory timing bookkeeping and byte 0x53 remain raw data.
"""

from dataclasses import dataclass

from .eseq_converter import (
    CLAVINOVA_MDA_HEADER_SIZE,
    ESEQ_HEADER_SIZE,
    ESEQ_MIDI_DIVISION,
    ESEQ_SIGNATURE,
    Q11_EVENT_STREAM_START,
    Q11_SIGNATURE,
    EseqConversionError,
    _eseq_header_time_signature,
    is_clavinova_mda_eseq_bytes,
    parse_eseq_bytes,
)
from .eseq_header import analyze_eseq_playback_flags


@dataclass(frozen=True)
class EseqHeaderField:
    key: str
    offset: int
    raw: bytes
    value: bool | int | str | tuple[int, ...] | None
    interpretation: str


@dataclass(frozen=True)
class EseqHeaderInspection:
    variant: str
    title: str
    stream_offset: int
    normal_program_header: bool
    resolution: int
    tempo_offset: int
    tempo_raw: int
    tempo_mirror_raw: int | None
    base_bpm: int
    base_mpqn: int
    effective_initial_mpqn: int
    tempo_changes: tuple[tuple[int, int], ...]
    tempo_factors: tuple[tuple[int, int], ...]
    header_meter: tuple[int, int] | None
    fields: tuple[EseqHeaderField, ...]
    actual_note_channels: tuple[int, ...]
    actual_pedal_lanes: tuple[tuple[int, int], ...]
    actual_xg: bool
    end_tick: int


def _normal_header_fields(data):
    def raw_field(key, offset, size):
        return EseqHeaderField(key, offset, bytes(data[offset:offset + size]), None, "raw")

    def boolean_field(key, offset):
        raw = data[offset]
        # Yamaha's converter emits 0/1. Preserve unestablished 0x80 and other
        # historical variants without presenting them as an ordinary boolean.
        return EseqHeaderField(
            key, offset, bytes((raw,)), bool(raw) if raw in (0, 1) else None,
            "boolean" if raw in (0, 1) else "raw",
        )

    mask = int.from_bytes(data[0x54:0x56], "little")
    return (
        EseqHeaderField("write_protect", 0x4F, bytes(data[0x4F:0x50]), bool(data[0x4F] & 0x80), "write_protect"),
        EseqHeaderField("arrangement", 0x50, bytes(data[0x50:0x51]), data[0x50] & 0x03, "arrangement"),
        boolean_field("xg_marker", 0x45),
        boolean_field("ensemble_parts", 0x4D),
        boolean_field("piano_parts", 0x50),
        boolean_field("detailed_pedal", 0x51),
        EseqHeaderField(
            "note_channel_mask", 0x54, bytes(data[0x54:0x56]),
            tuple(channel + 1 for channel in range(16) if mask & (1 << channel)),
            "channels",
        ),
        EseqHeaderField(
            "display_mode", 0x56, bytes(data[0x56:0x57]),
            "measure" if data[0x56] else "time", "display",
        ),
        EseqHeaderField(
            "duration_ticks", 0x37, bytes(data[0x37:0x3B]),
            int.from_bytes(data[0x37:0x3B], "little"), "integer",
        ),
        # These are not universal leading/trailing silence values. CPC1214
        # established album-specific delay/count/position relationships only.
        raw_field("timing_bookkeeping", 0x3B, 6),
        raw_field("tail_position", 0x41, 2),
        raw_field("raw_53", 0x53, 1),
    )


def inspect_eseq_header(data) -> EseqHeaderInspection:
    """Return original header bytes and decoded stream facts without changing data.

    Tempo changes and raw FB factors exclude the implicit initial header tempo.
    Each FB factor is relative to that original header tempo, not cumulative.
    A zero FB operand remains visible as zero even though the tolerant playback
    parser normalizes it to one. No raw factor is reconstructed from rounded
    microseconds. Field interpretation strings are stable presentation codes.
    """
    data = bytes(data or b"")
    if len(data) < CLAVINOVA_MDA_HEADER_SIZE:
        raise EseqConversionError("File is too small to be a valid Yamaha E-SEQ file.")
    if data[7:15] != ESEQ_SIGNATURE:
        raise EseqConversionError("This does not look like a Yamaha E-SEQ file; the COM-ESEQ signature is missing.")
    if data[0] != 0xFE:
        raise EseqConversionError("The Yamaha E-SEQ header marker is invalid.")

    if data[0x0F:0x17] == Q11_SIGNATURE:
        variant, stream_offset, tempo_offset = "q11", Q11_EVENT_STREAM_START, 0x24
    elif is_clavinova_mda_eseq_bytes(data):
        variant, stream_offset, tempo_offset = "mda", CLAVINOVA_MDA_HEADER_SIZE, 0x24
    else:
        variant, stream_offset, tempo_offset = "fil", ESEQ_HEADER_SIZE, 0x33
    if len(data) <= stream_offset:
        raise EseqConversionError("File is too small to be a valid Yamaha E-SEQ file.")
    normal_program_header = (
        variant == "fil"
        and data[0x19] == 0x40
        and int.from_bytes(data[0x1B:0x1F], "little") == 0x50
    )
    if variant == "fil" and data[0x19] != 0x40:
        raise EseqConversionError("This E-SEQ program header layout is not supported for inspection.")

    parsed = parse_eseq_bytes(data)
    base_mpqn = 60_000_000 // parsed.base_bpm
    effective_initial_mpqn = base_mpqn
    for tick, mpqn in parsed.tempo_events:
        if tick == 0:
            effective_initial_mpqn = mpqn
    meter = _eseq_header_time_signature(data)
    header_meter = (meter[0], 1 << meter[1]) if meter is not None else None
    flags = analyze_eseq_playback_flags(raw for _tick, _kind, raw in parsed.events)
    actual_note_channels = tuple(
        channel + 1 for channel in range(16) if flags.note_channel_mask & (1 << channel)
    )
    actual_pedal_lanes = tuple(sorted({
        ((raw[0] & 0x0F) + 1, raw[1])
        for _tick, _kind, raw in parsed.events
        if len(raw) == 3 and 0xB0 <= raw[0] <= 0xBF and raw[1] in (64, 66, 67)
    }))
    return EseqHeaderInspection(
        variant=variant,
        title=parsed.title,
        stream_offset=stream_offset,
        normal_program_header=normal_program_header,
        resolution=ESEQ_MIDI_DIVISION,
        tempo_offset=tempo_offset,
        tempo_raw=data[tempo_offset],
        tempo_mirror_raw=data[0x24] if normal_program_header else None,
        base_bpm=parsed.base_bpm,
        base_mpqn=base_mpqn,
        effective_initial_mpqn=effective_initial_mpqn,
        tempo_changes=tuple(parsed.tempo_events[1:]),
        tempo_factors=tuple(parsed.tempo_factors),
        header_meter=header_meter,
        fields=_normal_header_fields(data) if normal_program_header else (),
        actual_note_channels=actual_note_channels,
        actual_pedal_lanes=actual_pedal_lanes,
        actual_xg=flags.has_xg,
        end_tick=parsed.end_tick,
    )
