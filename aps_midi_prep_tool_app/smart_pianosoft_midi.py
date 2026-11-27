"""Smart PianoSoft MIDI metadata and consent recognition, without audio dependencies."""

from dataclasses import dataclass
import io
import math

import mido


class SynchronizationError(ValueError):
    """The MIDI and audio cannot be paired confidently."""


class SynchronizationCancelled(InterruptedError):
    pass


@dataclass(frozen=True)
class Fingerprint:
    reference_seconds: float
    samples: tuple[int, ...]
    flags: int = 0
    precision: int = 0


@dataclass(frozen=True)
class TimedMidiEvent:
    time_seconds: float
    data: bytes


@dataclass(frozen=True)
class SPSMetadata:
    cd_track: int | None
    head: Fingerprint
    tail: Fingerprint
    events: tuple[TimedMidiEvent, ...]
    duration_seconds: float


def _timecode(data):
    minute, second, frame, subframe = data
    if not (0 <= minute < 128 and 0 <= second < 60
            and 0 <= frame < 75 and 0 <= subframe < 128):
        raise SynchronizationError("Invalid Smart PianoSoft reference time.")
    # Mark IV seqSyncIsSysEx subtracts raw times >= 100 minutes from
    # 6,000 seconds. These are negative references, not a clock wrapping to
    # zero: a fingerprint can begin in the silence before a CD track starts.
    if minute >= 100:
        return -((minute - 100) * 60 + second + frame / 75 + subframe / 9600)
    return minute * 60 + second + frame / 75 + subframe / 9600


def _cd_track_reference(data):
    """Read the intended CD track from Yamaha's 43 71 7D meta event.

    The payload contains a selected absolute MSF position, an entry count,
    then the CD's track starts followed by its lead-out. The selected position
    identifies a table entry independently of the floppy's catalog order.
    Treat unsupported or malformed metadata as absent; fingerprints must still
    verify the recording, including when another CD edition changes its TOC.
    """
    if len(data) < 7 or data[:3] != (0x43, 0x71, 0x7D):
        return None
    count = data[6]
    if not 2 <= count <= 100 or len(data) != 7 + count * 3:
        return None

    def position(msf):
        minute, second, frame = msf
        if not (0 <= minute < 100 and 0 <= second < 60 and 0 <= frame < 75):
            return None
        return (minute * 60 + second) * 75 + frame

    selected = position(data[3:6])
    starts = tuple(position(data[index:index + 3]) for index in range(7, len(data), 3))
    if selected is None or any(value is None for value in starts):
        return None
    if any(first >= second for first, second in zip(starts, starts[1:])):
        return None
    if selected not in starts[:-1]:
        return None
    return starts.index(selected) + 1


def parse_sps_midi(path):
    """Parse an SMF, preserving its complete tempo map in event timestamps.

    Yamaha's 43 71 05 01 message is CD control, not a track number. The
    sequencer-specific 43 71 7D meta event can identify the intended CD track;
    selection must still be verified by both audio fingerprints.
    """
    # Keep filesystem errors separate from mido's OSError for malformed SMF
    # headers. Consent recognition may reject invalid MIDI as non-Smart content,
    # but it must report an actual failed read of the local working copy.
    if isinstance(path, (bytes, bytearray)):
        payload = path
    else:
        with open(str(path), "rb") as source:
            payload = source.read()
    try:
        midi = mido.MidiFile(file=io.BytesIO(payload), clip=False)
        if midi.type not in (0, 1) or midi.ticks_per_beat <= 0:
            raise SynchronizationError("Smart PianoSoft requires a synchronous, PPQN MIDI file.")
        fingerprints = {}
        cd_references = []
        events = []
        elapsed = 0.0
        for message in midi:
            elapsed += message.time
            if not math.isfinite(elapsed) or elapsed < 0:
                raise SynchronizationError("Invalid MIDI event timing.")
            if message.is_meta:
                if message.type == "sequencer_specific" and tuple(message.data[:3]) == (0x43, 0x71, 0x7D):
                    cd_references.append(_cd_track_reference(tuple(message.data)))
                continue
            if message.type == "sysex":
                data = tuple(message.data)
                if data[:3] == (0x43, 0x71, 0x7B):
                    if len(data) < 4:
                        raise SynchronizationError("Truncated Smart PianoSoft synchronization message.")
                    kind = data[3]
                    if kind in (0, 2):
                        if len(data) != 266 or kind in fingerprints:
                            raise SynchronizationError("Missing, duplicate, or malformed Smart PianoSoft fingerprints.")
                        if data[8] >> 4 or data[9] > 15:
                            raise SynchronizationError("Unsupported Smart PianoSoft fingerprint format.")
                        samples = tuple(value if value < 64 else value - 128 for value in data[10:])
                        fingerprints[kind] = Fingerprint(_timecode(data[4:8]), samples, data[8], data[9])
                    elif kind == 1:
                        if len(data) != 8:
                            raise SynchronizationError("Malformed Smart PianoSoft time marker.")
                        _timecode(data[4:8])
                    else:
                        raise SynchronizationError("Unsupported Smart PianoSoft synchronization message.")
                    continue
                # CD control and the source's Smart PianoSoft header do not
                # belong in the outgoing piano stream. Preserve unrelated
                # Yamaha musical SysEx.
                if data[:3] in {(0x43, 0x71, 0x05), (0x43, 0x71, 0x7E)}:
                    continue
            events.append(TimedMidiEvent(elapsed, bytes(message.bytes())))
    except (EOFError, OSError, ValueError, KeyError, IndexError) as exc:
        if isinstance(exc, SynchronizationError):
            raise
        raise SynchronizationError(f"Could not read Smart PianoSoft MIDI: {exc}") from exc
    if set(fingerprints) != {0, 2}:
        raise SynchronizationError("This MIDI lacks the two Smart PianoSoft CD fingerprints.")
    head, tail = fingerprints[0], fingerprints[2]
    if tail.reference_seconds - head.reference_seconds < 1:
        raise SynchronizationError("Smart PianoSoft reference times are too close or out of order.")
    if not events:
        raise SynchronizationError("The MIDI contains no musical events.")
    cd_track = cd_references[0] if cd_references and len(set(cd_references)) == 1 else None
    return SPSMetadata(cd_track, head, tail, tuple(events), elapsed)


