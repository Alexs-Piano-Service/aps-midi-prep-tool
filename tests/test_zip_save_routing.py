"""ZIP imports must be saved outside their temporary extraction directories."""

from types import MethodType, SimpleNamespace

import pytest

from aps_midi_prep_tool_app.main_window import MidiTitleWindow


def _table(paths, archive_sources):
    return SimpleNamespace(
        rowCount=lambda: len(paths),
        item=lambda row, column: SimpleNamespace(text=lambda: paths[row]) if column == 1 else None,
        zip_source_for_path=lambda path: archive_sources.get(path, ""),
    )


@pytest.mark.parametrize("archive_row", [0, 1])
def test_save_exports_mixed_zip_and_loose_files_even_without_edits(monkeypatch, archive_row):
    paths = ["loose.mid", "second.mid"]
    paths[archive_row] = "extracted/song.mid"
    exports = []
    window = SimpleNamespace(
        table=_table(paths, {"extracted/song.mid": "songs.zip"}),
        image_session=None,
        pendingEdits={},
        is_image_mode=lambda: False,
        save_as_changes=lambda: exports.append(tuple(paths)),
    )
    monkeypatch.setattr(MidiTitleWindow, "_ensure_preparation_ready", lambda self: True)

    MidiTitleWindow.save_pending_changes(window)

    assert exports == [tuple(paths)]
    assert window.pendingEdits == {}


def test_zip_import_save_still_requires_successful_preparation(monkeypatch):
    window = SimpleNamespace(
        save_as_changes=lambda: pytest.fail("Incomplete preparation must not be exported"),
    )
    monkeypatch.setattr(MidiTitleWindow, "_ensure_preparation_ready", lambda self: False)
    MidiTitleWindow.save_pending_changes(window)


def test_save_extracted_image_asks_for_persistent_image_even_without_edits(monkeypatch):
    exports = []
    window = SimpleNamespace(
        table=_table([], {"extracted/disk.img": "disks.zip"}),
        image_session=SimpleNamespace(source_path="extracted/disk.img"),
        save_image_as=lambda: exports.append(True),
    )
    monkeypatch.setattr(MidiTitleWindow, "_ensure_preparation_ready", lambda self: True)

    MidiTitleWindow.save_image_changes(window)

    assert exports == [True]


def test_existing_image_with_zip_additions_keeps_its_save_destination():
    window = SimpleNamespace(
        table=_table(["extracted/song.mid"], {"extracted/song.mid": "songs.zip"}),
        image_session=SimpleNamespace(source_path="existing.img"),
    )
    assert MidiTitleWindow._zip_import_source(window) == ""


def test_zip_context_uses_original_archive_folder(tmp_path):
    archive = tmp_path / "songs.zip"
    archive.write_bytes(b"original archive")
    paths = ["extracted/album/one.mid", "extracted/album/two.mid"]
    window = SimpleNamespace(table=_table(paths, dict.fromkeys(paths, str(archive))))

    MidiTitleWindow._set_regular_mode_context(window, file_paths=paths)

    assert window.regularModeContextPath == str(tmp_path)
    assert archive.read_bytes() == b"original archive"


@pytest.mark.parametrize("saved_location", [False, True])
def test_zip_export_default_uses_archive_folder_unless_user_has_saved_location(tmp_path, saved_location):
    archive = tmp_path / "songs.zip"
    archive.write_bytes(b"original archive")
    saved_dir = tmp_path / "exports"
    saved_dir.mkdir()
    window = SimpleNamespace(
        table=_table(["extracted/song.mid"], {"extracted/song.mid": str(archive)}),
        image_session=None,
        SETTING_SAVE_AS_LOCATION="save-location",
        settings=SimpleNamespace(value=lambda *_: str(saved_dir) if saved_location else ""),
    )
    window._existing_directory_for_dialog_path = MethodType(
        MidiTitleWindow._existing_directory_for_dialog_path, window,
    )

    location = MidiTitleWindow._last_save_as_location(window)

    assert location == str(saved_dir if saved_location else tmp_path)


def test_save_helpers_accept_tables_without_zip_import_support():
    window = SimpleNamespace(table=SimpleNamespace())
    assert MidiTitleWindow._zip_source_for_path(window, "song.mid") == ""
    assert MidiTitleWindow._zip_import_source(window) == ""
