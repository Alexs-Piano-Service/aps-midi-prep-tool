import math

import pytest

from aps_midi_prep_tool_app import floppy_image


SECTOR_SIZE = 512


def test_ordinary_read_forwards_cancellation_and_deadline(monkeypatch):
    calls = []
    cancel = lambda: False
    class Device:
        def read_at_recovery(self, offset, size, label, **kwargs):
            calls.append((offset, size, kwargs))
            return b"S" * size
        def read_at(self, *_args):
            pytest.fail("Ordinary reads must use cancellable I/O")
    monkeypatch.setattr(floppy_image.time, "monotonic", lambda: 100.0)
    assert floppy_image._read_device_exact(Device(), 512, 4, "song", cancel) == b"SSSS"
    assert calls == [(512, 4, {"cancel_callback": cancel, "deadline_at": 130.0})]


@pytest.mark.parametrize("reader", [
    floppy_image._try_read_device_exact,
    lambda device, offset, size: floppy_image._read_device_best_effort(device, offset, size, "song"),
])
def test_stalled_read_is_not_retried_or_zero_filled(reader):
    calls = []
    class Device:
        def read_at_recovery(self, *_args, **_kwargs):
            calls.append(True)
            raise floppy_image._RecoveryReadDeadlineExceeded("deadline")
    with pytest.raises(floppy_image.FastFloppyReadError, match="30 seconds") as error:
        reader(Device(), 512, 1024)
    assert not error.value.fallback_allowed
    assert calls == [True]


def test_fast_read_stalled_at_25_percent_stops_without_publishing_image(tmp_path, monkeypatch):
    source = tmp_path / "original.img"
    layout = floppy_image._PROTECTED_FAT12_LAYOUTS[0]
    floppy_image._create_blank_fat12_image_from_layout(source, layout, "ORIGINAL")
    data = bytearray(source.read_bytes())
    geometry = floppy_image._geometry_from_boot_sector(data[:512])
    for index in range(geometry.num_fats):
        fat_start = geometry.fat_offset + index * geometry.fat_size
        data[fat_start + 3:fat_start + 5] = b"\xff\x0f"  # cluster 2 ends this file
    entry = floppy_image._dos_directory_entry(b"SONG    FIL", 2, 4)
    data[geometry.root_offset:geometry.root_offset + 32] = entry
    data[geometry.data_offset:geometry.data_offset + 4] = b"SONG"
    source.write_bytes(data)
    progress = []
    closed = []
    class Device:
        def read_at_recovery(self, offset, size, _label, **_kwargs):
            if offset >= geometry.data_offset:
                raise floppy_image._RecoveryReadDeadlineExceeded("driver still waiting")
            return bytes(data[offset:offset + size])
        def close(self):
            closed.append(True)
    monkeypatch.setattr(floppy_image, "_open_block_device_for_read", lambda _path: Device())
    output = tmp_path / "copy.img"
    with pytest.raises(floppy_image.FastFloppyReadError, match="Start in recovery mode"):
        floppy_image._read_floppy_device_fast_image(
            str(source), str(output), len(data),
            progress_callback=lambda step, *_args: progress.append(step),
        )
    assert progress[-1] == 25
    assert closed == [True]
    assert not output.exists()
    assert source.read_bytes() == data


class _FakeRecoveryDevice:
    def __init__(self, read):
        self._read = read
        self.calls = []
        self.closed = False

    def read_at(self, offset, size, label):
        self.calls.append((offset, size, label))
        return self._read(offset, size)

    def close(self):
        self.closed = True


def _install_fake_device(monkeypatch, read):
    device = _FakeRecoveryDevice(read)
    monkeypatch.setattr(
        floppy_image,
        "_open_block_device_for_recovery_read",
        lambda _device_path: device,
    )
    return device


def _read_recovery_image(monkeypatch, tmp_path, read, *, sectors=4, **policy):
    device = _install_fake_device(monkeypatch, read)
    output_path = tmp_path / "recovery.img"
    chunk_size = policy.pop("chunk_size", sectors * SECTOR_SIZE)
    diagnostics = floppy_image._read_block_device_recovery_image(
        "A:",
        output_path,
        sectors * SECTOR_SIZE,
        chunk_size=chunk_size,
        sector_size=SECTOR_SIZE,
        **policy,
    )
    return device, output_path, diagnostics


def _assert_sector_accounting(diagnostics):
    assert diagnostics["expected_sectors"] == (
        diagnostics["readable_sectors"]
        + diagnostics["bad_sectors"]
        + diagnostics.get("unresolved_sectors", 0)
        + diagnostics["unattempted_sectors"]
    )
    assert diagnostics["readable_sectors"] == (
        diagnostics["good_sectors"]
        + diagnostics["recovered_after_fallback_sectors"]
    )
    assert diagnostics["bytes_recovered"] == (
        diagnostics["readable_sectors"] * diagnostics["sector_size"]
        + diagnostics.get("partial_bytes_recovered", 0)
    )


def test_recovery_chunk_prefers_bounded_reader_and_forwards_controls():
    calls = []
    cancel_callback = lambda: False

    class BoundedDevice:
        def read_at_recovery(self, offset, size, label, **kwargs):
            calls.append((offset, size, label, kwargs))
            return b"X" * size

    result = floppy_image._read_device_chunk_for_recovery(
        BoundedDevice(),
        512,
        16,
        cancel_callback=cancel_callback,
        deadline_at=123.5,
    )

    assert result == b"X" * 16
    assert calls == [
        (
            512,
            16,
            "floppy recovery image",
            {
                "cancel_callback": cancel_callback,
                "deadline_at": 123.5,
                "submitted_callback": None,
            },
        )
    ]


