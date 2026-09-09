"""Write a complete, checked file before replacing its destination."""

import errno
import os
import shutil
import tempfile


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
