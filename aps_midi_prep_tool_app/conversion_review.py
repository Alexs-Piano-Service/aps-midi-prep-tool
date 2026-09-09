"""Musical summaries for inspecting staged conversions without saving them."""

from collections import Counter
from dataclasses import asdict, dataclass
import re

from .eseq_converter import ESEQ_MIDI_DIVISION, is_clavinova_mda_eseq_bytes, parse_eseq_bytes
from .eseq_legacy import is_legacy_eseq_bytes
from .midi_type0_converter import _MidiTimeMap, _encode_vlq, _parse_track_events, _parse_vlq
from .message_catalog import translate_text


def localize_music_format(label, language_code=None):
    """Translate display labels without changing stored format identifiers."""
    label = str(label or "")
    match = re.fullmatch(r"(MIDI )?Type (\d+)( \(independent sequences, total\))?", label)
    if match:
        source = "MIDI Type {type}" if match[1] else "Type {type}"
        translated = translate_text(source, language_code, type=match[2])
        if match[3]:
            translated = translate_text("{format} (independent sequences, total)", language_code, format=translated)
        return translated
    return translate_text(label, language_code)


def localize_music_error(error, language_code=None):
    """Translate recognized parser diagnostics while retaining unknown technical details."""
    return translate_text(str(error), language_code)


@dataclass(frozen=True)
class MusicalSummary:
    format: str
    notes: int
    duration_seconds: float
    channels: tuple
    pedals: dict
    titles: tuple
    metadata: dict
    zero_volume_events: int
    trailing_bytes: int
    xg_detected: bool


@dataclass(frozen=True)
class ConversionReport:
    before: MusicalSummary
    after: MusicalSummary
    removed_metadata: dict
    notes_changed: bool
    pedals_changed: bool
    channel_events_changed: bool
    legacy_timing: bool = False
    channel_payload_changed: bool | None = None
    pedal_channels_routed: tuple = ()
    other_channel_payload_changed: bool | None = None
    yamaha_pedal_controllers: tuple = ()
    pedal_binary_events_added: int = 0
    pedal_duplicate_events_removed: int = 0
    expected_channel_events_changed: bool | None = None

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        def summary(values):
            values = dict(values)
            # JSON object keys become strings, and tuple fields become lists.
            # Normalize these before rendering controller counts or comparing
            # a restored report with its original in-memory value.
            values["pedals"] = {int(controller): count for controller, count in values["pedals"].items()}
            values["channels"] = tuple(values["channels"])
            values["titles"] = tuple(values["titles"])
            return MusicalSummary(**values)

        return cls(
            summary(data["before"]), summary(data["after"]),
            dict(data["removed_metadata"]), bool(data["notes_changed"]),
            bool(data["pedals_changed"]), bool(data["channel_events_changed"]),
            bool(data.get("legacy_timing", False)),
            None if data.get("channel_payload_changed") is None else bool(data["channel_payload_changed"]),
            tuple(data.get("pedal_channels_routed", ())),
            None if data.get("other_channel_payload_changed") is None else bool(data["other_channel_payload_changed"]),
            tuple(data.get("yamaha_pedal_controllers", ())),
            int(data.get("pedal_binary_events_added", 0)),
            int(data.get("pedal_duplicate_events_removed", 0)),
            None if data.get("expected_channel_events_changed") is None else bool(data["expected_channel_events_changed"]),
        )

    def to_text(self, language_code=None):
        def tr(source, **values):
            return translate_text(source, language_code, **values)

        before, after = self.before, self.after
        channels = lambda summary: ", ".join(map(str, summary.channels)) or "—"
        titles = lambda summary: " | ".join(repr(title) for title in summary.titles) or "—"
        lines = [
            f"{localize_music_format(before.format, language_code)} → {localize_music_format(after.format, language_code)}",
            tr("Notes: {count}", count=f"{before.notes} → {after.notes}"),
            tr("Duration: {duration}", duration=f"{before.duration_seconds:.3f} s → {after.duration_seconds:.3f} s"),
            tr("Channels: {channels}", channels=f"{channels(before)} → {channels(after)}"),
            f"{tr('Title')}: {titles(before)} → {titles(after)}",
            tr("Pedals / Controllers:"),
        ]
        for controller in (64, 66, 67):
            lines.append(f"CC{controller}: {before.pedals.get(controller, 0)} → {after.pedals.get(controller, 0)}")
        metadata_aliases = {"Track names": "Track Name", "Instrument names": "Instrument Name", "Lyrics": "Lyric", "Markers": "Marker", "Cue points": "Cue"}
        lines.extend([
            tr("Zero-volume CC7: {before} → {after}", before=before.zero_volume_events, after=after.zero_volume_events),
            tr("Trailing data: {before} → {after} bytes", before=before.trailing_bytes, after=after.trailing_bytes),
            tr("Removed metadata: {metadata}", metadata=", ".join(
                f"{tr(metadata_aliases.get(label, label))}: {count}" for label, count in self.removed_metadata.items()
            ) or tr("none")),
        ])
        if self.legacy_timing:
            lines.append(tr("Timing follows MID2ESEQ: a 117 BPM clock, preparation delay, spacing of crowded events, and two seconds of ending silence."))
        if self.pedal_channels_routed:
            lines.append(tr(
                "Continuous pedals moved from channel 1 to channel 3: {controllers}.",
                controllers=", ".join(f"CC{controller}" for controller in self.pedal_channels_routed),
            ))
        if self.yamaha_pedal_controllers:
            lines.append(tr(
                "Yamaha pedals on {controllers}: {binary} on/off events added on channel 1; {duplicates} redundant continuous events omitted on channel 3.",
                controllers=", ".join(f"CC{controller}" for controller in self.yamaha_pedal_controllers),
                binary=self.pedal_binary_events_added,
                duplicates=self.pedal_duplicate_events_removed,
            ))
        if self.notes_changed:
            lines.append(tr("Note values, channels, or timing changed."))
        if self.pedals_changed:
            lines.append(tr("Pedal values, channels, or timing changed."))
        if self.channel_events_changed:
            if self.expected_channel_events_changed is False:
                lines.append(tr("MIDI channel events match the Yamaha pedal conversion and expected timing."))
            elif self.expected_channel_events_changed is True:
                lines.append(tr("Other MIDI channel events or timing differ from the expected Yamaha pedal conversion."))
                if self.other_channel_payload_changed:
                    lines.append(tr("MIDI channel messages changed (notes, controllers, or instruments)."))
            elif self.pedal_channels_routed and self.other_channel_payload_changed is False:
                lines.append(tr("MIDI channel messages changed only in pedal routing or timing; message values are unchanged."))
            elif self.channel_payload_changed is False:
                lines.append(tr("MIDI channel message timing changed; message values are unchanged."))
            else:
                lines.append(tr("MIDI channel messages changed (notes, controllers, or instruments)."))
        return "\n".join(lines)


