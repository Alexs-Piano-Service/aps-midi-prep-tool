"""Apply Yamaha catalog metadata to the current staged song list."""

import os

from .catalog_application import CatalogCandidate, plan_catalog_application
from .catalog_inspection import read_catalog
from .midi_metadata import read_first_title_from_midi
from .pending_changes import staged_batch


def _folder(path):
    return os.path.normcase(os.path.dirname(os.path.abspath(path)))


class CatalogActionsMixin:
    def _catalog_song_candidates(self, song_paths=None):
        allowed = set(song_paths) if song_paths is not None else None
        candidates = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item is None or self._is_special_pianodir_row(row):
                continue
            path = item.text()
            if allowed is not None and path not in allowed:
                continue
            mode = (self._image_path_title_mode(path) if self.is_image_mode()
                    else self._listed_file_title_mode(path))
            if mode != "midi":
                continue
            filename = (self._final_image_path(path) if self.is_image_mode()
                        else self._regular_row_output_filename(row))
            candidates.append(CatalogCandidate(path, filename, aliases=(path,)))
        return candidates

    def _apply_catalog_plan(self, inspected, candidates):
        plan = plan_catalog_application(inspected, candidates)
        if inspected.kind == "PDISK.MNG":
            if not plan.album_title:
                raise ValueError(self._lt("The catalog contains no album title."))
            changed = self.imagePianodirTitleEdit.text() != plan.album_title
            self.imagePianodirTitleEdit.setText(plan.album_title)
            self._update_image_pianodir_metadata_ui()
            message = self._lt("Album title applied: {title}. Use Undo to revert.", title=plan.album_title)
            return {"applied": changed, "message": message}
        if not candidates:
            raise ValueError(self._lt("Load MIDI files before applying song titles."))

        rows = {self.table.item(row, 1).text(): row for row in range(self.table.rowCount())
                if self.table.item(row, 1) is not None}
        image_mode = self.is_image_mode()
        pending = self.pendingImageTitleEdits if image_mode else self.pendingEdits
        edits = []
        # Read and validate everything before altering the working list. A
        # catalog-backed display title is not proof the MIDI already embeds it.
        for update in plan.updates:
            path = update.key
            row = rows[path]
            error = self._validate_title_for_destination(update.title, "midi")
            if error:
                raise ValueError(f"{os.path.basename(path)}: {error}")
            material = (self._pending_or_extracted_image_path(path) if image_mode
                        else self._regular_source_material_path(path))
            actual_title = pending.get(path)
            if actual_title is None:
                actual_title = read_first_title_from_midi(material)
            if actual_title != update.title or self._row_raw_title(row) != update.title:
                edits.append((row, path, update.title))
        sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        try:
            for row, path, title in edits:
                pending[path] = title
                if image_mode:
                    # This explicit operation copies catalog titles into MIDI;
                    # ordinary Smart title editing can still edit the catalog.
                    self._image_info_for_path(path).pop("title_source", None)
                filename = self.table.item(row, 3).text()
                self.table.setItem(row, 4, self._make_title_item(
                    title, title_mode="midi", fallback_title=filename, edited=True,
                ))
                self._update_compat_indicator(row, title)
        finally:
            self.table.setSortingEnabled(sorting)
        if image_mode:
            self._reapply_image_centered_title_assumption()
            self._refresh_image_mode_action_state()
        else:
            self._reapply_regular_centered_title_assumption()
            self._refresh_regular_mode_action_state()
        messages = [self._lt(
            "Applied {count} song title(s). Use Save to write them, or Undo to revert.", count=len(edits),
        ) if edits else self._lt("No song titles needed changing.")]
        skipped = len(plan.missing_songs) + len(plan.ambiguous_songs) + len(plan.invalid_songs)
        if skipped:
            messages.append(self._lt(
                "Skipped {count} catalog record(s) without a unique matching MIDI file.", count=skipped,
            ))
        if plan.empty_title_songs:
            messages.append(self._lt("Skipped {count} empty catalog title(s).", count=len(plan.empty_title_songs)))
        return {"applied": bool(edits), "message": "\n".join(messages)}

    @staged_batch
    def _stage_inspected_catalog_action(self, item):
        if not self.choose_button.isEnabled():
            raise ValueError(self.status_label.text())
        session = item.get("session_id")
        if session is not None and (not self.is_image_mode() or session != id(self.image_session)):
            raise ValueError(self._lt("This catalog is no longer available in the current file list."))
        inspected = read_catalog(item["path"])
        candidates = self._catalog_song_candidates()
        source = item.get("source_path") or item["path"]
        # Prefer the catalog's own folder; a separately opened catalog can also
        # be deliberately applied to the current list by matching filenames.
        scoped = [candidate for candidate in candidates if _folder(candidate.key) == _folder(source)]
        if scoped:
            candidates = scoped
        result = self._apply_catalog_plan(inspected, candidates)
        self.status_label.setText(result["message"])
        return result

    @staged_batch
    def _apply_imported_catalogs(self, catalog_paths, *, song_paths):
        candidates = self._catalog_song_candidates(song_paths)
        if not candidates:
            return ""
        catalogs, messages = [], []
        for path in dict.fromkeys(catalog_paths):
            try:
                catalogs.append((path, read_catalog(path)))
            except (OSError, ValueError) as exc:
                messages.append(self._lt("Could not apply {filename}: {error}",
                                         filename=os.path.basename(path), error=self._lt(str(exc))))
        album_titles = {catalog.title for _, catalog in catalogs
                        if catalog.kind == "PDISK.MNG" and catalog.title}
        album_applied = False
        songs = [(path, catalog) for path, catalog in catalogs if catalog.kind == "PSONG.MNG"]
        for path, catalog in catalogs:
            if catalog.kind == "PDISK.MNG":
                if album_applied:
                    continue
                if not catalog.title and album_titles:
                    continue
                album_applied = True
                if len(album_titles) > 1:
                    messages.append(self._lt("Multiple catalogs specify different album titles. Apply one catalog to choose the album name."))
                    continue
                scoped = candidates
            else:
                scoped = ([candidate for candidate in candidates if _folder(candidate.key) == _folder(path)]
                          if not self.is_image_mode() else [])
                if not scoped:
                    if len(songs) > 1:
                        message = self._lt("Multiple catalogs cannot be matched to separate song folders. Apply them individually.")
                        if message not in messages:
                            messages.append(message)
                        continue
                    scoped = candidates
            try:
                messages.append(self._apply_catalog_plan(catalog, scoped)["message"])
            except (OSError, ValueError) as exc:
                messages.append(self._lt("Could not apply {filename}: {error}",
                                         filename=os.path.basename(path), error=self._lt(str(exc))))
        return "\n".join(messages)
