"""Read FAT root-directory metadata through an ordinary Windows directory handle.

These queries do not open the raw volume and do not require administrator access.
Callers must treat any failure or malformed response as an unavailable preflight.

FSCTL_QUERY_FAT_BPB accepts a directory handle and returns the 36-byte BPB prefix:
https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-fscc/2dbdd557-ec02-4cfc-87f0-721d3cfb7628
FastFAT reports ShortNameLength only for stored LFNs. Its root compaction uses
the same length-derived slot count, discarding orphan LFN records:
https://github.com/microsoft/Windows-driver-samples/tree/main/filesys/fastfat
"""

import struct


_FILE_LIST_DIRECTORY = 0x0001
_FILE_SHARE_ALL = 0x0007
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_FSCTL_QUERY_FAT_BPB = 0x00090058
_FILE_ID_BOTH_DIRECTORY_INFO = 10
_ERROR_NO_MORE_FILES = 18
_DIRECTORY_BUFFER_SIZE = 64 * 1024
_DIRECTORY_NAME_OFFSET = 104


def _root_entry_count(boot):
    """Validate the fixed FAT12/16 BPB prefix returned by FastFAT."""
    if len(boot) != 36:
        raise ValueError("Windows returned an incomplete FAT boot parameter block")
    sector_size = struct.unpack_from("<H", boot, 11)[0]
    sectors_per_cluster = boot[13]
    reserved = struct.unpack_from("<H", boot, 14)[0]
    fat_count = boot[16]
    root_entries = struct.unpack_from("<H", boot, 17)[0]
    total_sectors = struct.unpack_from("<H", boot, 19)[0] or struct.unpack_from("<I", boot, 32)[0]
    fat_sectors = struct.unpack_from("<H", boot, 22)[0]
    if (
        sector_size not in (512, 1024, 2048, 4096)
        or not sectors_per_cluster
        or sectors_per_cluster > 128
        or sectors_per_cluster & (sectors_per_cluster - 1)
        or not reserved
        or not fat_count
        or not 0 < root_entries <= 2048
        or not fat_sectors
    ):
        raise ValueError("Windows returned an invalid FAT12/16 boot parameter block")
    root_sectors = (root_entries * 32 + sector_size - 1) // sector_size
    data_sectors = total_sectors - reserved - fat_count * fat_sectors - root_sectors
    cluster_count = data_sectors // sectors_per_cluster
    if not 0 < cluster_count < 65525:
        raise ValueError("Windows did not report a FAT12/16 filesystem")
    return root_entries


def _directory_records(data):
    """Decode FILE_ID_BOTH_DIR_INFO without depending on host C type widths."""
    offset = 0
    while True:
        if len(data) - offset < _DIRECTORY_NAME_OFFSET:
            raise ValueError("Windows returned an incomplete FAT directory record")
        next_offset, file_index = struct.unpack_from("<II", data, offset)
        attributes, name_length = struct.unpack_from("<II", data, offset + 56)
        short_length = data[offset + 68]
        if not name_length or name_length % 2 or name_length > 510:
            raise ValueError("Windows returned an invalid FAT filename length")
        if short_length > 24 or short_length % 2:
            raise ValueError("Windows returned an invalid FAT short filename length")
        record_end = offset + _DIRECTORY_NAME_OFFSET + name_length
        if record_end > len(data):
            raise ValueError("Windows returned a truncated FAT filename")
        if next_offset and (
            next_offset % 8
            or next_offset < _DIRECTORY_NAME_OFFSET + name_length
            or offset + next_offset > len(data) - _DIRECTORY_NAME_OFFSET
        ):
            raise ValueError("Windows returned an invalid FAT directory record offset")
        try:
            name = data[offset + _DIRECTORY_NAME_OFFSET:record_end].decode("utf-16-le")
            short_name = data[offset + 70:offset + 70 + short_length].decode("utf-16-le")
        except UnicodeDecodeError as error:
            raise ValueError("Windows returned an invalid UTF-16 FAT filename") from error
        if "\0" in name or "\0" in short_name:
            raise ValueError("Windows returned a null character in a FAT filename")
        yield {
            "name": name,
            "short_name": short_name,
            "attributes": attributes,
            "file_index": file_index,
            "slots": 1 + (name_length + 25) // 26 if short_length else 1,
        }
        if not next_offset:
            return
        offset += next_offset


def query_root_directory(root, windows_api, cancel_callback=None):
    """Return FAT12/16 root capacity and all live directory records.

    ``windows_api`` is the application's ``(ctypes, wintypes, kernel32)`` tuple.
    ``cancel_callback``, when provided, raises to interrupt the query. Native
    failures raise ``WinError``; malformed responses raise ``ValueError``.
    """
    ctypes, _wintypes, kernel32 = windows_api
    dword = ctypes.c_uint32
    handle_type = ctypes.c_void_p
    kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, dword, dword, handle_type, dword, dword, handle_type]
    kernel32.CreateFileW.restype = handle_type
    kernel32.DeviceIoControl.argtypes = [
        handle_type, dword, handle_type, dword, handle_type, dword,
        ctypes.POINTER(dword), handle_type,
    ]
    kernel32.DeviceIoControl.restype = ctypes.c_int
    kernel32.GetFileInformationByHandleEx.argtypes = [handle_type, ctypes.c_int, handle_type, dword]
    kernel32.GetFileInformationByHandleEx.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [handle_type]
    kernel32.CloseHandle.restype = ctypes.c_int

    def check_cancelled():
        if cancel_callback is not None:
            cancel_callback()

    check_cancelled()
    handle = kernel32.CreateFileW(
        str(root), _FILE_LIST_DIRECTORY, _FILE_SHARE_ALL, None,
        _OPEN_EXISTING, _FILE_FLAG_BACKUP_SEMANTICS, None,
    )
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        check_cancelled()
        boot = ctypes.create_string_buffer(36)
        returned = dword()
        if not kernel32.DeviceIoControl(
            handle, _FSCTL_QUERY_FAT_BPB, None, 0, boot, len(boot),
            ctypes.byref(returned), None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if returned.value != len(boot):
            raise ValueError("Windows returned an incomplete FAT boot parameter block")
        root_entries = _root_entry_count(boot.raw)
        entries = []
        names = set()
        occupied_slots = 0
        while True:
            check_cancelled()
            # A fresh zeroed buffer prevents stale records from a prior page
            # from being accepted if a driver returns malformed data.
            buffer = ctypes.create_string_buffer(_DIRECTORY_BUFFER_SIZE)
            if not kernel32.GetFileInformationByHandleEx(
                handle, _FILE_ID_BOTH_DIRECTORY_INFO, buffer, len(buffer),
            ):
                error_code = ctypes.get_last_error()
                if error_code == _ERROR_NO_MORE_FILES:
                    break
                raise ctypes.WinError(error_code)
            for entry in _directory_records(buffer.raw):
                if entry["name"] in names:
                    raise ValueError("Windows returned a repeated FAT directory record")
                names.add(entry["name"])
                occupied_slots += entry["slots"]
                if occupied_slots > root_entries:
                    raise ValueError("Windows returned more FAT directory entries than the root can hold")
                entries.append(entry)
        check_cancelled()
        return {"root_entries": root_entries, "entries": entries}
    finally:
        kernel32.CloseHandle(handle)
