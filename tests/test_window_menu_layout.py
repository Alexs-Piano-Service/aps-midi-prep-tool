"""Keep the main menubar organized around end-user tasks and stable commands."""

import os
from collections import Counter

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QMenu

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.message_catalog import SUPPORTED_LANGUAGES


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = QSettings(str(tmp_path / "layout.ini"), QSettings.IniFormat)
    settings.setValue("preparation_profile", "custom")
    monkeypatch.setattr(main_window, "QSettings", lambda *_args: settings)
    monkeypatch.setattr(main_window.MidiTitleWindow, "_log_event", lambda *_a, **_k: None)
    instance = main_window.MidiTitleWindow()
    yield instance
    instance._confirm_discard_image_changes = lambda: True
    instance.close()
    app.processEvents()


def _label(action):
    return action.text().split("\t", 1)[0].replace("&", "")


def _menu_contents(window):
    # Retain Python references to the menus while inspecting Qt-owned actions.
    menus = {menu.menuAction(): menu for menu in window.findChildren(QMenu)}
    contents = []

    def visit(menu, path):
        for action in menu.actions():
            if action.isSeparator():
                continue
            submenu = menus.get(action)
            if submenu is not None:
                visit(submenu, path + (submenu,))
            else:
                contents.append((action, path))

    for action in window.menuBar().actions():
        menu = menus[action]
        visit(menu, (menu,))
    return contents


def test_top_level_menus_follow_desktop_order_and_common_open_commands_are_direct(window):
    assert [_label(action) for action in window.menuBar().actions()] == [
        "File", "Edit", "View", "Disk", "Utilities", "Settings", "Help",
    ]
    assert window.fileOpenFolderAction in window.fileMenu.actions()
    assert window.fileOpenImageAction in window.fileMenu.actions()
    assert window.fileQuitAction is window.fileMenu.actions()[-1]


def test_menu_commands_remain_available_once_after_reorganization(window):
    contents = _menu_contents(window)
    counts = Counter(action for action, _path in contents)
    assert all(count == 1 for count in counts.values())
    expected = {
        action for action in vars(window).values()
        if isinstance(action, QAction) and not action.isSeparator()
    }
    for group in (
        window.appearanceActions, window.fontSizeActions,
        window.languageActions, window.titleDisplayEncodingActions,
    ):
        expected.update(group.values())
    assert expected <= counts.keys()


@pytest.mark.parametrize("menu_name,action_names", (
    ("editMenu", (
        "editUndoAction", "editUndoAllAction", "editReviewChangesAction",
        "utilitiesRenameAction", "utilitiesLongFilenamesAction", "utilitiesTrimTitleSpacesAction",
        "utilitiesPedalCompatibilityAction", "utilitiesMergeChannelsAction", "utilitiesStripXfAction",
        "utilitiesSmfAction", "utilitiesEseqToMidiAction", "utilitiesMidiToEseqAction",
    )),
    ("diskMenu", (
        "fileReadFloppyAction", "fileImageFloppyAction", "savePartialCaptureAction",
        "fileSaveToFloppyAction", "fileWriteImageToFloppyAction", "fileRecoverImageAction",
        "utilitiesRepairBootSectorAction", "utilitiesFormatFloppyAction",
    )),
    ("settingsMenu", (
        "preparationAction", "viewFormatDisklavierScreenAction", "fileCreateAlbumSubfolderAction",
        "fileCreateImageAlbumSubfolderAction", "fileBackUpBeforeSavingAction",
        "fileCreateTagSidecarsAction", "fileCreateMetadataSummaryAction", "fileAutoWriteProtectAction",
        "verifyFloppyWriteAction", "settingsUseDos83FilenamesAction", "settingsPianoOverlapAction",
        "settingsKeyboardShortcutsAction", "settingsResetHiddenDialogsAction",
        "helpCheckUpdatesAtStartupAction",
    )),
    ("fileMenu", ("fileWriteProtectOriginalAction",)),
))
def test_commands_live_with_related_tasks(window, menu_name, action_names):
    locations = dict(_menu_contents(window))
    for name in action_names:
        assert locations[getattr(window, name)][0] is getattr(window, menu_name), name


