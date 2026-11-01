"""Track removable media independently of filesystem labels and boot sectors.

Native Linux floppy drives expose an actively polled generation/change state:
https://github.com/torvalds/linux/blob/v6.12/include/uapi/linux/fd.h
https://github.com/torvalds/linux/blob/v6.12/drivers/block/floppy.c

For removable SCSI disks (including USB floppies), a blocking whole-device
open synchronously checks media events. Only then is BLKGETDISKSEQ current:
https://github.com/torvalds/linux/blob/v6.12/drivers/scsi/sd.c
https://github.com/torvalds/linux/blob/v6.12/block/disk-events.c
https://github.com/torvalds/linux/blob/v6.12/include/uapi/linux/fs.h

A disk sequence query alone does not poll hardware. Partition opens can skip
the driver's open callback, so this guard accepts only whole removable sd
devices that advertise media-change events. Unsupported drivers, broken native
change lines, and unavailable checks fail closed. These checks still depend on
the drive reporting changes; they do not provide a physical eject lock.
"""

import ctypes
import os
from pathlib import Path
import stat
import sys


class _FloppyMaxErrors(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint) for name in ("abort", "read_track", "reset", "recal", "reporting")]


class _FloppyDriveParams(ctypes.Structure):
    # Native widths and alignment match linux/fd.h, including its compat ABI.
    _fields_ = [
        ("cmos", ctypes.c_byte),
        *[(name, ctypes.c_ulong) for name in ("max_dtr", "hlt", "hut", "srt", "spinup", "spindown")],
        *[(name, ctypes.c_ubyte) for name in ("spindown_offset", "select_delay", "rps", "tracks")],
        ("timeout", ctypes.c_ulong),
        ("interleave_sect", ctypes.c_ubyte),
        ("max_errors", _FloppyMaxErrors),
        ("flags", ctypes.c_ubyte),
        ("read_track", ctypes.c_byte),
        ("autodetect", ctypes.c_short * 8),
        ("checkfreq", ctypes.c_int),
        ("native_format", ctypes.c_int),
    ]


class _FloppyDriveState(ctypes.Structure):
    _fields_ = [
        *[(name, ctypes.c_ulong) for name in ("flags", "spinup_date", "select_date", "first_read_date")],
        *[(name, ctypes.c_short) for name in ("probed_format", "track", "maxblock", "maxtrack")],
        *[(name, ctypes.c_int) for name in ("generation", "keep_data", "fd_ref", "fd_device")],
        ("last_checked", ctypes.c_ulong),
        ("dmabuf", ctypes.c_void_p),
        ("bufblocks", ctypes.c_int),
    ]


def _ior(kind, number, output_type):
    return (2 << 30) | (ctypes.sizeof(output_type) << 16) | (kind << 8) | number


_FDGETDRVPRM = _ior(2, 0x11, _FloppyDriveParams)
_FDPOLLDRVSTAT = _ior(2, 0x13, _FloppyDriveState)
_BLKGETDISKSEQ = _ior(0x12, 128, ctypes.c_uint64)
_FD_BROKEN_DCL = 0x20
_FD_NEWCHANGE = 1 << 2
_FD_UNCERTAIN = (1 << 1) | (1 << 4) | (1 << 6)


def _ioctl(descriptor, request, buffer):
    # Import lazily: the application also imports its image module on Windows.
    import fcntl
    return fcntl.ioctl(descriptor, request, buffer)


def _read_ioctl(descriptor, request, output_type):
    size = ctypes.sizeof(output_type)
    reply = _ioctl(descriptor, request, bytes(size))
    if not isinstance(reply, bytes) or len(reply) != size:
        raise OSError("The media-change query returned an incomplete response.")
    return output_type.from_buffer_copy(reply)


def _identity(info):
    return info.st_dev, info.st_ino, getattr(info, "st_rdev", 0), stat.S_IFMT(info.st_mode)


def _sd_device_directory(info):
    return Path("/sys/dev/block") / f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}"


