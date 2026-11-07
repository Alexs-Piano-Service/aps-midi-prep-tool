"""Write a complete, checked file before replacing its destination."""

import errno
import hashlib
import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass, replace


def _file_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _parent_identity(path):
    info = os.stat(os.path.dirname(os.path.abspath(path)))
    return info.st_dev, info.st_ino


def _check_parent(path, expected):
    if _parent_identity(path) != expected:
        raise OSError("The destination folder is unavailable or changed; cleanup could not be verified.")


@dataclass(frozen=True)
class PublishedFile:
    """Evidence captured from the file we created, never from a later occupant."""

    destination: str
    identity: tuple
    sha256: bytes
    parent_identity: tuple
    ctime_ns: int | None = None

    def check_unchanged(self, destination=None, *, missing_ok=False):
        """Verify that this path still contains our unchanged publication.

        Replaced or edited files, symlinks, and filesystems without usable file
        IDs fail closed. A missing file is acceptable only for removal, and
        only when its original parent directory is still present.
        An optional resolved destination binds a pending replacement to this
        exact path, preventing a second symlink resolution from redirecting it.
        """
        if (destination is not None and
                os.path.normcase(os.path.abspath(os.fsdecode(destination))) !=
                os.path.normcase(os.path.abspath(self.destination))):
            raise OSError("The output path changed; it was kept.")
        _check_parent(self.destination, self.parent_identity)
        try:
            before = os.lstat(self.destination)
        except FileNotFoundError:
            # ENOENT also occurs when removable media disappears. Only a
            # missing name in the original directory proves cleanup succeeded.
            _check_parent(self.destination, self.parent_identity)
            if missing_ok:
                return False
            raise OSError("The published output is missing; its original was not restored.") from None
        if (not self.identity[1] or not stat.S_ISREG(before.st_mode)
                or _file_identity(before) != self.identity
                or self.ctime_ns is not None and before.st_ctime_ns != self.ctime_ns):
            raise OSError("The output changed or its ownership could not be verified; it was kept.")
        digest = hashlib.sha256()
        with open(self.destination, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode) or _file_identity(opened) != self.identity:
                raise OSError("The output changed while checking it; it was kept.")
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
            after_read = os.fstat(handle.fileno())
            if (_file_identity(after_read) != self.identity
                    or after_read.st_ctime_ns != opened.st_ctime_ns):
                raise OSError("The output changed while checking it; it was kept.")
        after = os.lstat(self.destination)
        # Compare ctime only between queries of the same kind: Windows path
        # and descriptor queries may disagree about it for an unchanged file.
        if (not stat.S_ISREG(after.st_mode) or _file_identity(after) != self.identity
                or after.st_ctime_ns != before.st_ctime_ns or digest.digest() != self.sha256):
            raise OSError("The output contents changed; it was kept.")
        _check_parent(self.destination, self.parent_identity)
        return True

    def quarantine_if_unchanged(self, destination=None, *, missing_ok=False):
        """Detach the current entry without ever discarding its inode.

        The first check avoids disturbing known conflicts. Only the check of
        the moved entry establishes what the rename actually captured. Keep
        that entry even on success: an existing writer can still change it
        through an open handle after any ownership check. Callers must report
        the returned recovery paths and must not automatically delete them.
        """
        if not self.check_unchanged(destination, missing_ok=missing_ok):
            return ()
        directory = tempfile.mkdtemp(
            prefix=".aps_rollback_", dir=os.path.dirname(os.path.abspath(self.destination)),
        )
        captured = os.path.join(directory, "captured")
        moved = False
        # Write the mapping before moving anything so an interrupted rollback
        # still leaves the detached file's original location discoverable.
        try:
            with open(os.path.join(directory, "recovery.json"), "x", encoding="utf-8") as handle:
                json.dump({"destination": self.destination, "captured": captured,
                           "identity": self.identity, "sha256": self.sha256.hex()}, handle)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            _check_parent(self.destination, self.parent_identity)
            # The fresh private directory has no existing captured entry.
            # Rename is atomic and stays on the destination's filesystem.
            os.rename(self.destination, captured)
            moved = True
            _check_parent(self.destination, self.parent_identity)
            # Our rename changes ctime; retain the original identity, size,
            # mtime and digest and validate the captured entry in its new parent.
            replace(self, destination=captured, parent_identity=_parent_identity(captured),
                    ctime_ns=None).check_unchanged()
        except BaseException as exc:
            # Do not drop the known recovery path when a disconnect makes
            # existence queries fail. A rename error can also be ambiguous.
            retained = (captured,) if moved else (captured, self.destination)
            raise FileRollbackError(exc, retained, directory) from exc
        return (captured,)

    def remove_if_unchanged(self):
        """Remove the public name and return preserved quarantine paths."""
        paths = self.quarantine_if_unchanged(missing_ok=True)
        if paths and os.path.lexists(self.destination):
            raise FileRollbackError(
                OSError("Another output appeared during rollback; it was kept."),
                (*paths, self.destination), os.path.dirname(paths[0]),
            )
        return paths


