"""Fault-injected directory metadata queries; no Windows drive is accessed."""

import ctypes
from ctypes import wintypes
import struct
from types import SimpleNamespace

import pytest

from aps_midi_prep_tool_app.windows_fat_directory import query_root_directory


def _boot_sector(root_entries=224):
    boot = bytearray(36)
    struct.pack_into("<H", boot, 11, 512)
    boot[13] = 1
    struct.pack_into("<H", boot, 14, 1)
    boot[16] = 2
    struct.pack_into("<H", boot, 17, root_entries)
    struct.pack_into("<H", boot, 19, 2880)
    boot[21] = 0xF0
    struct.pack_into("<H", boot, 22, 9)
    return bytes(boot)


def _record(name, short_name="", attributes=0x20, file_index=0, has_next=False):
    name_bytes = name.encode("utf-16-le")
    short_bytes = short_name.encode("utf-16-le")
    length = 104 + len(name_bytes)
    if has_next:
        length = (length + 7) // 8 * 8
    record = bytearray(length)
    struct.pack_into("<II", record, 0, length if has_next else 0, file_index)
    struct.pack_into("<II", record, 56, attributes, len(name_bytes))
    record[68] = len(short_bytes)
    record[70:70 + len(short_bytes)] = short_bytes
    record[104:104 + len(name_bytes)] = name_bytes
    return bytes(record)


def _windows_api(pages=(), boot=None, boot_error=None, boot_returned=None, open_error=None):
    state = SimpleNamespace(opened=[], closed=[], queries=[], last_error=0, boot_calls=[])
    pages = iter(pages)
    boot = _boot_sector() if boot is None else boot

    def winerror(code):
        error = OSError(f"[WinError {code}] mocked native failure")
        error.winerror = code
        return error

    def create_file(*args):
        state.opened.append(args)
        if open_error is not None:
            state.last_error = open_error
            return ctypes.c_void_p(-1).value
        return 42

    def device_io(handle, control, input_buffer, input_length, output, output_length, returned, overlapped):
        state.boot_calls.append((handle, control, input_buffer, input_length, output_length, overlapped))
        assert ctypes.sizeof(returned._obj) == 4, "DWORD must remain 32 bits on non-Windows test hosts"
        if boot_error is not None:
            state.last_error = boot_error
            return False
        ctypes.memmove(output, bytes(boot), min(len(boot), output_length))
        returned._obj.value = len(boot) if boot_returned is None else boot_returned
        return True

    def query(handle, information_class, output, output_length):
        state.queries.append((handle, information_class, output_length))
        page = next(pages, 18)
        if isinstance(page, int):
            state.last_error = page
            return False
        assert len(page) <= output_length
        ctypes.memmove(output, bytes(page), len(page))
        return True

    def close(handle):
        state.closed.append(handle)
        return True

    native = SimpleNamespace(**vars(ctypes))
    native.get_last_error = lambda: state.last_error
    native.WinError = winerror
    kernel = SimpleNamespace(
        CreateFileW=create_file,
        DeviceIoControl=device_io,
        GetFileInformationByHandleEx=query,
        CloseHandle=close,
    )
    return (native, wintypes, kernel), state


def test_uses_only_an_ordinary_root_handle_and_enumerates_every_page():
    first = _record("SONG.MID", file_index=32, has_next=True) + _record(
        "Hidden data folder", "HIDDEN~1", attributes=0x16, file_index=128,
    )
    second = _record("System data.mid", "SYSTEM~1.MID", attributes=0x06, file_index=224)
    api, state = _windows_api([first, second])

    result = query_root_directory("A:\\", api)

    assert state.opened == [("A:\\", 1, 7, None, 3, 0x02000000, None)]
    assert state.boot_calls == [(42, 0x90058, None, 0, 36, None)]
    assert state.queries == [(42, 10, 65536)] * 3
    assert state.closed == [42]
    assert result["root_entries"] == 224
    assert result["entries"] == [
        {"name": "SONG.MID", "short_name": "", "attributes": 0x20, "file_index": 32, "slots": 1},
        {"name": "Hidden data folder", "short_name": "HIDDEN~1", "attributes": 0x16,
         "file_index": 128, "slots": 3},
        {"name": "System data.mid", "short_name": "SYSTEM~1.MID", "attributes": 0x06,
         "file_index": 224, "slots": 3},
    ]


@pytest.mark.parametrize("name,short_name,slots", [
    ("SONG.MID", "", 1),
    ("song.mid", "SONG.MID", 2),
    ("a" * 13, "AAAAAA~1", 2),
    ("a" * 14, "AAAAAA~1", 3),
    ("😀" * 7 + ".MID", "EMOJI~1.MID", 3),
])
def test_lfn_slots_count_utf16_code_units(name, short_name, slots):
    api, state = _windows_api([_record(name, short_name)])
    result = query_root_directory("A:\\", api)
    assert result["entries"][0]["slots"] == slots
    assert state.closed == [42]


