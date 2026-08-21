from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import qtawesome as qta
from PySide6.QtCore import QAbstractTableModel, QDate, QEvent, QModelIndex, QPoint, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDateEdit, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QPushButton, QSizePolicy,
    QTableView, QVBoxLayout, QWidget,
)

from services.process_status_service import (
    DB_PATH, PROCESS_ORDER, ProcessStatusService, classification_sort_key,
    power_sort_key,
)
from services.process_excel_exporter import export_process_workbook
from ui.message_dialog import show_app_message


def format_number(value: object) -> str:
    try:
        return f"{float(value or 0):,.0f}"
    except (TypeError, ValueError):
        return "0"


def _repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)


FILTER_BUTTON_SELECTION_STYLE = """
QPushButton {
    color: #3B3B40;
    background: #FFFFFF;
    border: 1px solid #D7DCE3;
    border-radius: 9px;
    min-height: 34px;
    padding: 0 14px;
    font-weight: 700;
}
QPushButton:hover {
    background: #F3F7FF;
    border-color: #BFD9FA;
}
QPushButton:checked, QPushButton:checked:hover, QPushButton:checked:focus {
    color: #0A7AFF;
    background: #E8F2FF;
    border: 1px solid #0A7AFF;
}
"""

CLASSIFICATION_BUTTON_STYLE = """
QPushButton {
    color: #3B3B40;
    background: #FFFFFF;
    border: 1px solid #D7DCE3;
    border-radius: 7px;
    min-height: 26px;
    padding: 0 9px;
    font-size: 12px;
    font-weight: 700;
}
QPushButton:hover {
    background: #F3F7FF;
    border-color: #BFD9FA;
}
QPushButton:checked, QPushButton:checked:hover, QPushButton:checked:focus {
    color: #0A7AFF;
    background: #E8F2FF;
    border: 1px solid #0A7AFF;
}
"""


