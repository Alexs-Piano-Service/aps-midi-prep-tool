"""Same-key overlap choices, applied before source channels are collapsed.

Smart repair follows aps-midi-piano-merger's ratio-2 underlay rule. Releases
use exact tick boundaries (zero release gap), preserving native E-SEQ delays.
"""

from bisect import bisect_left
from collections import defaultdict, deque
from dataclasses import dataclass

from .midi_type0_converter import (
    MIDI_ALL_SOUND_OFF_CONTROLLER,
    MIDI_NOTE_TERMINATION_CONTROLLERS,
    _is_midi_system_reset,
    _make_synthetic_event_like,
    _midi_port_number,
)


OVERLAP_MODES = frozenset({"smart", "retrigger", "off"})


class ChannelMergeCancelled(Exception):
    """The user canceled before this song's merge was written or staged."""


@dataclass
class _Note:
    on: int
    off: int
    start: int
    end: int
    raw: bytes
    release: bytes
    port: int


def _event_ports(events, source_tracks):
    """Port prefixes apply within their source track, including mid-song changes."""
    ports = defaultdict(int)
    for event in events:
        track = event[1] if source_tracks and len(event) == 4 else 0
        port = _midi_port_number(event[-1])
        if port is not None:
            ports[track] = port
        yield ports[track]


def _notes(events, end_event, source_tracks, event_ports):
    active = defaultdict(deque)
    notes = []

    def close(key, index, tick, release=None):
        note = active[key].popleft()
        note.off, note.end = index, tick
        if release is not None:
            note.release = release
        if not active[key]:
            del active[key]

    for index, event in enumerate(events):
        tick, raw = event[0], event[-1]
        track = event[1] if source_tracks and len(event) == 4 else 0
        port = event_ports[index]
        if _is_midi_system_reset(raw):
            for key in list(active):
                while key[1] == port and key in active:
                    close(key, index, tick)
        if len(raw) != 3 or not 0x80 <= raw[0] <= 0xEF:
            continue
        channel, kind = raw[0] & 15, raw[0] & 0xF0
        key = (track, port, channel, raw[1])
        if kind == 0x90 and raw[2]:
            note = _Note(index, len(events), tick, max(tick, end_event[0]),
                         raw, bytes([0x80 | channel, raw[1], 0]), port)
            active[key].append(note)
            notes.append(note)
        elif kind in (0x80, 0x90):
            if key not in active:
                # SMF channel state is shared across tracks. Prefer a release
                # in its own track, but also accept split attack/release tracks.
                candidates = [other for other in active if other[1:] == key[1:]]
                if not candidates:
                    continue
                key = min(candidates, key=lambda other: active[other][0].on)
            close(key, index, tick, raw)
        elif kind == 0xB0 and raw[1] in (
            MIDI_NOTE_TERMINATION_CONTROLLERS | {MIDI_ALL_SOUND_OFF_CONTROLLER}
        ):
            for other in list(active):
                if other[1:3] == key[1:3]:
                    while other in active:
                        close(other, index, tick)
    return notes


class _MinEndTree:
    """Find distinct contained strikes without quadratic underlay scans."""

    def __init__(self, count):
        self.size = 1 << (count - 1).bit_length()
        self.values = [float("inf")] * (2 * self.size)

    def insert(self, index, end):
        pos = index + self.size
        if end >= self.values[pos]:
            return
        self.values[pos] = end
        while pos > 1:
            pos //= 2
            self.values[pos] = min(self.values[2 * pos], self.values[2 * pos + 1])

    def first(self, lo, hi, end, node=1, left=0, right=None):
        if right is None:
            right = self.size
        if right <= lo or left >= hi or self.values[node] > end:
            return -1
        if right - left == 1:
            return left
        mid = (left + right) // 2
        found = self.first(lo, hi, end, 2 * node, left, mid)
        return found if found != -1 else self.first(lo, hi, end, 2 * node + 1, mid, right)


def _without_underlays(notes):
    starts = sorted({note.start for note in notes})
    positions = {start: index for index, start in enumerate(starts)}
    by_length = sorted(notes, key=lambda note: (note.end - note.start, note.on))
    tree = _MinEndTree(len(starts))
    removed = set()
    eligible = 0
    for note in by_length:
        while eligible < len(by_length):
            short = by_length[eligible]
            if 2 * (short.end - short.start) > note.end - note.start:
                break
            eligible += 1
            if short.on not in removed:
                tree.insert(positions[short.start], short.end)
        lo, hi = positions[note.start], bisect_left(starts, note.end)
        first = tree.first(lo, hi, note.end)
        if first != -1 and tree.first(first + 1, hi, note.end) != -1:
            removed.add(note.on)
    return [note for note in notes if note.on not in removed]


