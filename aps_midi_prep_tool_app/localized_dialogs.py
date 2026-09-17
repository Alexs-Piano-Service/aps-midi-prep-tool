"""Application-language message boxes shared by primary and nested dialogs."""

from PySide6.QtCore import QCoreApplication, QEvent, QLibraryInfo, QLocale, QTimer, QTranslator
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox as QtQMessageBox

from .icon_utils import apply_window_icon
from .message_catalog import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, normalize_language_code, translate_text
from .ui_utils import center_dialog_on_parent


class _ApplicationTranslator(QTranslator):
    def __init__(self, language, parent):
        super().__init__(parent)
        self.language = language

    def translate(self, context, source_text, disambiguation=None, n=-1):
        source = source_text.replace("&", "")
        if context == "QPlatformTheme" and source in {"OK", "Cancel", "Close", "Yes", "No", "Save"}:
            return translate_text(source, self.language)
        if context == "QMessageBox" and source in {"Show Details...", "Hide Details..."}:
            return translate_text(source, self.language)
        return super().translate(context, source_text, disambiguation, n)


def install_qt_translations(language, application=None):
    """Keep Qt's own menus and file pickers in the selected app language."""
    application = application or QCoreApplication.instance()
    if application is None:
        return False
    language = normalize_language_code(language)
    if getattr(application, "_aps_qt_language", None) == language:
        return True
    previous = getattr(application, "_aps_qt_translator", None)
    if previous is not None:
        application.removeTranslator(previous)
        application._aps_qt_translator = None
        previous.deleteLater()
    locale = QLocale({"zh-Hans": "zh_CN", "pt-BR": "pt_BR"}.get(language, language))
    application._aps_qt_language = None
    if language == DEFAULT_LANGUAGE:
        application._aps_qt_language = language
        return True
    translator = _ApplicationTranslator(language, application)
    if translator.load(locale, "qtbase", "_", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        application.installTranslator(translator)
        application._aps_qt_translator = translator
        application._aps_qt_language = language
        return True
    translator.deleteLater()
    return False


def _message_parent_language(parent):
    widget = parent
    for _ in range(8):
        if widget is None:
            break
        language_method = getattr(widget, "_language_code", None)
        if callable(language_method):
            return normalize_language_code(language_method())
        for attribute in ("language_code", "currentLanguage", "language"):
            language = getattr(widget, attribute, None)
            if isinstance(language, str) and language:
                return normalize_language_code(language)
        parent_method = getattr(widget, "parent", None)
        widget = parent_method() if callable(parent_method) else None
    return DEFAULT_LANGUAGE


def _translate_for_parent(parent, text):
    return translate_text(text, _message_parent_language(parent))


_MESSAGE_BUTTON_LABELS = {
    QtQMessageBox.Ok: "OK",
    QtQMessageBox.Cancel: "Cancel",
    QtQMessageBox.Close: "Close",
    QtQMessageBox.Yes: "Yes",
    QtQMessageBox.No: "No",
    QtQMessageBox.Save: "Save",
}


def _is_default_button_caption(text, label):
    default_labels = {label, QCoreApplication.translate("QPlatformTheme", label)}
    default_labels.update(translate_text(label, option.code) for option in SUPPORTED_LANGUAGES)
    return text.replace("&", "") in {caption.replace("&", "") for caption in default_labels}


def _translate_message_box_buttons(message_box, language=None):
    language = normalize_language_code(language)
    for standard_button, label in _MESSAGE_BUTTON_LABELS.items():
        button = message_box.button(standard_button)
        if button is not None and _is_default_button_caption(button.text(), label):
            button.setText(translate_text(label, language))


class QMessageBox(QtQMessageBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for buttons in self.findChildren(QDialogButtonBox):
            buttons.installEventFilter(self)

    def eventFilter(self, watched, event):
        if isinstance(watched, QDialogButtonBox) and event.type() == QEvent.LanguageChange:
            # Qt's button box independently resets all standard captions on a
            # queued language event, including deliberately named actions.
            _translate_message_box_buttons(self, _message_parent_language(self.parent()))
            self._translate_details_button()
            return True
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        # Qt refreshes standard captions when shown, which can replace the
        # application's wording with a different toolkit translation.
        _translate_message_box_buttons(self, _message_parent_language(self.parent()))
        self._translate_details_button()

    def changeEvent(self, event):
        custom_captions = {}
        if event.type() == QEvent.LanguageChange:
            custom_captions = {
                button_id: button.text() for button_id, label in _MESSAGE_BUTTON_LABELS.items()
                if (button := self.button(button_id)) is not None
                and not _is_default_button_caption(button.text(), label)
            }
        super().changeEvent(event)
        if event.type() == QEvent.LanguageChange:
            for button_id, caption in custom_captions.items():
                self.button(button_id).setText(caption)
            _translate_message_box_buttons(self, _message_parent_language(self.parent()))
            self._translate_details_button()

    def setDetailedText(self, text):
        super().setDetailedText(text)
        self._translate_details_button()

    def _translate_details_button(self):
        language = _message_parent_language(self.parent())
        captions = {
            caption.replace("&", ""): source
            for source in ("Show Details...", "Hide Details...")
            for caption in (source, QCoreApplication.translate("QMessageBox", source))
        }
        for button in self.buttons():
            source = captions.get(button.text().replace("&", ""))
            if source is None:
                continue
            button.setText(translate_text(source, language))
            if not button.property("_aps_details_translation"):
                # Qt changes the caption itself when the details are toggled.
                button.clicked.connect(self._translate_details_button)
                button.setProperty("_aps_details_translation", True)

    def setWindowTitle(self, title):
        super().setWindowTitle(_translate_for_parent(self.parent(), title))

    def setText(self, text):
        super().setText(_translate_for_parent(self.parent(), text))

    def setInformativeText(self, text):
        super().setInformativeText(_translate_for_parent(self.parent(), text))

    def setStandardButtons(self, buttons):
        super().setStandardButtons(buttons)
        _translate_message_box_buttons(self, _message_parent_language(self.parent()))

    @staticmethod
    def _exec_static(parent, icon, title, text, buttons, defaultButton):
        box = QMessageBox(parent)
        apply_window_icon(box)
        box.setIcon(icon)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStandardButtons(buttons)
        if defaultButton != QtQMessageBox.StandardButton.NoButton:
            box.setDefaultButton(defaultButton)
        _translate_message_box_buttons(box, _message_parent_language(parent))
        if hasattr(parent, "_center_child_dialog"):
            parent._center_child_dialog(box)
        else:
            center_dialog_on_parent(box, parent)
            QTimer.singleShot(0, lambda: center_dialog_on_parent(box, parent))
        return box.exec()

    @staticmethod
    def information(
        parent,
        title,
        text,
        buttons=QtQMessageBox.StandardButton.Ok,
        defaultButton=QtQMessageBox.StandardButton.NoButton,
    ):
        return QMessageBox._exec_static(
            parent,
            QtQMessageBox.Information,
            title,
            text,
            buttons,
            defaultButton,
        )

    @staticmethod
    def warning(
        parent,
        title,
        text,
        buttons=QtQMessageBox.StandardButton.Ok,
        defaultButton=QtQMessageBox.StandardButton.NoButton,
    ):
        return QMessageBox._exec_static(
            parent,
            QtQMessageBox.Warning,
            title,
            text,
            buttons,
            defaultButton,
        )

    @staticmethod
    def critical(
        parent,
        title,
        text,
        buttons=QtQMessageBox.StandardButton.Ok,
        defaultButton=QtQMessageBox.StandardButton.NoButton,
    ):
        return QMessageBox._exec_static(
            parent,
            QtQMessageBox.Critical,
            title,
            text,
            buttons,
            defaultButton,
        )

    @staticmethod
    def question(
        parent,
        title,
        text,
        buttons=QtQMessageBox.StandardButton.Yes | QtQMessageBox.StandardButton.No,
        defaultButton=QtQMessageBox.StandardButton.NoButton,
    ):
        return QMessageBox._exec_static(
            parent,
            QtQMessageBox.Question,
            title,
            text,
            buttons,
            defaultButton,
        )
