"""Playback flags supported by the Mark IV's E-SEQ conversion library.

These describe newly generated song data. Imported headers may carry additional
firmware-specific bits and should retain their original flag bytes.
"""

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class EseqPlaybackFlags:
    note_channel_mask: int = 0
    has_half_pedal: bool = False
    has_xg: bool = False


def analyze_eseq_playback_flags(messages: Iterable[bytes]) -> EseqPlaybackFlags:
    """Describe validated messages after channel and pedal routing.

    SysEx messages use decoded wire bytes, with an initial F0 packet and any
    subsequent F7 continuation packet markers. A continuation marker is not
    part of the transmitted body. Retain only the six-byte XG prefix while
    waiting for the actual terminator, so large dumps need no extra copy.

    Yamaha's ``esqwWriteMidi`` records note-on and note-off channels, including
    zero-velocity note-ons. ``hpdl`` stores detailed CC64/67 on MIDI channel 3;
    other controllers do not establish that representation. Channel 3 notes
    prevent claiming it as a dedicated pedal channel. ``tgid`` detects the
    XG-On prefix, allowing any device number in its low nibble.
    """
    note_channel_mask = 0
    has_half_pedal = False
    has_xg = False
    sysex_prefix = None

    for raw in messages:
        if not raw:
            continue
        status = raw[0]
        if status in (0xF0, 0xF7):
            if status == 0xF0:
                sysex_prefix = bytearray()
            if sysex_prefix is None:
                continue
            payload = memoryview(raw)[1:]
            complete = bool(payload) and payload[-1] == 0xF7
            body = payload[:-1] if complete else payload
            sysex_prefix.extend(body[:6 - len(sysex_prefix)])
            if complete:
                if (
                    len(sysex_prefix) == 6
                    and sysex_prefix[0] == 0x43
                    and sysex_prefix[1] & 0xF0 == 0x10
                    and sysex_prefix[2:] == b"\x4C\x00\x00\x7E"
                ):
                    has_xg = True
                sysex_prefix = None
            continue
        if len(raw) != 3:
            continue
        if 0x80 <= status <= 0x9F:
            note_channel_mask |= 1 << (status & 0x0F)
        elif status == 0xB2 and raw[1] in (64, 67):
            has_half_pedal = True

    # Yamaha checks note-channel occupancy before reserving channel 3 for
    # detailed pedals. Its ordinary instrument controllers are ambiguous.
    has_half_pedal = has_half_pedal and not (note_channel_mask & 0x0004)
    return EseqPlaybackFlags(note_channel_mask, has_half_pedal, has_xg)
