"""Deliver the current prepared song list through the emulator image builder."""

import os
import shutil
import tempfile

from PySide6.QtWidgets import QFileDialog, QProgressDialog

from .dos83_renamer import build_dos83_filename
from .emulator_image_builder import DEFAULT_SAFETY_MARGIN_BYTES, _catalog_song_matches
from .floppy_image import DISK_FORMAT_BY_KEY, FloppyImageError
from .midi_metadata import probe_midi_file_type
from .smart_pianosoft import (
    SMART_PIANOSOFT_DISK_CATALOG_NAME, SMART_PIANOSOFT_SONG_CATALOG_NAME,
    build_smart_pianosoft_song_catalog, build_smart_pianosoft_song_record,
    smart_pianosoft_metadata_from_directory,
)


def _prepared_catalog_sources(window, rows, temporary, image_mode):
    """Read catalogs beside the original songs, including staged image edits."""
    folders = {}
    for _row, source, _kind, _filename in rows:
        directory = os.path.dirname(source if image_mode else os.path.abspath(source))
        folders.setdefault(directory, []).append(source)
    metadata, matches = {}, {}
    for number, (directory, sources) in enumerate(folders.items()):
        if image_mode:
            # Keep snapshots hidden from song discovery. Each source directory
            # has its own catalog identity even if filenames repeat elsewhere.
            catalog_directory = os.path.join(temporary, ".catalog_sources", str(number))
            entries = set(window.imageEntriesByPath) | set(window.pendingImageAdditions)
            for path in entries - window.pendingImageDeletes:
                name = os.path.basename(path).upper()
                if os.path.dirname(path) == directory and name in {
                    SMART_PIANOSOFT_SONG_CATALOG_NAME, SMART_PIANOSOFT_DISK_CATALOG_NAME,
                }:
                    os.makedirs(catalog_directory, exist_ok=True)
                    shutil.copyfile(window._pending_or_extracted_image_path(path), os.path.join(catalog_directory, name))
            source_metadata = smart_pianosoft_metadata_from_directory(catalog_directory)
            by_name = {song.filename.casefold(): song for song in source_metadata.songs}
            matches.update({path: by_name[os.path.basename(path).casefold()] for path in sources
                            if os.path.basename(path).casefold() in by_name})
        else:
            # Conversion payloads can live in scratch space; the row's original
            # path still identifies its source album and opaque catalog fields.
            source_metadata = smart_pianosoft_metadata_from_directory(directory)
            matches.update(_catalog_song_matches(source_metadata.songs, sources))
        metadata[directory] = source_metadata
    return metadata, matches