class Card(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")


class KpiCard(Card):
    clicked = Signal()

    def __init__(self, title: str, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._clickable = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        row = QHBoxLayout()
        dot = QLabel("●")
        dot.setStyleSheet(f"color:{color}")
        label = QLabel(title)
        label.setObjectName("kpiTitle")
        row.addWidget(dot)
        row.addWidget(label)
        row.addStretch()
        self.link_icon = QLabel()
        self.link_icon.setPixmap(qta.icon("fa6s.arrow-right", color="#0A7AFF").pixmap(12, 12))
        self.link_icon.setVisible(False)
        row.addWidget(self.link_icon)
        layout.addLayout(row)
        self.value = QLabel("—")
        self.value.setObjectName("kpiValue")
        layout.addWidget(self.value)
        self.detail = QLabel("조회 전")
        self.detail.setObjectName("muted")
        layout.addWidget(self.detail)

    def set_data(self, value: str, detail: str) -> None:
        self.value.setText(value)
        self.detail.setText(detail)

    def set_clickable(self, clickable: bool, tooltip: str = "") -> None:
        self._clickable = clickable
        self.setProperty("clickable", clickable)
        self.setCursor(Qt.PointingHandCursor if clickable else Qt.ArrowCursor)
        self.link_icon.setVisible(clickable)
        self.setToolTip(tooltip)
        _repolish(self)

    def set_selected_process(self, selected: bool) -> None:
        self.setProperty("selectedProcess", selected)
        _repolish(self)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._clickable and event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class RowTableModel(QAbstractTableModel):
    """가시 셀만 그리는 대용량 진행현황 표 모델."""

    def __init__(self, columns: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.columns = columns
        self.rows: list[dict] = []

    def rowCount(self, _parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(self.rows)

    def columnCount(self, _parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(self.columns)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):  # noqa: N802
        if role == Qt.DisplayRole and orientation == Qt.Horizontal and 0 <= section < len(self.columns):
            return self.columns[section]
        return super().headerData(section, orientation, role)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        row = self.rows[index.row()]
        column = self.columns[index.column()]
        value = row.get("공정", {}).get(column, 0) if column in PROCESS_ORDER else row.get(column, "")
        if role == Qt.DisplayRole:
            return format_number(value) if isinstance(value, (int, float)) else str(value or "")
        if role == Qt.TextAlignmentRole and isinstance(value, (int, float)):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and column == "수주번호" and row.get("_수주목록"):
            return str(row["_수주목록"])
        if role == Qt.ToolTipRole and column in {"신규분류요약", "수주번호", "품명", "T코드", "P코드", "Q코드", "R코드"}:
            return str(value or "") or None
        if role == Qt.UserRole:
            return float(value) if isinstance(value, (int, float)) else value
        if role == Qt.UserRole + 1:
            return row
        return None

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def refresh_column(self, column_name: str) -> None:
        if not self.rows or column_name not in self.columns:
            return
        column = self.columns.index(column_name)
        self.dataChanged.emit(
            self.index(0, column), self.index(len(self.rows) - 1, column),
            [Qt.DisplayRole, Qt.ToolTipRole],
        )


class DataTable(Card):
    row_selected = Signal(object)

    def __init__(self, title: str, columns: list[str], widths: dict[str, int] | None = None,
                 stretch_column: str | None = None, allow_sort: bool = True,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.columns = columns
        self.widths = widths or {}
        self.allow_sort = allow_sort
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(9)
        heading = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("CardTitle")
        self.caption = QLabel("데이터를 조회해 주세요.")
        self.caption.setObjectName("CardSub")
        heading.addWidget(title_label)
        heading.addStretch()
        heading.addWidget(self.caption)
        self.heading_layout = heading
        layout.addLayout(heading)
        self.table = QTableView()
        self.table.setObjectName("DataTable")
        self.model = RowTableModel(columns, self.table)
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.setSortingEnabled(allow_sort)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        # Windows native style can override QSS selection colors.  Pin the
        # Control Tower palette so selected rows remain clearly readable.
        selection_palette = self.table.palette()
        selection_palette.setColor(QPalette.Highlight, QColor("#E8F2FF"))
        selection_palette.setColor(QPalette.HighlightedText, QColor("#075CCF"))
        self.table.setPalette(selection_palette)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.horizontalHeader().setMinimumSectionSize(54)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for index, column in enumerate(columns):
            if column in self.widths:
                self.table.setColumnWidth(index, self.widths[column])
            if column == stretch_column:
                self.table.horizontalHeader().setSectionResizeMode(index, QHeaderView.Stretch)
        self.table.selectionModel().selectionChanged.connect(self._emit_selected)
        layout.addWidget(self.table, 1)
        self.table.viewport().setMouseTracking(True)
        self.table.viewport().installEventFilter(self)
        self._product_popup = QFrame(None, Qt.ToolTip | Qt.FramelessWindowHint)
        self._product_popup.setObjectName("ProductListPopup")
        popup_layout = QVBoxLayout(self._product_popup)
        popup_layout.setContentsMargins(12, 10, 12, 10)
        popup_layout.setSpacing(6)
        popup_title = QLabel("품번 / 품명")
        popup_title.setStyleSheet("font-weight: 800; color: #1F2937;")
        popup_layout.addWidget(popup_title)
        self._product_list = QListWidget()
        self._product_list.setSelectionMode(QAbstractItemView.NoSelection)
        self._product_list.setFocusPolicy(Qt.NoFocus)
        self._product_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._product_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._product_list.setStyleSheet(
            "QListWidget { border: 0; background: #FFFFFF; color: #1F2937; }"
            "QListWidget::item { min-height: 26px; padding: 2px 4px; }"
        )
        popup_layout.addWidget(self._product_list)
        self._product_popup.setStyleSheet(
            "QFrame#ProductListPopup { background: #FFFFFF; border: 1px solid #AFC8E8; border-radius: 8px; }"
        )
        self._product_popup.installEventFilter(self)
        self._product_list.viewport().installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        viewport = self.table.viewport() if hasattr(self, "table") else None
        if watched is viewport:
            if event.type() == QEvent.ToolTip:
                index = self.table.indexAt(event.pos())
                if index.isValid() and self.columns[index.column()] == "품명":
                    row = self.model.rows[index.row()]
                    items = row.get("_제품목록") if row.get("_간략보기유형") == "order" else None
                    if items:
                        self._show_product_popup(list(items), event.globalPos())
                        event.accept()
                        return True
            elif event.type() == QEvent.MouseMove and self._product_popup.isVisible():
                index = self.table.indexAt(event.position().toPoint())
                valid = (
                    index.isValid()
                    and self.columns[index.column()] == "품명"
                    and self.model.rows[index.row()].get("_간략보기유형") == "order"
                )
                if not valid:
                    self._product_popup.hide()
            elif event.type() == QEvent.Leave and self._product_popup.isVisible():
                QTimer.singleShot(180, self._hide_product_popup_if_outside)
        elif watched in {self._product_popup, self._product_list.viewport()} and event.type() == QEvent.Leave:
            QTimer.singleShot(180, self._hide_product_popup_if_outside)
        return super().eventFilter(watched, event)

    def _show_product_popup(self, items: list[str], global_pos: QPoint) -> None:
        self._product_list.clear()
        self._product_list.addItems(items)
        visible_rows = min(len(items), 10)
        popup_width = 560
        popup_height = min(360, 50 + max(1, visible_rows) * 30)
        self._product_popup.resize(popup_width, popup_height)
        position = global_pos + QPoint(14, 18)
        screen = QApplication.screenAt(global_pos)
        if screen is not None:
            area = screen.availableGeometry()
            position.setX(min(max(area.left(), position.x()), area.right() - popup_width))
            position.setY(min(max(area.top(), position.y()), area.bottom() - popup_height))
        self._product_popup.move(position)
        self._product_popup.show()
        self._product_popup.raise_()

    def _hide_product_popup_if_outside(self) -> None:
        if self._product_popup.isVisible() and not self._product_popup.geometry().contains(QCursor.pos()):
            self._product_popup.hide()

    def load(self, rows: list[dict], caption: str | None = None) -> None:
        self.model.set_rows(rows)
        self.caption.setText(caption or f"{len(rows):,}건")
        if rows:
            self.table.selectRow(0)
        else:
            self.row_selected.emit(None)

    def _emit_selected(self) -> None:
        selected = self.table.selectionModel().selectedRows()
        if selected:
            self.row_selected.emit(self.model.rows[selected[0].row()])

    def contextMenuEvent(self, event) -> None:
        self._show_context_menu(self.table.viewport().mapFrom(self, event.pos()))

    def _show_context_menu(self, position) -> None:
        index = self.table.indexAt(position)
        if not index.isValid() or index.row() >= len(self.model.rows):
            return
        owner = self.parent()
        while owner is not None and not hasattr(owner, "fixed_process"):
            owner = owner.parent()
        process = str(getattr(owner, "fixed_process", "") or "").strip()
        allowed = {"하이드레이션", "접착", "검사접착", "검사·접착", "누수규격"}
        row = self.model.rows[index.row()]
        product_code = str(row.get("P코드") or "").strip()
        if process not in allowed or not product_code.upper().startswith("P"):
            return

        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        lookup_action = menu.addAction("식염수 리드지 조회하기")
        selected_action = menu.exec(self.table.viewport().mapToGlobal(position))
        if selected_action is not lookup_action:
            return

        from ui.process_saline_lead_popup import ProcessSalineLeadPopup

        popup = getattr(self, "_saline_lead_popup", None)
        if popup is None:
            popup = ProcessSalineLeadPopup(self.window())
            self._saline_lead_popup = popup
        product_name = str(row.get("품명") or row.get("품명P") or "").strip()
        popup.show_product(product_code, product_name)


DUE_DETAIL_COLUMNS = [
    "신규분류요약", "이니셜", "수주번호", "T코드", "P코드", "Q코드", "R코드",
    "품명", "POWER", "CP", "AXIS", "ADD", "납기일", *PROCESS_ORDER,
]
DUE_DETAIL_WIDTHS = {
    "신규분류요약": 150, "이니셜": 82, "수주번호": 100, "T코드": 178,
    "P코드": 178, "Q코드": 178, "R코드": 178, "품명": 210, "POWER": 72,
    "CP": 68, "AXIS": 62, "ADD": 68, "납기일": 92, "사출": 76,
    "분리": 76, "하이드레이션": 96, "접착": 76, "누수규격": 86, "포장": 76,
}


class DueDetailPage(QWidget):
    MAX_DISPLAY_ROWS = 3000
    reset_requested = Signal()
    filtered_rows_changed = Signal(object)
    search_scope_changed = Signal()

    PROCESS_DEFAULT_CODE = {
        "사출": "R코드",
        "분리": "Q코드",
        "하이드레이션": "P코드",
        "접착": "P코드",
        "누수규격": "P코드",
    }
    PROCESS_DEFAULT_NAME = {
        "사출": "R",
        "분리": "Q",
        "하이드레이션": "P",
        "접착": "P",
        "누수규격": "P",
    }
    PREVIOUS_PROCESS = {
        "분리": "사출",
        "하이드레이션": "분리",
        "접착": "하이드레이션",
        "누수규격": "접착",
        "포장": "누수규격",
    }

    def __init__(self, parent: QWidget | None = None, *, fixed_process: str | None = None) -> None:
        super().__init__(parent)
        self.fixed_process = fixed_process
        self.all_rows: list[dict] = []
        self._filtered_rows: list[dict] = []
        self._displayed_rows: list[dict] = []
        self._filtered_quantity = 0.0
        self._current_page = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        filters = Card()
        filter_layout = QVBoxLayout(filters)
        filter_layout.setContentsMargins(14, 8, 14, 8)
        filter_layout.setSpacing(5)
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        due_label = QLabel("납기")
        due_label.setObjectName("FilterLabel")
        filter_row.addWidget(due_label)
        self.due_group = QButtonGroup(self)
        self.due_group.setExclusive(True)
        self.due_buttons: list[QPushButton] = []
        for index, text in enumerate(("해제", "직접", "당월", "+7일", "+14일")):
            button = QPushButton(text)
            button.setObjectName("FilterButton")
            button.setStyleSheet(FILTER_BUTTON_SELECTION_STYLE)
            button.setCheckable(True)
            button.setProperty("dueMode", text)
            button.setChecked(index == 0)
            button.clicked.connect(self._due_mode_changed)
            self.due_group.addButton(button)
            self.due_buttons.append(button)
            filter_row.addWidget(button)
        self.due_end = QDateEdit(QDate.currentDate())
        self.due_end.setObjectName("FilterInput")
        self.due_end.setCalendarPopup(True)
        self.due_end.setDisplayFormat("yyyy-MM-dd")
        self.due_end.setEnabled(False)
        self.due_end.dateChanged.connect(self._apply_filter)
        filter_row.addWidget(self.due_end)
        filter_row.addSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("SearchInput")
        self.search.setMinimumWidth(220)
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText("현재 결과 내: 품명·이니셜·수주번호·품목코드")
        self.search.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search, 1)
        filter_layout.addLayout(filter_row)
        self.classification_panel = QWidget()
        self.classification_layout = QGridLayout(self.classification_panel)
        self.classification_layout.setContentsMargins(0, 0, 0, 0)
        self.classification_layout.setHorizontalSpacing(6)
        self.classification_layout.setVerticalSpacing(3)
        class_label = QLabel("분류")
        class_label.setObjectName("FilterLabel")
        self.classification_layout.addWidget(class_label, 0, 0)
        self.classification_buttons: dict[str, QPushButton] = {}
        filter_layout.addWidget(self.classification_panel)
        display_row = QHBoxLayout()
        display_row.setSpacing(10)
        process_label = QLabel("공정 보기")
        process_label.setObjectName("FilterLabel")
        display_row.addWidget(process_label)
        self.process_group = QButtonGroup(self)
        self.process_group.setExclusive(True)
        self.process_buttons: list[QPushButton] = []
        process_options = (fixed_process,) if fixed_process else (
            "전체", *(process_name for process_name in PROCESS_ORDER if process_name != "포장")
        )
        for process_name in process_options:
            button = QPushButton(process_name)
            button.setObjectName("FilterButton")
            button.setStyleSheet(FILTER_BUTTON_SELECTION_STYLE)
            button.setCheckable(True)
            button.setProperty("processName", process_name)
            button.setChecked(process_name == (fixed_process or "누수규격"))
            button.clicked.connect(self._process_changed)
            self.process_group.addButton(button)
            self.process_buttons.append(button)
            display_row.addWidget(button)
        if fixed_process:
            display_row.addSpacing(8)
            self.workable_only = QCheckBox("작업 가능만")
            self.workable_only.setToolTip(
                "직전 공정 부족수량이 0인 항목만 표시합니다."
                if fixed_process in self.PREVIOUS_PROCESS
                else "사출은 선행 공정이 없어 모든 항목이 작업 대상입니다."
            )
            self.workable_only.setVisible(fixed_process in self.PREVIOUS_PROCESS)
            self.workable_only.stateChanged.connect(self._apply_filter)
            display_row.addWidget(self.workable_only)
            self.compact_view = QCheckBox("간략히 보기")
            compact_code = self.PROCESS_DEFAULT_CODE.get(fixed_process, "현재 공정코드")
            self.compact_view.setToolTip(
                f"표시 코드 체크와 관계없이 {fixed_process}은(는) {compact_code}가 같은 행을 합치고 "
                "가장 빠른 납기일 순으로 표시합니다."
            )
            self.compact_view.setVisible(fixed_process in self.PROCESS_DEFAULT_CODE)
            self.compact_view.stateChanged.connect(self._apply_filter)
            display_row.addWidget(self.compact_view)
        else:
            display_row.addSpacing(8)
            summary_label = QLabel("간략히 보기")
            summary_label.setObjectName("FilterLabel")
            display_row.addWidget(summary_label)
            self.summary_mode = QComboBox()
            self.summary_mode.setObjectName("ProgressStatusFilter")
            self.summary_mode.addItem("해제", "detail")
            self.summary_mode.addItem("수주별", "order")
            self.summary_mode.addItem("제품별", "product")
            self.summary_mode.setToolTip(
                "수주별은 수주번호로 묶고, 제품별은 수주번호와 현재 품명 기준의 조합으로 묶습니다."
            )
            self.summary_mode.currentIndexChanged.connect(self._apply_filter)
            display_row.addWidget(self.summary_mode)
        display_row.addSpacing(10)
        code_label = QLabel("APS 코드 관계" if fixed_process else "코드 표시")
        code_label.setObjectName("FilterLabel")
        display_row.addWidget(code_label)
        self.code_checks: dict[str, QCheckBox] = {}
        for code_column in ("T코드", "P코드", "Q코드", "R코드"):
            checkbox = QCheckBox(code_column)
            checkbox.setChecked(
                bool(fixed_process)
                and code_column == self.PROCESS_DEFAULT_CODE.get(fixed_process)
            )
            checkbox.stateChanged.connect(self._update_code_visibility)
            self.code_checks[code_column] = checkbox
            display_row.addWidget(checkbox)
        display_row.addSpacing(14)
        name_label = QLabel("품명 기준")
        name_label.setObjectName("FilterLabel")
        display_row.addWidget(name_label)
        self.name_basis = QComboBox()
        self.name_basis.setObjectName("ProgressStatusFilter")
        self.name_basis.addItem("판매명", "판매")
        self.name_basis.addItem("생산명", "P")
        self.name_basis.addItem("분리명", "Q")
        self.name_basis.addItem("사출명", "R")
        default_name = self.PROCESS_DEFAULT_NAME.get(fixed_process or "", "판매")
        self.name_basis.setCurrentIndex(max(0, self.name_basis.findData(default_name)))
        self.name_basis.currentIndexChanged.connect(self._update_name_basis)
        display_row.addWidget(self.name_basis)
        display_row.addStretch()
        sort_note = QLabel("정렬: 납기일 → POWER → 신규분류요약 → 품번")
        sort_note.setObjectName("CardSub")
        display_row.addWidget(sort_note)
        filter_layout.addLayout(display_row)
        layout.addWidget(filters)
        self.table = DataTable("납기별 상세", DUE_DETAIL_COLUMNS, DUE_DETAIL_WIDTHS, "품명", False)
        layout.addWidget(self.table, 1)
        self.pagination_bar = QWidget()
        pagination_layout = QHBoxLayout(self.pagination_bar)
        pagination_layout.setContentsMargins(0, 0, 0, 0)
        pagination_layout.setSpacing(8)
        pagination_layout.addStretch()
        pagination_button_style = """
            QPushButton {
                min-width: 34px; max-width: 34px; min-height: 30px; max-height: 30px;
                border: 1px solid #B8D5FF; border-radius: 10px;
                background: #FFFFFF; color: #0877F9;
                font-size: 18px; font-weight: 800;
            }
            QPushButton:hover { background: #EAF3FF; border-color: #0877F9; }
            QPushButton:pressed { background: #D8E9FF; }
            QPushButton:disabled {
                background: #F3F5F8; border-color: #E1E6ED; color: #B7C0CC;
            }
        """
        self.previous_page_button = QPushButton("‹")
        self.previous_page_button.setToolTip("이전 3,000행")
        self.previous_page_button.setStyleSheet(pagination_button_style)
        self.previous_page_button.clicked.connect(lambda: self._change_page(-1))
        pagination_layout.addWidget(self.previous_page_button)
        self.page_status_label = QLabel("1 / 1 페이지")
        self.page_status_label.setMinimumWidth(108)
        self.page_status_label.setStyleSheet(
            "background: #FFFFFF; color: #315274; border: 1px solid #D9E4F2; "
            "border-radius: 10px; padding: 6px 12px; font-weight: 700;"
        )
        self.page_status_label.setAlignment(Qt.AlignCenter)
        pagination_layout.addWidget(self.page_status_label)
        self.next_page_button = QPushButton("›")
        self.next_page_button.setToolTip("다음 3,000행")
        self.next_page_button.setStyleSheet(pagination_button_style)
        self.next_page_button.clicked.connect(lambda: self._change_page(1))
        pagination_layout.addWidget(self.next_page_button)
        pagination_layout.addStretch()
        self.pagination_bar.setVisible(True)
        layout.addWidget(self.pagination_bar)
        self._update_code_visibility()
        self._update_process_visibility()

    def load(self, rows: list[dict]) -> None:
        self.all_rows = list(rows)
        selected = self._selected_classifications()
        categories = sorted({str(row.get("신규분류요약") or "").strip() for row in rows if row.get("신규분류요약")}, key=classification_sort_key)
        process = self._selected_process()
        quantities = {
            category: sum(
                float(row.get("공정", {}).get(process, 0) or 0)
                if process != "전체"
                else sum(float(value or 0) for value in row.get("공정", {}).values())
                for row in rows if str(row.get("신규분류요약") or "").strip() == category
            )
            for category in categories
        }
        while self.classification_layout.count() > 1:
            item = self.classification_layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self.classification_buttons = {}
        options = [("전체", sum(quantities.values())), *((category, quantities[category]) for category in categories)]
        for index, (category, quantity) in enumerate(options):
            button = QPushButton(f"{category} ({format_number(quantity)})")
            button.setObjectName("FilterButton")
            button.setStyleSheet(CLASSIFICATION_BUTTON_STYLE)
            button.setCheckable(True)
            button.setProperty("classification", category)
            button.setToolTip(f"{category} · 현재 조건 부족수량 {format_number(quantity)} pcs")
            button.setChecked(category in selected or (category == "전체" and not (selected & set(categories))))
            button.clicked.connect(lambda _checked=False, selected_button=button: self._classification_changed(selected_button))
            row, column = divmod(index, 6)
            self.classification_layout.addWidget(button, row, column + 1)
            self.classification_buttons[category] = button
        for column in range(1, 7):
            self.classification_layout.setColumnStretch(column, 0)
        self.classification_layout.setColumnStretch(7, 1)
        self._apply_filter()

    def _selected_classifications(self) -> set[str]:
        if not hasattr(self, "classification_buttons") or not self.classification_buttons:
            return {"전체"}
        return {
            category for category, button in self.classification_buttons.items()
            if button.isChecked()
        } or {"전체"}

    def _classification_changed(self, selected_button: QPushButton) -> None:
        category = str(selected_button.property("classification") or "전체")
        all_button = self.classification_buttons.get("전체")
        category_buttons = [
            button for name, button in self.classification_buttons.items() if name != "전체"
        ]
        if category == "전체":
            if all_button:
                all_button.setChecked(True)
            for button in category_buttons:
                button.setChecked(False)
        else:
            if selected_button.isChecked() and all_button:
                all_button.setChecked(False)
            if not any(button.isChecked() for button in category_buttons) and all_button:
                all_button.setChecked(True)
        self._apply_filter()

    def reset_filters(self) -> None:
        """리스크 연계 검색 전에 공정현황 최초 진입 기본값으로 되돌린다."""
        for index, button in enumerate(self.due_buttons):
            button.setChecked(index == 0)
        self.due_end.setDate(QDate.currentDate())
        self.due_end.setEnabled(False)
        for category, button in self.classification_buttons.items():
            button.setChecked(category == "전체")
        self.search.clear()
        target_process = self.fixed_process or "누수규격"
        for button in self.process_buttons:
            button.setChecked(str(button.property("processName") or "") == target_process)
        default_code = self.PROCESS_DEFAULT_CODE.get(self.fixed_process or "")
        for code_name, checkbox in self.code_checks.items():
            checkbox.setChecked(bool(self.fixed_process) and code_name == default_code)
        default_name = self.PROCESS_DEFAULT_NAME.get(self.fixed_process or "", "판매")
        self.name_basis.setCurrentIndex(max(0, self.name_basis.findData(default_name)))
        if hasattr(self, "workable_only"):
            self.workable_only.setChecked(False)
        if hasattr(self, "compact_view"):
            self.compact_view.setChecked(False)
        if hasattr(self, "summary_mode"):
            self.summary_mode.setCurrentIndex(0)
        self._update_code_visibility()
        self._update_process_visibility()

    def _update_code_visibility(self, *_args: object) -> None:
        if not hasattr(self, "table"):
            return
        for column, checkbox in self.code_checks.items():
            self.table.table.setColumnHidden(DUE_DETAIL_COLUMNS.index(column), not checkbox.isChecked())
        # stateChanged가 전달된 사용자 변경일 때만 상단 마스터 검색을 재평가한다.
        # 표 로드 후 가시성 동기화 호출에서는 재귀 조회를 만들지 않는다.
        if _args:
            self.search_scope_changed.emit()

    def _selected_process(self) -> str:
        button = self.process_group.checkedButton()
        return str(button.property("processName")) if button else "누수규격"

    def _process_changed(self, *_args: object) -> None:
        """공정 선택에 맞춰 분류별 APS 부족수량과 표를 함께 갱신한다."""
        self.load(self.all_rows)

    def _update_process_visibility(self) -> None:
        if not hasattr(self, "table"):
            return
        selected = self._selected_process()
        visible_until = len(PROCESS_ORDER) - 1 if selected == "전체" else PROCESS_ORDER.index(selected)
        for index, column in enumerate(PROCESS_ORDER):
            self.table.table.setColumnHidden(DUE_DETAIL_COLUMNS.index(column), index > visible_until)

    def _update_name_basis(self, *_args: object) -> None:
        """현재 결과의 품명 열만 바꿔 대용량 표 전체 재생성을 피한다."""
        if not hasattr(self, "table"):
            return
        basis = str(self.name_basis.currentData() or "판매")
        for source in self._displayed_rows:
            source["품명"] = source.get(f"품명{basis}") or source.get("품명판매") or ""
        self.table.model.refresh_column("품명")
        self.search_scope_changed.emit()

    def master_search_fields(self) -> tuple[str, ...]:
        """현재 표에 실제 표시하도록 선택한 업무 필드만 반환한다."""
        fields = ["신규분류요약", "이니셜", "수주번호"]
        fields.extend(
            column for column, checkbox in self.code_checks.items()
            if checkbox.isChecked()
        )
        basis = str(self.name_basis.currentData() or "판매")
        fields.append(f"품명{basis}")
        fields.extend(("POWER", "CP", "AXIS", "ADD"))
        return tuple(fields)

    def _due_mode(self) -> str:
        button = self.due_group.checkedButton()
        return str(button.property("dueMode")) if button else "해제"

    def _due_mode_changed(self, *_args: object) -> None:
        mode = self._due_mode()
        today = QDate.currentDate()
        if mode == "당월": self.due_end.setDate(QDate(today.year(), today.month(), today.daysInMonth()))
        elif mode == "+7일": self.due_end.setDate(today.addDays(7))
        elif mode == "+14일": self.due_end.setDate(today.addDays(14))
        self.due_end.setEnabled(mode == "직접")
        self._apply_filter()

    def _due_limit(self) -> date | None:
        mode = self._due_mode()
        today = date.today()
        if mode == "해제": return None
        if mode == "당월":
            return (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if mode == "+7일": return today + timedelta(days=7)
        if mode == "+14일": return today + timedelta(days=14)
        selected = self.due_end.date()
        return date(selected.year(), selected.month(), selected.day())

    @staticmethod
    def _compact_text(values: list[str], limit: int = 3) -> str:
        unique = list(dict.fromkeys(value for value in values if value))
        if not unique:
            return ""
        visible = unique[:limit]
        return " / ".join(visible) + (f" 외 {len(unique) - limit}건" if len(unique) > limit else "")

    def _group_same_process_code(self, rows: list[dict], basis: str) -> list[dict]:
        code_field = self.PROCESS_DEFAULT_CODE.get(self.fixed_process or "")
        if not code_field:
            return rows
        grouped: dict[str, list[dict]] = {}
        for index, row in enumerate(rows):
            code = str(row.get(code_field) or "").strip()
            # 코드 미확인 행끼리 잘못 합치지 않는다.
            key = code or f"__missing__{index}"
            grouped.setdefault(key, []).append(row)
        result: list[dict] = []
        for members in grouped.values():
            if len(members) == 1:
                result.append(members[0])
                continue
            row = dict(members[0])
            row["공정"] = {
                process_name: sum(
                    float(member.get("공정", {}).get(process_name, 0) or 0)
                    for member in members
                )
                for process_name in PROCESS_ORDER
            }
            order_numbers = list(dict.fromkeys(
                str(member.get("수주번호") or "").strip() for member in members
                if member.get("수주번호")
            ))
            initials = [str(member.get("이니셜") or "").strip() for member in members]
            classifications = [str(member.get("신규분류요약") or "").strip() for member in members]
            names = [
                str(member.get(f"품명{basis}") or member.get("품명판매") or "").strip()
                for member in members
            ]
            row["수주번호"] = f"{len(order_numbers):,}건 묶음"
            row["_수주목록"] = "\n".join(order_numbers)
            row["이니셜"] = self._compact_text(initials)
            row["신규분류요약"] = self._compact_text(classifications)
            row["품명"] = self._compact_text(names)
            row["납기일"] = min(
                (str(member.get("납기일") or "9999-12-31") for member in members),
                default="",
            )
            result.append(row)
        return result

    def _group_main_rows(self, rows: list[dict], mode: str, basis: str) -> list[dict]:
        if mode not in {"order", "product"}:
            return rows
        grouped: dict[tuple[str, ...], list[dict]] = {}
        for index, row in enumerate(rows):
            order_number = str(row.get("수주번호") or "").strip()
            product_name = str(row.get("품명") or "").strip()
            key = (order_number,) if mode == "order" else (order_number, product_name)
            if not order_number or (mode == "product" and not product_name):
                key = (*key, f"__missing__{index}")
            grouped.setdefault(key, []).append(row)

        result: list[dict] = []
        for members in grouped.values():
            row = dict(members[0])
            if len(members) > 1:
                row["공정"] = {
                    process_name: sum(
                        float(member.get("공정", {}).get(process_name, 0) or 0)
                        for member in members
                    )
                    for process_name in PROCESS_ORDER
                }
            order_numbers = list(dict.fromkeys(
                str(member.get("수주번호") or "").strip() for member in members
                if member.get("수주번호")
            ))
            row["수주번호"] = self._compact_text(order_numbers)
            row["_수주목록"] = "\n".join(order_numbers)
            row["이니셜"] = self._compact_text([
                str(member.get("이니셜") or "").strip() for member in members
            ])
            row["신규분류요약"] = self._compact_text([
                str(member.get("신규분류요약") or "").strip() for member in members
            ])
            row["품명"] = self._compact_text([
                str(member.get("품명") or "").strip() for member in members
            ])
            for field in ("T코드", "P코드", "Q코드", "R코드"):
                row[field] = self._compact_text([
                    str(member.get(field) or "").strip() for member in members
                ])
            for field in ("POWER", "CP", "AXIS", "ADD"):
                row[field] = ""
            row["납기일"] = min(
                (str(member.get("납기일") or "9999-12-31") for member in members),
                default="",
            )
            row["_간략보기유형"] = mode
            if mode == "order":
                code_field = {"판매": "T코드", "P": "P코드", "Q": "Q코드", "R": "R코드"}.get(basis, "T코드")
                row["_제품목록"] = list(dict.fromkeys(
                    f"{str(member.get(code_field) or '-').strip().split('-', 1)[0]} / {str(member.get('품명') or '-').strip()}"
                    for member in members
                ))
            result.append(row)
        return result

    @staticmethod
    def _row_sort_key(row: dict) -> tuple:
        return (
            row.get("납기일") or "9999-12-31",
            power_sort_key(row.get("_POWER_NUM")),
            row.get("_분류정렬") or classification_sort_key(row.get("신규분류요약")),
            row.get("_제품정렬") or row.get("T코드") or "",
            row.get("수주번호") or "",
            float(row.get("_CP_NUM")) if row.get("_CP_NUM") is not None else float("inf"),
            int(row.get("_AXIS_NUM")) if row.get("_AXIS_NUM") is not None else 999,
            float(row.get("_ADD_NUM")) if row.get("_ADD_NUM") is not None else float("inf"),
        )

    def export_rows(self, source_rows: list[dict]) -> tuple[list[dict], list[dict]]:
        """화면 표시 옵션과 무관한 공정 고정 기준의 상세/간략 행을 만든다."""
        process = self.fixed_process or self._selected_process()
        basis = self.PROCESS_DEFAULT_NAME.get(self.fixed_process or "", "판매")
        categories = self._selected_classifications()
        token = self.search.text().strip().casefold()
        due_limit = self._due_limit()
        detail_rows: list[dict] = []
        search_fields = (
            "신규분류요약", "이니셜", "수주번호", "T코드", "P코드", "Q코드", "R코드",
            "품명판매", "품명P", "품명Q", "품명R", "POWER", "CP", "AXIS", "ADD",
        )
        for source in source_rows:
            if "전체" not in categories and source.get("신규분류요약") not in categories:
                continue
            if process != "전체" and float(source.get("공정", {}).get(process, 0) or 0) == 0:
                continue
            if (
                hasattr(self, "workable_only")
                and self.workable_only.isChecked()
                and (previous := self.PREVIOUS_PROCESS.get(self.fixed_process or ""))
                and float(source.get("공정", {}).get(previous, 0) or 0) != 0
            ):
                continue
            if due_limit is not None:
                try:
                    row_due = datetime.strptime(str(source.get("납기일") or "")[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
                if row_due > due_limit:
                    continue
            if token and token not in " ".join(
                str(source.get(field) or "") for field in search_fields
            ).casefold():
                continue
            row = dict(source)
            row["품명"] = source.get(f"품명{basis}") or source.get("품명판매") or ""
            detail_rows.append(row)
        detail_rows.sort(key=self._row_sort_key)
        compact_rows = self._group_same_process_code(detail_rows, basis)
        compact_rows.sort(key=self._row_sort_key)
        return detail_rows, compact_rows

    def _apply_filter(self, *_args: object) -> None:
        categories = self._selected_classifications()
        process = self._selected_process()
        basis = str(self.name_basis.currentData() or "판매")
        token = self.search.text().strip().casefold()
        due_limit = self._due_limit()
        rows = []
        for source in self.all_rows:
            if "전체" not in categories and source.get("신규분류요약") not in categories: continue
            if process != "전체" and float(source.get("공정", {}).get(process, 0) or 0) == 0: continue
            if (
                hasattr(self, "workable_only")
                and self.workable_only.isChecked()
                and (previous := self.PREVIOUS_PROCESS.get(self.fixed_process or ""))
                and float(source.get("공정", {}).get(previous, 0) or 0) != 0
            ):
                continue
            if due_limit is not None:
                try: row_due = datetime.strptime(str(source.get("납기일") or "")[:10], "%Y-%m-%d").date()
                except ValueError: continue
                if row_due > due_limit: continue
            if token:
                haystack = " ".join(str(source.get(key) or "") for key in ("신규분류요약", "이니셜", "수주번호", "T코드", "P코드", "Q코드", "R코드", "품명판매", "품명P", "품명Q", "품명R", "POWER", "CP", "AXIS", "ADD")).casefold()
                if token not in haystack: continue
            row = dict(source)
            row["품명"] = source.get(f"품명{basis}") or source.get("품명판매") or ""
            rows.append(row)
        if hasattr(self, "compact_view") and self.compact_view.isChecked():
            rows = self._group_same_process_code(rows, basis)
        if hasattr(self, "summary_mode"):
            rows = self._group_main_rows(rows, str(self.summary_mode.currentData() or "detail"), basis)
        rows.sort(key=self._row_sort_key)
        total = len(rows)
        self.filtered_rows_changed.emit(rows)
        quantity = sum(float(row.get("공정", {}).get(process, 0) or 0) if process != "전체" else sum(float(value or 0) for value in row.get("공정", {}).values()) for row in rows)
        self._filtered_rows = rows
        self._filtered_quantity = quantity
        self._current_page = 0
        self._render_page()
        self._update_code_visibility()
        self._update_process_visibility()

    def _change_page(self, offset: int) -> None:
        total_pages = max(1, (len(self._filtered_rows) + self.MAX_DISPLAY_ROWS - 1) // self.MAX_DISPLAY_ROWS)
        next_page = min(max(0, self._current_page + offset), total_pages - 1)
        if next_page == self._current_page:
            return
        self._current_page = next_page
        self._render_page()

    def _render_page(self) -> None:
        total = len(self._filtered_rows)
        total_pages = max(1, (total + self.MAX_DISPLAY_ROWS - 1) // self.MAX_DISPLAY_ROWS)
        self._current_page = min(self._current_page, total_pages - 1)
        start = self._current_page * self.MAX_DISPLAY_ROWS
        end = min(start + self.MAX_DISPLAY_ROWS, total)
        displayed = self._filtered_rows[start:end]
        self._displayed_rows = displayed
        range_text = f" · {start + 1:,}-{end:,}행 표시" if total else ""
        self.table.load(
            displayed,
            f"{total:,}행 · 필요수량 {format_number(self._filtered_quantity)}{range_text}",
        )
        self.page_status_label.setText(f"{self._current_page + 1} / {total_pages} 페이지")
        self.previous_page_button.setEnabled(self._current_page > 0)
        self.next_page_button.setEnabled(self._current_page + 1 < total_pages)
        self.pagination_bar.setVisible(True)


class ProcessOverviewPage(QWidget):
    process_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, fixed_process: str | None = None,
                 initial_rows: list[dict] | None = None, monitor_changes: bool = True) -> None:
        super().__init__(parent)
        self.fixed_process = fixed_process
        self.service = ProcessStatusService()
        self.all_rows: list[dict] = []
        self._db_signature: tuple[int, int] | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        filters = Card()
        top = QHBoxLayout(filters)
        top.setContentsMargins(16, 12, 16, 12)
        top.setSpacing(9)
        label = QLabel("진행현황"); label.setObjectName("FilterLabel"); top.addWidget(label)
        self.market_group = QButtonGroup(self); self.market_group.setExclusive(False)
        self.market_buttons = []
        for index, market in enumerate(("전체", "해외", "PB", "국내", "안전")):
            button = QPushButton(market); button.setObjectName("FilterButton"); button.setCheckable(True)
            button.setStyleSheet(FILTER_BUTTON_SELECTION_STYLE)
            button.setProperty("market", market); button.setChecked(index == 0)
            button.clicked.connect(lambda _checked=False, selected=button: self._market_changed(selected))
            self.market_group.addButton(button); self.market_buttons.append(button); top.addWidget(button)
        top.addSpacing(14)
        label = QLabel("관별"); label.setObjectName("FilterLabel"); top.addWidget(label)
        factory = QLabel("S관(3공장)"); factory.setObjectName("FixedFactoryChip"); factory.setAlignment(Qt.AlignCenter); factory.setMinimumWidth(108)
        factory.setToolTip("생산3팀 전용 프로그램은 S관 데이터만 조회합니다."); top.addWidget(factory)
        top.addStretch()
        label = QLabel("통합검색"); label.setObjectName("FilterLabel"); top.addWidget(label)
        self.search = QLineEdit(); self.search.setObjectName("SearchInput"); self.search.setMinimumWidth(210); self.search.setMaximumWidth(320)
        self.search.setClearButtonEnabled(True); self.search.setPlaceholderText("전체검색: 이니셜·수주번호·품번·품명·POWER / 쉼표(,) OR / * 전체"); self.search.returnPressed.connect(self._apply_market_view); self.search.textChanged.connect(self._top_search_text_changed); top.addWidget(self.search)
        # 갱신 상태는 내부 로직에서 유지하되 상단에는 불필요한 완료 문구를 표시하지 않는다.
        self.status = QLabel("준비", self); self.status.setObjectName("StatusChip"); self.status.hide()
        refresh = QPushButton("조회"); refresh.setObjectName("PrimaryButton"); refresh.setIcon(qta.icon("fa6s.magnifying-glass", color="#FFFFFF")); refresh.clicked.connect(self.reload_data); top.addWidget(refresh)
        reset = QPushButton("필터 초기화"); reset.setObjectName("SecondaryButton")
        reset.setIcon(qta.icon("fa6s.arrow-rotate-left", color="#52677E"))
        reset.setMinimumWidth(118)
        reset.setToolTip("상단 검색과 현재 탭의 모든 필터를 기본값으로 되돌립니다.")
        reset.clicked.connect(self.reset_all_filters)
        self.reset_button = reset
        top.addWidget(reset)
        if fixed_process:
            export = QPushButton("엑셀 내보내기")
            export.setObjectName("SecondaryButton")
            export.setIcon(qta.icon("fa6s.file-excel", color="#168A45"))
            export.setMinimumWidth(126)
            export.setToolTip("현재 필터 결과를 바탕화면에 두 개 시트의 엑셀 파일로 저장합니다.")
            export.clicked.connect(self._export_excel)
            self.export_button = export
            top.addWidget(export)
        root.addWidget(filters)
        kpis = QHBoxLayout(); kpis.setSpacing(10)
        self.kpi_all = KpiCard("진행 대상 수주", "#0A7AFF")
        self.kpi_all.set_clickable(True, "공정 현황 전체 탭으로 이동")
        self.kpi_all.set_selected_process(fixed_process is None)
        self.kpi_all.clicked.connect(lambda: self.process_requested.emit("process_overview"))
        self.process_kpis: dict[str, KpiCard] = {}
        process_cards = (
            ("사출", "사출", "#0A7AFF", "injection"),
            ("분리", "분리", "#22B95A", "separation"),
            ("하이드레이션", "하이드레이션", "#7C3AED", "hydration"),
            ("접착", "검사·접착", "#E69000", "inspection"),
            ("누수규격", "누수·규격", "#00A7A7", "leak"),
        )
        cards = [self.kpi_all]
        for process_name, title, color, page_key in process_cards:
            card = KpiCard(title, color)
            card.value.setProperty("compact", True)
            card.set_clickable(True, f"{title} 공정 탭으로 이동")
            card.set_selected_process(process_name == fixed_process)
            card.clicked.connect(lambda target=page_key: self.process_requested.emit(target))
            self.process_kpis[process_name] = card
            cards.append(card)
        for card in cards:
            card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed); card.setMinimumHeight(98); kpis.addWidget(card)
        root.addLayout(kpis)
        self.detail_page = DueDetailPage(fixed_process=fixed_process); self.detail_page.reset_requested.connect(self.reset_all_filters); self.detail_page.filtered_rows_changed.connect(self._update_kpis); self.detail_page.search_scope_changed.connect(self._apply_market_view); root.addWidget(self.detail_page, 1)
        if initial_rows is None:
            self.reload_data()
        else:
            self.load_normalized_rows(initial_rows)
        self.snapshot_timer = QTimer(self)
        self.snapshot_timer.setInterval(15_000)
        self.snapshot_timer.timeout.connect(self._refresh_if_changed)
        if monitor_changes:
            self.snapshot_timer.start()

    @staticmethod
    def _signature() -> tuple[int, int] | None:
        try:
            stat = Path(DB_PATH).stat(); return stat.st_size, stat.st_mtime_ns
        except OSError: return None

    @staticmethod
    def _normalize(rows: list[dict]) -> list[dict]:
        result = []
        for source in rows:
            row = dict(source); row["공정"] = {name: float(source.get(name) or 0) for name in PROCESS_ORDER}
            row["구분"] = source.get("진행현황") or "국내"; row["품명판매"] = source.get("품명판매") or source.get("품명") or ""
            row["_분류정렬"] = classification_sort_key(row.get("신규분류요약")); result.append(row)
        return result

    def reload_data(self) -> None:
        self.status.setText("데이터 갱신 중…"); self.status.setProperty("status", "warning"); _repolish(self.status)
        self.all_rows = self._normalize(self.service.load_rows()); self._db_signature = self._signature(); self._apply_market_view()

    def load_normalized_rows(self, rows: list[dict]) -> None:
        """이미 읽은 APS 공정 스냅샷을 전용 탭끼리 공유한다."""
        self.all_rows = list(rows)
        self._db_signature = self._signature()
        self._apply_market_view()

    def search_from_risk(self, order_no: str) -> None:
        """리스크 카드의 수주번호를 전체 시장 통합검색으로 표시한다."""
        for index, button in enumerate(self.market_buttons):
            button.setChecked(index == 0)
        self.detail_page.reset_filters()
        self.search.setText(str(order_no or "").strip())
        self._apply_market_view()
        self.search.setFocus(Qt.OtherFocusReason)
        self.search.selectAll()

    def reset_all_filters(self) -> None:
        """상단 통합검색과 상세 조건을 최초 진입 기본값으로 되돌린다."""
        for index, button in enumerate(self.market_buttons):
            button.setChecked(index == 0)
        self.search.clear()
        self.detail_page.reset_filters()
        self._apply_market_view()

    def _top_search_text_changed(self, text: str) -> None:
        # 조회된 검색어를 X/Backspace로 비우면 즉시 전체 데이터로 복귀한다.
        if not str(text or "").strip():
            self._apply_market_view()

    def _refresh_if_changed(self) -> None:
        if self._signature() != self._db_signature: self.reload_data()

    def _market_changed(self, selected: QPushButton) -> None:
        market = str(selected.property("market") or ""); all_button = self.market_buttons[0]; categories = self.market_buttons[1:]
        if market == "전체":
            all_button.setChecked(True)
            for button in categories: button.setChecked(False)
        else:
            if selected.isChecked(): all_button.setChecked(False)
            if not any(button.isChecked() for button in categories): all_button.setChecked(True)
        self._apply_market_view()

    def _apply_market_view(self) -> None:
        markets = {str(button.property("market")) for button in self.market_buttons if button.isChecked()} or {"전체"}
        raw_search = self.search.text().replace("，", ",").strip()
        tokens = tuple(dict.fromkeys(
            token.strip().casefold() for token in raw_search.split(",") if token.strip()
        ))
        search_all = not tokens or "*" in tokens
        search_fields = self.detail_page.master_search_fields()
        rows = [
            row for row in self.all_rows
            if ("전체" in markets or row.get("구분") in markets)
            and (
                search_all
                or any(
                    token in str(row.get(field) or "").casefold()
                    for token in tokens
                    for field in search_fields
                )
            )
        ]
        orders = len({str(row.get("수주번호") or "") for row in rows if row.get("수주번호")})
        self.kpi_all.set_data(f"{orders:,}건", f"S관 · 세부 품목 {len(rows):,}종")
        self.detail_page.load(rows)
        self.status.setText(f"완료 · S관(3공장) {orders:,}건"); self.status.setProperty("status", "success" if rows else "warning")
        self.status.setToolTip(f"APS 원천 갱신 {self.service.status().get('source_refreshed_at') or '-'}"); _repolish(self.status)

    def _export_master_rows(self) -> list[dict]:
        """코드 표시와 품명 기준에 흔들리지 않는 전체 APS 필드 검색 결과."""
        markets = {str(button.property("market")) for button in self.market_buttons if button.isChecked()} or {"전체"}
        raw_search = self.search.text().replace("，", ",").strip()
        tokens = tuple(dict.fromkeys(
            token.strip().casefold() for token in raw_search.split(",") if token.strip()
        ))
        search_all = not tokens or "*" in tokens
        fields = (
            "신규분류요약", "이니셜", "수주번호", "T코드", "P코드", "Q코드", "R코드",
            "품명판매", "품명P", "품명Q", "품명R", "POWER", "CP", "AXIS", "ADD",
        )
        return [
            row for row in self.all_rows
            if ("전체" in markets or row.get("구분") in markets)
            and (
                search_all
                or any(
                    token in str(row.get(field) or "").casefold()
                    for token in tokens
                    for field in fields
                )
            )
        ]

    def _export_excel(self) -> None:
        if not self.fixed_process or not hasattr(self, "export_button"):
            return
        button = self.export_button
        original_text = button.text()
        button.setEnabled(False)
        button.setText("내보내는 중…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            source_rows = self._export_master_rows()
            detail_rows, compact_rows = self.detail_page.export_rows(source_rows)
            output = export_process_workbook(self.fixed_process, detail_rows, compact_rows)
            button.setText("저장 완료 ✓")
            button.setToolTip(f"저장 완료: {output}")
            QTimer.singleShot(2500, lambda: button.setText(original_text))
        except Exception as exc:  # 사용자 PC의 엑셀 런타임/파일 잠금 오류를 안내한다.
            button.setText(original_text)
            show_app_message(
                self,
                "엑셀 내보내기 실패",
                f"엑셀 파일을 만들지 못했습니다.\n\n{exc}",
                kind="error",
            )
        finally:
            QApplication.restoreOverrideCursor()
            button.setEnabled(True)

    def _update_kpis(self, rows: list[dict]) -> None:
        orders: set[str] = set()
        for row in rows:
            grouped_orders = str(row.get("_수주목록") or "").splitlines()
            if grouped_orders:
                orders.update(order.strip() for order in grouped_orders if order.strip())
            elif row.get("수주번호"):
                orders.add(str(row["수주번호"]).strip())
        self.kpi_all.set_data(f"{len(orders):,}건", f"S관 · 현재 필터 품목 {len(rows):,}종")
        for process_name, card in self.process_kpis.items():
            quantity = sum(float(row.get("공정", {}).get(process_name, 0) or 0) for row in rows)
            card.set_data(f"{quantity:,.0f} pcs", "선택 조건 부족수량 · 클릭하여 이동")