def test_windows_deadline_race_preserves_normally_completed_read(
    monkeypatch,
):
    class FakeDword:
        def __init__(self):
            self.value = 0

    class FakeWintypes:
        DWORD = FakeDword

    class FakeBuffer:
        raw = b"DATA"

    class FakeOverlapped:
        Offset = 0
        OffsetHigh = 0
        hEvent = None

    class FakeCtypes:
        def __init__(self):
            self.last_error = 0

        @staticmethod
        def create_string_buffer(_size):
            return FakeBuffer()

        @staticmethod
        def byref(value):
            return value

        def get_last_error(self):
            return self.last_error

        @staticmethod
        def FormatError(_error_code):
            return "error"

    class FakeKernel32:
        def __init__(self, fake_ctypes):
            self.fake_ctypes = fake_ctypes
            self.wait_timeouts = []
            self.cancel_calls = 0
            self.closed = []

        @staticmethod
        def CreateEventW(*_args):
            return 77

        def ReadFile(self, *_args):
            self.fake_ctypes.last_error = (
                floppy_image._WindowsRecoveryVolumeHandle.ERROR_IO_PENDING
            )
            return False

        def WaitForSingleObject(self, _event, timeout):
            self.wait_timeouts.append(timeout)
            return floppy_image._WindowsRecoveryVolumeHandle.WAIT_TIMEOUT

        @staticmethod
        def GetOverlappedResult(_handle, _overlapped, bytes_read, _wait):
            bytes_read.value = 4
            return True

        def CancelIoEx(self, *_args):
            self.cancel_calls += 1
            return True

        def CloseHandle(self, event):
            self.closed.append(event)
            return True

    fake_ctypes = FakeCtypes()
    kernel32 = FakeKernel32(fake_ctypes)
    handle = object.__new__(floppy_image._WindowsRecoveryVolumeHandle)
    handle.handle = 1
    handle._ctypes = fake_ctypes
    handle._wintypes = FakeWintypes
    handle._kernel32 = kernel32
    monkeypatch.setattr(
        floppy_image._WindowsRecoveryVolumeHandle,
        "_overlapped_type",
        FakeOverlapped,
    )
    clock_values = iter((0.0, 2.0, 2.0))
    monkeypatch.setattr(
        floppy_image.time,
        "monotonic",
        lambda: next(clock_values),
    )

    result = handle.read_at_recovery(0, 4, "test", deadline_at=1.0)

    assert result == b"DATA"
    assert kernel32.wait_timeouts == [0]
    assert kernel32.cancel_calls == 0
    assert kernel32.closed == [77]


def test_windows_pending_read_reaper_releases_signalled_resources(monkeypatch):
    reaped = floppy_image.threading.Event()

    class FakeKernel32:
        @staticmethod
        def WaitForSingleObject(_event, _timeout):
            return floppy_image._WindowsRecoveryVolumeHandle.WAIT_OBJECT_0

        @staticmethod
        def CloseHandle(_event):
            reaped.set()
            return True

    handle = object.__new__(floppy_image._WindowsRecoveryVolumeHandle)
    handle._kernel32 = FakeKernel32()
    handle.handle = 1
    monkeypatch.setattr(
        floppy_image._WindowsRecoveryVolumeHandle,
        "_retained_pending_reads",
        [],
    )
    monkeypatch.setattr(
        floppy_image._WindowsRecoveryVolumeHandle,
        "_retained_pending_reads_lock",
        floppy_image.threading.Lock(),
    )

    handle._retain_pending_read(object(), object(), 88)

    assert reaped.wait(1.0)
    assert floppy_image._WindowsRecoveryVolumeHandle._retained_pending_reads == []
    assert handle.incomplete_cancel_drain is True


def test_windows_pending_read_stays_retained_if_reaper_cannot_start(monkeypatch):
    class FakeKernel32:
        @staticmethod
        def WaitForSingleObject(_event, _timeout):
            raise AssertionError("reaper should not run")

    class FailingThread:
        def __init__(self, **_kwargs):
            pass

        @staticmethod
        def start():
            raise RuntimeError("cannot start new thread")

    handle = object.__new__(floppy_image._WindowsRecoveryVolumeHandle)
    handle._kernel32 = FakeKernel32()
    handle.handle = 1
    monkeypatch.setattr(
        floppy_image._WindowsRecoveryVolumeHandle,
        "_retained_pending_reads",
        [],
    )
    monkeypatch.setattr(
        floppy_image._WindowsRecoveryVolumeHandle,
        "_retained_pending_reads_lock",
        floppy_image.threading.Lock(),
    )
    monkeypatch.setattr(floppy_image.threading, "Thread", FailingThread)
    buffer = object()
    overlapped = object()

    handle._retain_pending_read(buffer, overlapped, 99)

    assert floppy_image._WindowsRecoveryVolumeHandle._retained_pending_reads == [
        (buffer, overlapped, 99)
    ]
    assert handle.incomplete_cancel_drain is True


def test_recovery_reader_counts_healthy_chunk_as_readable_sectors(monkeypatch, tmp_path):
    expected = bytes((index % 251) + 1 for index in range(4 * SECTOR_SIZE))

    device, output_path, diagnostics = _read_recovery_image(
        monkeypatch,
        tmp_path,
        lambda offset, size: expected[offset:offset + size],
    )

    assert output_path.read_bytes() == expected
    assert device.closed
    assert len(device.calls) == 1
    assert diagnostics["read_calls"] == 1
    assert diagnostics["fallback_read_calls"] == 0
    assert diagnostics["read_passes"] == 1
    assert diagnostics["attempted_sectors"] == 4
    assert diagnostics["good_sectors"] == 4
    assert diagnostics["recovered_after_fallback_sectors"] == 0
    assert diagnostics["readable_sectors"] == 4
    assert diagnostics["bad_sectors"] == 0
    assert diagnostics["unattempted_sectors"] == 0
    _assert_sector_accounting(diagnostics)


def test_default_recovery_reads_small_chunks_and_reports_progress_between_reads(
    monkeypatch,
    tmp_path,
):
    source = b"A" * (40 * SECTOR_SIZE)
    progress = []

    def read(offset, size):
        # Each completed chunk must be reported before another read starts.
        assert len(progress) == len(device.calls) - 1
        return source[offset:offset + size]

    device = _install_fake_device(monkeypatch, read)
    output_path = tmp_path / "default-chunks.img"
    diagnostics = floppy_image._read_block_device_recovery_image(
        "A:",
        output_path,
        len(source),
        progress_callback=lambda *update: progress.append(update),
    )

    assert device.closed
    assert output_path.read_bytes() == source
    assert [(offset, size) for offset, size, _label in device.calls] == [
        (0, 8 * 1024),
        (8 * 1024, 8 * 1024),
        (16 * 1024, 4 * 1024),
    ]
    assert [(value, total) for value, total, _message in progress] == [
        (28, 100),
        (56, 100),
        (70, 100),
    ]
    assert "40 of 40 sector(s) attempted; 40 readable, 0 bad, 0 unresolved" in progress[-1][2]
    _assert_sector_accounting(diagnostics)


