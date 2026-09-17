"""Review and undo history for staged edits; never writes source songs."""

import copy
import json
import os
import shutil
import tempfile
from functools import wraps

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QLabel,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
)

from .localized_dialogs import QMessageBox
from .message_catalog import tr, translate_text
from .preparation_profiles import PREPARATION_SETTING_KEYS
from .conversion_review import ConversionReport, localize_music_error, localize_music_format
from .eseq_pianodir import PianodirMetadata


_STATE_FIELDS = (
    "pendingEdits", "pendingRegularConversions", "pendingRegularRenames",
    "pendingRegularOrderKeyEdits", "listedFileInfo", "regularEseqMode",
    "regularEseqVariant", "regularHasPianodir", "regularPianodirPopulated",
    "regularPianodirSourcePath", "loadedRegularEseqPaths",
    "pendingImageRenames", "pendingImageTitleEdits", "pendingImageDeletes",
    "pendingImageAdditions", "pendingImageReplacements", "pendingImageExportFilenames",
    "pendingSmartPianoSoftTitleEdits", "pendingSmartPianoSoftCatalogReplacement",
    "imageFileInfo", "imageEseqMode", "imageEseqVariant", "imageHasPianodir",
    "imagePianodirPopulated", "pendingGeneratePianodir", "pendingDeletePianodir",
    "pendingExportPianodirMetadata",
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
    def _pending_text(self, key, **fields):
        return tr("pending." + key, self._language_code(), **fields)

    def _init_pending_changes_ui(self, layout):
        self._staged_undo_stack = []
        self._staging_epoch = 0
        self._undo_all_requires_source_reset = False
        self._staged_metadata_edit = None
        self._restored_staging_assets = []
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

    def _capture_staged_state(self):
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
            "rows": [[QTableWidgetItem(self.table.item(row, col)) if self.table.item(row, col) else None
                      for col in range(self.table.columnCount())]
                     for row in range(self.table.rowCount())],
            "album": self.imagePianodirTitleEdit.text(),
            "catalog": self.imagePianodirCatalogEdit.text(),
        }

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

    def _clear_staging_history(self):
        self._invalidate_staged_undo()
        self._undo_all_requires_source_reset = False
        for assets in getattr(self, "_restored_staging_assets", []):
            assets.cleanup()
        self._restored_staging_assets = []

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
                        self.table.setItem(row, column, QTableWidgetItem(item))
            self.imagePianodirTitleEdit.setText(snapshot["album"])
            self.imagePianodirCatalogEdit.setText(snapshot["catalog"])
            self._restored_staging_assets.append(snapshot["assets"])
            self._refresh_after_pending_restore()
        finally:
            self._staging_depth = 0
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
                                ("editUndoAllAction", self._can_undo_all_staged_changes())):
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
        dialog = QDialog(self)
        dialog.setWindowTitle(self._pending_text("review"))
        dialog.resize(880, 520)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(0, 4, dialog)
        table.setHorizontalHeaderLabels([self._pending_text("original"), self._pending_text("proposed"),
                                         translate_text("Type", self._language_code()), self._pending_text("changes")])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(table)
        details = QTextEdit(dialog)
        details.setReadOnly(True)
        layout.addWidget(details)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, dialog)
        buttons.button(QDialogButtonBox.Close).setText(translate_text("Close", self._language_code()))
        discard = buttons.addButton(self._pending_text("discard"), QDialogButtonBox.ActionRole)
        undo = buttons.addButton(self._pending_text("undo"), QDialogButtonBox.ActionRole)
        layout.addWidget(buttons)
        review_rows = []

        def refresh():
            try:
                review_rows[:] = self._pending_review_rows()
            except Exception as exc:
                details.setPlainText(str(exc))
                return
            table.setRowCount(len(review_rows))
            for index, (path, original, proposed, kind, detail) in enumerate(review_rows):
                preview = detail.splitlines()[0] if detail else ""
                if len(preview) > 100:
                    preview = preview[:97] + "…"
                for column, value in enumerate((original, proposed, kind, preview)):
                    table.setItem(index, column, QTableWidgetItem(value))
            table.resizeColumnsToContents()
            for column, maximum in enumerate((260, 260, 140, 240)):
                table.setColumnWidth(column, min(table.columnWidth(column), maximum))
            table.horizontalHeader().setStretchLastSection(True)
            table.resizeRowsToContents()
            undo.setEnabled(bool(self._staged_undo))
            discard.setEnabled(False)

        def selection_changed():
            selected = sorted({index.row() for index in table.selectedIndexes()})
            discard.setEnabled(bool(selected))
            details.setPlainText("\n\n".join(review_rows[row][4] for row in selected))

        def discard_selected():
            selected = sorted({index.row() for index in table.selectedIndexes()})
            try:
                self.discard_staged_song_changes([review_rows[row][0] for row in selected])
            except Exception as exc:
                QMessageBox.warning(dialog, self._pending_text("review"), str(exc))
            refresh()

        table.itemSelectionChanged.connect(selection_changed)
        discard.clicked.connect(discard_selected)
        undo.clicked.connect(lambda: (self.undo_last_staged_batch(), refresh()))
        buttons.rejected.connect(dialog.reject)
        refresh()
        self._exec_child_dialog(dialog)

    @staged_batch
    def discard_staged_song_changes(self, paths):
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
                    material = self.image_session.extract_file(path)
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
        self._refresh_after_pending_restore()
