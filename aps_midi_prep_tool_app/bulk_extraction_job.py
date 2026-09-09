"""Local extraction checkpoints with verified inputs and output ownership."""

import hashlib
import json
import os
import re
from contextlib import contextmanager
from functools import wraps
from string import Formatter

from .bulk_job_translations import BULK_JOB_TRANSLATIONS
from .conversion_review import inspect_music_bytes
from .helpers.atomic_file import atomic_write_bytes
from .message_catalog import translate_text


def localize_extraction_job_error(error, language_code=None):
    """Render canonical checkpoint diagnostics without translating paths or saved jobs."""
    if isinstance(error, json.JSONDecodeError):
        return translate_text(
            "The extraction job is not valid JSON (line {line}, column {column}).",
            language_code, line=error.lineno, column=error.colno,
        )
    message = str(error)
    for template in BULK_JOB_TRANSLATIONS:
        fields = []
        pattern = ""
        for literal, field, _format, _conversion in Formatter().parse(template):
            pattern += re.escape(literal)
            if field:
                fields.append(field)
                pattern += "(.+?)"
        match = re.fullmatch(r"(.*?: )?" + pattern, message, re.DOTALL)
        if match:
            return (match[1] or "") + translate_text(
                template, language_code, **dict(zip(fields, match.groups()[1:]))
            )
    return message


@contextmanager
def _job_lock(path):
    """Hold an OS lock, which is released automatically if the process exits."""
    lock_path = os.path.abspath(os.fspath(path)) + ".lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError("This extraction job is already running in another process.") from exc
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError("This extraction job is already running in another process.") from exc
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def serialized_extraction(function):
    @wraps(function)
    def run(*args, **kwargs):
        path = kwargs.get("job_record_path")
        if path is None:
            return function(*args, **kwargs)
        with _job_lock(path):
            return function(*args, **kwargs)
    return run


def file_digest(path, check_cancelled=None):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if check_cancelled is not None:
                check_cancelled()
            digest.update(chunk)
    return digest.hexdigest()


def read_extraction_job(path):
    with open(path, "r", encoding="utf-8") as handle:
        job = json.load(handle)
    if not isinstance(job, dict) or job.get("version") != 1 or job.get("kind") != "aps-bulk-extraction":
        raise ValueError("This is not a supported APS extraction job.")
    if not all(isinstance(job.get(key), kind) for key, kind in (
        ("source_directory", str), ("output_directory", str), ("options", dict), ("images", dict),
    )):
        raise ValueError("The extraction job is incomplete or malformed.")
    required_options = {"convert_eseq", "include_eseq_sources", "long_midi_filenames", "trim_title_spaces", "use_album_names"}
    if set(job["options"]) != required_options or any(type(value) is not bool for value in job["options"].values()):
        raise ValueError("The extraction job has malformed options.")
    owned_paths = set()
    owned_directories = set()
    for name, image in job["images"].items():
        if not _safe_relative_path(name) or "/" in name.replace("\\", "/") or not isinstance(image, dict):
            raise ValueError("The extraction job has a malformed image record.")
        if not _valid_digest(image.get("sha256")) or image.get("state") not in {"pending", "running", "failed", "complete"} or not isinstance(image.get("entries"), dict):
            raise ValueError(f"The extraction job has an incomplete image record: {name}")
        directory = image.get("output_directory")
        if directory is not None and not _safe_relative_path(directory):
            raise ValueError(f"The extraction job has an unsafe image output folder: {name}")
        directory_key = directory.replace("\\", "/").casefold() if directory is not None else ""
        if directory_key:
            if directory_key in owned_directories:
                raise ValueError(f"More than one extraction image claims the output folder {directory}")
            owned_directories.add(directory_key)
        if image["state"] == "complete" and (
            not directory or type(image.get("entry_count")) is not int or image["entry_count"] != len(image["entries"])
        ):
            raise ValueError(f"The extraction job is missing completed image entries: {name}")
        for entry_name, entry in image["entries"].items():
            if not _safe_relative_path(entry_name) or not isinstance(entry, dict) or entry.get("state") not in {"failed", "complete", "skipped"}:
                raise ValueError(f"The extraction job has a malformed song record: {name}")
            outputs = entry.get("outputs")
            if not isinstance(outputs, list) or (entry["state"] == "complete" and not outputs):
                raise ValueError(f"The extraction job is missing verified outputs for {entry_name}")
            if entry["state"] == "skipped" and (outputs or not _valid_skipped_entry(job, entry_name, entry)):
                raise ValueError(f"The extraction job has an invalid skipped entry: {entry_name}")
            for output in outputs:
                if not isinstance(output, dict) or not _safe_relative_path(output.get("path")) or not _valid_digest(output.get("sha256")) or type(output.get("converted")) is not bool:
                    raise ValueError(f"The extraction job has a malformed output record for {entry_name}")
                output_key = output["path"].replace("\\", "/").casefold()
                if not directory_key or not output_key.startswith(directory_key + "/"):
                    raise ValueError(f"An output is outside its recorded image folder: {output['path']}")
                if output_key in owned_paths:
                    raise ValueError(f"More than one extraction entry claims the output {output['path']}")
                owned_paths.add(output_key)
    return job