def test_fallback_progress_updates_when_attempted_percentage_stays_the_same(
    monkeypatch,
    tmp_path,
):
    progress = []

    def read(offset, size):
        if size > SECTOR_SIZE or offset == SECTOR_SIZE:
            raise OSError("unreadable")
        return b"A" * size

    device, _output_path, diagnostics = _read_recovery_image(
        monkeypatch,
        tmp_path,
        read,
        progress_callback=lambda *update: progress.append(update),
    )

    assert device.closed
    assert len(progress) == 4
    assert [(value, total) for value, total, _message in progress] == [(70, 100)] * 4
    for (_value, _total, message), counts in zip(
        progress,
        (
            "1 readable, 0 bad, 3 unresolved",
            "1 readable, 1 bad, 2 unresolved",
            "2 readable, 1 bad, 1 unresolved",
            "3 readable, 1 bad, 0 unresolved",
        ),
    ):
        assert counts in message
    _assert_sector_accounting(diagnostics)


def test_recovery_reader_fallback_zero_fills_only_unreadable_sector(monkeypatch, tmp_path):
    sectors = {
        0: b"A" * SECTOR_SIZE,
        1: b"B" * SECTOR_SIZE,
        3: b"D" * SECTOR_SIZE,
    }

    def read(offset, size):
        if size > SECTOR_SIZE:
            raise OSError("bulk read failed")
        sector_index = offset // SECTOR_SIZE
        if sector_index == 2:
            raise OSError("sector is unreadable")
        return sectors[sector_index]

    device, output_path, diagnostics = _read_recovery_image(
        monkeypatch,
        tmp_path,
        read,
    )

    assert device.closed
    assert output_path.read_bytes() == (
        sectors[0]
        + sectors[1]
        + (b"\x00" * SECTOR_SIZE)
        + sectors[3]
    )
    assert diagnostics["read_calls"] == 5
    assert diagnostics["fallback_read_calls"] == 4
    assert diagnostics["read_passes"] == 2
    assert diagnostics["attempted_sectors"] == 4
    assert diagnostics["good_sectors"] == 0
    assert diagnostics["recovered_after_fallback_sectors"] == 3
    assert diagnostics["readable_sectors"] == 3
    assert diagnostics["bad_sectors"] == 1
    assert diagnostics["unattempted_sectors"] == 0
    assert diagnostics["bad_sector_ranges"] == [[2, 2]]
    assert diagnostics["fallback_recovered_sector_ranges"] == [[0, 1], [3, 3]]
    assert diagnostics["unattempted_sector_ranges"] == []
    _assert_sector_accounting(diagnostics)


def test_recovery_reader_prioritizes_detected_smaller_geometry(monkeypatch, tmp_path):
    selected_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.1440"]
    detected_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]
    boot = floppy_image._build_standard_yamaha_boot_sector(1, b"YAMAHA")
    source = boot + (b"\xE5" * (detected_format.size_bytes - len(boot)))

    device = _install_fake_device(
        monkeypatch,
        lambda offset, size: source[offset:offset + size],
    )
    output_path = tmp_path / "detected-720.img"
    diagnostics = floppy_image._read_block_device_recovery_image(
        "A:",
        output_path,
        selected_format.size_bytes,
        chunk_size=64 * 1024,
        sector_size=SECTOR_SIZE,
    )

    assert device.closed
    assert output_path.read_bytes() == source
    assert diagnostics["selected_format_label"] == "IBM 1.44M HD"
    assert diagnostics["detected_format_label"] == "IBM 720K DD"
    assert diagnostics["geometry_mismatch"] is True
    assert diagnostics["stop_reason"] == "detected_smaller_geometry"
    assert diagnostics["readable_sectors"] == detected_format.size_bytes // SECTOR_SIZE
    assert diagnostics["bad_sectors"] == 0
    assert diagnostics["unattempted_sectors"] == (
        (selected_format.size_bytes - detected_format.size_bytes) // SECTOR_SIZE
    )
    assert diagnostics["unattempted_sector_ranges"] == [[1440, 2879]]
    message = floppy_image._usb_recovery_no_data_message(diagnostics)
    assert "requested as IBM 1.44M HD" in message
    assert "identifies the disk as IBM 720K DD" in message
    _assert_sector_accounting(diagnostics)


def test_recovery_reader_reports_detected_larger_geometry(monkeypatch, tmp_path):
    selected_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]
    detected_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.1440"]
    layout = floppy_image._PROTECTED_FAT12_LAYOUTS[2]
    boot = floppy_image._build_standard_fat12_boot_sector(layout, 1, b"YAMAHA")
    source = boot + (b"\xE5" * (detected_format.size_bytes - len(boot)))
    device = _install_fake_device(
        monkeypatch,
        lambda offset, size: source[offset:offset + size],
    )
    output_path = tmp_path / "selected-720-detected-1440.img"

    diagnostics = floppy_image._read_block_device_recovery_image(
        "A:",
        output_path,
        selected_format.size_bytes,
        chunk_size=64 * 1024,
        sector_size=SECTOR_SIZE,
    )

    assert device.closed
    assert len(output_path.read_bytes()) == selected_format.size_bytes
    assert diagnostics["selected_format_label"] == "IBM 720K DD"
    assert diagnostics["detected_format_label"] == "IBM 1.44M HD"
    assert diagnostics["geometry_mismatch"] is True
    assert diagnostics["stop_reason"] == "completed"
    message = floppy_image._usb_recovery_no_data_message(diagnostics)
    assert "requested as IBM 720K DD" in message
    assert "identifies the disk as IBM 1.44M HD" in message
    assert "Retry recovery with the detected disk format" in message