class FileRollbackError(OSError):
    """Rollback stopped without discarding the entry it captured."""

    def __init__(self, error, retained_paths, recovery_directory):
        self.retained_paths = tuple(retained_paths)
        self.recovery_directory = recovery_directory
        self.original_exception = error
        super().__init__(f"{error}\nRollback recovery files were kept at: "
                         + ", ".join(self.retained_paths)
                         + f"\nRecovery information: {recovery_directory}")


class FilePublicationCleanupError(OSError):
    """Publication failed and the new partial output could not be removed."""

    def __init__(self, destination, error, cleanup_error):
        self.retained_paths = (os.fsdecode(destination),)
        self.original_exception = error
        super().__init__(
            f"{error}\nThe partial output could not be removed: {destination}\n{cleanup_error}"
        )


def publish_new_file(staged_path, destination, *, check_cancel=None, published_callback=None,
                     allow_copy=True):
    """Publish an already-verified file without replacing an existing path.

    A hard link publishes atomically where supported. FAT/exFAT and other
    filesystems without links use an exclusive streaming copy and readback
    verification. Failure removes only the partial file created by this call;
    the staged source always remains available to its caller. ``check_cancel``
    takes no arguments and may raise the caller's cancellation exception.
    ``published_callback`` receives a ``PublishedFile`` receipt before the
    final cancellation check, allowing a caller to roll back a larger set.
    ``allow_copy=False`` requires atomic publication and refuses a streaming
    fallback, which is necessary when restoring files during rollback.
    """
    def check():
        if check_cancel is not None:
            check_cancel()

    check()
    parent_identity = _parent_identity(destination)
    receipt = None
    if published_callback is not None:
        # For hard links the stage is the created inode. Fingerprint it before
        # linking so a later edit/replacement cannot be adopted as our output.
        with open(staged_path, "rb") as source:
            source_stat = os.fstat(source.fileno())
            digest = hashlib.sha256()
            while True:
                check()
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(source.fileno())
            if (_file_identity(after) != _file_identity(source_stat)
                    or after.st_ctime_ns != source_stat.st_ctime_ns):
                raise OSError("The staged output changed while it was being published.")
            receipt = PublishedFile(
                os.fsdecode(destination), _file_identity(source_stat), digest.digest(), parent_identity,
            )
    try:
        os.link(staged_path, destination)
    except OSError as exc:
        unsupported = {errno.EPERM, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOSYS}
        if os.name == "nt":
            unsupported.add(errno.EINVAL)
        if exc.errno not in unsupported:
            raise
        if not allow_copy:
            raise OSError("Atomic exclusive publication is unavailable; recovery files were kept.") from exc
    else:
        if published_callback is not None:
            published_callback(receipt)
            check()
        return

    check()
    with open(staged_path, "rb") as incoming:
        source_stat = os.fstat(incoming.fileno())
        source_identity = (source_stat.st_dev, source_stat.st_ino, source_stat.st_size,
                           source_stat.st_mtime_ns, source_stat.st_ctime_ns)
        # Creation is deliberately outside cleanup: if another process won the
        # destination name, xb must fail without removing that process's file.
        outgoing = open(destination, "xb")
        created_identity = None
        try:
            digest = hashlib.sha256()
            copied = 0
            with outgoing:
                created = os.fstat(outgoing.fileno())
                created_identity = (created.st_dev, created.st_ino)
                while True:
                    check()
                    chunk = incoming.read(1024 * 1024)
                    if not chunk:
                        break
                    if outgoing.write(chunk) != len(chunk):
                        raise OSError("The new output was not written completely.")
                    copied += len(chunk)
                    digest.update(chunk)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            source_after = os.fstat(incoming.fileno())
            if source_identity != (source_after.st_dev, source_after.st_ino, source_after.st_size,
                                   source_after.st_mtime_ns, source_after.st_ctime_ns):
                raise OSError("The staged output changed while it was being published.")
            if copied != source_stat.st_size:
                raise OSError("The staged output was not copied completely.")
            check()
            verified = hashlib.sha256()
            with open(destination, "rb") as written:
                identity = os.fstat(written.fileno())
                if (identity.st_dev, identity.st_ino) != created_identity:
                    raise OSError("The destination changed while it was being published.")
                while True:
                    check()
                    chunk = written.read(1024 * 1024)
                    if not chunk:
                        break
                    verified.update(chunk)
                completed = os.fstat(written.fileno())
                if (_file_identity(completed) != _file_identity(identity)
                        or completed.st_ctime_ns != identity.st_ctime_ns):
                    raise OSError("The destination changed while it was being verified.")
                if published_callback is not None:
                    # Capture path-query ctime while the readback descriptor
                    # still holds the created inode. Unlike hard links (whose
                    # stages retain the inode), copies need this extra guard
                    # against a removed file's ID being recycled later.
                    published_stat = os.lstat(destination)
                    if (not stat.S_ISREG(published_stat.st_mode)
                            or _file_identity(published_stat) != _file_identity(completed)):
                        raise OSError("The destination changed while it was being verified.")
            if verified.digest() != digest.digest():
                raise OSError("The new output did not match the prepared data.")
            check()
        except BaseException as exc:
            # Do not unlink a different file or symlink that another process
            # placed at this path during cancellation or a failed write.
            try:
                _check_parent(destination, parent_identity)
                try:
                    current = os.lstat(destination)
                except FileNotFoundError:
                    _check_parent(destination, parent_identity)
                    current = None
                if current is not None and (created_identity is None or not created_identity[1]):
                    raise OSError("The partial output's ownership could not be verified.")
                if (current is not None and stat.S_ISREG(current.st_mode)
                        and (current.st_dev, current.st_ino) == created_identity):
                    os.unlink(destination)
            except OSError as cleanup_error:
                raise FilePublicationCleanupError(destination, exc, cleanup_error) from exc
            raise
        if published_callback is not None:
            published_callback(PublishedFile(
                os.fsdecode(destination), _file_identity(completed), digest.digest(),
                parent_identity,
                ctime_ns=published_stat.st_ctime_ns,
            ))
            check()


