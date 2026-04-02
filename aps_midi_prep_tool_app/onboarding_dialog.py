import sys

from PySide6.QtCore import QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMessageBox, QCheckBox

from .app_info import (
    APP_TITLE_WITH_VERSION,
    APP_WEBSITE,
    COPYRIGHT_HOLDER,
    COPYRIGHT_YEAR,
    SETTINGS_APP,
    SETTINGS_ORG,
)


def show_first_time_dialog(app_icon: QIcon | None = None):
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    skip_dialog = settings.value("skip_first_time_dialog", False, type=bool)

    if not skip_dialog:
        if sys.platform.startswith("win"):
            body_font_stack = '"Segoe UI", "Arial", sans-serif'
        elif sys.platform == "darwin":
            body_font_stack = '"Helvetica Neue", "Helvetica", sans-serif'
        else:
            body_font_stack = '"Noto Sans", "DejaVu Sans", "Arial", sans-serif'

        msgBox = QMessageBox()
        if app_icon is not None and not app_icon.isNull():
            msgBox.setWindowIcon(app_icon)
        msgBox.setIcon(QMessageBox.Information)
        msgBox.setWindowTitle(f"Welcome to {APP_TITLE_WITH_VERSION}")
        msgBox.setText(f"""<html>
      <head>
        <style type="text/css">
          body {{ font-family: {body_font_stack}; }}
          h1 {{ font-weight: bold; margin-bottom: 10px; text-align: center; }}
          h2 {{ text-align: center; }}
          p {{ margin: 10px 0; }}
          ul {{ margin: 10px 20px; }}
          li {{ margin-bottom: 6px; }}
          a {{ text-decoration: none; }}
          a:hover {{ text-decoration: underline; }}
        </style>
      </head>
      <body>
        <h1>{APP_TITLE_WITH_VERSION}</h1>
        <p>
          This tool is designed to help you quickly update and manage the titles of your MIDI files.
          Whether you have a few or many files, renaming them is a breeze.
        </p>
        <p><strong>Getting Started:</strong></p>
        <ol>
          <li>
            <strong>Select a Folder:</strong> Click the <em>"Choose MIDI Folder"</em> button to load the folder containing your MIDI files.
          </li>
          <li>
            <strong>Or Drag and Drop:</strong> You can also drag <code>.mid</code> and <code>.midi</code> files directly into the table.
          </li>
          <li>
            <strong>Edit Titles:</strong> Click a title cell to open the edit dialog. Changes are queued until you save.
          </li>
          <li>
            <strong>Quick Copy:</strong> Click the clipboard icon or double-click a filename to copy the filename.
          </li>
          <li>
            <strong>Save Options:</strong> Use <em>Save</em>, <em>Save As...</em>, <em>Rename All to DOS 8.3</em>, or <em>Convert All to MIDI Type 0</em> from the Save button menu.
          </li>
        </ol>
        <p>
          For more details, visit our website at
          <a href="{APP_WEBSITE}">alexanderpeppe.com</a>.<br />
          Copyright {COPYRIGHT_YEAR} {COPYRIGHT_HOLDER}
        </p>
        <p>Happy editing!</p>
      </body>
    </html>""")
        dont_show_checkbox = QCheckBox("Do not show this dialog again")
        dont_show_checkbox.setStyleSheet("margin-top: 10px")
        msgBox.setCheckBox(dont_show_checkbox)
        msgBox.setStandardButtons(QMessageBox.Ok)
        msgBox.exec()
        if dont_show_checkbox.isChecked():
            settings.setValue("skip_first_time_dialog", True)