def test_smaller_boot_geometry_normalizes_oversized_chunk_accounting(
    monkeypatch,
    tmp_path,
):
    selected_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.1440"]
    detected_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]
    boot = floppy_image._build_standard_yamaha_boot_sector(1, b"YAMAHA")
    source = boot + (b"\xE5" * (selected_format.size_bytes - len(boot)))
    device = _install_fake_device(
        monkeypatch,
        lambda offset, size: source[offset:offset + size],
    )
    output_path = tmp_path / "oversized-chunk-detected-720.img"

    diagnostics = floppy_image._read_block_device_recovery_image(
        "A:",
        output_path,
        selected_format.size_bytes,
        chunk_size=selected_format.size_bytes,
        sector_size=SECTOR_SIZE,
    )

    detected_sectors = detected_format.size_bytes // SECTOR_SIZE
    assert device.closed
    assert len(output_path.read_bytes()) == detected_format.size_bytes
    assert diagnostics["readable_sectors"] == detected_sectors
    assert diagnostics["unattempted_sectors"] == (
        diagnostics["expected_sectors"] - detected_sectors
    )
    assert diagnostics["bytes_recovered"] == detected_format.size_bytes
    assert diagnostics["image_bytes"] == detected_format.size_bytes
    _assert_sector_accounting(diagnostics)


def test_recovery_reader_soft_deadline_keeps_unattempted_sectors_distinct_from_bad(
    monkeypatch,
    tmp_path,
):
    clock = {"now": 0.0}

    def monotonic():
        return clock["now"]

    def read(_offset, _size):
        clock["now"] += 1.0
        raise OSError("timed-out read")

    monkeypatch.setattr(floppy_image.time, "monotonic", monotonic)
    device, output_path, diagnostics = _read_recovery_image(
        monkeypatch,
        tmp_path,
        read,
        sectors=20,
        chunk_size=4 * SECTOR_SIZE,
        soft_deadline_seconds=3,
        all_bad_sample_sectors=100,
        mostly_bad_sample_sectors=100,
        consecutive_bad_sectors=100,
    )

    assert device.closed
    assert len(output_path.read_bytes()) == 20 * SECTOR_SIZE
    assert diagnostics["stopped_early"] is True
    assert "deadline" in diagnostics["stop_reason"]
    assert 0 < diagnostics["attempted_sectors"] < diagnostics["expected_sectors"]
    assert diagnostics["bad_sectors"] + diagnostics["unresolved_sectors"] == (
        diagnostics["attempted_sectors"]
    )
    assert diagnostics["unresolved_sectors"] > 0
    assert diagnostics["unattempted_sectors"] > 0
    _assert_sector_accounting(diagnostics)


def test_bounded_reader_preflight_deadline_does_not_mark_sectors_attempted(
    monkeypatch,
    tmp_path,
):
    class PreflightDeadlineDevice:
        closed = False

        def read_at_recovery(self, _offset, _size, _label, **_kwargs):
            raise floppy_image._RecoveryReadDeadlineExceeded("deadline")

        def close(self):
            self.closed = True

    device = PreflightDeadlineDevice()
    monkeypatch.setattr(
        floppy_image,
        "_open_block_device_for_recovery_read",
        lambda _path: device,
    )

    diagnostics = floppy_image._read_block_device_recovery_image(
        "A:",
        tmp_path / "preflight-deadline.img",
        4 * SECTOR_SIZE,
        chunk_size=4 * SECTOR_SIZE,
        sector_size=SECTOR_SIZE,
        soft_deadline_seconds=300,
    )

    assert device.closed
    assert diagnostics["stop_reason"] == "soft_deadline"
    assert diagnostics["read_calls"] == 0
    assert diagnostics["read_passes"] == 0
    assert diagnostics["attempted_sectors"] == 0
    assert diagnostics["unresolved_sectors"] == 0
    assert diagnostics["unattempted_sectors"] == 4


def test_submitted_read_deadline_leaves_range_unresolved_without_retrying(
    monkeypatch,
    tmp_path,
):
    class DeadlineDevice:
        closed = False

        def __init__(self):
            self.calls = []

        def read_at_recovery(self, offset, size, _label, **kwargs):
            self.calls.append((offset, size, kwargs["deadline_at"]))
            kwargs["submitted_callback"]()
            raise floppy_image._RecoveryReadDeadlineExceeded("deadline")

        def close(self):
            self.closed = True

    device = DeadlineDevice()
    monkeypatch.setattr(floppy_image.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        floppy_image,
        "_open_block_device_for_recovery_read",
        lambda _path: device,
    )
    output_path = tmp_path / "submitted-deadline.img"

    diagnostics = floppy_image._read_block_device_recovery_image(
        "A:",
        output_path,
        12 * SECTOR_SIZE,
        chunk_size=4 * SECTOR_SIZE,
    )

    assert device.closed
    assert device.calls == [(0, 4 * SECTOR_SIZE, 400.0)]
    assert output_path.read_bytes() == b"\x00" * (12 * SECTOR_SIZE)
    assert diagnostics["read_deadline_mode"] == "windows_overlapped_cancel"
    assert diagnostics["stop_reason"] == "soft_deadline"
    assert diagnostics["read_calls"] == 1
    assert diagnostics["fallback_read_calls"] == 0
    assert diagnostics["attempted_sectors"] == 4
    assert diagnostics["bad_sectors"] == 0
    assert diagnostics["unresolved_sector_ranges"] == [[0, 3]]
    assert diagnostics["unattempted_sector_ranges"] == [[4, 11]]
    _assert_sector_accounting(diagnostics)


def test_recovery_reader_stops_early_when_sample_is_entirely_unreadable(monkeypatch, tmp_path):
    def read(_offset, _size):
        raise OSError("unreadable")

    device, output_path, diagnostics = _read_recovery_image(
        monkeypatch,
        tmp_path,
        read,
        sectors=12,
        chunk_size=SECTOR_SIZE,
        soft_deadline_seconds=3600,
        all_bad_sample_sectors=3,
        mostly_bad_sample_sectors=100,
        consecutive_bad_sectors=100,
        bad_media_minimum_coverage=0.0,
    )

    assert device.closed
    assert len(output_path.read_bytes()) == 12 * SECTOR_SIZE
    assert diagnostics["stopped_early"] is True
    assert "bad" in diagnostics["stop_reason"] or "unreadable" in diagnostics["stop_reason"]
    assert 0 < diagnostics["attempted_sectors"] <= 3
    assert diagnostics["bad_sectors"] == diagnostics["attempted_sectors"]
    assert diagnostics["unattempted_sectors"] == (
        diagnostics["expected_sectors"] - diagnostics["attempted_sectors"]
    )
    _assert_sector_accounting(diagnostics)


