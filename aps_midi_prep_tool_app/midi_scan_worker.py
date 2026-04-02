import os

from PySide6.QtCore import QThread, Signal

from .midi_metadata import extract_first_title_from_midi, extract_midi_type_label_from_midi


class MidiProcessingWorker(QThread):
    progressChanged = Signal(int)
    fileProcessed = Signal(str, str, str, str)  # full_path, filename, title, midi_type
    finished = Signal()

    def __init__(self, directory, parent=None):
        super().__init__(parent)
        self.directory = directory

    def run(self):
        files = [f for f in os.listdir(self.directory)
                 if f.lower().endswith((".mid", ".midi"))]
        files.sort()
        total = len(files)
        for index, file_name in enumerate(files):
            full_path = os.path.join(self.directory, file_name)
            title = extract_first_title_from_midi(full_path)
            midi_type = extract_midi_type_label_from_midi(full_path)
            self.fileProcessed.emit(full_path, file_name, title, midi_type)
            progress = int((index + 1) * 100 / total) if total > 0 else 100
            self.progressChanged.emit(progress)
        self.finished.emit()
