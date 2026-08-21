from __future__ import annotations

import hashlib
import sqlite3
import sys
import threading
import textwrap
from html import escape
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import (
    QObject,
    QMimeData,
    QPointF,
    QRunnable,
    QRect,
    QRectF,
    QSignalBlocker,
    QSize,
    QStringListModel,
    QThreadPool,
    Qt,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtPdf import QPdfDocument
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QScrollArea, QToolTip
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QCompleter,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QVBoxLayout,
    QWidget,
)

import qtawesome as qta

from config import LEAD_SHEET_PDF_BACKUP_DIR, LEAD_SHEET_PREVIEW_CACHE_DIR
from services.bom_explorer import BomExplorerService
from services.item_code_service import ItemCodeService
from ui.startup_splash import SmoothActivityBar


PREFIX_COLORS = {
    "T": "#0A7AFF",
    "S": "#315B8A",
    "P": "#536AF0",
    "Q": "#11A7A0",
    "R": "#F59E0B",
    "B": "#8B6F47",
    "A": "#8B5CF6",
}


def _set_persistent_clipboard_text(text: str) -> None:
    """Write text to Windows and materialize Qt's delayed clipboard data."""
    QApplication.clipboard().setText(text)
    QApplication.processEvents()
    if sys.platform == "win32":
        try:
            import ctypes

            # Qt can expose clipboard data through delayed OLE rendering.  The BOM
            # window is hidden when another module opens, so flush it now to make
            # the text independently available to that process.
            ctypes.windll.ole32.OleFlushClipboard()
        except (AttributeError, OSError):
            pass


class ItemCodeLoadSignals(QObject):
    finished = Signal(int, object)
    failed = Signal(int, str)


