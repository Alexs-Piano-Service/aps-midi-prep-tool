"""Staging works when PySide does not expose the item copy constructor."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem

from aps_midi_prep_tool_app import pending_changes, preparation_layer
from test_inspection_staging import _load_folder, window  # noqa: F401
from test_preparation_automatic import destination_window  # noqa: F401
from test_preparation_layers import (
    _apply, _assert_sources_unchanged, _edit_first_title, _export, _load,
    _materials, _rows,
)


_CUSTOM_ROLE = Qt.UserRole + 600
_RAW_TITLE_ROLE = Qt.UserRole + 1
_EDITED_TITLE_ROLE = Qt.UserRole + 2


@pytest.fixture(autouse=True)
def _unavailable_item_copy_constructor(monkeypatch):
    def construct(*args, **kwargs):
        if args and isinstance(args[0], QTableWidgetItem):
            raise TypeError("QTableWidgetItem(existing_item) is unavailable")
        return QTableWidgetItem(*args, **kwargs)

    # Keep this guard effective even when the modules no longer import the
    # constructor: reintroducing the old copy expression must fail here.
    for module in (pending_changes, preparation_layer):
        monkeypatch.setattr(module, "QTableWidgetItem", construct, raising=False)


def _decorate(item, label):
    item.setData(_CUSTOM_ROLE, {"source": label, "values": [1, 2, 3]})
    item.setToolTip(f"Details for {label}")
    item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
    item.setCheckState(Qt.PartiallyChecked)


def _item_values(item):
    return (
        item.text(), item.data(_CUSTOM_ROLE), item.toolTip(), item.flags(),
        item.checkState(), item.data(preparation_layer.PREPARATION_ID_ROLE),
        item.data(_RAW_TITLE_ROLE), item.data(_EDITED_TITLE_ROLE),
    )


def _mutate(item):
    item.setText("Changed independently")
    item.setData(_CUSTOM_ROLE, {"changed": True})
    item.setToolTip("Changed tooltip")
    item.setFlags(Qt.ItemIsEnabled)
    item.setCheckState(Qt.Unchecked)


def test_staged_capture_and_restore_preserve_independent_table_items(window, tmp_path):
    sources = _load_folder(window, tmp_path)
    originals = [source.read_bytes() for source in sources]
    item = window.table.item(0, 3)
    _decorate(item, "filename")
    expected = _item_values(item)
    snapshot = window._capture_staged_state()
    try:
        saved_item = snapshot["rows"][0][3]
        assert saved_item is not item
        assert _item_values(saved_item) == expected
        identity = snapshot["rows"][0][1].data(preparation_layer.PREPARATION_ID_ROLE)
        assert identity
        assert identity == window.table.item(0, 1).data(preparation_layer.PREPARATION_ID_ROLE)

        _mutate(item)
        assert _item_values(saved_item) == expected
        window._restore_staged_snapshot(snapshot)

        restored = window.table.item(0, 3)
        assert restored is not saved_item
        assert _item_values(restored) == expected
        assert window.table.item(0, 1).data(preparation_layer.PREPARATION_ID_ROLE) == identity
        _mutate(restored)
        assert _item_values(saved_item) == expected
        assert [source.read_bytes() for source in sources] == originals
    finally:
        snapshot["assets"].cleanup()


def _snapshot(path, filename, title):
    row = [None, QTableWidgetItem(path), None, QTableWidgetItem(filename),
           QTableWidgetItem(title), QTableWidgetItem("Details"), None]
    row[1].setData(preparation_layer.PREPARATION_ID_ROLE, "stable-song-id")
    row[4].setData(_RAW_TITLE_ROLE, title)
    row[4].setData(_EDITED_TITLE_ROLE, False)
    for column in (3, 4, 5):
        _decorate(row[column], f"{filename}:{column}")
    return {"rows": [row], "state": {}, "album": "Album", "catalog": "Catalog"}


@pytest.mark.parametrize("image_mode", (False, True), ids=("folder", "image"))
def test_preparation_rebase_clones_baseline_and_deliberate_edits(image_mode):
    source = "album/SONG.MID"
    before = _snapshot(source, "SONG.MID", "Original")
    after = _snapshot("album/SONG.FIL", "SONG.FIL", "Automatic")
    current = _snapshot("album/SONG.FIL", "RENAMED.FIL", "Deliberate title")
    source_rows = [snapshot["rows"][0] for snapshot in (before, after, current)]
    original_values = [[_item_values(item) if item is not None else None for item in row]
                       for row in source_rows]

    result = preparation_layer.PreparationLayer(before, after).rebase(
        current, raw_title_role=_RAW_TITLE_ROLE, edited_title_role=_EDITED_TITLE_ROLE,
        image_mode=image_mode,
    )

    row = result["rows"][0]
    assert row[0] is None and row[2] is None and row[6] is None
    assert row[1].text() == source
    assert row[1].data(preparation_layer.PREPARATION_ID_ROLE) == "stable-song-id"
    expected_filename = list(original_values[2][3])
    expected_filename[0] = "RENAMED.MID"
    assert _item_values(row[3]) == tuple(expected_filename)
    expected_title = list(original_values[2][4])
    expected_title[-1] = True
    assert _item_values(row[4]) == tuple(expected_title)
    assert _item_values(row[5]) == original_values[0][5]
    title_field = "pendingImageTitleEdits" if image_mode else "pendingEdits"
    rename_field = "pendingImageRenames" if image_mode else "pendingRegularRenames"
    assert result["state"][title_field] == {source: "Deliberate title"}
    assert result["state"][rename_field] == {
        source: "album/RENAMED.MID" if image_mode else "RENAMED.MID",
    }
    assert result["preparation_layer"] is None

    for column in (1, 3, 4, 5):
        assert all(row[column] is not original[column] for original in source_rows)
        _mutate(row[column])
    assert [[_item_values(item) if item is not None else None for item in original]
            for original in source_rows] == original_values


def test_destination_change_and_undo_keep_deliberate_title_and_filename(
    destination_window, tmp_path, monkeypatch,
):
    window, originals = _load(destination_window, tmp_path / "music", False)
    _apply(window, "mark_ii")
    _edit_first_title(window, monkeypatch, "Keep this title")
    row, source, kind, _filename = _rows(window)[0]
    assert kind == "eseq"
    window._stage_regular_row_pending_rename(row, source, "RENAMED.FIL")
    prepared = _materials(window)

    _apply(window, "midi_export")

    assert _rows(window)[0][2:] == ("midi", "RENAMED.MID")
    assert window._row_raw_title(_rows(window)[0][0]) == "Keep this title"
    exported = _export(window, tmp_path / "first-export")
    assert set(exported) == {"RENAMED.MID"}
    window.undo_last_staged_batch()

    assert window._preparation_profile().key == "mark_ii"
    assert _rows(window)[0][2:] == ("eseq", "RENAMED.FIL")
    assert window._row_raw_title(_rows(window)[0][0]) == "Keep this title"
    assert _materials(window) == prepared
    _apply(window, "midi_export")
    assert _export(window, tmp_path / "second-export") == exported
    _assert_sources_unchanged(originals)
