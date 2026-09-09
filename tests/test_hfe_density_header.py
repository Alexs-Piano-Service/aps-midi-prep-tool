"""Generated HFE interface density must match its selected disk format."""

from pathlib import Path

import pytest

from aps_midi_prep_tool_app import floppy_image


# Values come from the HxC HFE specification, independently of app constants.
_EXPECTED_INTERFACES = {
    "ibm.160": 0x00,
    "ibm.180": 0x00,
    "ibm.320": 0x00,
    "ibm.360": 0x00,
    "ibm.720": 0x00,
    "ibm.800": 0x00,
    "ibm.1200": 0x01,
    "ibm.1440": 0x01,
    "ibm.2880": 0x08,
}


def _hfe_bytes(*, bitrate=250, interface=0xFF):
    header = bytearray(b"\xff" * 512)
    header[:8] = b"HXCPICFE"
    header[8:11] = bytes((0, 80, 2))
    header[12:14] = bitrate.to_bytes(2, "little")
    header[14:16] = (300).to_bytes(2, "little")
    header[16] = interface
    header[17:20] = bytes((1, 1, 0))
    # Include nontrivial bytes after the header to detect accidental rewriting.
    return bytes(header) + bytes(range(256)) * 4


@pytest.mark.parametrize("disk_format,interface", _EXPECTED_INTERFACES.items())
def test_interface_density_matches_format_without_touching_bitrate_or_track_data(tmp_path, disk_format, interface):
    before = _hfe_bytes(bitrate=1000 if interface == 8 else 500 if interface == 1 else 250)
    output = tmp_path / "generated.hfe"
    output.write_bytes(before)

    assert floppy_image._normalize_nalbantov_hfe_header(
        output, floppy_image.DISK_FORMAT_BY_KEY[disk_format],
    )

    expected = bytearray(before)
    expected[0x0B] = 0x00  # ISO IBM MFM
    expected[0x10] = interface
    assert output.read_bytes() == bytes(expected)
    assert not floppy_image._normalize_nalbantov_hfe_header(output, disk_format)
    assert output.read_bytes() == bytes(expected)


def test_every_supported_ibm_format_has_a_verified_interface_density():
    assert set(_EXPECTED_INTERFACES) == {
        key for key in floppy_image.DISK_FORMAT_BY_KEY if key.startswith("ibm.")
    }


def test_existing_wrong_hd_flag_is_corrected_for_720_kb_output(tmp_path):
    output = tmp_path / "generated.hfe"
    before = _hfe_bytes(interface=0x01)
    output.write_bytes(before)

    assert floppy_image._normalize_nalbantov_hfe_header(output, "ibm.720")

    assert output.read_bytes()[0x10] == 0x00
    assert output.read_bytes()[0x0C:0x10] == before[0x0C:0x10]


@pytest.mark.parametrize("disk_format", ["mac.800", "ibm.unknown", None])
def test_unrecognized_format_does_not_get_a_guessed_ibm_header(tmp_path, disk_format):
    output = tmp_path / "generated.hfe"
    before = _hfe_bytes(interface=0x07)
    output.write_bytes(before)

    assert not floppy_image._normalize_nalbantov_hfe_header(output, disk_format)
    assert output.read_bytes() == before


@pytest.mark.parametrize("name,data", [
    ("generated.img", _hfe_bytes()),
    ("generated.hfe", b"HXCPICFE\x00\x50"),
    ("generated.hfe", b"badmagic" + _hfe_bytes()[8:]),
])
def test_other_or_unrecognized_containers_are_unchanged(tmp_path, name, data):
    output = tmp_path / name
    output.write_bytes(data)

    assert not floppy_image._normalize_nalbantov_hfe_header(output, "ibm.720")
    assert output.read_bytes() == data


def test_greaseweazle_conversion_normalizes_output_without_modifying_source(tmp_path, monkeypatch):
    original = tmp_path / "original.hfe"
    output = tmp_path / "generated.hfe"
    source_bytes = _hfe_bytes()
    original.write_bytes(source_bytes)
    monkeypatch.setattr(floppy_image, "_find_gw", lambda: "gw")

    def run_command(args, message, **kwargs):
        assert args == ["gw", "convert", "--format=ibm.720", str(original), str(output)]
        Path(args[-1]).write_bytes(source_bytes)
        return ""

    monkeypatch.setattr(floppy_image, "_run_command", run_command)

    floppy_image._gw_convert(str(original), str(output), "ibm.720")

    assert original.read_bytes() == source_bytes
    expected = bytearray(source_bytes)
    expected[0x0B] = expected[0x10] = 0
    assert output.read_bytes() == bytes(expected)
