"""Build Yamaha MIDI catalogs from the editor's current, staged song list."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
import tempfile
import uuid

from .helpers.file_batch import publish_file_batch
from .preparation_profiles import (
    SETTING_PROFILE, preserves_yamaha_midi_catalogs, generate_yamaha_catalogs_enabled,
)
from .smart_pianosoft import (
    SmartPianoSoftMetadata, build_smart_pianosoft_song_catalog,
    build_smart_pianosoft_song_record, parse_smart_pianosoft_disk_title,
    parse_smart_pianosoft_song_catalog, update_smart_pianosoft_disk_title,
)
from .yamaha_catalogs import yamaha_catalog_metadata_from_directory, yamaha_metadata_from_pianodir_bytes

CATALOG_NAMES = {"PSONG.MNG", "PDISK.MNG"}


def catalogs_enabled(window):
    settings = getattr(window, "settings", None)
    return (generate_yamaha_catalogs_enabled(settings) or
            preserves_yamaha_midi_catalogs(settings.value(SETTING_PROFILE, "unsure") if settings else "unsure"))


def catalog_generation_enabled(window):
    settings = getattr(window, "settings", None)
    return generate_yamaha_catalogs_enabled(settings)


def regular_catalog_source_directories(window, *, include_eseq=False):
    """Folders whose MIDI output should carry Yamaha metadata."""
    if not catalogs_enabled(window):
        return set()
    directories = {os.path.dirname(os.path.abspath(window.table.item(row, 1).text()))
                   for row in window._regular_file_rows()
                   if window._listed_file_title_mode(window.table.item(row, 1).text())
                   in ({"midi", "eseq"} if include_eseq else {"midi"})}
    if catalog_generation_enabled(window):
        return directories
    return {directory for directory in directories
            if yamaha_catalog_metadata_from_directory(directory).has_source_catalog}


def regular_catalog_names(window):
    directories = regular_catalog_source_directories(window)
    if not directories:
        return set()
    if catalog_generation_enabled(window):
        return CATALOG_NAMES.copy()
    names = set().union(*(yamaha_catalog_metadata_from_directory(directory).source_catalog_names
                          for directory in directories))
    return names & CATALOG_NAMES


def image_catalog_source_directories(window):
    """Image folders whose output retains DOS 8.3 Yamaha catalog references."""
    if not catalogs_enabled(window):
        return set()
    if catalog_generation_enabled(window):
        return {os.path.dirname(window._final_image_path(path))
                for _row, path, kind, _name in window._preparation_song_rows()
                if kind == "midi"}
    directories = set()
    for path in set(window.imageEntriesByPath) | set(window.pendingImageAdditions):
        if path in window.pendingImageDeletes:
            continue
        name = os.path.basename(path).upper()
        if name == "PIANODIR.FIL":
            try:
                yamaha_metadata_from_pianodir_bytes(
                    Path(window._pending_or_extracted_image_path(path)).read_bytes(),
                )
            except (OSError, ValueError):
                continue
        elif name not in CATALOG_NAMES:
            continue
        directories.add(os.path.dirname(window._final_image_path(path)))
    return directories


def catalog_output_path(directory, name):
    """Use the existing catalog's casing on case-sensitive filesystems too."""
    if os.path.isdir(directory):
        matches = sorted(entry for entry in os.listdir(directory) if entry.upper() == name.upper())
        if matches:
            name = name if name in matches else matches[0]
    return os.path.join(directory, name)


def _midi_type(path):
    with open(path, "rb") as handle:
        header = handle.read(10)
    if len(header) != 10 or header[:4] != b"MThd":
        raise ValueError(f"Cannot catalog an invalid MIDI file: {path}")
    return int.from_bytes(header[8:10], "big")


def _catalog_pair(metadata, records, title, *, generate_missing):
    names = CATALOG_NAMES if generate_missing else metadata.source_catalog_names
    payloads = {}
    if "PSONG.MNG" in names:
        payloads["PSONG.MNG"] = build_smart_pianosoft_song_catalog(metadata.song_catalog, records)
    if "PDISK.MNG" in names:
        payloads["PDISK.MNG"] = update_smart_pianosoft_disk_title(metadata.disk_catalog, title)
    return payloads