def test_empty_directory_is_a_successful_query():
    api, state = _windows_api()
    assert query_root_directory("A:\\", api) == {"root_entries": 224, "entries": []}
    assert state.closed == [42]


@pytest.mark.parametrize("failure", ["open", "boot", "first_page", "later_page"])
def test_native_failures_abort_and_close_the_handle(failure):
    kwargs = {
        "open": {"open_error": 5},
        "boot": {"boot_error": 5},
        "first_page": {"pages": [5]},
        "later_page": {"pages": [_record("SONG.MID"), 5]},
    }[failure]
    api, state = _windows_api(**kwargs)
    with pytest.raises(OSError, match="WinError 5"):
        query_root_directory("A:\\", api)
    assert state.closed == ([] if failure == "open" else [42])


@pytest.mark.parametrize("returned", [0, 35, 37])
def test_requires_complete_fixed_length_boot_parameter_block(returned):
    api, state = _windows_api(boot_returned=returned)
    with pytest.raises(ValueError, match="incomplete FAT boot"):
        query_root_directory("A:\\", api)
    assert state.queries == []
    assert state.closed == [42]


@pytest.mark.parametrize("offset,format,value", [
    (11, "<H", 0),
    (13, "<B", 3),
    (14, "<H", 0),
    (16, "<B", 0),
    (17, "<H", 0),
    (17, "<H", 2049),
    (19, "<H", 10),
    (22, "<H", 0),
])
def test_rejects_invalid_or_nonfat_bpb(offset, format, value):
    boot = bytearray(_boot_sector())
    struct.pack_into(format, boot, offset, value)
    api, state = _windows_api(boot=boot)
    with pytest.raises(ValueError, match="FAT"):
        query_root_directory("A:\\", api)
    assert state.queries == []
    assert state.closed == [42]


@pytest.mark.parametrize("offset,format,value", [
    (60, "<I", 0),
    (60, "<I", 3),
    (60, "<I", 512),
    (68, "<B", 3),
    (68, "<B", 26),
    (0, "<I", 8),
    (0, "<I", 65536),
    (0, "<I", 127),
])
def test_rejects_malformed_directory_record_lengths(offset, format, value):
    page = bytearray(_record("SONG.MID"))
    struct.pack_into(format, page, offset, value)
    api, state = _windows_api([page])
    with pytest.raises(ValueError, match="FAT"):
        query_root_directory("A:\\", api)
    assert state.closed == [42]


def test_rejects_truncated_filename_at_end_of_query_buffer():
    page = bytearray(65536)
    first = _record("SONG.MID", has_next=True)
    page[:len(first)] = first
    struct.pack_into("<I", page, 0, 65424)
    page[65424:65528] = _record("ABCD.MID")[:104]
    api, state = _windows_api([page])
    with pytest.raises(ValueError, match="truncated FAT filename"):
        query_root_directory("A:\\", api)
    assert state.closed == [42]


@pytest.mark.parametrize("name_bytes", [b"\x00\xd8", b"\x00\x00"])
def test_rejects_invalid_utf16_or_embedded_null(name_bytes):
    page = bytearray(_record("A"))
    page[104:] = name_bytes
    api, state = _windows_api([page])
    with pytest.raises(ValueError, match="FAT filename"):
        query_root_directory("A:\\", api)
    assert state.closed == [42]


def test_repeated_pages_cannot_loop_or_claim_a_complete_snapshot():
    page = _record("SONG.MID")
    api, state = _windows_api([page, page])
    with pytest.raises(ValueError, match="repeated FAT directory"):
        query_root_directory("A:\\", api)
    assert len(state.queries) == 2
    assert state.closed == [42]


def test_rejects_records_exceeding_root_directory_capacity():
    api, state = _windows_api([_record("Long filename.mid", "LONGFI~1.MID")], boot=_boot_sector(root_entries=2))
    with pytest.raises(ValueError, match="more FAT directory entries"):
        query_root_directory("A:\\", api)
    assert state.closed == [42]


def test_cancellation_between_pages_closes_the_handle():
    api, state = _windows_api([_record("ONE.MID"), _record("TWO.MID")])

    def cancel():
        if state.queries:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        query_root_directory("A:\\", api, cancel_callback=cancel)
    assert len(state.queries) == 1
    assert state.closed == [42]


def test_cancellation_before_open_does_not_access_the_drive():
    api, state = _windows_api()

    def cancel():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        query_root_directory("A:\\", api, cancel_callback=cancel)
    assert state.opened == []
    assert state.closed == []
