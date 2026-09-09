import pytest

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication

from aps_midi_prep_tool_app import main_window


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def timeline(application):
    widget = main_window.PianoRollTimelineWidget()
    yield widget
    widget.close()
    widget.deleteLater()
    application.processEvents()


def _note(start, end, channel=1):
    return {"start_sec": start, "end_sec": end, "pitch": 60, "channel": channel}


def _pedal(start, end, value=90):
    return {"start_sec": start, "end_sec": end, "controller": 64, "value": value}


def _render(widget, rect):
    image = QImage(rect.width(), rect.height(), QImage.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    try:
        widget.render(painter, QPoint(0, 0), QRegion(rect))
    finally:
        painter.end()
    return image


def test_unordered_intervals_preserve_overlap_and_original_paint_order(timeline):
    notes = [_note(8, 14, 2), _note(1, 40), _note(2, 3), _note(10, 11, 3)]
    pedals = [_pedal(13, 20), _pedal(0, 40), _pedal(9, 12), _pedal(3, 4)]
    timeline.set_notes(notes, 60, pedals)

    assert timeline._intervals_in_window(timeline._note_interval_index, 10, 12) == [
        notes[0], notes[1], notes[3]
    ]
    assert timeline._intervals_in_window(timeline._pedal_interval_index, 10, 12) == [
        pedals[1], pedals[2]
    ]
    assert timeline.display_notes == notes
    assert timeline.display_pedals == pedals


def test_long_sustained_events_survive_many_short_intervals(timeline):
    long_note = _note(0, 1000)
    long_pedal = _pedal(0, 1000)
    notes = [_note(i, i + 0.1) for i in range(1, 900)] + [long_note]
    pedals = [_pedal(i, i + 0.1) for i in range(1, 900)] + [long_pedal]
    timeline.set_notes(notes, 1000, pedals)

    assert timeline._intervals_in_window(timeline._note_interval_index, 950, 960) == [long_note]
    assert timeline._intervals_in_window(timeline._pedal_interval_index, 950, 960) == [long_pedal]


def test_interval_endpoints_and_minimum_width_are_kept(timeline):
    notes = [_note(5, 10), _note(20, 25), _note(15, 14), _note(-3, -1), _note(70, 80)]
    timeline.set_notes(notes, 60)

    assert timeline._intervals_in_window(timeline._note_interval_index, 10, 20) == notes[:3]
    assert timeline._intervals_in_window(timeline._note_interval_index, 0, 0) == [notes[3]]
    assert timeline._intervals_in_window(timeline._note_interval_index, 60, 60) == [notes[4]]


def test_empty_and_replaced_content_clear_both_indexes(timeline):
    timeline.set_notes([_note(0, 50)], 60, [_pedal(0, 50)])
    timeline.set_notes([], 60, [])

    assert timeline._events_for_paint(QRect(0, 0, 800, 400)) == ([], [])
    assert not _render(timeline, QRect(0, 0, 800, 400)).isNull()


@pytest.mark.parametrize("offset,dirty_rect", [
    (0.0, QRect(0, 0, 600, 380)),
    (0.625, QRect(350, 0, 400, 380)),
    (25.375, QRect(620, 0, 360, 380)),
])
def test_visible_interval_paint_matches_full_scan_pixels(timeline, monkeypatch, offset, dirty_rect):
    # Overlap with different channel colors makes painter ordering observable.
    notes = [_note(12, 30, 3), _note(0, 55, 1), _note(14, 20, 2)]
    notes += [_note(i * 0.25, i * 0.25 + 0.02, i % 4 + 1) for i in range(240)]
    pedals = [_pedal(0, 55), _pedal(12, 18, 20)]
    pedals += [_pedal(i * 0.25, i * 0.25, i % 127 + 1) for i in range(240)]
    timeline.set_notes(notes, 60, pedals)
    timeline.resize(timeline.minimumWidth(), 380)
    timeline.set_view_offset_px(offset)
    timeline.set_playhead(15)

    indexed_image = _render(timeline, dirty_rect)
    monkeypatch.setattr(timeline, "_events_for_paint", lambda _rect: (timeline.display_notes, timeline.display_pedals))
    full_scan_image = _render(timeline, dirty_rect)

    assert indexed_image == full_scan_image


def test_fractional_scroll_offset_selects_the_shifted_window(timeline):
    timeline.set_notes([_note(1, 1.1), _note(40, 40.1)], 60)
    timeline.resize(timeline.minimumWidth(), 400)
    timeline.set_view_offset_px(timeline._x_for_seconds(40) - 30.625)

    notes, pedals = timeline._events_for_paint(QRect(20, 0, 20, 400))

    assert notes == [timeline.display_notes[1]]
    assert pedals == []


def test_dense_song_paints_only_visible_note_colors(timeline, monkeypatch):
    notes = [_note(i * 600 / 50000, i * 600 / 50000 + 0.15) for i in range(50000)]
    timeline.set_notes(notes, 600)
    timeline.resize(timeline.minimumWidth(), 400)
    painted_channels = []
    original_color = main_window._midi_channel_color

    def record_color(channel):
        painted_channels.append(channel)
        return original_color(channel)

    monkeypatch.setattr(main_window, "_midi_channel_color", record_color)
    image = _render(timeline, QRect(0, 0, 800, 400))

    assert not image.isNull()
    assert 0 < len(painted_channels) < 2500
    assert len(timeline.display_notes) == 50000