_META_LABELS = {1: "Text", 2: "Copyright", 3: "Track names", 4: "Instrument names", 5: "Lyrics", 6: "Markers", 7: "Cue points", 0x51: "Tempo", 0x58: "Time Signature", 0x59: "Key Signature", 0x7F: "Sequencer-specific"}


def _metadata_label(raw):
    if raw[0] in (0xF0, 0xF7):
        return "SysEx"
    if raw.startswith(b"HEADER:"):
        return "Extended MIDI header"
    return _META_LABELS.get(raw[1], f"Meta FF {raw[1]:02X}")


def _meta_event(kind, payload):
    return b"\xff" + bytes([kind]) + _encode_vlq(len(payload)) + payload


def _canonical_metadata(raw, *, eseq=False):
    if raw[:1] == b"\xff":
        return _meta_event(raw[1], _meta_payload(raw))
    if raw and raw[0] in (0xF0, 0xF7):
        offset = 1 if eseq else _parse_vlq(raw, 1, len(raw))[1]
        return raw[:1] + raw[offset:]
    return None


def _meta_payload(raw):
    size, offset = _parse_vlq(raw, 2, len(raw))
    return raw[offset:offset + size]


def _read_events(data):
    """Return timed channel events and raw metadata, including independent Type 2 timing."""
    if data[:4] != b"MThd":
        parsed = parse_eseq_bytes(data)
        tempos = [(tick, 0, order, tempo) for order, (tick, tempo) in enumerate(parsed.tempo_events)]
        time_map = _MidiTimeMap(ESEQ_MIDI_DIVISION, tempos)
        events = [(time_map.tick_to_milliseconds(tick) / 1000, raw) for tick, _, raw in parsed.events]
        metadata = [_meta_event(0x51, tempo.to_bytes(3, "big")) for _, tempo in parsed.tempo_events]
        metadata.extend(_meta_event(0x58, bytes([numerator, denominator, 24, 8])) for _, numerator, denominator in parsed.time_signature_events)
        if not is_clavinova_mda_eseq_bytes(data):
            metadata.append(_meta_event(0x03, parsed.title.encode("latin1")))
        metadata.extend(item for _, raw in events if (item := _canonical_metadata(raw, eseq=True)) is not None)
        return "E-SEQ", events, time_map.tick_to_milliseconds(parsed.end_tick) / 1000, (parsed.title,), metadata, 0

    if len(data) < 14:
        raise ValueError("Truncated MIDI header.")
    header_size = int.from_bytes(data[4:8], "big")
    format_type = int.from_bytes(data[8:10], "big")
    count = int.from_bytes(data[10:12], "big")
    division = int.from_bytes(data[12:14], "big")
    offset = 8 + header_size
    if header_size < 6 or offset > len(data):
        raise ValueError("Invalid MIDI header.")
    tracks = []
    while len(tracks) < count:
        if offset + 8 > len(data):
            raise ValueError("Missing MIDI track.")
        end = offset + 8 + int.from_bytes(data[offset + 4:offset + 8], "big")
        if end > len(data):
            raise ValueError("Truncated MIDI chunk.")
        if data[offset:offset + 4] == b"MTrk":
            tracks.append(_parse_track_events(data[offset + 8:end]))
        offset = end
    tempos = []
    metadata = []
    if header_size > 6:
        metadata.append(b"HEADER:" + data[14:8 + header_size])
    titles = []
    for index, (events, _) in enumerate(tracks):
        for tick, order, raw in events:
            if raw[:2] == b"\xff\x51":
                tempos.append((tick, index, order, int.from_bytes(_meta_payload(raw), "big")))
            elif raw[:2] == b"\xff\x03":
                titles.append(_meta_payload(raw).decode("latin1"))
            canonical = _canonical_metadata(raw)
            if canonical is not None:
                metadata.append(canonical)
    shared_map = _MidiTimeMap(division, tempos)
    timed = []
    durations = []
    cumulative = 0.0
    for index, (events, end_tick) in enumerate(tracks):
        time_map = _MidiTimeMap(division, [item for item in tempos if item[1] == index]) if format_type == 2 else shared_map
        timed.extend((cumulative + time_map.tick_to_milliseconds(tick) / 1000, raw) for tick, _, raw in events)
        duration = time_map.tick_to_milliseconds(end_tick) / 1000
        durations.append(duration)
        if format_type == 2:
            cumulative += duration
    label = f"MIDI Type {format_type}" + (" (independent sequences, total)" if format_type == 2 else "")
    return label, timed, cumulative if format_type == 2 else max(durations, default=0), tuple(titles), metadata, len(data) - offset


