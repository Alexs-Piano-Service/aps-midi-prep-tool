"""Resizable song-by-song comparison of unsaved changes."""

from html import escape

from PySide6.QtCore import QItemSelectionModel, QSignalBlocker, Qt
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel,
    QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout,
)

from .icon_utils import apply_window_icon
from .localized_dialogs import QMessageBox
from .message_catalog import translate_text
from .ui_utils import resize_dialog_to_screen


class PendingChangesDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.owner = window
        self.language_code = window._language_code()
        self.rows = []
        self.p = window._pending_text
        apply_window_icon(self)
        self.setWindowTitle(self.p("review"))
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.PlainText)
        self.summary.setWordWrap(True)
        font = self.summary.font()
        font.setBold(True)
        self.summary.setFont(font)
        layout.addWidget(self.summary)
        help_label = QLabel(self.p("review_help"))
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        self.splitter = QSplitter(Qt.Vertical)
        self.splitter.setChildrenCollapsible(False)
        layout.addWidget(self.splitter, 1)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([
            self.p("original_filename"), self.p("proposed_filename"), self.p("changes"),
        ])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(self.fontMetrics().height() * 5)
        self.splitter.addWidget(self.table)

        self.details = QTextBrowser()
        self.details.setOpenLinks(False)
        self.details.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.details.setMinimumHeight(self.fontMetrics().height() * 6)
        self.details.setAccessibleName(self.t("Details"))
        self.splitter.addWidget(self.details)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.buttons.button(QDialogButtonBox.Close).setText(self.t("Close"))
        self.buttons.button(QDialogButtonBox.Close).setDefault(True)
        self.discard_button = self.buttons.addButton(self.p("discard_selected"), QDialogButtonBox.ActionRole)
        self.undo_button = self.buttons.addButton(self.p("undo_last"), QDialogButtonBox.ActionRole)
        self.discard_button.setAutoDefault(False)
        self.undo_button.setAutoDefault(False)
        # Allow a narrow window; long translated captions use a vertical footer
        # instead of imposing the combined width of all three buttons.
        self.buttons.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        layout.addWidget(self.buttons)
        self.table.itemSelectionChanged.connect(self.update_details)
        self.discard_button.clicked.connect(self.discard_selected)
        self.undo_button.clicked.connect(self.undo_last)
        self.buttons.rejected.connect(self.reject)
        self.refresh()

        # Tables and details scroll at larger fonts; only the footer needs to
        # reflow when the requested initial width is limited by the screen.
        resize_dialog_to_screen(self, width=900, height=600)
        self._fit_buttons(self.width())
        height = self.height()
        self.splitter.setSizes([round(height * 0.55), round(height * 0.45)])

    def _fit_buttons(self, width):
        margins = self.layout().contentsMargins()
        buttons = self.buttons.buttons()
        self.buttons.setMinimumWidth(max(button.sizeHint().width() for button in buttons))
        spacing = max(0, self.buttons.layout().spacing())
        row_width = sum(button.sizeHint().width() for button in buttons) + spacing * (len(buttons) - 1)
        available = width - margins.left() - margins.right()
        orientation = Qt.Horizontal if row_width <= available else Qt.Vertical
        if self.buttons.orientation() != orientation:
            self.buttons.setOrientation(orientation)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_buttons(event.size().width())

    def t(self, source):
        return translate_text(source, self.language_code)

    def selected_rows(self):
        return sorted(index.row() for index in self.table.selectionModel().selectedRows())

    def change_summary(self, row):
        _path, original, proposed, kind, detail = row
        before_name, _, before_title = original.partition("\n")
        after_name, _, after_title = proposed.partition("\n")
        changes = []
        if before_name == "—":
            changes.append(self.p("song_added"))
        elif after_name == "—":
            changes.append(self.p("song_removed"))
        else:
            if before_name != after_name:
                changes.append(self.p("filename_changed"))
            if before_title != after_title:
                changes.append(self.p("title_changed"))
        if detail:
            changes.append(detail.splitlines()[0])
        elif " → " in kind:
            changes.append(kind)
        return "; ".join(changes)

    def refresh(self):
        selected_paths = {self.rows[row][0] for row in self.selected_rows()}
        current_row = self.table.currentRow()
        try:
            rows = self.owner._pending_review_rows()
        except Exception as exc:
            self.details.setPlainText(str(exc))
            self.discard_button.setEnabled(False)
            return
        with QSignalBlocker(self.table):
            self.table.clearSelection()
            self.rows = rows
            self.table.setRowCount(len(rows))
            for index, row in enumerate(rows):
                path, original, proposed, _kind, _detail = row
                values = (original.partition("\n")[0], proposed.partition("\n")[0], self.change_summary(row))
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setToolTip(value if column == 2 else (original if column == 0 else proposed) + "\n" + path)
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    self.table.setItem(index, column, item)
            selection = self.table.selectionModel()
            for index, row in enumerate(rows):
                if row[0] in selected_paths:
                    selection.select(self.table.model().index(index, 0), QItemSelectionModel.Select | QItemSelectionModel.Rows)
            selected = self.selected_rows()
            if selected:
                self.table.setCurrentCell(selected[0], 0, QItemSelectionModel.NoUpdate)
            elif rows:
                self.table.setCurrentCell(max(0, min(current_row, len(rows) - 1)), 0)
                self.table.selectRow(self.table.currentRow())
        count = len(rows)
        summary = self.p("count", count=count) if count else self.p("none")
        if not count and self.owner._pending_changes_to_discard():
            summary = self.p("catalog")
        self.summary.setText(summary)
        self.undo_button.setEnabled(self.owner._can_undo_staged_changes())
        self.update_details()

    def comparison_html(self, row):
        path, original, proposed, kind, detail = row
        before_name, _, before_title = original.partition("\n")
        after_name, _, after_title = proposed.partition("\n")
        before_kind, separator, after_kind = kind.partition(" → ")
        if not separator:
            after_kind = before_kind
        if before_name == "—":
            before_kind = "—"
        if after_name == "—":
            after_kind = "—"

        def cell(value, bold=False):
            value = escape(value).replace("\n", "<br>") or "—"
            value = f'<span style="white-space: pre-wrap;">{value}</span>'
            return f"<b>{value}</b>" if bold else value

        fields = ((self.t("Filename"), before_name, after_name),
                  (self.t("Song title"), before_title, after_title),
                  (self.t("Type"), before_kind, after_kind))
        comparison = "".join(
            f"<tr><td>{escape(label)}</td><td>{cell(before)}</td>"
            f"<td>{cell(after, before != after)}</td></tr>"
            for label, before, after in fields
        )
        return (
            f"<h3>{escape(after_name if after_name != '—' else before_name)}</h3>"
            '<table width="100%" cellspacing="0" cellpadding="5">'
            f'<tr><th width="16%"></th><th width="42%" align="left">{escape(self.p("original"))}</th>'
            f'<th width="42%" align="left">{escape(self.p("proposed"))}</th></tr>'
            f"{comparison}</table>"
            f"<p>{cell(translate_text('Source: {source}', self.language_code, source=path))}</p>"
            + (f"<h4>{escape(self.t('Details'))}</h4><p>{cell(detail)}</p>" if detail else "")
        )

    def update_details(self):
        selected = self.selected_rows()
        self.discard_button.setEnabled(bool(selected))
        if selected:
            self.details.setHtml("<hr>".join(self.comparison_html(self.rows[row]) for row in selected))
        else:
            self.details.setPlainText(self.p("select_song") if self.rows else self.summary.text())

    def discard_selected(self):
        paths = [self.rows[row][0] for row in self.selected_rows()]
        try:
            self.owner.discard_staged_song_changes(paths)
        except Exception as exc:
            QMessageBox.warning(self, self.p("review"), str(exc))
        self.refresh()

    def undo_last(self):
        self.owner.undo_last_staged_batch()
        self.refresh()
