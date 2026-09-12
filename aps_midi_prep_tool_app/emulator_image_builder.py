"""Build emulator-ready MIDI or Yamaha E-SEQ floppy-image sets."""

from __future__ import annotations

import csv
import hashlib
import os
import posixpath
import random
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, replace

from .dos83_renamer import build_dos83_filename
from .conversion_review import build_conversion_report
from .eseq_converter import (
    ESEQ_TITLE_LENGTH,
    convert_eseq_file_to_midi_path,
    convert_midi_file_to_eseq_path,
    is_eseq_file,
)
from .eseq_pianodir import (
    PIANODIR_FILENAME,
    PIANODIR_HEADER,
    PIANODIR_COUNT_OFFSET,
    PIANODIR_TRACK_SIZE,
    PIANODIR_MAX_TRACKS,
    PianodirMetadata,
    PianodirTrackEntry,
    build_eseq_order_key_from_path,
    build_pianodir_bytes,
    is_clavinova_mda_file,
    update_eseq_order_key_to_path,
)
from .floppy_image import (
    DISK_FORMATS,
    FloppyImageError,
    FloppyOperationCancelled,
    _copy_host_file_into_image,
    _delete_eseq_directory_entries_from_image,
    _finish_temp_output,
    _geometry_from_boot_sector,
    _is_image_capacity_error,
    _read_fat12_file_bytes,
    _write_image_direct,
    allocated_size,
    create_blank_floppy_image,
    read_image_listing,
    verify_image_payloads,
)
from .midi_metadata import (
    MidiTitleFormatError,
    extract_eseq_title_from_file,
    extract_first_title_from_midi,
    extract_midi_type_label_from_midi,
    is_midi_file,
    update_eseq_title_to_path,
    write_midi_title_to_path,
)
from .midi_type0_converter import convert_midi_file_to_type0_path
from .message_catalog import tr
from .long_midi_filename import build_long_midi_filename
from .smart_pianosoft import (
    SMART_PIANOSOFT_DISK_CATALOG_NAME,
    SMART_PIANOSOFT_SONG_CATALOG_NAME,
    SmartPianoSoftMetadata,
    build_smart_pianosoft_song_catalog,
    build_smart_pianosoft_song_record,
    parse_smart_pianosoft_song_catalog,
    smart_pianosoft_metadata_from_directory,
    update_smart_pianosoft_disk_title,
)


EMULATOR_IMAGE_EXTENSIONS = {"img", "hfe"}
EMULATOR_CONTENT_FORMATS = {"eseq", "midi"}
EMULATOR_DISK_LAYOUTS = {"fill", "folders"}
DEFAULT_IMAGE_PREFIX = "DSKA"
DEFAULT_STARTING_NUMBER = 0
DEFAULT_SAFETY_MARGIN_BYTES = 32 * 1024
MAX_IMAGE_NUMBER = 9999
_INVALID_PORTABLE_FILENAME_CHARS = '<>:"/\\|?*'
_NATURAL_NUMBER_RE = re.compile(r"(\d+)")
_BAD_SECTOR_FILLER = b"-=[BAD SECTOR]=-" * 2


@dataclass(frozen=True)
class EmulatorImageBuildResult:
    source_directory: str
    output_directory: str
    song_files_found: int
    files_prepared: int
    converted_files: int
    images_created: int
    output_content: str
    output_paths: tuple[str, ...]
    image_prefix: str
    starting_number: int
    safety_margin_bytes: int
    shuffled: bool
    song_list_path: str = ""
    disk_layout: str = "fill"
    warnings: tuple[str, ...] = ()
    contents_verified: bool = False

    @property
    def midi_files_found(self):
        """Compatibility alias for callers of the original E-SEQ-only builder."""
        return self.song_files_found


@dataclass(frozen=True)
class _PreparedSong:
    source_path: str
    image_path: str
    local_path: str
    title: str
    album_title: str = ""
    smart_pianosoft: SmartPianoSoftMetadata = SmartPianoSoftMetadata()
    catalog_record: bytes = b""
    warning: str = ""
    title_source: str = "Embedded title"
    conversion_report: object = None


@dataclass(frozen=True)
class EmulatorDiskPreview:
    output_path: str
    free_bytes: int
    songs: tuple[_PreparedSong, ...]


@dataclass(frozen=True)
class EmulatorAlbumPreview:
    source_directory: str
    title: str
    song_count: int
    included: bool
    title_source: str


@dataclass(frozen=True)
class EmulatorBuildPreview:
    source_directory: str
    output_directory: str
    disks: tuple[EmulatorDiskPreview, ...]
    albums: tuple[EmulatorAlbumPreview, ...]
    warnings: tuple[str, ...]
    album_titles: dict[str, str]
    title_overrides: dict[str, str]
    output_content: str
    disk_layout: str


def _natural_sort_key(path):
    relative = os.fspath(path).replace("\\", "/")
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in _NATURAL_NUMBER_RE.split(relative)
    )


def _display_relative_path(path, start):
    """Normalize separators for reports only, without changing filesystem paths."""
    return os.path.relpath(path, start).replace("\\", "/")


def discover_song_files(source_directory, *, include_subfolders=True):
    """Identify songs with normal extensions or no extension, checking content."""
    source_directory = os.path.abspath(os.fspath(source_directory))
    if not os.path.isdir(source_directory):
        raise FloppyImageError(f"The MIDI folder was not found: {source_directory}")

    paths = []

    def add_song(path):
        filename = os.path.basename(path)
        if filename.startswith(".") or filename.endswith("~"):
            return
        if os.path.splitext(filename)[1].lower() not in {"", ".mid", ".midi", ".fil", ".mda"}:
            return
        if filename.upper() in {
            PIANODIR_FILENAME,
            "MUSIC.DIR",
            SMART_PIANOSOFT_DISK_CATALOG_NAME,
            SMART_PIANOSOFT_SONG_CATALOG_NAME,
        }:
            return
        if os.path.isfile(path) and (is_midi_file(path) or is_eseq_file(path)):
            paths.append(os.path.abspath(path))

    try:
        if include_subfolders:
            for root, directory_names, filenames in os.walk(source_directory):
                directory_names[:] = [name for name in directory_names if not name.startswith(".")]
                directory_names.sort(key=_natural_sort_key)
                for filename in filenames:
                    add_song(os.path.join(root, filename))
        else:
            with os.scandir(source_directory) as entries:
                for entry in entries:
                    add_song(entry.path)
    except OSError as exc:
        raise FloppyImageError(f"Could not read the MIDI folder: {exc}") from exc

    return sorted(
        paths,
        key=lambda path: (
            os.path.relpath(path, source_directory).count(os.sep),
            _natural_sort_key(os.path.relpath(path, source_directory)),
        ),
    )


