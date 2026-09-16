import unittest
from types import MethodType

from aps_midi_prep_tool_app.main_window import MidiTitleWindow


class _FakeSettings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def contains(self, key):
        return key in self.values

    def value(self, key, default=None, **_kwargs):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    @staticmethod
    def sync():
        return None


class EseqConversionTitleSpacingTests(unittest.TestCase):
    def test_new_title_spacing_choice_reshows_previously_hidden_dialog(self):
        class FakeWindow:
            SETTING_LONG_MIDI_FILENAMES = MidiTitleWindow.SETTING_LONG_MIDI_FILENAMES
            SETTING_ESEQ_TO_MIDI_LONG_FILENAMES = (
                MidiTitleWindow.SETTING_ESEQ_TO_MIDI_LONG_FILENAMES
            )
            SETTING_READ_FLOPPY_LONG_FILENAMES = (
                MidiTitleWindow.SETTING_READ_FLOPPY_LONG_FILENAMES
            )
            SETTING_ESEQ_TO_MIDI_TRIM_TITLE_SPACES = (
                MidiTitleWindow.SETTING_ESEQ_TO_MIDI_TRIM_TITLE_SPACES
            )
            SETTING_SKIP_ESEQ_TO_MIDI_CONVERSION_PROMPT = (
                MidiTitleWindow.SETTING_SKIP_ESEQ_TO_MIDI_CONVERSION_PROMPT
            )
            SETTING_HIDE_CHOICES_RESET_VERSION = (
                MidiTitleWindow.SETTING_HIDE_CHOICES_RESET_VERSION
            )
            HIDE_CHOICES_RESET_VERSION = MidiTitleWindow.HIDE_CHOICES_RESET_VERSION
            DEFAULT_LONG_MIDI_FILENAMES = MidiTitleWindow.DEFAULT_LONG_MIDI_FILENAMES
            _long_midi_filenames_enabled = MidiTitleWindow._long_midi_filenames_enabled
            _set_long_midi_filenames_enabled = (
                MidiTitleWindow._set_long_midi_filenames_enabled
            )

        window = FakeWindow()
        window.settings = _FakeSettings(
            {
                window.SETTING_ESEQ_TO_MIDI_LONG_FILENAMES: False,
                window.SETTING_SKIP_ESEQ_TO_MIDI_CONVERSION_PROMPT: True,
                window.SETTING_HIDE_CHOICES_RESET_VERSION: window.HIDE_CHOICES_RESET_VERSION,
            }
        )

        MidiTitleWindow._reset_user_hide_choices_if_needed(window)

        self.assertFalse(
            window.settings.values[window.SETTING_SKIP_ESEQ_TO_MIDI_CONVERSION_PROMPT]
        )
        self.assertFalse(
            window.settings.values[window.SETTING_ESEQ_TO_MIDI_TRIM_TITLE_SPACES]
        )

    def test_hidden_conversion_dialog_uses_remembered_title_spacing_choice(self):
        class FakeWindow:
            SETTING_LONG_MIDI_FILENAMES = MidiTitleWindow.SETTING_LONG_MIDI_FILENAMES
            SETTING_ESEQ_TO_MIDI_LONG_FILENAMES = (
                MidiTitleWindow.SETTING_ESEQ_TO_MIDI_LONG_FILENAMES
            )
            SETTING_READ_FLOPPY_LONG_FILENAMES = (
                MidiTitleWindow.SETTING_READ_FLOPPY_LONG_FILENAMES
            )
            SETTING_ESEQ_TO_MIDI_TRIM_TITLE_SPACES = (
                MidiTitleWindow.SETTING_ESEQ_TO_MIDI_TRIM_TITLE_SPACES
            )
            SETTING_SKIP_ESEQ_TO_MIDI_CONVERSION_PROMPT = (
                MidiTitleWindow.SETTING_SKIP_ESEQ_TO_MIDI_CONVERSION_PROMPT
            )
            DEFAULT_LONG_MIDI_FILENAMES = MidiTitleWindow.DEFAULT_LONG_MIDI_FILENAMES
            _long_midi_filenames_enabled = MidiTitleWindow._long_midi_filenames_enabled

        window = FakeWindow()
        window.settings = _FakeSettings(
            {
                window.SETTING_ESEQ_TO_MIDI_LONG_FILENAMES: True,
                window.SETTING_ESEQ_TO_MIDI_TRIM_TITLE_SPACES: True,
                window.SETTING_SKIP_ESEQ_TO_MIDI_CONVERSION_PROMPT: True,
            }
        )

        result = MidiTitleWindow._confirm_eseq_to_midi_conversion(
            window,
            title="Convert",
            message="Convert files?",
        )

        self.assertEqual(result, (True, True, True))

    def test_long_filename_choice_defaults_long_and_is_shared_between_dialogs(self):
        class FakeWindow:
            SETTING_LONG_MIDI_FILENAMES = MidiTitleWindow.SETTING_LONG_MIDI_FILENAMES
            SETTING_ESEQ_TO_MIDI_LONG_FILENAMES = (
                MidiTitleWindow.SETTING_ESEQ_TO_MIDI_LONG_FILENAMES
            )
            SETTING_READ_FLOPPY_LONG_FILENAMES = (
                MidiTitleWindow.SETTING_READ_FLOPPY_LONG_FILENAMES
            )
            DEFAULT_LONG_MIDI_FILENAMES = MidiTitleWindow.DEFAULT_LONG_MIDI_FILENAMES
            _long_midi_filenames_enabled = MidiTitleWindow._long_midi_filenames_enabled
            _set_long_midi_filenames_enabled = (
                MidiTitleWindow._set_long_midi_filenames_enabled
            )

        window = FakeWindow()
        window.settings = _FakeSettings()

        self.assertTrue(window._long_midi_filenames_enabled())

        window._set_long_midi_filenames_enabled(True)

        self.assertTrue(window._long_midi_filenames_enabled())
        self.assertTrue(
            window.settings.values[window.SETTING_ESEQ_TO_MIDI_LONG_FILENAMES]
        )
        self.assertTrue(
            window.settings.values[window.SETTING_READ_FLOPPY_LONG_FILENAMES]
        )

    def test_conversion_spacing_cleanup_only_stages_requested_rows(self):
        class FakeWindow:
            def __init__(self):
                self.titles = {
                    0: "  Moon    River  ",
                    1: "Already Clean",
                    2: "  Summer   Wind ",
                }
                self.staged = {}
                self._trim_title_spacing = MidiTitleWindow._trim_title_spacing
                self._stage_trim_title_spaces_for_rows = MethodType(
                    MidiTitleWindow._stage_trim_title_spaces_for_rows,
                    self,
                )

            def _row_title_spacing_needs_trim(self, row):
                return self._trim_title_spacing(self.titles[row]) != self.titles[row]

            @staticmethod
            def _row_title_mode(_row):
                return "eseq"

            def _row_raw_title(self, row):
                return self.titles[row]

            @staticmethod
            def _validate_trimmed_title(_filename, _title_mode, _new_title):
                return ""

            def _stage_trimmed_title_for_row(self, row, new_title, _title_mode):
                self.staged[row] = new_title
                self.titles[row] = new_title
                return True

            class _Table:
                @staticmethod
                def item(row, column):
                    if column != 3:
                        return None

                    class _Item:
                        @staticmethod
                        def text():
                            return f"TRACK{row}.FIL"

                    return _Item()

            table = _Table()

        window = FakeWindow()
        changed, errors = window._stage_trim_title_spaces_for_rows((0, 1))

        self.assertEqual(changed, 1)
        self.assertEqual(errors, [])
        self.assertEqual(window.staged, {0: "Moon River"})
        self.assertEqual(window.titles[2], "  Summer   Wind ")


if __name__ == "__main__":
    unittest.main()