def test_view_menu_contains_only_display_controls(window):
    display_actions = {
        window.viewLongTitleWarningAction, window.viewShowStatusAction,
        window.viewShowQuickPanelAction, window.viewShowAlbumMetadataAction,
        window.viewShowSaveDestinationAction, window.viewShowPreparationRowAction,
        window.viewLogsAction, *window.titleDisplayEncodingActions.values(),
    }
    assert {
        action for action, path in _menu_contents(window) if path[0] is window.viewMenu
    } == display_actions


def test_menu_groups_have_no_empty_separators(window):
    reachable = {menu for _action, path in _menu_contents(window) for menu in path}
    for menu in reachable:
        actions = menu.actions()
        assert actions and not actions[0].isSeparator(), menu.title()
        assert not actions[-1].isSeparator(), menu.title()
        assert not any(left.isSeparator() and right.isSeparator()
                       for left, right in zip(actions, actions[1:])), menu.title()


def test_shortcut_settings_use_the_current_menu_category(window):
    locations = dict(_menu_contents(window))
    for spec in window._keyboard_shortcut_specs():
        top_menu = locations[getattr(window, spec["action"])][0]
        assert spec["category"] == _label(top_menu.menuAction()), spec["id"]


# These IDs are persistent preference keys. Moving an action must retain both
# its existing bindings and its factory default; only Quit is newly assigned.
SHORTCUT_DEFAULTS = {
    "edit.undo": "Ctrl+Z", "edit.undo_all": "", "edit.review": "",
    "file.new_image": "Ctrl+N", "file.open_folder": "Ctrl+O",
    "file.open_image": "Ctrl+Shift+O", "file.read_floppy": "Ctrl+R",
    "file.image_floppy": "Ctrl+I", "file.save": "Ctrl+S",
    "file.save_as": "Ctrl+Shift+S", "file.save_as_zip": "",
    "file.save_as_image": "Ctrl+Shift+I", "file.clear_list": "Ctrl+Shift+Delete",
    "file.save_to_floppy": "Ctrl+F", "file.write_image_to_floppy": "Ctrl+Shift+F",
    "file.auto_write_protect": "Ctrl+Shift+P", "file.write_protect_original": "Ctrl+Alt+P",
    "file.create_album_subfolder": "Ctrl+Shift+A", "file.create_image_album_subfolder": "",
    "file.back_up_before_saving": "Ctrl+Alt+B", "file.create_tag_sidecars": "Ctrl+Shift+T",
    "file.create_metadata_summary": "Ctrl+Shift+Y", "view.long_title_warning": "Ctrl+Alt+W",
    "view.format_disklavier_screen": "Ctrl+Alt+D", "view.hide_status": "Ctrl+Alt+S",
    "view.hide_quick_panel": "Ctrl+Alt+Q", "view.hide_album_metadata": "Ctrl+Alt+A",
    "view.show_save_destination": "", "view.show_preparation_row": "", "view.logs": "F8",
    "utilities.song_list": "F3", "utilities.file_inspection": "F4", "utilities.render_audio": "F5",
    "utilities.bulk_extraction": "", "utilities.markiv_backup": "", "utilities.emulator_images": "",
    "utilities.repair_boot_sector": "", "utilities.rename": "Ctrl+Shift+R",
    "utilities.long_filenames": "", "utilities.trim_title_spaces": "Ctrl+Shift+Space",
    "utilities.smf0": "Ctrl+Shift+0", "utilities.eseq_to_midi": "Ctrl+Shift+M",
    "utilities.midi_to_eseq": "Ctrl+Shift+E", "utilities.pedal_compatibility": "",
    "utilities.merge_channels": "", "utilities.strip_xf": "",
    "utilities.recover_image": "Ctrl+Shift+D", "utilities.format_floppy": "F6",
    "settings.reset_hidden_dialogs": "Ctrl+Shift+H", "help.welcome": "F1",
    "help.check_updates": "F9", "help.feedback": "F11", "help.report_bug": "F10",
    "help.about": "Ctrl+F1", "file.quit": "Ctrl+Q",
}


