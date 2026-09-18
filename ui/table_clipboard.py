"""Excel-compatible copy/select behavior for every table in the application."""
from __future__ import annotations
from html import escape

from PySide6.QtCore import QEvent, QMimeData, QObject, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QAbstractItemView, QTableView, QTableWidget, QWidget


def _table_for(widget: QObject) -> QTableView | QTableWidget | None:
    current = widget
    while isinstance(current, QWidget):
        if isinstance(current, (QTableView, QTableWidget)):
            return current
        current = current.parentWidget()
    return None


def _is_specification_column(table: QTableView | QTableWidget, column: int) -> bool:
    header = str(table.model().headerData(column, Qt.Horizontal, Qt.DisplayRole) or "").upper()
    compact = header.replace(" ", "").replace("\n", "")
    return "POWER" in compact or "파워" in compact or compact in {"CP", "AXIS", "ADD", "규격", "해당규격"}


def _plain_value(index, preserve_display_text: bool = False) -> str:
    if preserve_display_text:
        value = index.data(Qt.DisplayRole)
        if value is None:
            return ""
        text = str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")
        return text
    value = index.data(Qt.UserRole)
    if value is None:
        value = index.data(Qt.EditRole)
    if value is None:
        value = index.data(Qt.DisplayRole)
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).replace("\t", " ").replace("\r", " ").replace("\n", " ")


class TableClipboardController(QObject):
    def eventFilter(self, watched, event):
        table = _table_for(watched)
        if table is not None and event.type() in (QEvent.Show, QEvent.Polish):
            table.setSelectionBehavior(QAbstractItemView.SelectItems)
            table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        if table is not None and event.type() == QEvent.KeyPress:
            if event.matches(QKeySequence.SelectAll):
                table.selectAll()
                event.accept()
                return True
            if event.matches(QKeySequence.Copy):
                self.copy_selection(table)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    @staticmethod
    def copy_selection(table: QTableView | QTableWidget) -> None:
        indexes = [
            index for index in table.selectionModel().selectedIndexes()
            if not table.isRowHidden(index.row()) and not table.isColumnHidden(index.column())
        ]
        if not indexes and table.currentIndex().isValid():
            indexes = [table.currentIndex()]
        if not indexes:
            return
        rows = sorted({index.row() for index in indexes})
        cols = sorted({index.column() for index in indexes})
        selected = {(index.row(), index.column()): index for index in indexes}
        visible_rows = [row for row in range(table.model().rowCount()) if not table.isRowHidden(row)]
        visible_cols = [col for col in range(table.model().columnCount()) if not table.isColumnHidden(col)]
        all_selected = rows == visible_rows and cols == visible_cols and len(indexes) == len(rows) * len(cols)
        lines: list[str] = []
        html_rows: list[list[tuple[str, bool, bool]]] = []
        if all_selected:
            header = table.horizontalHeader()
            custom_rows = getattr(header, "clipboard_header_rows", None)
            if callable(custom_rows):
                header_rows = custom_rows(cols)
            else:
                header_rows = [[table.model().headerData(col, Qt.Horizontal, Qt.DisplayRole) or "" for col in cols]]
            for line in header_rows:
                values=[str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ") for value in line]
                lines.append("\t".join(values));html_rows.append([(value,False,True) for value in values])
        for row in rows:
            values=[];html_values=[]
            for col in cols:
                preserve=_is_specification_column(table,col)
                value=_plain_value(selected[(row,col)],preserve) if (row,col) in selected else ""
                values.append(value);html_values.append((value,preserve,False))
            lines.append("\t".join(values));html_rows.append(html_values)
        html_table=['<html><body><table>']
        for html_row in html_rows:
            html_table.append('<tr>')
            for value,preserve,is_header in html_row:
                tag='th' if is_header else 'td';style=' style="mso-number-format:\\@;"' if preserve else ''
                html_table.append(f'<{tag}{style}>{escape(value)}</{tag}>')
            html_table.append('</tr>')
        html_table.append('</table></body></html>')
        mime=QMimeData();mime.setText("\r\n".join(lines));mime.setHtml(''.join(html_table));QApplication.clipboard().setMimeData(mime)


def install_table_clipboard(app: QApplication) -> TableClipboardController:
    controller = TableClipboardController(app)
    app.installEventFilter(controller)
    return controller
