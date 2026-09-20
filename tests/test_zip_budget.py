"""ZIP imports share a bounded extraction budget until their files are released."""

import os
from pathlib import Path
import zipfile

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from aps_midi_prep_tool_app import drop_table_widget, zip_import
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def table(application):
    parent = QWidget()
    parent.can_accept_regular_drop_path = lambda path: path.endswith(".mid")
    parent.errors = []
    parent._show_error_list = lambda _title, _summary, details, **_kwargs: parent.errors.extend(details)
    widget = drop_table_widget.DropTableWidget(0, 2, parent)
    yield widget
    widget.cleanup_zip_imports()
    parent.deleteLater()
    application.processEvents()


def _archive(tmp_path, name, entries):
    archive_path = tmp_path / name
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename, data in entries.items():
            archive.writestr(filename, data)
    return str(archive_path)


@pytest.mark.parametrize("limit", ["bytes", "files"])
@pytest.mark.parametrize("separate_drops", [False, True])
def test_multiple_archives_share_budget_in_one_drop_and_across_drops(
    table, tmp_path, monkeypatch, limit, separate_drops,
):
    first = _archive(tmp_path, "first.zip", {"song.mid": b"ab", "notes.txt": b"1234"})
    second_entries = {"other.mid": b"abc"} if limit == "bytes" else {"other.mid": b"", "notes.txt": b""}
    second = _archive(tmp_path, "second.zip", second_entries)
    monkeypatch.setattr(zip_import, "MAX_SESSION_UNCOMPRESSED_BYTES", 8)
    monkeypatch.setattr(zip_import, "MAX_SESSION_FILES", 3)
    parent = table.window()

    if separate_drops:
        paths = table._expand_zip_paths(parent, [first])
        paths += table._expand_zip_paths(parent, [second])
    else:
        paths = table._expand_zip_paths(parent, [first, second])

    assert [Path(path).read_bytes() for path in paths] == [b"ab"]
    assert len(table._zip_imports) == 1
    root = table._zip_imports[0][0].name
    assert (Path(root) / "notes.txt").read_bytes() == b"1234"
    assert table._zip_import_usage == {root: (6, 2)}
    assert len(parent.errors) == 1
    assert parent.errors[0].startswith("second.zip: ")
    assert zip_import._SESSION_LIMIT in parent.errors[0]


def test_unsupported_archive_releases_budget_for_next_archive(table, tmp_path, monkeypatch):
    unsupported = _archive(tmp_path, "unsupported.zip", {"notes.txt": b"1234"})
    supported = _archive(tmp_path, "supported.zip", {"song.mid": b"1234"})
    monkeypatch.setattr(zip_import, "MAX_SESSION_UNCOMPRESSED_BYTES", 4)
    monkeypatch.setattr(zip_import, "MAX_SESSION_FILES", 1)

    paths = table._expand_zip_paths(table.window(), [unsupported, supported])

    assert [Path(path).read_bytes() for path in paths] == [b"1234"]
    assert list(table._zip_import_usage.values()) == [(4, 1)]
    assert len(table.window().errors) == 1
    assert "No supported files" in table.window().errors[0]


def test_cancelled_multi_archive_drop_releases_all_new_reservations(table, tmp_path, monkeypatch):
    previous = _archive(tmp_path, "previous.zip", {"previous.mid": b"old"})
    previous_paths = table._expand_zip_paths(table.window(), [previous])
    retained_usage = dict(table._zip_import_usage)
    first = _archive(tmp_path, "first.zip", {"one.mid": b"one"})
    second = _archive(tmp_path, "second.zip", {"two.mid": b"two"})
    real_extract = drop_table_widget.extract_zip
    new_roots = []

    def cancel_second(path, destination, **kwargs):
        new_roots.append(Path(destination))
        if path == second:
            kwargs["is_cancelled"] = lambda: True
        return real_extract(path, destination, **kwargs)

    monkeypatch.setattr(drop_table_widget, "extract_zip", cancel_second)

    assert table._expand_zip_paths(table.window(), [first, second]) is None
    assert table._zip_import_usage == retained_usage
    assert len(table._zip_imports) == 1
    assert all(not root.exists() for root in new_roots)
    assert all(Path(path).exists() for path in previous_paths)


def test_removing_extractions_makes_budget_available_again(table, tmp_path, monkeypatch):
    archive = _archive(tmp_path, "songs.zip", {"song.mid": b"1234"})
    monkeypatch.setattr(zip_import, "MAX_SESSION_UNCOMPRESSED_BYTES", 4)
    monkeypatch.setattr(zip_import, "MAX_SESSION_FILES", 1)
    first_paths = table._expand_zip_paths(table.window(), [archive])

    table.cleanup_zip_imports()

    assert table._zip_import_usage == {}
    assert all(not Path(path).exists() for path in first_paths)
    paths = table._expand_zip_paths(table.window(), [archive])
    assert [Path(path).read_bytes() for path in paths] == [b"1234"]
    assert table.window().errors == []


@pytest.mark.parametrize("code", [language.code for language in SUPPORTED_LANGUAGES])
def test_shared_budget_errors_are_localized(table, tmp_path, monkeypatch, code):
    archive = _archive(tmp_path, "songs.zip", {"song.mid": b"12"})
    monkeypatch.setattr(zip_import, "MAX_SESSION_UNCOMPRESSED_BYTES", 1)
    parent = table.window()
    parent._lt = lambda source: translate_text(source, code)

    assert table._expand_zip_paths(parent, [archive]) == []

    translated = translate_text(zip_import._SESSION_LIMIT, code)
    if code != "en":
        assert translated != zip_import._SESSION_LIMIT
    assert parent.errors == ["songs.zip: " + translate_text(
        "Could not extract ZIP file: {error}", code, error=translated,
    )]
    assert table._zip_import_usage == {}