def discover_midi_files(source_directory, *, include_subfolders=True):
    """Return only MIDI files; retained for API compatibility."""
    return [
        path
        for path in discover_song_files(
            source_directory,
            include_subfolders=include_subfolders,
        )
        if is_midi_file(path)
    ]


def sanitize_image_set_name(name, fallback="Emulator Disks"):
    """Return a portable filename stem for the generated image set."""
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    cleaned = [
        " " if ord(char) < 32 or char in _INVALID_PORTABLE_FILENAME_CHARS else char
        for char in text
    ]
    text = re.sub(r"\s+", " ", "".join(cleaned)).strip(" .")
    return (text or fallback)[:120].rstrip(" .") or fallback


def sanitize_image_prefix(prefix, fallback=DEFAULT_IMAGE_PREFIX):
    """Return a Nalbantov-style one-to-four-character image prefix."""
    cleaned = "".join(
        char
        for char in str(prefix or "").upper()
        if "A" <= char <= "Z" or "0" <= char <= "9"
    )[:4]
    if cleaned:
        return cleaned
    fallback_cleaned = "".join(
        char
        for char in str(fallback or DEFAULT_IMAGE_PREFIX).upper()
        if "A" <= char <= "Z" or "0" <= char <= "9"
    )[:4]
    return fallback_cleaned or DEFAULT_IMAGE_PREFIX


def _numbered_image_stem(image_prefix, image_number):
    image_number = int(image_number)
    if not 0 <= image_number <= MAX_IMAGE_NUMBER:
        raise FloppyImageError(
            f"Disk number must be between 0 and {MAX_IMAGE_NUMBER}."
        )
    return f"{sanitize_image_prefix(image_prefix)}{image_number:04d}"


def _volume_label(image_prefix, image_number):
    return _numbered_image_stem(image_prefix, image_number)[:11]


def _raise_if_cancelled(cancel_callback):
    if cancel_callback is not None and cancel_callback():
        raise FloppyOperationCancelled("Emulator image creation cancelled.")


def _notify(progress_callback, step, total, message):
    if progress_callback is not None:
        progress_callback(int(step or 0), max(1, int(total or 1)), str(message or ""))


def _normalize_index_path(value):
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    text = re.sub(r"/+", "/", text)
    normalized = posixpath.normpath(text)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return "" if normalized == "." else normalized.casefold()


