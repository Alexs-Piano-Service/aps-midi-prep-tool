"""Exercise folder Save without constructing a Qt application or window."""

import os
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.eseq_pianodir import normalize_eseq_order_key
from aps_midi_prep_tool_app.main_window import MidiTitleWindow
from aps_midi_prep_tool_app.midi_metadata import read_first_title_from_midi


def _midi_bytes(title="Old"):
    title_bytes = title.encode("ascii")
    track = b"\x00\xff\x03" + bytes([len(title_bytes)]) + title_bytes + b"\x00\xff\x2f\x00"
    return (
        b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x00\x60"
        + b"MTrk" + len(track).to_bytes(4, "big") + track
    )


class FakeItem:
    def __init__(self, value):
        self.value = value
        self.roles = {}

    def text(self):
        return self.value

    def setText(self, value):
        self.value = value

    def data(self, role):
        return self.roles.get(role)

    def setData(self, role, value):
        self.roles[role] = value

    def setToolTip(self, value):
        pass


class FakeTable:
    def __init__(self, paths):
        self.rows = [
            {1: FakeItem(path), 3: FakeItem(Path(path).name), 4: FakeItem(f"Title {index}")}
            for index, path in enumerate(paths, start=1)
        ]

    def rowCount(self):
        return len(self.rows)

    def item(self, row, column):
        return self.rows[row].get(column)

    def isSortingEnabled(self):
        return False

    def setItem(self, row, column, item):
        self.rows[row][column] = item


class FakeProgress:
    def __init__(self, window, maximum):
        self.window = window
        self.maximum = maximum
        self.value = 0
        self.closed = False
        self.auto_reset = True
        self.cancelled = window.cancel_after == 0

    def wasCanceled(self):
        return self.cancelled

    def setValue(self, value):
        self.value = value
        if self.window.cancel_after is not None and value >= self.window.cancel_after:
            self.cancelled = True
        if self.auto_reset and value == self.maximum:
            self.cancelled = False

    def setAutoReset(self, value):
        self.auto_reset = value

    def setAutoClose(self, value):
        pass

    def close(self):
        self.closed = True


