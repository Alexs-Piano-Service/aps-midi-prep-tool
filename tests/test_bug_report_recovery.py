import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QScrollArea, QWidget
import pytest

from aps_midi_prep_tool_app.main_window import MidiTitleWindow


class _BugReportWindow:
    BUG_REPORT_LOG_TAIL_CHARS = 1024

    _normalized_floppy_user_context = staticmethod(
        MidiTitleWindow._normalized_floppy_user_context
    )
    _without_floppy_recovery_human_report = staticmethod(
        MidiTitleWindow._without_floppy_recovery_human_report
    )

    @staticmethod
    def _lt(text):
        return text

    def _bug_report_context(self, *, include_floppy_recovery=True):
        context = {"mode": "midi", "row_count": 0}
        if include_floppy_recovery:
            context["floppy_recovery"] = {
                "attempted_sectors": 120,
                "readable_sectors": 118,
                "bad_sectors": 2,
            }
        return context

    def _build_bug_report_payload(self, **kwargs):
        return MidiTitleWindow._build_bug_report_payload(self, **kwargs)


def test_bug_report_payload_includes_normalized_floppy_context_and_diagnostics():
    window = _BugReportWindow()

    payload = window._build_bug_report_payload(
        summary="Floppy recovery failed",
        description="The disk was not recognized.",
        contact="",
        include_logs=False,
        floppy_user_context={
            "disk_kind": "DISKLAVIER",
            "works_in_original_instrument": "yes",
            "usb_drive_reads_other_disks": "not_tried",
            "media_marking": "2DD",
            "instrument_model": "  DGC1 ENST  " + ("x" * 250),
        },
    )

    assert payload["context"]["floppy_recovery"]["readable_sectors"] == 118
    assert payload["context"]["floppy_user_context"] == {
        "disk_kind": "disklavier",
        "works_in_original_instrument": "yes",
        "usb_drive_reads_other_disks": "not_tried",
        "media_marking": "2dd",
        "instrument_model": ("DGC1 ENST  " + ("x" * 250))[:200],
    }
    assert payload["logs"]["included"] is False


def test_feedback_and_opt_out_omit_recovery_diagnostics():
    window = _BugReportWindow()

    report = window._build_bug_report_payload(
        summary="Unrelated report",
        description="No floppy diagnostics requested.",
        contact="",
        include_logs=False,
        include_floppy_recovery_diagnostics=False,
    )
    feedback = MidiTitleWindow._build_feedback_payload(
        window,
        summary="Feedback",
        description="Looks good.",
        contact="",
        include_logs=False,
    )

    assert "floppy_recovery" not in report["context"]
    assert "floppy_recovery" not in feedback["context"]
    assert feedback["kind"] == "feedback"


@pytest.mark.parametrize("include_diagnostics", [False, True])
def test_bug_report_includes_file_save_phase_and_native_errors(include_diagnostics):
    class Window(_BugReportWindow):
        _bug_report_context = MidiTitleWindow._bug_report_context
        _json_safe_disk_recovery_diagnostics = staticmethod(
            MidiTitleWindow._json_safe_disk_recovery_diagnostics
        )

        @staticmethod
        def is_floppy_mode():
            return True

    window = Window()
    window.image_session = SimpleNamespace(
        source_kind="floppy_usb", source_name="A:", source_ext="img",
        last_floppy_save_diagnostics={
            "stage": "preflight_listing", "status": "failed",
            "target_mutation_attempted": False,
            "files_removed": 0, "files_copied": 0,
            "preflight_listing": {
                "stage": "query_free_space",
                "space_queries": [
                    {"api": "GetDiskFreeSpaceExW", "error": {"winerror": 50}},
                    {"api": "GetDiskFreeSpaceW", "error": {"winerror": 21}},
                ],
            },
            "raw_bytes": b"Never include disk bytes",
        },
    )
    payload = window._build_bug_report_payload(
        summary="Floppy Save Failed", description="", contact="", include_logs=False,
        include_floppy_recovery_diagnostics=include_diagnostics,
    )
    if include_diagnostics:
        diagnostics = payload["context"]["floppy_save"]
        assert diagnostics["stage"] == "preflight_listing"
        assert diagnostics["target_mutation_attempted"] is False
        assert diagnostics["files_removed"] == diagnostics["files_copied"] == 0
        assert diagnostics["preflight_listing"]["space_queries"][0]["error"]["winerror"] == 50
        assert "raw_bytes" not in diagnostics
    else:
        assert "floppy_save" not in payload["context"]
    feedback = MidiTitleWindow._build_feedback_payload(
        window, summary="Feedback", description="", contact="", include_logs=False,
    )
    assert "floppy_save" not in feedback["context"]