def regular_catalog_payloads(window, *, path_remap=None, base_dir=None, retain_unlisted=False,
                             image_record_sink=None):
    """Return destination/payload pairs, using the displayed titles and names.

    In-place saves retain entries for unlisted siblings. Exports describe only
    exported songs. Source templates are read before any destination is written.
    """
    if not catalogs_enabled(window):
        return {}
    source_directories = regular_catalog_source_directories(window)
    destination_directories = {
        os.path.dirname((path_remap or {}).get(source) or os.path.join(
            base_dir or os.path.dirname(source), window._regular_row_output_filename(row)))
        for row in window._regular_file_rows()
        if (source := window.table.item(row, 1).text())
        and os.path.dirname(os.path.abspath(source)) in source_directories
        and (path_remap is None or source in path_remap)
    }
    groups = {}
    metadata_by_dir = {}
    original_metadata_by_dir = {}
    pending_directories = {os.path.dirname(os.path.abspath(path)) for path in
                           getattr(window, "pendingYamahaCatalogPayloads", {})}
    for row in window._regular_file_rows():
        source = window.table.item(row, 1).text()
        if window._listed_file_title_mode(source) != "midi":
            continue
        source_dir = os.path.dirname(os.path.abspath(source))
        destination = ((path_remap or {}).get(source) or os.path.join(
            base_dir or source_dir, window._regular_row_output_filename(row)))
        if os.path.dirname(destination) not in destination_directories:
            continue
        metadata = metadata_by_dir.setdefault(source_dir, None)
        if metadata is None:
            metadata = yamaha_catalog_metadata_from_directory(source_dir)
            original_metadata_by_dir[source_dir] = metadata
            pending = {os.path.basename(path).upper(): payload for path, payload in
                       getattr(window, "pendingYamahaCatalogPayloads", {}).items()
                       if os.path.dirname(os.path.abspath(path)) == source_dir
                       and (catalog_generation_enabled(window)
                            or os.path.basename(path).upper() in metadata.source_catalog_names)}
            if pending:
                song_data = pending.get("PSONG.MNG", metadata.song_catalog)
                disk_data = pending.get("PDISK.MNG", metadata.disk_catalog)
                metadata = replace(metadata, song_catalog=song_data, disk_catalog=disk_data,
                                   songs=parse_smart_pianosoft_song_catalog(song_data))
            metadata_by_dir[source_dir] = metadata
        if path_remap is not None and source not in path_remap:
            continue
        source_name = os.path.basename(source).casefold()
        names = [source_name]
        if source_dir in pending_directories:
            # A failed save can leave the source at its old name while the
            # pending catalog already describes the planned rename.
            names.insert(0, os.path.basename(destination).casefold())
        song = next((song for name in names for song in metadata.songs
                     if song.filename.casefold() == name), None)
        if song is None:
            # The user may revise a pending rename before retrying. The source
            # catalog still owns its opaque fields even if neither name now
            # matches the previous attempt's catalog.
            song = next((song for song in original_metadata_by_dir[source_dir].songs
                         if song.filename.casefold() == source_name), None)
        title = song.title if song and song.title else window._row_raw_title(row)
        if song and source_dir in pending_directories:
            title = song.title
        if getattr(window, "pendingRegularConversions", {}).get(source, {}).get("title_edited"):
            title = window._row_raw_title(row)
        title = getattr(window, "pendingEdits", {}).get(source, title)
        record = build_smart_pianosoft_song_record(
            os.path.basename(destination), title,
            source_record=song.raw_record if song else b"",
            midi_format=_midi_type(window._regular_source_material_path(source)),
        )
        if image_record_sink is not None:
            image_record_sink[destination] = record
        group = groups.setdefault(os.path.dirname(destination), {"metadata": metadata, "records": [], "by_source": {}})
        if metadata.has_source_catalog and not group["metadata"].has_source_catalog:
            group["metadata"] = metadata
        elif metadata.has_source_catalog:
            current = group["metadata"]
            group["metadata"] = replace(
                current, song_catalog=current.song_catalog or metadata.song_catalog,
                disk_catalog=current.disk_catalog or metadata.disk_catalog,
                pianodir_source=current.pianodir_source or metadata.pianodir_source,
            )
        group["records"].append(record)
        group["by_source"][song.filename.casefold() if song else source_name] = record
    result = {}
    album = window._current_regular_pianodir_metadata().disk_title
    album_edited = album != window.loadedRegularPianodirMetadata.disk_title
    for directory, group in groups.items():
        metadata = group["metadata"]
        if not catalog_generation_enabled(window) and not metadata.has_source_catalog:
            continue
        records = group["records"]
        existing_files = {name.casefold(): os.path.join(directory, name)
                          for name in os.listdir(directory)} if os.path.isdir(directory) else {}

        def song_exists(filename):
            path = existing_files.get(filename.casefold())
            return bool(path and os.path.isfile(path))

        if retain_unlisted:
            selected = group["by_source"]
            if metadata.song_catalog:
                # In-place title/filename edits must not renumber catalog
                # tracks merely because the table is sorted differently.
                ordered = []
                matched = set()
                for song in metadata.songs:
                    name = song.filename.casefold()
                    if name in selected:
                        ordered.append(selected[name])
                        matched.add(name)
                    elif song.raw_record and song_exists(song.filename):
                        ordered.append(song.raw_record)
                ordered.extend(record for name, record in selected.items() if name not in matched)
                records = ordered
        # A blank editor field is an intentional album-title edit.
        title = album if len(groups) == 1 or album_edited else metadata.disk_title
        # A split-image template is bounded; every song's complete record also
        # travels with its file specification for the per-disk catalog builder.
        catalog_records = records[:999] if image_record_sink is not None else records
        for name, payload in _catalog_pair(metadata, catalog_records, title,
                                         generate_missing=catalog_generation_enabled(window)).items():
            result[catalog_output_path(directory, name)] = payload
    return result


