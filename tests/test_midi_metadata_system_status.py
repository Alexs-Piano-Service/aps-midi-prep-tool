"""Title edits validate SMF event framing before publishing changed files."""

import pytest

from aps_midi_prep_tool_app.midi_metadata import MidiTitleFormatError, write_midi_title_to_path


_TITLE = b"\x00\xff\x03\x03Old"
_END = b"\x00\xff\x2f\x00"
_WIRE_STATUSES = [
    (0xF1, b"\x00"), (0xF2, b"\x00\x00"), (0xF3, b"\x00"),
    (0xF4, b""), (0xF5, b""), (0xF6, b""), (0xF8, b""),
    (0xF9, b""), (0xFA, b""), (0xFB, b""), (0xFC, b""),
    (0xFD, b""), (0xFE, b""),
]


def _midi(*tracks):
    header = (int(len(tracks) > 1).to_bytes(2, "big")
              + len(tracks).to_bytes(2, "big") + b"\x00\x60")
    return b"MThd\x00\x00\x00\x06" + header + b"".join(
        b"MTrk" + len(track).to_bytes(4, "big") + track for track in tracks
    )


@pytest.mark.parametrize("status,data", _WIRE_STATUSES, ids=lambda value: f"{value:02X}" if isinstance(value, int) else None)
@pytest.mark.parametrize("after_title", [False, True])
@pytest.mark.parametrize("destination_kind", ["new", "existing", "in_place"])
def test_title_edit_rejects_direct_system_status_without_changing_files(
    tmp_path, status, data, after_title, destination_kind,
):
    event = b"\x00" + bytes([status]) + data
    source_bytes = _midi((_TITLE + event if after_title else event + _TITLE) + _END)
    source = tmp_path / "source.mid"
    source.write_bytes(source_bytes)
    destination = source if destination_kind == "in_place" else tmp_path / "destination.mid"
    if destination_kind == "existing":
        destination.write_bytes(b"existing output")

    with pytest.raises(MidiTitleFormatError, match=f"Unsupported system status byte: 0x{status:02X}"):
        write_midi_title_to_path(source, "Renamed", destination)

    assert source.read_bytes() == source_bytes
    if destination_kind == "new":
        assert not destination.exists()
    elif destination_kind == "existing":
        assert destination.read_bytes() == b"existing output"
    assert not list(tmp_path.glob(".aps_write_*"))


def test_title_edit_rejects_direct_system_status_in_a_later_track(tmp_path):
    source = tmp_path / "source.mid"
    source_bytes = _midi(_TITLE + _END, b"\x00\xf8" + _END)
    source.write_bytes(source_bytes)
    destination = tmp_path / "destination.mid"

    with pytest.raises(MidiTitleFormatError, match="Unsupported system status byte: 0xF8"):
        write_midi_title_to_path(source, "Renamed", destination)

    assert source.read_bytes() == source_bytes
    assert not destination.exists()


@pytest.mark.parametrize("event_prefix", [b"\x00\xf7", b"\x00\xff\x7f"], ids=["escaped", "meta"])
def test_title_edit_preserves_system_status_bytes_inside_length_delimited_payloads(tmp_path, event_prefix):
    payload = bytes(range(0xF0, 0x100))
    event = event_prefix + bytes([len(payload)]) + payload
    source_bytes = _midi(_TITLE + event + _END)
    source = tmp_path / "source.mid"
    source.write_bytes(source_bytes)
    destination = tmp_path / "destination.mid"

    write_midi_title_to_path(source, "Renamed", destination)

    assert source.read_bytes() == source_bytes
    assert destination.read_bytes() == _midi(b"\x00\xff\x03\x07Renamed" + event + _END)
