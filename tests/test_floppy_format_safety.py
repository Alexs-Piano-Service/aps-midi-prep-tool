import ctypes
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMAT_BY_KEY,
    FloppyDriveInfo,
    FloppyImageError,
    FloppyImageSession,
    _WindowsVolumeHandle,
    prepare_yamaha_bytes,
    usb_floppy_format_capacity_error,
)
from aps_midi_prep_tool_app.main_window import MidiTitleWindow


_FAT12_720_SECTOR_SIZE = 512
_FAT12_720_FAT_SIZE = 3 * _FAT12_720_SECTOR_SIZE
_FAT12_720_FAT1_OFFSET = _FAT12_720_SECTOR_SIZE
_FAT12_720_FAT2_OFFSET = _FAT12_720_FAT1_OFFSET + _FAT12_720_FAT_SIZE
_FAT12_720_ROOT_OFFSET = _FAT12_720_FAT2_OFFSET + _FAT12_720_FAT_SIZE
_FAT12_720_DATA_OFFSET = 14 * _FAT12_720_SECTOR_SIZE


def _fat12_720_boot_sector():
    boot = bytearray(_FAT12_720_SECTOR_SIZE)
    boot[0:3] = b"\xEB\x3C\x90"
    boot[3:11] = b"IBM  6.0"
    boot[11:13] = (512).to_bytes(2, "little")
    boot[13] = 2
    boot[14:16] = (1).to_bytes(2, "little")
    boot[16] = 2
    boot[17:19] = (112).to_bytes(2, "little")
    boot[19:21] = (1440).to_bytes(2, "little")
    boot[21] = 0xF9
    boot[22:24] = (3).to_bytes(2, "little")
    boot[24:26] = (9).to_bytes(2, "little")
    boot[26:28] = (2).to_bytes(2, "little")
    boot[38] = 0x29
    boot[39:43] = bytes.fromhex("e7 18 40 0d")
    boot[43:54] = b"NO NAME    "
    boot[54:62] = b"FAT12   "
    boot[510:512] = b"\x55\xAA"
    return bytes(boot)


def _fat12_720_unsigned_bpb_stub():
    boot = bytearray(_FAT12_720_SECTOR_SIZE)
    boot[0:3] = b"\xEB\x3C\x90"
    boot[3:11] = b"AN CHEN "
    boot[11:13] = (512).to_bytes(2, "little")
    boot[13] = 2
    boot[14:16] = (1).to_bytes(2, "little")
    boot[16] = 2
    boot[17:19] = (112).to_bytes(2, "little")
    boot[19:21] = (1440).to_bytes(2, "little")
    boot[21] = 0xF9
    boot[22:24] = (3).to_bytes(2, "little")
    boot[24:26] = (9).to_bytes(2, "little")
    boot[26:28] = (2).to_bytes(2, "little")
    return bytes(boot)


def _fat12_720_boot_in_second_fat_image():
    image = bytearray(DISK_FORMAT_BY_KEY["ibm.720"].size_bytes)
    image[:_FAT12_720_SECTOR_SIZE] = b"\xF6" * _FAT12_720_SECTOR_SIZE

    fat1 = bytearray(_FAT12_720_FAT_SIZE)
    fat1[:5] = b"\xF9\xFF\xFF\xFF\x0F"
    image[
        _FAT12_720_FAT1_OFFSET:_FAT12_720_FAT1_OFFSET + _FAT12_720_FAT_SIZE
    ] = fat1
    image[
        _FAT12_720_FAT2_OFFSET:_FAT12_720_FAT2_OFFSET + _FAT12_720_SECTOR_SIZE
    ] = _fat12_720_boot_sector()

    midi = (
        b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xE0"
        b"MTrk\x00\x00\x00\x04\x00\xFF\x2F\x00"
    )
    root_entry = bytearray(32)
    root_entry[:11] = b"TRACK01 MID"
    root_entry[11] = 0x20
    root_entry[26:28] = (2).to_bytes(2, "little")
    root_entry[28:32] = len(midi).to_bytes(4, "little")
    image[_FAT12_720_ROOT_OFFSET:_FAT12_720_ROOT_OFFSET + 32] = root_entry
    image[_FAT12_720_DATA_OFFSET:_FAT12_720_DATA_OFFSET + len(midi)] = midi
    return bytes(image)