def write_catalog_payloads(payloads, *, backup_callback=None):
    """Publish the pair together with rollback if either write fails."""
    if not payloads:
        return []
    with tempfile.TemporaryDirectory(prefix="aps_yamaha_catalogs_") as directory:
        prepared = []
        for index, (destination, payload) in enumerate(payloads.items()):
            os.makedirs(os.path.dirname(os.path.abspath(destination)), exist_ok=True)
            staged = Path(directory, str(index))
            staged.write_bytes(payload)
            prepared.append((str(staged), destination))
        publish_file_batch(prepared, backup_callback=backup_callback)
    return list(payloads)


def image_catalog_payloads(window):
    """Return image-relative catalogs for current MIDI rows and staged edits."""
    if not catalogs_enabled(window):
        return {}
    templates = {}
    pianodir_metadata = {}
    catalog_paths = {}
    paths = set(window.imageEntriesByPath) | set(window.pendingImageAdditions)
    for path in paths:
        if path in window.pendingImageDeletes:
            continue
        name = os.path.basename(path).upper()
        if name == "PIANODIR.FIL":
            try:
                payload = Path(window._pending_or_extracted_image_path(path)).read_bytes()
                pianodir_metadata[os.path.dirname(window._final_image_path(path))] = yamaha_metadata_from_pianodir_bytes(payload)
            except (OSError, ValueError):
                pass
            continue
        if name not in CATALOG_NAMES:
            continue
        directory = os.path.dirname(window._final_image_path(path))
        payload = Path(window._pending_or_extracted_image_path(path)).read_bytes()
        # Invalid existing metadata is reported instead of silently discarded.
        if name == "PSONG.MNG":
            parse_smart_pianosoft_song_catalog(payload)
        else:
            parse_smart_pianosoft_disk_title(payload)
        templates.setdefault(directory, {})[name] = payload
        catalog_paths[(directory, name)] = window._final_image_path(path)
    groups = {directory: [] for directory in templates}
    for row in range(window.table.rowCount()):
        item = window.table.item(row, 1)
        if item is None:
            continue
        path = item.text()
        if path in window.pendingImageDeletes or window._image_path_title_mode(path) != "midi":
            continue
        final = window._final_image_path(path)
        directory = os.path.dirname(final)
        if not catalog_generation_enabled(window) and directory not in templates and directory not in pianodir_metadata:
            continue
        template = templates.get(directory, {})
        songs = parse_smart_pianosoft_song_catalog(template["PSONG.MNG"]) if "PSONG.MNG" in template else ()
        song = next((song for song in songs if song.filename.casefold() == os.path.basename(path).casefold()), None)
        if song is None and directory in pianodir_metadata:
            song = next((song for song in pianodir_metadata[directory].songs
                         if song.filename.casefold() == os.path.basename(path).casefold()), None)
        title = song.title if song and song.title else window._row_raw_title(row)
        if window._image_info_for_path(path).get("title_edited"):
            title = window._row_raw_title(row)
        title = window.pendingImageTitleEdits.get(path, window.pendingSmartPianoSoftTitleEdits.get(path, title))
        record = build_smart_pianosoft_song_record(
            os.path.basename(final), title, source_record=song.raw_record if song else b"",
            midi_format=_midi_type(window._pending_or_extracted_image_path(path)),
        )
        groups.setdefault(directory, []).append(record)
    result = {}
    album = window._current_image_pianodir_metadata().disk_title
    album_edited = album != window.loadedImagePianodirMetadata.disk_title
    for directory, records in groups.items():
        template = templates.get(directory, {})
        disk = template.get("PDISK.MNG", b"")
        metadata = SmartPianoSoftMetadata(song_catalog=template.get("PSONG.MNG", b""), disk_catalog=disk,
                                         pianodir_source=directory in pianodir_metadata)
        source_album = parse_smart_pianosoft_disk_title(disk) if disk else pianodir_metadata.get(directory, SmartPianoSoftMetadata()).disk_title
        title = album if len(groups) == 1 or album_edited else source_album
        for name, payload in _catalog_pair(metadata, records, title,
                                         generate_missing=catalog_generation_enabled(window)).items():
            result[catalog_paths.get((directory, name), os.path.join(directory, name).replace("\\", "/"))] = payload
    return result


def stage_image_catalogs(window, additions, replacements):
    for path, payload in image_catalog_payloads(window).items():
        source = next((source for source in window.imageEntriesByPath
                       if window._final_image_path(source).casefold() == path.casefold()), None)
        staged = Path(window.image_session.patched_dir, f"{uuid.uuid4().hex}_{os.path.basename(path)}")
        staged.write_bytes(payload)
        if source is not None:
            replacements[source] = str(staged)
        else:
            additions[path] = str(staged)