class SaveWindow:
    """Use real save/state methods, replacing UI and peripheral output only."""

    def __init__(self, paths, *, eseq=False, outputs=False):
        self.paths = paths
        self.table = FakeTable(paths)
        self.image_session = None
        self.regularEseqMode = eseq
        self.regularEseqVariant = "eseq"
        self.regularModeContextPath = str(Path(paths[0]).parent)
        self.pendingEdits = {path: f"Title {index}" for index, path in enumerate(paths, start=1)}
        self.pendingRegularConversions = {}
        self.pendingRegularRenames = {}
        self.pendingRegularOrderKeyEdits = {}
        self.pendingGeneratePianodir = outputs and eseq
        self.listedFileInfo = {
            path: {
                "title": "Old",
                "title_mode": "eseq" if eseq else "midi",
                "is_midi": not eseq,
                "order_key": normalize_eseq_order_key(f"{len(paths) - index:03d}"),
            }
            for index, path in enumerate(paths)
        }
        self.outputs = outputs
        self.cancel_after = None
        self.failures = {}
        self.writes = []
        self.conversion_writes = []
        self.backups = []
        self.later_actions = []
        self.messages = []
        self.errors = []
        self.progress_dialogs = []
        self.status_label = FakeItem("")
        self.backup_checkbox = SimpleNamespace(isChecked=lambda: False)

    def __getattr__(self, name):
        value = getattr(MidiTitleWindow, name)
        return MethodType(value, self) if callable(value) else value

    def is_image_mode(self):
        return False

    def _is_special_pianodir_row(self, row):
        return False

    def _regular_eseq_rows(self):
        return self._regular_file_rows() if self.regularEseqMode else []

    def _language_code(self):
        return "en"

    def _lt(self, source, **kwargs):
        return source.format(**kwargs)

    def _prepare_progress_dialog(self, dialog):
        pass

    def _ensure_pianodir_generation_for_save(self):
        return True

    def _ensure_eseq_file_limit(self, count, **kwargs):
        return True

    def _should_generate_pianodir(self, **kwargs):
        return self.pendingGeneratePianodir

    def _tag_sidecars_enabled(self):
        return self.outputs

    def _metadata_summary_enabled(self):
        return self.outputs

    def _regular_midi_file_count(self):
        # Keep all later output stages enabled in the cancellation regression.
        return len(self.paths)

    def _create_backup_if_enabled(self, path):
        self.backups.append(path)
        return self.failures.get((path, "backup"))

    def _write_eseq_file_to_path(self, source, destination, *, title=None, order_key=None):
        self.writes.append((source, title, order_key))
        error = self.failures.get((source, "write"))
        if not error:
            Path(destination).write_bytes(repr((title, order_key)).encode("ascii"))
        return error

    def _write_listed_file_to_path(self, source, title, destination, *, order_key=None):
        self.conversion_writes.append((source, destination))
        return self.failures.get((source, "write")) or MidiTitleWindow._write_listed_file_to_path(
            self, source, title, destination, order_key=order_key,
        )

    def _save_pending_regular_renames(self, **kwargs):
        self.later_actions.append("rename")
        count = len(self.pendingRegularRenames)
        self.pendingRegularRenames.clear()
        return [], count, 0

    def _write_regular_pianodir(self, **kwargs):
        self.later_actions.append("catalog")
        return str(Path(self.regularModeContextPath) / "PIANODIR.FIL")

    def _current_regular_pianodir_metadata(self):
        return None

    def _refresh_regular_pianodir_row(self):
        pass

    def _write_tag_sidecars_for_regular_rows(self, **kwargs):
        self.later_actions.append("sidecars")
        return []

    def _write_metadata_summary_for_regular_rows(self, **kwargs):
        self.later_actions.append("summary")
        return [], str(Path(self.regularModeContextPath) / "summary.csv")

    def _show_error_list(self, title, message, errors, **kwargs):
        self.errors.append((title, message, list(errors), kwargs))

    def _refresh_regular_mode_action_state(self):
        pass

    def _log_event(self, *args, **kwargs):
        pass

    def _cleanup_midi_scratch_dir(self):
        self.later_actions.append("cleanup")

    def _load_regular_files(self, paths, status_text):
        self.later_actions.append("reload")
        self.pendingRegularConversions.clear()
        self.pendingEdits.clear()

    def stage_conversions(self, rows):
        destinations = {}
        for row in rows:
            source = self.table.item(row, 1).text()
            destination = str(Path(source).with_name(f"converted{row + 1}.mid"))
            scratch = Path(source).with_suffix(".scratch.mid")
            scratch.write_bytes(_midi_bytes("Converted"))
            self.pendingRegularConversions[source] = {
                "temp_path": str(scratch),
                "target_filename": Path(destination).name,
                "target_kind": "midi",
                "overwrite_original": False,
            }
            self.table.item(row, 3).setText(Path(destination).name)
            destinations[source] = destination
        return destinations


@pytest.fixture
def save_window(tmp_path, monkeypatch):
    def make(*, eseq=False, outputs=False):
        paths = [str(tmp_path / name) for name in ("first.mid", "second.mid")]
        for path in paths:
            Path(path).write_bytes(_midi_bytes())
        window = SaveWindow(paths, eseq=eseq, outputs=outputs)

        def progress(*args):
            dialog = FakeProgress(window, args[3])
            window.progress_dialogs.append(dialog)
            return dialog

        real_update = main_window.update_midi_title

        def update(path, title):
            window.writes.append((path, title, None))
            return window.failures.get((path, "write")) or real_update(path, title)

        monkeypatch.setattr(main_window, "QProgressDialog", progress)
        monkeypatch.setattr(main_window.QApplication, "processEvents", lambda: None)
        monkeypatch.setattr(main_window.QMessageBox, "information", lambda _parent, *args: window.messages.append(args))
        monkeypatch.setattr(main_window, "update_midi_title", update)
        return window

    return make


def _assert_not_complete(window):
    assert all(title != "Save Complete" for title, _message in window.messages)