def _safe_relative_path(path):
    return (
        isinstance(path, str) and bool(path) and "\0" not in path
        and not path.startswith(("/", "\\")) and not re.match(r"^[A-Za-z]:", path)
        and all(part not in {"", ".", ".."} for part in path.replace("\\", "/").split("/"))
    )


def _valid_digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _valid_skipped_entry(job, name, entry):
    return (
        entry.get("reason") == "eseq_directory"
        and name.replace("\\", "/").split("/")[-1].upper() in {"PIANODIR.FIL", "MUSIC.DIR"}
        and job["options"]["convert_eseq"] and not job["options"]["include_eseq_sources"]
    )


class ExtractionJob:
    def __init__(self, path, source_directory, output_directory, options, *, resume=False, check_cancelled=None):
        self.path = os.path.abspath(os.fspath(path))
        if os.path.islink(self.path):
            raise ValueError("An extraction job cannot be written through a symbolic link.")
        self.root = os.path.realpath(output_directory)
        self.check_cancelled = check_cancelled
        if resume:
            self.data = read_extraction_job(self.path)
            if (
                self.data["source_directory"] != source_directory
                or self.data["output_directory"] != output_directory
                or self.data["options"] != options
            ):
                raise ValueError("The saved job uses different source, destination, or extraction options.")
        else:
            if os.path.lexists(self.path):
                raise ValueError("The extraction job already exists. Resume it or choose a new job path.")
            self.data = {
                "kind": "aps-bulk-extraction", "version": 1,
                "source_directory": source_directory, "output_directory": output_directory,
                "options": options, "images": {},
            }

    def save(self):
        atomic_write_bytes(self.path, (json.dumps(self.data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))

    def prepare_images(self, paths):
        names = {os.path.basename(path) for path in paths}
        if set(self.data["images"]) - names:
            raise ValueError("An image recorded in this job is missing from the source folder.")
        for path in paths:
            name = os.path.basename(path)
            if self.check_cancelled is not None:
                self.check_cancelled()
            digest = file_digest(path, self.check_cancelled)
            previous = self.data["images"].get(name)
            if previous is not None and previous.get("sha256") != digest:
                raise ValueError(f"Source image changed since this job was saved: {name}")
            if previous is None:
                self.data["images"][name] = {"sha256": digest, "state": "pending", "entries": {}}
        self.save()

    def safe_output_path(self, path):
        absolute = os.path.abspath(path)
        if os.path.commonpath((self.root, absolute)) != self.root or absolute != os.path.realpath(absolute):
            raise ValueError(f"Unsafe output path in extraction job: {path}")
        return absolute

    def image(self, name):
        return self.data["images"][name]

    def verify_image_unchanged(self, path):
        if file_digest(path, self.check_cancelled) != self.image(os.path.basename(path))["sha256"]:
            raise ValueError(f"Source image changed while extracting: {os.path.basename(path)}")

    def entry_complete(self, image_name, entry_name):
        entry = self.image(image_name)["entries"].get(entry_name, {})
        if entry.get("state") == "skipped":
            return _valid_skipped_entry(self.data, entry_name, entry)
        if entry.get("state") != "complete":
            return False
        if not entry.get("outputs"):
            return False
        for output in entry.get("outputs", []):
            try:
                path = self.safe_output_path(os.path.join(self.root, output["path"]))
                if file_digest(path, self.check_cancelled) != output["sha256"]:
                    return False
                if output.get("converted"):
                    with open(path, "rb") as handle:
                        inspect_music_bytes(handle.read())
            except (OSError, ValueError, KeyError):
                return False
        return True

    def finish_entry(self, image_name, entry_name, paths, *, converted=False, renamed_midi=False):
        outputs = []
        for path, is_converted in paths:
            path = self.safe_output_path(path)
            if is_converted:
                with open(path, "rb") as handle:
                    inspect_music_bytes(handle.read())
            outputs.append({
                "path": os.path.relpath(path, self.root), "sha256": file_digest(path, self.check_cancelled),
                "converted": is_converted,
            })
        if not outputs and not _valid_skipped_entry(self.data, entry_name, {"reason": "eseq_directory"}):
            raise ValueError(f"No output was verified for {entry_name}")
        self.image(image_name)["entries"][entry_name] = {
            "state": "complete" if outputs else "skipped", "outputs": outputs,
            "reason": "" if outputs else "eseq_directory",
            "converted": converted, "renamed_midi": renamed_midi,
        }
        self.save()