def test_menu_reorganization_preserves_stored_shortcut_ids_and_defaults(window):
    specs = window._keyboard_shortcut_specs()
    assert len(specs) == len(SHORTCUT_DEFAULTS)
    assert {spec["id"]: spec["default"] for spec in specs} == SHORTCUT_DEFAULTS


def test_quit_uses_existing_discard_guard_before_closing(window, monkeypatch):
    confirmations = []
    cleanup = []
    answers = iter((False, True))
    monkeypatch.setattr(window, "is_image_mode", lambda: True)
    monkeypatch.setattr(window, "_reset_image_state", lambda: cleanup.append("reset"))

    def confirm():
        confirmations.append("asked")
        return next(answers)

    monkeypatch.setattr(window, "_confirm_discard_image_changes", confirm)
    window.show()
    QApplication.processEvents()
    window.fileQuitAction.trigger()
    assert window.isVisible()
    assert confirmations == ["asked"]
    assert not cleanup
    window.fileQuitAction.trigger()
    assert not window.isVisible()
    assert confirmations == ["asked", "asked"]
    assert cleanup == ["reset"]


@pytest.mark.parametrize("language", [language.code for language in SUPPORTED_LANGUAGES])
def test_language_refresh_preserves_menu_locations_and_preferences(window, language):
    window.fileCreateTagSidecarsAction.setChecked(True)
    window.verifyFloppyWriteAction.setChecked(True)
    window.viewShowStatusAction.setChecked(True)
    before = dict(_menu_contents(window))
    checked = {action: action.isChecked() for action in before if action.isCheckable()
               and action not in window.languageActions.values()}
    window.currentLanguage = language
    window._refresh_translated_ui()
    assert dict(_menu_contents(window)) == before
    assert {action: action.isChecked() for action in checked} == checked
    for spec in window._keyboard_shortcut_specs():
        action = getattr(window, spec["action"])
        assert "\t" not in action.text()
        assert action.shortcut().isEmpty()
    menus = {menu for path in before.values() for menu in path}
    for menu in (*menus, window.menuBar()):
        assert all("\t" not in action.text() for action in menu.actions())
        mnemonics = [
            QKeySequence.mnemonic(action.text().partition("\t")[0]).toString().casefold()
            for action in menu.actions() if not action.isSeparator()
        ]
        assigned = [mnemonic for mnemonic in mnemonics if mnemonic]
        assert len(assigned) == len(set(assigned)), (language, menu.objectName(), mnemonics)


def test_mnemonic_refresh_preserves_literal_ampersands_and_stability(window):
    menu = window.helpMenu
    first = menu.addAction("&Fish && Chips")
    second = menu.addAction("&Fish && Sauce")
    third = menu.addAction("&Bread")
    actions = [first, second, third]
    before = [action.text().replace("&&", "\0").replace("&", "").replace("\0", "&")
              for action in actions]
    window._refresh_menu_mnemonics()
    after = [action.text() for action in actions]
    assert [text.replace("&&", "\0").replace("&", "").replace("\0", "&")
            for text in after] == before
    assert all("\t" not in action.text() for action in actions)
    mnemonics = [QKeySequence.mnemonic(action.text()).toString() for action in actions]
    assert all(mnemonics) and len(set(mnemonics)) == len(mnemonics)
    for _ in range(3):
        window._refresh_menu_mnemonics()
        assert [action.text() for action in actions] == after
