"""Review and undo history for staged edits; never writes source songs."""

import copy
import json
import os
import shutil
import tempfile
import uuid
from functools import wraps

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QLabel

from .localized_dialogs import QMessageBox
from .message_catalog import tr
from .pending_changes_dialog import PendingChangesDialog
from .preparation_profiles import PREPARATION_SETTING_KEYS
from .conversion_review import ConversionReport, localize_music_error, localize_music_format
from .drop_table_widget import zip_import_operation
from .eseq_pianodir import PianodirMetadata
from .preparation_layer import PREPARATION_ID_ROLE, PreparationLayer


_STATE_FIELDS = (
    "pendingEdits", "pendingRegularConversions", "pendingRegularRenames",
    "pendingRegularOrderKeyEdits", "listedFileInfo", "regularEseqMode",
    "regularEseqVariant", "regularHasPianodir", "regularPianodirPopulated",
    "regularPianodirSourcePath", "loadedRegularEseqPaths", "loadedRegularPianodirMetadata",
    "regularTitlesLikelyCentered",
    "pendingImageRenames", "pendingImageTitleEdits", "pendingImageDeletes",
    "pendingImageAdditions", "pendingImageReplacements", "pendingImageExportFilenames",
    "pendingSmartPianoSoftTitleEdits", "pendingSmartPianoSoftCatalogReplacement",
    "imageFileInfo", "imageEseqMode", "imageEseqVariant", "imageHasPianodir",
    "imagePianodirPopulated", "pendingGeneratePianodir", "pendingDeletePianodir",
    "pendingExportPianodirMetadata", "loadedImagePianodirMetadata", "imageTitlesLikelyCentered",
)


def staged_batch(method):
    """Nested staging helpers belong to the single outer user action."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if not getattr(self, "_pending_review_initialized", False) or getattr(self, "_staging_depth", 0):
            return method(self, *args, **kwargs)
        self._commit_staged_metadata_edit()
        snapshot = self._capture_staged_state()
        self._staging_depth = 1
        self._refresh_pending_changes_ui()
        try:
            return method(self, *args, **kwargs)
        finally:
            self._staging_depth = 0
            self._record_staged_snapshot(snapshot)
            self._refresh_pending_changes_ui()
            collect = getattr(getattr(self, "table", None), "collect_unused_zip_imports", None)
            if callable(collect):
                collect()
    return wrapped


def manual_midi_batch(method):
    """Run a row utility before automatic preparation, with one Undo action."""
    @staged_batch
    @wraps(method)
    def wrapped(self, rows, *args, **kwargs):
        run = getattr(self, "_run_manual_midi_edit", None)
        if run is None:
            return method(self, rows, *args, **kwargs)
        return run(rows, lambda selected: method(self, selected, *args, **kwargs))
    return wrapped


def manual_midi_inspection_action(method):
    """Adapt an inspection action's source identity to the same edit boundary."""
    @staged_batch
    @wraps(method)
    def wrapped(self, item, action):
        source = str(item.get("source_path") or "")
        row = next((row for row in range(self.table.rowCount())
                    if self.table.item(row, 1) is not None
                    and self.table.item(row, 1).text() == source), None)
        run = getattr(self, "_run_manual_midi_edit", None)
        if row is None or run is None:
            return method(self, item, action)

        def apply(selected):
            updated = dict(item)
            if selected:
                updated["source_path"] = selected[0][1]
            return method(self, updated, action)

        identity = self.table.item(row, 1).data(PREPARATION_ID_ROLE)
        result = run([(row, source)], apply)
        if identity and isinstance(result, dict) and "item" in result:
            current_path = next((self.table.item(index, 1).text()
                                 for index in range(self.table.rowCount())
                                 if self.table.item(index, 1) is not None
                                 and self.table.item(index, 1).data(PREPARATION_ID_ROLE) == identity), source)
            result["item"] = next((candidate for candidate in self._inspection_items()
                                   if candidate.get("source_path") == current_path), result["item"])
        return result
    return wrapped