def test_cancel_before_first_write_keeps_all_pending_changes(save_window):
    window = save_window(outputs=True)
    original = {path: Path(path).read_bytes() for path in window.paths}
    pending = dict(window.pendingEdits)
    window.pendingRegularRenames[window.paths[0]] = "RENAMED.MID"
    window.cancel_after = 0

    window.save_pending_changes()

    assert window.writes == []
    assert window.backups == []
    assert window.pendingEdits == pending
    assert window.pendingRegularRenames
    assert window.later_actions == []
    assert {path: Path(path).read_bytes() for path in window.paths} == original
    assert all(info["title"] == "Old" for info in window.listedFileInfo.values())
    assert all(dialog.closed for dialog in window.progress_dialogs)
    _assert_not_complete(window)


def test_cancel_after_one_write_commits_only_successful_title_and_order_then_retries(save_window):
    window = save_window(eseq=True, outputs=True)
    first, second = window.paths
    original_second = Path(second).read_bytes()
    expected_order = window._regular_eseq_order_key_edits()
    window.pendingRegularRenames[second] = "RENAMED.FIL"
    window.cancel_after = 1

    window.save_pending_changes()

    assert window.writes == [(first, "Title 1", expected_order[first])]
    assert window.pendingEdits == {second: "Title 2"}
    assert window.listedFileInfo[first]["title"] == "Title 1"
    assert window.listedFileInfo[first]["order_key"] == expected_order[first]
    assert window.listedFileInfo[second]["title"] == "Old"
    assert window.listedFileInfo[second]["order_key"] != expected_order[second]
    assert Path(second).read_bytes() == original_second
    assert window.pendingRegularRenames == {second: "RENAMED.FIL"}
    assert window.pendingGeneratePianodir
    assert window.later_actions == []
    assert "1 file(s) saved; 1 file(s) remaining." in window.status_label.text()
    _assert_not_complete(window)

    window.cancel_after = None
    window.save_pending_changes()

    assert window.writes == [
        (first, "Title 1", expected_order[first]),
        (second, "Title 2", expected_order[second]),
    ]
    assert window.pendingEdits == {}
    assert window.pendingRegularOrderKeyEdits == {}
    assert window._regular_eseq_order_key_edits() == {}
    assert window.listedFileInfo[second]["title"] == "Title 2"
    assert window.listedFileInfo[second]["order_key"] == expected_order[second]
    assert window.later_actions == ["rename", "catalog", "sidecars", "summary"]
    assert window.messages[-1][0] == "Save Complete"


def test_cancel_at_progress_maximum_still_defers_later_output_stages(save_window):
    window = save_window(eseq=True, outputs=True)
    window.pendingRegularRenames[window.paths[1]] = "RENAMED.FIL"
    window.cancel_after = 2

    window.save_pending_changes()

    assert len(window.writes) == 2
    assert window.pendingEdits == {}
    assert window.later_actions == []
    assert window.pendingRegularRenames
    assert window.pendingGeneratePianodir
    assert "2 file(s) saved; 0 file(s) remaining." in window.status_label.text()
    _assert_not_complete(window)

    window.cancel_after = None
    window.save_pending_changes()

    assert len(window.writes) == 2
    assert window.later_actions == ["rename", "catalog", "sidecars", "summary"]
    assert window.messages[-1][0] == "Save Complete"


@pytest.mark.parametrize("failure_stage", ["write", "backup"])
def test_failed_title_remains_pending_and_retry_skips_successful_file(save_window, failure_stage):
    window = save_window(outputs=True)
    first, second = window.paths
    window.failures[(first, failure_stage)] = "first.mid: destination is read-only"

    window.save_pending_changes()

    assert window.pendingEdits == {first: "Title 1"}
    assert window.listedFileInfo[first]["title"] == "Old"
    assert window.listedFileInfo[second]["title"] == "Title 2"
    assert read_first_title_from_midi(first) == "Old"
    assert read_first_title_from_midi(second) == "Title 2"
    assert window.later_actions == []
    assert window.errors[0][0] == "Save Failed"
    _assert_not_complete(window)

    window.failures.clear()
    window.save_pending_changes()

    assert sum(path == second for path, _title, _order in window.writes) == 1
    assert window.pendingEdits == {}
    assert read_first_title_from_midi(first) == "Title 1"
    assert window.later_actions == ["sidecars", "summary"]
    assert window.messages[-1][0] == "Save Complete"