def atomic_write_bytes(dest_path, payload, *, validate=None, replace_existing=True,
                       published_callback=None, before_replace=None, allow_copy=True, mode=None):
    """Replace a destination only after its temporary output passes readback checks.

    Existing destination permissions and symlinks are preserved, and read-only
    destinations are rejected. ``validate`` receives the bytes read back from
    disk and may raise to reject the output.
    ``replace_existing=False`` publishes exclusively and never follows a
    destination symlink. Filesystems without hard links use exclusive creation
    and cleanup for new files. Backups remain the caller's responsibility.
    ``published_callback`` receives a ``PublishedFile`` receipt as soon as the
    output is published, even if later cleanup fails. ``before_replace``
    receives the resolved destination after staging and before publication. It
    is not an atomic ownership guard. Rollback must detach the existing entry
    there and publish exclusively with ``allow_copy=False``. ``mode`` applies
    permissions to the private staged file, before it becomes visible.
    """
    dest_path = (os.path.realpath if replace_existing else os.path.abspath)(os.fsdecode(dest_path))
    parent_identity = _parent_identity(dest_path) if published_callback is not None else None
    descriptor, temp_path = tempfile.mkstemp(
        prefix=".aps_write_", suffix=".tmp", dir=os.path.dirname(dest_path),
    )
    try:
        try:
            handle = os.fdopen(descriptor, "wb")
        except BaseException:
            os.close(descriptor)
            raise
        with handle:
            if handle.write(payload) != len(payload):
                raise OSError("The temporary output was not written completely.")
            handle.flush()
            os.fsync(handle.fileno())

        with open(temp_path, "rb") as handle:
            written = handle.read()
        if written != payload:
            raise OSError("The temporary output did not match the prepared data.")
        if validate is not None:
            validate(written)

        if replace_existing and os.path.exists(dest_path):
            # POSIX rename needs only directory access and could otherwise
            # overwrite a read-only file. Ask the OS for actual write access
            # without truncating, honoring ACLs and Windows read-only flags.
            write_descriptor = os.open(dest_path, os.O_WRONLY)
            os.close(write_descriptor)
            if mode is None:
                shutil.copymode(dest_path, temp_path)
        if mode is not None:
            os.chmod(temp_path, mode)

        receipt = None
        if published_callback is not None and (replace_existing or os.name == "nt"):
            # The staged file becomes the destination inode. Capture it before
            # publication; inspecting the destination afterwards could adopt a
            # different process's replacement as our output. Rename can change
            # ctime, so it is deliberately omitted from this receipt.
            receipt = PublishedFile(
                dest_path, _file_identity(os.stat(temp_path)),
                hashlib.sha256(written).digest(), parent_identity,
            )
            _check_parent(dest_path, parent_identity)
        if before_replace is not None:
            before_replace(dest_path)
        if replace_existing:
            os.replace(temp_path, dest_path)
            if published_callback is not None:
                published_callback(receipt)
        elif os.name == "nt":
            # Windows rename fails if the target already exists.
            os.rename(temp_path, dest_path)
            if published_callback is not None:
                published_callback(receipt)
        else:
            publish_new_file(temp_path, dest_path, published_callback=published_callback,
                             allow_copy=allow_copy)
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
