"""Keep the Linux typography proportions when the desktop uses another font size."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window


@pytest.fixture
def make_window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    original_font = QFont(app.font())
    original_palette = QPalette(app.palette())
    original_style = main_window._base_style_name(app.style())
    original_stylesheet = app.styleSheet()
    original_appearance = app.property("_aps_appearance_mode")
    windows = []
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)

    def create(point_size=12, *, density="regular", pixel_size=None):
        font = QFont(original_font)
        if pixel_size is None:
            font.setPointSizeF(point_size)
        else:
            font.setPixelSize(pixel_size)
        app.setFont(font)
        settings = QSettings(str(tmp_path / f"appearance-{len(windows)}.ini"), QSettings.IniFormat)
        settings.setValue("preparation_profile", "custom")
        settings.setValue("font_scale", density)
        settings.setValue("language", "en")
        instance = main_window.MidiTitleWindow(settings=settings)
        windows.append(instance)
        instance.show()
        app.processEvents()
        return instance

    yield create

    for window in windows:
        window._confirm_discard_image_changes = lambda: True
        window.close()
        window.deleteLater()
    app.processEvents()
    main_window._set_application_base_style(app, original_style)
    app.setStyleSheet(original_stylesheet)
    app.setPalette(original_palette)
    app.setFont(original_font)
    app.setProperty("_aps_appearance_mode", original_appearance)


ACTION_WIDGETS = (
    "choose_button", "open_image_button", "read_floppy_button",
    "clearButton", "saveButton", "saveAsButton", "saveAsImageButton",
)


def _margins(layout):
    margins = layout.contentsMargins()
    return margins.left(), margins.top(), margins.right(), margins.bottom()


@pytest.mark.parametrize("point_size", [9, 12, 16])
@pytest.mark.parametrize("density,factor", [("regular", 1.0), ("small", 0.92), ("compact", 0.84)])
def test_action_typography_follows_body_font_and_still_fits(make_window, point_size, density, factor):
    window = make_window(point_size, density=density)
    body_font = window.backup_checkbox.font()
    assert body_font.pointSizeF() == pytest.approx(point_size * factor)

    for name in ACTION_WIDGETS:
        button = getattr(window, name)
        font = button.font()
        assert font.family() == body_font.family()
        assert font.bold()
        assert font.pointSizeF() / body_font.pointSizeF() == pytest.approx(1.5)
        # Inspect the actual widget geometry after Qt has laid out the window.
        # Different platform styles may add different padding, but text must fit.
        metrics = button.fontMetrics()
        assert button.contentsRect().width() >= metrics.horizontalAdvance(button.text())
        assert button.contentsRect().height() >= metrics.height()

    banner_font = window.modeBannerLabel.font()
    assert banner_font.family() == body_font.family()
    assert banner_font.pointSizeF() / body_font.pointSizeF() == pytest.approx(14 / 12)
    assert window.title_monospace_font.styleHint() == QFont.Monospace


def test_regular_linux_reference_keeps_existing_control_sizes(make_window):
    window = make_window(12)
    assert window.choose_button.font().pointSizeF() == 18
    assert window.modeBannerLabel.font().pointSizeF() == 14
    for panel, grid in window._controlPanelLayoutPairs:
        assert _margins(panel) == (10, 14, 10, 10)
        assert grid.verticalSpacing() == 6
        assert all(grid.rowMinimumHeight(row) == 40 for row in range(3))
    assert window.renameAllButton.minimumHeight() == 36


def test_control_rows_adapt_to_desktop_body_size(make_window):
    heights = []
    margins = []
    for point_size in (9, 12, 16):
        window = make_window(point_size)
        panel, grid = window._controlPanelLayoutPairs[0]
        heights.append(grid.rowMinimumHeight(0))
        margins.append(panel.contentsMargins().top())
    assert heights[0] < heights[1] < heights[2]
    assert margins[0] < margins[1] < margins[2]


def test_density_changes_return_to_original_font_and_spacing(make_window):
    window = make_window(9)

    def snapshot():
        panel, grid = window._controlPanelLayoutPairs[0]
        return (
            QFont(window.backup_checkbox.font()),
            QFont(window.choose_button.font()),
            grid.rowMinimumHeight(0),
            _margins(panel),
        )

    original = snapshot()
    window._apply_font_scale("compact")
    QApplication.processEvents()
    compact = snapshot()
    assert compact[0].pointSizeF() < original[0].pointSizeF()
    assert compact[1].pointSizeF() < original[1].pointSizeF()
    assert compact[2] <= original[2]
    window._apply_font_scale("small")
    window._apply_font_scale("regular")
    QApplication.processEvents()
    assert snapshot() == original


def test_pixel_sized_desktop_font_keeps_heading_proportions(make_window):
    window = make_window(pixel_size=16)
    body_font = window.backup_checkbox.font()
    heading_font = window.choose_button.font()
    assert body_font.pixelSize() == 16
    assert heading_font.pixelSize() == 24
    assert heading_font.family() == body_font.family()
    assert heading_font.bold()