def test_production_bad_media_cutoff_does_not_wait_for_quarter_disk(
    monkeypatch,
    tmp_path,
):
    selected = floppy_image.DISK_FORMAT_BY_KEY["ibm.1440"]
    device = _install_fake_device(
        monkeypatch,
        lambda _offset, _size: (_ for _ in ()).throw(OSError("unreadable")),
    )

    diagnostics = floppy_image._read_block_device_recovery_image(
        "A:",
        tmp_path / "production-cutoff.img",
        selected.size_bytes,
        chunk_size=SECTOR_SIZE,
        sector_size=SECTOR_SIZE,
        soft_deadline_seconds=3600,
    )

    assert device.closed
    assert diagnostics["stop_reason"] == "all_sectors_bad"
    assert diagnostics["attempted_sectors"] == math.ceil(
        diagnostics["expected_sectors"]
        * floppy_image.USB_FLOPPY_RECOVERY_BAD_MEDIA_MINIMUM_COVERAGE
    )
    assert diagnostics["attempted_sectors"] < diagnostics["expected_sectors"] // 4
    assert diagnostics["unattempted_sectors"] > 0
    _assert_sector_accounting(diagnostics)


def test_recovery_reader_propagates_cancellation_from_device_read(monkeypatch, tmp_path):
    def read(_offset, _size):
        raise floppy_image.FloppyOperationCancelled("Operation cancelled.")

    device = _install_fake_device(monkeypatch, read)

    with pytest.raises(floppy_image.FloppyOperationCancelled) as error:
        floppy_image._read_block_device_recovery_image(
            "A:",
            tmp_path / "cancelled.img",
            4 * SECTOR_SIZE,
            chunk_size=4 * SECTOR_SIZE,
            sector_size=SECTOR_SIZE,
        )

    assert device.closed
    assert error.value.diagnostics["stop_reason"] == "cancelled"
    assert error.value.diagnostics["attempted_sectors"] == 4
    assert error.value.diagnostics["unresolved_sectors"] == 4
    assert "_sector_states" not in error.value.diagnostics


def test_recovery_reader_preserves_positive_short_read_prefix(monkeypatch, tmp_path):
    prefix = (b"A" * SECTOR_SIZE) + (b"B" * 88)

    def read(offset, size):
        if offset == 0 and size == 2 * SECTOR_SIZE:
            return prefix
        raise OSError("could not finish partial sector")

    device, output_path, diagnostics = _read_recovery_image(
        monkeypatch,
        tmp_path,
        read,
        sectors=2,
    )

    assert device.closed
    assert output_path.read_bytes() == prefix + (b"\x00" * (SECTOR_SIZE - 88))
    assert diagnostics["readable_sectors"] == 1
    assert diagnostics["bad_sectors"] == 0
    assert diagnostics["unresolved_sectors"] == 1
    assert diagnostics["partially_readable_sectors"] == 1
    assert diagnostics["partial_bytes_recovered"] == 88
    assert diagnostics["bytes_recovered"] == len(prefix)
    _assert_sector_accounting(diagnostics)


def test_positive_short_read_good_prefix_resets_consecutive_bad_streak(
    monkeypatch,
    tmp_path,
):
    def read(offset, size):
        if size > SECTOR_SIZE:
            if offset == 0:
                raise OSError("first chunk failed")
            if offset == 2 * SECTOR_SIZE:
                return b"C" * SECTOR_SIZE
            if offset == 4 * SECTOR_SIZE:
                return (b"E" * SECTOR_SIZE) + (b"F" * SECTOR_SIZE)
        raise OSError("sector is unreadable")

    device, output_path, diagnostics = _read_recovery_image(
        monkeypatch,
        tmp_path,
        read,
        sectors=6,
        chunk_size=2 * SECTOR_SIZE,
        soft_deadline_seconds=3600,
        all_bad_sample_sectors=100,
        mostly_bad_sample_sectors=100,
        consecutive_bad_sectors=3,
        bad_media_minimum_coverage=0.0,
    )

    assert device.closed
    assert output_path.read_bytes() == (
        (b"\x00" * (2 * SECTOR_SIZE))
        + (b"C" * SECTOR_SIZE)
        + (b"\x00" * SECTOR_SIZE)
        + (b"E" * SECTOR_SIZE)
        + (b"F" * SECTOR_SIZE)
    )
    assert diagnostics["stop_reason"] == "completed"
    assert diagnostics["readable_sectors"] == 3
    assert diagnostics["bad_sectors"] == 3
    assert diagnostics["unattempted_sectors"] == 0
    _assert_sector_accounting(diagnostics)


def test_corrupt_boot_geometry_is_recorded_but_not_trusted():
    selected = floppy_image.DISK_FORMAT_BY_KEY["ibm.1440"]
    diagnostics = floppy_image._new_usb_floppy_recovery_diagnostics(
        None,
        selected,
        selected.size_bytes,
        sector_size=SECTOR_SIZE,
        soft_deadline_seconds=300,
    )
    boot = bytearray(
        floppy_image._build_standard_fat12_boot_sector(
            floppy_image._PROTECTED_FAT12_LAYOUTS[2],
            1,
            b"TEST",
        )
    )
    boot[19:21] = (1440).to_bytes(2, "little")

    geometry = floppy_image._set_recovery_boot_geometry(diagnostics, boot)

    assert geometry.total_size == floppy_image.DISK_FORMAT_BY_KEY["ibm.720"].size_bytes
    assert diagnostics["boot_claimed_format_label"] == "IBM 720K DD"
    assert diagnostics["detected_format_label"] == ""
    assert diagnostics["geometry_mismatch"] is None

    image = bytes(boot) + (b"\x00" * (selected.size_bytes - len(boot)))
    floppy_image._populate_recovery_scan_diagnostics(
        image,
        diagnostics,
        disk_format_hint=selected,
    )
    assert diagnostics["detected_format_label"] == ""
    assert diagnostics["geometry_mismatch"] is None


