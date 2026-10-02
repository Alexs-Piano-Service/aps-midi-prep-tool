"""Bulk extraction keeps its form usable while users resize or resume a job."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialogButtonBox, QLabel,
    QLineEdit, QPushButton, QScrollArea,
)

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    original_font = QFont(app.font())
    settings = QSettings(str(tmp_path / "bulk-dialog.ini"), QSettings.IniFormat)
    monkeypatch.setenv("APS_MIDI_RENAME_RECOVERY_DIR", str(tmp_path / "rename-recovery"))
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_args, **_kwargs: None)
    w = main_window.MidiTitleWindow()
    source = tmp_path / "Floppy images"
    source.mkdir()
    output = tmp_path / "Extracted songs"
    settings.setValue(w.SETTING_BULK_EXTRACTION_SOURCE, str(source))
    settings.setValue(w.SETTING_BULK_EXTRACTION_OUTPUT, str(output))
    settings.setValue(w.SETTING_BULK_EXTRACTION_CONVERT_ESEQ, True)
    settings.setValue(w.SETTING_BULK_EXTRACTION_LONG_MIDI_FILENAMES, True)
    settings.setValue(w.SETTING_BULK_EXTRACTION_TRIM_TITLE_SPACES, True)
    settings.setValue(w.SETTING_BULK_EXTRACTION_INCLUDE_ESEQ_SOURCES, False)
    settings.setValue(w.SETTING_BULK_EXTRACTION_USE_ALBUM_NAMES, False)
    yield w, source, output
    w.deleteLater()
    app.setFont(original_font)
    app.processEvents()


def _controls(window, dialog):
    def checkbox(key):
        return next(widget for widget in dialog.findChildren(QCheckBox)
                    if widget.text() == window._t(key))

    edits = dialog.findChildren(QLineEdit)
    return {
        "source": next(edit for edit in edits
                       if edit.placeholderText() == window._t("bulk.source.placeholder")),
        "output": next(edit for edit in edits
                       if edit.placeholderText() == window._t("bulk.output.placeholder")),
        "convert": checkbox("bulk.convert"),
        "preserve_volume": dialog.findChild(QCheckBox, "bulkPreserveOriginalVolumeControls"),
        "long_names": checkbox("bulk.long_filenames"),
        "trim": checkbox("bulk.trim_titles"),
        "include": checkbox("bulk.include_sources"),
        "naming": dialog.findChild(QComboBox),
        "save": next(widget for widget in dialog.findChildren(QCheckBox)
                     if widget.text() == window._lt("Save progress for verified resume")),
        "resume": next(widget for widget in dialog.findChildren(QPushButton)
                       if widget.text() == window._lt("Resume extraction job...")),
    }


def _assert_buttons_visible(dialog):
    boxes = dialog.findChildren(QDialogButtonBox)
    assert len(boxes) == 1
    buttons = boxes[0]
    assert dialog.rect().contains(buttons.geometry())
    scroll = dialog.findChild(QScrollArea)
    assert scroll is not None
    assert dialog.rect().contains(scroll.geometry())
    assert scroll.geometry().bottom() < buttons.geometry().top()
    for button in buttons.buttons():
        assert button.isVisible()
        assert buttons.rect().contains(button.geometry())
        assert button.width() >= button.sizeHint().width()
    assert not buttons.button(QDialogButtonBox.Ok).geometry().intersects(
        buttons.button(QDialogButtonBox.Cancel).geometry()
    )


def _run_dialog(window, monkeypatch, exercise):
    execute = window._exec_child_dialog
    failures = []

    def inspect(dialog, **kwargs):
        assert kwargs.get("resize_to_contents") is False

        def exercise_visible_dialog():
            try:
                exercise(dialog)
            except BaseException as exc:
                failures.append(exc)
            finally:
                if dialog.isVisible():
                    dialog.reject()

        QTimer.singleShot(150, exercise_visible_dialog)
        return execute(dialog, **kwargs)

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    window.show_bulk_extraction_utility()
    if failures:
        raise failures[0]


def _resume_job(window, monkeypatch, tmp_path):
    source = tmp_path / "Resumed floppy images"
    source.mkdir()
    output = tmp_path / "Resumed MIDI songs"
    job_path = str(tmp_path / ("Saved extraction with a long descriptive name " * 4 + ".json"))
    job = {
        "source_directory": str(source),
        "output_directory": str(output),
        "options": {
            "convert_eseq": True,
            "preserve_volume_controls": True,
            "long_midi_filenames": False,
            "trim_title_spaces": False,
            "include_eseq_sources": True,
            "use_album_names": True,
        },
    }
    monkeypatch.setattr(main_window.QFileDialog, "getOpenFileName", lambda *_a, **_k: (job_path, ""))
    monkeypatch.setattr(main_window, "read_extraction_job", lambda path: job if path == job_path else None)
    return job_path, job


@pytest.mark.parametrize("font_size", [9, 14])
def test_live_bulk_form_keeps_user_geometry_and_options_when_resized_or_resumed(
    window, monkeypatch, tmp_path, font_size,
):
    w, source, output = window
    QApplication.instance().setFont(QFont(w.font().family(), font_size))
    w.setFont(QFont(w.font().family(), font_size))
    job_path, job = _resume_job(w, monkeypatch, tmp_path)
    launches = []
    monkeypatch.setattr(w, "_start_bulk_extraction", lambda *args, **kwargs: launches.append((args, kwargs)))

    def exercise(dialog):
        controls = _controls(w, dialog)
        assert dialog.font().pointSize() == font_size
        controls["include"].setChecked(True)
        controls["trim"].setChecked(False)
        controls["naming"].setCurrentIndex(1)
        for size in ((1100, 800), (700, 520), (960, 680)):
            dialog.resize(*size)
            dialog.move(20, 30)
            QTest.qWait(130)
            assert dialog.size().toTuple() == size
            assert dialog.pos().toTuple() == (20, 30)
            geometry = dialog.geometry()
            for convert in (False, True):
                controls["convert"].setChecked(convert)
                QTest.qWait(130)
                assert dialog.geometry() == geometry
                assert controls["include"].isEnabled() is convert
                assert controls["preserve_volume"].isEnabled() is convert
                assert controls["trim"].isEnabled() is convert
                assert controls["include"].isChecked()
                assert not controls["trim"].isChecked()
                assert controls["long_names"].isChecked()
                assert controls["naming"].currentData() == "album"
                assert controls["source"].text() == str(source)
                assert controls["output"].text() == str(output)
                _assert_buttons_visible(dialog)

        dialog.resize(700, 520)
        QTest.qWait(130)
        geometry = dialog.geometry()
        controls["resume"].click()
        QTest.qWait(130)
        assert dialog.geometry() == geometry
        assert controls["source"].text() == job["source_directory"]
        assert controls["output"].text() == job["output_directory"]
        assert controls["convert"].isChecked()
        assert controls["preserve_volume"].isChecked()
        assert not controls["long_names"].isChecked()
        assert not controls["trim"].isChecked()
        assert controls["include"].isChecked()
        assert controls["save"].isChecked()
        assert controls["naming"].currentData() == "album"
        assert all(not control.isEnabled() for name, control in controls.items() if name != "resume")
        hint = next(label for label in dialog.findChildren(QLabel) if job_path in label.text())
        scroll = dialog.findChild(QScrollArea)
        scroll.ensureWidgetVisible(hint)
        QTest.qWait(130)
        assert not hint.visibleRegion().isEmpty()
        assert dialog.geometry() == geometry
        _assert_buttons_visible(dialog)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Cancel).click()

    _run_dialog(w, monkeypatch, exercise)
    assert launches == []
    assert w.settings.value(w.SETTING_BULK_EXTRACTION_SOURCE) == str(source)
    assert w.settings.value(w.SETTING_BULK_EXTRACTION_OUTPUT) == str(output)


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_bulk_translated_buttons_fit_at_large_font_and_small_window(window, monkeypatch, language):
    w, _source, _output = window
    w.currentLanguage = language
    QApplication.instance().setFont(QFont(w.font().family(), 14))
    w.setFont(QFont(w.font().family(), 14))

    def exercise(dialog):
        assert dialog.font().pointSize() == 14
        available = dialog.screen().availableGeometry()
        assert dialog.width() <= available.width()
        assert dialog.height() <= available.height()
        _assert_buttons_visible(dialog)
        for size in ((700, 520), (1100, 800), (700, 520)):
            dialog.resize(*size)
            dialog.move(20, 30)
            QTest.qWait(130)
            assert dialog.size().toTuple() == size
            assert dialog.pos().toTuple() == (20, 30)
            _assert_buttons_visible(dialog)

    _run_dialog(w, monkeypatch, exercise)


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("preserve_volume", [False, True])
def test_bulk_extract_submits_selected_or_resumed_options(
    window, monkeypatch, tmp_path, resume, preserve_volume,
):
    w, source, output = window
    job_path, job = _resume_job(w, monkeypatch, tmp_path)
    job["options"]["preserve_volume_controls"] = preserve_volume
    if resume:
        # The saved job overrides even an opposite remembered preference.
        w.settings.setValue(w.SETTING_BULK_EXTRACTION_PRESERVE_VOLUME, not preserve_volume)
    launches = []
    monkeypatch.setattr(w, "_start_bulk_extraction", lambda *args, **kwargs: launches.append((args, kwargs)))

    def exercise(dialog):
        controls = _controls(w, dialog)
        if resume:
            controls["resume"].click()
        else:
            assert not controls["preserve_volume"].isChecked()
            controls["preserve_volume"].setChecked(preserve_volume)
            controls["long_names"].setChecked(False)
            controls["trim"].setChecked(False)
            controls["include"].setChecked(True)
            controls["naming"].setCurrentIndex(1)
            controls["save"].setChecked(False)
        dialog.resize(700, 520)
        QTest.qWait(130)
        _assert_buttons_visible(dialog)
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()

    _run_dialog(w, monkeypatch, exercise)
    assert launches == [(
        (job["source_directory"], job["output_directory"]) if resume else (str(source), str(output)),
        {**job["options"], "job_record_path": job_path if resume else None, "resume": resume},
    )]
    assert w.settings.value(w.SETTING_BULK_EXTRACTION_PRESERVE_VOLUME, type=bool) is preserve_volume