def create_prepared_disk_images(window):
    """Stage the visible prepared songs, then start the existing reviewed build.

    The caller checks preparation and operation readiness. This entry point
    requires a concrete song format; manual/mixed-format sessions use the
    ordinary Save As Image command instead.
    """
    profile = window._preparation_profile()
    medium = window._preparation_medium()
    if profile.song_format not in {"midi", "eseq"}:
        return window.save_as_image()

    image_mode = window.is_image_mode()
    deleted = getattr(window, "pendingImageDeletes", set()) if image_mode else set()
    rows = [entry for entry in window._preparation_song_rows() if entry[1] not in deleted]
    if not rows:
        return None

    output_directory = QFileDialog.getExistingDirectory(
        window,
        window._t("emulator.select_output"),
        str(window.settings.value(window.SETTING_EMULATOR_IMAGE_OUTPUT, "") or "")
        or window._last_save_as_location(),
    )
    if not output_directory:
        return None

    temporary = tempfile.TemporaryDirectory(prefix="aps_prepared_images_")
    progress = QProgressDialog(window._t("emulator.progress.preparing"), None, 0, len(rows), window)
    window._prepare_progress_dialog(progress)
    progress.setAutoClose(False)
    progress.setCancelButton(None)
    worker = None
    window._set_disk_load_busy(True)
    try:
        order_edits = (window._image_eseq_order_key_edits() if image_mode
                       else window._regular_eseq_order_key_edits())
        catalog_metadata, catalog_matches = ({}, {})
        if profile.song_format == "midi":
            catalog_metadata, catalog_matches = _prepared_catalog_sources(window, rows, temporary.name, image_mode)
        carry_catalogs = any(item.song_catalog or item.disk_catalog for item in catalog_metadata.values())
        catalog_groups = []
        group_origin = None
        for number, (row, source_path, kind, filename) in enumerate(rows, start=1):
            if kind != profile.song_format:
                raise FloppyImageError(window._lt("Required format: {format}", format=profile.song_format.upper()))
            # Natural discovery order must match the table, including songs
            # with duplicate names from different folders. Do not build one
            # global PIANODIR: the builder creates a catalog for each disk.
            if carry_catalogs:
                origin = os.path.dirname(source_path if image_mode else os.path.abspath(source_path))
                # Consecutive album groups retain the exact visible order,
                # including interleaved albums and duplicate song filenames.
                if origin != group_origin or len(catalog_groups[-1][2]) == 999:
                    group_origin = origin
                    directory = os.path.join(temporary.name, f"{number:06d}")
                    os.makedirs(directory)
                    catalog_groups.append((directory, catalog_metadata[origin], []))
                staged_path = os.path.join(directory, build_dos83_filename(filename, number, extension="MID"))
            else:
                staged_path = os.path.join(temporary.name, f"{number:06d}_{os.path.basename(filename)}")
            window._apply_stage_progress(
                progress, number - 1, len(rows),
                window._lt("Preparing {filename} for image export...", filename=filename),
            )
            if image_mode:
                window._write_image_row_to_destination(
                    source_path, staged_path, order_key=order_edits.get(source_path),
                )
            else:
                title = window.pendingEdits.get(source_path, window._row_raw_title(row))
                error = window._write_listed_file_to_path(
                    source_path, title, staged_path,
                    order_key=order_edits.get(source_path),
                )
                if error:
                    raise FloppyImageError(error)
            if carry_catalogs:
                source_record = catalog_matches.get(source_path)
                title = source_record.title if source_record is not None else window._row_raw_title(row)
                if not image_mode and source_path in window.pendingEdits:
                    title = window.pendingEdits[source_path]
                elif image_mode and source_path in window.pendingImageTitleEdits:
                    title = window.pendingImageTitleEdits[source_path]
                catalog_groups[-1][2].append(build_smart_pianosoft_song_record(
                    os.path.basename(staged_path), title,
                    source_record=source_record.raw_record if source_record is not None else b"",
                    midi_format=probe_midi_file_type(staged_path),
                ))

        for directory, source_metadata, records in catalog_groups:
            if source_metadata.song_catalog:
                with open(os.path.join(directory, SMART_PIANOSOFT_SONG_CATALOG_NAME), "wb") as handle:
                    handle.write(build_smart_pianosoft_song_catalog(source_metadata.song_catalog, records))
            if source_metadata.disk_catalog:
                with open(os.path.join(directory, SMART_PIANOSOFT_DISK_CATALOG_NAME), "wb") as handle:
                    handle.write(source_metadata.disk_catalog)

        metadata = window._current_album_metadata_for_preservation()
        defaults = window._preparation_export_defaults()
        prefix = medium.image_prefix or str(
            window.settings.value(window.SETTING_EMULATOR_IMAGE_PREFIX, "DSKA") or "DSKA"
        )
        starting_number = int(window.settings.value(
            window.SETTING_EMULATOR_IMAGE_STARTING_NUMBER, medium.starting_number or 0,
        ) or 0)
        safety_margin = int(window.settings.value(
            window.SETTING_EMULATOR_IMAGE_SAFETY_MARGIN_KIB, DEFAULT_SAFETY_MARGIN_BYTES // 1024,
        ) or 0) * 1024
        disk_key = defaults.get("disk_format") or str(window.settings.value(
            window.SETTING_EMULATOR_IMAGE_DISK_FORMAT, "ibm.720",
        ))
        output_ext = medium.image_format or defaults.get("image_format") or str(
            window.settings.value(window.SETTING_EMULATOR_IMAGE_OUTPUT_FORMAT, "hfe") or "hfe"
        )
        progress.close()
        window.settings.setValue(window.SETTING_EMULATOR_IMAGE_OUTPUT, output_directory)
        window._start_emulator_image_build(
            temporary.name, output_directory,
            prefix=prefix,
            starting_number=starting_number,
            safety_margin_bytes=safety_margin,
            album_title=getattr(metadata, "disk_title", ""),
            catalog_number=getattr(metadata, "catalog_number", ""),
            output_content=profile.song_format,
            disk_format=DISK_FORMAT_BY_KEY[disk_key],
            output_ext=output_ext,
            include_subfolders=carry_catalogs,
            preserve_catalog_midi=True,
            shuffle=False,
            include_song_lists=False,
            disk_layout="fill",
        )
        worker = getattr(window, "emulatorImageWorker", None)
        if worker is not None:
            # Qt can hold bound methods weakly. Retain the TemporaryDirectory
            # in the callback itself so GC cannot remove a running build's input.
            worker.finished.connect(lambda staging=temporary: staging.cleanup())
            # Cover a build that finishes between start() and connection.
            if worker.isFinished():
                temporary.cleanup()
            temporary = None
        return worker
    except Exception as exc:
        progress.close()
        window._show_operation_error(
            window._t("emulator.failure.title"),
            window._t("emulator.failure.summary", path=output_directory),
            exc,
            guidance=window._t("emulator.failure.guidance"),
        )
        return None
    finally:
        progress.close()
        if temporary is not None:
            temporary.cleanup()
        if worker is None:
            window._set_disk_load_busy(False)
