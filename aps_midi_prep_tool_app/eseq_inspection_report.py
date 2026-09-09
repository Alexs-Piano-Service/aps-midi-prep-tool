"""Localized E-SEQ source details, separate from the decoded MIDI preview."""

from .eseq_inspection import inspect_eseq_header
from .message_catalog import translate_text


_FIELD_LABELS = {
    "arrangement": "Arrangement/type code",
    "write_protect": "Write protection",
    "xg_marker": "XG / tone generator marker",
    "ensemble_parts": "Ensemble / extra note parts",
    "piano_parts": "Left/right piano parts",
    "detailed_pedal": "Detailed pedal flag",
    "note_channel_mask": "Note-channel mask",
    "display_mode": "Counter display",
    "duration_ticks": "Declared duration (ticks)",
    "timing_bookkeeping": "Timing bytes (album-specific)",
    "tail_position": "Tail-position bytes",
    "raw_53": "Unresolved header byte",
}


def format_eseq_header_details(data, *, source_label="", language_code="en"):
    """Describe original bytes without changing the performance or source file."""
    info = inspect_eseq_header(data)

    def report(text, **values):
        return translate_text(text, language_code, **values)

    def channels(values):
        return ", ".join(str(value) for value in values) if values else report("None detected")

    def bpm(mpqn):
        return f"{60_000_000 / mpqn:.3f}"

    lines = [
        report("E-SEQ file details"),
        report("File: {filename}", filename=source_label or report("Selected file")),
        report("Title: {title}", title=info.title),
        report("Container: {variant}", variant={
            "fil": "Disklavier FIL", "q11": "Disklavier Q11", "mda": "Clavinova MDA",
        }[info.variant]),
        report("Event stream offset: {offset}", offset=f"0x{info.stream_offset:02X}"),
        "",
        report("Playback timing:"),
        report("Clock resolution: {ticks} ticks per quarter note", ticks=info.resolution),
        report("Header tempo [{offset}]: {raw} → {bpm} BPM ({mpqn} µs/quarter note)",
               offset=f"0x{info.tempo_offset:02X}", raw=f"0x{info.tempo_raw:02X}",
               bpm=info.base_bpm, mpqn=info.base_mpqn),
    ]
    if info.tempo_mirror_raw is not None:
        lines.append(report("Tempo mirror [0x24]: {raw}", raw=f"0x{info.tempo_mirror_raw:02X}"))
    if info.tempo_raw == 0:
        lines.append(report("Zero tempo byte selects Yamaha's 117 BPM default."))
    lines.append(report("Startup tempo: {bpm} BPM ({mpqn} µs/quarter note)",
                        bpm=bpm(info.effective_initial_mpqn), mpqn=info.effective_initial_mpqn))
    lines.append(report("Tempo-factor commands (FB): {count}", count=len(info.tempo_factors)))
    if info.tempo_factors:
        lines.append(report("FB factors use thousandths of the original header tempo; 1000 is neutral."))
        for (tick, factor), (_tempo_tick, mpqn) in zip(info.tempo_factors[:24], info.tempo_changes[:24]):
            lines.append(report("Tick {tick}: FB {factor} → {bpm} BPM", tick=tick, factor=factor, bpm=bpm(mpqn)))
        if len(info.tempo_factors) > 24:
            lines.append(report("...and {count} more tempo command(s).", count=len(info.tempo_factors) - 24))
    if info.header_meter is not None:
        numerator, denominator = info.header_meter
        lines.append(report("Header time signature: {meter}", meter=f"{numerator}/{denominator}"))

    if info.fields:
        lines.extend(("", report("Original E-SEQ header:")))
        for field in info.fields:
            value = field.value
            if field.interpretation in {"boolean", "write_protect"}:
                value = report("Set" if value else "Not set") if value is not None else report("Unknown flag value")
            elif field.interpretation == "channels":
                value = channels(value)
            elif field.interpretation == "display":
                value = report("Measure" if value == "measure" else "Time")
            elif field.interpretation == "arrangement":
                value = report({0: "Solo", 1: "L-R Split", 2: "Ensemble"}.get(value, "Unknown"))
            elif field.interpretation == "raw":
                value = report("Unknown flag value" if field.key in {
                    "xg_marker", "ensemble_parts", "piano_parts", "detailed_pedal",
                } else "Not interpreted")
            lines.append(report("{label} [{offset}]: {raw} — {value}",
                                label=report(_FIELD_LABELS[field.key]), offset=f"0x{field.offset:02X}",
                                raw=field.raw.hex(" ").upper(), value=value))
        lines.append(report("Header flag meanings follow the Mark IV converter; other raw values are left uninterpreted."))
        if any(field.key == "timing_bookkeeping" for field in info.fields):
            lines.append(report("Timing-field meanings vary between E-SEQ variants and albums."))
    lines.append(report("Note channels in stream: {channels}", channels=channels(info.actual_note_channels)))
    return "\n".join(lines)
