"""Merge piano channels without rebuilding a Yamaha E-SEQ container."""

import os
import uuid
from collections import defaultdict

from .eseq_converter import (
    ESEQ_SIGNATURE,
    EseqConversionError,
    _declared_stream_end,
    _decode_15,
    _encode_vlq,
    _eseq_event_stream_start,
    _is_q11_eseq,
    is_clavinova_mda_eseq_bytes,
)
from .midi_channel_merger import (
    _acoustic_grand_program_event,
    _is_canonical_channel_merge,
    _merge_track_events,
)


def _stream_tokens(data, stream_start):
    """Retain original bytes and positions while exposing channel messages.

    Four-element merger events keep their token index as their track ID. This
    lets note releases and controller resets expand at their original command
    without re-encoding even one of the source's delay or tempo commands.
    """
    tokens = []
    events = []
    tick = 0
    pos = stream_start
    declared_end = _declared_stream_end(data, stream_start)

    def require(size):
        if pos + size > len(data):
            raise EseqConversionError("Encountered an incomplete E-SEQ event.")

    while pos < len(data):
        if declared_end is not None and pos >= declared_end:
            if all(value in (0, 0xF6) for value in data[pos:]):
                break
        start = pos
        status = data[pos]
        pos += 1
        raw = None
        editable = False

        if status == 0xF2:
            tokens.append(data[start:pos])
            break
        if status < 0x80 or status == 0xF6:
            pass
        elif status in (0xF1, 0xF3, 0xFF):
            require(1)
            value = data[pos]
            pos += 1
            if status == 0xF3:
                tick += value
            elif status == 0xFF:
                raw = b"\xFF\x20\x01" + bytes([value & 0x0F])
                editable = True
        elif status in (0xF4, 0xF9, 0xFB):
            require(2)
            if status == 0xF4:
                tick += _decode_15(data[pos], data[pos + 1])
            pos += 2
        elif status == 0xF0:
            wire = bytearray(b"\xF0")
            while pos < len(data):
                value = data[pos]
                pos += 1
                if value in (0xF3, 0xF4):
                    size = 1 if value == 0xF3 else 2
                    require(size)
                    tick += data[pos] if size == 1 else _decode_15(data[pos], data[pos + 1])
                    pos += size
                elif value == 0xF7:
                    wire.append(value)
                    break
                elif value < 0x80:
                    wire.append(value)
                else:
                    raise EseqConversionError(
                        f"Cannot safely merge embedded E-SEQ SysEx status 0x{value:02X}."
                    )
            else:
                raise EseqConversionError("Encountered an unterminated E-SEQ SysEx event.")
            # The shared merger recognizes resets in SMF packet form. The
            # original wire bytes, including embedded delays, stay untouched.
            raw = b"\xF0" + _encode_vlq(len(wire) - 1) + bytes(wire[1:])
        elif 0x80 <= status <= 0xEF:
            size = 1 if status & 0xF0 in (0xC0, 0xD0) else 2
            require(size)
            if any(value >= 0x80 for value in data[pos:pos + size]):
                raise EseqConversionError("Invalid data byte in an E-SEQ channel event.")
            pos += size
            raw = data[start:pos]
            editable = True
        else:
            raise EseqConversionError(f"Unsupported E-SEQ opcode 0x{status:02X}.")

        index = len(tokens)
        tokens.append(data[start:pos])
        if raw is not None:
            events.append((tick, index, int(editable), raw))

    return tokens, events, pos


def _is_dedicated_pedal(raw):
    return len(raw) == 3 and raw[0] == 0xB2 and raw[1] in (64, 67)


