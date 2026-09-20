"""ZIP extraction paints its progress UI before I/O and updates within files."""

import os
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QApplication, QProgressBar, QWidget

from aps_midi_prep_tool_app import drop_table_widget, zip_import


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("cancel_during_file", [False, True])
def test_extraction_is_visible_before_open_and_advances_within_one_file(
    application, tmp_path, monkeypatch, cancel_during_file,
):
    archive_path = tmp_path / "songs.zip"
    payload = b"a single song requiring several copy chunks"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("album/song.mid", payload)
    monkeypatch.setattr(zip_import, "COPY_CHUNK_SIZE", 4)

    parent = QWidget()
    parent.can_accept_regular_drop_path = lambda path: path.endswith(".mid")
    added = []
    errors = []
    parent._zip_import_references = lambda: added
    parent.add_regular_file_from_drop = lambda path: added.append(Path(path)) or {"status": "added"}
    parent._show_error_list = lambda *args, **_kwargs: errors.append(args)
    parent._show_operation_error = lambda *args, **_kwargs: errors.append(args)
    table = drop_table_widget.DropTableWidget(0, 1, parent)
    dialogs = []

    class ProgressDialog(drop_table_widget.QProgressDialog):
        def __init__(self, *args):
            super().__init__(*args)
            self.painted = False
            dialogs.append(self)

        def paintEvent(self, event):
            self.painted = True
            super().paintEvent(event)

    monkeypatch.setattr(drop_table_widget, "QProgressDialog", ProgressDialog)
    real_open = zip_import.zipfile.ZipFile
    opened = []

    def inspect_before_open(*args, **kwargs):
        dialog = dialogs[0]
        assert dialog.isVisible()
        assert dialog.painted
        bar = dialog.findChild(QProgressBar)
        assert bar.isVisible()
        assert (bar.minimum(), bar.maximum()) == (0, 0)
        opened.append(True)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(zip_import.zipfile, "ZipFile", inspect_before_open)
    real_extract = drop_table_widget.extract_zip
    observations, extraction_roots = [], []

    def inspect_extraction(path, destination, **kwargs):
        extraction_roots.append(Path(destination))
        notify = kwargs["byte_progress"]

        def progress(completed, total):
            notify(completed, total)
            bar = dialogs[0].findChild(QProgressBar)
            observations.append((completed, total, bar.value(), bar.maximum(), bar.isVisible()))
            if cancel_during_file and 0 < completed < total:
                dialogs[0].cancel()

        kwargs["byte_progress"] = progress
        return real_extract(path, destination, **kwargs)

    monkeypatch.setattr(drop_table_widget, "extract_zip", inspect_extraction)
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(archive_path))])
    accepted = []
    try:
        table.dropEvent(SimpleNamespace(
            mimeData=lambda: mime, acceptProposedAction=lambda: accepted.append(True),
        ))
        assert accepted == [True]
        assert errors == []
        assert opened == [True]
        partial = [observation for observation in observations if 0 < observation[0] < observation[1]]
        assert partial
        assert all(completed == value and total == maximum and visible
                   for completed, total, value, maximum, visible in partial)
        assert not dialogs[0].isVisible()
        if cancel_during_file:
            assert added == []
            assert all(not path.exists() for path in extraction_roots)
        else:
            assert len(added) == 1
            assert added[0].read_bytes() == payload
            assert observations[-1][:4] == (len(payload),) * 4
    finally:
        table.cleanup_zip_imports()
        parent.deleteLater()
        application.processEvents()