def test_huge_corrupt_boot_geometry_is_rejected_before_recovery_scan():
    boot = bytearray(
        floppy_image._build_standard_fat12_boot_sector(
            floppy_image._PROTECTED_FAT12_LAYOUTS[0],
            1,
            b"TEST",
        )
    )
    boot[19:21] = b"\x00\x00"
    boot[32:36] = (0xFFFFFFFF).to_bytes(4, "little")

    assert floppy_image._geometry_from_boot_sector(boot) is None
    diagnostics = _message_diagnostics(selected_bytes=0)
    floppy_image._populate_recovery_scan_diagnostics(boot, diagnostics)
    assert diagnostics["geometry_scans"] == []
    assert diagnostics["detected_format_label"] == ""


def test_device_eof_after_readable_supported_capacity_detects_smaller_geometry(
    monkeypatch,
    tmp_path,
):
    selected = floppy_image.DISK_FORMAT_BY_KEY["ibm.360"]
    actual = floppy_image.DISK_FORMAT_BY_KEY["ibm.160"]
    source = bytes((index % 251) + 1 for index in range(actual.size_bytes))
    device = _install_fake_device(
        monkeypatch,
        lambda offset, size: source[offset:offset + size],
    )
    output_path = tmp_path / "eof-detected.img"

    diagnostics = floppy_image._read_block_device_recovery_image(
        "A:",
        output_path,
        selected.size_bytes,
        chunk_size=16 * 1024,
        sector_size=SECTOR_SIZE,
    )

    assert device.closed
    assert output_path.read_bytes() == source
    assert diagnostics["detected_format_key"] == "ibm.160"
    assert diagnostics["detection_basis"] == "device_eof_after_readable_prefix"
    assert diagnostics["geometry_mismatch"] is True
    assert diagnostics["stop_reason"] == "detected_smaller_geometry"
    assert diagnostics["unresolved_sectors"] == 0


def test_positive_short_read_ending_at_supported_capacity_detects_geometry(
    monkeypatch,
    tmp_path,
):
    selected = floppy_image.DISK_FORMAT_BY_KEY["ibm.360"]
    actual = floppy_image.DISK_FORMAT_BY_KEY["ibm.180"]
    source = bytes((index % 251) + 1 for index in range(actual.size_bytes))
    device = _install_fake_device(
        monkeypatch,
        lambda offset, size: source[offset:offset + size],
    )
    output_path = tmp_path / "short-eof-detected.img"

    diagnostics = floppy_image._read_block_device_recovery_image(
        "A:",
        output_path,
        selected.size_bytes,
        chunk_size=8 * 1024,
        sector_size=SECTOR_SIZE,
    )

    assert device.closed
    assert output_path.read_bytes() == source
    assert diagnostics["detected_format_key"] == "ibm.180"
    assert diagnostics["detection_basis"] == "device_eof_after_readable_prefix"
    assert diagnostics["stop_reason"] == "detected_smaller_geometry"
    assert diagnostics["bad_sectors"] == 0
    assert diagnostics["unresolved_sectors"] == 0


def _message_diagnostics(**overrides):
    diagnostics = {
        "selected_format_label": "IBM 1.44M HD",
        "detected_format_label": "",
        "geometry_mismatch": False,
        "expected_sectors": 100,
        "attempted_sectors": 100,
        "good_sectors": 100,
        "recovered_after_fallback_sectors": 0,
        "readable_sectors": 100,
        "bad_sectors": 0,
        "unattempted_sectors": 0,
        "sector_size": SECTOR_SIZE,
        "bytes_recovered": 100 * SECTOR_SIZE,
        "stopped_early": False,
        "stop_reason": "",
        "nonzero_sectors": 80,
        "recognizable_signatures": 0,
        "midi_header_signatures": 0,
        "eseq_signatures": 0,
        "pianodir_signatures": 0,
        "fat_copies_valid": 0,
        "root_directory_plausible": False,
        "recovered_files": 0,
    }
    diagnostics.update(overrides)
    return diagnostics


@pytest.mark.parametrize(
    ("diagnostics", "required_fragments"),
    (
        (
            _message_diagnostics(
                detected_format_label="IBM 720K DD",
                geometry_mismatch=True,
                readable_sectors=50,
                good_sectors=50,
                bad_sectors=50,
                bytes_recovered=50 * SECTOR_SIZE,
            ),
            ("IBM 1.44M HD", "IBM 720K DD", "format"),
        ),
        (
            _message_diagnostics(
                readable_sectors=40,
                good_sectors=40,
                bad_sectors=60,
                bytes_recovered=40 * SECTOR_SIZE,
            ),
            ("40.0%", "read"),
        ),
        (
            _message_diagnostics(
                readable_sectors=99,
                good_sectors=99,
                bad_sectors=1,
                bytes_recovered=99 * SECTOR_SIZE,
            ),
            ("99.0%", "unsupported"),
        ),
        (
            _message_diagnostics(nonzero_sectors=0),
            ("blank",),
        ),
        (
            _message_diagnostics(
                fat_copies_valid=2,
                fat_copies_expected=2,
                fat_area_readable=True,
                fat_copies_consistent=True,
                fat_allocation_empty=True,
                root_directory_readable=True,
                root_directory_structurally_valid=True,
                root_directory_entries=0,
            ),
            ("formatted but blank",),
        ),
    ),
    ids=(
        "geometry-mismatch",
        "low-read",
        "unsupported-data",
        "zero-filled-blank",
        "formatted-blank",
    ),
)
def test_recovery_primary_message_classifies_failure(diagnostics, required_fragments):
    message = floppy_image._usb_recovery_no_data_message(diagnostics).lower()

    for fragment in required_fragments:
        assert fragment.lower() in message


