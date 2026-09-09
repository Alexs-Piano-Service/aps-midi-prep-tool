"""Bulk extraction of files from every supported floppy image in a folder."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass

from .bulk_extraction_job import ExtractionJob, serialized_extraction
from .helpers.atomic_file import atomic_write_bytes

from .eseq_converter import convert_eseq_file_to_midi_path, is_eseq_file
from .eseq_pianodir import (
    is_eseq_directory_path,
    is_pianodir_path,
    read_pianodir_metadata_from_file,
)
from .floppy_image import (
    FloppyImageError,
    FloppyImageSession,
    FloppyOperationCancelled,
    is_supported_image_path,
)
from .long_midi_filename import build_long_midi_filename
from .message_catalog import tr
from .midi_metadata import (
    is_midi_file,
    normalize_title_spacing,
    read_eseq_title_from_file,
    read_first_title_from_midi,
)
from .smart_pianosoft import (
    smart_pianosoft_catalog_from_session,
    smart_pianosoft_disk_title_from_session,
)


_INVALID_FOLDER_CHARS = '<>:"/\\|?*'
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


@dataclass(frozen=True)
class BulkExtractionResult:
    source_directory: str
    output_directory: str
    images_found: int
    images_processed: int
    files_extracted: int
    files_converted: int
    included_eseq_sources: bool
    output_directories: tuple[str, ...]
    errors: tuple[str, ...]
    files_reused: int = 0
    images_skipped: int = 0
    job_record_path: str = ""


def discover_image_files(source_directory):
    """Return supported image files directly inside *source_directory*."""
    source_directory = os.path.abspath(os.fspath(source_directory))
    if not os.path.isdir(source_directory):
        raise FloppyImageError(f"The image folder was not found: {source_directory}")

    image_paths = []
    try:
        with os.scandir(source_directory) as entries:
            for entry in entries:
                try:
                    is_file = entry.is_file()
                except OSError:
                    is_file = False
                if is_file and is_supported_image_path(entry.name):
                    image_paths.append(os.path.abspath(entry.path))
    except OSError as exc:
        raise FloppyImageError(f"Could not read the image folder: {exc}") from exc

    return sorted(image_paths, key=lambda path: (os.path.basename(path).casefold(), path))


def sanitize_output_folder_name(name, fallback="image"):
    """Make an image/album name safe as a portable output folder name."""
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    cleaned = []
    for char in text:
        if ord(char) < 32 or char in _INVALID_FOLDER_CHARS:
            cleaned.append(" ")
        else:
            cleaned.append(char)
    text = re.sub(r"\s+", " ", "".join(cleaned)).strip(" .")
    if not text:
        text = str(fallback or "image").strip() or "image"
    if text.upper() in _WINDOWS_RESERVED_NAMES:
        text = f"{text} Image"
    return text[:150].rstrip(" .") or "image"


def _raise_if_cancelled(cancel_callback):
    if cancel_callback is not None and cancel_callback():
        raise FloppyOperationCancelled("Bulk extraction cancelled.")


def _safe_image_relative_parts(image_path):
    raw_path = str(image_path or "").replace("\\", "/")
    if raw_path.startswith("/") or re.match(r"^[A-Za-z]:", raw_path):
        raise FloppyImageError(f"Unsafe absolute path in image: {image_path}")
    parts = [part for part in raw_path.split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        raise FloppyImageError(f"Unsafe file path in image: {image_path}")
    return tuple(parts)


def _unique_output_directory(output_root, preferred_name, used_names):
    base_name = sanitize_output_folder_name(preferred_name)
    candidate_name = base_name
    suffix = 2
    while candidate_name.casefold() in used_names or os.path.lexists(
        os.path.join(output_root, candidate_name)
    ):
        candidate_name = f"{base_name}_{suffix}"
        suffix += 1
    used_names.add(candidate_name.casefold())
    return os.path.join(output_root, candidate_name)


def _album_name_from_image(session, entries):
    for entry in entries:
        if not is_pianodir_path(entry.path):
            continue
        try:
            metadata_path = session.extract_file(entry.path)
            metadata = read_pianodir_metadata_from_file(metadata_path)
            album_name = str(metadata.disk_title or "").strip()
            if album_name:
                return album_name
        except Exception:
            pass
    return smart_pianosoft_disk_title_from_session(session, entries)


def _relative_key(parts):
    return "/".join(parts).casefold()


def _available_midi_path(
    image_output_directory,
    source_parts,
    reserved_keys,
    *,
    preferred_filename="",
):
    source_filename = source_parts[-1]
    source_stem = os.path.splitext(source_filename)[0] or source_filename or "song"
    parent_parts = source_parts[:-1]
    preferred_filename = os.path.basename(str(preferred_filename or "").strip())
    if preferred_filename:
        preferred_stem = os.path.splitext(preferred_filename)[0] or source_stem
        candidates = [preferred_filename, f"{preferred_stem}_converted.mid"]
        collision_stem = preferred_stem
    else:
        candidates = [f"{source_stem}.mid", f"{source_stem}_converted.mid"]
        collision_stem = source_stem
    suffix = 2
    while True:
        for filename in candidates:
            parts = (*parent_parts, filename)
            key = _relative_key(parts)
            path = os.path.join(image_output_directory, *parts)
            if key not in reserved_keys and not os.path.lexists(path):
                reserved_keys.add(key)
                return path
        candidates = [f"{collision_stem}_converted_{suffix}.mid"]
        suffix += 1


@serialized_extraction
def bulk_extract_images(
    source_directory,
    output_directory,
    *,
    convert_eseq=False,
    include_eseq_sources=False,
    long_midi_filenames=False,
    trim_title_spaces=False,
    use_album_names=False,
    progress_callback=None,
    progress_detail_callback=None,
    cancel_callback=None,
    session_loader=None,
    eseq_detector=None,
    eseq_converter=None,
    eseq_title_reader=None,
    language_code=None,
    job_record_path=None,
    resume=False,
):
    """Extract every file from every supported image directly in a folder.

    Each image is written to its own uniquely named subfolder. If requested,
    detected E-SEQ files are replaced by MIDI conversions and Yamaha directory
    files are omitted from the output unless source inclusion is requested.
    """
    source_directory = os.path.abspath(os.fspath(source_directory))
    output_directory = os.path.abspath(os.fspath(output_directory))
    image_paths = discover_image_files(source_directory)
    total_images = len(image_paths)

    if not image_paths:
        if resume:
            raise ValueError("No source images remain for this saved extraction job.")
        return BulkExtractionResult(
            source_directory=source_directory,
            output_directory=output_directory,
            images_found=0,
            images_processed=0,
            files_extracted=0,
            files_converted=0,
            included_eseq_sources=bool(convert_eseq and include_eseq_sources),
            output_directories=(),
            errors=(),
        )

    try:
        os.makedirs(output_directory, exist_ok=True)
    except OSError as exc:
        raise FloppyImageError(f"Could not create the output folder: {exc}") from exc
    if not os.path.isdir(output_directory):
        raise FloppyImageError(f"The output path is not a folder: {output_directory}")

    job = None
    if job_record_path is not None:
        job = ExtractionJob(job_record_path, source_directory, output_directory, {
            "convert_eseq": bool(convert_eseq),
            "include_eseq_sources": bool(include_eseq_sources),
            "long_midi_filenames": bool(long_midi_filenames),
            "trim_title_spaces": bool(trim_title_spaces),
            "use_album_names": bool(use_album_names),
        }, resume=resume, check_cancelled=lambda: _raise_if_cancelled(cancel_callback))
        job.prepare_images(image_paths)
        if resume:
            # Retry previously interrupted/failed images before untouched images.
            priority = {"running": 0, "failed": 0, "pending": 1, "complete": 2}
            image_paths.sort(key=lambda path: priority.get(job.image(os.path.basename(path)).get("state"), 1))
    elif resume:
        raise ValueError("Choose a saved extraction job to resume.")

    session_loader = session_loader or FloppyImageSession.load
    eseq_detector = eseq_detector or is_eseq_file
    eseq_converter = eseq_converter or convert_eseq_file_to_midi_path
    eseq_title_reader = eseq_title_reader or read_eseq_title_from_file
    errors = []
    output_directories = []
    used_folder_names = set()
    images_processed = 0
    files_extracted = 0
    files_converted = 0
    files_reused = 0
    images_skipped = 0

    def notify(
        step,
        message_id,
        *,
        stage="",
        image_index=0,
        file_completed=0,
        file_total=0,
        **kwargs,
    ):
        message = tr(message_id, language_code, **kwargs)
        if progress_callback is not None:
            progress_callback(step, total_images, message)
        if progress_detail_callback is not None:
            progress_detail_callback(
                {
                    "stage": stage,
                    "message": message,
                    "overall_completed": int(step or 0),
                    "image_index": int(image_index or 0),
                    "image_total": total_images,
                    "image_name": str(kwargs.get("image") or ""),
                    "file_completed": int(file_completed or 0),
                    "file_total": int(file_total or 0),
                }
            )

    notify(0, "bulk.progress.scan", stage="scan")

    for image_index, image_path in enumerate(image_paths):
        _raise_if_cancelled(cancel_callback)
        image_name = os.path.basename(image_path)
        if job is not None:
            image_record = job.image(image_name)
            if image_record.get("state") == "complete" and all(
                job.entry_complete(image_name, name) for name in image_record["entries"]
            ):
                job.verify_image_unchanged(image_path)
                files_reused += sum(len(entry.get("outputs", [])) for entry in image_record["entries"].values())
                images_processed += 1
                images_skipped += 1
                if image_record.get("output_directory"):
                    output_directories.append(job.safe_output_path(os.path.join(output_directory, image_record["output_directory"])))
                notify(image_index + 1, "bulk.progress.finished", stage="finished", image_index=image_index + 1, image=image_name)
                continue
            image_record["state"] = "running"
            job.save()
        notify(
            image_index,
            "bulk.progress.opening",
            stage="opening",
            image_index=image_index + 1,
            image=image_name,
        )
        session = None
        entry_total = 0
        image_error_start = len(errors)
        try:
            session = session_loader(
                image_path,
                cancel_callback=cancel_callback,
            )
            _raise_if_cancelled(cancel_callback)
            entries = list(session.list_entries().entries)
            entry_total = len(entries)
            if job is not None:
                image_record["entry_count"] = entry_total
            smart_pianosoft_song_catalog = smart_pianosoft_catalog_from_session(
                session,
                entries,
            )
            for warning in getattr(session, "conversion_warnings", ()) or ():
                errors.append(f"{image_name}: {warning}")
            preferred_name = os.path.splitext(image_name)[0] or image_name
            if use_album_names:
                preferred_name = _album_name_from_image(session, entries) or preferred_name
            if job is not None and image_record.get("output_directory"):
                image_output_directory = job.safe_output_path(os.path.join(output_directory, image_record["output_directory"]))
                os.makedirs(image_output_directory, exist_ok=True)
            else:
                image_output_directory = _unique_output_directory(
                    output_directory, preferred_name, used_folder_names,
                )
                os.makedirs(image_output_directory)
                if job is not None:
                    image_record["output_directory"] = os.path.relpath(image_output_directory, output_directory)
                    job.save()
            output_directories.append(image_output_directory)

            entry_parts = {}
            reserved_keys = set()
            for entry in entries:
                try:
                    parts = _safe_image_relative_parts(entry.path)
                except Exception as exc:
                    errors.append(f"{image_name} / {entry.path}: {exc}")
                    continue
                entry_parts[id(entry)] = parts
                reserved_keys.add(_relative_key(parts))

            converted_song_number = 0
            extracted_midi_number = 0
            for file_index, entry in enumerate(entries, start=1):
                _raise_if_cancelled(cancel_callback)
                parts = entry_parts.get(id(entry))
                if parts is None:
                    continue
                if job is not None:
                    if job.entry_complete(image_name, entry.path):
                        saved = image_record["entries"][entry.path]
                        converted_song_number += bool(saved.get("converted"))
                        extracted_midi_number += bool(saved.get("renamed_midi"))
                        files_reused += len(saved.get("outputs", []))
                        continue
                    image_record["entries"][entry.path] = {"state": "failed", "outputs": []}
                    job.save()
                notify(
                    image_index,
                    "bulk.progress.extracting",
                    stage="extracting",
                    image_index=image_index + 1,
                    file_completed=file_index - 1,
                    file_total=entry_total,
                    image=image_name,
                    current=file_index,
                    total=entry_total,
                    path=entry.path,
                )
                destination_path = os.path.join(image_output_directory, *parts)
                if job is not None:
                    job.safe_output_path(destination_path)
                try:
                    extracted_path = session.extract_file(entry.path)
                except Exception as exc:
                    errors.append(f"{image_name} / {entry.path}: {exc}")
                    continue

                eseq_directory_entry = is_eseq_directory_path(entry.path)
                if convert_eseq and eseq_directory_entry and not include_eseq_sources:
                    if job is not None:
                        job.finish_entry(image_name, entry.path, [])
                    continue

                try:
                    convert_entry = bool(
                        convert_eseq
                        and not eseq_directory_entry
                        and eseq_detector(extracted_path)
                    )
                except Exception as exc:
                    errors.append(f"{image_name} / {entry.path} (E-SEQ detection): {exc}")
                    continue

                written_paths = []
                converted_paths = set()
                rename_midi_entry = False
                if convert_entry:
                    converted_song_number += 1
                    source_title = ""
                    title_read_successfully = False
                    if long_midi_filenames or trim_title_spaces:
                        try:
                            source_title = str(eseq_title_reader(extracted_path) or "")
                            title_read_successfully = True
                        except Exception as exc:
                            errors.append(
                                f"{image_name} / {entry.path} (E-SEQ title): {exc}"
                            )
                    conversion_title = (
                        normalize_title_spacing(source_title)
                        if trim_title_spaces
                        else source_title
                    )
                    preferred_filename = ""
                    if long_midi_filenames:
                        preferred_filename = build_long_midi_filename(
                            converted_song_number,
                            conversion_title,
                            parts[-1],
                        )
                    midi_path = _available_midi_path(
                        image_output_directory,
                        parts,
                        reserved_keys,
                        preferred_filename=preferred_filename,
                    )
                    notify(
                        image_index,
                        "bulk.progress.converting",
                        stage="converting",
                        image_index=image_index + 1,
                        file_completed=file_index - 1,
                        file_total=entry_total,
                        image=image_name,
                        path=entry.path,
                    )
                    try:
                        os.makedirs(os.path.dirname(midi_path), exist_ok=True)
                        with tempfile.TemporaryDirectory(prefix=".aps_convert_", dir=os.path.dirname(midi_path)) as conversion_dir:
                            staged_midi_path = os.path.join(conversion_dir, os.path.basename(midi_path))
                            if trim_title_spaces and title_read_successfully:
                                eseq_converter(extracted_path, staged_midi_path, title_override=conversion_title)
                            else:
                                eseq_converter(extracted_path, staged_midi_path)
                            with open(staged_midi_path, "rb") as handle:
                                atomic_write_bytes(midi_path, handle.read(), replace_existing=False)
                        files_converted += 1
                        written_paths.append(midi_path)
                        converted_paths.add(midi_path)
                    except Exception as exc:
                        errors.append(f"{image_name} / {entry.path} (E-SEQ conversion): {exc}")
                        continue

                if not convert_entry and long_midi_filenames:
                    try:
                        rename_midi_entry = is_midi_file(extracted_path)
                    except Exception:
                        rename_midi_entry = False
                    if rename_midi_entry:
                        extracted_midi_number += 1
                        catalog_song = smart_pianosoft_song_catalog.get(
                            os.path.basename(str(entry.path or "")).casefold()
                        )
                        if catalog_song is not None:
                            track_number = catalog_song.track_number
                            midi_title = catalog_song.title
                        else:
                            track_number = extracted_midi_number
                            try:
                                midi_title = read_first_title_from_midi(extracted_path)
                            except Exception:
                                midi_title = ""
                        long_filename = build_long_midi_filename(
                            track_number,
                            normalize_title_spacing(midi_title),
                            parts[-1],
                        )
                        reserved_keys.discard(_relative_key(parts))
                        destination_path = _available_midi_path(
                            image_output_directory,
                            parts,
                            reserved_keys,
                            preferred_filename=long_filename,
                        )

                if not convert_entry or include_eseq_sources:
                    try:
                        os.makedirs(os.path.dirname(destination_path), exist_ok=True)
                        if os.path.lexists(destination_path):
                            stem, extension = os.path.splitext(destination_path)
                            suffix = 2
                            while os.path.lexists(f"{stem}_{suffix}{extension}"):
                                suffix += 1
                            destination_path = f"{stem}_{suffix}{extension}"
                        if job is not None:
                            job.safe_output_path(destination_path)
                        with open(extracted_path, "rb") as handle:
                            atomic_write_bytes(destination_path, handle.read(), replace_existing=False)
                        files_extracted += 1
                        written_paths.append(destination_path)
                    except Exception as exc:
                        errors.append(f"{image_name} / {entry.path}: {exc}")
                        continue

                if entry.modified_time is not None:
                    for written_path in written_paths:
                        try:
                            os.utime(
                                written_path,
                                (float(entry.modified_time), float(entry.modified_time)),
                            )
                        except (OSError, TypeError, ValueError):
                            pass

                if job is not None:
                    try:
                        job.finish_entry(
                            image_name, entry.path,
                            [(path, path in converted_paths) for path in written_paths],
                            converted=convert_entry, renamed_midi=rename_midi_entry,
                        )
                    except (OSError, ValueError) as exc:
                        errors.append(f"{image_name} / {entry.path} (output verification): {exc}")

            images_processed += 1
            if job is not None:
                job.verify_image_unchanged(image_path)
                image_record["state"] = "complete" if len(errors) == image_error_start and all(
                    entry.path in image_record["entries"]
                    and image_record["entries"][entry.path].get("state") in {"complete", "skipped"}
                    for entry in entries
                ) else "failed"
                job.save()
        except FloppyOperationCancelled:
            raise
        except Exception as exc:
            errors.append(f"{image_name}: {exc}")
            if job is not None:
                image_record["state"] = "failed"
                job.save()
        finally:
            if session is not None:
                try:
                    session.cleanup()
                except Exception:
                    pass
        notify(
            image_index + 1,
            "bulk.progress.finished",
            stage="finished",
            image_index=image_index + 1,
            file_completed=entry_total,
            file_total=entry_total,
            image=image_name,
        )

    return BulkExtractionResult(
        source_directory=source_directory,
        output_directory=output_directory,
        images_found=total_images,
        images_processed=images_processed,
        files_extracted=files_extracted,
        files_converted=files_converted,
        included_eseq_sources=bool(convert_eseq and include_eseq_sources),
        output_directories=tuple(output_directories),
        errors=tuple(errors),
        files_reused=files_reused,
        images_skipped=images_skipped,
        job_record_path=job.path if job is not None else "",
    )