def _fat12_720_unsigned_bpb_in_second_fat_image():
    image = bytearray(_fat12_720_boot_in_second_fat_image())
    image[
        _FAT12_720_FAT2_OFFSET:_FAT12_720_FAT2_OFFSET + _FAT12_720_SECTOR_SIZE
    ] = _fat12_720_unsigned_bpb_stub()
    return bytes(image)


def _populated_standard_fat12_image(
    *,
    total_sectors,
    sectors_per_cluster,
    root_entries,
    media_descriptor,
    sectors_per_fat,
    sectors_per_track,
):
    image = bytearray(total_sectors * _FAT12_720_SECTOR_SIZE)
    boot = bytearray(_fat12_720_boot_sector())
    boot[13] = sectors_per_cluster
    boot[17:19] = root_entries.to_bytes(2, "little")
    boot[19:21] = total_sectors.to_bytes(2, "little")
    boot[21] = media_descriptor
    boot[22:24] = sectors_per_fat.to_bytes(2, "little")
    boot[24:26] = sectors_per_track.to_bytes(2, "little")
    image[:_FAT12_720_SECTOR_SIZE] = boot

    fat_size = sectors_per_fat * _FAT12_720_SECTOR_SIZE
    fat1_offset = _FAT12_720_SECTOR_SIZE
    fat2_offset = fat1_offset + fat_size
    fat = bytearray(fat_size)
    fat[:5] = bytes([media_descriptor, 0xFF, 0xFF, 0xFF, 0x0F])
    image[fat1_offset:fat1_offset + fat_size] = fat
    image[fat2_offset:fat2_offset + fat_size] = fat

    root_offset = fat2_offset + fat_size
    root_entry = bytearray(32)
    root_entry[:11] = b"TRACK01 MID"
    root_entry[11] = 0x20
    root_entry[26:28] = (2).to_bytes(2, "little")
    root_entry[28:32] = (4).to_bytes(4, "little")
    image[root_offset:root_offset + 32] = root_entry
    root_size = ((root_entries * 32 + 511) // 512) * 512
    image[root_offset + root_size:root_offset + root_size + 4] = b"MThd"
    return bytes(image)


def _unsigned_bpb_stub_in_second_fat_image(**geometry):
    image = bytearray(_populated_standard_fat12_image(**geometry))
    stub = bytearray(image[:_FAT12_720_SECTOR_SIZE])
    stub[28:] = b"\x00" * (_FAT12_720_SECTOR_SIZE - 28)
    image[:_FAT12_720_SECTOR_SIZE] = b"\xF6" * _FAT12_720_SECTOR_SIZE
    fat_size = geometry["sectors_per_fat"] * _FAT12_720_SECTOR_SIZE
    fat2_offset = _FAT12_720_SECTOR_SIZE + fat_size
    image[fat2_offset:fat2_offset + _FAT12_720_SECTOR_SIZE] = stub
    return bytes(image)


_SUPPORTED_NON_720_FAT12_LAYOUTS = (
    pytest.param(
        {
            "total_sectors": 1600,
            "sectors_per_cluster": 2,
            "root_entries": 112,
            "media_descriptor": 0xF9,
            "sectors_per_fat": 3,
            "sectors_per_track": 10,
        },
        id="ibm-800k",
    ),
    pytest.param(
        {
            "total_sectors": 2880,
            "sectors_per_cluster": 1,
            "root_entries": 224,
            "media_descriptor": 0xF0,
            "sectors_per_fat": 9,
            "sectors_per_track": 18,
        },
        id="ibm-1440k",
    ),
)


def _usb_drive(size_bytes):
    return FloppyDriveInfo(
        path="A:",
        size_bytes=size_bytes,
        transport="usb",
        model="Windows removable drive A:",
    )


def test_usb_format_capacity_rejects_2880_image_for_1440_drive():
    drive = _usb_drive(DISK_FORMAT_BY_KEY["ibm.1440"].size_bytes)
    disk_format = DISK_FORMAT_BY_KEY["ibm.2880"]

    message = usb_floppy_format_capacity_error(drive, disk_format)

    assert "IBM 2.88M ED" in message
    assert "2,949,120 bytes" in message
    assert "1,474,560 bytes" in message
    assert "will not attempt an oversized raw write" in message


def test_prepare_yamaha_bytes_repairs_boot_sector_stored_over_second_fat(tmp_path):
    source = _fat12_720_boot_in_second_fat_image()
    output_path = tmp_path / "repaired.img"

    result = prepare_yamaha_bytes(source, output_path)

    repaired = output_path.read_bytes()
    assert result.changed
    assert len(repaired) == DISK_FORMAT_BY_KEY["ibm.720"].size_bytes
    relocated_boot = source[
        _FAT12_720_FAT2_OFFSET:_FAT12_720_FAT2_OFFSET + _FAT12_720_SECTOR_SIZE
    ]
    assert repaired[:_FAT12_720_SECTOR_SIZE] == relocated_boot
    assert repaired[510:512] == b"\x55\xAA"
    fat1 = source[
        _FAT12_720_FAT1_OFFSET:_FAT12_720_FAT1_OFFSET + _FAT12_720_FAT_SIZE
    ]
    assert repaired[
        _FAT12_720_FAT1_OFFSET:_FAT12_720_FAT1_OFFSET + _FAT12_720_FAT_SIZE
    ] == fat1
    assert repaired[
        _FAT12_720_FAT2_OFFSET:_FAT12_720_FAT2_OFFSET + _FAT12_720_FAT_SIZE
    ] == fat1
    assert repaired[_FAT12_720_ROOT_OFFSET:] == source[_FAT12_720_ROOT_OFFSET:]


def test_prepare_yamaha_bytes_repairs_unsigned_bpb_stub_stored_over_second_fat(tmp_path):
    source = _fat12_720_unsigned_bpb_in_second_fat_image()
    output_path = tmp_path / "repaired.img"

    result = prepare_yamaha_bytes(source, output_path)

    repaired = output_path.read_bytes()
    relocated_stub = source[
        _FAT12_720_FAT2_OFFSET:_FAT12_720_FAT2_OFFSET + _FAT12_720_SECTOR_SIZE
    ]
    repaired_boot = repaired[:_FAT12_720_SECTOR_SIZE]
    fat1 = source[
        _FAT12_720_FAT1_OFFSET:_FAT12_720_FAT1_OFFSET + _FAT12_720_FAT_SIZE
    ]

    assert result.changed
    assert len(repaired) == DISK_FORMAT_BY_KEY["ibm.720"].size_bytes
    assert relocated_stub[3:11] == b"AN CHEN "
    assert relocated_stub[510:512] == b"\x00\x00"
    assert repaired_boot != relocated_stub
    assert repaired_boot[11:28] == relocated_stub[11:28]
    assert repaired_boot[510:512] == b"\x55\xAA"
    assert repaired[
        _FAT12_720_FAT1_OFFSET:_FAT12_720_FAT1_OFFSET + _FAT12_720_FAT_SIZE
    ] == fat1
    assert repaired[
        _FAT12_720_FAT2_OFFSET:_FAT12_720_FAT2_OFFSET + _FAT12_720_FAT_SIZE
    ] == fat1
    assert repaired[_FAT12_720_ROOT_OFFSET:] == source[_FAT12_720_ROOT_OFFSET:]


def test_prepare_yamaha_bytes_repairs_relocated_boot_when_primary_signature_is_damaged(tmp_path):
    source = bytearray(_fat12_720_boot_in_second_fat_image())
    relocated_boot = bytes(
        source[
            _FAT12_720_FAT2_OFFSET:_FAT12_720_FAT2_OFFSET + _FAT12_720_SECTOR_SIZE
        ]
    )
    source[:_FAT12_720_SECTOR_SIZE] = relocated_boot
    source[510:512] = b"\x00\x00"
    output_path = tmp_path / "repaired.img"

    result = prepare_yamaha_bytes(bytes(source), output_path)

    repaired = output_path.read_bytes()
    assert result.changed
    assert repaired[:_FAT12_720_SECTOR_SIZE] == relocated_boot


def test_prepare_yamaha_bytes_leaves_valid_primary_boot_unchanged(tmp_path):
    source = bytearray(_fat12_720_boot_in_second_fat_image())
    source[:_FAT12_720_SECTOR_SIZE] = source[
        _FAT12_720_FAT2_OFFSET:_FAT12_720_FAT2_OFFSET + _FAT12_720_SECTOR_SIZE
    ]
    output_path = tmp_path / "unchanged.img"

    result = prepare_yamaha_bytes(bytes(source), output_path)

    assert result.changed and not result.boot_sector_repaired
    repaired = output_path.read_bytes()
    assert repaired[:_FAT12_720_FAT2_OFFSET] == source[:_FAT12_720_FAT2_OFFSET]
    assert repaired[_FAT12_720_FAT2_OFFSET:_FAT12_720_FAT2_OFFSET + 3 * _FAT12_720_SECTOR_SIZE] == source[_FAT12_720_SECTOR_SIZE:4 * _FAT12_720_SECTOR_SIZE]
    assert repaired[_FAT12_720_FAT2_OFFSET + 3 * _FAT12_720_SECTOR_SIZE:] == source[_FAT12_720_FAT2_OFFSET + 3 * _FAT12_720_SECTOR_SIZE:]


def test_prepare_yamaha_bytes_rejects_unsigned_relocated_bpb_when_primary_boot_is_valid(tmp_path):
    source = bytearray(_fat12_720_unsigned_bpb_in_second_fat_image())
    source[:_FAT12_720_SECTOR_SIZE] = _fat12_720_boot_sector()
    output_path = tmp_path / "unchanged.img"

    result = prepare_yamaha_bytes(bytes(source), output_path)

    assert result.changed and not result.boot_sector_repaired
    repaired = output_path.read_bytes()
    assert repaired[:_FAT12_720_FAT2_OFFSET] == source[:_FAT12_720_FAT2_OFFSET]
    assert repaired[_FAT12_720_FAT2_OFFSET:_FAT12_720_FAT2_OFFSET + 3 * _FAT12_720_SECTOR_SIZE] == source[_FAT12_720_SECTOR_SIZE:4 * _FAT12_720_SECTOR_SIZE]
    assert repaired[_FAT12_720_FAT2_OFFSET + 3 * _FAT12_720_SECTOR_SIZE:] == source[_FAT12_720_FAT2_OFFSET + 3 * _FAT12_720_SECTOR_SIZE:]


@pytest.mark.parametrize("geometry", _SUPPORTED_NON_720_FAT12_LAYOUTS)
def test_prepare_yamaha_bytes_leaves_populated_supported_fat12_image_unchanged(
    tmp_path,
    geometry,
):
    image = _populated_standard_fat12_image(**geometry)
    output_path = tmp_path / "unchanged.img"

    result = prepare_yamaha_bytes(image, output_path)

    assert not result.changed
    assert output_path.read_bytes() == image


def test_prepare_yamaha_bytes_leaves_valid_noncanonical_primary_bpb_unchanged(tmp_path):
    image = bytearray(
        _populated_standard_fat12_image(
            total_sectors=1440,
            sectors_per_cluster=2,
            root_entries=112,
            media_descriptor=0xF9,
            sectors_per_fat=3,
            sectors_per_track=9,
        )
    )
    image[24:26] = (8).to_bytes(2, "little")
    output_path = tmp_path / "unchanged.img"

    result = prepare_yamaha_bytes(bytes(image), output_path)

    assert not result.changed
    assert output_path.read_bytes() == image


@pytest.mark.parametrize("geometry", _SUPPORTED_NON_720_FAT12_LAYOUTS)
def test_prepare_yamaha_bytes_repairs_supported_unsigned_bpb_stub_layout(
    tmp_path,
    geometry,
):
    source = _unsigned_bpb_stub_in_second_fat_image(**geometry)
    output_path = tmp_path / "repaired.img"
    fat_size = geometry["sectors_per_fat"] * _FAT12_720_SECTOR_SIZE
    fat1_offset = _FAT12_720_SECTOR_SIZE
    fat2_offset = fat1_offset + fat_size
    root_offset = fat2_offset + fat_size

    result = prepare_yamaha_bytes(source, output_path)

    repaired = output_path.read_bytes()
    relocated_stub = source[fat2_offset:fat2_offset + _FAT12_720_SECTOR_SIZE]
    fat1 = source[fat1_offset:fat1_offset + fat_size]
    assert result.changed
    assert len(repaired) == len(source)
    assert relocated_stub[510:512] == b"\x00\x00"
    assert repaired[:_FAT12_720_SECTOR_SIZE] != relocated_stub
    assert repaired[11:28] == relocated_stub[11:28]
    assert repaired[510:512] == b"\x55\xAA"
    assert repaired[fat1_offset:fat1_offset + fat_size] == fat1
    assert repaired[fat2_offset:fat2_offset + fat_size] == fat1
    assert repaired[root_offset:] == source[root_offset:]


@pytest.mark.parametrize(
    ("offset", "replacement"),
    [
        (_FAT12_720_FAT2_OFFSET + 510, b"\x00\x00"),
        (_FAT12_720_FAT1_OFFSET, b"\x00\x00\x00"),
        (_FAT12_720_FAT2_OFFSET + 19, (2880).to_bytes(2, "little")),
        (_FAT12_720_ROOT_OFFSET, b"\x01"),
    ],
    ids=(
        "unsigned-full-boot-sector-is-not-a-minimal-bpb-stub",
        "primary-fat-signature-missing",
        "displaced-bpb-has-wrong-geometry",
        "root-directory-is-implausible",
    ),
)
def test_prepare_yamaha_bytes_rejects_malformed_boot_in_second_fat_variants(
    tmp_path,
    offset,
    replacement,
):
    source = bytearray(_fat12_720_boot_in_second_fat_image())
    source[offset:offset + len(replacement)] = replacement
    output_path = tmp_path / "unchanged.img"

    result = prepare_yamaha_bytes(bytes(source), output_path)

    assert not result.changed
    assert output_path.read_bytes() == source


def test_prepare_yamaha_bytes_rejects_signed_relocated_boot_with_mismatched_fat_tail(tmp_path):
    source = bytearray(_fat12_720_boot_in_second_fat_image())
    source[_FAT12_720_FAT2_OFFSET + _FAT12_720_SECTOR_SIZE + 17] = 0x01
    output_path = tmp_path / "unchanged.img"

    result = prepare_yamaha_bytes(bytes(source), output_path)

    assert not result.changed
    assert output_path.read_bytes() == source


@pytest.mark.parametrize(
    ("offset", "replacement"),
    [
        (_FAT12_720_FAT2_OFFSET + _FAT12_720_SECTOR_SIZE + 17, b"\x01"),
        (_FAT12_720_FAT2_OFFSET + 19, (2880).to_bytes(2, "little")),
        (_FAT12_720_FAT1_OFFSET, b"\x00\x00\x00"),
        (_FAT12_720_ROOT_OFFSET, b"\x01"),
    ],
    ids=(
        "fat-tail-does-not-match-fat1",
        "relocated-bpb-has-wrong-geometry",
        "fat1-is-invalid",
        "root-directory-is-implausible",
    ),
)
def test_prepare_yamaha_bytes_rejects_weak_unsigned_bpb_stub_evidence(
    tmp_path,
    offset,
    replacement,
):
    source = bytearray(_fat12_720_unsigned_bpb_in_second_fat_image())
    source[offset:offset + len(replacement)] = replacement
    output_path = tmp_path / "unchanged.img"

    result = prepare_yamaha_bytes(bytes(source), output_path)

    assert not result.changed
    assert output_path.read_bytes() == source


@pytest.mark.parametrize("size_bytes", [0, 737_280, 1_474_560])
def test_usb_format_capacity_allows_720_image_when_capacity_is_sufficient_or_unknown(
    size_bytes,
):
    assert not usb_floppy_format_capacity_error(
        _usb_drive(size_bytes),
        DISK_FORMAT_BY_KEY["ibm.720"],
    )


def test_usb_format_session_blocks_oversized_write_before_disk_probe(monkeypatch):
    def unexpected_probe(*_args, **_kwargs):
        pytest.fail("The disk must not be probed or mutated after capacity validation fails")

    monkeypatch.setattr(
        FloppyImageSession,
        "_try_prepare_existing_usb_floppy",
        classmethod(unexpected_probe),
    )

    with pytest.raises(FloppyImageError, match="oversized raw write"):
        FloppyImageSession.format_usb_floppy(
            _usb_drive(DISK_FORMAT_BY_KEY["ibm.1440"].size_bytes),
            DISK_FORMAT_BY_KEY["ibm.2880"],
        )


def test_format_dialog_blocks_oversized_usb_write_before_confirmation(monkeypatch):
    drive = _usb_drive(DISK_FORMAT_BY_KEY["ibm.1440"].size_bytes)
    warning = {}

    class FakeWindow:
        def _prepare_for_disk_load(self, _description):
            return True

        def _choose_format_floppy_options(self):
            return {
                "source_kind": "floppy_usb",
                "source": drive,
                "target_name": drive.display_name,
                "drive_size_bytes": drive.size_bytes,
                "disk_format": DISK_FORMAT_BY_KEY["ibm.2880"],
                "eseq_disk": False,
            }

        def _confirm_format_floppy(self, *_args, **_kwargs):
            pytest.fail("An oversized format must be rejected before confirmation")

        def _start_floppy_format_worker(self, *_args, **_kwargs):
            pytest.fail("An oversized format must not start a worker")

    def capture_warning(_parent, title, message, *_args, **_kwargs):
        warning.update(title=title, message=message)

    monkeypatch.setattr(main_window.QMessageBox, "warning", capture_warning)

    MidiTitleWindow.format_disklavier_floppy(FakeWindow())

    assert warning["title"] == "Incompatible Floppy Format"
    assert "IBM 2.88M ED" in warning["message"]
    assert "will not attempt an oversized raw write" in warning["message"]


def test_windows_short_write_reports_image_and_chunk_byte_counts(tmp_path):
    image_path = tmp_path / "oversized.img"
    image_path.write_bytes(b"x" * 10_000)

    class FakeKernel32:
        def __init__(self):
            self.write_sizes = iter((8_192, 0))

        def WriteFile(self, _handle, _buffer, _size, bytes_written, _overlapped):
            bytes_written._obj.value = next(self.write_sizes)
            return True

        def FlushFileBuffers(self, _handle):
            pytest.fail("A short write must stop before flushing")

    volume = _WindowsVolumeHandle.__new__(_WindowsVolumeHandle)
    volume.path = r"\\.\A:"
    volume.handle = object()
    volume._ctypes = SimpleNamespace(
        byref=ctypes.byref,
        create_string_buffer=ctypes.create_string_buffer,
        FormatError=lambda _code: "",
        get_last_error=lambda: 0,
    )
    volume._wintypes = SimpleNamespace(DWORD=ctypes.c_uint32)
    volume._kernel32 = FakeKernel32()
    volume._seek = lambda _offset, _label: None

    with pytest.raises(FloppyImageError) as exc_info:
        volume.write_file(image_path)

    message = str(exc_info.value)
    assert r"Could not fully write floppy device \\.\A:." in message
    assert "Windows wrote 8,192 of 10,000 image bytes" in message
    assert "requested 1,808 bytes and wrote 0 bytes" in message
    assert "larger than the floppy or drive capacity" in message