def test_usb_recovery_failure_carries_reader_and_scan_diagnostics(monkeypatch):
    disk_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.160"]
    drive = floppy_image.FloppyDriveInfo(
        path="A:",
        size_bytes=disk_format.size_bytes,
        transport="usb",
        model="Test USB floppy",
    )
    source = floppy_image.FloppyRecoverySource(drive, disk_format)
    device = _install_fake_device(
        monkeypatch,
        lambda _offset, size: b"\x00" * size,
    )

    with pytest.raises(floppy_image.FloppyRecoveryError) as error:
        floppy_image.FloppyImageSession._recover_usb_floppy(source)

    diagnostics = error.value.diagnostics
    assert device.closed
    assert diagnostics["drive_path"] == "A:"
    assert diagnostics["drive_model"] == "Test USB floppy"
    assert diagnostics["selected_format_key"] == "ibm.160"
    assert diagnostics["expected_sectors"] == disk_format.size_bytes // SECTOR_SIZE
    assert diagnostics["readable_sectors"] == diagnostics["expected_sectors"]
    assert diagnostics["bad_sectors"] == 0
    assert diagnostics["unattempted_sectors"] == 0
    assert diagnostics["nonzero_sectors"] == 0
    assert diagnostics["recognizable_signatures"] == 0
    assert diagnostics["recovered_files"] == 0
    assert "Floppy diagnostics" in diagnostics["human_report"]
    assert "blank or unformatted" in str(error.value)
    assert "_sector_states" not in diagnostics


def test_cancellation_after_raw_copy_carries_finalized_diagnostics(tmp_path):
    disk_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.160"]
    source_path = tmp_path / "copied.img"
    source_path.write_bytes(b"\x00" * disk_format.size_bytes)
    diagnostics = floppy_image._new_usb_floppy_recovery_diagnostics(
        None,
        disk_format,
        disk_format.size_bytes,
        sector_size=SECTOR_SIZE,
        soft_deadline_seconds=300,
    )
    diagnostics.update(
        {
            "attempted_sectors": disk_format.size_bytes // SECTOR_SIZE,
            "good_sectors": disk_format.size_bytes // SECTOR_SIZE,
            "readable_sectors": disk_format.size_bytes // SECTOR_SIZE,
            "unattempted_sectors": 0,
            "bytes_recovered": disk_format.size_bytes,
            "stop_reason": "completed",
        }
    )

    with pytest.raises(floppy_image.FloppyOperationCancelled) as error:
        floppy_image.FloppyImageSession._recover_from_raw_image(
            source_path,
            str(tmp_path),
            source_name="copied floppy",
            disk_format_hint=disk_format,
            recovery_diagnostics=diagnostics,
            cancel_callback=lambda: True,
        )

    assert error.value.diagnostics["recovery_cancelled"] is True
    assert error.value.diagnostics["readable_sectors"] == (
        disk_format.size_bytes // SECTOR_SIZE
    )
    assert "Floppy diagnostics" in error.value.diagnostics["human_report"]


def test_nonzero_unrecognized_converted_image_is_not_reported_blank(tmp_path):
    disk_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]
    repeated = bytes(range(1, 256))
    data = (repeated * math.ceil(disk_format.size_bytes / len(repeated)))[:disk_format.size_bytes]
    image_path = tmp_path / "unrecognized.img"
    image_path.write_bytes(data)
    sector_map = {
        "found": disk_format.size_bytes // SECTOR_SIZE,
        "total": disk_format.size_bytes // SECTOR_SIZE,
    }

    assert not floppy_image._converted_image_appears_blank_or_unformatted(
        image_path,
        disk_format,
        sector_map,
    )


@pytest.mark.parametrize("fill_byte", (0x00, 0xE5, 0xF6, 0xFF))
def test_fill_only_converted_image_can_be_reported_blank(tmp_path, fill_byte):
    disk_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]
    image_path = tmp_path / "blank.img"
    image_path.write_bytes(bytes([fill_byte]) * disk_format.size_bytes)
    sector_map = {
        "found": disk_format.size_bytes // SECTOR_SIZE,
        "total": disk_format.size_bytes // SECTOR_SIZE,
    }

    assert floppy_image._converted_image_appears_blank_or_unformatted(
        image_path,
        disk_format,
        sector_map,
    )


