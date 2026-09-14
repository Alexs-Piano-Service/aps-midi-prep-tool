"""Offer recovery before users start working with files after an interrupted rename."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from .dos83_renamer import find_pending_midi_renames, recover_midi_dos83_transaction


def show_rename_recovery_dialogs(parent):
    translate = getattr(parent, "_lt", lambda value: value)
    try:
        pending = find_pending_midi_renames()
    except Exception as exc:
        QMessageBox.warning(parent, translate("Rename recovery unavailable"), str(exc))
        return
    for directory in pending:
        dialog = QMessageBox(parent)
        dialog.setIcon(QMessageBox.Warning)
        dialog.setTextFormat(Qt.PlainText)
        dialog.setWindowTitle(translate("Recover interrupted rename"))
        dialog.setText(translate("A filename rename was interrupted. Restore the original filenames or finish the planned rename?"))
        dialog.setInformativeText(translate("Recovery copies will be kept until recovery completes. Choosing Later leaves all files in their current locations."))
        dialog.setDetailedText(directory)
        restore = dialog.addButton(translate("Restore originals"), QMessageBox.AcceptRole)
        resume = dialog.addButton(translate("Resume rename"), QMessageBox.ActionRole)
        dialog.addButton(translate("Later"), QMessageBox.RejectRole)
        dialog.setDefaultButton(restore)
        dialog.exec()
        clicked = dialog.clickedButton()
        if clicked is not restore and clicked is not resume:
            continue
        try:
            recover_midi_dos83_transaction(directory, "restore" if clicked is restore else "resume")
        except Exception as exc:
            QMessageBox.warning(parent, translate("Rename recovery incomplete"), str(exc))
        else:
            QMessageBox.information(parent, translate("Rename recovery complete"), translate("The files are ready to open. Recovery copies have been removed."))