class _MetadataUndoFilter(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window

    def eventFilter(self, editor, event):
        if event.type() in (QEvent.FocusIn, QEvent.KeyPress, QEvent.InputMethod, QEvent.MouseButtonPress):
            self.window._begin_staged_metadata_edit(editor)
        return False


class PendingChangesMixin:
    def _staged_zip_import_references(self):
        """Path-bearing current state and every source reachable through Undo."""
        for name in _STATE_FIELDS:
            yield getattr(self, name, None)
        snapshots = list(getattr(self, "_staged_undo_stack", ()))
        layers = list(getattr(self, "_preparation_layers", ()))
        current_layer = getattr(self, "_preparation_layer", None)
        if current_layer is not None:
            layers.append(current_layer)
        for layer in layers:
            snapshots.extend((layer.before, layer.after))
        metadata_edit = getattr(self, "_staged_metadata_edit", None)
        if metadata_edit:
            snapshots.append(metadata_edit[1])
        for snapshot in snapshots:
            yield snapshot.get("state", {})
            yield snapshot.get("copies", {})
            for row in snapshot.get("rows", ()):
                if len(row) > 1 and row[1] is not None:
                    yield row[1].text()

    def _pending_text(self, key, **fields):
        return tr("pending." + key, self._language_code(), **fields)

    def _init_pending_changes_ui(self, layout):
        self._staged_undo_stack = []
        self._staging_epoch = 0
        self._undo_all_requires_source_reset = False
        self._staged_metadata_edit = None
        self._restored_staging_assets = []
        self._preparation_layer = None
        self._preparation_layers = []
        self._pending_review_initialized = True
        self.saveDestinationLabel = QLabel()
        self.saveDestinationLabel.setWordWrap(True)
        self.saveDestinationLabel.setTextFormat(Qt.PlainText)
        layout.addWidget(self.saveDestinationLabel)
        self.saveDestinationLabel.setVisible(
            self.settings.value(self.SETTING_SHOW_SAVE_DESTINATION, False, type=bool)
        )

    @property
    def _staged_undo(self):
        stack = getattr(self, "_staged_undo_stack", [])
        return stack[-1] if stack else None

    def _connect_staged_metadata_edits(self):
        self._metadata_undo_filter = _MetadataUndoFilter(self)
        for editor in (self.imagePianodirTitleEdit, self.imagePianodirCatalogEdit):
            editor.installEventFilter(self._metadata_undo_filter)
            editor.editingFinished.connect(self._commit_staged_metadata_edit)
            editor.textEdited.connect(self._refresh_pending_changes_ui)

    def _begin_staged_metadata_edit(self, editor):
        if getattr(self, "_staging_depth", 0) or not getattr(self, "_pending_review_initialized", False):
            return
        pending = self._staged_metadata_edit
        if pending and pending[0] is not editor:
            self._commit_staged_metadata_edit()
        if self._staged_metadata_edit is None:
            self._staged_metadata_edit = (editor, self._capture_staged_state())

    def _commit_staged_metadata_edit(self):
        pending = getattr(self, "_staged_metadata_edit", None)
        if pending is None:
            return
        self._staged_metadata_edit = None
        self._record_staged_snapshot(pending[1])
        self._refresh_pending_changes_ui()

    def _record_staged_snapshot(self, snapshot):
        if (snapshot["session"] == id(self.image_session)
                and snapshot["epoch"] == self._staging_epoch
                and snapshot["signature"] != self._staged_signature()):
            self._staged_undo_stack.append(snapshot)
        else:
            snapshot["assets"].cleanup()

    def _can_undo_staged_changes(self):
        snapshot = self._staged_undo
        if snapshot and snapshot["session"] == id(self.image_session):
            return True
        pending = getattr(self, "_staged_metadata_edit", None)
        if pending:
            snapshot = pending[1]
            return (snapshot["session"] == id(self.image_session)
                    and snapshot["epoch"] == self._staging_epoch
                    and (snapshot["album"], snapshot["catalog"]) !=
                    (self.imagePianodirTitleEdit.text(), self.imagePianodirCatalogEdit.text()))
        return False

    def _pending_changes_to_discard(self):
        metadata = (self.loadedImagePianodirMetadata if self.is_image_mode()
                    else self.loadedRegularPianodirMetadata)
        current_metadata = (self._current_image_pianodir_metadata() if self.is_image_mode()
                            else self._current_regular_pianodir_metadata())
        return bool(
            self._pending_song_paths() or self.pendingGeneratePianodir or self.pendingDeletePianodir
            or current_metadata != metadata
            or getattr(self, "pendingSmartPianoSoftCatalogReplacement", "")
        )

    def _can_undo_all_staged_changes(self):
        return self._can_undo_staged_changes() or (
            getattr(self, "_undo_all_requires_source_reset", False) and self._pending_changes_to_discard()
        )

    def _staged_signature(self):
        state = {key: copy.deepcopy(getattr(self, key)) for key in _STATE_FIELDS if hasattr(self, key)}
        rows = tuple(
            tuple(self.table.item(row, col).text() if self.table.item(row, col) else ""
                  for col in (1, 3, 4, 6))
            for row in range(self.table.rowCount())
        )
        settings = {key: (self.settings.contains(key), self.settings.value(key))
                    for key in PREPARATION_SETTING_KEYS}
        return state, rows, self.imagePianodirTitleEdit.text(), self.imagePianodirCatalogEdit.text(), settings

    def _capture_staged_state(self, *, include_preparation_layer=True):
        # Paths of newly added image songs change during format conversion and
        # renaming. Item data survives both operations and snapshot restoration.
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item is not None and not item.data(PREPARATION_ID_ROLE):
                item.setData(PREPARATION_ID_ROLE, uuid.uuid4().hex)
        signature = self._staged_signature()
        assets = tempfile.TemporaryDirectory(prefix="aps_undo_")
        copies = {}
        material_paths = list(getattr(self, "pendingImageAdditions", {}).values())
        material_paths += list(getattr(self, "pendingImageReplacements", {}).values())
        material_paths += [item.get("temp_path") for item in getattr(self, "pendingRegularConversions", {}).values()]
        material_paths.append(getattr(self, "pendingSmartPianoSoftCatalogReplacement", ""))
        try:
            for source in dict.fromkeys(material_paths):
                if source and os.path.isfile(source):
                    target = os.path.join(assets.name, str(len(copies)) + "_" + os.path.basename(source))
                    shutil.copy2(source, target)
                    copies[source] = target
        except Exception:
            assets.cleanup()
            raise
        return {
            "signature": signature, "state": signature[0], "assets": assets, "copies": copies,
            "settings": signature[4],
            "session": id(self.image_session),
            "epoch": self._staging_epoch,
            "restore_sources_after_undo_all": (
                self._undo_all_requires_source_reset and self._pending_changes_to_discard()
            ),
            "rows": [[self.table.item(row, col).clone() if self.table.item(row, col) is not None else None
                      for col in range(self.table.columnCount())]
                     for row in range(self.table.rowCount())],
            "album": self.imagePianodirTitleEdit.text(),
            "catalog": self.imagePianodirCatalogEdit.text(),
            "preparation_layer": getattr(self, "_preparation_layer", None) if include_preparation_layer else None,
        }

    def _remove_preparation_layer(self):
        layer = getattr(self, "_preparation_layer", None)
        if layer is None:
            return
        self._commit_staged_metadata_edit()
        current = self._capture_staged_state(include_preparation_layer=False)
        try:
            rebased = layer.rebase(
                current, raw_title_role=self.TITLE_RAW_ROLE,
                edited_title_role=self.TITLE_EDITED_ROLE, image_mode=self.is_image_mode(),
            )
        except Exception:
            current["assets"].cleanup()
            raise
        self._restore_staged_snapshot(rebased)

    def _begin_preparation_layer(self):
        if not getattr(self, "_pending_review_initialized", False):
            return None
        self._remove_preparation_layer()
        return self._capture_staged_state(include_preparation_layer=False)

    def _finish_preparation_layer(self, before):
        if before is None:
            return
        after = self._capture_staged_state(include_preparation_layer=False)
        layer = PreparationLayer(before, after)
        self._preparation_layer = layer
        # Undo may still reference an older immutable layer. Their asset owners
        # are retained until session history is cleared, just like restored Undo.
        self._preparation_layers.append(layer)

    def _commit_preparation_paths(self, paths):
        layer = getattr(self, "_preparation_layer", None)
        if layer is None:
            return
        rows = [[self.table.item(row, column) for column in range(self.table.columnCount())]
                for row in range(self.table.rowCount())]
        self._preparation_layer = layer.without_paths(set(paths), rows)

    def _run_manual_midi_edit(self, rows, operation):
        """Apply MIDI edits to retained MIDI sources, then prepare their output.

        A MIDI-only utility still needs the prepared MIDI representation of a
        native E-SEQ source. Those selected rows keep that representation, and
        its explicitly edited bytes become their manual layer. Native MIDI
        songs retain their original track structure and metadata until the
        utility itself deliberately changes them.
        """
        layer = getattr(self, "_preparation_layer", None)
        if (layer is None or getattr(self, "_preparing_destination", False)
                or not self._preparation_profile().song_format):
            return operation(rows)
        # A dialog can retain row numbers while the table is reordered. Resolve
        # its source paths first so an edit never follows a new row occupant.
        current_rows = {self.table.item(row, 1).text(): row for row in range(self.table.rowCount())
                        if self.table.item(row, 1) is not None}
        rows = [(current_rows[path], path) for _row, path in rows if path in current_rows]
        selected = {self.table.item(row, 1).data(PREPARATION_ID_ROLE)
                    for row, _path in rows if self.table.item(row, 1) is not None}
        info_field = "imageFileInfo" if self.is_image_mode() else "listedFileInfo"
        native_midi = {
            row[1].data(PREPARATION_ID_ROLE)
            for row in layer.before["rows"] if row[1] is not None
            and layer.before["state"].get(info_field, {}).get(row[1].text(), {}).get("title_mode") == "midi"
        } & selected
        if not native_midi:
            return operation(rows)

        sorting_enabled = self.table.isSortingEnabled()
        header = self.table.horizontalHeader()
        sort_column, sort_order = header.sortIndicatorSection(), header.sortIndicatorOrder()
        prepared = self._capture_staged_state()
        excluded_paths = {row[1].text() for row in prepared["rows"] if row[1] is not None
                          and row[1].data(PREPARATION_ID_ROLE) not in native_midi}
        partial = layer.without_paths(excluded_paths, prepared["rows"])
        rebased = partial.rebase(
            prepared, raw_title_role=self.TITLE_RAW_ROLE,
            edited_title_role=self.TITLE_EDITED_ROLE, image_mode=self.is_image_mode(),
        )
        self._destination_preparation_queued = False
        self._restore_staged_snapshot(rebased)
        self._preparation_layer = layer
        remapped = [(row, self.table.item(row, 1).text()) for row in range(self.table.rowCount())
                    if self.table.item(row, 1) is not None
                    and self.table.item(row, 1).data(PREPARATION_ID_ROLE) in selected]
        before_edit = self._staged_signature()
        try:
            return operation(remapped)
        finally:
            status = self.status_label.text()
            if before_edit == self._staged_signature():
                # A canceled/no-op utility must not manufacture a manual edit
                # from the temporary removal of preparation. Its original
                # staged files still exist, so preserve their exact identities.
                unchanged = dict(prepared, copies={})
                self._restore_staged_snapshot(unchanged)
                header.setSortIndicator(sort_column, sort_order)
                self.table.setSortingEnabled(sorting_enabled)
            else:
                self._prepare_for_destination()
            self.status_label.setText(status)

    @zip_import_operation
    def _invalidate_staged_undo(self):
        self._staging_epoch = getattr(self, "_staging_epoch", 0) + 1
        self._undo_all_requires_source_reset = True
        for snapshot in getattr(self, "_staged_undo_stack", []):
            snapshot["assets"].cleanup()
        self._staged_undo_stack = []
        pending = getattr(self, "_staged_metadata_edit", None)
        if pending:
            pending[1]["assets"].cleanup()
        self._staged_metadata_edit = None

    @zip_import_operation
    def _clear_staging_history(self):
        self._invalidate_staged_undo()
        self._undo_all_requires_source_reset = False
        self._preparation_layer = None
        for layer in getattr(self, "_preparation_layers", []):
            layer.before["assets"].cleanup()
            layer.after["assets"].cleanup()
        self._preparation_layers = []
        for assets in getattr(self, "_restored_staging_assets", []):
            assets.cleanup()
        self._restored_staging_assets = []

    @zip_import_operation
    def undo_last_staged_batch(self):
        if getattr(self, "_staging_depth", 0):
            return
        self._commit_staged_metadata_edit()
        snapshot = self._staged_undo
        if not snapshot or snapshot["session"] != id(self.image_session):
            return
        self._staged_undo_stack.pop()
        self._restore_staged_snapshot(snapshot)
        self.status_label.setText(self._pending_text("undone"))

    @zip_import_operation
    def undo_all_staged_changes(self):
        if getattr(self, "_staging_depth", 0):
            return
        self._commit_staged_metadata_edit()
        if not self._can_undo_all_staged_changes():
            return
        snapshots = self._staged_undo_stack
        if snapshots and snapshots[0]["session"] != id(self.image_session):
            return
        restore_sources = (snapshots[0].get("restore_sources_after_undo_all", False) if snapshots
                           else self._undo_all_requires_source_reset)
        rollback = self._capture_staged_state() if restore_sources else None
        self._staged_undo_stack = []
        try:
            if snapshots:
                self._restore_staged_snapshot(snapshots[0])
            if restore_sources:
                self._discard_remaining_changes_after_save()
        except Exception as exc:
            if rollback is not None:
                self._restore_staged_snapshot(rollback)
            self._staged_undo_stack = snapshots
            self._refresh_pending_changes_ui()
            message = self._pending_text("undo_all_failed", error=str(exc))
            self.status_label.setText(message)
            QMessageBox.warning(self, self._pending_text("undo_all"), message)
            return
        if rollback is not None:
            rollback["assets"].cleanup()
        for snapshot in snapshots[1:]:
            snapshot["assets"].cleanup()
        self._undo_all_requires_source_reset = False
        self._refresh_pending_changes_ui()
        self.status_label.setText(self._pending_text("all_undone"))

    def _discard_remaining_changes_after_save(self):
        """Discard unfinished edits against the source files that exist now."""
        self._staging_depth = 1
        try:
            self.discard_staged_song_changes(sorted(self._pending_song_paths()))
            self.pendingGeneratePianodir = False
            self.pendingDeletePianodir = False
            self.pendingExportPianodirMetadata = PianodirMetadata()
            metadata = (self.loadedImagePianodirMetadata if self.is_image_mode()
                        else self.loadedRegularPianodirMetadata)
            self.imagePianodirTitleEdit.setText(metadata.disk_title)
            self.imagePianodirCatalogEdit.setText(metadata.catalog_number)
            self._refresh_after_pending_restore()
        finally:
            self._staging_depth = 0

    def _restore_staged_snapshot(self, snapshot):
        previous_depth = getattr(self, "_staging_depth", 0)
        state = copy.deepcopy(snapshot["state"])
        copies = snapshot["copies"]
        for field in ("pendingImageAdditions", "pendingImageReplacements"):
            if field in state:
                state[field] = {key: copies.get(path, path) for key, path in state[field].items()}
        for item in state.get("pendingRegularConversions", {}).values():
            item["temp_path"] = copies.get(item.get("temp_path"), item.get("temp_path"))
        catalog_path = state.get("pendingSmartPianoSoftCatalogReplacement", "")
        state["pendingSmartPianoSoftCatalogReplacement"] = copies.get(catalog_path, catalog_path)
        for key, value in state.items():
            setattr(self, key, value)
        self._preparation_layer = snapshot.get("preparation_layer")
        for key, (existed, value) in snapshot.get("settings", {}).items():
            if existed:
                self.settings.setValue(key, value)
            else:
                self.settings.remove(key)
        self._staging_depth = 1
        try:
            self.table.setSortingEnabled(False)
            self.table.setRowCount(0)
            self.table.setRowCount(len(snapshot["rows"]))
            for row, items in enumerate(snapshot["rows"]):
                for column, item in enumerate(items):
                    if item is not None:
                        self.table.setItem(row, column, item.clone())
            self.imagePianodirTitleEdit.setText(snapshot["album"])
            self.imagePianodirCatalogEdit.setText(snapshot["catalog"])
            self._restored_staging_assets.append(snapshot["assets"])
            self._refresh_after_pending_restore()
        finally:
            self._staging_depth = previous_depth
        self._refresh_pending_changes_ui()

    def _refresh_after_pending_restore(self):
        self._refresh_preparation_ui()
        screen_format = self.settings.value(self.SETTING_FORMAT_DISKLAVIER_SCREEN, False, type=bool)
        for name in ("format_disklavier_checkbox", "viewFormatDisklavierScreenAction"):
            control = getattr(self, name, None)
            if control is not None:
                blocked = control.blockSignals(True)
                control.setChecked(screen_format)
                control.blockSignals(blocked)
        action = getattr(self, "settingsUseDos83FilenamesAction", None)
        if action is not None:
            blocked = action.blockSignals(True)
            action.setChecked(self._dos83_filenames_enabled())
            action.blockSignals(blocked)
        if self.is_image_mode():
            self._refresh_pianodir_row()
            self._refresh_disk_usage_bars()
        else:
            self._refresh_regular_eseq_mode()
            self._refresh_regular_pianodir_row()
        self._refresh_image_title_display_items()
        self._refresh_regular_title_display_items()
        self._refresh_regular_mode_action_state()
        self._refresh_pending_changes_ui()

    def _pending_song_paths(self):
        if self.is_image_mode():
            fields = ("pendingImageRenames", "pendingImageTitleEdits", "pendingImageDeletes",
                      "pendingImageAdditions", "pendingImageReplacements", "pendingSmartPianoSoftTitleEdits")
            paths = set().union(*(set(getattr(self, key, {})) for key in fields))
            paths.update(self._image_eseq_order_key_edits())
            return {path for path in paths if os.path.basename(path).upper() not in
                    {"PIANODIR.FIL", "MUSIC.DIR", "PSONG.MNG", "PDISK.MNG"}}
        paths = set(self.pendingEdits) | set(self.pendingRegularConversions) | set(self.pendingRegularRenames)
        if self.is_local_eseq_mode():
            paths.update(self._regular_eseq_order_key_edits())
        return paths

    def _refresh_pending_changes_ui(self, *_signal_args):
        refresh_overlap = getattr(self, "_refresh_overlap_repair_state", None)
        if refresh_overlap is not None:
            refresh_overlap()
        if not getattr(self, "_pending_review_initialized", False):
            return
        count = len(self._pending_song_paths())
        label = self._pending_text("count", count=count) if count else self._pending_text("none")
        if getattr(self, "pendingGeneratePianodir", False) or getattr(self, "pendingDeletePianodir", False):
            label += " — " + self._pending_text("catalog")
        can_undo = self._can_undo_staged_changes()
        busy = (bool(getattr(self, "_staging_depth", 0)) or
                (getattr(self, "choose_button", None) is not None and not self.choose_button.isEnabled()))
        for name, available in (("editUndoAction", can_undo),
                                ("editUndoAllAction", self._can_undo_all_staged_changes()),
                                ("editResetPreparationAction", bool(getattr(self, "_preparation_layer", None)
                                    or self._preparation_profile().song_format))):
            action = getattr(self, name, None)
            if action is not None:
                action.setEnabled(available and not busy)
        review_action = getattr(self, "editReviewChangesAction", None)
        if review_action is not None:
            review_action.setEnabled(not busy)
            review_action.setToolTip(label)
            review_action.setStatusTip(label)
        if self.image_session is not None:
            destination = self.image_session.source_path or self.image_session.source_name
            destination_detail = destination
        else:
            directories = sorted({os.path.dirname(path) for path in self.listedFileInfo})
            destination_detail = "; ".join(directories) or self.regularModeContextPath
            destination = destination_detail
            if len(directories) > 1:
                try:
                    common = os.path.commonpath(directories)
                except ValueError:
                    common = directories[0]
                destination = self._lt("{path} ({count} folders)", path=common, count=len(directories))
        text = self._pending_text("destination", destination=destination or "—")
        if hasattr(self, "saveButton") and not self.saveButton.isEnabled():
            text += "\n" + self._pending_text("disabled", reason=self.saveButton.toolTip())
        self.saveDestinationLabel.setText(text)
        self.saveDestinationLabel.setToolTip(destination_detail)

    def _pending_review_rows(self):
        rows = []
        order_rows = self._image_eseq_rows() if self.is_image_mode() else self._regular_eseq_rows()
        current_order = [self.table.item(row, 1).text() for row in order_rows]
        original_order = sorted(current_order, key=self._original_song_order_key)
        for path in sorted(self._pending_song_paths()):
            inspection_error = ""
            original_kind = ""
            if self.is_image_mode():
                info = self.imageFileInfo.get(path, {})
                proposed = self.pendingImageRenames.get(path, path)
                title = self.pendingSmartPianoSoftTitleEdits.get(path, self.pendingImageTitleEdits.get(path, info.get("title", "")))
                report = info.get("change_report")
                if path in self.pendingImageAdditions:
                    original = "—"
                    original_title = "—"
                else:
                    original = os.path.basename(path)
                    try:
                        original_title, original_kind, mode, _midi, _key = self._probe_regular_file(self.image_session.extract_file(path))
                        original_kind = original_kind or mode.upper()
                    except Exception as exc:
                        original_title = "—"
                        inspection_error = self._lt("Could not inspect original: {error}", error=localize_music_error(exc, self._language_code()))
                    if self._image_title_is_smart_pianosoft_catalog_backed(path):
                        song = self.smartPianoSoftCatalog.get(os.path.basename(path).casefold())
                        if song is not None:
                            original_title = song.title
                if path in self.pendingImageDeletes:
                    proposed = "—"
                    title = "—"
                report_error = info.get("change_report_error", "")
            else:
                info = self.listedFileInfo.get(path, {})
                original = os.path.basename(path)
                try:
                    original_title, original_kind, mode, _midi, _key = self._probe_regular_file(path)
                    original_kind = original_kind or mode.upper()
                except Exception as exc:
                    original_title = "—"
                    inspection_error = self._lt("Could not inspect original: {error}", error=localize_music_error(exc, self._language_code()))
                proposed = self._regular_output_filename_for_path(path)
                title = self.pendingEdits.get(path, info.get("title", ""))
                report = self.pendingRegularConversions.get(path, {}).get("change_report")
                report_error = self.pendingRegularConversions.get(path, {}).get("change_report_error", "")
            if isinstance(report, dict):
                try:
                    detail = ConversionReport.from_dict(report).to_text(self._language_code())
                except (TypeError, ValueError, KeyError):
                    detail = report.get("text") or json.dumps(report, indent=2, ensure_ascii=False, default=str)
            else:
                detail = report.to_text(self._language_code()) if hasattr(report, "to_text") else str(report or "")
            detail = "\n".join(part for part in (detail, localize_music_error(report_error or "", self._language_code()), inspection_error) if part)
            if path in current_order and current_order.index(path) != original_order.index(path):
                order_detail = self._lt("Playback order: {before} → {after}.",
                                        before=original_order.index(path) + 1, after=current_order.index(path) + 1)
                detail = "\n".join(part for part in (detail, order_detail) if part)
            kind = info.get("midi_type") or info.get("title_mode", "").upper()
            if original_kind and original_kind != kind:
                kind = (localize_music_format(original_kind, self._language_code()) + " → "
                        + (localize_music_format(kind, self._language_code()) or "—"))
            else:
                kind = localize_music_format(kind, self._language_code())
            rows.append((path, original + "\n" + str(original_title), os.path.basename(proposed) + "\n" + title,
                         kind, detail))
        return rows

    def _original_song_order_key(self, path):
        info = (self.imageFileInfo if self.is_image_mode() else self.listedFileInfo).get(path, {})
        song = getattr(self, "smartPianoSoftCatalog", {}).get(os.path.basename(path).casefold())
        if song is not None:
            return (0, int(song.track_number), b"", path.casefold())
        mode = info.get("title_mode", "")
        return (1 if mode == "eseq" else 2, 0,
                info.get("order_key", b"") if mode == "eseq" else b"", path.casefold())

    def _restore_selected_song_positions(self, paths):
        """Restore selected slots while preserving other songs' relative order."""
        if not self.is_image_mode() and not self.is_local_eseq_mode():
            return
        managed = {"PIANODIR.FIL", "MUSIC.DIR", "PSONG.MNG", "PDISK.MNG"}
        slots = [row for row in range(self.table.rowCount())
                 if not self._is_special_pianodir_row(row) and self.table.item(row, 1)
                 and os.path.basename(self.table.item(row, 1).text()).upper() not in managed]
        current = [self.table.item(row, 1).text() for row in slots]
        selected = set(paths) & set(current)
        if not selected:
            return

        original = sorted(current, key=self._original_song_order_key)
        fixed = {index: path for index, path in enumerate(original) if path in selected}
        remaining = iter(path for path in current if path not in selected)
        desired = [fixed[index] if index in fixed else next(remaining) for index in range(len(current))]
        for slot, path in zip(slots, desired):
            row = next(index for index in range(self.table.rowCount())
                       if self.table.item(index, 1) and self.table.item(index, 1).text() == path)
            self._move_table_row(row, slot)

    def show_pending_changes(self):
        self._commit_staged_metadata_edit()
        dialog = PendingChangesDialog(self)
        try:
            self._exec_child_dialog(dialog, resize_to_contents=False)
        finally:
            dialog.deleteLater()

    @staged_batch
    def discard_staged_song_changes(self, paths):
        paths = tuple(paths)
        self.table.setSortingEnabled(False)
        for path in paths:
            row = next((r for r in range(self.table.rowCount())
                        if self.table.item(r, 1) and self.table.item(r, 1).text() == path), -1)
            if self.is_image_mode():
                if path in self.pendingImageAdditions:
                    self.pendingImageAdditions.pop(path)
                    self.imageFileInfo.pop(path, None)
                    if row >= 0:
                        self.table.removeRow(row)
                else:
                    identity = (self.table.item(row, 1).data(PREPARATION_ID_ROLE)
                                if row >= 0 else None)
                    material = self.image_session.extract_file(path)
                    # Probing consults the staged filename when deciding which
                    # container to inspect. Remove its automatic suffix before
                    # reading the immutable image entry again.
                    for field in ("pendingImageRenames", "pendingImageTitleEdits",
                                  "pendingImageReplacements", "pendingImageExportFilenames"):
                        getattr(self, field).pop(path, None)
                    self.imageFileInfo.pop(path, None)
                    self._probe_image_file(path, os.path.getsize(material), material)
                    self.pendingImageDeletes.discard(path)
                    if path in self.pendingSmartPianoSoftTitleEdits:
                        self.pendingSmartPianoSoftTitleEdits.pop(path)
                        remaining = dict(self.pendingSmartPianoSoftTitleEdits)
                        self.pendingSmartPianoSoftTitleEdits.clear()
                        self.pendingImageReplacements.pop(self.smartPianoSoftCatalogPath, None)
                        self.pendingSmartPianoSoftCatalogReplacement = ""
                        for other, title in remaining.items():
                            self._stage_smart_pianosoft_catalog_title(other, title)
                    if row >= 0:
                        self.table.removeRow(row)
                    info = self.imageFileInfo[path]
                    song = self.smartPianoSoftCatalog.get(os.path.basename(path).casefold())
                    if info.get("is_midi") and song is not None and song.title:
                        info["title"] = song.title
                        info["title_source"] = "smart_pianosoft_catalog"
                        info["smart_pianosoft_track"] = song.track_number
                    self.add_image_table_row(path, os.path.basename(path), info.get("size", 0),
                                             title=info.get("title", ""), midi_type=info.get("midi_type", ""),
                                             order_key=info.get("order_key", b""))
                    if identity:
                        self.table.item(self.table.rowCount() - 1, 1).setData(PREPARATION_ID_ROLE, identity)
                    if row >= 0 and row != self.table.rowCount() - 1:
                        self._move_table_row(self.table.rowCount() - 1, row)
                for field in ("pendingImageRenames", "pendingImageTitleEdits", "pendingImageReplacements", "pendingImageExportFilenames"):
                    getattr(self, field).pop(path, None)
            else:
                title, midi_type, title_mode, is_midi, order_key = self._probe_regular_file(path)
                for field in ("pendingEdits", "pendingRegularConversions", "pendingRegularRenames", "pendingRegularOrderKeyEdits"):
                    getattr(self, field).pop(path, None)
                self._set_listed_file_info(path, title=title, midi_type=midi_type, title_mode=title_mode,
                                           is_midi=is_midi, order_key=order_key)
                if row >= 0:
                    self.table.item(row, 3).setText(os.path.basename(path))
                    self.table.item(row, 3).setToolTip("")
                    self.table.setItem(row, 4, self._make_title_item(title, title_mode=title_mode, fallback_title=os.path.basename(path)))
                    self._update_midi_type_indicator(row, midi_type)
                    self._update_compat_indicator(row, title)
        self._restore_selected_song_positions(paths)
        # Explicit discard establishes the actual source as this song's new
        # baseline; earlier manual edits must not revive on the next switch.
        self._commit_preparation_paths(paths)
        self._refresh_after_pending_restore()
