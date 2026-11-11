"""Split-export companion warnings stay complete and preserve filesystem data."""

import errno
import os
from string import Formatter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from aps_midi_prep_tool_app.recovery_delivery_translations import RECOVERY_DELIVERY_TRANSLATIONS
from aps_midi_prep_tool_app.write_safety_messages import localize_write_message
from test_save_as_overwrite import window


WARNING_MESSAGES = (
    "Images Created with Companion Warning",
    "Created {count} images successfully:\n{paths}",
    "The companion files could not be copied. Your current files and pending changes are still loaded.",
    "The incomplete companion folder was removed: {path}",
    "The incomplete companion folder could not be removed: {path}\n{error}",
    "Use the completed images listed above. To save the prepared files separately, use Save As and choose a folder with enough free space. To export images again, choose a different output name or folder.",
    "The companion folder changed during export; it was kept.",
)
LANGUAGES = [entry.code for entry in SUPPORTED_LANGUAGES]


def _fields(template):
    return sorted(field for _text, field, _format, _conversion in Formatter().parse(template) if field)


@pytest.mark.parametrize("source", WARNING_MESSAGES)
def test_split_export_companion_warning_catalog_is_complete(source):
    translations = RECOVERY_DELIVERY_TRANSLATIONS[source]
    assert set(translations) == set(LANGUAGES) - {"en"}
    assert translate_text(source, "en") == source
    for language, translated in translations.items():
        assert translated.strip()
        assert translated != source
        assert _fields(translated) == _fields(source)
        assert translate_text(source, language) == translated


@pytest.mark.parametrize("language", LANGUAGES)
def test_split_export_companion_warning_preserves_literal_paths_and_error(language):
    paths = "/media/Concert {count} <piano & duet>_01.img\n/media/Concert {paths} <piano & duet>_02.hfe"
    folder = "/media/Concert {path} <piano & duet>_files"
    error = "[Errno 28] <No space & remaining> on '{error}': " + folder

    published = translate_text(WARNING_MESSAGES[1], language, count=2, paths=paths)
    assert "2" in published.split("\n", 1)[0]
    assert published.split("\n", 1)[1] == paths

    removed = translate_text(WARNING_MESSAGES[3], language, path=folder)
    assert removed.endswith(folder)

    retained = translate_text(WARNING_MESSAGES[4], language, path=folder, error=error)
    assert retained.split("\n", 1)[0].endswith(folder)
    assert retained.split("\n", 1)[1] == error


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("cleanup_state", ["removed", "retained"])
def test_split_export_warning_dialog_shows_all_literal_paths_despite_hidden_completion(
    window, tmp_path, monkeypatch, language, cleanup_state,
):
    window.currentLanguage = language
    window.settings.setValue(window.SETTING_HIDE_SAVE_AS_IMAGE_COMPLETE_DIALOG, True)
    paths = [
        str(tmp_path / f"Concert {{paths}} <piano & duet>_{number:02d}.img")
        for number in range(1, 13)
    ]
    folder = str(tmp_path / "Concert {path} <piano & duet>_files")
    exc = OSError(errno.ENOSPC, "No space left on device", folder + "/Song {error} <A & B>.mid")
    if cleanup_state == "removed":
        exc.export_context_removed_directory = folder
        expected_cleanup = translate_text(WARNING_MESSAGES[3], language, path=folder)
    else:
        exc.export_context_retained_directory = folder
        cleanup_error = translate_text(WARNING_MESSAGES[6], language)
        exc.export_context_cleanup_error = OSError(cleanup_error)
        expected_cleanup = translate_text(WARNING_MESSAGES[4], language, path=folder, error=cleanup_error)
    dialogs = []

    def inspect_dialog(box):
        dialogs.append(box)
        assert isinstance(box, QMessageBox)
        assert box.icon() == QMessageBox.Warning
        assert box.textFormat() == Qt.PlainText
        assert box.windowTitle() == translate_text(WARNING_MESSAGES[0], language)
        message = box.text()
        assert translate_text(WARNING_MESSAGES[1], language, count=12, paths="\n".join(paths)) in message
        assert all(path in message for path in paths)
        assert translate_text(WARNING_MESSAGES[2], language) in message
        assert localize_write_message(str(exc), language) in message
        assert exc.filename in message
        assert expected_cleanup in message
        assert translate_text(WARNING_MESSAGES[5], language) in message
        return QMessageBox.Ok

    monkeypatch.setattr(window, "_exec_child_dialog", inspect_dialog)
    window._show_export_context_failure(paths, exc)

    assert len(dialogs) == 1
    assert window.settings.value(window.SETTING_HIDE_SAVE_AS_IMAGE_COMPLETE_DIALOG, type=bool)
    assert window.status_label.text() == translate_text(WARNING_MESSAGES[0], language)
    assert not window._test_errors
