import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QProgressDialog, QTableWidget

from .floppy_image import is_supported_image_path
from .ui_utils import center_dialog_on_parent


class DropTableWidget(QTableWidget):
    def __init__(self, rows, columns, parent=None):
        super().__init__(rows, columns, parent)
        self.setAcceptDrops(True)

    def file_exists(self, file_path):
        """Return True if a row already contains this file (full path is stored in column 1)."""
        for i in range(self.rowCount()):
            item = self.item(i, 1)
            if item and item.text() == file_path:
                return True
        return False

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            main_window = self.window()
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
                if is_supported_image_path(file_path):
                    event.acceptProposedAction()
                    return
                if getattr(main_window, "is_image_mode", lambda: False)() and os.path.isfile(file_path):
                    event.acceptProposedAction()
                    return
                if getattr(main_window, "can_accept_regular_drop_path", lambda _path: False)(file_path):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            main_window = self.window()
            urls = event.mimeData().urls()
            local_paths = [url.toLocalFile() for url in urls if url.toLocalFile()]
            image_paths = [path for path in local_paths if is_supported_image_path(path)]

            if image_paths and hasattr(main_window, "load_image_file"):
                main_window.load_image_file(image_paths[0])
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
                if self.file_exists(file_path):
                    results.append({"status": "skipped", "path": file_path, "message": "Already listed."})
                elif hasattr(main_window, "add_regular_file_from_drop"):
                    results.append(main_window.add_regular_file_from_drop(file_path))
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