def test_recovery_diagnostic_sanitizer_never_serializes_disk_bytes():
    diagnostics = {
        "readable_sectors": 10,
        "raw_sector": b"MThd secret disk bytes",
        "nested": [1, bytearray(b"more disk bytes"), {"ok": True}],
    }

    sanitized = MidiTitleWindow._json_safe_disk_recovery_diagnostics(diagnostics)

    assert sanitized == {
        "readable_sectors": 10,
        "nested": [1, {"ok": True}],
    }


def test_prefilled_report_description_omits_structured_recovery_block():
    window = _BugReportWindow()
    message = (
        "Recovery could read only 43% of the disk.\n\n"
        "Floppy diagnostics\n"
        "------------------\n"
        "Drive: A: | USB\n"
        "Readable sectors: 619"
    )

    description = MidiTitleWindow._prefilled_bug_report_details(window, message)

    assert "Recovery could read only 43% of the disk." in description
    assert "Floppy diagnostics" not in description
    assert "Readable sectors" not in description


def test_bug_report_form_scrolls_but_action_buttons_remain_fixed():
    app = QApplication.instance() or QApplication([])
    inspected = {}

    class Host(QWidget):
        _lt = staticmethod(lambda text: text)
        _json_safe_disk_recovery_diagnostics = staticmethod(
            MidiTitleWindow._json_safe_disk_recovery_diagnostics
        )
        _make_dialog_form_grid = MidiTitleWindow._make_dialog_form_grid
        _make_dialog_form_label = MidiTitleWindow._make_dialog_form_label
        _add_dialog_form_row = MidiTitleWindow._add_dialog_form_row
        _align_dialog_form_labels = MidiTitleWindow._align_dialog_form_labels
        lastDiskRecoveryDiagnostics = {"readable_sectors": 12}
        diskRecoveryContext = {"load_kind": "floppy_usb"}

        @staticmethod
        def is_floppy_mode():
            return False

        def _exec_child_dialog(self, dialog):
            scroll_area = dialog.findChild(QScrollArea)
            buttons = dialog.findChild(QDialogButtonBox)
            inspected.update(
                {
                    "scroll_area": scroll_area,
                    "buttons": buttons,
                    "scroll_index": dialog.layout().indexOf(scroll_area),
                    "buttons_index": dialog.layout().indexOf(buttons),
                    "minimum_height": dialog.minimumSizeHint().height(),
                }
            )
            return QDialog.Rejected

    host = Host()
    MidiTitleWindow.show_bug_report_dialog(host)

    assert app is not None
    assert inspected["scroll_area"] is not None
    assert inspected["scroll_area"].widgetResizable()
    assert inspected["scroll_index"] == 0
    assert inspected["buttons_index"] == 1
    assert inspected["minimum_height"] < 700


def test_bug_report_identifies_the_build_commit(monkeypatch):
    from aps_midi_prep_tool_app import build_info
    monkeypatch.setattr(build_info, "build_identity", lambda: {"commit": "1234abcd", "dirty": True})
    payload = _BugReportWindow()._build_bug_report_payload(
        summary="Save failed", description="Unsupported query", contact="", include_logs=False,
    )
    assert payload["app"]["build"] == {"commit": "1234abcd", "dirty": True}


@pytest.mark.parametrize("changed", [False, True])
def test_save_guidance_distinguishes_untouched_and_partial_media(changed):
    window = SimpleNamespace(_lt=lambda source, **fields: source.format(**fields), image_session=SimpleNamespace(last_floppy_save_diagnostics={
        "status": "failed", "target_mutation_attempted": changed,
        "recovery_directory": "C:/recovery/save-123", "stage": "preflight_listing",
    }))
    guidance = MidiTitleWindow._floppy_operation_error_guidance(
        window, "Windows could not report filesystem space", operation="save_files", file_level=True,
    )
    assert ("APS has not changed the floppy" in guidance) is (not changed)
    assert ("partially written" in guidance) is changed
    assert "C:/recovery/save-123" in guidance
    assert "Save As Image" in guidance
    assert "damaged" not in guidance and "write-protect" not in guidance
