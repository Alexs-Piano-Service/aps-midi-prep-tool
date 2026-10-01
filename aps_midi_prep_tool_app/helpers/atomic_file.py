"""Write a complete, checked file before replacing its destination."""

import errno
import hashlib
import os
import shutil
import tempfile


def publish_new_file(staged_path, destination, *, check_cancel=None):
    """Publish an already-verified file without replacing an existing path.

    A hard link publishes atomically where supported. FAT/exFAT and other
    filesystems without links use an exclusive streaming copy and readback
    verification. Failure removes only the partial file created by this call;
    the staged source always remains available to its caller. ``check_cancel``
    takes no arguments and may raise the caller's cancellation exception.
    """
    def check():
        if check_cancel is not None:
            check_cancel()

    check()
    try:
        os.link(staged_path, destination)
        return
    except OSError as exc:
        unsupported = {errno.EPERM, errno.EOPNOTSUPP, errno.EXDEV, errno.ENOSYS}
        if os.name == "nt":
            unsupported.add(errno.EINVAL)
        if exc.errno not in unsupported:
            raise

    check()
    with open(staged_path, "rb") as incoming:
        source_stat = os.fstat(incoming.fileno())
        source_identity = (source_stat.st_dev, source_stat.st_ino, source_stat.st_size,
                           source_stat.st_mtime_ns, source_stat.st_ctime_ns)
        # Creation is deliberately outside cleanup: if another process won the
        # destination name, xb must fail without removing that process's file.
        outgoing = open(destination, "xb")
        created = os.fstat(outgoing.fileno())
        created_identity = (created.st_dev, created.st_ino)
        try:
            digest = hashlib.sha256()
            copied = 0
            with outgoing:
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
            if verified.digest() != digest.digest():
                raise OSError("The new output did not match the prepared data.")
            check()
        except BaseException:
            # Do not unlink a different file or symlink that another process
            # placed at this path during cancellation or a failed write.
            try:
                current = os.lstat(destination)
                if (current.st_dev, current.st_ino) == created_identity:
                    os.unlink(destination)
            except FileNotFoundError:
                pass
            raise


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
