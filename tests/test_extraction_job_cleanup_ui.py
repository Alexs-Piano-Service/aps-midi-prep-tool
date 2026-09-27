"""Resume suggestions reflect only progress records that still exist."""

import pytest
from PySide6.QtWidgets import QCheckBox, QDialog

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.bulk_extraction import BulkExtractionResult
from aps_midi_prep_tool_app.bulk_extraction_job import localize_extraction_job_error
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES, translate_text
from test_inspection_staging import window  # noqa: F401


@pytest.mark.parametrize("scenario", ["completed", "failed", "empty", "other_job"])
def test_completion_clears_only_its_own_stale_resume_suggestion(window, tmp_path, monkeypatch, scenario):
    job = tmp_path / "job.json"
    other = str(tmp_path / "other-job.json")
    window.bulkExtractionContext = {"job_record_path": str(job)}
    key = window.SETTING_BULK_EXTRACTION_LAST_JOB
    window.settings.setValue(key, other if scenario == "other_job" else str(job))
    if scenario == "failed":
        job.write_text("Saved progress")
    result = BulkExtractionResult(
        str(tmp_path), str(tmp_path / "output"),
        0 if scenario == "empty" else 1, 0 if scenario == "empty" else 1,
        0 if scenario == "empty" else 2, 0, False, (),
        ("Could not extract one song",) if scenario == "failed" else (),
        job_record_path=str(job) if scenario == "failed" else "",
    )
    messages = []
    monkeypatch.setattr(main_window.QMessageBox, "information", lambda *args: messages.append(args[-1]))
    monkeypatch.setattr(window, "_show_error_list", lambda _title, summary, *_args, **_kwargs: messages.append(summary))
    window._on_bulk_extraction_success(result)
    expected = other if scenario == "other_job" else str(job) if scenario == "failed" else ""
    assert window.settings.value(key, "") == expected
    assert len(messages) == 1
    assert ("Resume this job from:" in messages[0]) == (scenario == "failed")


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_cleanup_hint_and_errors_are_localized(window, monkeypatch, language):
    window._set_language(language)
    hint = "Progress records are removed after successful extraction. Failed or cancelled jobs keep their records for resuming."
    inspected = []

    def inspect(dialog, **_kwargs):
        checkbox = next(widget for widget in dialog.findChildren(QCheckBox)
                        if widget.text() == translate_text("Save progress for verified resume", language))
        assert checkbox.toolTip() == translate_text(hint, language)
        inspected.append(True)
        return QDialog.Rejected

    monkeypatch.setattr(window, "_exec_child_dialog", inspect)
    window.show_bulk_extraction_utility()
    assert inspected == [True]
    path = "C:/Albums (Original)/job.json"
    for template, fields in (
        ("The extraction job changed before cleanup: {path}", {"path": path}),
        ("Could not remove completed extraction job: {path} ({error})", {"path": path, "error": "Permission denied"}),
    ):
        assert localize_extraction_job_error(template.format(**fields), language) == translate_text(template, language, **fields)
