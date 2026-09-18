import os
import tempfile

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QProgressDialog, QTableWidget

from .localized_dialogs import QMessageBox
from .floppy_image import is_supported_image_path
from .ui_utils import center_dialog_on_parent
from .zip_import import ZipImportCancelled, ZipImportError, extract_zip


class DropTableWidget(QTableWidget):
    def __init__(self, rows, columns, parent=None):
        super().__init__(rows, columns, parent)
        self.setAcceptDrops(True)
        self._drag_invite_active = False
        self._drag_urls_supported_for_current_drag = None
        # Keep archive sources alive through conversion, undo, and image saves.
        # They have a separate lifetime from the window's conversion scratch space.
        self._zip_imports = []
        self._zip_import_generation = 0

    def _lt(self, text):
        window = self.window()
        if window is self:
            return text
        translator = getattr(window, "_lt", None)
        if callable(translator):
            return translator(text)
        return text

    def _set_drag_invite_active(self, active):
        active = bool(active)
        if self._drag_invite_active == active:
            return
        self._drag_invite_active = active
        self.viewport().update()

    def _scale_factor(self):
        window = self.window()
        scale_getter = getattr(window, "_layout_scale_factor", None)
        if callable(scale_getter):
            try:
                return max(0.5, float(scale_getter()))
            except Exception:
                pass
        return self._font_scale_factor()

    def _font_scale_factor(self):
        window = self.window()
        scale_getter = getattr(window, "_font_scale_factor", None)
        if callable(scale_getter):
            try:
                return max(0.5, float(scale_getter()))
            except Exception:
                pass
        return 1.0

    def _scaled_int(self, value, *, minimum=0):
        try:
            scaled = int(round(float(value) * self._scale_factor()))
        except (TypeError, ValueError):
            scaled = int(minimum)
        return max(int(minimum), scaled)

    def _scaled_font(self, *, bold=False, extra_points=0, minimum=8):
        font = QFont(self.font())
        point_size = font.pointSizeF()
        if point_size <= 0:
            point_size = QApplication.font().pointSizeF()
        if point_size <= 0:
            point_size = float(minimum)
        point_size += float(extra_points) * self._font_scale_factor()
        font.setPointSizeF(max(float(minimum), point_size))
        font.setBold(bool(bold))
        return font

    def setRowCount(self, rows):
        super().setRowCount(rows)
        self.viewport().update()

    def insertRow(self, row):
        super().insertRow(row)
        self.viewport().update()

    def removeRow(self, row):
        super().removeRow(row)
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.rowCount() > 0 and not self._drag_invite_active:
            return

        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing, True)
        margin = self._scaled_int(18, minimum=8)
        rect = self.viewport().rect().adjusted(margin, margin, -margin, -margin)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        palette = self.palette()
        accent = palette.highlight().color()
        base_text = QColor(palette.text().color())
        subtitle_color = QColor(base_text)
        subtitle_color.setAlpha(230)
        if self._drag_invite_active:
            fill = QColor(accent)
            fill.setAlpha(176)
            border = QColor(accent)
            border.setAlpha(255)
            text_color = base_text
            title = self._lt("Drop to import")
            subtitle = self._lt("MIDI, E-SEQ, ZIP, IMG, HFE, SCP, and other disk images")
        else:
            fill = palette.base().color()
            fill.setAlpha(238)
            border = QColor(base_text)
            border.setAlpha(180)
            text_color = base_text
            title = self._lt("Drop files or disk images here")
            subtitle = self._lt("MIDI, E-SEQ, ZIP, IMG, HFE, SCP, and other disk images")

        card = QRect(rect)
        if self._drag_invite_active:
            painter.fillRect(self.viewport().rect(), fill)
            painter.setBrush(Qt.NoBrush)
        else:
            painter.setBrush(fill)
        border_pen = QPen(border, max(1.0, 2.0 * self._scale_factor()) if self._drag_invite_active else max(1.0, 1.5 * self._scale_factor()))
        border_pen.setStyle(Qt.CustomDashLine)
        border_pen.setDashPattern([1, max(3, self._scaled_int(5, minimum=3))])
        border_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(border_pen)
        radius = self._scaled_int(8, minimum=5)
        painter.drawRoundedRect(card, radius, radius)

        title_font = self._scaled_font(bold=True, extra_points=3, minimum=10)
        painter.setFont(title_font)
        painter.setPen(text_color)
        content_padding = self._scaled_int(36, minimum=16)
        text_gap = self._scaled_int(12, minimum=6)
        title_height = self._scaled_int(54, minimum=36)
        subtitle_height = self._scaled_int(44, minimum=30)
        combined_height = title_height + text_gap + subtitle_height
        content = card.adjusted(content_padding, content_padding, -content_padding, -content_padding)
        top = content.center().y() - combined_height // 2
        top = max(content.top(), min(top, content.bottom() - combined_height + 1))
        title_rect = QRect(content.left(), top, content.width(), title_height)
        painter.drawText(title_rect, Qt.AlignCenter | Qt.TextWordWrap, title)

        subtitle_font = self._scaled_font(minimum=8)
        painter.setFont(subtitle_font)
        painter.setPen(text_color if self._drag_invite_active else subtitle_color)
        subtitle_rect = QRect(
            content.left(),
            title_rect.bottom() + 1 + text_gap,
            content.width(),
            subtitle_height,
        )
        painter.drawText(subtitle_rect, Qt.AlignCenter | Qt.TextWordWrap, subtitle)

    def file_exists(self, file_path):
        """Return True if a row already contains this file (full path is stored in column 1)."""
        for i in range(self.rowCount()):
            item = self.item(i, 1)
            if item and item.text() == file_path:
                return True
        return False

    def _can_accept_drag_path(self, main_window, file_path):
        if not file_path:
            return False
        if self._is_zip_path(file_path):
            return True
        if self._safe_is_supported_image_path(file_path):
            return True
        if self._safe_main_window_path_check(main_window, "can_accept_electone_evt_path", file_path):
            return True
        if self._safe_main_window_path_check(main_window, "can_accept_v50_nseq_path", file_path):
            return True
        if self._safe_main_window_path_check(main_window, "can_accept_psr600_blk_path", file_path):
            return True
        if self._safe_main_window_path_check(main_window, "can_accept_mpc_seq_path", file_path):
            return True
        if self._safe_main_window_call(main_window, "is_image_mode") and self._safe_is_file(file_path):
            return True
        return self._safe_main_window_path_check(main_window, "can_accept_regular_drop_path", file_path)

    @staticmethod
    def _is_zip_path(path):
        return os.path.splitext(path)[1].lower() == ".zip"

    def zip_source_for_path(self, path):
        if not path:
            return ""
        path = os.path.normcase(os.path.abspath(path))
        for directory, archive_path in self._zip_imports:
            root = os.path.normcase(os.path.abspath(directory.name))
            try:
                if os.path.commonpath((root, path)) == root:
                    return archive_path
            except ValueError:
                continue
        return ""

    def cleanup_zip_imports(self):
        # Invalidate an extraction paused in processEvents before cleaning up.
        self._zip_import_generation += 1
        for directory, _archive_path in self._zip_imports:
            directory.cleanup()
        self._zip_imports.clear()

    def _expand_zip_paths(self, main_window, paths):
        if not any(self._is_zip_path(path) for path in paths):
            return paths

        progress = QProgressDialog(
            self._lt("Extracting ZIP files..."), self._lt("Cancel"), 0, 0, main_window,
        )
        progress.setWindowTitle(self._lt("Extracting ZIP Files"))
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        center_dialog_on_parent(progress, main_window)
        expanded = []
        imports = []
        errors = []
        generation = self._zip_import_generation

        def cancelled():
            return generation != self._zip_import_generation or progress.wasCanceled()

        def report_progress(completed, total):
            progress.setRange(0, max(1, total))
            progress.setValue(completed)
            QApplication.processEvents()

        try:
            for path in paths:
                if not self._is_zip_path(path):
                    expanded.append(path)
                    continue
                directory = tempfile.TemporaryDirectory(prefix="aps_zip_import_")
                try:
                    members = extract_zip(
                        path, directory.name,
                        progress=report_progress,
                        is_cancelled=cancelled,
                    )
                    # Keep companion metadata on disk, but only import files the
                    # active workflow can use. ZIP files are expanded once.
                    accepted = [
                        member for member in members
                        if not self._is_zip_path(member)
                        and self._can_accept_drag_path(main_window, member)
                    ]
                    if not accepted:
                        errors.append(
                            f"{os.path.basename(path)}: "
                            + self._lt("No supported files were found in the ZIP file.")
                        )
                        directory.cleanup()
                        continue
                    imports.append((directory, os.path.abspath(path)))
                    expanded.extend(accepted)
                except ZipImportCancelled:
                    directory.cleanup()
                    for previous, _archive_path in imports:
                        previous.cleanup()
                    status_label = getattr(main_window, "status_label", None)
                    if status_label is not None:
                        status_label.setText(self._lt("Drop cancelled."))
                    return None
                except Exception as exc:
                    directory.cleanup()
                    detail = self._lt(str(exc)) if isinstance(exc, ZipImportError) else str(exc)
                    errors.append(
                        f"{os.path.basename(path)}: "
                        + self._lt("Could not extract ZIP file: {error}").format(error=detail)
                    )
            self._zip_imports.extend(imports)
        finally:
            progress.close()

        if errors:
            show_errors = getattr(main_window, "_show_error_list", None)
            if callable(show_errors):
                show_errors(
                    "Some Files Were Not Added",
                    "Some dropped files could not be added to the list",
                    errors,
                    warning=True,
                    guidance="Unsupported or unreadable files were skipped; the files already added remain staged",
                )
            else:
                self._show_drop_exception(main_window, ValueError("\n".join(errors)))
        return expanded if generation == self._zip_import_generation else None

    def _safe_is_supported_image_path(self, file_path):
        try:
            return bool(file_path and is_supported_image_path(file_path))
        except Exception:
            return False

    def _safe_is_file(self, file_path):
        try:
            return bool(file_path and os.path.isfile(file_path))
        except Exception:
            return False

    def _safe_main_window_call(self, main_window, method_name):
        method = getattr(main_window, method_name, None)
        if not callable(method):
            return False
        try:
            return bool(method())
        except Exception:
            return False

    def _safe_main_window_path_check(self, main_window, method_name, file_path):
        method = getattr(main_window, method_name, None)
        if not callable(method):
            return False
        try:
            return bool(method(file_path))
        except Exception:
            return False

    def _local_paths_from_urls(self, urls):
        local_paths = []
        for url in urls:
            try:
                file_path = url.toLocalFile()
            except Exception:
                continue
            if file_path:
                local_paths.append(file_path)
        return local_paths

    def _safe_accept_event(self, event):
        try:
            event.acceptProposedAction()
        except Exception:
            pass

    def _safe_ignore_event(self, event):
        try:
            event.ignore()
        except Exception:
            pass

    def _show_drop_exception(self, main_window, exc):
        detail = str(exc).strip() or repr(exc)
        self._log_drop_error(main_window, "Failed", detail=detail)
        show_operation_error = getattr(main_window, "_show_operation_error", None)
        if callable(show_operation_error):
            try:
                show_operation_error(
                    "Drop Failed",
                    "The dropped files could not be added.",
                    detail,
                    guidance="Try using File > Open MIDI Folder... or File > Open Image... if Windows will not provide the dropped path.",
                )
                return
            except Exception:
                pass
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Warning)
        dialog.setWindowTitle(self._lt("Drop Failed"))
        dialog.setText(self._lt("The dropped files could not be added.") + f"\n\n{detail}")
        dialog.setStandardButtons(QMessageBox.Ok)
        dialog.button(QMessageBox.Ok).setText(self._lt("OK"))
        dialog.exec()

    def _log_drop_event(self, main_window, action, **details):
        logger = getattr(main_window, "_log_event", None)
        if callable(logger):
            try:
                logger("Drag and drop", action, **details)
            except Exception:
                pass

    def _log_drop_error(self, main_window, action, **details):
        logger = getattr(main_window, "_log_error_event", None)
        if callable(logger):
            try:
                logger("Drag and drop", action, **details)
            except Exception:
                pass

    def _summarize_drop_results(self, results):
        summary = {}
        for result in results or ():
            if not result:
                continue
            status = str(result.get("status") or "unknown")
            summary[status] = summary.get(status, 0) + 1
        return summary

    def _drag_event_has_supported_urls(self, event):
        try:
            mime_data = event.mimeData()
            return bool(mime_data is not None and mime_data.hasUrls())
        except Exception:
            return False

    def dragEnterEvent(self, event):
        supported = self._drag_event_has_supported_urls(event)
        self._drag_urls_supported_for_current_drag = supported
        if supported:
            self._set_drag_invite_active(True)
            event.acceptProposedAction()
            return
        self._set_drag_invite_active(False)
        event.ignore()

    def dragMoveEvent(self, event):
        supported = self._drag_urls_supported_for_current_drag
        if supported is None:
            supported = self._drag_event_has_supported_urls(event)
            self._drag_urls_supported_for_current_drag = supported
        if supported:
            self._set_drag_invite_active(True)
            event.acceptProposedAction()
        else:
            self._set_drag_invite_active(False)
            event.ignore()

    def dragLeaveEvent(self, event):
        self._drag_urls_supported_for_current_drag = None
        self._set_drag_invite_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._drag_urls_supported_for_current_drag = None
        self._set_drag_invite_active(False)
        progressDialog = None
        main_window = self.window()
        generation = self._zip_import_generation
        try:
            mime_data = event.mimeData()
            has_urls = mime_data is not None and mime_data.hasUrls()
        except Exception as exc:
            self._show_drop_exception(main_window, exc)
            self._safe_accept_event(event)
            return
        if has_urls:
            try:
                local_paths = self._local_paths_from_urls(mime_data.urls())
                self._log_drop_event(
                    main_window,
                    "Files dropped",
                    count=len(local_paths),
                    first=os.path.basename(local_paths[0]) if local_paths else "",
                    mode="image" if self._safe_main_window_call(main_window, "is_image_mode") else "midi",
                )
                local_paths = self._expand_zip_paths(main_window, local_paths)
                if not local_paths:
                    self._safe_accept_event(event)
                    return
                image_paths = [path for path in local_paths if self._safe_is_supported_image_path(path)]

                if image_paths and hasattr(main_window, "load_image_file"):
                    self._log_drop_event(main_window, "Opening disk image", path=image_paths[0])
                    main_window.load_image_file(image_paths[0])
                    self._safe_accept_event(event)
                    return

                electone_evt_paths = [
                    path
                    for path in local_paths
                    if self._safe_main_window_path_check(main_window, "can_accept_electone_evt_path", path)
                ]
                if electone_evt_paths and hasattr(main_window, "handle_electone_evt_file_drop"):
                    handled = main_window.handle_electone_evt_file_drop(local_paths)
                    if handled:
                        self._safe_accept_event(event)
                        return
                    electone_evt_path_set = {os.path.abspath(path) for path in electone_evt_paths}
                    local_paths = [
                        path
                        for path in local_paths
                        if os.path.abspath(path) not in electone_evt_path_set
                    ]
                    if not local_paths:
                        self._safe_accept_event(event)
                        return

                v50_nseq_paths = [
                    path
                    for path in local_paths
                    if self._safe_main_window_path_check(main_window, "can_accept_v50_nseq_path", path)
                ]
                if v50_nseq_paths and hasattr(main_window, "handle_v50_nseq_file_drop"):
                    handled = main_window.handle_v50_nseq_file_drop(local_paths)
                    if handled:
                        self._safe_accept_event(event)
                        return
                    v50_nseq_path_set = {os.path.abspath(path) for path in v50_nseq_paths}
                    local_paths = [
                        path
                        for path in local_paths
                        if os.path.abspath(path) not in v50_nseq_path_set
                    ]
                    if not local_paths:
                        self._safe_accept_event(event)
                        return

                psr600_blk_paths = [
                    path
                    for path in local_paths
                    if self._safe_main_window_path_check(
                        main_window,
                        "can_accept_psr600_blk_path",
                        path,
                    )
                ]
                if (
                    psr600_blk_paths
                    and hasattr(main_window, "handle_psr600_blk_file_drop")
                ):
                    handled = main_window.handle_psr600_blk_file_drop(
                        local_paths
                    )
                    if handled:
                        self._safe_accept_event(event)
                        return
                    psr600_blk_path_set = {
                        os.path.abspath(path)
                        for path in psr600_blk_paths
                    }
                    local_paths = [
                        path
                        for path in local_paths
                        if os.path.abspath(path)
                        not in psr600_blk_path_set
                    ]
                    if not local_paths:
                        self._safe_accept_event(event)
                        return

                mpc_seq_paths = [
                    path
                    for path in local_paths
                    if self._safe_main_window_path_check(main_window, "can_accept_mpc_seq_path", path)
                ]
                if mpc_seq_paths and hasattr(main_window, "handle_mpc_seq_file_drop"):
                    handled = main_window.handle_mpc_seq_file_drop(local_paths)
                    if handled:
                        self._safe_accept_event(event)
                        return
                    mpc_seq_path_set = {os.path.abspath(path) for path in mpc_seq_paths}
                    local_paths = [
                        path
                        for path in local_paths
                        if os.path.abspath(path) not in mpc_seq_path_set
                    ]
                    if not local_paths:
                        self._safe_accept_event(event)
                        return

                if self._safe_main_window_call(main_window, "is_image_mode"):
                    if hasattr(main_window, "queue_image_additions"):
                        main_window.queue_image_additions(local_paths)
                    self._log_drop_event(
                        main_window,
                        "Queued image additions",
                        count=len(local_paths),
                    )
                    self._safe_accept_event(event)
                    return

                regular_paths = list(local_paths)
                results = []
                if hasattr(main_window, "prepare_regular_file_drop"):
                    try:
                        main_window.prepare_regular_file_drop(regular_paths)
                    except Exception as exc:
                        results.append({
                            "status": "error",
                            "path": "",
                            "message": self._lt("Could not prepare dropped files: {error}").format(error=exc),
                        })
                total = len(regular_paths)
                if total > 1:
                    progressDialog = QProgressDialog(self._lt("Adding files..."), self._lt("Cancel"), 0, total, main_window)
                    progressDialog.setWindowTitle(self._lt("Adding Files"))
                    progressDialog.setWindowModality(Qt.WindowModal)
                    progressDialog.setMinimumDuration(0)
                    center_dialog_on_parent(progressDialog, main_window)
                for i, file_path in enumerate(regular_paths):
                    if generation != self._zip_import_generation:
                        break
                    if progressDialog and progressDialog.wasCanceled():
                        results.append({"status": "cancelled", "path": file_path})
                        break
                    if hasattr(main_window, "add_regular_file_from_drop"):
                        try:
                            result = main_window.add_regular_file_from_drop(file_path)
                        except Exception as exc:
                            result = {
                                "status": "error",
                                "path": file_path,
                                "message": self._lt("Could not add dropped file: {error}").format(error=exc),
                            }
                        results.append(result)
                        if result and result.get("status") == "cancelled":
                            break
                    if progressDialog:
                        progressDialog.setValue(i + 1)
                        QApplication.processEvents()
                if progressDialog:
                    progressDialog.close()
                    progressDialog = None
                if generation != self._zip_import_generation:
                    self._safe_accept_event(event)
                    return
                if hasattr(main_window, "finish_regular_file_drop"):
                    main_window.finish_regular_file_drop(results)
                summary = self._summarize_drop_results(results)
                self._log_drop_event(
                    main_window,
                    "Import finished",
                    total=len(results),
                    added=summary.get("added", 0),
                    converted=summary.get("converted", 0),
                    skipped=summary.get("skipped", 0),
                    errors=summary.get("error", 0),
                    cancelled=summary.get("cancelled", 0),
                )
                self._safe_accept_event(event)
            except Exception as exc:
                if progressDialog:
                    progressDialog.close()
                self._show_drop_exception(main_window, exc)
                self._safe_accept_event(event)
        else:
            self._safe_ignore_event(event)