def _read_index_rows(index_path):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with open(index_path, "r", encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError:
            continue
        except (OSError, csv.Error):
            return []
    return []


def _set_unique_title(mapping, key, title):
    if not key:
        return
    if key not in mapping:
        mapping[key] = title
    elif mapping[key] != title:
        mapping[key] = None


def _sha256_file(path, cancel_callback=None):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            _raise_if_cancelled(cancel_callback)
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _load_index_title_overrides(
    source_directory,
    song_paths,
    *,
    cancel_callback=None,
):
    """Match nonblank INDEX.csv titles to songs without guessing ambiguously."""
    try:
        index_candidates = [
            entry.path
            for entry in os.scandir(source_directory)
            if entry.is_file() and entry.name.casefold() == "index.csv"
        ]
    except OSError:
        return {}
    if not index_candidates:
        return {}

    index_candidates.sort(
        key=lambda path: (
            os.path.basename(path) != "INDEX.csv",
            os.path.basename(path).casefold(),
        )
    )
    rows = _read_index_rows(index_candidates[0])
    exact_titles = {}
    basename_titles = {}
    hash_titles = {}

    for raw_row in rows:
        row = {
            str(key or "").strip().casefold(): str(value or "")
            for key, value in (raw_row or {}).items()
            if key is not None
        }
        title = row.get("title", "").replace("\x00", " ").strip()
        if not title:
            continue

        references = []
        output_file = row.get("output_file", "").strip()
        output_folder = row.get("output_folder", "").strip()
        if output_file:
            references.append(output_file)
            if output_folder and _normalize_index_path(output_folder):
                references.append(posixpath.join(output_folder, output_file))
        for column in ("source_path", "path", "filename", "file"):
            if row.get(column, "").strip():
                references.append(row[column])

        for reference in references:
            key = _normalize_index_path(reference)
            _set_unique_title(exact_titles, key, title)
            _set_unique_title(
                basename_titles,
                posixpath.basename(key),
                title,
            )

        sha256 = row.get("sha256", "").strip().casefold()
        if re.fullmatch(r"[0-9a-f]{64}", sha256):
            _set_unique_title(hash_titles, sha256, title)

    basename_counts = {}
    for song_path in song_paths:
        basename = os.path.basename(song_path).casefold()
        basename_counts[basename] = basename_counts.get(basename, 0) + 1

    overrides = {}
    unmatched = []
    for song_path in song_paths:
        _raise_if_cancelled(cancel_callback)
        relative_key = _normalize_index_path(
            os.path.relpath(song_path, source_directory)
        )
        absolute_key = _normalize_index_path(os.path.abspath(song_path))
        title = exact_titles.get(relative_key)
        if title is None:
            title = exact_titles.get(absolute_key)
        if title is None:
            basename = os.path.basename(song_path).casefold()
            if basename_counts.get(basename) == 1:
                title = basename_titles.get(basename)
        if title:
            overrides[song_path] = title
        elif hash_titles:
            unmatched.append(song_path)

    for song_path in unmatched:
        _raise_if_cancelled(cancel_callback)
        try:
            title = hash_titles.get(
                _sha256_file(song_path, cancel_callback=cancel_callback)
            )
        except OSError:
            continue
        if title:
            overrides[song_path] = title
    return overrides


def _catalog_song_matches(catalog_songs, song_paths, *, cancel_callback=None):
    """Match local originals and extraction-generated names without guessing."""
    songs_by_stem = {}
    for song in catalog_songs:
        for filename in (
            song.filename,
            build_long_midi_filename(song.track_number, song.title, song.filename),
            # Earlier extraction could not read the catalog and fell back to
            # the original stem, for example 01.MID -> 01 - 01.mid.
            build_long_midi_filename(
                song.track_number, os.path.splitext(song.filename)[0], song.filename,
            ),
        ):
            _set_unique_title(
                songs_by_stem,
                os.path.splitext(filename)[0].casefold(),
                song,
            )
    matches = {}
    for path in song_paths:
        _raise_if_cancelled(cancel_callback)
        stem = os.path.splitext(os.path.basename(path))[0].casefold()
        song = songs_by_stem.get(stem)
        if song is not None:
            matches[path] = song
    unmatched = [path for path in song_paths if path not in matches]
    if matches and unmatched:
        # Manually renamed copies can still be identified by exact contents.
        # Limit this lookup to the same source folder and reject conflicting
        # catalog identities, including identical recordings listed twice.
        songs_by_hash = {}
        for path, song in matches.items():
            try:
                digest = _sha256_file(path, cancel_callback=cancel_callback)
            except OSError:
                continue
            _set_unique_title(songs_by_hash, digest, song)
        for path in unmatched:
            try:
                digest = _sha256_file(path, cancel_callback=cancel_callback)
            except OSError:
                continue
            song = songs_by_hash.get(digest)
            if song is not None:
                matches[path] = song
    return matches


def _title_override_for_output(title, output_content):
    text = str(title or "").replace("\x00", " ").strip()
    if not text:
        return None
    encoded = text.encode("latin1", errors="replace")
    if output_content == "eseq":
        encoded = encoded[:ESEQ_TITLE_LENGTH]
    return encoded.decode("latin1")


def _prepared_song_name(source_path, index, output_content):
    return build_dos83_filename(
        os.path.basename(source_path),
        index,
        extension="FIL" if output_content == "eseq" else "MID",
    )


def _contains_bad_sector_filler(path, cancel_callback=None):
    """Recognize repeated recovery filler, allowing a marker in normal text."""
    tail = b""
    with open(path, "rb") as handle:
        while True:
            _raise_if_cancelled(cancel_callback)
            chunk = handle.read(64 * 1024)
            if not chunk:
                return False
            data = tail + chunk
            if _BAD_SECTOR_FILLER in data:
                return True
            tail = data[-(len(_BAD_SECTOR_FILLER) - 1):]


def _prepare_song_files(
    song_paths,
    temp_directory,
    *,
    output_content,
    require_midi_type0=False,
    title_overrides=None,
    title_sources=None,
    folder_metadata=None,
    catalog_songs=None,
    progress_callback=None,
    cancel_callback=None,
    midi_to_eseq_converter=None,
    eseq_to_midi_converter=None,
    language_code=None,
):
    midi_to_eseq_converter = midi_to_eseq_converter or convert_midi_file_to_eseq_path
    eseq_to_midi_converter = eseq_to_midi_converter or convert_eseq_file_to_midi_path
    prepared = []
    converted_count = 0
    total = len(song_paths)
    title_overrides = dict(title_overrides or {})
    title_sources = dict(title_sources or {})
    folder_metadata = dict(folder_metadata or {})
    catalog_songs = dict(catalog_songs or {})
    include_song_catalog = output_content == "midi" and any(
        item.song_catalog for item in folder_metadata.values()
    )

    for index, source_path in enumerate(song_paths, start=1):
        _raise_if_cancelled(cancel_callback)
        source_name = os.path.basename(source_path)
        source_is_midi = is_midi_file(source_path)
        source_is_eseq = is_eseq_file(source_path)
        title_override = _title_override_for_output(
            title_overrides.get(source_path),
            output_content,
        )
        if not source_is_midi and not source_is_eseq:
            raise FloppyImageError(
                f"'{source_name}' is not a valid Standard MIDI or Yamaha E-SEQ file."
            )

        output_label = "E-SEQ" if output_content == "eseq" else "MIDI"
        _notify(
            progress_callback,
            index - 1,
            max(1, total * 2),
            tr(
                "emulator.progress.preparing_file",
                language_code,
                filename=source_name,
                format=output_label,
                current=index,
                total=total,
            ),
        )

        image_path = _prepared_song_name(source_path, index, output_content)
        source_metadata = folder_metadata.get(os.path.dirname(source_path), SmartPianoSoftMetadata())
        preserved_midi = False
        warning = ""
        local_path = os.path.join(
            temp_directory,
            f"{index:05d}_{uuid.uuid4().hex}_{image_path}",
        )
        try:
            bad_sector_filler = source_is_midi and _contains_bad_sector_filler(
                source_path, cancel_callback,
            )
            if bad_sector_filler:
                if output_content != "midi":
                    raise FloppyImageError(
                        "The source contains [BAD SECTOR] recovery filler. "
                        "Missing MIDI data cannot be converted to E-SEQ."
                    )
                if title_override is not None and not source_metadata.song_catalog:
                    raise FloppyImageError(
                        "The source contains [BAD SECTOR] recovery filler. "
                        "Its title cannot be updated without a PSONG.MNG catalog."
                    )
                shutil.copy2(source_path, local_path)
                preserved_midi = True
                warning = (
                    "Source contains [BAD SECTOR] recovery filler. MIDI bytes were "
                    "preserved unchanged; missing data was not repaired and playback may fail."
                )
            elif output_content == "eseq" and source_is_midi:
                converter_options = {"filename_hint": image_path}
                if title_override is not None:
                    converter_options["title_override"] = title_override
                midi_to_eseq_converter(source_path, local_path, **converter_options)
                converted_count += 1
            elif output_content == "eseq" and is_clavinova_mda_file(source_path):
                intermediate_midi = os.path.join(
                    temp_directory,
                    f"{index:05d}_{uuid.uuid4().hex}.mid",
                )
                eseq_to_midi_converter(source_path, intermediate_midi)
                converter_options = {"filename_hint": image_path}
                if title_override is not None:
                    converter_options["title_override"] = title_override
                midi_to_eseq_converter(
                    intermediate_midi,
                    local_path,
                    **converter_options,
                )
                converted_count += 1
            elif output_content == "eseq":
                update_error = update_eseq_order_key_to_path(
                    source_path,
                    build_eseq_order_key_from_path(image_path),
                    local_path,
                )
                if update_error:
                    raise FloppyImageError(update_error)
                if title_override is not None:
                    update_error = update_eseq_title_to_path(
                        local_path,
                        title_override,
                        local_path,
                    )
                    if update_error:
                        raise FloppyImageError(update_error)
            elif source_is_eseq:
                converter_options = {}
                if title_override is not None:
                    converter_options["title_override"] = title_override
                eseq_to_midi_converter(
                    source_path,
                    local_path,
                    **converter_options,
                )
                converted_count += 1
            elif title_override is not None:
                try:
                    write_midi_title_to_path(source_path, title_override, local_path)
                except MidiTitleFormatError as exc:
                    if not source_metadata.song_catalog:
                        raise
                    # A catalog can carry the title without rewriting a MIDI
                    # event stream that the title editor cannot safely parse.
                    shutil.copy2(source_path, local_path)
                    preserved_midi = True
                    warning = (
                        f"Could not update the embedded title: {exc} "
                        "MIDI bytes were preserved unchanged; "
                        "the title is stored in PSONG.MNG. Playback may fail."
                    )
            else:
                shutil.copy2(source_path, local_path)
            if output_content == "midi" and require_midi_type0:
                if preserved_midi and extract_midi_type_label_from_midi(local_path) != "Type 0":
                    raise FloppyImageError(
                        "The preserved MIDI contains unreadable data and cannot be prepared as MIDI Type 0."
                    )
                # Work on the prepared copy so title edits and source files
                # survive unchanged. Packing and review see the final size.
                changed = convert_midi_file_to_type0_path(local_path, local_path)
                if extract_midi_type_label_from_midi(local_path) != "Type 0":
                    raise FloppyImageError("Preparation did not produce MIDI Type 0.")
                if changed and not source_is_eseq:
                    converted_count += 1
        except FloppyOperationCancelled:
            raise
        except Exception as exc:
            raise FloppyImageError(
                f"Could not prepare '{source_name}' as {output_label}: {exc}"
            ) from exc
        output_is_valid = (
            is_eseq_file(local_path)
            if output_content == "eseq"
            else is_midi_file(local_path)
        )
        if not os.path.isfile(local_path) or not output_is_valid:
            raise FloppyImageError(
                f"Preparation did not produce a valid {output_label} file for '{source_name}'."
            )

        # Preserved files use the title carried by PSONG.MNG. Other entries
        # describe the title actually embedded in the prepared song.
        title = (title_override or os.path.splitext(source_name)[0]) if preserved_midi else (
            extract_first_title_from_midi(local_path)
            if output_content == "midi"
            else extract_eseq_title_from_file(local_path)
        )
        if title.startswith("Error"):
            title = ""
        embedded_title_present = bool(title)
        title = title or os.path.splitext(source_name)[0]
        conversion_report = None
        try:
            conversion_report = build_conversion_report(source_path, local_path)
        except Exception as exc:
            with open(source_path, "rb") as original, open(local_path, "rb") as prepared_file:
                unchanged_midi = output_content == "midi" and source_is_midi and original.read() == prepared_file.read()
            if not unchanged_midi:
                raise FloppyImageError(f"Prepared song could not be musically verified: {source_name}: {exc}") from exc
            warning = (warning + " " if warning else "") + (
                f"Musical parsing unavailable; source bytes were preserved. Playback remains unverified: {exc}"
            )
        catalog_record = b""
        if include_song_catalog:
            catalog_song = catalog_songs.get(source_path)
            with open(local_path, "rb") as handle:
                midi_format = int.from_bytes(handle.read(10)[8:10], "big")
            catalog_record = build_smart_pianosoft_song_record(
                image_path,
                title,
                source_record=catalog_song.raw_record if catalog_song is not None else b"",
                midi_format=midi_format,
            )
            if preserved_midi and len(title.encode("cp1252", errors="replace")) > 32:
                warning += " The PSONG.MNG title is limited to 32 bytes."
        prepared.append(
            _PreparedSong(
                source_path=source_path,
                image_path=image_path,
                local_path=local_path,
                title=title,
                album_title=source_metadata.disk_title,
                smart_pianosoft=source_metadata,
                catalog_record=catalog_record,
                warning=warning,
                title_source=title_sources.get(source_path) or (
                    "Embedded title" if embedded_title_present and not preserved_midi else "Filename"
                ),
                conversion_report=conversion_report,
            )
        )

    return prepared, converted_count


def _directory_path_for_songs(temp_directory, songs, metadata, image_number):
    track_entries = [
        PianodirTrackEntry(
            image_path=song.image_path,
            local_path=song.local_path,
            title=song.title,
        )
        for song in songs
    ]
    output_path = os.path.join(
        temp_directory,
        f"pianodir_{image_number:03d}_{uuid.uuid4().hex}.fil",
    )
    with open(output_path, "wb") as handle:
        handle.write(build_pianodir_bytes(track_entries, metadata=metadata))
    return output_path


def _metadata_for_disk(metadata, image_prefix, disk_number):
    """Use the emulator slot as the catalog ID unless an API caller overrides it."""
    catalog_number = (
        metadata.catalog_number
        or f"{sanitize_image_prefix(image_prefix)}-{int(disk_number):04d}"
    )
    return PianodirMetadata(
        catalog_number=catalog_number,
        disk_title=metadata.disk_title or catalog_number,
        raw_label_bytes=metadata.raw_label_bytes,
    )


def _metadata_for_songs(metadata, songs, disk_layout):
    if disk_layout != "folders" or metadata.disk_title or not songs:
        return metadata
    return PianodirMetadata(
        catalog_number=metadata.catalog_number,
        disk_title=(
            songs[0].album_title or os.path.basename(os.path.dirname(songs[0].source_path))
        ),
        raw_label_bytes=metadata.raw_label_bytes,
    )


def _verify_raw_image(
    raw_path,
    songs,
    *,
    include_pianodir,
    expected_catalogs=None,
    safety_margin_bytes=0,
    cancel_callback=None,
):
    expected_catalogs = dict(expected_catalogs or {})
    listing = read_image_listing(raw_path)
    actual_names = {
        os.path.basename(entry.path).upper()
        for entry in listing.entries
        if not entry.directory
    }
    expected_names = {song.image_path.upper() for song in songs}
    expected_names.update(name.upper() for name in expected_catalogs)
    if include_pianodir:
        expected_names.add(PIANODIR_FILENAME)
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        detail = []
        if missing:
            detail.append(f"missing: {', '.join(missing)}")
        if extra:
            detail.append(f"unexpected: {', '.join(extra)}")
        raise FloppyImageError(
            "Generated image verification failed"
            + (f" ({'; '.join(detail)})" if detail else ".")
        )
    if listing.free_space < safety_margin_bytes:
        raise FloppyImageError(
            "Generated image verification failed: the disk has only "
            f"{listing.free_space:,} bytes free, below the requested "
            f"{safety_margin_bytes:,}-byte safety margin."
        )
    for song in songs:
        _raise_if_cancelled(cancel_callback)
        with open(song.local_path, "rb") as handle:
            prepared_bytes = handle.read()
        if _read_fat12_file_bytes(raw_path, song.image_path) != prepared_bytes:
            raise FloppyImageError(
                f"Generated image verification failed: packed contents differ for {song.image_path}."
            )
    expected_order = [song.image_path.upper() for song in songs]
    for name, expected_payload in expected_catalogs.items():
        _raise_if_cancelled(cancel_callback)
        payload = _read_fat12_file_bytes(raw_path, name)
        if payload != expected_payload:
            raise FloppyImageError(
                f"Generated image verification failed: packed contents differ for {name}."
            )
        if name.upper() == SMART_PIANOSOFT_SONG_CATALOG_NAME:
            try:
                references = [song.filename.upper() for song in parse_smart_pianosoft_song_catalog(payload)]
            except ValueError as exc:
                raise FloppyImageError(f"Generated image verification failed: invalid {name}: {exc}") from exc
        elif name.upper() == PIANODIR_FILENAME:
            count = int.from_bytes(payload[PIANODIR_COUNT_OFFSET:PIANODIR_COUNT_OFFSET + 2], "little") - 1
            if not payload.startswith(PIANODIR_HEADER) or count != len(songs):
                raise FloppyImageError(f"Generated image verification failed: invalid {name} header or song count.")
            references = []
            for index in range(count):
                start = len(PIANODIR_HEADER) + index * PIANODIR_TRACK_SIZE
                record = payload[start:start + 11]
                stem = record[:8].decode("ascii", errors="replace").rstrip(" \x00")
                extension = record[8:].decode("ascii", errors="replace").rstrip(" \x00")
                references.append((stem + ("." + extension if extension else "")).upper())
        else:
            continue
        if references != expected_order:
            raise FloppyImageError(
                f"Generated image verification failed: {name} song references or order differ from the prepared songs."
            )


def _midi_catalogs_for_songs(songs, image_stem):
    """Carry source catalogs into this image with its actual names and order."""
    catalogs = {}
    song_template = next(
        (song.smart_pianosoft.song_catalog for song in songs if song.smart_pianosoft.song_catalog),
        b"",
    )
    disk_template = next(
        (song.smart_pianosoft.disk_catalog for song in songs if song.smart_pianosoft.disk_catalog),
        b"",
    )
    if song_template:
        catalogs[SMART_PIANOSOFT_SONG_CATALOG_NAME] = build_smart_pianosoft_song_catalog(
            song_template, [song.catalog_record for song in songs],
        )
    if disk_template:
        # A pooled image can contain several albums. Give that compilation its
        # own slot title; the combined song list retains all source albums.
        if len({os.path.dirname(song.source_path) for song in songs}) > 1:
            disk_template = update_smart_pianosoft_disk_title(disk_template, image_stem)
        catalogs[SMART_PIANOSOFT_DISK_CATALOG_NAME] = disk_template
    return catalogs


def _pack_raw_images(
    prepared_songs,
    temp_directory,
    disk_format,
    *,
    image_prefix,
    starting_number,
    safety_margin_bytes,
    metadata,
    output_content,
    disk_layout="fill",
    progress_callback=None,
    cancel_callback=None,
    language_code=None,
):
    include_pianodir = output_content == "eseq"
    placeholder_path = (
        _directory_path_for_songs(
            temp_directory,
            [],
            _metadata_for_disk(metadata, image_prefix, starting_number),
            0,
        )
        if include_pianodir
        else ""
    )
    raw_images = []
    current_raw = ""
    current_songs = []
    total = len(prepared_songs)
    root_entry_limit = 0

    def start_image(image_number):
        nonlocal root_entry_limit
        raw_path = os.path.join(temp_directory, f"raw_{image_number:03d}.img")
        disk_number = starting_number + image_number - 1
        if disk_number > MAX_IMAGE_NUMBER:
            raise FloppyImageError(
                f"This set would exceed disk {MAX_IMAGE_NUMBER}; choose a lower "
                "starting number."
            )
        create_blank_floppy_image(
            raw_path,
            disk_format,
            volume_label=_volume_label(image_prefix, disk_number),
            cancel_callback=cancel_callback,
        )
        with open(raw_path, "rb") as handle:
            # The generated FAT12 root also contains one volume-label entry.
            root_entry_limit = _geometry_from_boot_sector(handle.read(512)).root_entries - 1
        if include_pianodir:
            _copy_host_file_into_image(
                raw_path,
                placeholder_path,
                PIANODIR_FILENAME,
                cancel_callback=cancel_callback,
            )
        return raw_path

    def has_room_with_margin(raw_path, song):
        listing = read_image_listing(raw_path)
        required_bytes = allocated_size(
            os.path.getsize(song.local_path),
            listing.cluster_size,
        )
        if output_content == "midi":
            catalogs = current_midi_catalogs([*current_songs, song])
            required_bytes += sum(
                allocated_size(len(payload), listing.cluster_size)
                for payload in catalogs.values()
            )
            if len(listing.entries) + 1 + len(catalogs) > root_entry_limit:
                return False
        return listing.free_space - required_bytes >= safety_margin_bytes

    def current_midi_catalogs(songs):
        return _midi_catalogs_for_songs(
            songs, _numbered_image_stem(image_prefix, starting_number + len(raw_images)),
        )

    def too_large_message(song):
        preparation_note = (
            " after E-SEQ conversion" if output_content == "eseq" else ""
        )
        margin_note = (
            f" while preserving the {safety_margin_bytes // 1024:,} KiB safety margin"
            if safety_margin_bytes
            else ""
        )
        return (
            f"'{os.path.basename(song.source_path)}' is too large to fit on "
            f"a {disk_format.label} image{preparation_note}{margin_note}."
        )

    def finish_image():
        if not current_raw or not current_songs:
            return
        image_number = len(raw_images) + 1
        expected_catalogs = {}
        if include_pianodir:
            disk_number = starting_number + image_number - 1
            directory_path = _directory_path_for_songs(
                temp_directory,
                current_songs,
                _metadata_for_disk(
                    _metadata_for_songs(metadata, current_songs, disk_layout),
                    image_prefix,
                    disk_number,
                ),
                image_number,
            )
            with open(directory_path, "rb") as handle:
                expected_catalogs[PIANODIR_FILENAME] = handle.read()
            _delete_eseq_directory_entries_from_image(
                current_raw,
                cancel_callback=cancel_callback,
            )
            _copy_host_file_into_image(
                current_raw,
                directory_path,
                PIANODIR_FILENAME,
                cancel_callback=cancel_callback,
            )
        catalogs = current_midi_catalogs(current_songs) if output_content == "midi" else {}
        expected_catalogs.update(catalogs)
        for name, payload in catalogs.items():
            catalog_path = os.path.join(temp_directory, name)
            with open(catalog_path, "wb") as handle:
                handle.write(payload)
            _copy_host_file_into_image(
                current_raw, catalog_path, name, cancel_callback=cancel_callback,
            )
        _verify_raw_image(
            current_raw,
            current_songs,
            include_pianodir=include_pianodir,
            expected_catalogs=expected_catalogs,
            safety_margin_bytes=safety_margin_bytes,
            cancel_callback=cancel_callback,
        )
        raw_images.append((current_raw, tuple(current_songs)))

    current_raw = start_image(1)
    for index, song in enumerate(prepared_songs, start=1):
        _raise_if_cancelled(cancel_callback)
        if (
            disk_layout == "folders"
            and current_songs
            and os.path.dirname(song.source_path)
            != os.path.dirname(current_songs[0].source_path)
        ):
            finish_image()
            current_songs = []
            current_raw = start_image(len(raw_images) + 1)
        _notify(
            progress_callback,
            total + index - 1,
            max(1, total * 2),
            tr(
                "emulator.progress.packing",
                language_code,
                filename=song.image_path,
                disk=len(raw_images) + 1,
            ),
        )

        if include_pianodir and len(current_songs) >= PIANODIR_MAX_TRACKS:
            finish_image()
            current_songs = []
            current_raw = start_image(len(raw_images) + 1)

        if not has_room_with_margin(current_raw, song):
            if not current_songs:
                raise FloppyImageError(too_large_message(song))
            finish_image()
            current_songs = []
            current_raw = start_image(len(raw_images) + 1)
            if not has_room_with_margin(current_raw, song):
                raise FloppyImageError(too_large_message(song))

        try:
            _copy_host_file_into_image(
                current_raw,
                song.local_path,
                song.image_path,
                cancel_callback=cancel_callback,
            )
            current_songs.append(song)
            continue
        except FloppyImageError as exc:
            if not _is_image_capacity_error(exc):
                raise

        if not current_songs:
            raise FloppyImageError(too_large_message(song))

        finish_image()
        current_songs = []
        current_raw = start_image(len(raw_images) + 1)
        if not has_room_with_margin(current_raw, song):
            raise FloppyImageError(too_large_message(song))
        try:
            _copy_host_file_into_image(
                current_raw,
                song.local_path,
                song.image_path,
                cancel_callback=cancel_callback,
            )
        except FloppyImageError as exc:
            if _is_image_capacity_error(exc):
                raise FloppyImageError(too_large_message(song)) from exc
            raise
        current_songs.append(song)

    finish_image()
    return raw_images


def _output_paths(
    output_directory,
    image_prefix,
    output_ext,
    image_count,
    starting_number,
):
    filenames = [
        f"{_numbered_image_stem(image_prefix, image_number)}.{output_ext}"
        for image_number in range(
            starting_number,
            starting_number + image_count,
        )
    ]
    return [os.path.join(output_directory, filename) for filename in filenames]


def _song_lists_output_path(
    output_directory,
    image_prefix,
    starting_number,
    image_count,
):
    first_stem = _numbered_image_stem(image_prefix, starting_number)
    if image_count == 1:
        filename = f"{first_stem}-song-list.txt"
    else:
        last_stem = _numbered_image_stem(
            image_prefix,
            starting_number + image_count - 1,
        )
        filename = f"{first_stem}-{last_stem}-song-lists.txt"
    return os.path.join(output_directory, filename)


def _song_list_display_text(value, fallback=""):
    text = re.sub(r"\s+", " ", str(value or "").replace("\x00", " ")).strip()
    return text or fallback


def _build_emulator_song_lists_text(
    raw_images,
    final_paths,
    *,
    image_prefix,
    starting_number,
    metadata,
    output_content,
    disk_layout="fill",
    source_directory="",
    warnings=(),
):
    """Describe the whole set in image/playback order, retaining source albums."""
    def source_album_label(song):
        folder = os.path.dirname(song.source_path)
        relative_folder = _display_relative_path(folder, source_directory)
        return _song_list_display_text(
            relative_folder if relative_folder != "." else os.path.basename(folder)
        )

    lines = [
        "Emulator Disk Set Song Lists",
        f"Images: {len(final_paths)}",
        f"Songs: {sum(len(songs) for _raw_path, songs in raw_images)}",
        "",
    ]
    for image_index, ((_raw_path, songs), final_path) in enumerate(
        zip(raw_images, final_paths),
        start=1,
    ):
        lines.append(
            f"Image {image_index} of {len(final_paths)}: {os.path.basename(final_path)}"
        )
        if disk_layout == "folders":
            lines.append(f"Folder: {source_album_label(songs[0])}")
            if output_content == "midi":
                album = songs[0].album_title or os.path.basename(os.path.dirname(songs[0].source_path))
                lines.append(f"Album: {_song_list_display_text(album)}")
        if output_content == "eseq":
            disk_metadata = _metadata_for_disk(
                _metadata_for_songs(metadata, songs, disk_layout),
                image_prefix,
                starting_number + image_index - 1,
            )
            disk_title = _song_list_display_text(disk_metadata.disk_title)
            catalog_number = _song_list_display_text(disk_metadata.catalog_number)
            if disk_title:
                lines.append(f"Album: {disk_title}")
            if catalog_number:
                lines.append(f"Catalog: {catalog_number}")
        lines.append("")
        previous_folder = None
        for song_index, song in enumerate(songs, start=1):
            if disk_layout == "fill":
                folder = os.path.dirname(song.source_path)
                if folder != previous_folder:
                    if previous_folder is not None:
                        lines.append("")
                    album_label = source_album_label(song)
                    if song.album_title:
                        album_label = f"{_song_list_display_text(song.album_title)} ({album_label})"
                    lines.append(f"Source album: {album_label}")
                    previous_folder = folder
            fallback_title = os.path.splitext(os.path.basename(song.source_path))[0]
            lines.append(
                f"{song_index}. {_song_list_display_text(song.title, fallback_title)}"
            )
        if image_index < len(final_paths):
            lines.append("")
    if warnings:
        lines.extend(["", "Warnings:", *[f"- {warning}" for warning in warnings]])
    return "\n".join(lines).rstrip() + "\n"


def build_emulator_disk_images(
    source_directory,
    output_directory,
    *,
    prefix=None,
    starting_number=DEFAULT_STARTING_NUMBER,
    safety_margin_bytes=DEFAULT_SAFETY_MARGIN_BYTES,
    set_name=None,
    album_title="",
    catalog_number="",
    disk_format=None,
    output_ext="hfe",
    output_content="eseq",
    require_midi_type0=False,
    include_subfolders=True,
    disk_layout="fill",
    shuffle=False,
    include_song_lists=False,
    overwrite_existing=False,
    overwrite_callback=None,
    review_callback=None,
    language_code=None,
    progress_callback=None,
    cancel_callback=None,
    midi_to_eseq_converter=None,
    eseq_to_midi_converter=None,
):
    """Build numbered emulator-ready images with E-SEQ or MIDI songs.

    ``set_name`` is retained as a compatibility alias for ``prefix``.
    ``disk_layout="folders"`` starts a new disk for each folder containing
    songs. Oversized folders continue on additional disks without mixing
    albums. Folder layout always scans nested folders; ``include_subfolders``
    controls discovery only for the automatic-fill layout.
    MIDI images carry available folder-local MNG catalogs, adapted to each
    image's songs, while E-SEQ images use PIANODIR.FIL.
    ``require_midi_type0`` converts MIDI output before preview and packing,
    preserving musical channels and instruments.
    """
    source_directory = os.path.abspath(os.fspath(source_directory))
    output_directory = os.path.abspath(os.fspath(output_directory))
    output_ext = str(output_ext or "hfe").lower().lstrip(".")
    if output_ext not in EMULATOR_IMAGE_EXTENSIONS:
        raise FloppyImageError(
            "Emulator image format must be raw IMG or HFE."
        )
    output_content = str(output_content or "eseq").strip().lower()
    if output_content not in EMULATOR_CONTENT_FORMATS:
        raise FloppyImageError("Disk contents must be Yamaha E-SEQ or Standard MIDI.")
    if disk_layout not in EMULATOR_DISK_LAYOUTS:
        raise FloppyImageError("Disk layout must be 'fill' or 'folders'.")
    if disk_format is None:
        disk_format = next(item for item in DISK_FORMATS if item.key == "ibm.720")
    if disk_format not in DISK_FORMATS:
        raise FloppyImageError("Choose a supported IBM floppy disk format.")
    try:
        starting_number = int(starting_number)
    except (TypeError, ValueError) as exc:
        raise FloppyImageError("The starting disk number must be a whole number.") from exc
    if not 0 <= starting_number <= MAX_IMAGE_NUMBER:
        raise FloppyImageError(
            f"The starting disk number must be between 0 and {MAX_IMAGE_NUMBER}."
        )
    try:
        safety_margin_bytes = int(safety_margin_bytes)
    except (TypeError, ValueError) as exc:
        raise FloppyImageError("The safety margin must be a whole number of bytes.") from exc
    if safety_margin_bytes < 0:
        raise FloppyImageError("The safety margin cannot be negative.")

    song_paths = discover_song_files(
        source_directory,
        include_subfolders=disk_layout == "folders" or include_subfolders,
    )
    if not song_paths:
        raise FloppyImageError(
            "The selected folder does not contain any MIDI or Yamaha E-SEQ song files."
        )
    folders = {}
    for song_path in song_paths:
        _raise_if_cancelled(cancel_callback)
        folders.setdefault(os.path.dirname(song_path), []).append(song_path)
    title_overrides = {}
    folder_metadata = {}
    catalog_songs = {}
    for folder, folder_songs in folders.items():
        _raise_if_cancelled(cancel_callback)
        folder_metadata[folder] = smart_pianosoft_metadata_from_directory(folder)
        catalog_songs.update(_catalog_song_matches(
            folder_metadata[folder].songs, folder_songs, cancel_callback=cancel_callback,
        ))
    title_overrides.update({path: song.title for path, song in catalog_songs.items() if song.title})
    title_sources = {path: "MNG catalog" for path in title_overrides}

    # Explicit index edits take precedence over catalog and embedded titles.
    index_titles = _load_index_title_overrides(
            source_directory,
            song_paths,
            cancel_callback=cancel_callback,
    )
    title_overrides.update(index_titles)
    title_sources.update({path: "INDEX.csv" for path in index_titles})
    for folder, folder_songs in folders.items():
        _raise_if_cancelled(cancel_callback)
        if folder != source_directory:
            index_titles = _load_index_title_overrides(
                    folder,
                    folder_songs,
                    cancel_callback=cancel_callback,
            )
            title_overrides.update(index_titles)
            title_sources.update({path: "INDEX.csv" for path in index_titles})
    shuffle = bool(shuffle)
    include_song_lists = bool(include_song_lists)
    if disk_layout == "folders":
        # Discovery already supplies natural folder/song order. Keep each
        # containing directory together, even when shuffling its songs.
        for folder_songs in folders.values():
            if shuffle:
                random.shuffle(folder_songs)
        song_paths = [path for paths in folders.values() for path in paths]
    elif shuffle:
        random.shuffle(song_paths)

    image_prefix = sanitize_image_prefix(prefix if prefix is not None else set_name)
    album_title = str(album_title or "").strip()
    catalog_number = str(catalog_number or "").strip()
    metadata = PianodirMetadata(
        catalog_number=catalog_number,
        disk_title=album_title,
    )
    try:
        os.makedirs(output_directory, exist_ok=True)
    except OSError as exc:
        raise FloppyImageError(f"Could not create the output folder: {exc}") from exc
    if not os.path.isdir(output_directory):
        raise FloppyImageError(f"The output path is not a folder: {output_directory}")

    temp_directory = tempfile.mkdtemp(prefix="aps_emulator_images_")
    committed_paths = []
    replacement_backups = {}
    try:
        included_folders = list(folders)
        reviewed_album_titles = {}
        reviewed_song_titles = {}
        original_song_paths = list(song_paths)
        revision = 0
        while True:
            _raise_if_cancelled(cancel_callback)
            preparation_directory = os.path.join(temp_directory, f"preview_{revision}")
            os.makedirs(preparation_directory)
            active_metadata = dict(folder_metadata)
            if album_title and disk_layout == "folders":
                active_metadata = {
                    folder: replace(value, disk_title=album_title)
                    for folder, value in active_metadata.items()
                }
            for folder, title in reviewed_album_titles.items():
                existing = active_metadata[folder]
                active_metadata[folder] = replace(
                    existing, disk_title=title,
                    disk_catalog=(update_smart_pianosoft_disk_title(existing.disk_catalog, title)
                                  if existing.disk_catalog else b""),
                )
            prepared_songs, converted_count = _prepare_song_files(
                song_paths,
                preparation_directory,
                output_content=output_content,
                require_midi_type0=require_midi_type0,
                title_overrides={**title_overrides, **reviewed_song_titles},
                title_sources={**title_sources, **{path: "Preview edit" for path in reviewed_song_titles}},
                folder_metadata=active_metadata,
                catalog_songs=catalog_songs,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
                midi_to_eseq_converter=midi_to_eseq_converter,
                eseq_to_midi_converter=eseq_to_midi_converter,
                language_code=language_code,
            )
            raw_images = _pack_raw_images(
                prepared_songs,
                preparation_directory,
                disk_format,
                image_prefix=image_prefix,
                starting_number=starting_number,
                safety_margin_bytes=safety_margin_bytes,
                metadata=(replace(metadata, disk_title="")
                          if reviewed_album_titles and disk_layout == "folders" else metadata),
                output_content=output_content,
                disk_layout=disk_layout,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
                language_code=language_code,
            )
            if review_callback is None:
                break
            preview_paths = _output_paths(
                output_directory, image_prefix, output_ext, len(raw_images), starting_number,
            )
            album_order = included_folders + [folder for folder in folders if folder not in included_folders]
            preview = EmulatorBuildPreview(
                source_directory=source_directory,
                output_directory=output_directory,
                disks=tuple(EmulatorDiskPreview(path, read_image_listing(raw).free_space, tuple(songs))
                            for (raw, songs), path in zip(raw_images, preview_paths)),
                albums=tuple(EmulatorAlbumPreview(
                    folder,
                    reviewed_album_titles.get(folder) or album_title or folder_metadata[folder].disk_title or os.path.basename(folder),
                    len(folders[folder]), folder in included_folders,
                    "Preview edit" if folder in reviewed_album_titles else (
                        "Build option" if album_title else (
                            "MNG catalog" if folder_metadata[folder].disk_title else "Folder name"
                        )
                    ),
                ) for folder in album_order),
                warnings=tuple(f"{song.image_path}: {song.warning}" for song in prepared_songs if song.warning),
                album_titles=dict(reviewed_album_titles),
                title_overrides=dict(reviewed_song_titles),
                output_content=output_content,
                disk_layout=disk_layout,
            )
            decision = review_callback(preview)
            _raise_if_cancelled(cancel_callback)
            if not decision or decision.get("action") == "cancel":
                raise FloppyOperationCancelled("Emulator image preview was cancelled.")
            if decision.get("action") == "build":
                break
            if decision.get("action") != "revise":
                raise FloppyImageError("Invalid emulator preview decision.")
            selected = list(decision.get("included_folders", ()))
            if not selected or len(set(selected)) != len(selected) or not set(selected) <= set(folders):
                raise FloppyImageError("Choose at least one source album, without duplicate folders.")
            reviewed_album_titles = dict(decision.get("album_titles", {}))
            reviewed_song_titles = dict(decision.get("title_overrides", {}))
            if not set(reviewed_album_titles) <= set(folders) or not set(reviewed_song_titles) <= set(original_song_paths):
                raise FloppyImageError("Preview edits must refer to the selected source collection.")
            if not all(isinstance(value, str) and value.strip() and "\x00" not in value
                       for value in (*reviewed_album_titles.values(), *reviewed_song_titles.values())):
                raise FloppyImageError("Preview titles must be nonempty text without NUL characters.")
            included_folders = selected
            song_paths = [path for folder in selected for path in original_song_paths if os.path.dirname(path) == folder]
            shutil.rmtree(preparation_directory)
            revision += 1
        if reviewed_album_titles and disk_layout == "folders":
            metadata = replace(metadata, disk_title="")
        ending_number = starting_number + len(raw_images) - 1
        if ending_number > MAX_IMAGE_NUMBER:
            raise FloppyImageError(
                f"This set would end at disk {ending_number}; choose a starting "
                f"number that keeps every disk at {MAX_IMAGE_NUMBER} or below."
            )
        final_paths = _output_paths(
            output_directory,
            image_prefix,
            output_ext,
            len(raw_images),
            starting_number,
        )
        warnings = tuple(
            f"{os.path.basename(final_path)} / {song.image_path} "
            f"({_display_relative_path(song.source_path, source_directory)}): {song.warning}"
            for (_raw_path, songs), final_path in zip(raw_images, final_paths)
            for song in songs if song.warning
        )
        song_list_path = (
            _song_lists_output_path(
                output_directory,
                image_prefix,
                starting_number,
                len(raw_images),
            )
            if include_song_lists
            else ""
        )
        output_candidates = [*final_paths]
        if song_list_path:
            output_candidates.append(song_list_path)
        existing_paths = [path for path in output_candidates if os.path.lexists(path)]
        if existing_paths:
            invalid_paths = [
                path
                for path in existing_paths
                if not os.path.isfile(path) or os.path.islink(path)
            ]
            if invalid_paths:
                names = ", ".join(
                    os.path.basename(path) for path in invalid_paths[:3]
                )
                if len(invalid_paths) > 3:
                    names += ", ..."
                raise FloppyImageError(
                    f"Output path cannot be replaced as a regular file: {names}."
                )

            overwrite_approved = bool(overwrite_existing)
            if not overwrite_approved and overwrite_callback is not None:
                _raise_if_cancelled(cancel_callback)
                overwrite_approved = bool(overwrite_callback(tuple(existing_paths)))
                _raise_if_cancelled(cancel_callback)
            if not overwrite_approved:
                if overwrite_callback is not None:
                    raise FloppyOperationCancelled(
                        "Emulator image replacement was cancelled."
                    )
                names = ", ".join(os.path.basename(path) for path in existing_paths[:3])
                if len(existing_paths) > 3:
                    names += ", ..."
                raise FloppyImageError(
                    f"Output file already exists: {names}. Choose another prefix, "
                    "starting number, or output folder."
                )

        staged_outputs = []
        total_steps = len(prepared_songs) * 2 + len(raw_images)
        for index, ((raw_path, _songs), final_path) in enumerate(
            zip(raw_images, final_paths),
            start=1,
        ):
            _raise_if_cancelled(cancel_callback)
            _notify(
                progress_callback,
                len(prepared_songs) * 2 + index - 1,
                total_steps,
                tr(
                    "emulator.progress.writing",
                    language_code,
                    current=index,
                    total=len(raw_images),
                ),
            )
            staged_path = os.path.join(
                temp_directory,
                f"output_{index:03d}.{output_ext}",
            )
            _write_image_direct(raw_path, staged_path, output_ext, disk_format)
            staged_outputs.append((staged_path, final_path))

        if song_list_path:
            staged_song_list_path = os.path.join(
                temp_directory,
                "emulator_song_lists.txt",
            )
            with open(
                staged_song_list_path,
                "w",
                encoding="utf-8",
                newline="\n",
            ) as handle:
                handle.write(
                    _build_emulator_song_lists_text(
                        raw_images,
                        final_paths,
                        image_prefix=image_prefix,
                        starting_number=starting_number,
                        metadata=metadata,
                        output_content=output_content,
                        disk_layout=disk_layout,
                        source_directory=source_directory,
                        warnings=warnings,
                    )
                )
            staged_outputs.append((staged_song_list_path, song_list_path))

        _raise_if_cancelled(cancel_callback)
        for backup_index, existing_path in enumerate(existing_paths, start=1):
            backup_path = os.path.join(
                temp_directory,
                f"existing_output_{backup_index:04d}.bak",
            )
            shutil.copy2(existing_path, backup_path)
            replacement_backups[existing_path] = backup_path

        for staged_path, final_path in staged_outputs:
            committed_paths.append(final_path)
            _finish_temp_output(staged_path, final_path)

        for (raw_path, _songs), final_path in zip(raw_images, final_paths):
            _raise_if_cancelled(cancel_callback)
            _notify(progress_callback, total_steps - 1, total_steps,
                    f"Verifying delivered contents: {os.path.basename(final_path)}...")
            verify_image_payloads(final_path, raw_path, disk_format, cancel_callback=cancel_callback)

        _notify(
            progress_callback,
            total_steps,
            total_steps,
            tr(
                "emulator.progress.complete",
                language_code,
                images=len(final_paths),
            ),
        )
        return EmulatorImageBuildResult(
            source_directory=source_directory,
            output_directory=output_directory,
            song_files_found=len(song_paths),
            files_prepared=len(prepared_songs),
            converted_files=converted_count,
            images_created=len(final_paths),
            output_content=output_content,
            output_paths=tuple(final_paths),
            image_prefix=image_prefix,
            starting_number=starting_number,
            safety_margin_bytes=safety_margin_bytes,
            shuffled=shuffle,
            song_list_path=song_list_path,
            disk_layout=disk_layout,
            warnings=warnings,
            contents_verified=True,
        )
    except Exception:
        for path in reversed(committed_paths):
            try:
                backup_path = replacement_backups.get(path)
                if backup_path and os.path.isfile(backup_path):
                    shutil.copy2(backup_path, path)
                elif os.path.lexists(path):
                    os.remove(path)
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(temp_directory, ignore_errors=True)


def build_emulator_eseq_images(*args, converter=None, **kwargs):
    """Compatibility wrapper for the original E-SEQ-only builder API."""
    kwargs["output_content"] = "eseq"
    if converter is not None:
        kwargs["midi_to_eseq_converter"] = converter
    return build_emulator_disk_images(*args, **kwargs)