def test_invalid_title_stays_pending_without_blocking_valid_title(save_window):
    window = save_window()
    first, second = window.paths
    window.pendingEdits[first] = "Invalid\x00title"

    window.save_pending_changes()

    assert window.pendingEdits == {first: "Invalid\x00title"}
    assert window.writes == [(second, "Title 2", None)]
    assert first not in window.backups
    assert read_first_title_from_midi(first) == "Old"
    assert read_first_title_from_midi(second) == "Title 2"
    assert window.errors
    _assert_not_complete(window)


@pytest.mark.parametrize("cancel_after", [0, 1])
def test_conversion_cancel_keeps_unfinished_rows_and_retry_skips_saved_outputs(save_window, cancel_after):
    window = save_window(outputs=True)
    destinations = window.stage_conversions([0, 1])
    original_sources = {path: Path(path).read_bytes() for path in window.paths}
    window.cancel_after = cancel_after

    window.save_pending_changes()

    saved = window.paths[:cancel_after]
    remaining = window.paths[cancel_after:]
    assert window.conversion_writes == [(path, destinations[path]) for path in saved]
    assert set(window.pendingRegularConversions) == set(remaining)
    assert set(window.pendingEdits) == set(remaining)
    assert window.table.rowCount() == 2
    assert [window.table.item(row, 1).text() for row in range(2)] == [
        destinations[path] if path in saved else path for path in window.paths
    ]
    assert window.later_actions == []
    for path in saved:
        assert read_first_title_from_midi(destinations[path]) == window.table.item(window.paths.index(path), 4).text()
    for path in remaining:
        assert not Path(destinations[path]).exists()
        assert Path(window.pendingRegularConversions[path]["temp_path"]).exists()
    assert {path: Path(path).read_bytes() for path in window.paths} == original_sources
    _assert_not_complete(window)

    window.cancel_after = None
    window.save_pending_changes()

    assert window.conversion_writes == [(path, destinations[path]) for path in window.paths]
    assert window.pendingRegularConversions == {}
    assert window.pendingEdits == {}
    assert all(Path(destination).exists() for destination in destinations.values())
    assert set(window.listedFileInfo) == set(destinations.values())
    assert window.messages[-1][0] == "Save Complete"


def test_conversion_failure_retains_only_failed_row_and_retry_avoids_existing_success(save_window):
    window = save_window(outputs=True)
    first, second = window.paths
    destinations = window.stage_conversions([0, 1])
    window.failures[(first, "write")] = "Could not write first.mid: simulated I/O error"

    window.save_pending_changes()

    assert set(window.pendingRegularConversions) == {first}
    assert window.pendingEdits == {first: "Title 1"}
    assert not Path(destinations[first]).exists()
    assert read_first_title_from_midi(destinations[second]) == "Title 2"
    assert window.table.item(0, 1).text() == first
    assert window.table.item(1, 1).text() == destinations[second]
    assert window.later_actions == []
    assert window.errors
    _assert_not_complete(window)

    window.failures.clear()
    window.save_pending_changes()

    assert window.conversion_writes == [
        (first, destinations[first]),
        (second, destinations[second]),
        (first, destinations[first]),
    ]
    assert window.pendingRegularConversions == {}
    assert window.pendingEdits == {}
    assert read_first_title_from_midi(destinations[first]) == "Title 1"
    assert window.messages[-1][0] == "Save Complete"


def test_conversion_and_ordinary_title_edit_are_both_saved(save_window):
    window = save_window()
    first, second = window.paths
    destinations = window.stage_conversions([0])

    window.save_pending_changes()

    assert window.conversion_writes == [(first, destinations[first])]
    assert window.writes == [(second, "Title 2", None)]
    assert read_first_title_from_midi(first) == "Old"
    assert read_first_title_from_midi(destinations[first]) == "Title 1"
    assert read_first_title_from_midi(second) == "Title 2"
    assert window.pendingEdits == {}
    assert window.pendingRegularConversions == {}
    assert set(window.listedFileInfo) == {destinations[first], second}
    assert window.messages[-1][0] == "Save Complete"