def _validate_sd_device(info):
    directory = _sd_device_directory(info).resolve(strict=True)
    try:
        (directory / "partition").stat()
    except FileNotFoundError:
        pass
    else:
        raise OSError("Media-change checks require the whole floppy device, not a partition.")
    if ((directory / "removable").read_text().strip() != "1"
            or (directory / "device" / "driver").resolve(strict=True).name != "sd"
            or "media_change" not in (directory / "events").read_text().split()):
        raise OSError("This device does not provide a supported active media-change check.")
    return directory


class PosixMediaGuard:
    """Hold the original device and make every later uncertainty permanent.

    Regular image files use descriptor/path identity, permitting authorized
    in-place FAT changes. Real block devices require Linux driver evidence;
    filesystem metadata is never used as a substitute for a media-change signal.
    """

    def __init__(self, path):
        self.path = os.path.abspath(os.fsdecode(path))
        self._descriptor = None
        self._failure = None
        self._open_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        try:
            self._descriptor = os.open(self.path, self._open_flags)
            info = os.fstat(self._descriptor)
            self._identity = _identity(info)
            if stat.S_ISREG(info.st_mode):
                self.method = "regular_file"
                self.count = None
            elif stat.S_ISBLK(info.st_mode) and sys.platform.startswith("linux"):
                if os.major(info.st_rdev) == 2:
                    self.method = "linux_floppy_generation"
                    self.count = self._native_count()
                else:
                    self.method = "linux_disk_sequence"
                    self._sysfs_directory = _validate_sd_device(info)
                    self.count = self._disk_sequence(self._descriptor)
            else:
                raise OSError("This target does not provide a supported media-change check.")
        except BaseException:
            self.close()
            raise

    def _native_count(self):
        generation = getattr(self, "count", None)
        for _attempt in range(3):
            params = _read_ioctl(self._descriptor, _FDGETDRVPRM, _FloppyDriveParams)
            if params.flags & _FD_BROKEN_DCL:
                raise OSError("The floppy drive's disk-change line is disabled or unreliable.")
            state = _read_ioctl(self._descriptor, _FDPOLLDRVSTAT, _FloppyDriveState)
            if state.flags & _FD_UNCERTAIN or (generation is not None and state.generation != generation):
                raise OSError("The target floppy changed or its inserted media cannot be verified.")
            generation = state.generation
            if not state.flags & _FD_NEWCHANGE:
                return generation
            # floppy_start sets NEWCHANGE for ordinary requests. FDPOLL
            # restarts queued I/O before copying its response, so this bit can
            # reappear after a successful poll. Re-poll without adopting a new
            # generation, even during construction. Never ignore it: an
            # incomplete hardware poll can also leave NEWCHANGE asserted.
        raise OSError("The target floppy changed or its inserted media cannot be verified.")

    @staticmethod
    def _disk_sequence(descriptor):
        return _read_ioctl(descriptor, _BLKGETDISKSEQ, ctypes.c_uint64).value

    def check(self):
        if self._failure is not None:
            raise self._failure
        try:
            if self._descriptor is None:
                raise OSError("The media-change guard is closed.")
            if (_identity(os.fstat(self._descriptor)) != self._identity
                    or _identity(os.stat(self.path)) != self._identity):
                raise OSError("The target device or image path changed during saving.")
            if self.method == "regular_file":
                return
            if self.method == "linux_floppy_generation":
                current = self._native_count()
            else:
                info = os.fstat(self._descriptor)
                if _validate_sd_device(info) != self._sysfs_directory:
                    raise OSError("The target floppy device changed during saving.")
                # A fresh blocking open invokes sd_open -> media event check,
                # including when background polling is disabled. Never reset
                # the original counter when this opens or revalidates media.
                probe = os.open(self.path, self._open_flags)
                try:
                    if _identity(os.fstat(probe)) != self._identity:
                        raise OSError("The target floppy device changed during saving.")
                    current = self._disk_sequence(probe)
                    if self._disk_sequence(self._descriptor) != self.count:
                        raise OSError("The target floppy changed during saving.")
                finally:
                    os.close(probe)
            if current != self.count:
                raise OSError("The target floppy changed during saving.")
        except OSError as exc:
            self._failure = exc
            raise

    def close(self):
        if self._descriptor is not None:
            descriptor, self._descriptor = self._descriptor, None
            os.close(descriptor)