def test_mixed_blank_fill_values_are_not_called_blank(tmp_path):
    disk_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]
    image_path = tmp_path / "mixed-fill.img"
    image_path.write_bytes(
        (b"\xE5" * (disk_format.size_bytes // 2))
        + (b"\xF6" * (disk_format.size_bytes // 2))
    )

    assert not floppy_image._converted_image_appears_blank_or_unformatted(
        image_path,
        disk_format,
        {
            "found": disk_format.size_bytes // SECTOR_SIZE,
            "total": disk_format.size_bytes // SECTOR_SIZE,
        },
    )


def test_incidental_single_fat_header_is_classified_as_unsupported_not_blank():
    disk_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]
    geometry = floppy_image._fat12_geometry_from_layout(
        floppy_image._PROTECTED_FAT12_LAYOUTS[0]
    )
    data = bytearray(b"\x01" * disk_format.size_bytes)
    data[geometry.fat_offset:geometry.fat_offset + 3] = b"\xF9\xFF\xFF"
    data[geometry.root_offset:geometry.root_offset + geometry.root_size] = (
        b"\x00" * geometry.root_size
    )
    diagnostics = _message_diagnostics(
        selected_bytes=disk_format.size_bytes,
        expected_sectors=disk_format.size_bytes // SECTOR_SIZE,
        attempted_sectors=disk_format.size_bytes // SECTOR_SIZE,
        good_sectors=disk_format.size_bytes // SECTOR_SIZE,
        readable_sectors=disk_format.size_bytes // SECTOR_SIZE,
        bytes_recovered=disk_format.size_bytes,
    )

    floppy_image._populate_recovery_scan_diagnostics(
        data,
        diagnostics,
        disk_format_hint=disk_format,
    )
    message = floppy_image._usb_recovery_no_data_message(diagnostics).lower()

    assert diagnostics["fat_copies_valid"] == 1
    assert diagnostics["fat_copies_consistent"] is False
    assert "unsupported" in message
    assert "formatted but blank" not in message


def test_empty_root_with_allocated_fat_clusters_is_reported_as_damage_not_blank():
    diagnostics = _message_diagnostics(
        fat_copies_expected=2,
        fat_copies_valid=2,
        fat_area_readable=True,
        fat_copies_consistent=True,
        fat_allocated_data_clusters=17,
        fat_allocation_empty=False,
        root_directory_readable=True,
        root_directory_structurally_valid=True,
        root_directory_entries=0,
    )

    message = floppy_image._usb_recovery_no_data_message(diagnostics).lower()

    assert "17 data cluster" in message
    assert "orphaned" in message
    assert "not evidence that the disk is blank" in message
    assert "formatted but blank" not in message


def test_fully_readable_damaged_fat_metadata_is_not_called_unsupported_or_blank():
    diagnostics = _message_diagnostics(
        detection_basis="validated_boot_sector",
        fat_copies_expected=2,
        fat_copies_valid=1,
        fat_area_readable=True,
        fat_copies_consistent=False,
        root_directory_readable=True,
        root_directory_structurally_valid=False,
        root_directory_plausible=False,
        root_directory_entries=None,
    )

    message = floppy_image._usb_recovery_no_data_message(diagnostics).lower()

    assert "filesystem damage" in message
    assert "fat copies" in message
    assert "rather than a blank disk" in message
    assert "unsupported format" not in message


def test_scan_counts_orphaned_fat_allocations_before_blank_classification():
    disk_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]
    layout = floppy_image._PROTECTED_FAT12_LAYOUTS[0]
    geometry = floppy_image._fat12_geometry_from_layout(layout)
    data = bytearray(b"\x01" * disk_format.size_bytes)
    data[:SECTOR_SIZE] = floppy_image._build_standard_fat12_boot_sector(
        layout,
        1,
        b"TEST",
    )
    allocated_fat = bytes([layout["media_descriptor"], 0xFF, 0xFF]) + (
        b"\xFF" * (geometry.fat_size - 3)
    )
    for fat_index in range(geometry.num_fats):
        fat_offset = geometry.fat_offset + fat_index * geometry.fat_size
        data[fat_offset:fat_offset + geometry.fat_size] = allocated_fat
    data[geometry.root_offset:geometry.root_offset + geometry.root_size] = (
        b"\x00" * geometry.root_size
    )
    diagnostics = _message_diagnostics(
        selected_bytes=disk_format.size_bytes,
        expected_sectors=disk_format.size_bytes // SECTOR_SIZE,
        attempted_sectors=disk_format.size_bytes // SECTOR_SIZE,
        good_sectors=disk_format.size_bytes // SECTOR_SIZE,
        readable_sectors=disk_format.size_bytes // SECTOR_SIZE,
        bytes_recovered=disk_format.size_bytes,
    )

    floppy_image._populate_recovery_scan_diagnostics(
        data,
        diagnostics,
        disk_format_hint=disk_format,
    )
    message = floppy_image._usb_recovery_no_data_message(diagnostics).lower()

    assert diagnostics["fat_copies_valid"] == 2
    assert diagnostics["fat_copies_consistent"] is True
    assert diagnostics["fat_allocation_empty"] is False
    assert diagnostics["fat_allocated_data_clusters"] > 0
    assert "orphaned" in message
    assert "formatted but blank" not in message


def test_unreadable_boot_and_root_diagnostics_remain_unknown():
    disk_format = floppy_image.DISK_FORMAT_BY_KEY["ibm.720"]
    layout = floppy_image._PROTECTED_FAT12_LAYOUTS[0]
    geometry = floppy_image._fat12_geometry_from_layout(layout)
    data = bytearray(disk_format.size_bytes)
    data[:SECTOR_SIZE] = floppy_image._build_standard_fat12_boot_sector(
        layout,
        1,
        b"TEST",
    )
    fat_signature = bytes([layout["media_descriptor"], 0xFF, 0xFF])
    for fat_index in range(geometry.num_fats):
        fat_offset = geometry.fat_offset + fat_index * geometry.fat_size
        data[fat_offset:fat_offset + len(fat_signature)] = fat_signature

    diagnostics = floppy_image._new_usb_floppy_recovery_diagnostics(
        None,
        disk_format,
        disk_format.size_bytes,
        sector_size=SECTOR_SIZE,
        soft_deadline_seconds=300,
    )
    sector_states = [1] * (disk_format.size_bytes // SECTOR_SIZE)
    sector_states[0] = 3
    second_fat_start = (
        geometry.fat_offset + geometry.fat_size
    ) // SECTOR_SIZE
    second_fat_end = second_fat_start + (
        geometry.fat_size // SECTOR_SIZE
    )
    sector_states[second_fat_start:second_fat_end] = [3] * (
        second_fat_end - second_fat_start
    )
    root_start = geometry.root_offset // SECTOR_SIZE
    root_end = (geometry.root_offset + geometry.root_size) // SECTOR_SIZE
    sector_states[root_start:root_end] = [3] * (root_end - root_start)
    diagnostics["_sector_states"] = sector_states

    floppy_image._populate_recovery_scan_diagnostics(
        data,
        diagnostics,
        disk_format_hint=disk_format,
    )

    assert diagnostics["boot_sector_readable"] is False
    assert diagnostics["boot_signature_present"] is None
    assert diagnostics["fat_area_readable"] is False
    assert diagnostics["fat_copies_consistent"] is None
    assert diagnostics["root_directory_readable"] is False
    assert diagnostics["root_directory_structurally_valid"] is None
    assert diagnostics["root_directory_plausible"] is None
    assert diagnostics["root_directory_entries"] is None
    assert diagnostics["pianodir_directory_entry_found"] is None
    assert "0x55AA signature: unknown" in diagnostics["human_report"]
    assert "Active root entries: unknown" in diagnostics["human_report"]
    assert "PIANODIR.FIL directory entry: unknown" in diagnostics["human_report"]


def test_human_diagnostics_include_bounded_retry_policy_and_read_errors():
    diagnostics = _message_diagnostics(
        read_calls=7,
        fallback_read_calls=6,
        read_passes=2,
        read_errors={"device timeout": 4, "CRC error": 2},
    )

    report = floppy_image.format_floppy_recovery_diagnostics(diagnostics)

    assert "Read passes used: 2" in report
    assert "at most one per-sector fallback attempt" in report
    assert "4x device timeout" in report
    assert "2x CRC error" in report