def _summary(data):
    kind, timed, duration, titles, metadata, trailing = _read_events(data)
    channel_events = [(tick, raw) for tick, raw in timed if raw and 0x80 <= raw[0] <= 0xEF]
    notes = [
        (tick, bytes([raw[0] - 0x10]) + raw[1:] if raw[0] & 0xF0 == 0x90 and raw[2] == 0 else raw)
        for tick, raw in channel_events if raw[0] & 0xF0 in (0x80, 0x90)
    ]
    pedals = [(tick, raw) for tick, raw in channel_events if raw[0] & 0xF0 == 0xB0 and raw[1] in (64, 66, 67)]
    xg = any(
        len(raw) == 9 and raw[:2] == b"\xf0\x43" and raw[2] & 0xF0 == 0x10
        and raw[3:] == b"\x4c\x00\x00\x7e\x00\xf7"
        for raw in metadata
    )
    summary = MusicalSummary(
        kind,
        sum(raw[0] & 0xF0 == 0x90 and raw[2] > 0 for _, raw in notes),
        duration,
        tuple(sorted({(raw[0] & 0x0F) + 1 for _, raw in channel_events})),
        dict(Counter(raw[1] for _, raw in pedals)),
        titles,
        dict(Counter(_metadata_label(raw) for raw in metadata)),
        sum(raw[0] & 0xF0 == 0xB0 and raw[1:] == b"\x07\x00" for _, raw in channel_events),
        trailing,
        xg,
    )
    return summary, metadata, notes, pedals, channel_events


def inspect_music_bytes(data):
    return _summary(data)[0]


def _events_changed(before, after, tolerance_seconds):
    before = sorted(before, key=lambda event: (event[1], event[0]))
    after = sorted(after, key=lambda event: (event[1], event[0]))
    return len(before) != len(after) or any(
        a_raw != b_raw or abs(a_tick - b_tick) > tolerance_seconds
        for (a_tick, a_raw), (b_tick, b_raw) in zip(before, after)
    )