def resolve_note_overlaps(groups, mode="off", choose_mode=None):
    """Return event timelines; ask once per file, only for positive overlaps.

    Each group is (events, ending event anchor, has source track IDs). Type 2
    sequences are separate groups and therefore never overlap one another.
    The optional callback receives the number of overlapping same-key attacks
    routed to the same MIDI port; channels within that port will be collapsed.
    Returning None cancels the merge. No callback means legacy merge behavior.
    """
    if mode not in OVERLAP_MODES:
        raise ValueError("Invalid piano overlap behavior.")
    analyzed = []
    count = 0
    for events, end_event, source_tracks in groups:
        events = sorted(events, key=lambda event: event[:-1])
        event_ports = list(_event_ports([*events, end_event], source_tracks))
        notes_by_key = defaultdict(list)
        for note in _notes(events, end_event, source_tracks, event_ports):
            if note.end > note.start:
                notes_by_key[note.port, note.raw[1]].append(note)
        affected = {}
        for key, notes in notes_by_key.items():
            latest_end = -1
            overlaps = 0
            for note in sorted(notes, key=lambda note: (note.start, note.on)):
                overlaps += note.start < latest_end
                latest_end = max(latest_end, note.end)
            if overlaps:
                affected[key] = notes
                count += overlaps
        analyzed.append((events, end_event, affected, event_ports))
    if count and choose_mode is not None:
        mode = choose_mode(count)
        if mode is None:
            raise ChannelMergeCancelled()
        if mode not in OVERLAP_MODES:
            raise ValueError("Invalid piano overlap behavior.")
    if not count or mode == "off":
        return [events for events, _end, _affected, _ports in analyzed]

    result = []
    for events, end_event, affected, event_ports in analyzed:
        replacements = defaultdict(list)
        removed_events = set()
        for notes in affected.values():
            # Remove only the pairs being reconstructed. Other ports, zero-
            # duration pairs and unmatched releases are not repair candidates.
            for note in notes:
                removed_events.add(note.on)
                if (note.off < len(events) and len(events[note.off][-1]) == 3
                        and events[note.off][-1][0] & 0xF0 in (0x80, 0x90)):
                    removed_events.add(note.off)
            # Exact unisons become one strike with the stronger velocity.
            unique = {}
            for note in sorted(notes, key=lambda note: (-note.raw[2], note.on)):
                unique.setdefault((note.start, note.end), note)
            notes = list(unique.values())
            if mode == "smart":
                notes = _without_underlays(notes)
            notes.sort(key=lambda note: (
                note.start, note.end if mode == "smart" else -note.end,
                -note.raw[2], note.on,
            ))
            strikes = {}
            for note in notes:
                strikes.setdefault(note.start, note)
            notes = list(strikes.values())
            for index, note in enumerate(notes):
                if index + 1 < len(notes) and note.end >= notes[index + 1].start:
                    following = notes[index + 1]
                    note.off = (min(note.off, following.on) if note.end == following.start
                                else following.on)
                    note.end = following.start
                replacements[note.on].append((1, note.start, note.raw, note.port))
                replacements[note.off].append((0, note.end, note.release, note.port))
        repaired = []
        for index, event in enumerate([*events, end_event]):
            for _priority, tick, raw, port in sorted(replacements[index]):
                # A missing release may use another track's end anchor. Route
                # it explicitly, then restore that track's original port.
                if port != event_ports[index]:
                    prefix = b"\xff\x21\x01" + bytes([port])
                    repaired.append(_make_synthetic_event_like(event, tick, len(repaired), prefix))
                repaired.append(_make_synthetic_event_like(event, tick, len(repaired), raw))
                if port != event_ports[index]:
                    prefix = b"\xff\x21\x01" + bytes([event_ports[index]])
                    repaired.append(_make_synthetic_event_like(event, tick, len(repaired), prefix))
            raw = event[-1]
            if index == len(events):
                continue
            if index in removed_events:
                continue
            repaired.append(_make_synthetic_event_like(event, event[0], len(repaired), raw))
        result.append(repaired)
    return result
