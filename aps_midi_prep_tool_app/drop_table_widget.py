import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QProgressDialog, QTableWidget

from .floppy_image import is_supported_image_path
from .midi_metadata import extract_first_title_from_midi, extract_midi_type_label_from_midi


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
                if file_path.lower().endswith(('.mid', '.midi')):
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

            total = len(urls)
            progressDialog = None
            if total > 1:
                progressDialog = QProgressDialog("Adding MIDI files...", "Cancel", 0, total, main_window)
                progressDialog.setWindowModality(Qt.WindowModal)
                progressDialog.setMinimumDuration(0)
            for i, url in enumerate(urls):
                file_path = url.toLocalFile()
                if file_path.lower().endswith(('.mid', '.midi')):
                    if not self.file_exists(file_path) and hasattr(main_window, "add_table_row"):
                        title = extract_first_title_from_midi(file_path)
                        midi_type = extract_midi_type_label_from_midi(file_path)
                        main_window.add_table_row(file_path, os.path.basename(file_path), title, midi_type)
                if progressDialog:
                    progressDialog.setValue(i + 1)
                    QApplication.processEvents()
            if progressDialog:
                progressDialog.close()
            event.acceptProposedAction()
        else:
            event.ignore()