def _verified_pedal_routing(before, after):
    """Recognize complete, value-preserving Yamaha pedal lane moves.

    Inspect the result independently of converter settings. A partial move,
    changed value, reordered lane, or existing destination lane must not earn a
    preservation claim merely because the output is a Disklavier file.
    """
    before = sorted(before, key=lambda event: event[0])
    after = sorted(after, key=lambda event: event[0])

    def lane(events, status, controller):
        return [raw[2] for _, raw in events
                if raw[0] == status and raw[1] == controller]

    routed = []
    for controller in (64, 66, 67):
        original = lane(before, 0xB0, controller)
        if (any(0 < value < 127 for value in original)
                and not lane(before, 0xB2, controller)
                and not lane(after, 0xB0, controller)
                and lane(after, 0xB2, controller) == original):
            routed.append(controller)
    return tuple(routed)


def _ordered_events_changed(before, after, tolerance_seconds):
    """Compare scheduled message order as well as values, counts and timestamps."""
    before = sorted(before, key=lambda event: event[0])
    after = sorted(after, key=lambda event: event[0])
    return len(before) != len(after) or any(
        old_raw != new_raw or abs(old_time - new_time) > tolerance_seconds
        for (old_time, old_raw), (new_time, new_raw) in zip(before, after)
    )


def _yamaha_pedal_reference(source_events, controllers):
    """Independently replay the recovered Yamaha rules on already scheduled events.

    This verifier deliberately does not import the production pedal converter.
    Edges compare successive raw samples, not a latched binary state. Detailed
    values start at zero; binary edges precede their detailed sample at the same
    timestamp. Only redundant detailed samples may disappear.
    """
    previous = {controller: 0 for controller in controllers}
    counts = {controller: [0, 0] for controller in controllers}
    expected = []
    for timestamp, raw in source_events:
        if len(raw) != 3 or raw[0] != 0xB0 or raw[1] not in controllers:
            expected.append((timestamp, raw))
            continue
        controller, value = raw[1:]
        old_value = previous[controller]
        low, high = (82, 84) if controller == 64 else (61, 64)
        edge = 127 if old_value < high <= value else 0 if value < low <= old_value else None
        if edge is not None:
            expected.append((timestamp, bytes((0xB0, controller, edge))))
            counts[controller][0] += 1
        if value != old_value:
            expected.append((timestamp, bytes((0xB2, controller, value))))
        else:
            counts[controller][1] += 1
        previous[controller] = value
    return expected, counts


def _verified_yamaha_pedals(before_bytes, after_bytes, old_channels, new_channels, tolerance_seconds):
    """Verify both pedal layers against the complete source and its playback clock."""
    # Destination occupancy is checked from all original channels, including
    # note-offs and zero-velocity note-ons, not only the pedal summary.
    if any(raw[0] in (0x82, 0x92) for _, raw in old_channels):
        return (), {}, None, None
    candidates = tuple(controller for controller in (64, 67)
                       if any(raw[:2] == bytes((0xB0, controller)) and 0 < raw[2] < 127
                              for _, raw in old_channels)
                       and not any(raw[:2] == bytes((0xB2, controller)) for _, raw in old_channels))
    if not candidates or not any(raw[0] == 0xB2 and raw[1] in candidates for _, raw in new_channels):
        # Preserved E-SEQ-origin MIDI and existing native arrangements do not
        # imply that a new Yamaha transformation was requested.
        return (), {}, None, None
    reference = old_channels
    if is_legacy_eseq_bytes(after_bytes):
        from .eseq_converter import _collect_merged_midi_events
        from .eseq_legacy import build_legacy_eseq_bytes

        division, source_events = _collect_merged_midi_events(before_bytes)
        # Run each original event through the established clock exactly once.
        # Pedal additions/removals never participate in reference scheduling.
        reference = _summary(build_legacy_eseq_bytes(source_events, division, title=""))[4]
    reference = sorted(reference, key=lambda event: event[0])
    expected, counts = _yamaha_pedal_reference(reference, candidates)

    def lane(events, controller):
        return [(timestamp, raw) for timestamp, raw in events
                if raw[0] in (0xB0, 0xB2) and raw[1] == controller]

    verified = tuple(controller for controller in candidates
                     if not _ordered_events_changed(lane(expected, controller), lane(new_channels, controller), tolerance_seconds))
    return verified, counts, expected, _ordered_events_changed(expected, new_channels, tolerance_seconds)


