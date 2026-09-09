"""Image export translates progress before inserting the user's filename."""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QProgressDialog

from aps_midi_prep_tool_app.main_window import MidiTitleWindow
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text


class _StopBeforeWriting(Exception):
    pass


@pytest.mark.parametrize("language", [entry.code for entry in SUPPORTED_LANGUAGES])
def test_image_export_progress_localizes_actual_producer_and_preserves_filename(tmp_path, language):
    app = QApplication.instance() or QApplication([])
    filename = "Save {filename} <album & 2>.mid"
    cells = {1: str(tmp_path / filename), 3: filename}
    window = SimpleNamespace(
        _regular_file_count=lambda: 1,
        _regular_file_rows=lambda: [0],
        is_local_eseq_mode=lambda: False,
        table=SimpleNamespace(item=lambda _row, column: SimpleNamespace(text=lambda: cells[column])),
        _row_raw_title=lambda _row: "Album title",
        _lt=lambda source, **values: translate_text(source, language, **values),
    )
    progress = QProgressDialog()
    seen = []

    def receive(step, total, message):
        seen.append((step, total, message))
        MidiTitleWindow._set_progress_dialog_message(window, progress, message)
        raise _StopBeforeWriting

    try:
        with pytest.raises(_StopBeforeWriting):
            MidiTitleWindow._stage_files_for_image_export(window, str(tmp_path), receive)
        expected = translate_text("Preparing {filename} for image export...", language, filename=filename)
        assert seen == [(0, 1, expected)]
        assert progress.labelText() == expected
        assert filename in expected
        if language != "en":
            assert expected != f"Preparing {filename} for image export..."
        assert list(tmp_path.iterdir()) == []
    finally:
        progress.close()
        app.processEvents()