class ItemCodeLoadTask(QRunnable):
    def __init__(
        self,
        request_id: int,
        service: ItemCodeService,
        codes: list[str],
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.request_id = request_id
        self.service = service
        self.codes = codes
        self.cancel_event = threading.Event()
        self.signals = ItemCodeLoadSignals()

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> None:
        try:
            result = self.service.load_many(
                self.codes,
                max_age_seconds=900,
                cancel_event=self.cancel_event,
            )
        except Exception as exc:
            try:
                self.signals.failed.emit(self.request_id, str(exc))
            except RuntimeError:
                pass
            return
        try:
            self.signals.finished.emit(self.request_id, result)
        except RuntimeError:
            pass


class BomWarmupSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class BomWarmupTask(QRunnable):
    """Warm local BOM caches without delaying the interactive first frame."""

    def __init__(self, service: BomExplorerService) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.service = service
        self.signals = BomWarmupSignals()

    def run(self) -> None:
        try:
            result = self.service.warmup()
        except Exception as exc:
            try:
                self.signals.failed.emit(str(exc))
            except RuntimeError:
                pass
            return
        try:
            self.signals.finished.emit(result)
        except RuntimeError:
            pass


class CopyableTableWidget(QTableWidget):
    """Excel-friendly table copy that includes selected column headers."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.excel_text_columns: set[int] = set()

    @staticmethod
    def _clipboard_text(value: str) -> str:
        return str(value or "").replace("\t", " ").replace("\r", " ").replace("\n", " ")

    def copy_selection_with_headers(self) -> None:
        ranges = self.selectedRanges()
        if not ranges:
            return
        top = min(selected.topRow() for selected in ranges)
        bottom = max(selected.bottomRow() for selected in ranges)
        left = min(selected.leftColumn() for selected in ranges)
        right = max(selected.rightColumn() for selected in ranges)
        headers = [
            self._clipboard_text(
                self.horizontalHeaderItem(column).text()
                if self.horizontalHeaderItem(column) else ""
            )
            for column in range(left, right + 1)
        ]
        lines = ["\t".join(headers)]
        html_rows = [
            "<tr>" + "".join(f"<th>{escape(value)}</th>" for value in headers) + "</tr>"
        ]
        for row in range(top, bottom + 1):
            plain_cells: list[str] = []
            html_cells: list[str] = []
            for column in range(left, right + 1):
                value = self._clipboard_text(
                    self.item(row, column).text() if self.item(row, column) else ""
                )
                if column in self.excel_text_columns:
                    # Excel prefers the HTML clipboard and keeps this as text.
                    # The formula-string fallback also preserves leading zeroes
                    # when only TSV/plain text is accepted.
                    plain_cells.append(f'=\"{value.replace(chr(34), chr(34) * 2)}\"')
                    html_cells.append(
                        "<td style=\"mso-number-format:'\\@';white-space:nowrap\">"
                        f"{escape(value)}</td>"
                    )
                else:
                    plain_cells.append(value)
                    html_cells.append(f"<td>{escape(value)}</td>")
            lines.append("\t".join(plain_cells))
            html_rows.append("<tr>" + "".join(html_cells) + "</tr>")
        mime = QMimeData()
        mime.setText("\n".join(lines))
        mime.setHtml(
            "<html><body><table>" + "".join(html_rows) + "</table></body></html>"
        )
        QApplication.clipboard().setMimeData(mime)

    def keyPressEvent(self, event) -> None:
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection_with_headers()
            event.accept()
            return
        super().keyPressEvent(event)


class ItemCodeProgressDialog(QDialog):
    """Control Tower-style modal that remains visibly responsive during API work."""

    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._finished = False
        self.setWindowTitle("품목코드 조회")
        self.setModal(True)
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(520, 300)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        self.card = QFrame()
        self.card.setObjectName("itemCodeProgressCard")
        self.card.setStyleSheet("""
            QFrame#itemCodeProgressCard {
                background: #FFFFFF;
                border: 1px solid #DDE8F4;
                border-radius: 20px;
            }
            QLabel { background: transparent; border: 0; }
            QLabel#itemCodeProgressTitle {
                color: #172033; font-size: 17px; font-weight: 800;
            }
            QLabel#itemCodeProgressBadge {
                color: #0A63D8; background: #EAF3FF;
                border: 1px solid #CBE1FF; border-radius: 10px;
                padding: 4px 9px; font-size: 11px; font-weight: 700;
            }
            QLabel#itemCodeProgressMessage {
                color: #34465A; font-size: 13px; font-weight: 600;
            }
            QLabel#itemCodeProgressCodes {
                color: #0A63D8; background: #F3F8FE;
                border: 1px solid #DCEBFA; border-radius: 8px;
                padding: 7px 10px; font-size: 11px; font-weight: 700;
            }
            QLabel#itemCodeProgressHint {
                color: #8492A3; font-size: 11px;
            }
            QPushButton {
                min-width: 80px; min-height: 30px;
                color: white; background: #0A7AFF; border: 0;
                border-radius: 8px; font-weight: 700;
            }
            QPushButton:hover { background: #006BE6; }
            QPushButton#itemCodeCancelButton {
                color: #52677E; background: #FFFFFF;
                border: 1px solid #CAD8E7;
            }
            QPushButton#itemCodeCancelButton:hover {
                color: #17324D; background: #F3F7FB; border-color: #9FB7CF;
            }
        """)
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(32, 62, 94, 58))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(0)
        header = QHBoxLayout()
        header.setSpacing(11)
        icon = QLabel()
        icon.setFixedSize(38, 38)
        icon.setAlignment(Qt.AlignCenter)
        icon.setPixmap(qta.icon("fa5s.database", color="#0A7AFF").pixmap(20, 20))
        icon.setStyleSheet("background:#EAF3FF;border-radius:11px;")
        title = QLabel("품목코드를 불러오고 있어요")
        title.setObjectName("itemCodeProgressTitle")
        title.setMinimumWidth(230)
        self.badge = QLabel("API 조회 중")
        self.badge.setObjectName("itemCodeProgressBadge")
        header.addWidget(icon)
        header.addWidget(title, 1)
        header.addWidget(self.badge)
        layout.addLayout(header)
        layout.addSpacing(18)
        self.message = QLabel("품목코드 수집 중입니다.\n잠시만 기다려 주세요.")
        self.message.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.message.setObjectName("itemCodeProgressMessage")
        self.message.setWordWrap(True)
        self.codes = QLabel("선택된 BOM 품번을 확인하고 있습니다.")
        self.codes.setObjectName("itemCodeProgressCodes")
        self.codes.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.activity = SmoothActivityBar()
        self.cancel_button = QPushButton("조회 중단")
        self.cancel_button.setObjectName("itemCodeCancelButton")
        self.cancel_button.clicked.connect(self._cancel_clicked)
        self.ok_button = QPushButton("확인")
        self.ok_button.setVisible(False)
        self.ok_button.clicked.connect(self.accept)
        layout.addWidget(self.message)
        layout.addSpacing(10)
        layout.addWidget(self.codes)
        layout.addSpacing(15)
        layout.addWidget(self.activity)
        layout.addSpacing(10)
        hint = QLabel("처리가 끝나면 이 창은 자동으로 닫힙니다.")
        hint.setObjectName("itemCodeProgressHint")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)
        layout.addSpacing(9)
        actions = QHBoxLayout()
        actions.addStretch()
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.ok_button)
        layout.addLayout(actions)

    def set_codes(self, codes: list[str]) -> None:
        self.codes.setText("  ·  ".join(codes))

    def _cancel_clicked(self) -> None:
        self._finished = True
        self.cancel_requested.emit()
        super().reject()

    def finish(self, message: str, *, success: bool) -> None:
        self._finished = True
        self.message.setText(message)
        self.activity.hide()
        if not success:
            self.badge.setText("확인 필요")
            self.badge.setStyleSheet(
                "color:#C2413A;background:#FFF0EF;border:1px solid #F4C6C2;"
                "border-radius:10px;padding:4px 9px;font-size:11px;font-weight:700;"
            )
            self.cancel_button.setVisible(False)
            self.ok_button.setVisible(True)
            self.ok_button.setEnabled(True)
            self.ok_button.setDefault(True)
            self.ok_button.setFocus()

    def reject(self) -> None:
        if self._finished:
            super().reject()


def _classification_sort_key(value: str) -> tuple[int, int, int, int, str]:
    """Obsidian 업무 기준: clear/color 각각 FRP→1-Day, HEMA→Si, 광학 순."""
    text = str(value or "").strip().upper()
    is_color = 1 if "_COLOR_" in text else 0
    is_silicone = 1 if text.startswith("SI_") else 0
    base = text[3:] if is_silicone else text
    if base.startswith("FRP"):
        family = 0
    elif base.startswith("1-DAY"):
        family = 1
    else:
        family = 9

    # More specific suffixes must be checked first because Fix contains Sph
    # and M/F_Toric contains M/F.
    if "FIX2" in base:
        optic = 4
    elif "FIX" in base:
        optic = 3
    elif "TORIC" in base:
        optic = 2
    elif "M/F" in base:
        optic = 1
    elif "SPH" in base:
        optic = 0
    else:
        optic = 9
    return (is_color, family, is_silicone, optic, text)


def _color_for(code: str) -> QColor:
    return QColor(PREFIX_COLORS.get(code[:1].upper(), "#7B8794"))


class BomNodeItem(QGraphicsObject):
    def __init__(
        self,
        item: dict[str, str],
        rect: QRectF,
        *,
        selected: bool = False,
        search_root: bool = False,
        callback: Callable[[str], None] | None = None,
        requery_callback: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.item = item
        self.rect = rect
        self.selected = selected
        self.search_root = search_root
        self.path_active = selected
        self.dimmed = False
        self.callback = callback
        self.requery_callback = requery_callback
        self.setPos(rect.topLeft())
        self._local_rect = QRectF(0, 0, rect.width(), rect.height())
        self.setAcceptHoverEvents(callback is not None)
        dia_bc = " / ".join(
            value for value in (item.get("dia", ""), item.get("bc", "")) if value
        )
        tooltip_fields = (
            ("품번", item.get("code", "")),
            ("품명", item.get("name", "")),
            ("품목구분", item.get("kind", "")),
            ("사용 여부", "사용" if item.get("use_yn") == "Y" else "미사용"),
            ("모델", item.get("model_name") or item.get("model_no", "")),
            ("공장", item.get("factory", "")),
            ("착용주기", item.get("wear_cycle", "")),
            ("DIA / BC", dia_bc),
            ("컬러 여부", item.get("color_yn", "")),
            ("등록일", item.get("registered_at", "")),
        )
        self.setToolTip(
            "\n".join(f"{label}  {value or '-'}" for label, value in tooltip_fields)
        )
        if callback:
            self.setCursor(Qt.PointingHandCursor)
        self.setZValue(5)

    def boundingRect(self) -> QRectF:
        return self._local_rect.adjusted(-3, -3, 3, 3)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        hovered = self.isUnderMouse() and self.callback is not None
        border = (
            QColor("#2F80ED")
            if self.selected or hovered
            else QColor("#78A6E8")
            if self.path_active
            else QColor("#7C8CF5")
            if self.search_root
            else QColor("#DCE3EB")
        )
        fill = (
            QColor("#F1F6FC")
            if self.selected
            else QColor("#F8FBFF")
            if self.path_active
            else QColor("#FFFFFF")
        )
        border_pen = QPen(border, 2.0 if self.selected else 1.25 if self.path_active else 1)
        if self.search_root and not self.selected:
            border_pen.setStyle(Qt.DashLine)
        painter.setPen(border_pen)
        painter.setBrush(fill)
        painter.drawRoundedRect(self._local_rect, 11, 11)

        accent = _color_for(self.item.get("code", ""))
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent)
        painter.drawRoundedRect(QRectF(0, 0, 5, self._local_rect.height()), 2.5, 2.5)

        code = self.item.get("code", "")
        name = self.item.get("name", "") or "품명 정보 없음"
        kind = self.item.get("kind", "") or code[:1]
        use_text = "사용" if self.item.get("use_yn", "Y") == "Y" else "미사용"
        left = 15
        width = self._local_rect.width() - 30

        painter.setPen(QColor("#152238"))
        compact = self._local_rect.height() < 56
        painter.setFont(QFont("Malgun Gothic", 9 if compact else 10 if not self.selected else 13, QFont.Bold))
        painter.drawText(QRectF(left, 4 if compact else 8, width, 20 if compact else 23), Qt.AlignLeft | Qt.AlignVCenter, code)

        painter.setFont(QFont("Malgun Gothic", 7 if compact else 8 if not self.selected else 9, QFont.DemiBold))
        painter.setPen(QColor("#526477"))
        elided = painter.fontMetrics().elidedText(name, Qt.ElideRight, int(width))
        painter.drawText(
            QRectF(left, 23 if compact else 30 if not self.selected else 39, width, 18 if compact else 21),
            Qt.AlignLeft | Qt.AlignVCenter,
            elided,
        )

        if not compact:
            painter.setFont(QFont("Malgun Gothic", 7 if not self.selected else 8, QFont.Medium))
            painter.setPen(QColor("#7A8795"))
            bottom = self._local_rect.height() - 23
            painter.drawText(QRectF(left, bottom, width * 0.58, 18), Qt.AlignLeft | Qt.AlignVCenter, f"구분  {kind}")
            painter.setPen(QColor("#0E9F6E") if use_text == "사용" else QColor("#DC2626"))
            painter.drawText(QRectF(left + width * 0.55, bottom, width * 0.4, 18), Qt.AlignRight | Qt.AlignVCenter, use_text)

        if self.selected:
            qta.icon("fa5s.check-square", color="#0A7AFF").paint(
                painter,
                QRect(int(self._local_rect.width() - 23), 8, 14, 14),
            )
        elif self.callback:
            painter.setFont(QFont("Segoe UI", 11, QFont.Bold))
            painter.setPen(QColor("#91A0B0"))
            painter.drawText(
                QRectF(self._local_rect.width() - 22, 4, 15, 23),
                Qt.AlignCenter,
                "›",
            )

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        self.update()

    def set_path_state(self, *, selected: bool, active: bool, dimmed: bool) -> None:
        self.selected = selected
        self.path_active = active
        self.dimmed = dimmed
        self.setOpacity(0.48 if dimmed else 1.0)
        self.setZValue(8 if selected else 6 if active else 5)
        self.update()

    def mousePressEvent(self, event) -> None:
        if self.callback:
            self.callback(self.item.get("code", ""))
            event.accept()
            return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        code = str(self.item.get("code", "")).strip()
        if not code or self.requery_callback is None:
            super().contextMenuEvent(event)
            return
        menu = QMenu()
        requery_action = menu.addAction(
            qta.icon("fa5s.search", color="#0A7AFF"),
            f"{code} 기준으로 재조회",
        )
        selected_action = menu.exec(event.screenPos())
        if selected_action is requery_action:
            self.requery_callback(code)
            event.accept()
            return
        super().contextMenuEvent(event)

    def hoverEnterEvent(self, event) -> None:
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self.update()
        super().hoverLeaveEvent(event)


class BomFlowView(QGraphicsView):
    item_selected = Signal(str)
    item_requery_requested = Signal(str)
    active_path_changed = Signal(object)

    STAGE_TITLES = (
        "판매코드",
        "생산코드",
        "분리코드",
        "사출코드",
        "사출코드 하위",
    )

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("bomFlowView")
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setFrameShape(QFrame.NoFrame)
        self.setBackgroundBrush(QColor("#FFFFFF"))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setMinimumHeight(560)
        self.setMaximumHeight(650)
        self.node_items: dict[str, BomNodeItem] = {}
        self.flow_items: dict[tuple[str, str], QGraphicsPathItem] = {}
        self.edges: list[dict[str, str]] = []
        self.stage_codes: list[list[str]] = []
        self.search_root_code = ""
        self.selected_code = ""
        self.set_hierarchy({})

    @staticmethod
    def _positions(count: int, height: float, card_height: float) -> list[float]:
        if count <= 0:
            return []
        return [18.0 + (card_height + 8.0) * index for index in range(count)]

    def _visible(self, rows: list[dict[str, str]]) -> list[dict[str, str]]:
        return rows

    def set_hierarchy(self, hierarchy: dict[str, Any]) -> None:
        scene = self.scene()
        scene.clear()
        self.node_items.clear()
        self.flow_items.clear()
        width = 1280.0
        columns = hierarchy.get("columns", [])
        self.stage_codes = [
            [str(row.get("code", "")) for row in column if row.get("code")]
            for column in columns
        ]
        selected_code = hierarchy.get("selected_code", "")
        self.search_root_code = selected_code
        self.selected_code = selected_code
        visible_count = max((len(column) for column in columns), default=1)
        height = max(500.0, visible_count * 56.0 + 30.0)
        scene.setSceneRect(0, 0, width, height)

        if not columns or not selected_code:
            self.active_path_changed.emit([[] for _title in self.STAGE_TITLES])
            empty = scene.addSimpleText("품번을 검색하면 상위·하위 BOM 연결 흐름이 표시됩니다.")
            empty.setFont(QFont("Malgun Gothic", 11, QFont.DemiBold))
            empty.setBrush(QColor("#7B8794"))
            empty.setPos(width / 2 - empty.boundingRect().width() / 2, height / 2)
            self._fit_scene()
            return

        visible_columns = [self._visible(column) for column in columns]
        self.edges = list(hierarchy.get("edges", []))
        column_count = len(visible_columns)
        card_w, card_h = 218.0, 48.0
        gap = (width - 36.0 - card_w * column_count) / max(1, column_count - 1)
        column_x = [18.0 + index * (card_w + gap) for index in range(column_count)]
        node_rects: dict[str, QRectF] = {}

        for index, column in enumerate(visible_columns):
            positions = self._positions(len(column), height, card_h)
            for row, y in zip(column, positions):
                node_rects[row["code"]] = QRectF(column_x[index], y, card_w, card_h)

        for edge in self.edges:
            parent_rect = node_rects.get(edge.get("parent", ""))
            child_rect = node_rects.get(edge.get("child", ""))
            if parent_rect is None or child_rect is None:
                continue
            flow = self._add_flow(
                QPointF(parent_rect.right(), parent_rect.center().y()),
                QPointF(child_rect.left(), child_rect.center().y()),
                _color_for(edge.get("parent", "")),
                edge.get("qty", "-"),
            )
            self.flow_items[(edge.get("parent", ""), edge.get("child", ""))] = flow

        for column in visible_columns:
            for row in column:
                rect = node_rects.get(row.get("code", ""))
                if rect is None:
                    continue
                node = BomNodeItem(
                    row,
                    rect,
                    selected=row.get("code") == selected_code,
                    search_root=row.get("code") == selected_code,
                    callback=self.item_selected.emit,
                    requery_callback=self.item_requery_requested.emit,
                )
                self.node_items[row["code"]] = node
                scene.addItem(node)
        self._fit_scene()
        selected_node = self.node_items.get(selected_code)
        if selected_node is not None:
            self.ensureVisible(selected_node, 24, 110)
        self._apply_path_highlight(selected_code)

    def set_selected(self, code: str) -> None:
        if code not in self.node_items:
            return
        self.selected_code = code
        self._apply_path_highlight(code)

    def _apply_path_highlight(self, code: str) -> None:
        if code not in self.node_items:
            return

        incoming: dict[str, list[str]] = {}
        outgoing: dict[str, list[str]] = {}
        for edge in self.edges:
            parent = edge.get("parent", "")
            child = edge.get("child", "")
            if parent in self.node_items and child in self.node_items:
                outgoing.setdefault(parent, []).append(child)
                incoming.setdefault(child, []).append(parent)

        active_nodes = {code}
        active_edges: set[tuple[str, str]] = set()

        frontier = [code]
        while frontier:
            child = frontier.pop()
            for parent in incoming.get(child, []):
                edge_key = (parent, child)
                if edge_key in active_edges:
                    continue
                active_edges.add(edge_key)
                if parent not in active_nodes:
                    active_nodes.add(parent)
                    frontier.append(parent)

        frontier = [code]
        while frontier:
            parent = frontier.pop()
            for child in outgoing.get(parent, []):
                edge_key = (parent, child)
                if edge_key in active_edges:
                    continue
                active_edges.add(edge_key)
                if child not in active_nodes:
                    active_nodes.add(child)
                    frontier.append(child)

        for item_code, node in self.node_items.items():
            node.set_path_state(
                selected=item_code == code,
                active=item_code in active_nodes,
                dimmed=item_code not in active_nodes,
            )

        for edge_key, flow in self.flow_items.items():
            is_active = edge_key in active_edges
            pen = QPen(
                QColor(72, 128, 196, 175) if is_active else QColor(148, 163, 184, 42),
                2.0 if is_active else 1.0,
                Qt.SolidLine,
                Qt.RoundCap,
                Qt.RoundJoin,
            )
            flow.setPen(pen)
            flow.setZValue(1 if is_active else -2)

        self.active_path_changed.emit(self.active_codes_by_stage())

    def active_codes_by_stage(self) -> list[list[str]]:
        """Return active path codes in their visible stage/card order."""
        return [
            [
                code
                for code in stage
                if code in self.node_items and self.node_items[code].path_active
            ]
            for stage in self.stage_codes
        ]

    def _add_flow(
        self,
        start: QPointF,
        end: QPointF,
        color: QColor,
        quantity: str,
    ) -> QGraphicsPathItem:
        path = QPainterPath(start)
        distance = end.x() - start.x()
        path.cubicTo(
            QPointF(start.x() + distance * 0.44, start.y()),
            QPointF(end.x() - distance * 0.44, end.y()),
            end,
        )
        flow_color = QColor(color)
        flow_color.setAlpha(105)
        flow = QGraphicsPathItem(path)
        flow.setPen(QPen(flow_color, 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        flow.setZValue(-1)
        self.scene().addItem(flow)
        return flow

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_scene()

    def _fit_scene(self) -> None:
        if self.scene() and self.scene().sceneRect().isValid():
            self.resetTransform()
            scene_width = self.scene().sceneRect().width()
            if scene_width > 0:
                factor = max(0.1, (self.viewport().width() - 8) / scene_width)
                self.scale(factor, factor)


class LeadPdfRenderSignals(QObject):
    finished = Signal(int, object, int, int)


class LeadPdfRenderTask(QRunnable):
    """Render one cropped PDF page to memory at the requested display size."""

    def __init__(self, request_id: int, pdf_path: str, width: int, height: int) -> None:
        super().__init__()
        self.request_id = request_id
        self.pdf_path = pdf_path
        self.width = max(1, width)
        self.height = max(1, height)
        self.signals = LeadPdfRenderSignals()

    def run(self) -> None:
        try:
            import pypdfium2 as pdfium

            document = pdfium.PdfDocument(self.pdf_path)
            page = document[0]
            page_width, page_height = page.get_size()
            scale = min(self.width / page_width, self.height / page_height)
            bitmap = page.render(scale=scale)
            pil_image = bitmap.to_pil().convert("RGBA")
            raw = pil_image.tobytes("raw", "RGBA")
            image = QImage(
                raw,
                pil_image.width,
                pil_image.height,
                pil_image.width * 4,
                QImage.Format.Format_RGBA8888,
            ).copy()
            self.signals.finished.emit(
                self.request_id,
                image,
                pil_image.width,
                pil_image.height,
            )
            page.close()
            document.close()
        except Exception:
            self.signals.finished.emit(self.request_id, QImage(), self.width, self.height)


class LeadPdfView(QScrollArea):
    """Fast PDF preview: immediate zoom feedback, then sharp in-memory rerender."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pdf_path = ""
        self._zoom = 1.0
        self._request_id = 0
        self._drag_origin = None
        self._render_cache: dict[tuple[str, int, int], QImage] = {}
        self._document = QPdfDocument(self)
        self._document.statusChanged.connect(self._document_status_changed)
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # QScrollArea 안에서 QLabel 자체를 확대하면 이전 프레임이 하단에
        # 남는 경우가 있다. PDF는 현재 배율 크기로 다시 렌더링하므로
        # QLabel의 추가 스케일링은 사용하지 않는다.
        self._label.setScaledContents(False)
        self._label.setAutoFillBackground(True)
        self.viewport().setAutoFillBackground(True)
        self._label.setStyleSheet("background: #f7faff; border: 0;")
        self.setWidget(self._label)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.viewport().setStyleSheet("background: #f7faff;")
        self.setStyleSheet(
            "QScrollArea { background: #f7faff; border: 0; }"
            "QScrollBar:vertical, QScrollBar:horizontal { background: #edf4fc; }"
        )
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(150)
        self._render_timer.timeout.connect(self._render_sharp)

    def load_pdf(self, pdf_path) -> None:
        path = str(pdf_path)
        if path != self._pdf_path:
            self._pdf_path = path
            self._zoom = 1.0
            self._request_id += 1
            self._document.close()
            self._document.load(path)
        self._schedule_render(immediate=True)

    def clear_pdf(self) -> None:
        self._request_id += 1
        self._pdf_path = ""
        self._document.close()
        self._label.clear()

    def _document_status_changed(self, status) -> None:
        if status == QPdfDocument.Status.Ready and self._pdf_path:
            self._schedule_render(immediate=True)

    def _display_size(self) -> tuple[int, int]:
        viewport_width = max(320, self.viewport().width() - 6)
        viewport_height = max(260, self.viewport().height() - 6)
        return (
            max(1, int(viewport_width * self._zoom)),
            max(1, int(viewport_height * self._zoom)),
        )

    def _schedule_render(self, *, immediate: bool = False) -> None:
        if not self._pdf_path:
            return
        width, height = self._display_size()
        self._label.resize(width, height)
        if immediate:
            self._render_timer.stop()
            self._render_sharp()
        else:
            self._render_timer.start()

    def _render_sharp(self) -> None:
        if not self._pdf_path or self._document.pageCount() < 1:
            return
        width, height = self._display_size()
        page_size = self._document.pagePointSize(0)
        if page_size.isEmpty():
            return
        scale = min(width / page_size.width(), height / page_size.height())
        target_size = (page_size * scale).toSize()
        render_width = max(1, target_size.width())
        render_height = max(1, target_size.height())
        cache_key = (self._pdf_path, render_width, render_height)
        cached = self._render_cache.get(cache_key)
        if cached is not None:
            self._apply_render(
                self._request_id,
                cached,
                render_width,
                render_height,
            )
            return
        self._request_id += 1
        request_id = self._request_id
        image = self._document.render(0, target_size)
        self._apply_render(
            request_id,
            image,
            render_width,
            render_height,
        )

    def _apply_render(self, request_id: int, image: QImage, width: int, height: int) -> None:
        if request_id != self._request_id or image.isNull() or not self._pdf_path:
            return
        cache_key = (self._pdf_path, width, height)
        self._render_cache[cache_key] = image
        while len(self._render_cache) > 4:
            self._render_cache.pop(next(iter(self._render_cache)))
        self._label.setPixmap(QPixmap.fromImage(image))
        self._label.resize(width, height)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if not delta:
            super().wheelEvent(event)
            return
        factor = 1.15 if delta > 0 else (1.0 / 1.15)
        self._zoom = max(0.5, min(6.0, self._zoom * factor))
        self._request_id += 1
        width, height = self._display_size()
        self._label.resize(width, height)
        self._schedule_render()
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin is not None:
            current = event.position().toPoint()
            delta = current - self._drag_origin
            self._drag_origin = current
            horizontal = self.horizontalScrollBar()
            vertical = self.verticalScrollBar()
            horizontal.setValue(horizontal.value() - delta.x())
            vertical.setValue(vertical.value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_origin is not None:
            self._drag_origin = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._pdf_path and self._zoom == 1.0:
            self._schedule_render()

    def reset_view(self) -> None:
        self._zoom = 1.0
        self._request_id += 1
        self.horizontalScrollBar().setValue(0)
        self.verticalScrollBar().setValue(0)
        self._schedule_render(immediate=True)


class BomStatusPage(QWidget):
    def __init__(
        self,
        service: BomExplorerService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        QTimer.singleShot(0, self._configure_saline_product_table)
        self.service = service or BomExplorerService()
        self.item_code_service = ItemCodeService()
        self.item_code_pool = QThreadPool.globalInstance()
        self._item_code_request_id = 0
        self._item_code_tasks: dict[int, ItemCodeLoadTask] = {}
        self._item_code_progress: ItemCodeProgressDialog | None = None
        self._warmup_task: BomWarmupTask | None = None
        self._warmup_again = False
        self._refresh_after_warmup = False
        self._code_selection_dirty = False
        self._loaded_code_query = ""
        self.current_code = ""
        self.selected_code = ""
        self._node_count = 0
        self._suggestions: list[dict[str, str]] = []
        self._tree_suggestion_timer = QTimer(self)
        self._tree_suggestion_timer.setSingleShot(True)
        self._tree_suggestion_timer.setInterval(140)
        self._tree_suggestion_timer.timeout.connect(
            lambda: self._update_suggestions(self.search_input.text())
        )
        self._code_suggestion_timer = QTimer(self)
        self._code_suggestion_timer.setSingleShot(True)
        self._code_suggestion_timer.setInterval(140)
        self._code_suggestion_timer.timeout.connect(
            lambda: self._update_code_suggestions(self.code_search.text())
        )
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        self.page_scroll = scroll
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.inner_tabs = QTabWidget()
        self.inner_tabs.setObjectName("bomInnerTabs")
        self.inner_tabs.tabBar().setObjectName("dashboardTabs")
        self.inner_tabs.tabBar().setExpanding(False)
        self.inner_tabs.currentChanged.connect(self._tab_changed)
        tree_tab = QWidget()
        tree_layout = QVBoxLayout(tree_tab)
        tree_layout.setContentsMargins(28, 18, 28, 28)
        tree_layout.setSpacing(14)
        toolbar = QFrame()
        toolbar.setObjectName("bomSearchBar")
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(16, 11, 16, 11)
        toolbar_layout.setSpacing(10)
        search_label = QLabel("검색")
        search_label.setObjectName("filterLabelStrong")
        self.search_mode = QComboBox()
        self.search_mode.setObjectName("bomSearchCombo")
        self.search_mode.addItem("통합 검색", "all")
        self.search_mode.addItem("품번 검색", "code")
        self.search_mode.addItem("품명 검색", "name")
        self.code_scope = QComboBox()
        self.code_scope.setObjectName("bomSearchCombo")
        for label, prefix in (
            ("전체 코드", ""),
            ("T코드", "T"),
            ("S코드", "S"),
            ("P코드", "P"),
            ("Q코드", "Q"),
            ("R코드", "R"),
            ("B·자재", "B"),
            ("A·약품", "A"),
        ):
            self.code_scope.addItem(label, prefix)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("bomSearchInput")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.setMinimumWidth(330)
        self.search_input.returnPressed.connect(self._submit_search)
        self.search_input.textEdited.connect(self._queue_tree_suggestions)
        self.completer_model = QStringListModel(self)
        self.completer = QCompleter(self.completer_model, self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.completer.setCompletionMode(QCompleter.PopupCompletion)
        self.completer.setMaxVisibleItems(12)
        self.completer.setWrapAround(False)
        self.completer.activated.connect(self._completion_selected)
        self.search_input.setCompleter(self.completer)
        self.search_mode.currentIndexChanged.connect(self._search_filter_changed)
        self.code_scope.currentIndexChanged.connect(self._search_filter_changed)
        self._update_search_placeholder()
        search_button = QPushButton("조회")
        search_button.setObjectName("smallPrimaryButton")
        search_button.setIcon(qta.icon("fa5s.search", color="white"))
        search_button.clicked.connect(self._submit_search)
        tree_reset_button = self._reset_button()
        tree_reset_button.clicked.connect(self._reset_tree_search)
        self.source_label = QLabel("품번 조회 전")
        self.source_label.setObjectName("muted")
        toolbar_layout.addWidget(search_label)
        toolbar_layout.addWidget(self.search_mode)
        toolbar_layout.addWidget(self.code_scope)
        toolbar_layout.addWidget(self.search_input)
        toolbar_layout.addWidget(search_button)
        toolbar_layout.addWidget(tree_reset_button)
        toolbar_layout.addStretch()
        toolbar_layout.addWidget(self.source_label)
        tree_layout.addWidget(toolbar)

        graph_panel = QFrame()
        graph_panel.setObjectName("card")
        graph_layout = QVBoxLayout(graph_panel)
        graph_layout.setContentsMargins(14, 10, 14, 10)
        graph_layout.setSpacing(4)
        stage_headers = QHBoxLayout()
        stage_headers.setContentsMargins(18, 2, 18, 2)
        stage_headers.setSpacing(22)
        self.stage_copy_buttons: list[QPushButton] = []
        self.stage_copy_feedback_timers: list[QTimer] = []
        for stage_index, text in enumerate(BomFlowView.STAGE_TITLES):
            stage_header = QWidget()
            stage_header_layout = QHBoxLayout(stage_header)
            stage_header_layout.setContentsMargins(0, 0, 0, 0)
            stage_header_layout.setSpacing(5)
            stage_header_layout.addStretch(1)
            label = QLabel(text)
            label.setObjectName("bomStageHeader")
            label.setAlignment(Qt.AlignCenter)
            stage_header_layout.addWidget(label)
            copy_button = QPushButton("복사")
            copy_button.setObjectName("bomStageCopyButton")
            copy_button.setIcon(qta.icon("fa5s.copy", color="#52677E"))
            copy_button.setEnabled(False)
            copy_button.setToolTip(f"활성화된 {text} 품번만 쉼표로 구분해 복사합니다.")
            copy_button.clicked.connect(
                lambda _checked=False, index=stage_index: self._copy_active_stage_codes(index)
            )
            self.stage_copy_buttons.append(copy_button)
            feedback_timer = QTimer(self)
            feedback_timer.setSingleShot(True)
            feedback_timer.setInterval(1_600)
            feedback_timer.timeout.connect(
                lambda index=stage_index: self._restore_stage_copy_button(index)
            )
            self.stage_copy_feedback_timers.append(feedback_timer)
            stage_header_layout.addWidget(copy_button)
            stage_header_layout.addStretch(1)
            stage_headers.addWidget(stage_header, 1)
        graph_layout.addLayout(stage_headers)
        self.flow_view = BomFlowView()
        self.flow_view.item_selected.connect(self._select_code)
        self.flow_view.item_requery_requested.connect(self._requery_code)
        self.flow_view.active_path_changed.connect(self._update_stage_copy_buttons)
        graph_layout.addWidget(self.flow_view)
        self.graph_note = QLabel("카드에 마우스를 올리면 품번 상세정보를 확인할 수 있습니다.")
        self.graph_note.setObjectName("bomGraphNote")
        graph_layout.addWidget(self.graph_note)
        tree_layout.addWidget(graph_panel)

        self.inner_tabs.addTab(tree_tab, "BOM 구성 현황")
        self.inner_tabs.addTab(self._build_product_tab(), "제품명 등록 검색")
        self.inner_tabs.addTab(self._build_code_tab(), "품목코드 구성 현황")
        self.inner_tabs.addTab(self._build_saline_lead_tab(), "식염수 리드지 조회")
        self.inner_tabs.addTab(self._build_change_tab(), "BOM 등록·수정 현황")
        root.addWidget(self.inner_tabs)

        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _update_stage_copy_buttons(self, stages: object) -> None:
        active_stages = stages if isinstance(stages, list) else []
        for index, button in enumerate(self.stage_copy_buttons):
            self.stage_copy_feedback_timers[index].stop()
            codes = active_stages[index] if index < len(active_stages) else []
            count = len(codes) if isinstance(codes, list) else 0
            button.setEnabled(count > 0)
            button.setText(f"복사 {count}" if count else "복사")
            button.setProperty("copied", False)
            button.style().unpolish(button)
            button.style().polish(button)

    def _restore_stage_copy_button(self, stage_index: int) -> None:
        if not 0 <= stage_index < len(self.stage_copy_buttons):
            return
        stages = self.flow_view.active_codes_by_stage()
        codes = stages[stage_index] if stage_index < len(stages) else []
        button = self.stage_copy_buttons[stage_index]
        button.setEnabled(bool(codes))
        button.setText(f"복사 {len(codes)}" if codes else "복사")
        button.setProperty("copied", False)
        button.style().unpolish(button)
        button.style().polish(button)

    def _copy_active_stage_codes(self, stage_index: int) -> None:
        stages = self.flow_view.active_codes_by_stage()
        if not 0 <= stage_index < len(stages):
            return
        codes = list(dict.fromkeys(stages[stage_index]))
        if not codes:
            return
        _set_persistent_clipboard_text(", ".join(codes))
        stage_title = BomFlowView.STAGE_TITLES[stage_index]
        button = self.stage_copy_buttons[stage_index]
        button.setText("복사됨 ✓")
        button.setProperty("copied", True)
        button.style().unpolish(button)
        button.style().polish(button)
        self.stage_copy_feedback_timers[stage_index].start()
        self.graph_note.setText(
            f"{stage_title} 활성 품번 {len(codes):,}개를 복사했습니다."
        )

    @staticmethod
    def _table(
        headers: list[str],
        table_class: type[QTableWidget] = QTableWidget,
    ) -> QTableWidget:
        table = table_class(0, len(headers))
        table.setObjectName("bomDataTable")
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setShowGrid(False)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(34)
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumHeight(560)
        return table

    @staticmethod
    def _fill_table(table: QTableWidget, rows: list[dict[str, str]], keys: list[str]) -> None:
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, key in enumerate(keys):
                value = row.get(key, "") or "-"
                if key == "use_yn":
                    value = "사용" if value == "Y" else "미사용" if value == "N" else value
                item = QTableWidgetItem(value)
                if key in {"parent_count", "child_count"}:
                    item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row_index, column_index, item)
        table.resizeColumnsToContents()
        header = table.horizontalHeader()
        for index in range(table.columnCount()):
            header.setSectionResizeMode(index, QHeaderView.ResizeToContents)
        if table.columnCount() > 1:
            header.setSectionResizeMode(1, QHeaderView.Stretch)
        if not table.property("preserveRowOrder"):
            table.setSortingEnabled(True)

    def _section_toolbar(self, title: str, placeholder: str) -> tuple[QFrame, QLineEdit, QPushButton]:
        frame = QFrame()
        frame.setObjectName("bomSectionBar")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)
        label = QLabel(title)
        label.setObjectName("filterLabelStrong")
        search = QLineEdit()
        search.setObjectName("bomSearchInput")
        search.setPlaceholderText(placeholder)
        search.setClearButtonEnabled(True)
        button = QPushButton("조회")
        button.setObjectName("smallPrimaryButton")
        button.setIcon(qta.icon("fa5s.search", color="white"))
        layout.addWidget(label)
        layout.addWidget(search, 1)
        layout.addWidget(button)
        return frame, search, button

    def _build_product_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(28, 10, 28, 28)
        layout.setSpacing(10)
        self.product_result = QLabel("각 컬럼 아래 필터행에 값을 입력하면 ERP처럼 조건을 조합해 조회합니다.")
        self.product_result.setObjectName("bomSectionNote")
        self.product_table = self._table(
            [
                "직접 상위코드", "제품명코드", "직접 하위코드", "제품명",
                "구분", "공장구분", "유효기간(년)", "DIA", "BC",
                "함수율", "분류요약",
            ]
        )
        self.product_table.setSortingEnabled(False)
        self.product_table.setColumnHidden(0, True)
        self.product_table.setColumnHidden(2, True)
        self._product_composition_expanded = False
        self._product_source_rows: list[dict[str, str]] = []
        self.product_filters: list[QWidget] = []
        self.product_filter_timer = QTimer(self)
        self.product_filter_timer.setSingleShot(True)
        self.product_filter_timer.setInterval(180)
        self.product_filter_timer.timeout.connect(self._apply_product_filters)
        self._setup_product_filters()
        self.product_table.cellDoubleClicked.connect(self._open_table_code)
        product_summary = QWidget()
        product_summary_layout = QHBoxLayout(product_summary)
        product_summary_layout.setContentsMargins(0, 0, 0, 0)
        product_summary_layout.setSpacing(8)
        product_summary_layout.addWidget(self.product_result)
        product_summary_layout.addStretch()
        self.product_composition_button = QPushButton("확장 구성")
        self.product_composition_button.setObjectName("bomCompositionButton")
        self.product_composition_button.setCheckable(True)
        self.product_composition_button.setIcon(
            qta.icon("fa5s.project-diagram", color="#52677E")
        )
        self.product_composition_button.setToolTip(
            "판매·생산·분리·사출 단계의 직접 상위·하위 품번을 표시합니다."
        )
        self.product_composition_button.toggled.connect(
            self._toggle_product_composition
        )
        product_summary_layout.addWidget(self.product_composition_button)
        product_reset_button = self._reset_button("필터 초기화")
        product_reset_button.clicked.connect(self._reset_product_filters)
        product_summary_layout.addWidget(product_reset_button)
        layout.addWidget(product_summary)
        layout.addWidget(self.product_table)
        return tab

    def _build_code_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(28, 10, 28, 28)
        layout.setSpacing(10)
        toolbar, self.code_search, button = self._section_toolbar(
            "기준 품번", "판매·생산·분리·사출 품번 입력 · 예: P0007"
        )
        self.code_search_mode = QComboBox()
        self.code_search_mode.setObjectName("bomSearchCombo")
        self.code_search_mode.addItem("통합 검색", "all")
        self.code_search_mode.addItem("품번 검색", "code")
        self.code_search_mode.addItem("품명 검색", "name")
        self.code_search_scope = QComboBox()
        self.code_search_scope.setObjectName("bomSearchCombo")
        for label, prefix in (
            ("전체 코드", ""),
            ("T코드", "T"),
            ("S코드", "S"),
            ("P코드", "P"),
            ("Q코드", "Q"),
            ("R코드", "R"),
        ):
            self.code_search_scope.addItem(label, prefix)
        toolbar.layout().insertWidget(1, self.code_search_mode)
        toolbar.layout().insertWidget(2, self.code_search_scope)
        self.code_suggestions: list[dict[str, str]] = []
        self.code_completer_model = QStringListModel(self)
        self.code_completer = QCompleter(self.code_completer_model, self)
        self.code_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.code_completer.setFilterMode(Qt.MatchContains)
        self.code_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.code_completer.setMaxVisibleItems(12)
        self.code_completer.setWrapAround(False)
        self.code_completer.activated.connect(self._code_completion_selected)
        self.code_search.setCompleter(self.code_completer)
        self.code_search.textEdited.connect(self._queue_code_suggestions)
        self.code_search_mode.currentIndexChanged.connect(self._code_search_filter_changed)
        self.code_search_scope.currentIndexChanged.connect(self._code_search_filter_changed)
        self.code_search.returnPressed.connect(self._request_code_search)
        # Use pressed instead of clicked so an open completer popup cannot
        # consume the first query action while it is committing a selection.
        button.pressed.connect(self._request_code_search)
        self._update_code_search_placeholder()
        code_reset_button = self._reset_button()
        code_reset_button.clicked.connect(self._reset_code_search)
        toolbar.layout().addWidget(code_reset_button)

        selector = QFrame()
        selector.setObjectName("bomSelectorPanel")
        selector_layout = QHBoxLayout(selector)
        selector_layout.setContentsMargins(14, 12, 14, 12)
        selector_layout.setSpacing(12)

        def selector_field(title: str, widget: QWidget) -> QWidget:
            field = QWidget()
            field_layout = QVBoxLayout(field)
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.setSpacing(5)
            label = QLabel(title)
            label.setObjectName("bomSelectorLabel")
            field_layout.addWidget(label)
            field_layout.addWidget(widget)
            return field

        self.code_sales_combo = QComboBox()
        self.code_sales_combo.setObjectName("bomSearchCombo")
        self.code_production_combo = QComboBox()
        self.code_production_combo.setObjectName("bomSearchCombo")
        self.code_separation = QLineEdit()
        self.code_separation.setObjectName("bomSearchInput")
        self.code_separation.setReadOnly(True)
        self.code_injection = QLineEdit()
        self.code_injection.setObjectName("bomSearchInput")
        self.code_injection.setReadOnly(True)
        for title, widget in (
            ("판매코드 선택", self.code_sales_combo),
            ("생산코드", self.code_production_combo),
            ("분리코드", self.code_separation),
            ("사출코드", self.code_injection),
        ):
            selector_layout.addWidget(selector_field(title, widget), 1)

        self.code_sales_combo.currentIndexChanged.connect(self._code_selection_changed)
        self.code_production_combo.currentIndexChanged.connect(self._production_code_changed)
        self.code_result = QLabel("기준 품번을 조회하면 연결된 BOM과 실제 품목코드를 구성합니다.")
        self.code_result.setObjectName("bomSectionNote")
        self.code_table = self._table([
            "도수·규격",
            "판매 품목코드 (API)",
            "생산 품목코드 (API)",
            "분리 품목코드 (API)",
            "사출 품목코드 (API)",
        ], CopyableTableWidget)
        self.code_table.setObjectName("bomCodeTable")
        self.code_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.code_table.setShowGrid(True)
        self.code_table.setGridStyle(Qt.SolidLine)
        self.code_table.verticalHeader().setDefaultSectionSize(38)
        self.code_table.setStyleSheet("""
            QTableWidget#bomCodeTable {
                gridline-color: #D7E1EC;
                selection-background-color: #DCEBFF;
                selection-color: #0F2742;
            }
            QTableWidget#bomCodeTable::item {
                padding: 6px 12px;
                border-right: 1px solid #D7E1EC;
            }
        """)
        self.code_table.setProperty("preserveRowOrder", True)
        self.code_table.excel_text_columns = {0}
        header = self.code_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        for column in range(1, 5):
            header.setSectionResizeMode(column, QHeaderView.Stretch)
        self._code_configuration: dict[str, Any] = {}
        layout.addWidget(toolbar)
        layout.addWidget(selector)
        layout.addWidget(self.code_result)
        layout.addWidget(self.code_table)
        return tab

    def _build_saline_lead_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(28, 10, 28, 28)
        layout.setSpacing(10)
        self.saline_lead_result = QLabel(
            "각 컬럼 아래 필터행에 값을 입력하면 ERP처럼 조건을 조합해 조회합니다."
        )
        self.saline_lead_result.setObjectName("bomSectionNote")

        summary = QWidget()
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(8)
        summary_layout.addWidget(self.saline_lead_result)
        summary_layout.addStretch()
        reset_button = self._reset_button("필터 초기화")
        reset_button.clicked.connect(self._reset_saline_lead_filters)
        summary_layout.addWidget(reset_button)
        layout.addWidget(summary)

        workspace = QSplitter(Qt.Horizontal)
        workspace.setObjectName("SalineLeadSplitter")
        workspace.setChildrenCollapsible(False)

        left_panel = QFrame()
        left_panel.setObjectName("SalineLeadWorkspaceCard")
        left_panel.setStyleSheet(
            "QFrame#SalineLeadWorkspaceCard { background:#FFFFFF; "
            "border:1px solid #D7E1EC; border-radius:12px; }"
        )
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(1, 1, 1, 1)
        left_layout.setSpacing(0)
        saline_headers = [
            "P코드", "제품명", "유효기간(년)", "DIA", "BC",
            "함수율", "분류요약",
        ]
        self.saline_lead_filter_table = QTableWidget(1, len(saline_headers))
        self.saline_lead_filter_table.setObjectName("SalineLeadFilterTable")
        self.saline_lead_filter_table.setHorizontalHeaderLabels(saline_headers)
        self.saline_lead_filter_table.verticalHeader().setVisible(False)
        self.saline_lead_filter_table.setRowHeight(0, 38)
        self.saline_lead_filter_table.setFixedHeight(78)
        self.saline_lead_filter_table.setShowGrid(False)
        self.saline_lead_filter_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.saline_lead_filter_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.saline_lead_filter_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.saline_lead_filter_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.saline_lead_table = self._table(saline_headers)
        self.saline_lead_table.horizontalHeader().setVisible(False)
        self.saline_lead_table.setSortingEnabled(False)
        self._saline_lead_source_rows: list[dict[str, str]] = []
        self.saline_lead_filters: list[QWidget] = []
        self.saline_lead_filter_timer = QTimer(self)
        self.saline_lead_filter_timer.setSingleShot(True)
        self.saline_lead_filter_timer.setInterval(180)
        self.saline_lead_filter_timer.timeout.connect(
            self._apply_saline_lead_filters
        )
        self._setup_saline_lead_filters()
        self.saline_lead_table.itemSelectionChanged.connect(
            self._saline_lead_selection_changed
        )
        self.saline_lead_table.cellDoubleClicked.connect(
            self._saline_lead_row_activated
        )
        self.saline_lead_filter_table.horizontalScrollBar().valueChanged.connect(
            self.saline_lead_table.horizontalScrollBar().setValue
        )
        self.saline_lead_table.horizontalScrollBar().valueChanged.connect(
            self.saline_lead_filter_table.horizontalScrollBar().setValue
        )
        left_layout.addWidget(self.saline_lead_filter_table)
        left_layout.addWidget(self.saline_lead_table)

        right_panel = QFrame()
        right_panel.setObjectName("SalineLeadRightColumn")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self.saline_lead_detail_scroll = QScrollArea()
        self.saline_lead_detail_scroll.setObjectName("SalineLeadDetailScroll")
        self.saline_lead_detail_scroll.setWidgetResizable(True)
        self.saline_lead_detail_scroll.setFrameShape(QFrame.NoFrame)
        self.saline_lead_detail_scroll.setStyleSheet(
            "QScrollArea#SalineLeadDetailScroll { background:transparent; border:none; } "
            "QScrollArea#SalineLeadDetailScroll QWidget#qt_scrollarea_viewport { "
            "background:#FFFFFF; border:none; }"
        )
        self.saline_lead_detail_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff
        )

        detail_content = QWidget()
        detail_content.setObjectName("SalineLeadDetailContent")
        detail_layout = QVBoxLayout(detail_content)
        detail_layout.setContentsMargins(14, 14, 14, 14)
        detail_layout.setSpacing(12)

        selection_summary = QFrame()
        selection_summary.setObjectName("SalineLeadSelectionSummary")
        selection_summary.setStyleSheet(
            "QFrame#SalineLeadSelectionSummary { background:#F3F8FE; "
            "border:1px solid #D9E6F3; border-radius:10px; }"
        )
        selection_summary_layout = QHBoxLayout(selection_summary)
        selection_summary_layout.setContentsMargins(14, 11, 14, 11)
        selection_summary_layout.setSpacing(11)
        selection_icon = QLabel()
        selection_icon.setObjectName("SalineLeadSelectionIcon")
        selection_icon.setAlignment(Qt.AlignCenter)
        selection_icon.setFixedSize(38, 38)
        selection_icon.setPixmap(
            qta.icon("fa5s.link", color="#0A67D1").pixmap(17, 17)
        )
        selection_text = QWidget()
        selection_text_layout = QVBoxLayout(selection_text)
        selection_text_layout.setContentsMargins(0, 0, 0, 0)
        selection_text_layout.setSpacing(2)
        selection_kicker = QLabel("선택 제품")
        selection_kicker.setObjectName("SalineLeadSelectionKicker")
        self.saline_lead_detail_heading = QLabel("P코드를 선택해 주세요")
        self.saline_lead_detail_heading.setObjectName("SalineLeadDetailHeading")
        self.saline_lead_detail_note = QLabel(
            "왼쪽 목록에서 제품을 선택하면 등록된 리드지와 식염수 정보를 표시합니다."
        )
        self.saline_lead_detail_note.setObjectName("SalineLeadDetailNote")
        self.saline_lead_detail_note.setWordWrap(True)
        self.saline_lead_detail_note.hide()
        selection_text_layout.addWidget(selection_kicker)
        selection_text_layout.addWidget(self.saline_lead_detail_heading)
        selection_text_layout.addWidget(self.saline_lead_detail_note)
        selection_summary_layout.addWidget(selection_icon)
        selection_summary_layout.addWidget(selection_text, 1)
        selection_text.setObjectName("SalineLeadSelectionText")
        selection_text.setStyleSheet(
            "QWidget#SalineLeadSelectionText, "
            "QWidget#SalineLeadSelectionText QLabel { "
            "background:transparent; border:none; }"
        )
        detail_layout.addWidget(selection_summary)

        def detail_card(
            title: str,
            icon_name: str,
            headers: list[str],
        ) -> tuple[QFrame, QLabel, QTableWidget]:
            tone = "lead" if title == "리드지 정보" else "saline"
            card = QFrame()
            card.setObjectName("SalineLeadDetailCard")
            card.setProperty("tone", tone)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 13, 14, 14)
            card_layout.setSpacing(10)
            header_layout = QHBoxLayout()
            icon = QLabel()
            icon.setObjectName("SalineLeadDetailIcon")
            icon.setProperty("tone", tone)
            icon.setAlignment(Qt.AlignCenter)
            icon.setFixedSize(34, 34)
            icon_color = "#0A67D1" if tone == "lead" else "#008A94"
            icon.setPixmap(qta.icon(icon_name, color=icon_color).pixmap(17, 17))
            title_group = QWidget()
            title_group_layout = QVBoxLayout(title_group)
            title_group_layout.setContentsMargins(0, 0, 0, 0)
            title_group_layout.setSpacing(1)
            title_label = QLabel(title)
            title_label.setObjectName("SalineLeadDetailTitle")
            subtitle_label = QLabel(
                "BOM 연결정보와 PDF 도안을 확인합니다."
                if tone == "lead"
                else "제품별 사용 등록정보를 확인합니다."
            )
            subtitle_label.setObjectName("SalineLeadDetailSubtitle")
            count_label = QLabel("선택 대기")
            count_label.setObjectName("SalineLeadDetailCount")
            count_label.setProperty("tone", tone)
            title_group_layout.addWidget(title_label)
            title_group_layout.addWidget(subtitle_label)
            header_layout.addWidget(icon)
            header_layout.addWidget(title_group, 1)
            header_layout.addWidget(count_label)
            table = self._table(headers)
            table.setObjectName("SalineLeadDetailTable")
            table.setMinimumHeight(150)
            table.setMaximumHeight(230)
            table.setSelectionMode(QAbstractItemView.NoSelection)
            table.setFocusPolicy(Qt.NoFocus)
            card_layout.addLayout(header_layout)
            card_layout.addWidget(table)
            return card, count_label, table

        lead_card, self.lead_detail_count, self.lead_detail_table = detail_card(
            "리드지 정보",
            "fa5s.layer-group",
            [
                "리드지코드", "리드지명", "규격", "BOM 소요량", "사용상태",
                "PDF 파일",
            ],
        )
        lead_card.setObjectName("SalineLeadSectionContent")
        lead_card.setStyleSheet(
            "QFrame#SalineLeadSectionContent { background:#FFFFFF; border:none; }"
        )
        self.lead_detail_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.lead_detail_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        lead_selection_palette = self.lead_detail_table.palette()
        lead_selection_palette.setColor(QPalette.Highlight, QColor("#E8F2FF"))
        lead_selection_palette.setColor(
            QPalette.HighlightedText, QColor("#075CCF")
        )
        self.lead_detail_table.setPalette(lead_selection_palette)
        self.lead_detail_table.setStyleSheet("""
            QTableWidget {
                selection-background-color: #E8F2FF;
                selection-color: #075CCF;
            }
            QTableWidget::item:selected {
                color: #075CCF;
                background: #E8F2FF;
                border-top: 1px solid #A8CCF5;
                border-bottom: 1px solid #A8CCF5;
            }
        """)
        self.lead_detail_table.itemSelectionChanged.connect(
            self._lead_detail_selection_changed
        )
        self.lead_detail_table.cellDoubleClicked.connect(self._open_lead_pdf)
        self.lead_detail_table.hide()

        selector_panel = QFrame()
        selector_panel.setObjectName("SalineLeadSelectorPanel")
        selector_layout = QHBoxLayout(selector_panel)
        selector_layout.setContentsMargins(10, 8, 10, 8)
        selector_layout.setSpacing(9)
        selector_label = QLabel("리드지 선택")
        selector_label.setObjectName("SalineLeadSelectorLabel")
        self.lead_selector_combo = QComboBox()
        self.lead_selector_combo.setObjectName("SalineLeadSelectorCombo")
        self.lead_selector_combo.addItem("등록된 리드지가 없습니다.")
        self.lead_selector_combo.setEnabled(False)
        self.lead_selector_combo.currentIndexChanged.connect(
            self._lead_selector_changed
        )
        selector_layout.addWidget(selector_label)
        selector_layout.addWidget(self.lead_selector_combo, 1)
        lead_card.layout().addWidget(selector_panel)
        selector_panel.hide()

        capture_body = QFrame()
        capture_body.setObjectName("SalineLeadCaptureBody")
        capture_body.setStyleSheet(
            "QFrame#SalineLeadCaptureBody { background:#FFFFFF; border:none; }"
        )
        capture_layout = QHBoxLayout(capture_body)
        capture_layout.setContentsMargins(0, 0, 0, 0)
        capture_layout.setSpacing(12)

        metadata_panel = QFrame()
        metadata_panel.setObjectName("SalineLeadMetadataPanel")
        metadata_panel.setStyleSheet(
            "QFrame#SalineLeadMetadataPanel { background:#FFFFFF; border:none; }"
        )
        metadata_layout = QVBoxLayout(metadata_panel)
        metadata_layout.setContentsMargins(12, 12, 12, 12)
        metadata_layout.setSpacing(8)
        metadata_heading = QLabel("연결 정보")
        metadata_heading.setObjectName("SalineLeadMetadataHeading")
        metadata_layout.addWidget(metadata_heading)
        self.lead_meta_values: dict[str, QLabel] = {}

        def metadata_field(label_text: str, key: str) -> None:
            field = QFrame()
            field.setObjectName("SalineLeadMetadataField")
            field.setStyleSheet(
                "QFrame#SalineLeadMetadataField { background:#F3F8FE; "
                "border:1px solid #DEE9F5; border-radius:8px; } "
                "QFrame#SalineLeadMetadataField QLabel { "
                "background:transparent; border:none; }"
            )
            field_layout = QVBoxLayout(field)
            field_layout.setContentsMargins(10, 7, 10, 8)
            field_layout.setSpacing(2)
            label = QLabel(label_text)
            label.setObjectName("SalineLeadMetadataLabel")
            label.setStyleSheet(
                "color:#60758A; font-size:11px; font-weight:600; "
                "background:transparent; border:none;"
            )
            value = QLabel("-")
            value.setObjectName("SalineLeadMetadataValue")
            value.setStyleSheet(
                "color:#10233F; font-size:13px; font-weight:700; "
                "background:transparent; border:none;"
            )
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.lead_meta_values[key] = value
            field_layout.addWidget(label)
            field_layout.addWidget(value)
            metadata_layout.addWidget(field)

        metadata_field("리드지코드", "code")
        metadata_field("리드지명", "name")
        metadata_field("규격", "spec")
        metadata_field("사용상태", "status")
        metadata_field("PDF 파일", "pdf_name")
        metadata_layout.addStretch()

        preview_frame = QFrame()
        preview_frame.setObjectName("SalineLeadPdfPreview")
        preview_frame.setStyleSheet(
            "QFrame#SalineLeadPdfPreview { background:#FFFFFF; border:none; }"
        )
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(10, 9, 10, 10)
        preview_layout.setSpacing(6)
        preview_header = QHBoxLayout()
        preview_title = QLabel("리드지 도안")
        preview_title.setObjectName("SalineLeadPreviewTitle")
        preview_badge = QLabel("미리보기")
        preview_badge.setObjectName("SalineLeadPreviewBadge")
        self.lead_preview_zoom_out_button = QPushButton("-")
        self.lead_preview_zoom_in_button = QPushButton("+")
        self.lead_preview_reset_button = QPushButton("기본 보기")
        self.lead_preview_save_button = QPushButton("위치 저장")
        self.lead_preview_zoom_out_button.hide()
        self.lead_preview_zoom_in_button.hide()
        self.lead_preview_reset_button.show()
        self.lead_preview_save_button.hide()
        for button in (
            self.lead_preview_zoom_out_button,
            self.lead_preview_zoom_in_button,
            self.lead_preview_reset_button,
            self.lead_preview_save_button,
        ):
            button.setObjectName("SalineLeadPreviewToolButton")
            button.setMinimumHeight(28)
            button.setStyleSheet(
                "QPushButton#SalineLeadPreviewToolButton { background:#FFFFFF; "
                "border:1px solid #C9D8E7; border-radius:7px; color:#24415F; "
                "padding:3px 8px; font-weight:600; } "
                "QPushButton#SalineLeadPreviewToolButton:hover { background:#EAF3FF; "
                "border-color:#8DB9E8; color:#0867C9; }"
            )
        self.lead_preview_zoom_out_button.setFixedWidth(30)
        self.lead_preview_zoom_in_button.setFixedWidth(30)
        self.lead_preview_zoom_out_button.setToolTip("미리보기 축소")
        self.lead_preview_zoom_in_button.setToolTip("미리보기 확대")
        self.lead_preview_reset_button.setToolTip("배율과 위치를 기본값으로 되돌립니다.")
        self.lead_preview_save_button.setToolTip("현재 배율과 위치를 이 PDF의 기본 미리보기로 저장합니다.")
        self.lead_preview_zoom_out_button.clicked.connect(
            lambda: self._change_lead_preview_zoom(1 / 1.15)
        )
        self.lead_preview_zoom_in_button.clicked.connect(
            lambda: self._change_lead_preview_zoom(1.15)
        )
        self.lead_preview_reset_button.clicked.connect(self._reset_lead_preview_view)
        self.lead_preview_save_button.clicked.connect(self._save_lead_preview_view)
        self.open_lead_pdf_button = QPushButton("원본 PDF 열기")
        self.open_lead_pdf_button.setObjectName("SalineLeadOpenPdfButton")
        self.open_lead_pdf_button.setIcon(
            qta.icon("fa5s.external-link-alt", color="#0A67D1")
        )
        self.open_lead_pdf_button.setEnabled(False)
        self.open_lead_pdf_button.clicked.connect(self._open_current_lead_pdf)
        self.register_lead_source_button = QPushButton("파일 등록")
        self.register_lead_source_button.setObjectName("SalineLeadOpenPdfButton")
        self.register_lead_source_button.setIcon(
            qta.icon("fa5s.plus", color="#0A67D1")
        )
        self.register_lead_source_button.clicked.connect(
            self._register_manual_lead_source
        )
        pdf_field_layout = self.lead_meta_values["pdf_name"].parentWidget().layout()
        pdf_action_layout = QHBoxLayout()
        pdf_action_layout.setSpacing(6)
        pdf_action_layout.addWidget(self.register_lead_source_button)
        pdf_action_layout.addWidget(self.open_lead_pdf_button)
        pdf_action_layout.addStretch()
        pdf_field_layout.addLayout(pdf_action_layout)
        self.lead_pdf_filename = QLabel("연결된 PDF 없음")
        self.lead_pdf_filename.setObjectName("SalineLeadPreviewFilename")
        self.lead_pdf_filename.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lead_pdf_filename.setWordWrap(True)
        self.lead_pdf_filename.hide()
        preview_header.addWidget(preview_title)
        preview_header.addWidget(preview_badge)
        preview_header.addStretch()
        preview_header.addWidget(self.lead_preview_zoom_out_button)
        preview_header.addWidget(self.lead_preview_zoom_in_button)
        preview_header.addWidget(self.lead_preview_reset_button)
        preview_header.addWidget(self.lead_preview_save_button)
        self.lead_pdf_preview = QLabel(
            "리드지 행에 연결된 PDF가 있으면 첫 페이지를 표시합니다."
        )
        self.lead_pdf_preview.setObjectName("SalineLeadPdfPreviewImage")
        self.lead_pdf_preview.setStyleSheet(
            "QLabel#SalineLeadPdfPreviewImage { background:#F8FBFF; "
            "border:1px solid #E1EAF4; border-radius:8px; color:#60758A; }"
        )
        self.lead_pdf_preview.setAlignment(Qt.AlignCenter)
        self.lead_pdf_preview.setWordWrap(True)
        self.lead_pdf_preview.setMinimumHeight(620)
        self.lead_pdf_preview.setMouseTracking(True)
        self.lead_pdf_preview.setCursor(Qt.OpenHandCursor)
        self._lead_preview_source_pixmap = None
        self._lead_preview_canvas_cache_key = None
        self._lead_preview_zoom = 1.0
        self._lead_preview_offset_x = 0
        self._lead_preview_offset_y = 0
        self._lead_preview_drag_anchor = None
        self._lead_preview_source_path = ""
        self._lead_preview_pdf_source = None
        self.lead_pdf_preview.installEventFilter(self)
        preview_layout.addLayout(preview_header)
        preview_layout.addWidget(self.lead_pdf_preview)
        capture_layout.addWidget(metadata_panel, 35)
        capture_layout.addWidget(preview_frame, 65)
        lead_card.setObjectName("SalineLeadInnerCard")
        lead_card.setStyleSheet(
            "QFrame#SalineLeadInnerCard { background:transparent; border:none; }"
        )
        lead_card.layout().addWidget(capture_body)
        saline_card, self.saline_detail_count, self.saline_detail_table = detail_card(
            "식염수 정보",
            "fa5s.tint",
            ["식염수코드", "식염수명", "현장코드", "공정코드", "사용상태", "수정일"],
        )
        saline_card.setObjectName("SalineLeadWorkspaceCard")
        saline_card.setStyleSheet(
            "QFrame#SalineLeadWorkspaceCard { background:#FFFFFF; "
            "border:1px solid #D7E1EC; border-radius:12px; }"
        )
        self.saline_detail_table.hide()
        self.saline_cards_scroll = QScrollArea()
        self.saline_cards_scroll.setObjectName("SalineRegistrationScroll")
        self.saline_cards_scroll.setWidgetResizable(True)
        self.saline_cards_scroll.setFrameShape(QFrame.NoFrame)
        self.saline_cards_scroll.setStyleSheet(
            "QScrollArea#SalineRegistrationScroll { background:#FFFFFF; border:none; }"
            "QScrollArea#SalineRegistrationScroll > QWidget > QWidget { "
            "background:#FFFFFF; border:none; }"
        )
        self.saline_cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.saline_cards_container = QWidget()
        self.saline_cards_container.setObjectName("SalineRegistrationContainer")
        self.saline_cards_container.setStyleSheet(
            "QWidget#SalineRegistrationContainer { background:#FFFFFF; border:none; }"
        )
        self.saline_cards_layout = QVBoxLayout(self.saline_cards_container)
        self.saline_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.saline_cards_layout.setSpacing(7)
        self.saline_cards_layout.addStretch()
        self.saline_cards_scroll.setWidget(self.saline_cards_container)
        saline_card.layout().addWidget(self.saline_cards_scroll)
        saline_card.setMinimumHeight(220)

        detail_layout.addWidget(lead_card)
        detail_layout.addStretch()
        self.saline_lead_detail_scroll.setWidget(detail_content)
        self.saline_lead_detail_scroll.setStyleSheet(
            "QScrollArea { background:#FFFFFF; border:none; }"
            "QScrollArea > QWidget > QWidget { background:#FFFFFF; border:none; }"
        )
        detail_content.setStyleSheet("background:#FFFFFF; border:none;")

        lead_outer_card = QFrame()
        lead_outer_card.setObjectName("SalineLeadWorkspaceCard")
        lead_outer_card.setStyleSheet(
            "QFrame#SalineLeadWorkspaceCard { background:#FFFFFF; "
            "border:1px solid #D7E1EC; border-radius:12px; }"
        )
        lead_outer_layout = QVBoxLayout(lead_outer_card)
        lead_outer_layout.setContentsMargins(0, 0, 0, 0)
        lead_outer_layout.setSpacing(0)
        lead_outer_layout.addWidget(self.saline_lead_detail_scroll)

        self.saline_lead_card_splitter = QSplitter(Qt.Vertical)
        self.saline_lead_card_splitter.setObjectName("SalineLeadCardSplitter")
        self.saline_lead_card_splitter.setChildrenCollapsible(False)
        self.saline_lead_card_splitter.setHandleWidth(10)
        self.saline_lead_card_splitter.addWidget(lead_outer_card)
        self.saline_lead_card_splitter.addWidget(saline_card)
        self.saline_lead_card_splitter.setStretchFactor(0, 3)
        self.saline_lead_card_splitter.setStretchFactor(1, 1)
        self.saline_lead_card_splitter.setSizes([760, 240])
        right_layout.addWidget(self.saline_lead_card_splitter, 1)

        workspace.addWidget(left_panel)
        workspace.addWidget(right_panel)
        workspace.setStretchFactor(0, 1)
        workspace.setStretchFactor(1, 1)
        workspace.setSizes([900, 900])
        layout.addWidget(workspace, 1)
        return tab

    def _build_change_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(28, 10, 28, 28)
        layout.setSpacing(12)
        self.change_result = QLabel("제품명·BOM 스냅샷을 비교해 누적 이력을 준비합니다.")
        self.change_result.setObjectName("bomSectionNote")
        history_bar = QHBoxLayout()
        history_bar.setSpacing(8)
        history_bar.addWidget(self.change_result, 1)
        history_bar.addWidget(QLabel("조회기간"))
        self.change_period = QComboBox()
        self.change_period.setObjectName("bomSearchCombo")
        self.change_period.setMinimumWidth(130)
        self.change_period.addItem("최근 7일", 7)
        self.change_period.addItem("최근 1개월", 30)
        self.change_period.addItem("최근 3개월", 90)
        self.change_period.setCurrentIndex(2)
        history_bar.addWidget(self.change_period)

        registration_panel = QFrame()
        registration_panel.setObjectName("bomSelectorPanel")
        registration_layout = QVBoxLayout(registration_panel)
        registration_layout.setContentsMargins(16, 14, 16, 16)
        registration_layout.setSpacing(8)
        registration_header = QHBoxLayout()
        registration_title = QLabel("신규등록")
        registration_title.setStyleSheet("font-size: 16px; font-weight: 800; color: #172B4D;")
        registration_description = QLabel("새로 확인된 T코드와 생산공장을 최신순으로 표시합니다.")
        registration_description.setObjectName("pageDescription")
        registration_header.addWidget(registration_title)
        registration_header.addWidget(registration_description, 1)
        registration_header.addStretch(1)
        registration_header.addWidget(QLabel("생산공장"))
        self.registration_factory = QComboBox()
        self.registration_factory.setObjectName("bomSearchCombo")
        self.registration_factory.setMinimumWidth(170)
        self.registration_factory.addItem("전체 공장", "")
        self.registration_factory.currentIndexChanged.connect(self._render_registration_rows)
        registration_header.addWidget(self.registration_factory)
        self.registration_result = QLabel("신규등록 이력을 불러오고 있습니다.")
        self.registration_result.setObjectName("bomSectionNote")
        self.registration_table = self._table([
            "등록일", "T코드", "제품명", "생산공장",
        ])
        self.registration_table.setProperty("preserveRowOrder", True)
        self.registration_table.setMinimumHeight(540)
        self.registration_table.cellDoubleClicked.connect(self._open_change_code)
        registration_layout.addLayout(registration_header)
        registration_layout.addWidget(self.registration_result)
        registration_layout.addWidget(self.registration_table)

        modification_panel = QFrame()
        modification_panel.setObjectName("bomSelectorPanel")
        modification_layout = QVBoxLayout(modification_panel)
        modification_layout.setContentsMargins(16, 14, 16, 16)
        modification_layout.setSpacing(8)
        modification_title = QLabel("수정현황")
        modification_title.setStyleSheet("font-size: 16px; font-weight: 800; color: #172B4D;")
        modification_description = QLabel(
            "T코드 생산공장 변경과 판매→생산→분리→사출→사출 하위 BOM 변경만 누적합니다."
        )
        modification_description.setObjectName("pageDescription")
        modification_header = QHBoxLayout()
        modification_header.addWidget(modification_title)
        modification_header.addWidget(modification_description, 1)
        modification_header.addStretch(1)
        modification_header.addWidget(QLabel("BOM 단계"))
        self.modification_stage = QComboBox()
        self.modification_stage.setObjectName("bomSearchCombo")
        self.modification_stage.setMinimumWidth(170)
        self.modification_stage.addItem("전체 단계", "")
        for stage in (
            "제품정보", "판매→생산", "생산→분리", "분리→사출", "사출→사출 하위",
        ):
            self.modification_stage.addItem(stage, stage)
        self.modification_stage.currentIndexChanged.connect(self._render_modification_rows)
        modification_header.addWidget(self.modification_stage)
        self.modification_result = QLabel("수정 이력을 불러오고 있습니다.")
        self.modification_result.setObjectName("bomSectionNote")
        self.change_table = self._table([
            "변경일", "변경 구분", "BOM 단계",
            "상위 품번 · 품명", "하위 품번 · 품명", "변경 내용",
        ])
        self.change_table.setProperty("preserveRowOrder", True)
        self.change_table.setMinimumHeight(540)
        self.change_table.cellDoubleClicked.connect(self._open_change_code)
        modification_layout.addLayout(modification_header)
        modification_layout.addWidget(self.modification_result)
        modification_layout.addWidget(self.change_table)

        self._all_registrations: list[dict[str, str]] = []
        self._all_modifications: list[dict[str, str]] = []
        self.change_period.currentIndexChanged.connect(self._render_change_period)
        layout.addLayout(history_bar)
        panels = QHBoxLayout()
        panels.setSpacing(12)
        panels.addWidget(registration_panel, 4)
        panels.addWidget(modification_panel, 6)
        layout.addLayout(panels, 1)
        return tab

    @staticmethod
    def _page_heading(title_text: str, description_text: str) -> QWidget:
        heading = QWidget()
        layout = QVBoxLayout(heading)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(4)
        title = QLabel(title_text)
        title.setObjectName("pageTitle")
        description = QLabel(description_text)
        description.setObjectName("pageDescription")
        description.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(description)
        return heading

    @staticmethod
    def _reset_button(text: str = "초기화") -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("bomResetButton")
        button.setIcon(qta.icon("fa5s.undo-alt", color="#52677E"))
        button.setToolTip("현재 조회 조건을 모두 초기화합니다.")
        return button

    def _tab_changed(self, index: int) -> None:
        if index == 1 and not self._product_source_rows:
            self._load_product_rows()
        elif index == 2 and self.code_table.rowCount() == 0:
            self._load_code_rows()
        elif index == 3:
            if not self._saline_lead_source_rows:
                self._load_saline_lead_rows()
        elif index == 4:
            self._load_change_rows()

    def start_warmup(
        self,
        *,
        force: bool = False,
        refresh_visible: bool = False,
    ) -> None:
        """Prepare BOM caches after the first frame without blocking the GUI."""
        if force:
            self.service.invalidate_cache()
            self._product_source_rows = []
            self._saline_lead_source_rows = []
        self._refresh_after_warmup = self._refresh_after_warmup or refresh_visible
        if self._warmup_task is not None:
            self._warmup_again = self._warmup_again or force
            return
        task = BomWarmupTask(self.service)
        task.signals.finished.connect(self._warmup_finished)
        task.signals.failed.connect(self._warmup_failed)
        self._warmup_task = task
        self.item_code_pool.start(task)

    def _warmup_finished(self, _result: dict[str, Any]) -> None:
        self._warmup_task = None
        refresh_visible = self._refresh_after_warmup
        self._refresh_after_warmup = False
        if refresh_visible:
            index = self.inner_tabs.currentIndex()
            if index == 0 and self.current_code:
                selected_code = self.selected_code
                self.load_code(self.current_code)
                if selected_code and selected_code != self.current_code:
                    self._select_code(selected_code)
            elif index == 1:
                self._load_product_rows()
            elif index == 2 and self.code_search.text().strip():
                # Refresh the local BOM selection only. The item-code API remains
                # strictly tied to the explicit Query button.
                self._load_code_rows(render_rows=False)
            elif index == 3:
                self._load_saline_lead_rows()
            elif index == 4:
                self._load_change_rows()
        if self._warmup_again:
            self._warmup_again = False
            self.start_warmup(force=True, refresh_visible=refresh_visible)

    def _warmup_failed(self, _message: str) -> None:
        self._warmup_task = None
        self._refresh_after_warmup = False
        if self._warmup_again:
            self._warmup_again = False
            self.start_warmup(force=True)

    def _load_product_rows(self) -> None:
        self._product_source_rows = self.service.product_rows("", limit=10000)
        self._apply_product_filters()

    def _setup_product_filters(self) -> None:
        self.product_table.setRowCount(1)
        self.product_table.setRowHeight(0, 38)
        placeholders = (
            "예: P0007", "예: *Rhapsody", "구분", "공장", "년", "DIA", "BC",
            "분류", "함수율",
        )
        table_columns = (1, 3, 4, 5, 6, 7, 8, 10, 9)
        choice_columns = {2, 3, 5, 6, 7, 8}
        for column, placeholder in enumerate(placeholders):
            table_column = table_columns[column]
            if column in choice_columns:
                editor = QComboBox()
                editor.setObjectName("bomColumnFilterCombo")
                editor.setEditable(False)
                editor.setMaxVisibleItems(18)
                editor.addItem("전체", "")
                editor.currentIndexChanged.connect(self._queue_product_filter)
                self.product_filters.append(editor)
                self.product_table.setCellWidget(0, table_column, editor)
                continue
            editor = QLineEdit()
            editor.setObjectName("bomColumnFilter")
            editor.setPlaceholderText(placeholder)
            editor.setClearButtonEnabled(True)
            editor.textChanged.connect(self._queue_product_filter)
            self.product_filters.append(editor)
            self.product_table.setCellWidget(0, table_column, editor)

    def _populate_product_filter_options(
        self,
        terms: list[str] | None = None,
    ) -> bool:
        filter_keys = [
            "code", "name", "kind", "factory", "validity_years", "dia", "bc",
            "classification_summary", "water_content",
        ]
        choice_keys = {
            2: "kind",
            3: "factory",
            5: "dia",
            6: "bc",
            7: "classification_summary",
            8: "water_content",
        }
        active_terms = terms or [""] * len(filter_keys)
        numeric_columns = {5, 6, 8}
        selection_changed = False
        for column, key in choice_keys.items():
            combo = self.product_filters[column]
            if not isinstance(combo, QComboBox):
                continue
            selected = str(combo.currentData() or "")
            candidate_rows = [
                row
                for row in self._product_source_rows
                if all(
                    index == column
                    or not term
                    or self._row_matches_product_filter(
                        row,
                        filter_key,
                        term,
                        index,
                    )
                    for index, (filter_key, term) in enumerate(
                        zip(filter_keys, active_terms)
                    )
                )
            ]
            values = {
                self._product_display_value(key, row.get(key, ""))
                for row in candidate_rows
                if self._product_display_value(key, row.get(key, ""))
            }
            if column in numeric_columns:
                def sort_key(value: str) -> tuple[int, float | str]:
                    try:
                        return (0, float(value))
                    except ValueError:
                        return (1, value.upper())
            elif key == "classification_summary":
                def sort_key(value: str) -> tuple[int, int, int, int, str]:
                    return _classification_sort_key(value)
            else:
                def sort_key(value: str) -> tuple[int, float | str]:
                    return (0, value.upper())
            blocker = QSignalBlocker(combo)
            combo.clear()
            combo.addItem("전체", "")
            for value in sorted(values, key=sort_key):
                combo.addItem(value, value)
            selected_index = combo.findData(selected)
            combo.setCurrentIndex(max(0, selected_index))
            selection_changed = selection_changed or (
                bool(selected) and selected_index < 0
            )
            del blocker
        return selection_changed

    @staticmethod
    def _filter_text(value: str) -> str:
        return " ".join(
            value.upper().lstrip("*").replace("_", " ").replace("-", " ").split()
        )

    def _queue_product_filter(self, _text: str = "") -> None:
        self.product_filter_timer.start()

    def _reset_product_filters(self) -> None:
        self.product_filter_timer.stop()
        blockers = [QSignalBlocker(editor) for editor in self.product_filters]
        for editor in self.product_filters:
            if isinstance(editor, QComboBox):
                editor.setCurrentIndex(0)
            elif isinstance(editor, QLineEdit):
                editor.clear()
        del blockers
        self._apply_product_filters()

    def _toggle_product_composition(self, checked: bool) -> None:
        self._product_composition_expanded = checked
        self.product_table.setColumnHidden(0, not checked)
        self.product_table.setColumnHidden(2, not checked)
        self.product_composition_button.setText(
            "확장 구성 ✓" if checked else "확장 구성"
        )
        self._apply_product_filters()

    @staticmethod
    def _compact_link_codes(codes: list[str], limit: int = 3) -> str:
        if not codes:
            return "-"
        visible = codes[:limit]
        suffix = f" · +{len(codes) - limit}" if len(codes) > limit else ""
        return " · ".join(visible) + suffix

    def _product_filter_term(self, editor: QWidget) -> str:
        if isinstance(editor, QComboBox):
            return self._filter_text(str(editor.currentData() or ""))
        if isinstance(editor, QLineEdit):
            return self._filter_text(editor.text())
        return ""

    @staticmethod
    def _product_display_value(key: str, value: Any) -> str:
        text = str(value or "").strip()
        if key == "dia" and text:
            try:
                return f"{float(text):.1f}"
            except ValueError:
                pass
        return text

    def _row_matches_product_filter(
        self,
        row: dict[str, str],
        key: str,
        term: str,
        column: int,
    ) -> bool:
        value = self._filter_text(
            self._product_display_value(key, row.get(key, ""))
        )
        if isinstance(self.product_filters[column], QComboBox):
            return value == term
        return term in value

    def _apply_product_filters(self, _text: str = "") -> None:
        keys = [
            "code", "name", "kind", "factory", "validity_years", "dia", "bc",
            "classification_summary", "water_content",
        ]
        terms = [self._product_filter_term(editor) for editor in self.product_filters]
        if self._populate_product_filter_options(terms):
            terms = [
                self._product_filter_term(editor)
                for editor in self.product_filters
            ]
            self._populate_product_filter_options(terms)
        rows = [
            row
            for row in self._product_source_rows
            if all(
                not term
                or self._row_matches_product_filter(
                    row,
                    key,
                    term,
                    column,
                )
                for column, (key, term) in enumerate(zip(keys, terms))
            )
        ]
        visible = rows[:500]
        direct_links = (
            self.service.direct_code_links([row.get("code", "") for row in visible])
            if self._product_composition_expanded
            else {}
        )
        self.product_table.setRowCount(len(visible) + 1)
        self.product_table.setRowHeight(0, 38)
        for row_index, row in enumerate(visible, start=1):
            code = row.get("code", "").upper()
            if self._product_composition_expanded:
                links = direct_links.get(code, {"parents": [], "children": []})
                for table_column, relation_key in ((0, "parents"), (2, "children")):
                    relation_codes = list(links.get(relation_key, []))
                    relation_item = QTableWidgetItem(
                        self._compact_link_codes(relation_codes)
                    )
                    relation_item.setToolTip(
                        "\n".join(relation_codes) or "직접 연결 코드 없음"
                    )
                    self.product_table.setItem(
                        row_index,
                        table_column,
                        relation_item,
                    )
            table_columns = (1, 3, 4, 5, 6, 7, 8, 10, 9)
            for table_column, key in zip(table_columns, keys):
                item = QTableWidgetItem(
                    self._product_display_value(key, row.get(key, "")) or "-"
                )
                if key in {"validity_years", "dia", "bc", "water_content"}:
                    item.setTextAlignment(Qt.AlignCenter)
                self.product_table.setItem(row_index, table_column, item)
        header = self.product_table.horizontalHeader()
        header.setStretchLastSection(False)
        for index in range(self.product_table.columnCount()):
            header.setSectionResizeMode(index, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        for index, width in {
            0: 170, 1: 100, 2: 170, 4: 72, 5: 88, 6: 94,
            7: 60, 8: 60, 9: 72, 10: 230,
        }.items():
            self.product_table.setColumnWidth(index, width)
        self.product_result.setText(
            f"필터 결과 {len(rows):,}건 · 화면 표시 {len(visible):,}건"
            + (" · 결과가 많으면 필터를 추가하세요" if len(rows) > len(visible) else "")
            + (" · 직접 상·하위 코드 표시" if self._product_composition_expanded else "")
        )

    def _load_saline_lead_rows(self) -> None:
        self._saline_lead_source_rows = (
            self.service.saline_registered_product_rows(limit=10000)
        )
        self._apply_saline_lead_filters()

    def _setup_saline_lead_filters(self) -> None:
        placeholders = (
            "예: P0007", "예: *Rhapsody", "년", "DIA", "BC", "함수율", "분류",
        )
        choice_columns = {3, 4, 5, 6}
        for column, placeholder in enumerate(placeholders):
            if column in choice_columns:
                editor = QComboBox()
                editor.setObjectName("bomColumnFilterCombo")
                editor.setEditable(False)
                editor.setMaxVisibleItems(18)
                editor.addItem("전체", "")
                editor.currentIndexChanged.connect(
                    self._queue_saline_lead_filter
                )
                self.saline_lead_filters.append(editor)
                self.saline_lead_filter_table.setCellWidget(0, column, editor)
                continue
            editor = QLineEdit()
            editor.setObjectName("bomColumnFilter")
            editor.setPlaceholderText(placeholder)
            editor.setClearButtonEnabled(True)
            editor.textChanged.connect(self._queue_saline_lead_filter)
            self.saline_lead_filters.append(editor)
            self.saline_lead_filter_table.setCellWidget(0, column, editor)

    def _populate_saline_lead_filter_options(
        self,
        terms: list[str] | None = None,
    ) -> bool:
        filter_keys = [
            "code", "name", "validity_years", "dia", "bc", "water_content",
            "classification_summary",
        ]
        choice_keys = {
            3: "dia",
            4: "bc",
            5: "water_content",
            6: "classification_summary",
        }
        active_terms = terms or [""] * len(filter_keys)
        numeric_columns = {3, 4, 5}
        selection_changed = False
        for column, key in choice_keys.items():
            combo = self.saline_lead_filters[column]
            if not isinstance(combo, QComboBox):
                continue
            selected = str(combo.currentData() or "")
            candidate_rows = [
                row
                for row in self._saline_lead_source_rows
                if all(
                    index == column
                    or not term
                    or self._row_matches_saline_lead_filter(
                        row, filter_key, term, index
                    )
                    for index, (filter_key, term) in enumerate(
                        zip(filter_keys, active_terms)
                    )
                )
            ]
            values = {
                self._product_display_value(key, row.get(key, ""))
                for row in candidate_rows
                if self._product_display_value(key, row.get(key, ""))
            }
            if column in numeric_columns:
                def sort_key(value: str) -> tuple[int, float | str]:
                    try:
                        return (0, float(value))
                    except ValueError:
                        return (1, value.upper())
            elif key == "classification_summary":
                def sort_key(value: str) -> tuple[int, int, int, int, str]:
                    return _classification_sort_key(value)
            else:
                def sort_key(value: str) -> tuple[int, float | str]:
                    return (0, value.upper())
            blocker = QSignalBlocker(combo)
            combo.clear()
            combo.addItem("전체", "")
            for value in sorted(values, key=sort_key):
                combo.addItem(value, value)
            selected_index = combo.findData(selected)
            combo.setCurrentIndex(max(0, selected_index))
            selection_changed = selection_changed or (
                bool(selected) and selected_index < 0
            )
            del blocker
        return selection_changed

    def _queue_saline_lead_filter(self, _text: str = "") -> None:
        self.saline_lead_filter_timer.start()

    def _reset_saline_lead_filters(self) -> None:
        self.saline_lead_filter_timer.stop()
        blockers = [
            QSignalBlocker(editor) for editor in self.saline_lead_filters
        ]
        for editor in self.saline_lead_filters:
            if isinstance(editor, QComboBox):
                editor.setCurrentIndex(0)
            elif isinstance(editor, QLineEdit):
                editor.clear()
        del blockers
        self.saline_lead_table.clearSelection()
        self.saline_lead_table.setCurrentCell(-1, -1)
        self._apply_saline_lead_filters()
        self._clear_saline_lead_details()
        self.saline_lead_table.verticalScrollBar().setValue(0)
        self.saline_lead_filter_table.horizontalScrollBar().setValue(0)
        self.saline_lead_detail_scroll.verticalScrollBar().setValue(0)
        self.saline_cards_scroll.verticalScrollBar().setValue(0)
        self.page_scroll.verticalScrollBar().setValue(0)

    def _saline_lead_filter_term(self, editor: QWidget) -> str:
        if isinstance(editor, QComboBox):
            return self._filter_text(str(editor.currentData() or ""))
        if isinstance(editor, QLineEdit):
            return self._filter_text(editor.text())
        return ""

    def _row_matches_saline_lead_filter(
        self,
        row: dict[str, str],
        key: str,
        term: str,
        column: int,
    ) -> bool:
        value = self._filter_text(
            self._product_display_value(key, row.get(key, ""))
        )
        if isinstance(self.saline_lead_filters[column], QComboBox):
            return value == term
        return term in value

    def _apply_saline_lead_filters(self, _text: str = "") -> None:
        keys = [
            "code", "name", "validity_years", "dia", "bc", "water_content",
            "classification_summary",
        ]
        terms = [
            self._saline_lead_filter_term(editor)
            for editor in self.saline_lead_filters
        ]
        if self._populate_saline_lead_filter_options(terms):
            terms = [
                self._saline_lead_filter_term(editor)
                for editor in self.saline_lead_filters
            ]
            self._populate_saline_lead_filter_options(terms)
        rows = [
            row
            for row in self._saline_lead_source_rows
            if all(
                not term
                or self._row_matches_saline_lead_filter(
                    row, key, term, column
                )
                for column, (key, term) in enumerate(zip(keys, terms))
            )
        ]
        visible = rows[:500]
        selected_code = self._current_saline_lead_code()
        blocker = QSignalBlocker(self.saline_lead_table)
        self.saline_lead_table.setRowCount(len(visible))
        selected_row = -1
        for row_index, row in enumerate(visible):
            code = row.get("code", "").upper()
            if code == selected_code:
                selected_row = row_index
            for table_column, key in enumerate(keys):
                item = QTableWidgetItem(
                    self._product_display_value(key, row.get(key, "")) or "-"
                )
                if key in {"validity_years", "dia", "bc", "water_content"}:
                    item.setTextAlignment(Qt.AlignCenter)
                self.saline_lead_table.setItem(row_index, table_column, item)
        for table in (self.saline_lead_filter_table, self.saline_lead_table):
            header = table.horizontalHeader()
            for index in range(table.columnCount()):
                header.setSectionResizeMode(index, QHeaderView.Interactive)
            header.setSectionResizeMode(1, QHeaderView.Stretch)
            header.setSectionResizeMode(6, QHeaderView.Stretch)
            for index, width in {
                0: 82, 2: 82, 3: 62, 4: 62, 5: 70,
            }.items():
                table.setColumnWidth(index, width)
        if selected_row >= 0:
            self.saline_lead_table.selectRow(selected_row)
        else:
            self.saline_lead_table.clearSelection()
            self.saline_lead_table.setCurrentCell(-1, -1)
        del blocker
        self.saline_lead_result.setText(
            f"P코드 필터 결과 {len(rows):,}건 · 화면 표시 {len(visible):,}건"
            + (" · 결과가 많으면 필터를 추가하세요" if len(rows) > len(visible) else "")
        )
        if selected_row >= 0:
            self._saline_lead_selection_changed()
        else:
            self._clear_saline_lead_details()

    def _current_saline_lead_code(self) -> str:
        row = self.saline_lead_table.currentRow()
        if row < 0:
            return ""
        item = self.saline_lead_table.item(row, 0)
        return item.text().strip().upper() if item else ""

    def _saline_lead_selection_changed(self) -> None:
        code = self._current_saline_lead_code()
        if not code:
            self._clear_saline_lead_details()
            return
        self._show_saline_lead_details(code)

    def _saline_lead_row_activated(self, row: int, _column: int) -> None:
        if row < 0:
            return
        item = self.saline_lead_table.item(row, 0)
        if item is not None:
            self._show_saline_lead_details(item.text())

    def _clear_saline_lead_details(self) -> None:
        self.saline_lead_detail_heading.setText("P코드를 선택해 주세요")
        self.saline_lead_detail_note.setText(
            "왼쪽 목록에서 제품을 선택하면 등록된 리드지와 식염수 정보를 표시합니다."
        )
        self.lead_detail_count.setText("선택 대기")
        self.saline_detail_count.setText("선택 대기")
        self.lead_detail_table.setRowCount(0)
        self.saline_detail_table.setRowCount(0)
        self._render_saline_cards([])
        self._clear_lead_pdf_preview()

    @staticmethod
    def _pdf_match_key(value: object) -> str:
        return "".join(
            character for character in str(value or "").upper()
            if character.isalnum()
        )

    def _match_lead_pdfs(
        self,
        leads: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        pdf_files: list[tuple[float, Path, str]] = []
        try:
            for path in LEAD_SHEET_PDF_BACKUP_DIR.rglob("*"):
                try:
                    if not path.is_file() or path.suffix.lower() != ".pdf":
                        continue
                    pdf_files.append(
                        (path.stat().st_mtime, path, self._pdf_match_key(path.stem))
                    )
                except OSError:
                    continue
        except OSError:
            pass
        pdf_files.sort(key=lambda item: item[0], reverse=True)

        matched_paths: set[str] = set()
        matched_rows: list[dict[str, Any]] = []
        for lead in leads:
            row = dict(lead)
            match: Path | None = None
            spec_key = self._pdf_match_key(row.get("spec"))
            code_key = self._pdf_match_key(row.get("code"))
            if spec_key:
                match = next(
                    (
                        path for _mtime, path, stem in pdf_files
                        if spec_key in stem and (not code_key or code_key in stem)
                    ),
                    None,
                )
            elif code_key:
                match = next(
                    (path for _mtime, path, stem in pdf_files if code_key in stem),
                    None,
                )
            row["pdf_name"] = match.name if match is not None else "PDF 없음"
            row["pdf_path"] = str(match) if match is not None else ""
            if match is not None:
                matched_paths.add(str(match))
            matched_rows.append(row)
        return matched_rows, len(matched_paths)

    def _open_lead_pdf(self, row: int, _column: int) -> None:
        item = self.lead_detail_table.item(row, 5)
        payload = item.data(Qt.UserRole) if item is not None else None
        path_text = str(payload.get("path") or "") if isinstance(payload, dict) else ""
        path = Path(path_text) if path_text else None
        if path is not None and path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _open_current_lead_pdf(self) -> None:
        path = getattr(self, "_current_lead_pdf_path", None)
        if isinstance(path, Path) and path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _lead_selector_changed(self, index: int) -> None:
        if index < 0 or index >= self.lead_detail_table.rowCount():
            return
        self.lead_detail_table.selectRow(index)
        self._lead_detail_selection_changed()

    def _clear_lead_pdf_preview(self, message: str = "연결된 PDF가 없습니다.") -> None:
        self._current_lead_pdf_path = None
        self.open_lead_pdf_button.setEnabled(False)
        self.lead_pdf_filename.setText("연결된 PDF 없음")
        self.lead_pdf_preview.clear()
        self.lead_pdf_preview.setText(message)

    def _lead_detail_selection_changed(self) -> None:
        row = self.lead_detail_table.currentRow()
        column_keys = {
            "code": 0,
            "name": 1,
            "spec": 2,
            "status": 4,
            "pdf_name": 5,
        }
        for key, column in column_keys.items():
            table_item = self.lead_detail_table.item(row, column) if row >= 0 else None
            self.lead_meta_values[key].setText(
                table_item.text() if table_item is not None else "-"
            )
        item = self.lead_detail_table.item(row, 5) if row >= 0 else None
        payload = item.data(Qt.UserRole) if item is not None else None
        if not isinstance(payload, dict) or not payload.get("path"):
            self._clear_lead_pdf_preview()
            return
        self._show_lead_pdf_preview(payload)

    def _show_lead_pdf_preview(self, payload: dict[str, str]) -> None:
        pdf_path = Path(str(payload.get("path") or ""))
        if not pdf_path.is_file():
            self._clear_lead_pdf_preview("PDF 파일을 찾을 수 없습니다.")
            return
        identity = self._pdf_match_key(
            payload.get("spec") or payload.get("code") or "LEAD"
        )
        digest = hashlib.sha1(pdf_path.name.encode("utf-8")).hexdigest()[:12]
        cache_path = (
            LEAD_SHEET_PREVIEW_CACHE_DIR
            / f"{identity}_{digest}_fixed_origin_v3.png"
        )
        pixmap = QPixmap()
        try:
            cache_is_current = (
                cache_path.is_file()
                and cache_path.stat().st_mtime >= pdf_path.stat().st_mtime
            )
            if cache_is_current:
                pixmap.load(str(cache_path))
            else:
                LEAD_SHEET_PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                document = QPdfDocument(self)
                document.load(str(pdf_path))
                if document.pageCount() > 0:
                    page_size = document.pagePointSize(0)
                    render_scale = 2.0
                    render_width = max(1, int(page_size.width() * render_scale))
                    render_height = max(1, int(page_size.height() * render_scale))
                    image = document.render(0, QSize(render_width, render_height))
                    if not image.isNull():
                        # Fixed PDF-point rectangle measured from the page's
                        # top-left. Expanding the canvas to the right/bottom no
                        # longer shifts the preview as percentage crops did.
                        crop_width = int(195.0 * render_scale)
                        crop_height = int(245.0 * render_scale)
                        crop_x = int(625.0 * render_scale)
                        crop_y = int(500.0 * render_scale)
                        crop_x = max(0, min(crop_x, image.width() - crop_width))
                        crop_y = max(0, min(crop_y, image.height() - crop_height))
                        crop_width = min(crop_width, image.width() - crop_x)
                        crop_height = min(crop_height, image.height() - crop_y)
                        preview_image = image.copy(
                            crop_x,
                            crop_y,
                            crop_width,
                            crop_height,
                        )
                        preview_image.save(str(cache_path), "PNG")
                        pixmap = QPixmap.fromImage(preview_image)
                document.close()
        except (OSError, ZeroDivisionError):
            pixmap = QPixmap()
        if pixmap.isNull():
            self._clear_lead_pdf_preview("PDF 미리보기를 만들 수 없습니다.")
            return
        target = self.lead_pdf_preview.size()
        self.lead_pdf_preview.setPixmap(
            pixmap.scaled(
                max(1, target.width() - 12),
                max(1, target.height() - 12),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )
        self.lead_pdf_filename.setText(pdf_path.name)
        self.lead_pdf_filename.setToolTip(str(pdf_path))
        self._current_lead_pdf_path = pdf_path
        self.open_lead_pdf_button.setEnabled(True)

    def _lead_preview_settings_path(self) -> Path:
        source = getattr(self, "_current_lead_pdf_path", None)
        data_root = None
        if source:
            source_path = Path(source)
            for parent in source_path.parents:
                if parent.name == "리드지 PDF 백업":
                    data_root = parent.parent
                    break
            if data_root is None:
                data_root = source_path.parent.parent
        if data_root is None:
            data_root = Path.home() / "AppData" / "Local" / "ddokddak_production3"
        settings_dir = data_root / "리드지 미리보기 캐시"
        settings_dir.mkdir(parents=True, exist_ok=True)
        return settings_dir / "preview_positions.json"

    def _lead_preview_key(self) -> str:
        source = getattr(self, "_current_lead_pdf_path", None)
        return Path(source).name.casefold() if source else ""

    def _capture_lead_preview_source(self) -> bool:
        current = self.lead_pdf_preview.pixmap()
        if current is None or current.isNull():
            return False
        current_key = int(current.cacheKey())
        if (
            self._lead_preview_source_pixmap is not None
            and self._lead_preview_canvas_cache_key == current_key
        ):
            return True
        self._lead_preview_source_pixmap = current.copy()
        self._lead_preview_canvas_cache_key = None
        return True

    def _render_lead_preview_view(self) -> None:
        if not self._capture_lead_preview_source():
            return
        from PySide6.QtGui import QColor, QPainter, QPixmap

        source = self._lead_preview_source_pixmap
        width = max(1, self.lead_pdf_preview.width())
        height = max(1, self.lead_pdf_preview.height())
        canvas = QPixmap(width, height)
        canvas.fill(QColor("#F8FBFF"))
        fit_scale = min(
            width / max(1, source.width()),
            height / max(1, source.height()),
        )
        display_scale = fit_scale * self._lead_preview_zoom
        scaled = source.scaled(
            max(1, int(source.width() * display_scale)),
            max(1, int(source.height() * display_scale)),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        x = width - scaled.width() + self._lead_preview_offset_x
        y = height - scaled.height() + self._lead_preview_offset_y
        painter = QPainter(canvas)
        painter.drawPixmap(x, y, scaled)
        painter.end()
        self.lead_pdf_preview.setPixmap(canvas)
        self._lead_preview_canvas_cache_key = int(canvas.cacheKey())

    def _change_lead_preview_zoom(self, factor: float) -> None:
        if not self._capture_lead_preview_source():
            return
        self._lead_preview_zoom = max(
            0.35,
            min(4.0, self._lead_preview_zoom * factor),
        )
        self._upgrade_lead_preview_resolution()
        self._render_lead_preview_view()

    def _upgrade_lead_preview_resolution(self) -> None:
        from PySide6.QtGui import QPixmap

        from services.lead_preview_service import fixed_pdf_preview_path

        pdf_path = getattr(self, "_lead_preview_pdf_source", None)
        if pdf_path is None:
            return
        if self._lead_preview_zoom <= 1.25:
            level = 1
        elif self._lead_preview_zoom <= 2.5:
            level = 2
        else:
            level = 4
        preview_path = fixed_pdf_preview_path(pdf_path, level)
        if preview_path is None or not preview_path.is_file():
            return
        if str(preview_path) == self._lead_preview_source_path:
            return
        pixmap = QPixmap(str(preview_path))
        if pixmap.isNull():
            return
        self._lead_preview_source_pixmap = pixmap
        self._lead_preview_source_path = str(preview_path)

    def _warm_lead_preview_pyramid(self, pdf_path) -> None:
        from concurrent.futures import ThreadPoolExecutor

        from services.lead_preview_service import ensure_fixed_pdf_preview

        self._lead_preview_pdf_source = pdf_path
        if not hasattr(self, "_lead_preview_cache_executor"):
            self._lead_preview_cache_executor = ThreadPoolExecutor(max_workers=1)
        warm_key = str(pdf_path)
        if getattr(self, "_lead_preview_warm_key", "") == warm_key:
            return
        self._lead_preview_warm_key = warm_key

        def warm() -> None:
            ensure_fixed_pdf_preview(pdf_path, 2)
            ensure_fixed_pdf_preview(pdf_path, 4)

        self._lead_preview_cache_executor.submit(warm)
        self._lead_preview_cache_poll_count = 0
        QTimer.singleShot(250, self._poll_lead_preview_pyramid)

    def _poll_lead_preview_pyramid(self) -> None:
        from services.lead_preview_service import fixed_pdf_preview_path

        pdf_path = getattr(self, "_lead_preview_pdf_source", None)
        if pdf_path is None:
            return
        self._upgrade_lead_preview_resolution()
        self._render_lead_preview_view()
        level_four_path = fixed_pdf_preview_path(pdf_path, 4)
        if level_four_path is not None and level_four_path.is_file():
            return
        self._lead_preview_cache_poll_count += 1
        if self._lead_preview_cache_poll_count < 40:
            QTimer.singleShot(250, self._poll_lead_preview_pyramid)

    def _configure_saline_product_table(self) -> None:
        for table in self.findChildren(QTableWidget):
            if table.columnCount() != 7:
                continue
            headers = [
                table.horizontalHeaderItem(index).text().strip()
                if table.horizontalHeaderItem(index) is not None
                else ""
                for index in range(7)
            ]
            if headers[:2] != ["P코드", "제품명"]:
                continue
            table.setColumnWidth(0, 82)
            table.setColumnWidth(2, 86)
            table.setColumnWidth(3, 56)
            table.setColumnWidth(4, 56)
            table.setColumnWidth(5, 66)
            table.setColumnWidth(6, 165)
            fixed_width = 82 + 86 + 56 + 56 + 66 + 165 + 8
            table.setColumnWidth(1, max(275, table.viewport().width() - fixed_width))
            if not getattr(table.viewport(), "_product_tooltip_installed", False):
                table.viewport()._product_tooltip_installed = True
                table.viewport()._product_tooltip_table = table
                table.viewport().installEventFilter(self)

    def _reset_lead_preview_view(self) -> None:
        if hasattr(self, "_lead_pdf_view") and self._lead_pdf_view.isVisible():
            self._lead_pdf_view.reset_view()
            return
        self._lead_preview_zoom = 1.0
        self._lead_preview_offset_x = 0
        self._lead_preview_offset_y = 0
        self._render_lead_preview_view()

    def _load_lead_preview_view(self) -> None:
        if not self._capture_lead_preview_source():
            return
        self._lead_preview_zoom = 1.0
        self._lead_preview_offset_x = 0
        self._lead_preview_offset_y = 0
        key = self._lead_preview_key()
        try:
            import json

            values = json.loads(
                self._lead_preview_settings_path().read_text(encoding="utf-8")
            ).get(key, {})
            self._lead_preview_zoom = max(0.35, min(4.0, float(values.get("zoom", 1.0))))
            self._lead_preview_offset_x = int(values.get("x", 0))
            self._lead_preview_offset_y = int(values.get("y", 0))
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self._render_lead_preview_view()

    def _save_lead_preview_view(self) -> None:
        key = self._lead_preview_key()
        if not key or not self._capture_lead_preview_source():
            return
        import json

        path = self._lead_preview_settings_path()
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(values, dict):
                values = {}
        except (OSError, ValueError, TypeError):
            values = {}
        values[key] = {
            "zoom": round(float(self._lead_preview_zoom), 4),
            "x": int(self._lead_preview_offset_x),
            "y": int(self._lead_preview_offset_y),
        }
        path.write_text(
            json.dumps(values, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.lead_preview_save_button.setText("저장됨")
        QTimer.singleShot(
            1200,
            lambda: self.lead_preview_save_button.setText("위치 저장"),
        )

    def eventFilter(self, watched: object, event: object) -> bool:
        from PySide6.QtCore import QEvent

        product_table = getattr(watched, "_product_tooltip_table", None)
        if product_table is not None:
            if event.type() == QEvent.Type.Resize:
                QTimer.singleShot(0, self._configure_saline_product_table)
            elif event.type() == QEvent.Type.ToolTip:
                item = product_table.itemAt(event.pos())
                if item is not None:
                    row = item.row()
                    code_item = product_table.item(row, 0)
                    name_item = product_table.item(row, 1)
                    code = code_item.text().strip() if code_item is not None else ""
                    name = name_item.text().strip() if name_item is not None else ""
                    QToolTip.showText(
                        event.globalPos(),
                        f"{code}\n{name}".strip(),
                        watched,
                    )
                    return True
        if watched is getattr(self, "lead_pdf_preview", None):
            event_type = event.type()
            if event_type == QEvent.Wheel:
                self._change_lead_preview_zoom(
                    1.15 if event.angleDelta().y() > 0 else 1 / 1.15
                )
                return True
            if event_type == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._lead_preview_drag_anchor = event.position().toPoint()
                self.lead_pdf_preview.setCursor(Qt.ClosedHandCursor)
                return True
            if (
                event_type == QEvent.MouseMove
                and self._lead_preview_drag_anchor is not None
                and event.buttons() & Qt.LeftButton
            ):
                position = event.position().toPoint()
                delta = position - self._lead_preview_drag_anchor
                self._lead_preview_drag_anchor = position
                self._lead_preview_offset_x += delta.x()
                self._lead_preview_offset_y += delta.y()
                self._render_lead_preview_view()
                return True
            if event_type == QEvent.MouseButtonRelease:
                self._lead_preview_drag_anchor = None
                self.lead_pdf_preview.setCursor(Qt.OpenHandCursor)
                return True
            if event_type == QEvent.Resize and self._lead_preview_source_pixmap is not None:
                QTimer.singleShot(0, self._render_lead_preview_view)
        return super().eventFilter(watched, event)

    def _show_vector_pdf_preview(self, preview_path) -> None:
        pdf_path = Path(preview_path)
        if not pdf_path.exists():
            return
        if not hasattr(self, "_lead_pdf_view"):
            self._lead_pdf_view = LeadPdfView(self.lead_pdf_preview.parentWidget())
            parent_layout = self.lead_pdf_preview.parentWidget().layout()
            parent_layout.replaceWidget(self.lead_pdf_preview, self._lead_pdf_view)
            self.lead_pdf_preview.hide()
        self._lead_pdf_view.load_pdf(pdf_path)
        self._lead_preview_pdf_source = pdf_path
        self._lead_preview_source_pixmap = None
        self._lead_preview_source_path = str(pdf_path)
        self._lead_pdf_view.show()
        QTimer.singleShot(0, self._lead_pdf_view.reset_view)

    def _set_auto_detected_preview(self, preview_path) -> None:
        if Path(preview_path).suffix.lower() == ".pdf":
            self._show_vector_pdf_preview(preview_path)
            return
        if hasattr(self, "_lead_pdf_view"):
            self._lead_pdf_view.clear_pdf()
            self._lead_pdf_view.hide()
            self.lead_pdf_preview.show()
        from PySide6.QtGui import QPixmap

        pixmap = QPixmap(str(preview_path))
        if pixmap.isNull():
            return
        self._lead_preview_source_pixmap = pixmap
        self._lead_preview_source_path = str(preview_path)
        self._lead_preview_zoom = 1.0
        self._lead_preview_offset_x = 0
        self._lead_preview_offset_y = 0
        self._render_lead_preview_view()
        self.lead_pdf_preview.setAlignment(Qt.AlignCenter)

    def _refresh_auto_detected_preview(self) -> None:
        from pathlib import Path

        from services.lead_preview_service import (
            ensure_cropped_pdf_preview,
            find_clone_lead_source,
            find_manual_lead_source,
            resolve_preview_folder,
        )

        pdf_value = getattr(self, "_current_lead_pdf_path", None)
        pdf_path = Path(pdf_value) if pdf_value else None
        code_label = self.lead_meta_values.get("code")
        spec_label = self.lead_meta_values.get("spec")
        lead_code = code_label.text().strip() if code_label is not None else ""
        lead_spec = spec_label.text().strip() if spec_label is not None else ""
        # A manually registered file is an intentional correction/replacement.
        # Prefer it over the synchronized clone when both match the same spec.
        manual_source = find_manual_lead_source(lead_spec, lead_code)
        clone_source = None
        if manual_source is None:
            clone_source = find_clone_lead_source(lead_spec, lead_code)

        source_pdf = clone_source
        if source_pdf is None and manual_source is not None:
            if manual_source.suffix.lower() == ".pdf":
                source_pdf = manual_source

        if source_pdf is not None:
            self._current_lead_pdf_path = source_pdf
            self.lead_pdf_filename.setText(source_pdf.name)

        image_path = None
        if source_pdf is not None:
            self._current_lead_pdf_path = source_pdf
            self.open_lead_pdf_button.setEnabled(True)
            self.lead_meta_values["pdf_name"].setText(source_pdf.name)
            image_path = ensure_cropped_pdf_preview(source_pdf)
        elif manual_source is not None:
            self._lead_preview_pdf_source = None
            self._current_lead_pdf_path = None
            self.open_lead_pdf_button.setEnabled(False)
            self.lead_meta_values["pdf_name"].setText(
                f"수동 등록 이미지\n{manual_source.name}"
            )
            image_path = manual_source
        if image_path:
            self._set_auto_detected_preview(image_path)
            self.lead_pdf_preview.setToolTip(str(image_path))
            return

        if hasattr(self, "_lead_pdf_view"):
            self._lead_pdf_view.clear_pdf()
            self._lead_pdf_view.hide()
            self.lead_pdf_preview.show()
        folder = resolve_preview_folder(pdf_path=pdf_path)
        expected_name = lead_spec or lead_code or "규격코드"
        self.lead_pdf_preview.clear()
        self.lead_pdf_preview.setText(
            "미리보기 이미지가 등록되지 않았습니다.\n\n"
            f"{expected_name}.png 파일을 추가해 주세요."
        )
        self.lead_pdf_preview.setToolTip(str(folder))
        self.lead_pdf_preview.setAlignment(Qt.AlignCenter)

    def _register_manual_lead_source(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from services.lead_preview_service import register_manual_lead_source

        code_label = self.lead_meta_values.get("code")
        spec_label = self.lead_meta_values.get("spec")
        lead_code = code_label.text().strip() if code_label is not None else ""
        lead_spec = spec_label.text().strip() if spec_label is not None else ""
        if not lead_code and not lead_spec:
            return
        source_path, _ = QFileDialog.getOpenFileName(
            self,
            "리드지 PDF 또는 이미지 등록",
            "",
            "리드지 파일 (*.pdf *.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if not source_path:
            return
        register_manual_lead_source(source_path, lead_spec, lead_code)
        self.register_lead_source_button.setText("등록 완료")
        QTimer.singleShot(
            1200,
            lambda: self.register_lead_source_button.setText("파일 등록"),
        )
        self._refresh_auto_detected_preview()

    def _start_lead_preview_worker(self, pdf_path, force: bool = False) -> None:
        from PySide6.QtCore import QThreadPool

        from services.lead_preview_service import create_lead_preview_worker

        if not hasattr(self, "_lead_preview_thread_pool"):
            self._lead_preview_thread_pool = QThreadPool(self)
            self._lead_preview_thread_pool.setMaxThreadCount(1)
            self._lead_preview_workers = []
        worker = create_lead_preview_worker(pdf_path, force=force)
        self._lead_preview_workers.append(worker)
        worker.signals.finished.connect(
            lambda source, preview, error, task=worker: self._on_lead_preview_ready(
                source,
                preview,
                error,
                task,
            )
        )
        self._lead_preview_thread_pool.start(worker)

    def _on_lead_preview_ready(self, source, preview, error, worker) -> None:
        from pathlib import Path

        workers = getattr(self, "_lead_preview_workers", [])
        if worker in workers:
            workers.remove(worker)
        current_pdf = getattr(self, "_current_lead_pdf_path", None)
        if current_pdf:
            try:
                is_current = Path(source).resolve() == Path(current_pdf).resolve()
            except OSError:
                is_current = str(source) == str(current_pdf)
            if is_current:
                if preview:
                    self._set_auto_detected_preview(preview)
                else:
                    self.lead_pdf_preview.clear()
                    self.lead_pdf_preview.setText(
                        "자동 도안 분석에 실패했습니다. 원본 PDF를 열어 확인해 주세요."
                    )
                    self.lead_pdf_preview.setAlignment(Qt.AlignCenter)
        if getattr(self, "_lead_preview_regeneration_queue", []):
            QTimer.singleShot(0, self._process_next_changed_lead_pdf)

    def _ensure_lead_preview_watcher(self, folder) -> None:
        from pathlib import Path

        from PySide6.QtCore import QFileSystemWatcher

        from services.lead_preview_service import source_signature

        folder = Path(folder)
        current_folder = getattr(self, "_lead_preview_watch_folder", None)
        if current_folder == folder and getattr(self, "_lead_preview_watcher", None):
            return

        watcher = QFileSystemWatcher(self)
        watcher.addPath(str(folder))
        pdf_paths = sorted(folder.glob("*.pdf"))
        if pdf_paths:
            watcher.addPaths([str(path) for path in pdf_paths])
        watcher.directoryChanged.connect(self._schedule_lead_preview_scan)
        watcher.fileChanged.connect(self._schedule_lead_preview_scan)
        self._lead_preview_watcher = watcher
        self._lead_preview_watch_folder = folder
        self._lead_preview_scan_pending = False
        self._lead_preview_regeneration_queue = []
        self._lead_preview_signatures = {}
        for path in pdf_paths:
            try:
                self._lead_preview_signatures[str(path)] = source_signature(path)
            except OSError:
                continue

    def _schedule_lead_preview_scan(self, *_args) -> None:
        if getattr(self, "_lead_preview_scan_pending", False):
            return
        self._lead_preview_scan_pending = True
        QTimer.singleShot(2500, self._scan_changed_lead_pdfs)

    def _scan_changed_lead_pdfs(self) -> None:
        from services.lead_preview_service import source_signature

        self._lead_preview_scan_pending = False
        folder = getattr(self, "_lead_preview_watch_folder", None)
        watcher = getattr(self, "_lead_preview_watcher", None)
        if folder is None or watcher is None:
            return

        current_paths = sorted(folder.glob("*.pdf"))
        previous = getattr(self, "_lead_preview_signatures", {})
        current = {}
        changed = []
        watched_files = set(watcher.files())
        for path in current_paths:
            try:
                signature = source_signature(path)
            except OSError:
                continue
            path_key = str(path)
            current[path_key] = signature
            if previous.get(path_key) != signature:
                changed.append(path)
            if path_key not in watched_files:
                watcher.addPath(path_key)
        self._lead_preview_signatures = current

        queued = {
            str(path)
            for path in getattr(self, "_lead_preview_regeneration_queue", [])
        }
        self._lead_preview_regeneration_queue.extend(
            path for path in changed if str(path) not in queued
        )
        if self._lead_preview_regeneration_queue:
            QTimer.singleShot(0, self._process_next_changed_lead_pdf)

    def _process_next_changed_lead_pdf(self) -> None:
        import time

        queue = getattr(self, "_lead_preview_regeneration_queue", [])
        if not queue:
            return
        pdf_path = queue.pop(0)
        try:
            modified_age_ns = time.time_ns() - pdf_path.stat().st_mtime_ns
        except OSError:
            QTimer.singleShot(0, self._process_next_changed_lead_pdf)
            return
        if modified_age_ns < 2_000_000_000:
            queue.append(pdf_path)
            QTimer.singleShot(1500, self._process_next_changed_lead_pdf)
            return

        self._start_lead_preview_worker(pdf_path, force=True)

    def _render_saline_cards(self, salines: list[dict[str, Any]]) -> None:
        while self.saline_cards_layout.count():
            item = self.saline_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not salines:
            empty_card = QFrame()
            empty_card.setObjectName("SalineRegistrationEmpty")
            empty_card.setStyleSheet(
                "QFrame#SalineRegistrationEmpty { background:#F6F9FD; "
                "border:1px dashed #C9D8E7; border-radius:9px; }"
            )
            empty_layout = QHBoxLayout(empty_card)
            empty_layout.setContentsMargins(14, 13, 14, 13)
            empty_layout.setSpacing(9)
            empty_icon = QLabel()
            empty_icon.setPixmap(qta.icon("fa5s.tint", color="#8AA0B4").pixmap(15, 15))
            empty_text = QLabel("선택한 제품에 등록된 식염수 정보가 없습니다.")
            empty_text.setObjectName("SalineRegistrationEmptyText")
            empty_layout.addWidget(empty_icon)
            empty_layout.addWidget(empty_text, 1)
            self.saline_cards_layout.addWidget(empty_card)
            self.saline_cards_layout.addStretch()
            return

        for saline in salines:
            card = QFrame()
            card.setObjectName("SalineRegistrationCard")
            card.setStyleSheet(
                "QFrame#SalineRegistrationCard { background:#F3F8FE; "
                "border:1px solid #D5E3F1; border-radius:10px; }"
            )
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(13, 10, 13, 10)
            card_layout.setSpacing(7)

            header = QHBoxLayout()
            code_label = QLabel(str(saline.get("code") or "-"))
            code_label.setObjectName("SalineRegistrationCode")
            name_label = QLabel(str(saline.get("name") or "식염수명 없음"))
            name_label.setObjectName("SalineRegistrationName")
            status = str(saline.get("status") or "-")
            status_label = QLabel(status)
            status_label.setObjectName("SalineRegistrationStatus")
            status_label.setProperty("active", status == "사용")
            header.addWidget(code_label)
            header.addWidget(name_label, 1)
            header.addWidget(status_label)
            card_layout.addLayout(header)

            meta = QHBoxLayout()
            meta.setSpacing(6)
            for label, key in (
                ("현장", "site_code"),
                ("공정", "process_code"),
                ("수정일", "updated_at"),
            ):
                value = str(saline.get(key) or "-")
                chip = QLabel(f"{label}  {value}")
                chip.setObjectName("SalineRegistrationMeta")
                meta.addWidget(chip)
            meta.addStretch()
            card_layout.addLayout(meta)
            self.saline_cards_layout.addWidget(card)
        self.saline_cards_layout.addStretch()

    def _show_saline_lead_details(self, code: str) -> None:
        details = self.service.saline_lead_details(code)
        product = details.get("product", {})
        name = str(product.get("name") or "품명 정보 없음")
        self.saline_lead_detail_heading.setText(f"{code.upper()}  {name}")
        leads, pdf_count = self._match_lead_pdfs(list(details.get("leads", [])))
        salines = list(details.get("salines", []))
        self.saline_lead_detail_note.setText(
            f"리드지 {len(leads):,}건 · PDF {pdf_count:,}건 · "
            f"식염수 {len(salines):,}건 · 사용 등록 기준"
        )
        self.lead_detail_count.setText(f"{len(leads):,}건")
        self.saline_detail_count.setText(f"{len(salines):,}건")
        lead_blocker = QSignalBlocker(self.lead_detail_table)
        self._fill_table(
            self.lead_detail_table,
            leads,
            ["code", "name", "spec", "quantity", "status", "pdf_name"],
        )
        for row_index, lead in enumerate(leads):
            pdf_item = self.lead_detail_table.item(row_index, 5)
            if pdf_item is not None:
                pdf_item.setData(
                    Qt.UserRole,
                    {
                        "path": str(lead.get("pdf_path") or ""),
                        "code": str(lead.get("code") or ""),
                        "spec": str(lead.get("spec") or ""),
                    },
                )
                if lead.get("pdf_path"):
                    pdf_item.setToolTip("더블클릭하여 PDF 열기")
        selector_blocker = QSignalBlocker(self.lead_selector_combo)
        self.lead_selector_combo.clear()
        for lead in leads:
            selector_text = " · ".join(
                value for value in (
                    str(lead.get("code") or ""),
                    str(lead.get("spec") or ""),
                    str(lead.get("name") or ""),
                ) if value
            )
            self.lead_selector_combo.addItem(selector_text or "리드지 정보", lead)
        self.lead_selector_combo.setEnabled(bool(leads))
        if not leads:
            self.lead_selector_combo.addItem("등록된 리드지가 없습니다.")
        del selector_blocker
        first_pdf_row = next(
            (
                index for index, lead in enumerate(leads)
                if lead.get("pdf_path")
            ),
            -1,
        )
        del lead_blocker
        selected_lead_row = first_pdf_row if first_pdf_row >= 0 else (0 if leads else -1)
        if selected_lead_row >= 0:
            selector_blocker = QSignalBlocker(self.lead_selector_combo)
            self.lead_selector_combo.setCurrentIndex(selected_lead_row)
            del selector_blocker
            self.lead_detail_table.selectRow(selected_lead_row)
            self._lead_detail_selection_changed()
        else:
            for value_label in self.lead_meta_values.values():
                value_label.setText("-")
            self._clear_lead_pdf_preview()
        self._fill_table(
            self.saline_detail_table,
            salines,
            ["code", "name", "site_code", "process_code", "status", "updated_at"],
        )
        self._render_saline_cards(salines)
        QTimer.singleShot(0, self._refresh_auto_detected_preview)
        self.saline_lead_detail_scroll.verticalScrollBar().setValue(0)
        self.saline_cards_scroll.verticalScrollBar().setValue(0)
        for table, stretch_column in (
            (self.lead_detail_table, 1),
            (self.saline_detail_table, 1),
        ):
            header = table.horizontalHeader()
            for index in range(table.columnCount()):
                header.setSectionResizeMode(index, QHeaderView.ResizeToContents)
            header.setSectionResizeMode(stretch_column, QHeaderView.Stretch)

    def _load_code_rows(
        self,
        _index: int = -1,
        *,
        render_rows: bool = True,
    ) -> None:
        code = self.code_search.text().strip().upper()
        if not code:
            self._clear_code_configuration("기준 품번을 입력해 주세요.")
            return
        try:
            configuration = self.service.code_configuration(code)
        except (LookupError, ValueError, sqlite3.Error) as exc:
            self._clear_code_configuration(str(exc))
            return
        self._loaded_code_query = code
        self._apply_code_configuration(configuration, render_rows=render_rows)

    def _queue_code_suggestions(self, text: str) -> None:
        if not text.strip():
            self._code_suggestion_timer.stop()
            self._update_code_suggestions(text)
            return
        self._code_suggestion_timer.start()

    def _update_code_suggestions(self, text: str) -> None:
        if not text.strip():
            self.code_suggestions = []
            self.code_completer_model.setStringList([])
            return
        self.code_suggestions = self.service.search(
            text,
            20,
            field=str(self.code_search_mode.currentData() or "all"),
            code_prefix=str(self.code_search_scope.currentData() or ""),
        )
        self.code_completer_model.setStringList([
            f"{item['code']}  ·  {item['name'] or '품명 정보 없음'}"
            for item in self.code_suggestions
        ])
        if self.code_suggestions:
            QTimer.singleShot(0, lambda value=text: self._show_code_completions(value))

    def _show_code_completions(self, typed_text: str) -> None:
        if self.code_search.text().strip() != typed_text.strip() or not self.code_suggestions:
            return
        self.code_completer.setCompletionPrefix("")
        self.code_completer.popup().setMinimumWidth(max(480, self.code_search.width()))
        self.code_completer.complete()

    def _code_search_filter_changed(self, _index: int = -1) -> None:
        self._update_code_search_placeholder()
        if self.code_search.text().strip():
            self._update_code_suggestions(self.code_search.text())

    def _update_code_search_placeholder(self) -> None:
        mode = str(self.code_search_mode.currentData() or "all")
        scope = str(self.code_search_scope.currentData() or "")
        scope_text = f"{scope}코드 내 " if scope else ""
        if mode == "code":
            hint = "품번 일부 입력 · 예: 0007, P0007"
        elif mode == "name":
            hint = "품명 일부 입력 · 예: 1-day, Rhapsody"
        else:
            hint = "품번·품명 입력 · 마스터: *0007, *rhap"
        self.code_search.setPlaceholderText(scope_text + hint)

    def _code_completion_selected(self, text: str) -> None:
        code = text.split("  ·  ", 1)[0]
        self.code_search.setText(code)
        # Selecting a suggestion prepares the BOM path from the local DB only.
        # ERP item-code API loading and the large result table remain query-only.
        self._load_code_rows(render_rows=False)

    def _request_code_search(self) -> None:
        """Commit any completion first, then run one explicit API query."""
        self.code_completer.popup().hide()
        QTimer.singleShot(0, self._submit_code_search)

    def _submit_code_search(self) -> None:
        raw = self.code_search.text().strip()
        if "  ·  " in raw:
            raw = raw.split("  ·  ", 1)[0]
            self.code_search.setText(raw)
        if (
            self._code_selection_dirty
            and raw.upper() == self._loaded_code_query.upper()
            and self._code_configuration
        ):
            self._code_selection_dirty = False
            self._render_code_full_rows()
            return
        candidates = self.service.search(
            raw,
            20,
            field=str(self.code_search_mode.currentData() or "all"),
            code_prefix=str(self.code_search_scope.currentData() or ""),
        )
        exact = next(
            (item for item in candidates if item["code"].upper() == raw.upper()),
            None,
        )
        selected = exact or (candidates[0] if candidates else None)
        if selected:
            self.code_search.setText(selected["code"])
        self._load_code_rows()

    def _apply_code_configuration(
        self,
        configuration: dict[str, Any],
        *,
        render_rows: bool,
    ) -> None:
        if "_production_context_options" not in configuration:
            configuration["_production_context_options"] = list(
                configuration.get("production_options", [])
            )
        self._code_configuration = configuration
        sales_blocker = QSignalBlocker(self.code_sales_combo)
        production_blocker = QSignalBlocker(self.code_production_combo)
        self.code_sales_combo.clear()
        for item in configuration.get("sales_options", []):
            self.code_sales_combo.addItem(
                f"{item['code']}  ·  {item.get('name', '')}", item["code"]
            )
        searched_code = str(configuration.get("searched_code", ""))
        selected_sales = self.code_sales_combo.findData(searched_code)
        if selected_sales >= 0:
            self.code_sales_combo.setCurrentIndex(selected_sales)
        self.code_production_combo.clear()
        production = configuration.get("production", {})
        for item in configuration.get("production_options", []):
            self.code_production_combo.addItem(
                f"{item['code']}  ·  {item.get('name', '')}",
                item["code"],
            )
        selected_production = self.code_production_combo.findData(production.get("code", ""))
        if selected_production >= 0:
            self.code_production_combo.setCurrentIndex(selected_production)
        separation = configuration.get("separation", {})
        injection = configuration.get("injection", {})
        self.code_separation.setText(
            f"{separation.get('code', '-')}  ·  {separation.get('name', '')}".rstrip(" ·")
        )
        self.code_injection.setText(
            f"{injection.get('code', '-')}  ·  {injection.get('name', '')}".rstrip(" ·")
        )
        del sales_blocker, production_blocker
        if render_rows:
            self._code_selection_dirty = False
            self._render_code_full_rows()
        else:
            self._code_selection_dirty = True
            self._item_code_request_id += 1
            self.code_result.setText("선택값이 변경되었습니다. 우측 조회를 누르면 아래 표에 반영됩니다.")

    def _code_selection_changed(self, _index: int = -1) -> None:
        if not self._code_configuration:
            return
        self._code_selection_dirty = True
        self._item_code_request_id += 1
        self.code_result.setText("선택값이 변경되었습니다. 우측 조회를 누르면 아래 표에 반영됩니다.")

    def _production_code_changed(self, _index: int = -1) -> None:
        production = str(self.code_production_combo.currentData() or "")
        if production and production != self._code_configuration.get("production", {}).get("code"):
            # 기준 검색어(Q/R 등)와 그 품번에서 얻은 상위 생산코드 후보는 유지한다.
            # 선택한 생산코드의 나머지 BOM/풀코드만 다시 계산해야 사용자가 다른
            # 후보를 즉시 재선택할 수 있다.
            context_options = list(
                self._code_configuration.get(
                    "_production_context_options",
                    self._code_configuration.get("production_options", []),
                )
            )
            searched_code = str(
                self._code_configuration.get("searched_code", self.code_search.text())
            )
            try:
                configuration = self.service.code_configuration(production)
            except (LookupError, ValueError, sqlite3.Error) as exc:
                self.code_result.setText(str(exc))
                return
            configuration["production_options"] = context_options
            configuration["_production_context_options"] = context_options
            configuration["searched_code"] = searched_code
            self._apply_code_configuration(configuration, render_rows=False)
            return
        self._code_selection_changed()

    def _render_code_full_rows(self, _index: int = -1) -> None:
        configuration = self._code_configuration
        if not configuration:
            return
        sales_code = str(self.code_sales_combo.currentData() or "")
        production = str(configuration.get("production", {}).get("code", ""))
        separation = str(configuration.get("separation", {}).get("code", ""))
        injection = str(configuration.get("injection", {}).get("code", ""))
        codes = list(dict.fromkeys(
            code for code in (sales_code, production, separation, injection) if code
        ))
        if not codes:
            return
        self._item_code_request_id += 1
        request_id = self._item_code_request_id
        self._show_item_code_progress(codes)
        cached, cache_is_fresh = self.item_code_service.cached_many_state(
            codes, max_age_seconds=900
        )
        if cached:
            self._update_item_code_progress("보관된 품목코드를 확인했습니다.\n표를 빠르게 구성하고 있습니다.")
            self._display_item_code_rows(
                codes,
                cached,
                "API 캐시 15분 이내" if cache_is_fresh else "로컬 캐시 표시 · API 갱신 중",
            )
        else:
            self.code_table.setRowCount(0)
            self.code_result.setText(
                f"선택 BOM  {' → '.join(codes)}  ·  ERP 품목코드 API 동시 호출 중"
            )
        if cache_is_fresh:
            self._finish_item_code_progress(
                f"품목코드 수집이 완료되었습니다.\n총 {self.code_table.rowCount():,}개 규격을 확인했습니다.",
                success=True,
            )
            return
        if request_id != self._item_code_request_id:
            return
        task = ItemCodeLoadTask(request_id, self.item_code_service, codes)
        task.signals.finished.connect(self._item_codes_loaded)
        task.signals.failed.connect(self._item_codes_failed)
        self._item_code_tasks[request_id] = task
        self.item_code_pool.start(task)

    def _selected_bom_path_text(self) -> str:
        sales = str(self.code_sales_combo.currentData() or "")
        production = str(self.code_production_combo.currentData() or "")
        separation = str(self._code_configuration.get("separation", {}).get("code", ""))
        injection = str(self._code_configuration.get("injection", {}).get("code", ""))
        direct_sales = set(self._code_configuration.get("direct_sales_codes", []))
        if sales and sales in direct_sales and separation:
            route = " → ".join(code for code in (sales, separation, injection) if code)
            return route + (f"  ·  병렬 생산코드 {production}" if production else "")
        return " → ".join(
            code for code in (sales, production, separation, injection) if code
        )

    def _item_codes_loaded(self, request_id: int, result: object) -> None:
        self._item_code_tasks.pop(request_id, None)
        if request_id != self._item_code_request_id or not isinstance(result, dict):
            return
        if result.get("cancelled"):
            return
        rows_by_code = result.get("rows_by_code", {})
        codes = list(rows_by_code.keys())
        sources = result.get("sources", {})
        source_text = " · ".join(
            f"{code}:{'API' if source == 'api' else '캐시'}"
            for code, source in sources.items()
        )
        self._update_item_code_progress("ERP API 응답을 받았습니다.\n품목코드 표를 구성하고 있습니다.")
        self._display_item_code_rows(codes, rows_by_code, source_text)
        errors = result.get("errors", {})
        if errors:
            self.code_result.setText(
                self.code_result.text() + "  ·  일부 API 실패로 캐시 사용"
            )
        completion_note = "\n일부 항목은 보관된 캐시를 사용했습니다." if errors else ""
        self._finish_item_code_progress(
            f"품목코드 수집이 완료되었습니다.\n총 {self.code_table.rowCount():,}개 규격을 확인했습니다."
            + completion_note,
            success=True,
        )

    def _item_codes_failed(self, request_id: int, message: str) -> None:
        self._item_code_tasks.pop(request_id, None)
        if request_id == self._item_code_request_id:
            self.code_result.setText(f"품목코드 API 조회 실패 · {message}")
            self._finish_item_code_progress(
                f"품목코드 수집 중 오류가 발생했습니다.\n{message}",
                success=False,
            )

    def _show_item_code_progress(self, codes: list[str]) -> None:
        if self._item_code_progress is not None:
            self._item_code_progress.deleteLater()
        dialog = ItemCodeProgressDialog(self)
        dialog.set_codes(codes)
        dialog.message.setText(
            "ERP에서 실제 품목코드를 확인하고 있습니다.\n"
            "움직이는 표시가 보이면 정상적으로 처리 중입니다."
        )
        dialog.finished.connect(self._item_code_progress_closed)
        dialog.cancel_requested.connect(self._cancel_item_code_load)
        self._item_code_progress = dialog
        dialog.open()
        QApplication.processEvents()

    def _update_item_code_progress(self, message: str) -> None:
        dialog = self._item_code_progress
        if dialog is not None:
            dialog.message.setText(message)
            QApplication.processEvents()

    def _cancel_item_code_load(self) -> None:
        request_id = self._item_code_request_id
        task = self._item_code_tasks.get(request_id)
        if task is not None:
            task.cancel()
        self._item_code_request_id += 1
        self.code_result.setText(
            "품목코드 조회를 중단했습니다. 기존 표는 그대로 유지됩니다."
        )

    def _finish_item_code_progress(self, message: str, *, success: bool) -> None:
        dialog = self._item_code_progress
        if dialog is not None:
            dialog.finish(message, success=success)
            QApplication.processEvents()
            if success:
                dialog.accept()
                self._show_item_code_toast(message.splitlines()[0])

    def _show_item_code_toast(self, message: str) -> None:
        host = self.window()
        toast = QLabel(f"✓  {message}", host)
        toast.setStyleSheet("""
            QLabel {
                background: #EAF8F1;
                color: #087A55;
                border: 1px solid #A9E2C8;
                border-radius: 10px;
                padding: 10px 16px;
                font-weight: 700;
            }
        """)
        toast.setAttribute(Qt.WA_TransparentForMouseEvents)
        toast.adjustSize()
        margin = 24
        toast.move(
            max(margin, host.width() - toast.width() - margin),
            max(margin, host.height() - toast.height() - 72),
        )
        toast.show()
        toast.raise_()
        QTimer.singleShot(3000, toast.deleteLater)

    def _item_code_progress_closed(self, _result: int) -> None:
        dialog = self._item_code_progress
        self._item_code_progress = None
        if dialog is not None:
            dialog.deleteLater()

    @staticmethod
    def _item_spec_key(row: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
        def rounded(value: object) -> float | None:
            try:
                return round(float(value), 4) if value is not None else None
            except (TypeError, ValueError):
                return None
        axis = row.get("axis_value")
        try:
            axis_value = int(axis) if axis is not None else None
        except (TypeError, ValueError):
            axis_value = None
        return (
            rounded(row.get("power_value")),
            rounded(row.get("cp_value")),
            axis_value,
            rounded(row.get("add_value")),
        )

    @staticmethod
    def _item_spec_sort_key(key: tuple[Any, Any, Any, Any]) -> tuple[Any, ...]:
        power, cp, axis, add = key
        cp_order = {-0.75: 0, -1.25: 1, -1.75: 2, -2.25: 3, -2.75: 4}
        add_order = {1.00: 0, 1.50: 1, 2.00: 2, 2.25: 3, 2.50: 4}
        return (
            1 if power is None else 0,
            -(power or 0.0),
            cp_order.get(cp, 90 + abs(cp or 0.0)),
            axis if axis is not None else 999,
            add_order.get(add, 90 + (add or 0.0)),
        )

    @staticmethod
    def _item_spec_label(
        key: tuple[Any, Any, Any, Any],
        *,
        is_toric: bool,
        is_multi: bool,
    ) -> str:
        power, cp, axis, add = key
        if power is None:
            power_text = "-"
        elif abs(power) < 0.0001:
            power_text = "-00.00"
        else:
            power_text = f"{power:+06.2f}"
        parts = [power_text]
        if is_toric:
            parts.append(f"{cp:.2f}" if cp is not None else "-")
            parts.append(f"{axis:03d}" if axis is not None else "-")
        elif is_multi:
            parts.append(f"{add:.2f}" if add is not None else "-")
        return " / ".join(parts)

    def _display_item_code_rows(
        self,
        codes: list[str],
        rows_by_code: dict[str, list[dict[str, Any]]],
        source_text: str,
    ) -> None:
        grouped: dict[tuple[Any, Any, Any, Any], dict[str, list[str]]] = {}
        is_toric = False
        is_multi = False
        for code in codes:
            for item in rows_by_code.get(code, []):
                key = self._item_spec_key(item)
                is_toric = is_toric or key[1] is not None or key[2] is not None
                is_multi = is_multi or key[3] is not None
                grouped.setdefault(key, {}).setdefault(code, []).append(
                    str(item.get("gd_cd", ""))
                )
        sales = str(self.code_sales_combo.currentData() or "")
        production = str(self.code_production_combo.currentData() or "")
        separation = str(self._code_configuration.get("separation", {}).get("code", ""))
        injection = str(self._code_configuration.get("injection", {}).get("code", ""))
        display_rows: list[dict[str, str]] = []
        for key in sorted(grouped, key=self._item_spec_sort_key):
            values = grouped[key]
            cell = lambda code: " / ".join(sorted(set(values.get(code, [])))) or "-"
            display_rows.append({
                "spec": self._item_spec_label(
                    key, is_toric=is_toric, is_multi=is_multi and not is_toric
                ),
                "sales_full": cell(sales),
                "production_full": cell(production),
                "separation_full": cell(separation),
                "injection_full": cell(injection),
            })
        self.code_table.setUpdatesEnabled(False)
        try:
            self._fill_table(
                self.code_table,
                display_rows,
                ["spec", "sales_full", "production_full", "separation_full", "injection_full"],
            )
            spec_header = "PW / CP / AXIS" if is_toric else "PW / ADD" if is_multi else "PW"
            self.code_table.horizontalHeaderItem(0).setText(spec_header)
            for row_index in range(self.code_table.rowCount()):
                for column_index in range(self.code_table.columnCount()):
                    item = self.code_table.item(row_index, column_index)
                    if item is not None:
                        item.setTextAlignment(Qt.AlignCenter)
            self.code_table.setSortingEnabled(False)
            header = self.code_table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            for column in range(1, 5):
                header.setSectionResizeMode(column, QHeaderView.Stretch)
        finally:
            self.code_table.setUpdatesEnabled(True)
            self.code_table.viewport().update()
        self.code_result.setText(
            f"선택 BOM  {self._selected_bom_path_text() or '-'}"
            f"  ·  ERP API 실제 품목코드 규격 {len(display_rows):,}건"
            + (f"  ·  {source_text}" if source_text else "")
            + "  ·  Ctrl+A → Ctrl+C: 컬럼명 포함 Excel 복사"
        )

    def _clear_code_configuration(self, message: str) -> None:
        self._item_code_request_id += 1
        self._code_selection_dirty = False
        self._loaded_code_query = ""
        self._code_configuration = {}
        for combo in (self.code_sales_combo, self.code_production_combo):
            blocker = QSignalBlocker(combo)
            combo.clear()
            del blocker
        self.code_separation.clear()
        self.code_injection.clear()
        self.code_table.setRowCount(0)
        self.code_table.horizontalHeaderItem(0).setText("PW / 규격")
        self.code_result.setText(message)

    def _reset_tree_search(self) -> None:
        blockers = [
            QSignalBlocker(self.search_mode),
            QSignalBlocker(self.code_scope),
            QSignalBlocker(self.search_input),
        ]
        self.search_mode.setCurrentIndex(0)
        self.code_scope.setCurrentIndex(0)
        self.search_input.clear()
        self.completer_model.setStringList([])
        self._update_search_placeholder()
        del blockers
        self.current_code = ""
        self.selected_code = ""
        self._node_count = 0
        self.flow_view.set_hierarchy({})
        self.graph_note.setObjectName("bomGraphNote")
        self.graph_note.setText("품번을 검색하면 연결된 BOM 상세정보를 확인할 수 있습니다.")
        self.source_label.setText("품번 조회 전")
        self.graph_note.style().unpolish(self.graph_note)
        self.graph_note.style().polish(self.graph_note)

    def _reset_code_search(self) -> None:
        blockers = [
            QSignalBlocker(self.code_search_mode),
            QSignalBlocker(self.code_search_scope),
        ]
        self.code_search.clear()
        self.code_search_mode.setCurrentIndex(0)
        self.code_search_scope.setCurrentIndex(0)
        self.code_suggestions = []
        self.code_completer_model.setStringList([])
        self._update_code_search_placeholder()
        del blockers
        self._clear_code_configuration("기준 품번을 조회하면 연결된 BOM과 실제 품목코드를 구성합니다.")

    def _load_change_rows(self) -> None:
        overview = self.service.bom_change_overview(limit=5000)
        self._all_registrations = list(overview.get("registrations", []))
        self._all_modifications = list(overview.get("modifications", []))
        self.change_result.setText(
            f"최근 비교 기준 {overview['baseline']} · 최근 {overview.get('retention_days', 90)}일 자동 보관"
        )
        selected_factory = str(self.registration_factory.currentData() or "")
        factories = sorted(
            {row.get("factory", "") or "미등록" for row in self._all_registrations},
            key=lambda value: (
                {"A관(1공장)": 0, "C관(2공장)": 1, "L관": 2, "S관(3공장)": 3, "미등록": 9}.get(value, 8),
                value,
            ),
        )
        blocker = QSignalBlocker(self.registration_factory)
        self.registration_factory.clear()
        self.registration_factory.addItem("전체 공장", "")
        for factory in factories:
            self.registration_factory.addItem(factory, factory)
        selected_index = self.registration_factory.findData(selected_factory)
        self.registration_factory.setCurrentIndex(max(0, selected_index))
        del blocker
        self._render_registration_rows()
        self._render_modification_rows()
        registration_header = self.registration_table.horizontalHeader()
        registration_header.setStretchLastSection(False)
        registration_header.setSectionResizeMode(0, QHeaderView.Fixed)
        registration_header.setSectionResizeMode(1, QHeaderView.Fixed)
        registration_header.setSectionResizeMode(2, QHeaderView.Stretch)
        registration_header.setSectionResizeMode(3, QHeaderView.Fixed)
        self.registration_table.setColumnWidth(0, 62)
        self.registration_table.setColumnWidth(1, 68)
        self.registration_table.setColumnWidth(3, 90)

    def _render_registration_rows(self, _index: int = -1) -> None:
        factory = str(self.registration_factory.currentData() or "")
        rows = [
            row for row in self._rows_in_change_period(self._all_registrations)
            if not factory or (row.get("factory", "") or "미등록") == factory
        ]
        display_rows = [
            {**row, "detected_date": self._change_date_label(row.get("detected_at", ""))}
            for row in rows
        ]
        self._fill_table(
            self.registration_table,
            display_rows,
            ["detected_date", "code", "product_name", "factory"],
        )
        for row_index, row in enumerate(rows):
            tooltip = self._change_tooltip(
                ("T코드", row.get("code", "")),
                ("제품명", row.get("product_name", "")),
                ("생산공장", row.get("factory", "")),
                ("등록일", self._change_date_label(row.get("detected_at", ""))),
            )
            for column in (1, 2, 3):
                item = self.registration_table.item(row_index, column)
                if item is not None:
                    item.setToolTip(tooltip)
        header = self.registration_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        self.registration_table.setColumnWidth(0, 62)
        self.registration_table.setColumnWidth(1, 68)
        self.registration_table.setColumnWidth(3, 90)
        filter_text = factory or "전체 공장"
        self.registration_result.setText(
            f"{self.change_period.currentText()} · {filter_text} · 신규 T코드 {len(rows):,}건 · 최신 감지순"
        )

    def _render_modification_rows(self, _index: int = -1) -> None:
        selected_stage = str(self.modification_stage.currentData() or "")
        rows = [
            row for row in self._rows_in_change_period(self._all_modifications)
            if not selected_stage or row.get("stage", "") == selected_stage
        ]
        display_rows = [
            {**row, "detected_date": self._change_date_label(row.get("detected_at", ""))}
            for row in rows
        ]
        self._fill_table(
            self.change_table,
            display_rows,
            [
                "detected_date", "change_type", "stage", "parent_display",
                "target_display", "change_comment",
            ],
        )
        for row_index, row in enumerate(rows):
            tooltip = self._change_tooltip(
                ("변경구분", row.get("change_type", "")),
                ("BOM 단계", row.get("stage", "")),
                ("상위 품번 · 품명", row.get("parent_display", "")),
                ("하위 품번 · 품명", row.get("target_display", "")),
                ("변경 내용", row.get("change_comment", "")),
                ("변경일", self._change_date_label(row.get("detected_at", ""))),
            )
            for column in (3, 4, 5):
                item = self.change_table.item(row_index, column)
                if item is not None:
                    item.setToolTip(tooltip)
        header = self.change_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        self.change_table.setColumnWidth(0, 62)
        self.change_table.setColumnWidth(1, 104)
        self.change_table.setColumnWidth(2, 108)
        self.change_table.setColumnWidth(5, 164)
        filter_text = selected_stage or "전체 단계"
        self.modification_result.setText(
            f"{self.change_period.currentText()} · {filter_text} · 누적 수정 {len(rows):,}건 · 최신 감지순"
        )

    def _render_change_period(self, _index: int = -1) -> None:
        self._render_registration_rows()
        self._render_modification_rows()

    def _rows_in_change_period(
        self,
        rows: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        days = int(self.change_period.currentData() or 90)
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        return [row for row in rows if str(row.get("detected_at", "")) >= cutoff]

    @staticmethod
    def _change_date_label(value: Any) -> str:
        try:
            parsed = datetime.fromisoformat(str(value).replace("T", " ")[:19])
            return f"{parsed.month}/{parsed.day}"
        except ValueError:
            return str(value or "-")[:10]

    @staticmethod
    def _change_tooltip(*sections: tuple[str, Any]) -> str:
        lines: list[str] = []
        for label, raw_value in sections:
            value = str(raw_value or "-").strip() or "-"
            if lines:
                lines.append("")
            lines.append(label)
            lines.extend(
                textwrap.wrap(
                    value,
                    width=52,
                    break_long_words=True,
                    break_on_hyphens=False,
                ) or ["-"]
            )
        return "\n".join(lines)

    def _open_table_code(self, row: int, _column: int) -> None:
        table = self.sender()
        if not isinstance(table, QTableWidget):
            return
        code_column = 1 if table is self.product_table else 0
        if table.item(row, code_column) is None:
            return
        self.inner_tabs.setCurrentIndex(0)
        self.load_code(table.item(row, code_column).text())

    def _open_change_code(self, row: int, _column: int) -> None:
        table = self.sender()
        if not isinstance(table, QTableWidget):
            return
        code_column = 1 if table is self.registration_table else 3
        if table.item(row, code_column) is None:
            return
        self.inner_tabs.setCurrentIndex(0)
        code = table.item(row, code_column).text().split(" · ", 1)[0].strip()
        self.load_code(code)

    def _queue_tree_suggestions(self, text: str) -> None:
        if not text.strip():
            self._tree_suggestion_timer.stop()
            self._update_suggestions(text)
            return
        self._tree_suggestion_timer.start()

    def _update_suggestions(self, text: str) -> None:
        if len(text.strip()) < 1:
            self._suggestions = []
            self.completer_model.setStringList([])
            return
        self._suggestions = self.service.search(
            text,
            20,
            field=str(self.search_mode.currentData() or "all"),
            code_prefix=str(self.code_scope.currentData() or ""),
        )
        self.completer_model.setStringList(
            [
                f"{item['code']}  ·  {item['name'] or '품명 정보 없음'}"
                for item in self._suggestions
            ]
        )
        if self._suggestions:
            # The service has already applied search mode, code scope, wildcard,
            # and separator normalization. Queue an empty QCompleter prefix so it
            # does not hide valid results such as '1-day 58' -> '1-Day_58'.
            QTimer.singleShot(0, lambda value=text: self._show_search_completions(value))

    def _show_search_completions(self, typed_text: str) -> None:
        if self.search_input.text().strip() != typed_text.strip() or not self._suggestions:
            return
        self.completer.setCompletionPrefix("")
        self.completer.popup().setMinimumWidth(max(480, self.search_input.width()))
        self.completer.complete()

    def _search_filter_changed(self, _index: int = -1) -> None:
        self._update_search_placeholder()
        if self.search_input.text().strip():
            self._update_suggestions(self.search_input.text())

    def _update_search_placeholder(self) -> None:
        mode = str(self.search_mode.currentData() or "all")
        scope = str(self.code_scope.currentData() or "")
        scope_text = f"{scope}코드 내 " if scope else ""
        if mode == "code":
            hint = "품번 일부 입력 · 예: 5423, P5423"
        elif mode == "name":
            hint = "품명 일부 입력 · 예: 1-day 58, Rhapsody"
        else:
            hint = "품번·품명 입력 · 마스터: *54, *rhap"
        self.search_input.setPlaceholderText(scope_text + hint)

    def _completion_selected(self, text: str) -> None:
        self.load_code(text.split("  ·  ", 1)[0])

    def _submit_search(self) -> None:
        raw = self.search_input.text().strip()
        if "  ·  " in raw:
            self.load_code(raw.split("  ·  ", 1)[0])
            return
        candidates = self.service.search(
            raw,
            20,
            field=str(self.search_mode.currentData() or "all"),
            code_prefix=str(self.code_scope.currentData() or ""),
        )
        exact = next(
            (item for item in candidates if item["code"].upper() == raw.upper()),
            None,
        )
        selected = exact or (candidates[0] if candidates else None)
        self.load_code(selected["code"] if selected else raw)

    def load_code(self, code: str) -> None:
        try:
            graph = self.service.graph(code)
        except (ValueError, LookupError, FileNotFoundError, OSError) as exc:
            self.graph_note.setText(str(exc))
            self.graph_note.setObjectName("bomGraphError")
            self.graph_note.style().unpolish(self.graph_note)
            self.graph_note.style().polish(self.graph_note)
            return

        self.current_code = graph["code"]
        self.selected_code = graph["code"]
        self.search_input.setText(self.current_code)
        self.graph_note.setObjectName("bomGraphNote")
        hierarchy = graph["hierarchy"]
        self._node_count = sum(len(column) for column in hierarchy["columns"])
        self.graph_note.setText(
            f"검색 기준  {self.current_code}  ·  상세 선택  {self.selected_code}  ·  연결 품번 {self._node_count:,}개"
        )
        self.graph_note.style().unpolish(self.graph_note)
        self.graph_note.style().polish(self.graph_note)
        self.flow_view.set_hierarchy(hierarchy)
        stamp = graph["source_refreshed_at"] or graph["local_refreshed_at"] or "확인 불가"
        self.source_label.setText(f"데이터 기준일시  {stamp}")

    def _select_code(self, code: str) -> None:
        if code not in self.flow_view.node_items:
            return
        self.selected_code = code
        self.flow_view.set_selected(self.selected_code)
        self.graph_note.setText(
            f"검색 기준  {self.current_code}  ·  상세 선택  {self.selected_code}  ·  연결 품번 {self._node_count:,}개"
        )

    def _requery_code(self, code: str) -> None:
        """Use the context-menu card as the new BOM search root."""
        self.load_code(code)

    def refresh(self) -> None:
        """Rebuild local caches after the product-reference collector finishes."""
        self.start_warmup(force=True, refresh_visible=True)
