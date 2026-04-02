import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QProgressDialog, QTableWidget

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
            for url in event.mimeData().urls():
                file_path = url.toLocalFile()
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
