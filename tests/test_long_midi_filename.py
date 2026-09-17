import unittest
from types import MethodType

from aps_midi_prep_tool_app.long_midi_filename import build_long_midi_filename
from aps_midi_prep_tool_app.main_window import MidiTitleWindow


class _FakeItem:
    def __init__(self, text):
        self._text = text
        self._tooltip = ""

    def text(self):
        return self._text

    def setText(self, text):
        self._text = text

    def setToolTip(self, tooltip):
        self._tooltip = tooltip

    def toolTip(self):
        return self._tooltip


class _FakeTable:
    def __init__(self, rows):
        self._rows = rows

    def rowCount(self):
        return len(self._rows)

    def item(self, row, column):
        return self._rows[row].get(column)


class _FakeEnabledButton:
    @staticmethod
    def isEnabled():
        return True


class _FakeLabel:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


class LongMidiFilenameTests(unittest.TestCase):
    def test_uses_track_number_and_song_title(self):
        self.assertEqual(
            build_long_midi_filename(3, "Moon River", "PIANO002.FIL", track_count=12),
            "03 - Moon River.mid",
        )

    def test_sanitizes_windows_filename_characters_and_whitespace(self):
        self.assertEqual(
            build_long_midi_filename(1, '  My: Song / Take * 2?  '),
            "01 - My- Song - Take - 2-.mid",
        )

    def test_falls_back_to_source_stem_for_blank_titles(self):
        self.assertEqual(
            build_long_midi_filename(1, "", "PIANO000.FIL"),
            "01 - PIANO000.mid",
        )

    def test_supports_three_digit_track_numbers(self):
        self.assertEqual(
            build_long_midi_filename(7, "Finale", track_count=120),
            "007 - Finale.mid",
        )

    def test_limits_unicode_names_to_portable_utf8_component_size(self):
        filename = build_long_midi_filename(1, "演" * 200)
        self.assertLessEqual(len(filename.encode("utf-8")), 240)
        self.assertTrue(filename.endswith(".mid"))

    def test_image_utility_shows_descriptive_export_names_in_filename_column(self):
        paths = ["PS204-03.MID", "PS204-05.MID"]
        titles = {
            paths[0]: "UP  WHERE       WE  BELONG",
            paths[1]: "SINGIN'        IN THE RAIN",
        }

        class FakeWindow:
            @staticmethod
            def _lt(source, **values):
                return source.format(**values)

            def __init__(self):
                self.choose_button = _FakeEnabledButton()
                self.table = _FakeTable(
                    [
                        {1: _FakeItem(path), 3: _FakeItem(path)}
                        for path in paths
                    ]
                )
                self.pendingImageExportFilenames = {}
                self.pendingImageTitleEdits = {}
                self.status_label = _FakeLabel()
                self._long_midi_filename_for_row = MethodType(
                    MidiTitleWindow._long_midi_filename_for_row,
                    self,
                )
                self._refresh_image_filename_display = MethodType(
                    MidiTitleWindow._refresh_image_filename_display,
                    self,
                )

            @staticmethod
            def is_image_mode():
                return True

            @staticmethod
            def _is_special_pianodir_row(_row):
                return False

            @staticmethod
            def _image_path_is_midi(_path):
                return True

            @staticmethod
            def _dos83_filenames_enabled():
                return True

            @staticmethod
            def _final_image_path(path):
                return path

            @staticmethod
            def _join_image_path(directory, filename):
                return f"{directory}/{filename}" if directory else filename

            @staticmethod
            def _update_menu_actions():
                return None

            @staticmethod
            def _listed_file_info(_path):
                return {}

            def _image_info_for_path(self, path):
                return {"title": titles[path]}

        window = FakeWindow()
        MidiTitleWindow.create_long_midi_filenames(window)

        self.assertEqual(window.table.item(0, 3).text(), "01 - UP WHERE WE BELONG.mid")
        self.assertEqual(window.table.item(1, 3).text(), "02 - SINGIN' IN THE RAIN.mid")
        self.assertEqual(
            MidiTitleWindow._image_folder_export_path(window, paths[0], paths[0]),
            "01 - UP WHERE WE BELONG.mid",
        )
        self.assertIn("Disk/image filename: PS204-03.MID", window.table.item(0, 3).toolTip())
        self.assertIn("Filename column shows the Save As names", window.status_label.text)


if __name__ == "__main__":
    unittest.main()