def compare_music_bytes(before_bytes, after_bytes, *, tolerance_seconds=None):
    before, old_meta, old_notes, old_pedals, old_channels = _summary(before_bytes)
    after, new_meta, new_notes, new_pedals, new_channels = _summary(after_bytes)
    if tolerance_seconds is None:
        tolerance_seconds = 0.002
        old_division = int.from_bytes(before_bytes[12:14], "big") if before_bytes[:4] == b"MThd" else ESEQ_MIDI_DIVISION
        new_division = int.from_bytes(after_bytes[12:14], "big") if after_bytes[:4] == b"MThd" else ESEQ_MIDI_DIVISION
        if old_division != new_division or (before_bytes[:4] == b"MThd") != (after_bytes[:4] == b"MThd"):
            for division, metadata in ((old_division, old_meta), (new_division, new_meta)):
                if division & 0x8000:
                    tick_seconds = _MidiTimeMap(division, []).tick_to_milliseconds(1) / 1000
                else:
                    tempos = [int.from_bytes(_meta_payload(raw), "big") for raw in metadata if raw[:2] == b"\xff\x51"]
                    tick_seconds = max(tempos or [500000]) / division / 1_000_000
                # Resampling rounds to the nearest tick. Account for the
                # coarser clock without hiding changes beyond that precision.
                tolerance_seconds = max(tolerance_seconds, tick_seconds / 2 + 1e-6)
    removed = Counter(old_meta) - Counter(new_meta)
    removed_labels = Counter()
    for raw, count in removed.items():
        removed_labels[_metadata_label(raw)] += count
    routed_pedals = ()
    yamaha_pedals, pedal_counts, yamaha_expected, expected_changed = (), {}, None, None
    if before_bytes[:4] == b"MThd" and after_bytes[:4] != b"MThd" and not is_clavinova_mda_eseq_bytes(after_bytes):
        routed_pedals = _verified_pedal_routing(old_pedals, new_pedals)
        yamaha_pedals, pedal_counts, yamaha_expected, expected_changed = _verified_yamaha_pedals(
            before_bytes, after_bytes, old_channels, new_channels, tolerance_seconds,
        )
        routed_pedals = tuple(sorted(set(routed_pedals) | set(yamaha_pedals)))
    expected_channels = Counter(
        b"\xb2" + raw[1:] if raw[0] == 0xB0 and raw[1] in routed_pedals else raw
        for _, raw in old_channels
    )
    if yamaha_pedals:
        expected_channels = Counter(raw for _, raw in yamaha_expected)
    return ConversionReport(
        before, after, dict(removed_labels),
        _events_changed(old_notes, new_notes, tolerance_seconds),
        _events_changed(old_pedals, new_pedals, tolerance_seconds),
        _events_changed(old_channels, new_channels, tolerance_seconds),
        legacy_timing=before_bytes[:4] == b"MThd" and is_legacy_eseq_bytes(after_bytes),
        # Compare exact message bytes and counts independently of their times.
        # The legacy envelope only explains timing; it never proves preservation.
        channel_payload_changed=Counter(raw for _, raw in old_channels) != Counter(raw for _, raw in new_channels),
        pedal_channels_routed=routed_pedals,
        other_channel_payload_changed=expected_channels != Counter(raw for _, raw in new_channels),
        yamaha_pedal_controllers=yamaha_pedals,
        pedal_binary_events_added=sum(pedal_counts[controller][0] for controller in yamaha_pedals),
        pedal_duplicate_events_removed=sum(pedal_counts[controller][1] for controller in yamaha_pedals),
        expected_channel_events_changed=expected_changed,
    )


def build_conversion_report(source_path, output_path):
    with open(source_path, "rb") as handle:
        before = handle.read()
    with open(output_path, "rb") as handle:
        after = handle.read()
    return compare_music_bytes(before, after)


def build_staged_conversion_details(source_path, output_path, *, baseline_bytes=None, retain_baseline=False):
    """Describe the complete pending result against an immutable source.

    Added image songs have no source entry to extract later. Keep their original
    bytes in ordinary staged state so undo and subsequent conversion batches do
    not depend on an intermediate scratch file still existing.
    """
    details = {"change_report": None, "change_report_error": ""}
    try:
        if baseline_bytes is None:
            with open(source_path, "rb") as handle:
                baseline_bytes = handle.read()
        if retain_baseline:
            details["change_report_baseline"] = baseline_bytes
        with open(output_path, "rb") as handle:
            report = compare_music_bytes(baseline_bytes, handle.read())
        details["change_report"] = report.as_dict()
        details["change_report"]["text"] = report.to_text()
    except (OSError, ValueError) as exc:
        details["change_report_error"] = str(exc)
    return details
