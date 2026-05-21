import os

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QProgressDialog, QTableWidget

from .floppy_image import is_supported_image_path
from .ui_utils import center_dialog_on_parent


class DropTableWidget(QTableWidget):
    def __init__(self, rows, columns, parent=None):
        super().__init__(rows, columns, parent)
        self.setAcceptDrops(True)
        self._drag_invite_active = False

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
        rect = self.viewport().rect().adjusted(18, 18, -18, -18)
        if rect.width() <= 0 or rect.height() <= 0:
            return

        palette = self.palette()
        accent = palette.highlight().color()
        if self._drag_invite_active:
            fill = QColor(accent)
            fill.setAlpha(34)
            border = QColor(accent)
            border.setAlpha(185)
            text_color = palette.text().color()
            title = self._lt("Drop to import")
            subtitle = self._lt("MIDI, E-SEQ, or disk image")
        else:
            fill = palette.base().color()
            fill.setAlpha(214)
            border = palette.mid().color()
            border.setAlpha(92)
            text_color = palette.text().color()
            title = self._lt("Drop files or disk images here")
            subtitle = self._lt("MIDI, E-SEQ, IMG, HFE, SCP, and other disk images")

        card_width = min(rect.width(), 680)
        card_height = min(rect.height(), 210)
        card = QRect(0, 0, card_width, card_height)
        card.moveCenter(rect.center())

        painter.setBrush(fill)
        border_pen = QPen(border, 2 if self._drag_invite_active else 1.5)
        border_pen.setStyle(Qt.DashLine)
        border_pen.setDashPattern([6, 5])
        border_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(border_pen)
        painter.drawRoundedRect(card, 8, 8)

        title_font = QFont(self.font())
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 3)
        painter.setFont(title_font)
        painter.setPen(text_color)
        title_rect = QRect(card.left() + 36, card.top() + 58, card.width() - 72, 50)
        painter.drawText(title_rect, Qt.AlignCenter | Qt.TextWordWrap, title)

        subtitle_font = QFont(self.font())
        subtitle_font.setPointSize(max(8, subtitle_font.pointSize()))
        painter.setFont(subtitle_font)
        painter.setPen(palette.mid().color() if not self._drag_invite_active else text_color)
        subtitle_rect = QRect(card.left() + 36, card.top() + 116, card.width() - 72, 42)
        painter.drawText(subtitle_rect, Qt.AlignCenter | Qt.TextWordWrap, subtitle)

    def file_exists(self, file_path):
        """Return True if a row already contains this file (full path is stored in column 1)."""
        for i in range(self.rowCount()):
            item = self.item(i, 1)
            if item and item.text() == file_path:
                return True
        return False

    def _can_accept_drag_path(self, main_window, file_path):
        if is_supported_image_path(file_path):
            return True
        if getattr(main_window, "can_accept_electone_evt_path", lambda _path: False)(file_path):
            return True
        if getattr(main_window, "can_accept_v50_nseq_path", lambda _path: False)(file_path):
            return True
        if getattr(main_window, "can_accept_mpc_seq_path", lambda _path: False)(file_path):
            return True
        if getattr(main_window, "is_image_mode", lambda: False)() and os.path.isfile(file_path):
            return True
        return bool(getattr(main_window, "can_accept_regular_drop_path", lambda _path: False)(file_path))

    def _drag_event_has_supported_urls(self, event):
        if not event.mimeData().hasUrls():
            return False
        main_window = self.window()
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if self._can_accept_drag_path(main_window, file_path):
                return True
        return False

    def dragEnterEvent(self, event):
        if self._drag_event_has_supported_urls(event):
            self._set_drag_invite_active(True)
            event.acceptProposedAction()
            return
        self._set_drag_invite_active(False)
        event.ignore()

    def dragMoveEvent(self, event):
        if self._drag_event_has_supported_urls(event):
            self._set_drag_invite_active(True)
            event.acceptProposedAction()
        else:
            self._set_drag_invite_active(False)
            event.ignore()

    def dragLeaveEvent(self, event):
        self._set_drag_invite_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._set_drag_invite_active(False)
        if event.mimeData().hasUrls():
            main_window = self.window()
            urls = event.mimeData().urls()
            local_paths = [url.toLocalFile() for url in urls if url.toLocalFile()]
            image_paths = [path for path in local_paths if is_supported_image_path(path)]

            if image_paths and hasattr(main_window, "load_image_file"):
                main_window.load_image_file(image_paths[0])
                event.acceptProposedAction()
                return

            electone_evt_paths = [
                path
                for path in local_paths
                if getattr(main_window, "can_accept_electone_evt_path", lambda _path: False)(path)
            ]
            if electone_evt_paths and hasattr(main_window, "handle_electone_evt_file_drop"):
                handled = main_window.handle_electone_evt_file_drop(local_paths)
                if handled:
                    event.acceptProposedAction()
                    return
                electone_evt_path_set = {os.path.abspath(path) for path in electone_evt_paths}
                local_paths = [
                    path
                    for path in local_paths
                    if os.path.abspath(path) not in electone_evt_path_set
                ]
                if not local_paths:
                    event.acceptProposedAction()
                    return

            v50_nseq_paths = [
                path
                for path in local_paths
                if getattr(main_window, "can_accept_v50_nseq_path", lambda _path: False)(path)
            ]
            if v50_nseq_paths and hasattr(main_window, "handle_v50_nseq_file_drop"):
                handled = main_window.handle_v50_nseq_file_drop(local_paths)
                if handled:
                    event.acceptProposedAction()
                    return
                v50_nseq_path_set = {os.path.abspath(path) for path in v50_nseq_paths}
                local_paths = [
                    path
                    for path in local_paths
                    if os.path.abspath(path) not in v50_nseq_path_set
                ]
                if not local_paths:
                    event.acceptProposedAction()
                    return

            mpc_seq_paths = [
                path
                for path in local_paths
                if getattr(main_window, "can_accept_mpc_seq_path", lambda _path: False)(path)
            ]
            if mpc_seq_paths and hasattr(main_window, "handle_mpc_seq_file_drop"):
                handled = main_window.handle_mpc_seq_file_drop(local_paths)
                if handled:
                    event.acceptProposedAction()
                    return
                mpc_seq_path_set = {os.path.abspath(path) for path in mpc_seq_paths}
                local_paths = [
                    path
                    for path in local_paths
                    if os.path.abspath(path) not in mpc_seq_path_set
                ]
                if not local_paths:
                    event.acceptProposedAction()
                    return

            if getattr(main_window, "is_image_mode", lambda: False)():
                if hasattr(main_window, "queue_image_additions"):
                    main_window.queue_image_additions(local_paths)
                event.acceptProposedAction()
                return

            regular_paths = [
                path
                for path in local_paths
                if getattr(main_window, "can_accept_regular_drop_path", lambda _path: False)(path)
            ]
            if hasattr(main_window, "prepare_regular_file_drop"):
                main_window.prepare_regular_file_drop(regular_paths)
            total = len(regular_paths)
            progressDialog = None
            if total > 1:
                progressDialog = QProgressDialog("Adding files...", "Cancel", 0, total, main_window)
                progressDialog.setWindowTitle("Adding Files")
                progressDialog.setWindowModality(Qt.WindowModal)
                progressDialog.setMinimumDuration(0)
                center_dialog_on_parent(progressDialog, main_window)
            results = []
            for i, file_path in enumerate(regular_paths):
                if hasattr(main_window, "add_regular_file_from_drop"):
                    result = main_window.add_regular_file_from_drop(file_path)
                    results.append(result)
                    if result and result.get("status") == "cancelled":
                        break
                if progressDialog:
                    progressDialog.setValue(i + 1)
                    QApplication.processEvents()
            if progressDialog:
                progressDialog.close()
            if hasattr(main_window, "finish_regular_file_drop"):
                main_window.finish_regular_file_drop(results)
            event.acceptProposedAction()
        else:
            event.ignore()