@pytest.mark.parametrize("destination_kind", ["original", "existing_copy", "new_copy"])
def test_eseq_combined_title_order_edit_does_not_publish_title_when_order_fails(tmp_path, monkeypatch, destination_kind):
    from aps_midi_prep_tool_app.eseq_converter import convert_midi_file_to_eseq_path

    midi = tmp_path / "song.mid"
    source = tmp_path / "song.fil"
    midi.write_bytes(_midi_bytes())
    convert_midi_file_to_eseq_path(midi, source)
    original = source.read_bytes()
    destination = source if destination_kind == "original" else tmp_path / "copy.fil"
    if destination_kind == "existing_copy":
        destination.write_bytes(b"previous destination")
    expected_destination = destination.read_bytes() if destination.exists() else None
    staged_titles = []

    def fail_order(path, order_key):
        staged_titles.append(main_window.extract_eseq_title_from_file(path))
        return "Order write failed"

    monkeypatch.setattr(main_window, "update_eseq_order_key", fail_order)

    error = MidiTitleWindow._write_eseq_file_to_path(
        SimpleNamespace(), source, destination, title="Updated title", order_key=b"001",
    )

    assert error == "Order write failed"
    assert staged_titles == ["Updated title"]
    assert source.read_bytes() == original
    if expected_destination is None:
        assert not destination.exists()
    else:
        assert destination.read_bytes() == expected_destination
    assert not list(tmp_path.glob(".aps_eseq_*"))


@pytest.mark.parametrize("destination_kind", ["original", "existing_copy"])
def test_eseq_combined_title_order_edit_rejects_read_only_destination(tmp_path, destination_kind):
    from aps_midi_prep_tool_app.eseq_converter import convert_midi_file_to_eseq_path

    midi = tmp_path / "song.mid"
    source = tmp_path / "song.fil"
    midi.write_bytes(_midi_bytes())
    convert_midi_file_to_eseq_path(midi, source)
    destination = source if destination_kind == "original" else tmp_path / "copy.fil"
    if destination_kind == "existing_copy":
        destination.write_bytes(b"previous destination")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    destination.chmod(0o444)
    try:
        if os.access(destination, os.W_OK):
            pytest.skip("The current user or filesystem does not enforce read-only permissions")

        error = MidiTitleWindow._write_eseq_file_to_path(
            SimpleNamespace(), source, destination, title="Updated title", order_key=b"001",
        )

        assert error is not None
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
        assert not os.access(destination, os.W_OK)
    finally:
        destination.chmod(0o644)


@pytest.mark.parametrize("destination_exists", [False, True])
@pytest.mark.parametrize("edit", ["copy", "order", "title_and_order"])
def test_eseq_export_can_read_protected_source(tmp_path, destination_exists, edit):
    from aps_midi_prep_tool_app.eseq_converter import convert_midi_file_to_eseq_path
    from aps_midi_prep_tool_app.eseq_pianodir import read_eseq_order_key_from_file

    midi = tmp_path / "song.mid"
    source = tmp_path / "song.fil"
    destination = tmp_path / "copy.fil"
    midi.write_bytes(_midi_bytes())
    convert_midi_file_to_eseq_path(midi, source)
    original = source.read_bytes()
    if destination_exists:
        destination.write_bytes(b"previous destination")
    source.chmod(0o444)
    try:
        error = MidiTitleWindow._write_eseq_file_to_path(
            SimpleNamespace(), source, destination,
            title="Updated title" if edit == "title_and_order" else None,
            order_key=b"001" if edit != "copy" else None,
        )

        assert error is None
        assert source.read_bytes() == original
        if edit == "copy":
            assert destination.read_bytes() == original
        else:
            assert read_eseq_order_key_from_file(destination) == normalize_eseq_order_key(b"001")
            expected_title = "Updated title" if edit == "title_and_order" else "Old"
            assert main_window.extract_eseq_title_from_file(destination) == expected_title
        assert set(tmp_path.iterdir()) == {midi, source, destination}
    finally:
        source.chmod(0o644)