def merge_eseq_channels_to_channel0_bytes(eseq_bytes):
    """Merge note/instrument channels, retaining native Yamaha pedal detail.

    Headers, order keys, title bytes, SysEx, tempo/meter commands, delay
    encodings, and unknown trailing data are copied from the source. Only
    channel commands and the corresponding size/note-channel fields change.
    """
    data = eseq_bytes
    if len(data) < 0x57 or data[7:15] != ESEQ_SIGNATURE:
        raise EseqConversionError("This does not look like a Yamaha E-SEQ file.")
    stream_start = _eseq_event_stream_start(data)
    if len(data) <= stream_start:
        raise EseqConversionError("File is too small to contain Yamaha E-SEQ events.")
    tokens, events, stream_end = _stream_tokens(data, stream_start)
    is_mda = is_clavinova_mda_eseq_bytes(data)
    is_q11 = _is_q11_eseq(data)
    note_channels = {
        raw[0] & 0x0F for _tick, _index, _editable, raw in events
        if 0x80 <= raw[0] <= 0x9F
    }
    # Disklavier stores detailed sustain/soft values separately from its
    # ordinary binary piano pedals. They must remain on Yamaha's detail lane.
    preserve_pedal_detail = (
        not is_mda and not is_q11 and 2 not in note_channels
        and any(_is_dedicated_pedal(raw) for _tick, _index, _editable, raw in events)
    )
    pedal_reset_indexes = {
        index for _tick, index, _editable, raw in events
        if preserve_pedal_detail and raw[:2] == b"\xB2\x79"
    }
    editable_indexes = {
        index for _tick, index, editable, raw in events
        if editable and not (preserve_pedal_detail and _is_dedicated_pedal(raw))
    }
    merge_events = [
        (tick, index, 0, raw) for tick, index, _editable, raw in events
        if not (preserve_pedal_detail and _is_dedicated_pedal(raw))
    ]
    canonical_events = [
        (tick, index, raw) for tick, index, _order, raw in merge_events
        if index not in pedal_reset_indexes
    ]
    if _is_canonical_channel_merge(0, [{"original_events": canonical_events}]):
        return eseq_bytes, False

    merged, _changed, has_notes = _merge_track_events(merge_events)
    replacements = defaultdict(bytearray)
    for _tick, index, _order, raw in merged:
        if index not in editable_indexes:
            continue
        if raw[:3] == b"\xFF\x20\x01":
            # Preserve reserved high bits of Yamaha's channel prefix byte.
            raw = b"\xFF" + bytes([(tokens[index][1] & 0xF0) | raw[-1]])
        replacements[index].extend(raw)
    # A reset on the dedicated Yamaha lane must still release its pedals;
    # the shared merger also restores any ordinary controls moved off it.
    for index in pedal_reset_indexes:
        replacements[index].extend(tokens[index])

    stream = bytearray()
    # Retain the F1 start command at byte zero, especially for MDA detection.
    program_index = 1 if tokens and tokens[0][:1] == b"\xF1" else 0
    for index, token in enumerate(tokens):
        if has_notes and index == program_index:
            stream.extend(_acoustic_grand_program_event())
        stream.extend(replacements[index] if index in editable_indexes else token)

    if bytes(stream) == data[stream_start:stream_end]:
        return eseq_bytes, False

    delta = len(stream) - (stream_end - stream_start)
    header = bytearray(data[:stream_start])
    # MDA's first seven bytes are a fixed signature, not a length field.
    length_fields = [0x1F] if is_mda else [3, 0x1F]
    for offset in length_fields:
        value = int.from_bytes(header[offset:offset + 4], "little")
        if offset == 3 and value != stream_end:
            # Factory disks also use this word as opaque control metadata.
            # Adjust it only when it demonstrably records the used length.
            continue
        if value:
            updated = value + delta
            if not 0 <= updated <= 0xFFFFFFFF:
                raise EseqConversionError("E-SEQ output length is out of range.")
            header[offset:offset + 4] = updated.to_bytes(4, "little")
    normal_program_header = (
        not is_mda and not is_q11
        and header[0x19] == 0x40
        and int.from_bytes(header[0x1B:0x1F], "little") == 0x50
    )
    if normal_program_header:
        header[0x54:0x56] = int(bool(note_channels)).to_bytes(2, "little")
        if 2 in note_channels and header[0x51] == 1:
            # It was an instrument channel, so there is no remaining native
            # detail lane after all its controllers move to piano.
            header[0x51] = 0

    suffix = data[stream_end:]
    if suffix and len(data) % 2048 == 0 and all(value in (0, 0xF6) for value in suffix):
        # Preserve sector alignment without carrying old padding into music.
        size = -(len(header) + len(stream)) % 2048
        suffix = suffix[:size].ljust(size, suffix[-1:])
    return bytes(header) + bytes(stream) + suffix, True


def merge_eseq_channels_to_channel0_path(source_path, dest_path):
    """Atomically write an E-SEQ merge; leave no-op destinations untouched."""
    source_path = os.fspath(source_path)
    dest_path = os.fspath(dest_path)
    with open(source_path, "rb") as handle:
        merged, changed = merge_eseq_channels_to_channel0_bytes(handle.read())
    if not changed:
        return False
    suffix = f".aps_channel_merge_{uuid.uuid4().hex}.tmp"
    temp_path = dest_path + (suffix.encode() if isinstance(dest_path, bytes) else suffix)
    try:
        with open(temp_path, "wb") as handle:
            handle.write(merged)
        os.replace(temp_path, dest_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
    return True
