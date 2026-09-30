"""Detached, explicitly authorized replacement of a portable APS executable."""

from dataclasses import dataclass
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time

from .self_update import UpdateCancelled, UpdateError, cleanup_staged_update


HELPER_ARGUMENT = "--aps-apply-update"
PREPARE_TIMEOUT = 120.0
PARENT_TIMEOUT = 180.0
REPLACE_TIMEOUT = 30.0
STARTUP_TIMEOUT = 90.0
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class _UpdateLockBusy(UpdateError):
    """Another helper owns this application's replacement transaction."""


class _TargetUpdateLock:
    """A persistent, nonblocking OS lock avoids unlink/recreate races."""

    def __init__(self, target):
        self.target = Path(target)
        self.path = self.target.with_name(self.target.name + ".update-lock")
        self.descriptor = None
        self.locked = False

    def __enter__(self):
        marker = ("\0APS portable updater lock v1\n"
                  + os.path.normcase(self.target.name) + "\n").encode("utf-8")
        flags = os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        created = False
        try:
            try:
                self.descriptor = os.open(self.path, flags | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
            except FileExistsError:
                if self.path.is_symlink():
                    raise UpdateError("The application update lock path is a symbolic link.")
                self.descriptor = os.open(self.path, flags)
            info = os.fstat(self.descriptor)
            path_info = self.path.lstat()
            if (not stat.S_ISREG(info.st_mode) or not stat.S_ISREG(path_info.st_mode)
                    or (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino)
                    or info.st_nlink != 1):
                raise UpdateError("The application update lock path is not a safe regular file.")
            if created:
                os.write(self.descriptor, marker)
                os.fsync(self.descriptor)
            os.lseek(self.descriptor, 0, os.SEEK_SET)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise _UpdateLockBusy(
                    "Another APS update is already using this application. "
                    "Please wait for that update to finish."
                ) from exc
            self.locked = True
            os.lseek(self.descriptor, 0, os.SEEK_SET)
            if os.read(self.descriptor, len(marker) + 1) != marker:
                raise UpdateError(
                    f"The update lock path is occupied by an unrecognized file: {self.path}"
                )
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_exception):
        if self.descriptor is not None:
            try:
                if self.locked:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(self.descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(self.descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            finally:
                os.close(self.descriptor)
                self.descriptor = None
                self.locked = False


def _write_json(path, value):
    temporary = path.with_name(path.name + ".new")
    with open(temporary, "x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _write_marker(path, token):
    _write_json(path, {"token": token})


def _has_marker(path, token):
    try:
        if path.is_symlink() or path.stat().st_size > 256:
            return False
        return json.loads(path.read_text(encoding="utf-8")) == {"token": token}
    except (OSError, ValueError):
        return False


def _hash_file(path):
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise UpdateError("The update executable is not a regular file.")
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def _sync_directory(directory):
    if os.name == "nt":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        pass  # Some removable filesystems do not support directory fsync.


def _clean_launch_environment():
    environment = dict(os.environ)
    bundle_roots = [os.path.normcase(os.path.abspath(value)) for value in (
        environment.get("APPDIR"), environment.get("_PYI_APPLICATION_HOME_DIR"),
        getattr(sys, "_MEIPASS", None),
    ) if value]

    def from_old_bundle(value):
        try:
            path = os.path.normcase(os.path.abspath(value))
            return any(os.path.commonpath((path, root)) == root for root in bundle_roots)
        except (OSError, ValueError):
            return False

    if bundle_roots and "PATH" in environment:
        environment["PATH"] = os.pathsep.join(
            item for item in environment["PATH"].split(os.pathsep) if not from_old_bundle(item)
        )
    for key in list(environment):
        if key.startswith(("_PYI", "APS_UPDATE_")) or key in {
            "_MEIPASS2", "APPIMAGE", "APPDIR", "OWD", "ARGV0",
            "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QT_QPA_FONTDIR",
            "QML2_IMPORT_PATH", "QML_IMPORT_PATH", "PYTHONHOME", "PYTHONPATH",
        }:
            environment.pop(key, None)
    for key in ("LD_LIBRARY_PATH", "LIBPATH"):
        original = environment.pop(key + "_ORIG", None)
        if original is None:
            environment.pop(key, None)
        else:
            environment[key] = original
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment


def _launch(command, *, cwd, environment):
    kwargs = dict(cwd=str(cwd), env=environment, stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if os.name == "nt":
        # PyInstaller may have installed its DLL search directory in this process.
        # The child is an independent bundle and must use its own dependencies.
        kernel = ctypes.windll.kernel32
        buffer = ctypes.create_unicode_buffer(32768)
        kernel.GetDllDirectoryW(len(buffer), buffer)
        kernel.SetDllDirectoryW(None)
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            return subprocess.Popen(command, **kwargs)
        finally:
            kernel.SetDllDirectoryW(buffer.value or None)
    kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def _parent_identity(pid):
    try:
        # /proc's start tick prevents a recycled Linux PID from authorizing a swap.
        data = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        return data.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return ""


class _ParentProcess:
    def __init__(self, pid, identity):
        self.pid, self.identity, self.handle = pid, identity, None
        if os.name == "nt":
            from ctypes import wintypes
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self.kernel.WaitForSingleObject.restype = wintypes.DWORD
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = self.kernel.OpenProcess(0x00100000, False, pid)
            if not self.handle:
                raise UpdateError("Could not monitor APS shutdown safely.")
        elif not identity or _parent_identity(pid) != identity:
            raise UpdateError("The APS process changed before its update helper was ready.")

    def exited(self):
        if self.handle:
            result = self.kernel.WaitForSingleObject(self.handle, 0)
            if result not in (0, 0x102):
                raise UpdateError("Could not determine whether APS has finished closing.")
            return result == 0
        return _parent_identity(self.pid) != self.identity

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _validate_helper_directory(directory, token):
    directory = Path(directory)
    return (
        directory.is_absolute() and not directory.is_symlink()
        and directory.parent.resolve() == Path(tempfile.gettempdir()).resolve()
        and directory.name.startswith(".aps-update-helper-")
        and _has_marker(directory / "owner", token)
    )


def _read_job(job_path):
    job_path = Path(job_path)
    directory = job_path.parent
    if (not job_path.is_absolute() or job_path.name != "job.json" or job_path.is_symlink()
            or directory.is_symlink() or not directory.name.startswith(".aps-update-")
            or directory.name.startswith(".aps-update-helper-")
            or job_path.stat().st_size > 32768):
        raise UpdateError("The updater received an invalid staging location.")
    job = json.loads(job_path.read_text(encoding="utf-8"))
    if not isinstance(job, dict) or job.get("format") != 1:
        raise UpdateError("The updater received an unsupported job.")
    for key in ("token", "sha256", "original_sha256"):
        if not isinstance(job.get(key), str) or not _HASH.fullmatch(job[key]):
            raise UpdateError("The updater job has an invalid verification value.")
    target = Path(job.get("target", ""))
    payload = Path(job.get("payload", ""))
    kind = job.get("kind")
    expected_payload = "payload.exe" if kind == "windows-onefile" else "payload.AppImage"
    if (kind not in {"windows-onefile", "linux-appimage"} or not target.is_absolute()
            or target.is_symlink() or target.parent.resolve() != directory.parent.resolve()
            or target != target.resolve() or directory != directory.resolve()
            or target.name in {"", ".", ".."}
            or payload != directory / expected_payload or payload.is_symlink()
            or not isinstance(job.get("parent_pid"), int) or job["parent_pid"] < 1
            or not _validate_helper_directory(job.get("helper_directory", ""), job["token"])):
        raise UpdateError("The updater job does not identify a safe executable replacement.")
    args = job.get("arguments")
    if (not isinstance(args, list) or len(args) > 64
            or any(not isinstance(arg, str) or len(arg) > 8192 or "\x00" in arg
                   or arg.startswith("--aps-") for arg in args)):
        raise UpdateError("The updater received unsafe restart arguments.")
    job["directory"] = str(directory)
    return job


def _cleanup_helper(directory, token, *, timeout=60.0):
    if not _validate_helper_directory(directory, token):
        return
    directory = Path(directory)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _has_marker(directory / "done", token):
            break
        time.sleep(0.2)
    else:
        return
    # Windows releases the helper executable just after its Python process exits.
    while time.monotonic() < deadline:
        try:
            shutil.rmtree(directory)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.2)


@dataclass
class UpdateHandoff:
    directory: Path
    helper_directory: Path
    token: str
    process: object
    _committed: bool = False
    _cancelled: bool = False

    def commit(self):
        if self._cancelled:
            raise UpdateError("This update was cancelled.")
        if self._committed:
            return
        if self.process.poll() is not None:
            raise UpdateError("The update helper stopped before APS could restart.")
        _write_marker(self.directory / "commit", self.token)
        self._committed = True

    def cancel(self):
        if self._committed or self._cancelled:
            return
        self._cancelled = True
        try:
            _write_marker(self.directory / "cancel", self.token)
        except OSError:
            pass
        def collect_cancelled_helper():
            deadline = time.monotonic() + PARENT_TIMEOUT + 30
            while self.process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.2)
            if self.process.poll() is not None:
                try:
                    job = _read_job(self.directory / "job.json")
                    if job["token"] == self.token and not (self.directory / "failure.json").exists():
                        shutil.rmtree(self.directory)
                except (OSError, ValueError, UpdateError):
                    pass
                try:
                    _write_marker(self.helper_directory / "done", self.token)
                except OSError:
                    pass
                _cleanup_helper(self.helper_directory, self.token)
        threading.Thread(target=collect_cancelled_helper, daemon=True).start()


def prepare_handoff(staged, *, cancel_callback=None):
    directory, target = Path(staged.directory), Path(staged.target.path)
    token = secrets.token_hex(32)
    helper_directory = Path(tempfile.mkdtemp(prefix=".aps-update-helper-"))
    os.chmod(helper_directory, 0o700)
    helper = helper_directory / ("APSUpdateHelper.exe" if staged.target.kind == "windows-onefile"
                                 else "APSUpdateHelper.AppImage")
    process = None
    try:
        _write_marker(helper_directory / "owner", token)
        if shutil.disk_usage(helper_directory).free < target.stat().st_size + 16 * 1024 * 1024:
            raise UpdateError("There is not enough free temporary storage for the update helper.")
        with open(target, "rb") as source, open(helper, "xb") as destination:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                if cancel_callback and cancel_callback():
                    raise UpdateCancelled("The update was cancelled.")
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(helper, stat.S_IMODE(target.stat().st_mode) | stat.S_IXUSR)
        if _hash_file(helper) != staged.original_sha256:
            raise UpdateError("This copy of APS changed while its update was being prepared.")
        arguments = list(sys.argv[1:])
        if any(arg.startswith("--aps-") for arg in arguments):
            arguments = []
        job_path = directory / "job.json"
        _write_json(job_path, {
            "format": 1, "target": str(target), "payload": str(staged.payload),
            "kind": staged.target.kind, "version": staged.version, "sha256": staged.sha256,
            "original_sha256": staged.original_sha256, "parent_pid": os.getpid(),
            "parent_identity": _parent_identity(os.getpid()), "arguments": arguments,
            "helper_directory": str(helper_directory), "token": token,
        })
        _read_job(job_path)
        process = _launch([str(helper), HELPER_ARGUMENT, str(job_path)],
                          cwd=helper_directory, environment=_clean_launch_environment())
        handoff = UpdateHandoff(directory, helper_directory, token, process)
        deadline = time.monotonic() + PREPARE_TIMEOUT
        while time.monotonic() < deadline:
            if cancel_callback and cancel_callback():
                handoff.cancel()
                raise UpdateCancelled("The update was cancelled.")
            if _has_marker(directory / "ready", token):
                return handoff
            if process.poll() is not None:
                raise UpdateError("The update helper could not start. The current copy is unchanged.")
            time.sleep(0.1)
        handoff.cancel()
        raise UpdateError("The update helper did not become ready in time. Please try again.")
    except BaseException:
        if process is None or process.poll() is not None:
            shutil.rmtree(helper_directory, ignore_errors=True)
            cleanup_staged_update(staged)
        else:
            UpdateHandoff(directory, helper_directory, token, process).cancel()
        raise


def _replace_with_retry(source, destination, *, timeout=REPLACE_TIMEOUT):
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.replace(source, destination)
            return
        except OSError as exc:
            retryable = exc.errno in {errno.EACCES, errno.EPERM, errno.EBUSY, errno.ETXTBSY,
                                     errno.EIO, errno.ENODEV, errno.ENOENT}
            if not retryable or time.monotonic() >= deadline:
                raise
            time.sleep(0.2)


def _record_failure(job, message):
    try:
        _write_json(Path(job["directory"]) / "failure.json", {
            "error": str(message), "target": job["target"],
            "backup": job["target"] + ".previous", "version": job.get("version", ""),
            "help": "Keep this folder and the .previous recovery copy. Reinsert the drive "
                    "if disconnected; the recovery copy can be renamed to the original filename.",
        })
    except OSError:
        pass


def _restart(job, *, acknowledge):
    environment = _clean_launch_environment()
    environment["APS_UPDATE_HELPER_DIRECTORY"] = job["helper_directory"]
    environment["APS_UPDATE_TOKEN"] = job["token"]
    if acknowledge:
        environment["APS_UPDATE_ACK"] = str(Path(job["directory"]) / "startup-ack")
    else:
        environment["APS_UPDATE_FAILURE"] = str(Path(job["directory"]) / "failure.json")
    return _launch([job["target"], *job["arguments"]],
                   cwd=Path(job["target"]).parent, environment=environment)


def _replace_and_restart(job, *, restart=None, startup_timeout=STARTUP_TIMEOUT):
    with _TargetUpdateLock(job["target"]):
        try:
            return _replace_locked_and_restart(job, restart=restart, startup_timeout=startup_timeout)
        except Exception as exc:
            # Keep recovery and its relaunch under the same per-target lock.
            _record_failure(job, exc)
            child = job.get("_restart_process")
            if child is None or child.poll() is not None:
                try:
                    if _hash_file(Path(job["target"])) == job["original_sha256"]:
                        (restart or _restart)(job, acknowledge=False)
                except (OSError, UpdateError):
                    pass
            raise


def _replace_locked_and_restart(job, *, restart=None, startup_timeout=STARTUP_TIMEOUT):
    restart = restart or _restart
    directory, target, payload = (Path(job[key]) for key in ("directory", "target", "payload"))
    backup = target.with_name(target.name + ".previous")
    if target.is_symlink() or payload.is_symlink() or backup.is_symlink():
        raise UpdateError("An executable or recovery path became a symbolic link.")
    if _hash_file(target) != job["original_sha256"] or _hash_file(payload) != job["sha256"]:
        raise UpdateError("An executable changed after verification. The update was not installed.")
    mode = stat.S_IMODE(target.stat().st_mode)
    os.chmod(payload, mode)
    # Make the recovery copy durable before the one atomic installation rename.
    # Power loss cannot leave the normal executable path between two renames.
    backup_staging = directory / "previous.tmp"
    with open(target, "rb") as source, open(backup_staging, "xb") as destination:
        shutil.copyfileobj(source, destination, 1024 * 1024)
        destination.flush()
        os.fsync(destination.fileno())
    os.chmod(backup_staging, mode)
    if _hash_file(backup_staging) != job["original_sha256"]:
        raise UpdateError("The running executable changed while its recovery copy was being saved.")
    if backup.exists():
        if not backup.is_file():
            raise UpdateError("The executable recovery path is occupied by a folder.")
        # Keep the older recovery copy until startup succeeds. The helper then
        # removes its own staging directory, leaving just the newest .previous
        # beside the application. Failed updates retain this copy for recovery.
        older_backup = directory / "older.previous"
        if older_backup.exists() or older_backup.is_symlink():
            raise UpdateError("The update staging recovery path is already occupied.")
        _replace_with_retry(backup, older_backup)
    _replace_with_retry(backup_staging, backup)
    _sync_directory(target.parent)
    _replace_with_retry(payload, target)
    _sync_directory(target.parent)
    child = None
    try:
        child = restart(job, acknowledge=True)
        job["_restart_process"] = child
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if _has_marker(directory / "startup-ack", job["token"]):
                return
            if child.poll() is not None:
                raise UpdateError("The updated application closed before completing startup.")
            time.sleep(0.1)
        raise UpdateError("The updated application did not confirm startup. Its recovery copy was kept.")
    except BaseException:
        # Never terminate a customer process or replace an executable still in use.
        if child is None or child.poll() is not None:
            _replace_with_retry(backup, target)
            _sync_directory(target.parent)
        raise


def _run_helper(job_path, *, parent_factory=_ParentProcess, replace_and_restart=None):
    job = _read_job(job_path)
    directory, token = Path(job["directory"]), job["token"]
    parent = None
    try:
        parent = parent_factory(job["parent_pid"], job.get("parent_identity", ""))
        _write_marker(directory / "ready", token)
        deadline = time.monotonic() + PARENT_TIMEOUT
        while time.monotonic() < deadline:
            if _has_marker(directory / "cancel", token):
                shutil.rmtree(directory)
                return 0
            if _has_marker(directory / "commit", token) and parent.exited():
                (replace_and_restart or _replace_and_restart)(job)
                shutil.rmtree(directory)
                return 0
            time.sleep(0.1)
        raise UpdateError("APS did not authorize and complete shutdown in time. No update was installed.")
    except Exception as exc:
        _record_failure(job, exc)
        return 1
    finally:
        if parent is not None:
            parent.close()
        try:
            _write_marker(Path(job["helper_directory"]) / "done", token)
        except OSError:
            pass


def run_update_helper_from_argv(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 2 or argv[1] != HELPER_ARGUMENT:
        return None
    if len(argv) != 3:
        return 2
    try:
        return _run_helper(Path(argv[2]))
    except Exception:
        return 2


def mark_update_started():
    """Acknowledge GUI startup; return a validated update failure for the UI, if any."""
    ack_value = os.environ.pop("APS_UPDATE_ACK", "")
    token = os.environ.pop("APS_UPDATE_TOKEN", "")
    os.environ.pop("APS_UPDATE_HELPER_DIRECTORY", "")
    failure_value = os.environ.pop("APS_UPDATE_FAILURE", "")
    if not token:
        return ""
    if not ack_value and not failure_value:
        return ""
    try:
        marker = Path(ack_value or failure_value)
        job = _read_job(marker.parent / "job.json")
        running = Path(os.environ.get("APPIMAGE") or sys.executable)
        if (token != job["token"]
                or running.resolve() != Path(job["target"]).resolve()):
            return ""
        if ack_value:
            if marker.name != "startup-ack":
                return ""
            _write_marker(marker, token)
        message = ""
        if failure_value and not ack_value:
            if marker.name != "failure.json" or marker.is_symlink() or marker.stat().st_size > 32768:
                return ""
            failure = json.loads(marker.read_text(encoding="utf-8"))
            if failure.get("target") == job["target"]:
                message = f"{failure.get('error', 'The update could not be installed.')}\n\n{marker}"
        # Reading the job requires the helper's owner marker. A rollback helper
        # may already have exited, so start its collector only after validation
        # and failure extraction/acknowledgement are fully complete.
        threading.Thread(target=_cleanup_helper,
                         args=(Path(job["helper_directory"]), token), daemon=True).start()
        return message
    except (OSError, ValueError, UpdateError):
        pass
    return ""
