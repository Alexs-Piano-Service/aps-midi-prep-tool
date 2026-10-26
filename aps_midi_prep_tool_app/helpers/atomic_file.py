"""Write a complete, checked file before replacing its destination."""

import errno
import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass


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

    def remove_if_unchanged(self):
        """Remove this publication, or refuse if ownership cannot be verified.

        A missing file is already cleaned up. A replaced or edited file, a
        symlink, and a filesystem without usable file IDs are never removed.
        """
        _check_parent(self.destination, self.parent_identity)
        try:
            before = os.lstat(self.destination)
        except FileNotFoundError:
            # ENOENT also occurs when removable media disappears. Only a
            # missing name in the original directory proves cleanup succeeded.
            _check_parent(self.destination, self.parent_identity)
            return
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
        os.unlink(self.destination)


class FilePublicationCleanupError(OSError):
    """Publication failed and the new partial output could not be removed."""

    def __init__(self, destination, error, cleanup_error):
        self.retained_paths = (os.fsdecode(destination),)
        self.original_exception = error
        super().__init__(
            f"{error}\nThe partial output could not be removed: {destination}\n{cleanup_error}"
        )


def publish_new_file(staged_path, destination, *, check_cancel=None, published_callback=None):
    """Publish an already-verified file without replacing an existing path.

    A hard link publishes atomically where supported. FAT/exFAT and other
    filesystems without links use an exclusive streaming copy and readback
    verification. Failure removes only the partial file created by this call;
    the staged source always remains available to its caller. ``check_cancel``
    takes no arguments and may raise the caller's cancellation exception.
    ``published_callback`` receives a ``PublishedFile`` receipt before the
    final cancellation check, allowing a caller to roll back a larger set.
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


def atomic_write_bytes(dest_path, payload, *, validate=None, replace_existing=True):
    """Replace a destination only after its temporary output passes readback checks.

    Existing destination permissions and symlinks are preserved, and read-only
    destinations are rejected. ``validate`` receives the bytes read back from
    disk and may raise to reject the output.
    ``replace_existing=False`` publishes exclusively and never follows a
    destination symlink. Filesystems without hard links use exclusive creation
    and cleanup for new files. Backups remain the caller's responsibility.
    """
    dest_path = (os.path.realpath if replace_existing else os.path.abspath)(os.fsdecode(dest_path))
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
            shutil.copymode(dest_path, temp_path)
        if replace_existing:
            os.replace(temp_path, dest_path)
        elif os.name == "nt":
            # Windows rename fails if the target already exists.
            os.rename(temp_path, dest_path)
        else:
            try:
                os.link(temp_path, dest_path)
            except OSError as exc:
                if exc.errno not in {errno.EPERM, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOSYS}:
                    raise
                # FAT and some removable filesystems have no hard links. The
                # payload is already validated; exclusively create a new file.
                # An interrupted copy can affect only this newly created file.
                handle = open(dest_path, "xb")
                try:
                    with handle:
                        if handle.write(written) != len(written):
                            raise OSError("The new output was not written completely.")
                        handle.flush()
                        os.fsync(handle.fileno())
                    with open(dest_path, "rb") as handle:
                        if handle.read() != written:
                            raise OSError("The new output did not match the prepared data.")
                except BaseException:
                    os.unlink(dest_path)
                    raise
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
