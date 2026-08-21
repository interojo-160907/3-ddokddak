from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable

import qtawesome as qta
from PySide6.QtCore import (
    QDate,
    QEvent,
    QObject,
    QPointF,
    QProcess,
    QRect,
    QRectF,
    QSize,
    QSignalBlocker,
    QStringListModel,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QCompleter,
    QDateEdit,
    QFrame,
    QGraphicsObject,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QComboBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from config import APP_DISPLAY_NAME, APP_NAME, APP_VERSION, ASSET_DIR, DATA_CENTER_DIR, DEFAULT_FACTORY, LEAD_SHEET_PDF_BACKUP_DIR, ROOT_DIR


def _collector_process_command(script_name: str, *arguments: str) -> tuple[str, list[str]]:
    if getattr(sys, "frozen", False):
        return sys.executable, ["--collector", Path(script_name).stem, *arguments]
    return sys.executable, [
        str(ROOT_DIR / "collectors" / script_name),
        *arguments,
    ]


def _hide_windows_process_window(arguments) -> None:
    arguments.flags |= 0x08000000  # CREATE_NO_WINDOW
    try:
        arguments.startupInfo.dwFlags |= 0x00000001  # STARTF_USESHOWWINDOW
        arguments.startupInfo.wShowWindow = 0  # SW_HIDE
    except AttributeError:
        pass


def _background_process(parent: QWidget) -> QProcess:
    process = QProcess(parent)
    process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
    if sys.platform == "win32":
        process.setCreateProcessArgumentsModifier(_hide_windows_process_window)
    return process


from services.bom_explorer import BomExplorerService
from services.collection_schedule import load_schedule, save_schedule
from services.dashboard_service import DashboardService
from services.process_status_service import ProcessStatusService, business_sort_key, classification_sort_key
from services.program_gate import DEFAULT_UPDATE_URL, ProgramGate
from ui.bom_page import BomStatusPage
from ui.message_dialog import ask_app_confirmation, show_app_message
from ui.notice_ticker import NoticeTicker
from ui.permission_dialog import show_permission_denied
from ui.process_overview_page import ProcessOverviewPage
from ui.update_flow_dialog import show_required_update


@dataclass(frozen=True)
class PageDefinition:
    key: str
    title: str
    description: str
    kicker: str


PAGES = (
    PageDefinition(
        "dashboard",
        "대시보드",
        "당월·전월 실적과 생산 필요수량, 납기 리스크를 한 화면에서 확인합니다.",
        "생산3팀 납기 현황",
    ),
    PageDefinition(
        "process_overview",
        "공정 현황",
        "국내·해외 살아있는 수주의 APS 공정별 잔여와 실제 포장실적을 확인합니다.",
        "수주 흐름  ·  생산·포장 연결",
    ),
    PageDefinition("injection", "사출 공정", "사출 공정의 생산계획과 최근 실적, 납기 위험을 확인합니다.", "공정 현황"),
    PageDefinition("separation", "분리 공정", "분리 공정의 생산계획과 최근 실적, 납기 위험을 확인합니다.", "공정 현황"),
    PageDefinition("hydration", "하이드레이션 공정", "하이드레이션 공정의 생산계획과 최근 실적, 납기 위험을 확인합니다.", "공정 현황"),
    PageDefinition("inspection", "검사·접착 공정", "검사·접착 공정의 생산계획과 최근 실적, 납기 위험을 확인합니다.", "공정 현황"),
    PageDefinition("leak", "누수·규격 공정", "누수·규격 공정의 생산계획과 최근 실적, 납기 위험을 확인합니다.", "공정 현황"),
    PageDefinition("bom", "BOM 현황", "판매코드부터 생산·분리·사출·하위자재까지 BOM 연결관계를 확인합니다.", "제품 구성정보"),
    PageDefinition("settings", "설정 및 운영", "데이터 저장 위치와 갱신 상태, 프로그램 업데이트 정보를 관리합니다.", "프로그램 관리"),
)


class BrandHero(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(154)
        self.setMaximumHeight(154)
        self.pixmap = QPixmap(str(ASSET_DIR / "ddokddak_mascot.png"))

        self.badge_label = QLabel("똑딱이", self)
        self.badge_label.setObjectName("BrandBadge")
        self.badge_label.setAlignment(Qt.AlignCenter)
        self.title_label = QLabel("생산3팀\n납기 통합조회", self)
        self.title_label.setObjectName("BrandTitle")
        self.subtitle_label = QLabel("Production · Delivery · BOM", self)
        self.subtitle_label.setObjectName("BrandSubtitle")

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.badge_label.setGeometry(14, 14, 58, 25)
        self.title_label.setGeometry(14, 47, 134, 55)
        self.subtitle_label.setGeometry(14, self.height() - 35, self.width() - 28, 20)
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0, 0, -1, -1)

        bg = QLinearGradient(rect.topLeft(), rect.bottomRight())
        bg.setColorAt(0.0, QColor("#F8FBFF"))
        bg.setColorAt(0.55, QColor("#E7F2FF"))
        bg.setColorAt(1.0, QColor("#EAF8E1"))
        painter.setPen(QPen(QColor("#D7E7F6"), 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 18, 18)

        clip_path = QPainterPath()
        clip_path.addRoundedRect(rect, 18, 18)
        painter.setClipPath(clip_path)
        if not self.pixmap.isNull():
            image_size = min(rect.height() * 1.43, rect.width() * 0.82)
            image_rect = QRectF(rect.right() - image_size + 18, rect.top() - 4, image_size, image_size)
            painter.setOpacity(0.96)
            painter.drawPixmap(image_rect, self.pixmap, self.pixmap.rect())
            painter.setOpacity(1.0)

        shade = QLinearGradient(rect.topLeft(), rect.topRight())
        shade.setColorAt(0.0, QColor(255, 255, 255, 236))
        shade.setColorAt(0.62, QColor(255, 255, 255, 145))
        shade.setColorAt(1.0, QColor(255, 255, 255, 18))
        painter.setBrush(shade)
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(rect, 18, 18)



class SidebarNavButton(QPushButton):
    def __init__(self, title: str, icon_name: str, *, compact: bool = False) -> None:
        super().__init__()
        self.setObjectName("SubNavButton" if compact else "NavButton")
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(False)
        self._active = False
        self._compact = compact
        icon_size = 13 if compact else 15
        self._icons = {
            False: qta.icon(icon_name, color="#64748B"),
            True: qta.icon(icon_name, color="#0A7AFF"),
        }
        self.setIcon(self._icons[False])
        self.setIconSize(QSize(icon_size, icon_size))
        self.setText(title)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setProperty("active", active)
        self.setIcon(self._icons[active])
        self.style().unpolish(self)
        self.style().polish(self)


class ClickableFrame(QFrame):
    clicked = Signal()
    pressed = Signal()
    right_pressed = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.pressed.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self.rect().contains(event.pos()):
            self.right_pressed.emit(event.globalPos())
            event.accept()
            return
        super().contextMenuEvent(event)


class KpiCard(QFrame):
    def __init__(self, title: str, icon: str, tone: str, detail: str) -> None:
        super().__init__()
        self.setObjectName("KpiCard")
        self.setProperty("tone", tone)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 15)
        layout.setSpacing(7)

        top = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("KpiTitle")
        icon_label = QLabel()
        icon_label.setObjectName("KpiIcon")
        icon_label.setProperty("tone", tone)
        icon_label.setPixmap(qta.icon(icon, color="#0A7AFF" if tone == "blue" else "#F59E0B").pixmap(QSize(16, 16)))
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setFixedSize(30, 30)
        top.addWidget(title_label)
        top.addStretch()
        top.addWidget(icon_label)
        layout.addLayout(top)

        value = QLabel("-")
        value.setObjectName("KpiValue")
        layout.addWidget(value)
        sub = QLabel(detail)
        sub.setObjectName("KpiDetail")
        layout.addWidget(sub)


class EmptyState(QFrame):
    def __init__(self, icon: str, title: str, description: str) -> None:
        super().__init__()
        self.setObjectName("Card")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.addStretch()
        icon_label = QLabel()
        icon_label.setPixmap(qta.icon(icon, color="#92A0B3").pixmap(QSize(40, 40)))
        icon_label.setAlignment(Qt.AlignCenter)
        title_label = QLabel(title)
        title_label.setObjectName("EmptyTitle")
        title_label.setAlignment(Qt.AlignCenter)
        desc_label = QLabel(description)
        desc_label.setObjectName("EmptyDescription")
        desc_label.setAlignment(Qt.AlignCenter)
        desc_label.setWordWrap(True)
        layout.addWidget(icon_label)
        layout.addSpacing(12)
        layout.addWidget(title_label)
        layout.addWidget(desc_label)
        layout.addStretch()


class DashboardEmptyState(QWidget):
    def __init__(self, icon: str, title: str, description: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 20, 18, 20)
        layout.setSpacing(6)
        layout.addStretch()
        icon_label = QLabel()
        icon_label.setPixmap(qta.icon(icon, color="#9AA8BA").pixmap(QSize(34, 34)))
        icon_label.setAlignment(Qt.AlignCenter)
        title_label = QLabel(title)
        title_label.setObjectName("DashboardEmptyTitle")
        title_label.setAlignment(Qt.AlignCenter)
        description_label = QLabel(description)
        description_label.setObjectName("DashboardEmptyDescription")
        description_label.setAlignment(Qt.AlignCenter)
        description_label.setWordWrap(True)
        layout.addWidget(icon_label)
        layout.addSpacing(5)
        layout.addWidget(title_label)
        layout.addWidget(description_label)
        layout.addStretch()


class EmptyChartCanvas(QWidget):
    """Chart scaffold used until reviewed API data is connected."""

    PROCESS_NAMES = ("사출", "분리", "하이드레이션", "검사·접착", "누수·규격")

    def __init__(self, *, horizontal: bool = False) -> None:
        super().__init__()
        self.horizontal = horizontal
        self.setMinimumHeight(205)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(16, 10, -16, -12)
        chart = bounds.adjusted(42, 18, -14, -34)

        painter.setPen(QPen(QColor("#E7EBF0"), 1))
        for step in range(5):
            y = chart.top() + chart.height() * step / 4
            painter.drawLine(int(chart.left()), int(y), int(chart.right()), int(y))

        painter.setPen(QPen(QColor("#C8D0DA"), 1))
        painter.drawLine(int(chart.left()), int(chart.top()), int(chart.left()), int(chart.bottom()))
        painter.drawLine(int(chart.left()), int(chart.bottom()), int(chart.right()), int(chart.bottom()))

        painter.setPen(QColor("#718096"))
        label_font = QFont("Malgun Gothic", 8)
        painter.setFont(label_font)
        slot = chart.width() / len(self.PROCESS_NAMES)
        for index, name in enumerate(self.PROCESS_NAMES):
            label_rect = QRectF(chart.left() + slot * index, chart.bottom() + 7, slot, 22)
            painter.drawText(label_rect, Qt.AlignHCenter | Qt.AlignTop, name)

        message_rect = QRectF(chart.center().x() - 96, chart.center().y() - 24, 192, 48)
        painter.setBrush(QColor("#F7F9FC"))
        painter.setPen(QPen(QColor("#DCE3EC"), 1))
        painter.drawRoundedRect(message_rect, 9, 9)
        painter.setPen(QColor("#718096"))
        painter.drawText(message_rect, Qt.AlignCenter, "API 데이터 연결 후 표시")


class ProcessMetricChart(QWidget):
    PROCESS_NAMES = ("사출", "분리", "하이드레이션", "검사·접착", "누수·규격")

    def __init__(self, primary: dict[str, float], secondary: dict[str, float] | None = None, line: dict[str, float] | None = None, *, stacked: bool = False, mode: str = "bar") -> None:
        super().__init__()
        self.primary = primary
        self.secondary = secondary or {}
        self.line = line or {}
        self.stacked = stacked
        self.mode = mode
        self.tooltip_data: dict[str, dict[str, dict[str, float]]] = {}
        self._bar_hit_rects: dict[str, QRectF] = {}
        self.setMouseTracking(True)
        self.setMinimumHeight(205)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, primary: dict[str, float], secondary: dict[str, float] | None = None) -> None:
        self.primary = dict(primary)
        self.secondary = dict(secondary or {})
        self.update()

    def set_mode_data(self, mode: str, values: dict[str, float]) -> None:
        self.mode = mode
        self.primary = dict(values)
        self.secondary = {}
        self.line = {}
        self.update()

    def set_tooltip_data(self, data: dict[str, dict[str, dict[str, float]]]) -> None:
        self.tooltip_data = data

    @staticmethod
    def _short_number(value: float) -> str:
        if abs(value) >= 1_000:
            return f"{value / 1_000:.0f}k"
        return f"{value:,.0f}"

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(12, 8, -12, -8)
        chart = bounds.adjusted(66, 12, -8, -34)
        self._bar_hit_rects = {}
        if self.mode == "line":
            values = [float(self.primary.get(name, 0)) for name in self.PROCESS_NAMES]
        elif self.stacked and self.secondary:
            values = [
                float(self.primary.get(name, 0)) + float(self.secondary.get(name, 0))
                for name in self.PROCESS_NAMES
            ]
        else:
            values = [float(self.primary.get(name, 0)) for name in self.PROCESS_NAMES]
            values += [float(self.secondary.get(name, 0)) for name in self.PROCESS_NAMES]
        minimum = 70.0 if self.mode == "line" and min(values or [0]) >= 70 else 0.0
        maximum = 100.0 if self.mode == "line" else (max(values or [0]) or 1)
        painter.setFont(QFont("Malgun Gothic", 8))
        for step in range(5):
            ratio = step / 4
            y = chart.bottom() - chart.height() * ratio
            painter.setPen(QPen(QColor("#E5EAF0"), 1))
            painter.drawLine(int(chart.left()), int(y), int(chart.right()), int(y))
            painter.setPen(QColor("#7A8798"))
            axis_value = minimum + (maximum - minimum) * ratio
            axis_label = f"{axis_value:.0f}%" if self.mode == "line" else self._short_number(axis_value)
            painter.drawText(QRectF(bounds.left(), y - 9, 54, 18), Qt.AlignRight | Qt.AlignVCenter, axis_label)
        slot = chart.width() / len(self.PROCESS_NAMES)
        bar_width = min(62.0, slot * (0.40 if self.secondary else 0.46))
        points: list[tuple[float, float]] = []
        for index, name in enumerate(self.PROCESS_NAMES):
            center = chart.left() + slot * (index + 0.5)
            if self.mode == "line":
                percent = max(minimum, min(maximum, float(self.primary.get(name, 0))))
                normalized = (percent - minimum) / (maximum - minimum or 1)
                points.append((center, chart.bottom() - chart.height() * normalized))
            elif self.stacked and self.secondary:
                primary_value = float(self.primary.get(name, 0))
                secondary_value = float(self.secondary.get(name, 0))
                primary_height = chart.height() * primary_value / maximum
                secondary_height = chart.height() * secondary_value / maximum
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor("#0A7AFF"))
                painter.drawRoundedRect(
                    QRectF(center - bar_width / 2, chart.bottom() - primary_height, bar_width, primary_height), 4, 4
                )
                painter.setBrush(QColor("#7C3AED"))
                painter.drawRoundedRect(
                    QRectF(center - bar_width / 2, chart.bottom() - primary_height - secondary_height, bar_width, secondary_height), 4, 4
                )
                self._bar_hit_rects[name] = QRectF(
                    center - bar_width / 2 - 8,
                    chart.bottom() - primary_height - secondary_height - 8,
                    bar_width + 16,
                    primary_height + secondary_height + 16,
                )
            else:
                series = [(self.primary, QColor("#0A7AFF"), -bar_width * 0.58 if self.secondary else 0.0)]
                if self.secondary:
                    series.append((self.secondary, QColor("#7C3AED"), bar_width * 0.58))
                for values_by_name, color, offset in series:
                    value = float(values_by_name.get(name, 0))
                    height = chart.height() * value / maximum
                    rect = QRectF(center - bar_width / 2 + offset, chart.bottom() - height, bar_width, height)
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(color)
                    painter.drawRoundedRect(rect, 4, 4)
                    self._bar_hit_rects[name] = rect.adjusted(-8, -8, 8, 8)
            if self.line and self.mode != "line":
                percent = max(0.0, min(100.0, float(self.line.get(name, 0))))
                points.append((center, chart.bottom() - chart.height() * percent / 100))
            painter.setPen(QColor("#617087"))
            painter.drawText(QRectF(chart.left() + slot * index, chart.bottom() + 7, slot, 22), Qt.AlignHCenter | Qt.AlignTop, name)
        if points:
            painter.setPen(QPen(QColor("#E6A100"), 2.2))
            for first, second in zip(points, points[1:]):
                painter.drawLine(int(first[0]), int(first[1]), int(second[0]), int(second[1]))
            painter.setBrush(QColor("#FFFFFF"))
            for x, y in points:
                painter.drawEllipse(QRectF(x - 4, y - 4, 8, 8))

    @staticmethod
    def _kpcs(value: float) -> str:
        return f"{value / 1_000:,.1f} kpcs"

    def _tooltip_text_for_process(self, process_name: str) -> str:
        detail = self.tooltip_data.get(process_name, {})
        if not detail:
            return ""
        lines = [f"{process_name} 공정 필요수량"]
        for lens_key, lens_name in (("color", "Color"), ("clear", "Clear")):
            classifications = detail.get(lens_key, {})
            total = sum(float(value or 0) for value in classifications.values())
            lines.append(f"\n{lens_name}  {self._kpcs(total)}")
            for classification, quantity in sorted(
                classifications.items(), key=lambda item: (-float(item[1] or 0), item[0])
            ):
                if float(quantity or 0) > 0:
                    lines.append(f"  {classification}: {self._kpcs(float(quantity))}")
        return "\n".join(lines)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        for process_name, hit_rect in self._bar_hit_rects.items():
            if hit_rect.contains(event.position()):
                tooltip_text = self._tooltip_text_for_process(process_name)
                if tooltip_text:
                    QToolTip.showText(event.globalPosition().toPoint(), tooltip_text, self)
                    return
        QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        QToolTip.hideText()
        super().leaveEvent(event)


class RequirementHorizontalChart(QWidget):
    PROCESS_NAMES = ("사출", "분리", "하이드레이션", "검사·접착", "누수·규격")
    PROCESS_COLORS = {
        "사출": ("#1683FF", "#A9CEF5"),
        "분리": ("#19A974", "#A9DFD0"),
        "하이드레이션": ("#7C4DFF", "#C8B9F5"),
        "검사·접착": ("#F29A16", "#F8D79C"),
        "누수·규격": ("#0E9FA5", "#A5DBDD"),
    }

    def __init__(self, clear: dict[str, float], color: dict[str, float]) -> None:
        super().__init__()
        self.primary = dict(clear)
        self.secondary = dict(color)
        self.tooltip_data: dict[str, dict[str, dict[str, float]]] = {}
        self._row_hit_rects: dict[str, QRectF] = {}
        self.setMouseTracking(True)
        self.setMinimumHeight(205)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_data(self, clear: dict[str, float], color: dict[str, float] | None = None) -> None:
        self.primary = dict(clear)
        self.secondary = dict(color or {})
        self.update()

    def set_tooltip_data(self, data: dict[str, dict[str, dict[str, float]]]) -> None:
        self.tooltip_data = data

    @staticmethod
    def _kpcs(value: float, decimals: int = 0) -> str:
        return f"{value / 1_000:,.{decimals}f} kpcs"

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(12, 8, -12, -8)
        chart = bounds.adjusted(92, 12, -104, -28)
        totals = {
            name: float(self.primary.get(name, 0) or 0) + float(self.secondary.get(name, 0) or 0)
            for name in self.PROCESS_NAMES
        }
        maximum = max(totals.values() or [0]) or 1.0
        maximum *= 1.08
        painter.setFont(QFont("Malgun Gothic", 8))
        for step in range(5):
            ratio = step / 4
            x = chart.left() + chart.width() * ratio
            painter.setPen(QPen(QColor("#E5EAF0"), 1))
            painter.drawLine(int(x), int(chart.top()), int(x), int(chart.bottom()))
            painter.setPen(QColor("#7A8798"))
            painter.drawText(
                QRectF(x - 35, chart.bottom() + 7, 70, 18),
                Qt.AlignHCenter | Qt.AlignTop,
                self._kpcs(maximum * ratio),
            )
        slot = chart.height() / len(self.PROCESS_NAMES)
        bar_height = min(34.0, slot * 0.58)
        self._row_hit_rects = {}
        for index, name in enumerate(self.PROCESS_NAMES):
            center_y = chart.top() + slot * (index + 0.5)
            strong_color, light_color = self.PROCESS_COLORS[name]
            clear_value = float(self.primary.get(name, 0) or 0)
            color_value = float(self.secondary.get(name, 0) or 0)
            total = clear_value + color_value
            clear_width = chart.width() * clear_value / maximum
            color_width = chart.width() * color_value / maximum
            painter.setPen(QColor("#40556F"))
            painter.drawText(
                QRectF(bounds.left(), center_y - 12, 78, 24),
                Qt.AlignRight | Qt.AlignVCenter,
                name,
            )
            painter.setPen(Qt.NoPen)
            total_rect = QRectF(chart.left(), center_y - bar_height / 2, clear_width + color_width, bar_height)
            painter.setBrush(QColor(light_color))
            painter.drawRoundedRect(total_rect, bar_height / 2, bar_height / 2)
            painter.setBrush(QColor(strong_color))
            clear_rect = QRectF(chart.left(), center_y - bar_height / 2, clear_width, bar_height)
            painter.drawRoundedRect(clear_rect, bar_height / 2, bar_height / 2)
            color_rect = QRectF(chart.left() + clear_width, center_y - bar_height / 2, color_width, bar_height)
            painter.setFont(QFont("Malgun Gothic", 8, QFont.Bold))
            if clear_width >= 72:
                painter.setPen(QColor("#FFFFFF"))
                painter.drawText(clear_rect, Qt.AlignCenter, f"{clear_value / 1_000:,.0f}k")
            if color_width >= 58:
                painter.setPen(QColor("#29405D"))
                painter.drawText(color_rect, Qt.AlignCenter, f"{color_value / 1_000:,.0f}k")
            small_segments: list[tuple[str, float, float]] = []
            if 0 < clear_width < 72:
                small_segments.append(("Clear", clear_value, clear_rect.center().x()))
            if 0 < color_width < 58:
                small_segments.append(("Color", color_value, color_rect.center().x()))
            if small_segments:
                callout_text = " · ".join(
                    f"{segment_name} {segment_value / 1_000:,.0f}k"
                    for segment_name, segment_value, _ in small_segments
                )
                anchor_x = sum(anchor for _, _, anchor in small_segments) / len(small_segments)
                note_width = min(148.0, max(64.0, 12.0 + len(callout_text) * 6.4))
                note_x = max(
                    chart.left(),
                    min(chart.right() - note_width, anchor_x - note_width / 2),
                )
                note_rect = QRectF(
                    note_x,
                    center_y - bar_height / 2 - 21,
                    note_width,
                    17,
                )
                painter.setPen(QPen(QColor(strong_color), 1))
                painter.drawLine(
                    int(anchor_x),
                    int(center_y - bar_height / 2),
                    int(note_rect.center().x()),
                    int(note_rect.bottom()),
                )
                painter.setBrush(QColor(strong_color))
                painter.drawEllipse(QRectF(anchor_x - 2.5, center_y - bar_height / 2 - 2.5, 5, 5))
                painter.setBrush(QColor("#FFFFFF"))
                painter.drawRoundedRect(note_rect, 6, 6)
                painter.setPen(QColor("#334A66"))
                painter.setFont(QFont("Malgun Gothic", 7, QFont.Bold))
                painter.drawText(note_rect.adjusted(5, 0, -5, 0), Qt.AlignCenter, callout_text)
            painter.setPen(QColor(strong_color))
            painter.setFont(QFont("Malgun Gothic", 8, QFont.Bold))
            painter.drawText(
                QRectF(chart.left() + clear_width + color_width + 8, center_y - 12, 94, 24),
                Qt.AlignLeft | Qt.AlignVCenter,
                self._kpcs(total),
            )
            painter.setFont(QFont("Malgun Gothic", 8))
            self._row_hit_rects[name] = QRectF(
                bounds.left(), center_y - slot / 2, bounds.width(), slot
            )

    def _tooltip_text(self, process_name: str) -> str:
        detail = self.tooltip_data.get(process_name, {})
        total = float(self.primary.get(process_name, 0) or 0) + float(self.secondary.get(process_name, 0) or 0)
        lines = [f"{process_name}  총 {self._kpcs(total, 1)}"]
        for lens_key, lens_name in (("color", "Color"), ("clear", "Clear")):
            classifications = detail.get(lens_key, {})
            lens_total = sum(float(value or 0) for value in classifications.values())
            lines.append(f"\n{lens_name}  {self._kpcs(lens_total, 1)}")
            ranked = sorted(classifications.items(), key=lambda item: -float(item[1] or 0))
            for classification, quantity in ranked[:3]:
                if float(quantity or 0) > 0:
                    lines.append(f"  {classification}: {self._kpcs(float(quantity), 1)}")
            remaining = sum(1 for _, quantity in ranked[3:] if float(quantity or 0) > 0)
            if remaining:
                lines.append(f"  외 {remaining}개 분류")
        return "\n".join(lines)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        for process_name, rect in self._row_hit_rects.items():
            if rect.contains(event.position()):
                QToolTip.showText(event.globalPosition().toPoint(), self._tooltip_text(process_name), self)
                return
        QToolTip.hideText()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        QToolTip.hideText()
        super().leaveEvent(event)


class ProductionTrendChart(QWidget):
    PROCESS_NAMES = ("사출", "분리", "하이드레이션", "검사·접착", "누수·규격")
    PROCESS_COLORS = {
        "사출": "#0A7AFF",
        "분리": "#22B95A",
        "하이드레이션": "#7C3AED",
        "검사·접착": "#E69000",
        "누수·규격": "#00A7A7",
    }

    def __init__(self, period: dict, view_mode: str = "daily") -> None:
        super().__init__()
        self.period = period
        self.view_mode = "daily"
        self._day_hit_rects: dict[str, QRectF] = {}
        self.setMouseTracking(True)
        self.setMinimumHeight(205)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_period(self, period: dict) -> None:
        self.period = period
        self.update()

    def set_view_mode(self, view_mode: str) -> None:
        self.view_mode = "daily"

    @staticmethod
    def _kpcs(value: float) -> str:
        return f"{value / 1_000:,.0f}k"

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(12, 8, -12, -8)
        chart = bounds.adjusted(64, 12, -52, -34)
        days = list(self.period.get("days", ()))
        if not days:
            return
        daily_good = self.period.get("daily_final_good", self.period.get("daily_total", {}))
        daily_yield = self.period.get("daily_overall_yield", {})
        live_day = str(self.period.get("live_day") or "")
        live_value = float(self.period.get("live_final_good") or 0)
        values = [float(daily_good.get(day, 0) or 0) for day in days]
        if live_day in days:
            values.append(live_value)
        maximum = max(values or [0]) or 1.0
        maximum *= 1.08
        painter.setFont(QFont("Malgun Gothic", 8))
        for step in range(5):
            ratio = step / 4
            y = chart.bottom() - chart.height() * ratio
            painter.setPen(QPen(QColor("#E5EAF0"), 1))
            painter.drawLine(int(chart.left()), int(y), int(chart.right()), int(y))
            painter.setPen(QColor("#7A8798"))
            painter.drawText(
                QRectF(bounds.left(), y - 9, 53, 18),
                Qt.AlignRight | Qt.AlignVCenter,
                self._kpcs(maximum * ratio),
            )
            painter.setPen(QColor("#A06A00"))
            painter.drawText(
                QRectF(chart.right() + 7, y - 9, 40, 18),
                Qt.AlignLeft | Qt.AlignVCenter,
                f"{ratio * 100:.0f}%",
            )
        slot = chart.width() / len(days)
        today = date.today()
        today_day = (
            str(today.day)
            if str(self.period.get("year_month") or "") == today.strftime("%Y-%m")
            else ""
        )
        self._day_hit_rects = {}
        last_data_index = max(
            (
                index for index, day in enumerate(days)
                if float(daily_good.get(day, 0) or 0) > 0
                or daily_yield.get(day) is not None
            ),
            default=-1,
        )
        bar_width = max(4.0, min(20.0, slot * 0.76))
        latest_bar_anchor: tuple[float, float, float, str] | None = None
        live_bar_anchor: tuple[float, float, float] | None = None
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#0A7AFF"))
        for index, day in enumerate(days):
            is_live = day == live_day
            value = live_value if is_live else float(daily_good.get(day, 0) or 0)
            center = chart.left() + slot * (index + 0.5)
            height = chart.height() * value / maximum
            rect = QRectF(center - bar_width / 2, chart.bottom() - height, bar_width, height)
            if value > 0:
                painter.setBrush(QColor("#20A66A") if is_live else QColor("#0A7AFF"))
                painter.drawRoundedRect(rect, 4, 4)
                if is_live:
                    live_bar_anchor = (center, rect.top(), value)
                if index == last_data_index and not is_live:
                    latest_bar_anchor = (center, rect.top(), value, day)
            self._day_hit_rects[day] = QRectF(
                chart.left() + slot * index, chart.top(), slot, chart.height()
            )
        previous_point: tuple[float, float] | None = None
        latest_yield_anchor: tuple[float, float, float, str] | None = None
        painter.setBrush(QColor("#FFFFFF"))
        for index, day in enumerate(days):
            yield_value = daily_yield.get(day)
            if yield_value is None:
                previous_point = None
                continue
            x = chart.left() + slot * (index + 0.5)
            y = chart.bottom() - chart.height() * max(0.0, min(100.0, float(yield_value))) / 100.0
            if previous_point is not None:
                painter.setPen(QPen(QColor(230, 144, 0, 50), 6.0, Qt.SolidLine, Qt.RoundCap))
                painter.drawLine(int(previous_point[0]), int(previous_point[1]), int(x), int(y))
                painter.setPen(QPen(QColor("#E69000"), 2.4, Qt.SolidLine, Qt.RoundCap))
                painter.drawLine(int(previous_point[0]), int(previous_point[1]), int(x), int(y))
            painter.setPen(QPen(QColor("#E69000"), 2.0))
            painter.drawEllipse(QRectF(x - 3, y - 3, 6, 6))
            if index == last_data_index:
                latest_yield_anchor = (x, y, float(yield_value), day)
            previous_point = (x, y)

        annotation_font = QFont("Malgun Gothic", 8, QFont.Bold)
        if latest_bar_anchor is not None and latest_yield_anchor is not None:
            _bar_x, _bar_y, bar_value, _bar_day = latest_bar_anchor
            yield_x, yield_y, yield_value, _yield_day = latest_yield_anchor
            confirmed_text = f"수율 {yield_value:.1f}% · 실적 {self._kpcs(bar_value)} pcs"
            painter.setFont(annotation_font)
            painter.setPen(QPen(QColor("#E69000"), 1.2))
            painter.drawLine(int(yield_x + 3), int(yield_y), int(yield_x + 7), int(yield_y))
            painter.setPen(QColor("#D17E00"))
            painter.drawText(
                QRectF(yield_x + 9, max(chart.top(), min(chart.bottom() - 18, yield_y - 9)), 185, 18),
                Qt.AlignLeft | Qt.AlignVCenter,
                confirmed_text,
            )

        # 오늘 값은 기준선/점선을 쓰지 않고 실제 초록 막대 바로 옆에만 표시한다.
        if live_bar_anchor is not None:
            live_x, live_y, live_amount = live_bar_anchor
            label_y = max(chart.top(), min(chart.bottom() - 18, live_y - 9))
            painter.setFont(annotation_font)
            painter.setPen(QColor("#07875F"))
            painter.drawText(
                QRectF(live_x + bar_width / 2 + 6, label_y, 145, 18),
                Qt.AlignLeft | Qt.AlignVCenter,
                f"금일 실적 {self._kpcs(live_amount)} pcs",
            )
        label_days = {"1", "5", "10", "15", "20", "25", days[-1]}
        if today_day:
            label_days.add(today_day)
        for index, day in enumerate(days):
            if day not in label_days:
                continue
            if day == today_day:
                painter.setPen(QColor("#07875F"))
                painter.setFont(QFont("Malgun Gothic", 8, QFont.Bold))
            else:
                painter.setPen(QColor("#617087"))
                painter.setFont(QFont("Malgun Gothic", 8))
            painter.drawText(
                QRectF(chart.left() + slot * index - slot / 2, chart.bottom() + 7, slot * 2, 22),
                Qt.AlignHCenter | Qt.AlignTop,
                day,
            )

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        for day, rect in self._day_hit_rects.items():
            if not rect.contains(event.position()):
                continue
            is_live = day == str(self.period.get("live_day") or "")
            good = (
                float(self.period.get("live_final_good") or 0)
                if is_live else float(self.period.get("daily_final_good", {}).get(day, 0) or 0)
            )
            overall = self.period.get("daily_overall_yield", {}).get(day)
            lines = [
                f"{self.period.get('year_month', '')}-{int(day):02d}",
                f"누수·규격 양품  {good / 1_000:,.1f} kpcs" + (" · 진행 중" if is_live else " · 확정"),
                f"종합수율  {float(overall):.1f}%" if overall is not None else "종합수율  —",
            ]
            for name in self.PROCESS_NAMES:
                value = self.period.get("daily_yield_by_process", {}).get(name, {}).get(day)
                if value is not None:
                    lines.append(f"{name}  {float(value):.1f}%")
            text = "\n".join(lines)
            QToolTip.showText(event.globalPosition().toPoint(), text, self)
            return
        QToolTip.hideText()

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        QToolTip.hideText()
        super().leaveEvent(event)


def _bom_color_for(code: str) -> QColor:
    colors = {
        "T": "#0A7AFF",
        "S": "#0A7AFF",
        "P": "#566DF4",
        "Q": "#22B7B3",
        "R": "#F0A640",
        "B": "#A88E69",
        "A": "#A88E69",
    }
    return QColor(colors.get(str(code)[:1].upper(), "#7B8794"))


class BomNodeItem(QGraphicsObject):
    def __init__(
        self,
        item: dict[str, str],
        rect: QRectF,
        *,
        selected: bool = False,
        callback: Callable[[str], None] | None = None,
        requery_callback: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.item = item
        self._local_rect = QRectF(0, 0, rect.width(), rect.height())
        self.selected = selected
        self.path_active = selected
        self.callback = callback
        self.requery_callback = requery_callback
        self.setPos(rect.topLeft())
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setZValue(5)

        dia_bc = " / ".join(value for value in (item.get("dia", ""), item.get("bc", "")) if value)
        fields = (
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
        self.setToolTip("\n".join(f"{label}  {value or '-'}" for label, value in fields))

    def boundingRect(self) -> QRectF:  # noqa: N802 - Qt API
        return self._local_rect.adjusted(-3, -3, 3, 3)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        painter.setRenderHint(QPainter.Antialiasing)
        hovered = self.isUnderMouse()
        border = QColor("#2F80ED") if self.selected or hovered else QColor("#78A6E8") if self.path_active else QColor("#DCE3EB")
        fill = QColor("#F1F6FC") if self.selected else QColor("#F8FBFF") if self.path_active else QColor("#FFFFFF")
        painter.setPen(QPen(border, 2.0 if self.selected else 1.25 if self.path_active else 1.0))
        painter.setBrush(fill)
        painter.drawRoundedRect(self._local_rect, 11, 11)

        painter.setPen(Qt.NoPen)
        painter.setBrush(_bom_color_for(self.item.get("code", "")))
        painter.drawRoundedRect(QRectF(0, 0, 5, self._local_rect.height()), 2.5, 2.5)

        code = self.item.get("code", "") or "-"
        name = self.item.get("name", "") or "품명 정보 없음"
        left = 15.0
        width = self._local_rect.width() - 30.0
        painter.setPen(QColor("#152238"))
        painter.setFont(QFont("Malgun Gothic", 10, QFont.Bold))
        painter.drawText(QRectF(left, 7, width, 22), Qt.AlignLeft | Qt.AlignVCenter, code)
        painter.setPen(QColor("#526477"))
        painter.setFont(QFont("Malgun Gothic", 8, QFont.DemiBold))
        elided = painter.fontMetrics().elidedText(name, Qt.ElideRight, int(width))
        painter.drawText(QRectF(left, 29, width, 18), Qt.AlignLeft | Qt.AlignVCenter, elided)

        if self.selected:
            qta.icon("fa6s.square-check", color="#0A7AFF").paint(
                painter, QRect(int(self._local_rect.width() - 23), 8, 14, 14)
            )
        else:
            painter.setFont(QFont("Segoe UI", 11, QFont.Bold))
            painter.setPen(QColor("#91A0B0"))
            painter.drawText(QRectF(self._local_rect.width() - 22, 4, 15, 23), Qt.AlignCenter, "›")

    def set_path_state(self, *, selected: bool, active: bool) -> None:
        self.selected = selected
        self.path_active = active
        self.setOpacity(1.0 if active else 0.46)
        self.setZValue(8 if selected else 6 if active else 5)
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton and self.callback:
            self.callback(self.item.get("code", ""))
            event.accept()
            return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - Qt API
        code = str(self.item.get("code", "")).strip()
        if not code:
            return
        menu = QMenu()
        requery_action = menu.addAction(qta.icon("fa6s.magnifying-glass", color="#0A7AFF"), f"{code} 기준으로 재조회")
        copy_code_action = menu.addAction(qta.icon("fa6s.copy", color="#52677E"), "품번 복사")
        copy_all_action = menu.addAction(qta.icon("fa6s.clipboard", color="#52677E"), "품번·품명 복사")
        selected_action = menu.exec(event.screenPos())
        if selected_action is requery_action and self.requery_callback:
            self.requery_callback(code)
        elif selected_action is copy_code_action:
            QApplication.clipboard().setText(code)
        elif selected_action is copy_all_action:
            QApplication.clipboard().setText(f"{code}\t{self.item.get('name', '')}".rstrip())
        event.accept()

    def hoverEnterEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self.update()
        super().hoverLeaveEvent(event)


class BomFlowBoard(QGraphicsView):
    item_selected = Signal(str)
    item_requery_requested = Signal(str)
    active_path_changed = Signal(object)
    STAGE_TITLES = ("판매코드", "생산코드", "분리코드", "사출코드", "사출코드 하위")

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("BomFlowBoard")
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setFrameShape(QFrame.NoFrame)
        self.setBackgroundBrush(QColor("#FFFFFF"))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setMinimumHeight(470)
        self.node_items: dict[str, BomNodeItem] = {}
        self.flow_items: dict[tuple[str, str], QGraphicsPathItem] = {}
        self.edges: list[dict[str, str]] = []
        self.stage_codes: list[list[str]] = []
        self.selected_code = ""
        self.show_empty("품번을 검색하면 5단계 BOM 연결관계가 표시됩니다.")

    @staticmethod
    def _positions(count: int, card_height: float) -> list[float]:
        return [18.0 + (card_height + 8.0) * index for index in range(max(0, count))]

    def show_empty(self, message: str) -> None:
        scene = self.scene()
        scene.clear()
        self.node_items.clear()
        self.flow_items.clear()
        self.edges = []
        self.stage_codes = [[] for _ in self.STAGE_TITLES]
        width, height = 1280.0, 500.0
        scene.setSceneRect(0, 0, width, height)
        empty = scene.addSimpleText(f"BOM 조회 대기\n\n{message}")
        empty.setFont(QFont("Malgun Gothic", 11, QFont.DemiBold))
        empty.setBrush(QColor("#7B8794"))
        empty.setPos(width / 2 - empty.boundingRect().width() / 2, height / 2 - 35)
        self.active_path_changed.emit(self.stage_codes)
        self._fit_scene()

    def set_hierarchy(self, hierarchy: dict[str, Any]) -> None:
        scene = self.scene()
        scene.clear()
        self.node_items.clear()
        self.flow_items.clear()
        columns = list(hierarchy.get("columns") or [])
        selected_code = str(hierarchy.get("selected_code") or "")
        if not columns or not selected_code:
            self.show_empty("품번을 검색하면 5단계 BOM 연결관계가 표시됩니다.")
            return

        width = 1280.0
        self.stage_codes = [[str(row.get("code") or "") for row in column if isinstance(row, dict)] for column in columns]
        self.edges = [edge for edge in list(hierarchy.get("edges") or []) if isinstance(edge, dict)]
        self.selected_code = selected_code
        visible_count = max((len(column) for column in columns), default=1)
        card_w, card_h = 218.0, 50.0
        height = max(500.0, visible_count * (card_h + 8.0) + 30.0)
        scene.setSceneRect(0, 0, width, height)
        gap = (width - 36.0 - card_w * len(columns)) / max(1, len(columns) - 1)
        column_x = [18.0 + index * (card_w + gap) for index in range(len(columns))]
        node_rects: dict[str, QRectF] = {}
        for index, column in enumerate(columns):
            for row, y in zip(column, self._positions(len(column), card_h)):
                if isinstance(row, dict) and row.get("code"):
                    node_rects[str(row["code"])] = QRectF(column_x[index], y, card_w, card_h)

        for edge in self.edges:
            parent = str(edge.get("parent") or "")
            child = str(edge.get("child") or "")
            parent_rect, child_rect = node_rects.get(parent), node_rects.get(child)
            if parent_rect is None or child_rect is None:
                continue
            start = QPointF(parent_rect.right(), parent_rect.center().y())
            end = QPointF(child_rect.left(), child_rect.center().y())
            path = QPainterPath(start)
            distance = end.x() - start.x()
            path.cubicTo(QPointF(start.x() + distance * 0.44, start.y()), QPointF(end.x() - distance * 0.44, end.y()), end)
            color = _bom_color_for(parent)
            color.setAlpha(110)
            flow = QGraphicsPathItem(path)
            flow.setPen(QPen(color, 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            flow.setZValue(-1)
            scene.addItem(flow)
            self.flow_items[(parent, child)] = flow

        for column in columns:
            for row in column:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("code") or "")
                rect = node_rects.get(code)
                if rect is None:
                    continue
                node = BomNodeItem(
                    row,
                    rect,
                    selected=code == selected_code,
                    callback=self.item_selected.emit,
                    requery_callback=self.item_requery_requested.emit,
                )
                self.node_items[code] = node
                scene.addItem(node)
        self._fit_scene()
        self._apply_path_highlight(selected_code)

    def set_selected(self, code: str) -> None:
        if code in self.node_items:
            self.selected_code = code
            self._apply_path_highlight(code)

    def _apply_path_highlight(self, code: str) -> None:
        incoming: dict[str, list[str]] = {}
        outgoing: dict[str, list[str]] = {}
        for edge in self.edges:
            parent, child = str(edge.get("parent") or ""), str(edge.get("child") or "")
            if parent in self.node_items and child in self.node_items:
                outgoing.setdefault(parent, []).append(child)
                incoming.setdefault(child, []).append(parent)

        active_nodes, active_edges = {code}, set()
        frontier = [code]
        while frontier:
            child = frontier.pop()
            for parent in incoming.get(child, []):
                if (parent, child) not in active_edges:
                    active_edges.add((parent, child))
                    if parent not in active_nodes:
                        active_nodes.add(parent)
                        frontier.append(parent)
        frontier = [code]
        while frontier:
            parent = frontier.pop()
            for child in outgoing.get(parent, []):
                if (parent, child) not in active_edges:
                    active_edges.add((parent, child))
                    if child not in active_nodes:
                        active_nodes.add(child)
                        frontier.append(child)

        for item_code, node in self.node_items.items():
            node.set_path_state(selected=item_code == code, active=item_code in active_nodes)
        for edge_key, flow in self.flow_items.items():
            active = edge_key in active_edges
            flow.setPen(QPen(QColor(72, 128, 196, 180) if active else QColor(148, 163, 184, 42), 2.0 if active else 1.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            flow.setZValue(1 if active else -2)
        self.active_path_changed.emit(self.active_codes_by_stage())

    def active_codes_by_stage(self) -> list[list[str]]:
        return [[code for code in stage if code in self.node_items and self.node_items[code].path_active] for stage in self.stage_codes]

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._fit_scene()

    def _fit_scene(self) -> None:
        if self.scene() and self.scene().sceneRect().isValid():
            self.resetTransform()
            factor = max(0.1, (self.viewport().width() - 8) / self.scene().sceneRect().width())
            self.scale(factor, factor)


class MainWindow(QMainWindow):
    PROCESS_KEYS = ("injection", "separation", "hydration", "inspection", "leak")

    def __init__(self, management_notices: list[dict[str, Any]] | tuple[dict[str, Any], ...] = ()) -> None:
        super().__init__()
        self.setWindowTitle("똑딱이 - 생산3팀 전용")
        self.setMinimumSize(1040, 680)
        self.resize(1420, 860)
        self.page_definitions = {page.key: page for page in PAGES}
        self.active_factory = DEFAULT_FACTORY
        self.dashboard_service = DashboardService()
        self.dashboard_data = self.dashboard_service.load()
        self.production_period_key = self.dashboard_data.get("default_production_period", "current")
        self.production_view_mode = "daily"
        self.page_indexes: dict[str, int] = {}
        self.nav_buttons: dict[str, SidebarNavButton] = {}
        self.collection_schedule = load_schedule()
        self._collection_last_attempt: dict[str, datetime] = {}
        self._current_page = "dashboard"
        self._force_close = False
        self._permission_check_future: Future | None = None
        self._permission_check_manual = False
        self._update_prompt_active = False
        self.management_notices = list(management_notices)
        self._permission_check_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="ddokddak-permission",
        )
        self._build_shell()
        self.notice_ticker.replace_notices(self.management_notices)
        self.show_page("dashboard")
        self._data_db_signatures = self._current_data_db_signatures()
        self._data_status_signatures = self._current_data_status_signatures()
        self.data_snapshot_timer = QTimer(self)
        self.data_snapshot_timer.setInterval(10_000)
        self.data_snapshot_timer.timeout.connect(self._check_data_snapshot_changes)
        self.data_snapshot_timer.start()
        self.aps_monitor_timer = QTimer(self)
        self.aps_monitor_timer.timeout.connect(self._start_aps_monitor_check)
        self.collection_scheduler_timer = QTimer(self)
        self.collection_scheduler_timer.setInterval(30_000)
        self.collection_scheduler_timer.timeout.connect(self._run_scheduled_collections)
        self.collection_scheduler_timer.start()
        self.data_cleanup_timer = QTimer(self)
        self.data_cleanup_timer.setInterval(6 * 60 * 60 * 1_000)
        self.data_cleanup_timer.timeout.connect(lambda: self._start_data_cleanup(scheduled=True))
        self.data_cleanup_timer.start()
        self.permission_check_timer = QTimer(self)
        self.permission_check_timer.setInterval(20 * 60 * 1_000)
        self.permission_check_timer.timeout.connect(self._start_runtime_permission_check)
        self.permission_check_timer.start()
        QTimer.singleShot(3_000, self._start_runtime_permission_check)
        self._apply_collection_timers(run_initial=True)
        QTimer.singleShot(5_000, self._run_scheduled_collections)
        QTimer.singleShot(20_000, lambda: self._start_data_cleanup(scheduled=True))

    @staticmethod
    def _database_signature(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
            return stat.st_size, stat.st_mtime_ns
        except OSError:
            return None

    def _current_data_db_signatures(self) -> dict[str, tuple[int, int] | None]:
        return {
            "bom": self._database_signature(DATA_CENTER_DIR / "bom" / "product_reference.sqlite"),
            "aps": self._database_signature(DATA_CENTER_DIR / "process-status" / "aps_process_status.sqlite"),
            "production": self._database_signature(DATA_CENTER_DIR / "production-performance" / "production_performance.sqlite"),
        }

    def _current_data_status_signatures(self) -> dict[str, tuple[int, int] | None]:
        return {
            "bom": self._database_signature(DATA_CENTER_DIR / "bom" / "snapshot" / "refresh_status.json"),
            "aps": self._database_signature(DATA_CENTER_DIR / "process-status" / "snapshot" / "refresh_status.json"),
            "production": self._database_signature(DATA_CENTER_DIR / "production-performance" / "snapshot" / "refresh_status.json"),
        }

    def _check_data_snapshot_changes(self) -> None:
        current = self._current_data_db_signatures()
        changed = {
            key for key, signature in current.items()
            if signature != self._data_db_signatures.get(key)
        }
        current_status = self._current_data_status_signatures()
        status_changed = current_status != self._data_status_signatures
        if not changed and not status_changed:
            return
        self._data_db_signatures = current
        self._data_status_signatures = current_status
        if changed:
            self._reload_changed_data_views(changed)
        else:
            self.dashboard_data = self.dashboard_service.load()
            self._refresh_settings_data_status()
            self._refresh_header_status()

    def _reload_changed_data_views(self, changed: set[str]) -> None:
        self.dashboard_data = self.dashboard_service.load()
        self._close_order_detail()
        self._refresh_risk_alerts()
        self._refresh_dashboard_channel_metrics()
        self._populate_process_matrix_table()
        period = self._selected_production_period()
        if hasattr(self, "performance_chart"):
            self.performance_chart.set_period(period)
        if hasattr(self, "performance_period_badge"):
            self.performance_period_badge.setText(self._production_period_badge_text(period))
        if hasattr(self, "performance_live_badge"):
            self.performance_live_badge.setVisible(
                str(period.get("year_month") or "") == date.today().strftime("%Y-%m")
            )
        if "aps" in changed and hasattr(self, "process_overview_page"):
            self.process_overview_page.reload_data()
            for page in getattr(self, "fixed_process_pages", {}).values():
                page.load_normalized_rows(self.process_overview_page.all_rows)
        if "bom" in changed and hasattr(self, "bom_page"):
            self.bom_page.refresh()
        self._refresh_settings_data_status()
        self._refresh_header_status()

    def _start_aps_monitor_check(self) -> None:
        if (
            hasattr(self, "aps_monitor_process")
            and self.aps_monitor_process.state() != QProcess.NotRunning
        ):
            return
        process = _background_process(self)
        process.setWorkingDirectory(str(ROOT_DIR))
        program, arguments = _collector_process_command(
            "aps_update_monitor.py", "--timeout", "60"
        )
        process.setProgram(program)
        process.setArguments(arguments)
        process.finished.connect(self._aps_monitor_finished)
        self.aps_monitor_process = process
        process.start()

    def _aps_monitor_finished(self, exit_code: int, _exit_status) -> None:
        process = self.aps_monitor_process
        if exit_code != 0:
            error = bytes(process.readAllStandardError()).decode(
                "utf-8", errors="replace"
            ).strip()
            self.data_status.setToolTip(
                f"APS 자동 확인 실패: {error[-240:] or '다음 주기에 다시 확인합니다.'}"
            )
            return
        try:
            result = json.loads(
                bytes(process.readAllStandardOutput()).decode(
                    "utf-8", errors="replace"
                )
            )
        except (ValueError, TypeError):
            self.data_status.setToolTip("APS 자동 확인 결과를 읽지 못했습니다.")
            return
        self.data_status.setToolTip(
            "APS 원천 갱신을 1분마다 확인합니다. 변경 시 S관 데이터를 자동 수집합니다."
        )
        if not result.get("changed"):
            return

        self._data_db_signatures = self._current_data_db_signatures()
        self._data_status_signatures = self._current_data_status_signatures()
        self._reload_changed_data_views({"aps"})
        self.show_page(self._current_page)

    def _build_shell(self) -> None:
        central = QWidget()
        central.setObjectName("ContentRoot")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(245)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(16, 20, 16, 16)
        side_layout.setSpacing(7)
        side_layout.addWidget(BrandHero())
        side_layout.addSpacing(13)

        menu_label = QLabel("업무 프로그램")
        menu_label.setObjectName("SidebarSection")
        side_layout.addWidget(menu_label)

        self._add_nav(side_layout, "dashboard", "대시보드", "fa6s.table-cells-large")
        self._add_nav(side_layout, "process_overview", "공정 현황", "fa6s.layer-group")
        self.process_container = QWidget()
        process_layout = QVBoxLayout(self.process_container)
        process_layout.setContentsMargins(14, 0, 0, 0)
        process_layout.setSpacing(2)
        process_items = (
            ("injection", "사출", "fa6s.gears"),
            ("separation", "분리", "fa6s.code-branch"),
            ("hydration", "하이드레이션", "fa6s.droplet"),
            ("inspection", "검사·접착", "fa6s.magnifying-glass"),
            ("leak", "누수·규격", "fa6s.ruler-combined"),
        )
        for key, title, icon in process_items:
            self._add_nav(process_layout, key, title, icon, compact=True)
        side_layout.addWidget(self.process_container)

        self._add_nav(side_layout, "bom", "BOM 현황", "fa6s.diagram-project")
        side_layout.addStretch()

        self._add_nav(side_layout, "settings", "설정 및 운영", "fa6s.gear")
        self.sidebar_status = QLabel(
            "<span style='color:#61758D; font-size:11px;'>사용 버전</span> "
            f"<span style='color:#0877F9; font-size:14px; font-weight:800;'>v{APP_VERSION}</span><br>"
            "<span style='color:#314B67; font-weight:700;'>생산기획팀</span> "
            "<span style='color:#8BA0B7;'>/</span> "
            "<span style='color:#173A63; font-weight:800;'>RD</span>"
        )
        self.sidebar_status.setObjectName("SidebarStatusCard")
        self.sidebar_status.setTextFormat(Qt.RichText)
        self.sidebar_status.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.sidebar_status.setStyleSheet(
            "QLabel#SidebarStatusCard {"
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #F7FBFF, stop:1 #EDF5FF);"
            "border: 1px solid #CFE2FA; border-radius: 12px; padding: 11px 14px;"
            "}"
        )
        side_layout.addWidget(self.sidebar_status)

        content = QWidget()
        content.setObjectName("Content")
        content_layout = QVBoxLayout(content)
        self.content_layout = content_layout
        content_layout.setContentsMargins(32, 26, 32, 26)
        content_layout.setSpacing(20)

        self.global_header = QWidget()
        self.global_header.setObjectName("GlobalHeader")
        self.global_header.setFixedHeight(36)
        header = QHBoxLayout(self.global_header)
        self.global_header_layout = header
        header.setContentsMargins(0, 0, 0, 0)
        header_text = QVBoxLayout()
        header_text.setSpacing(3)
        self.header_kicker = QLabel()
        self.header_kicker.setObjectName("PageKicker")
        self.header_title = QLabel()
        self.header_title.setObjectName("PageTitle")
        self.header_description = QLabel()
        self.header_description.setObjectName("PageDescription")
        self.header_description.setWordWrap(True)
        header_text.addWidget(self.header_kicker)
        header_text.addWidget(self.header_title)
        header_text.addWidget(self.header_description)
        header.addLayout(header_text, 1)
        self.notice_ticker = NoticeTicker(self.global_header)
        header.addWidget(self.notice_ticker, 2)

        self.header_meta = QLabel()
        self.header_meta.setObjectName("HeaderMeta")
        self.header_meta.setVisible(False)
        self.header_meta.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        header.addWidget(self.header_meta, 0, Qt.AlignRight | Qt.AlignVCenter)

        self.data_status = QPushButton("●  데이터 연결 전")
        self.data_status.setObjectName("DataStatusChip")
        self.data_status.setFocusPolicy(Qt.NoFocus)
        self.data_status.clicked.connect(self._open_collection_status)
        header.addWidget(self.data_status, 0, Qt.AlignTop)
        content_layout.addWidget(self.global_header)

        self.stack = QStackedWidget()
        content_layout.addWidget(self.stack, 1)
        self._add_page("dashboard", self._build_dashboard_page())
        self._add_page("process_overview", self._build_process_overview_page())
        for key in self.PROCESS_KEYS:
            self._add_page(key, self._build_process_page(self.page_definitions[key].title))
        self._add_page("bom", self._build_bom_page())
        self._add_page("settings", self._build_settings_page())

        root.addWidget(sidebar)
        root.addWidget(content, 1)
        self.setCentralWidget(central)

    def _add_nav(
        self,
        layout: QVBoxLayout,
        key: str,
        title: str,
        icon: str,
        *,
        compact: bool = False,
    ) -> None:
        button = SidebarNavButton(title, icon, compact=compact)
        button.clicked.connect(lambda checked=False, page_key=key: self.show_page(page_key))
        self.nav_buttons[key] = button
        layout.addWidget(button)

    def closeEvent(self, event) -> None:
        if self._force_close:
            self._shutdown_permission_checker()
            event.accept()
            return
        should_close = ask_app_confirmation(
            self,
            "프로그램 종료",
            "생산3팀 똑딱이를 종료할까요?\n\n진행 중인 데이터 수집이 있다면 완료 후 종료하는 것을 권장합니다.",
            accept_text="종료하기",
            reject_text="계속 사용",
        )
        if should_close:
            self._shutdown_permission_checker()
            event.accept()
        else:
            event.ignore()

    def _shutdown_permission_checker(self) -> None:
        if hasattr(self, "permission_check_timer"):
            self.permission_check_timer.stop()
        self._permission_check_executor.shutdown(wait=False, cancel_futures=True)

    def _start_runtime_permission_check(self, manual: bool = False) -> None:
        if self._permission_check_future is not None and not self._permission_check_future.done():
            if manual:
                show_app_message(
                    self,
                    "업데이트 확인",
                    "권한과 최신 버전을 이미 확인하고 있습니다.",
                )
            return
        self._permission_check_manual = manual
        if manual and hasattr(self, "settings_update_button"):
            self.settings_update_button.setEnabled(False)
            self.settings_update_button.setText("확인 중…")
        self._permission_check_future = self._permission_check_executor.submit(
            ProgramGate(APP_VERSION).check
        )
        QTimer.singleShot(200, self._finish_runtime_permission_check)

    def _finish_runtime_permission_check(self) -> None:
        future = self._permission_check_future
        if future is None:
            return
        if not future.done():
            QTimer.singleShot(200, self._finish_runtime_permission_check)
            return
        self._permission_check_future = None
        manual = self._permission_check_manual
        self._permission_check_manual = False
        if hasattr(self, "settings_update_button"):
            self.settings_update_button.setEnabled(True)
            self.settings_update_button.setText("업데이트 확인")
        try:
            result = future.result()
        except Exception as exc:
            if manual:
                show_app_message(
                    self,
                    "업데이트 확인 실패",
                    f"권한 및 버전 확인 중 오류가 발생했습니다.\n\n{exc}",
                    kind="warning",
                )
            return
        if result.reason == "network":
            if manual:
                show_app_message(
                    self,
                    "업데이트 확인 실패",
                    result.message or "관리 서버에 연결하지 못했습니다.",
                    kind="warning",
                )
            return
        if not result.allowed:
            self.permission_check_timer.stop()
            gate = ProgramGate(APP_VERSION)
            show_permission_denied(
                self,
                gate.identity()["pc_id"],
                result.message or "이 PC의 프로그램 사용 권한이 중지되었습니다.",
            )
            self._force_close = True
            self.close()
            return
        self.management_notices = list(result.notices)
        self.notice_ticker.replace_notices(self.management_notices)
        if result.update_required:
            if self._update_prompt_active:
                return
            self.permission_check_timer.stop()
            self._update_prompt_active = True
            try:
                action = show_required_update(
                    self,
                    APP_VERSION,
                    result.latest_version,
                    result.message or f"최신 버전 {result.latest_version}이 확인되었습니다.",
                    result.update_url,
                )
            finally:
                self._update_prompt_active = False
            if action in {"download", "update"}:
                self._force_close = True
                self.close()
            return
        if manual:
            latest = result.latest_version or APP_VERSION
            show_app_message(
                self,
                "업데이트 확인",
                f"현재 최신 버전을 사용하고 있습니다.\n\n현재 버전 v{APP_VERSION.lstrip('vV')}\n관리 기준 v{latest.lstrip('vV')}",
                kind="success",
            )

    def _add_page(self, key: str, page: QWidget) -> None:
        self.page_indexes[key] = self.stack.addWidget(page)

    def show_page(self, key: str) -> None:
        definition = self.page_definitions[key]
        self._current_page = key
        self.stack.setCurrentIndex(self.page_indexes[key])
        self.header_kicker.setText(definition.kicker.upper())
        self.header_title.setText(definition.title)
        self.header_description.setText(definition.description)
        dashboard_header = key == "dashboard"
        compact_process_header = key == "process_overview" or key in self.PROCESS_KEYS
        page_owns_header = key == "dashboard"
        self.header_kicker.setVisible(False)
        self.header_title.setVisible(not page_owns_header)
        self.header_description.setVisible(False)
        self.header_meta.setVisible(True)
        self.data_status.setVisible(True)
        if dashboard_header:
            self.global_header_layout.setContentsMargins(0, 0, 0, 0)
            self.content_layout.setContentsMargins(32, 14, 32, 18)
            self.content_layout.setSpacing(10)
        elif key == "bom":
            self.global_header_layout.setContentsMargins(32, 0, 32, 0)
            self.content_layout.setContentsMargins(0, 0, 0, 0)
            self.content_layout.setSpacing(0)
        elif compact_process_header:
            self.global_header_layout.setContentsMargins(0, 0, 0, 0)
            self.content_layout.setContentsMargins(32, 10, 32, 18)
            self.content_layout.setSpacing(8)
        else:
            self.global_header_layout.setContentsMargins(0, 0, 0, 0)
            self.content_layout.setContentsMargins(32, 26, 32, 26)
            self.content_layout.setSpacing(20)
        for page_key, button in self.nav_buttons.items():
            button.set_active(page_key == key)
        self._refresh_header_status()

    def _refresh_header_status(self) -> None:
        aps_fresh = self.dashboard_data.get("aps_status", {}).get("source_refreshed_at") or "-"
        self.header_meta.setText(f"APS 갱신  {aps_fresh}")
        api_collection_ready = all(
            self.dashboard_data.get(status_key, {}).get("status") in {"success", "skipped"}
            for status_key in ("aps_status", "production_status", "bom_status")
        )
        self.data_status.setProperty("state", "ready" if api_collection_ready else "waiting")
        self.data_status.setText(
            "●  수집 전체 양호" if api_collection_ready else "●  수집 상태 확인 필요"
        )
        if api_collection_ready:
            self.data_status.setCursor(Qt.ArrowCursor)
            self.data_status.setToolTip("BOM·APS·생산실적 수집이 모두 정상입니다.")
            self.data_status.setStyleSheet(
                "QPushButton { background:#ECFDF5; color:#087F5B; border:1px solid #9CE2C5; "
                "border-radius:10px; padding:7px 14px; font-weight:700; }"
            )
        else:
            self.data_status.setCursor(Qt.PointingHandCursor)
            self.data_status.setToolTip("클릭하여 설정 및 운영의 데이터 수집 상태를 확인합니다.")
            self.data_status.setStyleSheet(
                "QPushButton { background:#FFF7ED; color:#B45309; border:1px solid #FDBA74; "
                "border-radius:10px; padding:7px 14px; font-weight:800; }"
                "QPushButton:hover { background:#FFEDD5; border-color:#F97316; }"
                "QPushButton:pressed { background:#FED7AA; }"
            )
        self.data_status.style().unpolish(self.data_status)
        self.data_status.style().polish(self.data_status)

    def _open_collection_status(self) -> None:
        if self.data_status.property("state") == "ready":
            return
        self.show_page("settings")
        self.collection_expand_button.setChecked(True)
        self._toggle_collection_details(True)
        settings_page = self.stack.widget(self.page_indexes["settings"])
        if isinstance(settings_page, QScrollArea):
            QTimer.singleShot(
                0,
                lambda: settings_page.ensureWidgetVisible(self.collection_settings_card, 0, 18),
            )

    def _scroll_page(self, body: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("PageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        return scroll

    def _build_dashboard_page(self) -> QWidget:
        body = QWidget()
        body.setObjectName("PageBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 2)
        layout.setSpacing(12)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(12)
        self.dashboard_risk_card = self._build_risk_dashboard_card()
        top_row.addWidget(self.dashboard_risk_card, 1)

        summary = QWidget()
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(12)
        summary_layout.addWidget(self._build_aps_shortage_card())
        summary_layout.addWidget(self._build_requirement_chart_card(), 1)
        self.dashboard_summary_panel = summary

        self.dashboard_right_stack = QStackedWidget()
        self.dashboard_right_stack.setObjectName("DashboardRightStack")
        self.dashboard_right_stack.setAutoFillBackground(True)
        self.dashboard_right_stack.addWidget(summary)
        self.order_detail_panel = self._build_order_detail_panel()
        self.dashboard_right_stack.addWidget(self.order_detail_panel)
        self.dashboard_right_stack.setCurrentIndex(0)
        top_row.addWidget(self.dashboard_right_stack, 1)
        layout.addLayout(top_row, 3)

        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(12)
        bottom_row.addWidget(self._build_process_matrix_card(), 1)
        bottom_row.addWidget(self._build_performance_chart_card(), 1)
        layout.addLayout(bottom_row, 2)
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)
        self._refresh_dashboard_channel_metrics()
        return self._scroll_page(body)

    def _dashboard_card_header(
        self,
        title: str,
        subtitle: str = "",
        actions: QWidget | None = None,
    ) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("DashboardCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 14, 16, 15)
        card_layout.setSpacing(10)
        header = QHBoxLayout()
        header.setSpacing(9)
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("DashboardCardTitle")
        text_layout.addWidget(title_label)
        if subtitle:
            subtitle_label = QLabel(subtitle)
            subtitle_label.setObjectName("DashboardCardSubtitle")
            text_layout.addWidget(subtitle_label)
        header.addLayout(text_layout, 1)
        if actions is not None:
            header.addWidget(actions, 0, Qt.AlignVCenter)
        card_layout.addLayout(header)
        return card, card_layout

    def _count_chip(self, text: str, tone: str = "neutral") -> QLabel:
        chip = QLabel(text)
        chip.setObjectName("CountChip")
        chip.setProperty("tone", tone)
        chip.setAlignment(Qt.AlignCenter)
        return chip

    def _build_risk_dashboard_card(self) -> QFrame:
        return self._build_live_risk_dashboard_card()
        filters = QWidget()
        filters_layout = QHBoxLayout(filters)
        filters_layout.setContentsMargins(0, 0, 0, 0)
        filters_layout.setSpacing(5)
        for text, tone in (("전체 0건", "blue"), ("위험 D-3", "pink"), ("주의 D-7", "purple")):
            filters_layout.addWidget(self._count_chip(text, tone))

        card, card_layout = self._dashboard_card_header(
            f"{self.active_factory} 생산 미완료 리스크",
            "생산완료·포장대기 제외 · 위험 D-3 · 주의 D-7",
            filters,
        )
        card.setObjectName("RiskDashboardCard")
        list_area = QFrame()
        list_area.setObjectName("RiskListArea")
        list_layout = QVBoxLayout(list_area)
        list_layout.setContentsMargins(8, 8, 8, 8)
        list_layout.addWidget(
            DashboardEmptyState(
                "fa6s.bell",
                "리스크 알람 연결 전",
                "납기가 임박했지만 생산이 완료되지 않은 수주만 우선순위 순으로 표시합니다.",
            )
        )
        card_layout.addWidget(list_area, 1)

        watcher = QLabel("생산 미완료 감시  ·  대상 0건  ·  생산완료 및 포장대기 건은 제외")
        watcher.setObjectName("RiskWatcher")
        card_layout.addWidget(watcher)
        card.setMinimumHeight(398)
        return card

    def _filter_button(self, text: str, checked: bool) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("DashboardFilter")
        button.setCheckable(True)
        button.setChecked(checked)
        button.setCursor(Qt.PointingHandCursor)
        return button

    def _configure_dashboard_table(self, table: QTableWidget) -> None:
        table.setObjectName("DashboardTable")
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.horizontalHeader().setMinimumSectionSize(68)

    def _build_aps_shortage_card(self) -> QFrame:
        return self._build_live_aps_shortage_card()
        filters = QWidget()
        filters_layout = QHBoxLayout(filters)
        filters_layout.setContentsMargins(0, 0, 0, 0)
        filters_layout.setSpacing(5)
        filters_layout.addWidget(self._filter_button("국내", True))
        filters_layout.addWidget(self._filter_button("해외", True))
        filters_layout.addWidget(self._filter_button("안전재고", False))

        card, card_layout = self._dashboard_card_header(
            f"APS {self.active_factory} 공정별 부족수량",
            "생산3팀 기준 · 단위: pcs",
            filters,
        )
        freshness = QLabel("APS 원천 갱신  --/-- --:--  ·  오전 기준")
        freshness.setObjectName("FreshnessLabel")
        card_layout.addWidget(freshness)
        table = QTableWidget(1, 7)
        self._configure_dashboard_table(table)
        table.setHorizontalHeaderLabels(("관", "사출", "분리", "하이드레이션", "접착검사", "누수규격", "포장"))
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setFixedHeight(73)
        table.setItem(0, 0, QTableWidgetItem(self.active_factory))
        for column in range(1, 7):
            item = QTableWidgetItem("-")
            item.setTextAlignment(Qt.AlignCenter)
            table.setItem(0, column, item)
        card_layout.addWidget(table)
        return card

    def _build_requirement_chart_card(self) -> QFrame:
        return self._build_live_requirement_chart_card()
        card, card_layout = self._dashboard_card_header(
            f"{self.active_factory} 신규분류요약 기준 공정별 필요수량",
            "생산3팀 APS 필요수량 · 신규분류요약별 비교",
        )
        legend = QHBoxLayout()
        legend.setSpacing(12)
        for text, tone in (("●  Clear", "blue"), ("●  Color", "purple"), ("—  전체 필요수량", "neutral")):
            label = QLabel(text)
            label.setObjectName("ChartLegend")
            label.setProperty("tone", tone)
            legend.addWidget(label)
        legend.addStretch()
        card_layout.addLayout(legend)
        card_layout.addWidget(EmptyChartCanvas(horizontal=True), 1)
        card.setMinimumHeight(250)
        return card

    def _build_process_matrix_card(self) -> QFrame:
        return self._build_live_process_matrix_card()
        card, card_layout = self._dashboard_card_header(
            f"{self.active_factory} 공정별 수율·위험수량 요약",
            "생산3팀 Clear / Color 구분과 신규분류 위험수량",
        )
        table = QTableWidget(4, 7)
        self._configure_dashboard_table(table)
        table.setHorizontalHeaderLabels(("구분", "사출", "분리", "하이드레이션", "검사·접착", "누수·규격", "종합"))
        row_names = ("Clear 수율", "Color 수율", "종합 수율", "위험수량")
        for row, name in enumerate(row_names):
            table.setItem(row, 0, QTableWidgetItem(name))
            for column in range(1, 7):
                item = QTableWidgetItem("-")
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row, column, item)
        table.setMinimumHeight(180)
        card_layout.addWidget(table, 1)
        card.setMinimumHeight(255)
        return card

    def _build_performance_chart_card(self) -> QFrame:
        return self._build_live_performance_chart_card()
        card, card_layout = self._dashboard_card_header(
            f"{self.active_factory} 공정별 생산수량 및 수율",
            "생산3팀 당월 생산수량 막대 · 공정 수율 선",
        )
        legend = QHBoxLayout()
        legend.setSpacing(12)
        for text, tone in (("■  생산수량", "blue"), ("●  수율", "gold")):
            label = QLabel(text)
            label.setObjectName("ChartLegend")
            label.setProperty("tone", tone)
            legend.addWidget(label)
        legend.addStretch()
        period = QLabel("당월 기준")
        period.setObjectName("MiniBadge")
        legend.addWidget(period)
        card_layout.addLayout(legend)
        card_layout.addWidget(EmptyChartCanvas(), 1)
        card.setMinimumHeight(255)
        return card

    def _build_live_risk_dashboard_card(self) -> QFrame:
        return self._build_filterable_risk_dashboard_card()
        risks = self.dashboard_data.get("risks", [])
        danger_count = sum(1 for row in risks if row.get("tone") == "danger")
        warning_count = len(risks) - danger_count
        filters = QWidget()
        filters_layout = QHBoxLayout(filters)
        filters_layout.setContentsMargins(0, 0, 0, 0)
        filters_layout.setSpacing(5)
        for text, tone in ((f"전체 {len(risks)}건", "blue"), (f"위험 {danger_count}건", "pink"), (f"주의 {warning_count}건", "purple")):
            filters_layout.addWidget(self._count_chip(text, tone))
        card, card_layout = self._dashboard_card_header(
            "리스크 알림",
            "생산완료·포장대기 제외 · 위험 D-3 · 주의 D-7",
            filters,
        )
        card.setObjectName("RiskDashboardCard")
        list_area = QFrame()
        list_area.setObjectName("RiskListArea")
        list_layout = QVBoxLayout(list_area)
        list_layout.setContentsMargins(8, 8, 8, 8)
        list_layout.setSpacing(6)
        if risks:
            for row in risks[:7]:
                item = QFrame()
                item.setObjectName("RiskItem")
                item_layout = QVBoxLayout(item)
                item_layout.setContentsMargins(10, 7, 10, 7)
                item_layout.setSpacing(2)
                top = QHBoxLayout()
                badge = QLabel("위험" if row["tone"] == "danger" else "주의")
                badge.setObjectName("RiskBadge")
                badge.setProperty("tone", row["tone"])
                order = QLabel(f"{row['initial']}  {row['order_no']}")
                order.setObjectName("RiskOrder")
                due = QLabel(f"납기 {row['due']} · {row['due_label']}")
                due.setObjectName("RiskDue")
                top.addWidget(badge)
                top.addWidget(order)
                top.addStretch()
                top.addWidget(due)
                item_layout.addLayout(top)
                detail = QLabel(f"{row['classification']} · 생산 필요수량 {row['risk_qty']:,.0f} pcs")
                detail.setObjectName("RiskDetail")
                item_layout.addWidget(detail)
                list_layout.addWidget(item)
            list_layout.addStretch()
        else:
            list_layout.addWidget(DashboardEmptyState("fa6s.bell", "생산 미완료 리스크 없음", "D-7 이내 생산 미완료 수주가 없습니다."))
        card_layout.addWidget(list_area, 1)
        watcher = QLabel(f"생산 미완료 감시 · 대상 {len(risks)}건 · 생산완료 및 포장대기 건 제외")
        watcher.setObjectName("RiskWatcher")
        card_layout.addWidget(watcher)
        card.setMinimumHeight(398)
        return card

    def _build_filterable_risk_dashboard_card(self) -> QFrame:
        filters = QWidget()
        filters_layout = QHBoxLayout(filters)
        filters_layout.setContentsMargins(0, 0, 0, 0)
        filters_layout.setSpacing(8)
        self.risk_channel_checks: dict[str, QCheckBox] = {}
        for channel, checked in (("국내", False), ("해외", True)):
            check = QCheckBox(channel)
            check.setObjectName("RiskChannelCheck")
            check.setChecked(checked)
            self.risk_channel_checks[channel] = check
            filters_layout.addWidget(check)
        card, card_layout = self._dashboard_card_header(
            "리스크 알림",
            "납기일 기준 · 생산완료·포장대기 제외 · 위험 D-3 · 주의 D-7",
            filters,
        )
        card.setObjectName("RiskDashboardCard")
        self.risk_count_summary = QLabel()
        self.risk_count_summary.setObjectName("RiskCountSummary")
        card_layout.addWidget(self.risk_count_summary)

        scroll = QScrollArea()
        scroll.setObjectName("RiskScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.risk_list_widget = QWidget()
        self.risk_list_widget.setObjectName("RiskListWidget")
        self.risk_list_layout = QVBoxLayout(self.risk_list_widget)
        self.risk_list_layout.setContentsMargins(6, 6, 6, 6)
        self.risk_list_layout.setSpacing(6)
        scroll.setWidget(self.risk_list_widget)
        card_layout.addWidget(scroll, 1)
        self.risk_watcher = QLabel()
        self.risk_watcher.setObjectName("RiskWatcher")
        card_layout.addWidget(self.risk_watcher)
        for check in self.risk_channel_checks.values():
            check.toggled.connect(self._refresh_risk_alerts)
        self._refresh_risk_alerts()
        card.setMinimumHeight(430)
        return card

    def _clear_layout_widgets(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _refresh_risk_alerts(self) -> None:
        if not hasattr(self, "risk_list_layout"):
            return
        enabled = {channel for channel, check in self.risk_channel_checks.items() if check.isChecked()}
        all_risks = [
            row
            for row in self.dashboard_data.get("risks", [])
            if row.get("channel") in {"국내", "해외"}
        ]
        risks = [row for row in all_risks if row.get("channel") in enabled]
        danger_count = sum(1 for row in risks if row.get("tone") == "danger")
        warning_count = len(risks) - danger_count
        self.risk_count_summary.setText(
            f"전체 {len(all_risks)}건   ·   위험 {danger_count}건   ·   주의 {warning_count}건"
        )
        self._clear_layout_widgets(self.risk_list_layout)
        self.risk_item_cards: list[QWidget] = []
        if not risks:
            empty = DashboardEmptyState("fa6s.bell", "조건에 맞는 리스크 없음", "선택한 구분의 D-7 이내 생산 미완료 수주가 없습니다.")
            self.risk_list_layout.addWidget(empty)
        else:
            for row in risks:
                item = ClickableFrame()
                item.setObjectName("RiskItem")
                item.setProperty("orderNo", row["order_no"])
                item.setProperty(
                    "selected",
                    str(row["order_no"]) == str(getattr(self, "active_risk_order_no", "")),
                )
                self.risk_item_cards.append(item)
                item.pressed.connect(
                    lambda order_no=row["order_no"]: self._show_order_detail(order_no)
                )
                item.right_pressed.connect(
                    lambda global_pos, risk_row=dict(row): self._show_risk_context_menu(risk_row, global_pos)
                )
                item_layout = QVBoxLayout(item)
                item_layout.setContentsMargins(10, 7, 10, 7)
                item_layout.setSpacing(2)
                top = QHBoxLayout()
                badge = QLabel("위험" if row["tone"] == "danger" else "주의")
                badge.setObjectName("RiskBadge")
                badge.setProperty("tone", row["tone"])
                channel = QLabel(row.get("channel") or "-")
                channel.setObjectName("RiskChannelBadge")
                order = QLabel(f"{row['initial']}  {row['order_no']}")
                order.setObjectName("RiskOrder")
                due = QLabel(f"납기 {row['due']} · {row['due_label']}")
                due.setObjectName("RiskDue")
                top.addWidget(badge)
                top.addWidget(channel)
                top.addWidget(order)
                top.addStretch()
                top.addWidget(due)
                item_layout.addLayout(top)
                detail = QLabel(f"{row['classification']} · 생산 필요수량 {row['risk_qty']:,.0f} pcs")
                detail.setObjectName("RiskDetail")
                item_layout.addWidget(detail)
                tooltip = (
                    f"수주번호  {row['order_no']}\n"
                    f"이니셜  {row['initial'] or '-'} · 구분  {row.get('channel') or '-'}\n"
                    f"신규분류요약  {row['classification'] or '-'}\n"
                    f"납기일  {row['due']} · {row['due_label']}\n"
                    f"생산 필요수량  {float(row['risk_qty'] or 0) / 1_000:,.1f} kpcs\n"
                    "좌클릭: 수주 상세 · 우클릭: 실행할 기능 선택"
                )
                for tooltip_widget in (item, badge, channel, order, due, detail):
                    tooltip_widget.setToolTip(tooltip)
                    if tooltip_widget is not item:
                        tooltip_widget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                self.risk_list_layout.addWidget(item)
            self.risk_list_layout.addStretch()
        self.risk_watcher.setText(
            f"생산 미완료 감시 · 대상 {len(risks)}건 · 안전재고 제외 · 납기일 오름차순"
        )
        # 리스크 필터는 왼쪽 알림 목록에만 적용합니다.

    def _show_risk_context_menu(self, risk_row: dict, global_pos: object) -> None:
        initial = str(risk_row.get("initial") or "").strip() or "이니셜 없음"
        order_no = str(risk_row.get("order_no") or "").strip()
        menu = QMenu(self)
        menu.setObjectName("RiskContextMenu")
        menu.setMinimumWidth(360)
        menu.setStyleSheet("""
            QMenu#RiskContextMenu {
                color: #24344D;
                background: #FFFFFF;
                border: 1px solid #CCD8E6;
                border-radius: 12px;
                padding: 8px;
            }
            QMenu#RiskContextMenu::item {
                min-height: 24px;
                padding: 8px 14px 8px 38px;
                margin: 2px 0;
                border-radius: 8px;
                font-weight: 700;
            }
            QMenu#RiskContextMenu::item:selected {
                color: #075CCF;
                background: #E8F2FF;
            }
            QMenu#RiskContextMenu::item:disabled {
                color: #183153;
                background: #F3F7FC;
                padding-left: 14px;
                font-weight: 800;
            }
            QMenu#RiskContextMenu::separator {
                height: 1px;
                background: #E4EAF1;
                margin: 6px 8px;
            }
            QMenu#RiskContextMenu::icon {
                padding-left: 12px;
            }
        """)
        heading = menu.addAction(f"{initial}  ·  {order_no}")
        heading.setEnabled(False)
        menu.addSeparator()
        detail_action = menu.addAction(
            qta.icon("fa6s.file-lines", color="#52677E"),
            "수주 상세 보기",
        )
        process_action = menu.addAction(
            qta.icon("fa6s.layer-group", color="#0A7AFF"),
            "공정현황에서 수주번호 검색",
        )
        selected_action = menu.exec(global_pos)
        if selected_action is detail_action:
            self._show_order_detail(order_no)
        elif selected_action is process_action:
            self._open_risk_in_process_overview(risk_row)

    def _open_risk_in_process_overview(self, risk_row: dict) -> None:
        order_no = str(risk_row.get("order_no") or "").strip()
        if not order_no:
            return
        self.show_page("process_overview")
        self.process_overview_page.search_from_risk(order_no)

    def _refresh_dashboard_channel_metrics(self, enabled: set[str] | None = None) -> None:
        if enabled is None:
            enabled = {
                channel for channel, check in getattr(self, "aps_channel_checks", {}).items()
                if check.isChecked()
            }
        shortage = {name: 0.0 for name in ProcessMetricChart.PROCESS_NAMES}
        clear = {name: 0.0 for name in ProcessMetricChart.PROCESS_NAMES}
        color = {name: 0.0 for name in ProcessMetricChart.PROCESS_NAMES}
        tooltip_detail = {
            name: {"clear": {}, "color": {}}
            for name in ProcessMetricChart.PROCESS_NAMES
        }
        shortage_by_channel = self.dashboard_data.get("shortage_by_channel", {})
        requirement_by_channel = self.dashboard_data.get("requirement_by_channel", {})
        detail_by_channel = self.dashboard_data.get("requirement_detail_by_channel", {})
        for channel in enabled:
            for name in shortage:
                shortage[name] += float(shortage_by_channel.get(channel, {}).get(name, 0) or 0)
                clear[name] += float(requirement_by_channel.get(channel, {}).get("clear", {}).get(name, 0) or 0)
                color[name] += float(requirement_by_channel.get(channel, {}).get("color", {}).get(name, 0) or 0)
                for lens_key in ("clear", "color"):
                    for classification, quantity in (
                        detail_by_channel.get(channel, {}).get(lens_key, {}).get(name, {}).items()
                    ):
                        target = tooltip_detail[name][lens_key]
                        target[classification] = target.get(classification, 0.0) + float(quantity or 0)
        if hasattr(self, "aps_shortage_items"):
            for name, item in self.aps_shortage_items.items():
                item.setText(f"{shortage[name]:,.0f}")
        if hasattr(self, "requirement_chart"):
            self.requirement_chart.set_data(clear, color)
            self.requirement_chart.set_tooltip_data(tooltip_detail)
        if hasattr(self, "process_matrix_table"):
            self._populate_process_matrix_table()

    def _build_order_detail_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("OrderDetailPanel")
        panel.setAttribute(Qt.WA_StyledBackground, True)
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(16, 14, 16, 14)
        panel_layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("수주 상세")
        title.setObjectName("OrderDetailTitle")
        close_button = QPushButton("×")
        close_button.setObjectName("OrderDetailClose")
        close_button.setFixedSize(32, 32)
        close_button.clicked.connect(self._close_order_detail)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close_button)
        panel_layout.addLayout(header)
        self.order_detail_heading = QLabel("리스크 알림을 선택해 주세요.")
        self.order_detail_heading.setObjectName("OrderDetailHeading")
        panel_layout.addWidget(self.order_detail_heading)
        self.order_detail_summary = QLabel("수주 요약과 제품별 진행상태, 공정별 부족수량 합계를 표시합니다.")
        self.order_detail_summary.setObjectName("OrderDetailSummary")
        self.order_detail_summary.setWordWrap(True)
        panel_layout.addWidget(self.order_detail_summary)
        info_card = QFrame()
        info_card.setObjectName("OrderDetailInfoCard")
        info_grid = QGridLayout(info_card)
        info_grid.setContentsMargins(14, 11, 14, 11)
        info_grid.setHorizontalSpacing(26)
        info_grid.setVerticalSpacing(9)
        self.order_detail_info_values: dict[str, QLabel] = {}
        info_fields = (
            ("납기일", "due_date"),
            ("거래처", "cust_name"),
            ("이니셜", "initial"),
            ("국가", "dest_country"),
            ("구분", "demand_type"),
            ("공장", "factory"),
        )
        for index, (label_text, key) in enumerate(info_fields):
            field = QWidget()
            field_layout = QVBoxLayout(field)
            field_layout.setContentsMargins(0, 0, 0, 0)
            field_layout.setSpacing(3)
            label = QLabel(label_text)
            label.setObjectName("OrderDetailInfoLabel")
            value = QLabel("-")
            value.setObjectName("OrderDetailInfoValue")
            value.setWordWrap(True)
            field_layout.addWidget(label)
            field_layout.addWidget(value)
            info_grid.addWidget(field, index // 2, index % 2)
            self.order_detail_info_values[key] = value
        info_grid.setColumnStretch(0, 1)
        info_grid.setColumnStretch(1, 1)
        section = QLabel("제품별 진행상태")
        section.setObjectName("OrderDetailSection")
        scroll = QScrollArea()
        scroll.setObjectName("OrderDetailScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_body = QWidget()
        scroll_layout = QVBoxLayout(scroll_body)
        scroll_layout.setContentsMargins(0, 0, 4, 0)
        scroll_layout.setSpacing(10)
        scroll_layout.addWidget(info_card)
        scroll_layout.addWidget(section)
        self.order_item_widget = QWidget()
        self.order_item_layout = QVBoxLayout(self.order_item_widget)
        self.order_item_layout.setContentsMargins(0, 0, 2, 0)
        self.order_item_layout.setSpacing(8)
        scroll_layout.addWidget(self.order_item_widget, 1)
        scroll.setWidget(scroll_body)
        self.order_detail_scroll = scroll
        panel_layout.addWidget(scroll, 1)
        return panel

    def _show_order_detail(self, order_no: str) -> None:
        # 상세 페이지를 먼저 고정해 DB 조회 중 오른쪽 그래프가 순간 노출되지 않게 합니다.
        self._set_active_risk_card(order_no)
        self.order_detail_scroll.verticalScrollBar().setValue(0)
        if self.dashboard_right_stack.currentWidget() is not self.order_detail_panel:
            self.order_detail_heading.setText(f"{order_no} · 수주 정보 확인 중")
            self.order_detail_summary.setText("수주 상세 정보를 불러오고 있습니다.")
            for value in self.order_detail_info_values.values():
                value.setText("-")
            self._clear_layout_widgets(self.order_item_layout)
            self.dashboard_right_stack.setCurrentWidget(self.order_detail_panel)
            self.order_detail_panel.repaint()
        detail = self.dashboard_service.order_details(order_no)
        order = detail.get("order", {})
        products = detail.get("products", [])
        process_totals = detail.get("process_totals", {})
        if not order:
            self.order_detail_heading.setText(f"{order_no} · 상세 정보 없음")
            self.order_detail_summary.setText("해당 수주의 상세 데이터를 찾지 못했습니다.")
            return
        self.order_detail_heading.setText(f"{order_no} · 제품 {int(order.get('item_count') or 0):,}종")
        self.order_detail_summary.setText(
            f"수주수량 {float(order.get('order_qty') or 0):,.0f} pcs · "
            f"생산 필요수량 {float(order.get('remaining_qty') or 0):,.0f} pcs · 포장 제외"
        )
        info_values = {
            "due_date": order.get("due_date") or "-",
            "cust_name": order.get("cust_name") or "-",
            "initial": order.get("initial") or "-",
            "dest_country": order.get("dest_country") or "-",
            "demand_type": order.get("demand_type") or "-",
            "factory": "S관(3공장)",
        }
        for key, value in info_values.items():
            self.order_detail_info_values[key].setText(str(value))
        self._clear_layout_widgets(self.order_item_layout)
        process_names = ("사출", "분리", "하이드레이션", "검사·접착", "누수·규격")
        for index, row in enumerate(products, 1):
            card = QFrame()
            card.setObjectName("OrderItemCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 9, 10, 9)
            card_layout.setSpacing(7)
            top = QHBoxLayout()
            number = QLabel(str(index))
            number.setObjectName("OrderItemNumber")
            number.setAlignment(Qt.AlignCenter)
            number.setFixedSize(25, 25)
            name = QLabel(f"{row.get('classification') or '-'} · {row.get('item_name') or '-'}")
            name.setObjectName("OrderItemName")
            name.setWordWrap(True)
            top.addWidget(number)
            top.addWidget(name, 1)
            card_layout.addLayout(top)
            quantity = QLabel(
                f"수주 {float(row.get('order_qty') or 0):,.0f} pcs · "
                f"규격 {int(row.get('spec_count') or 0):,}개"
            )
            quantity.setObjectName("OrderItemQuantity")
            card_layout.addWidget(quantity)
            process_grid = QGridLayout()
            process_grid.setHorizontalSpacing(5)
            for column, process_name in enumerate(process_names):
                process_label = QLabel(process_name)
                process_label.setObjectName("OrderProcessName")
                process_label.setAlignment(Qt.AlignCenter)
                shortage = float(row.get(process_name) or 0)
                value = QLabel("완료" if shortage <= 0 else f"{shortage:,.0f} 부족")
                value.setObjectName("OrderProcessStatus")
                value.setProperty("state", "complete" if shortage <= 0 else "shortage")
                value.setAlignment(Qt.AlignCenter)
                process_grid.addWidget(process_label, 0, column)
                process_grid.addWidget(value, 1, column)
            card_layout.addLayout(process_grid)
            self.order_item_layout.addWidget(card)

        totals_title = QLabel("공정별 부족수량 합계")
        totals_title.setObjectName("OrderDetailSection")
        self.order_item_layout.addWidget(totals_title)
        totals_card = QFrame()
        totals_card.setObjectName("OrderProcessTotalsCard")
        totals_grid = QGridLayout(totals_card)
        totals_grid.setContentsMargins(10, 10, 10, 10)
        totals_grid.setHorizontalSpacing(6)
        for column, process_name in enumerate(process_names):
            process_label = QLabel(process_name)
            process_label.setObjectName("OrderProcessName")
            process_label.setAlignment(Qt.AlignCenter)
            value = QLabel(f"{float(process_totals.get(process_name) or 0):,.0f} pcs")
            value.setObjectName("OrderProcessTotal")
            value.setAlignment(Qt.AlignCenter)
            totals_grid.addWidget(process_label, 0, column)
            totals_grid.addWidget(value, 1, column)
        self.order_item_layout.addWidget(totals_card)
        self.order_item_layout.addStretch()
        self.order_detail_scroll.verticalScrollBar().setValue(0)
        QTimer.singleShot(0, lambda: self.order_detail_scroll.verticalScrollBar().setValue(0))

    def _close_order_detail(self) -> None:
        if hasattr(self, "dashboard_right_stack"):
            self.dashboard_right_stack.setCurrentWidget(self.dashboard_summary_panel)
        self._set_active_risk_card(None)

    def _set_active_risk_card(self, order_no: str | None) -> None:
        self.active_risk_order_no = order_no
        for card in getattr(self, "risk_item_cards", []):
            selected = str(card.property("orderNo") or "") == str(order_no or "")
            card.setProperty("selected", selected)
            card.style().unpolish(card)
            card.style().polish(card)

    @staticmethod
    def _is_within(widget: QWidget | None, ancestor: QWidget) -> bool:
        current = widget
        while current is not None:
            if current is ancestor:
                return True
            current = current.parentWidget()
        return False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.MouseButtonPress and isinstance(watched, QWidget):
            if event.button() == Qt.LeftButton:
                for card in getattr(self, "risk_item_cards", []):
                    if self._is_within(watched, card):
                        order_no = str(card.property("orderNo") or "")
                        if order_no:
                            self._show_order_detail(order_no)
                        # 자식 라벨에서 부모 스크롤영역으로 press가 전파되어
                        # 상세 패널을 다시 닫는 현상을 여기서 차단합니다.
                        return True
        detail_open = (
            hasattr(self, "dashboard_right_stack")
            and self.dashboard_right_stack.currentWidget() is getattr(self, "order_detail_panel", None)
        )
        if detail_open and event.type() == QEvent.MouseButtonPress and isinstance(watched, QWidget):
            inside_detail = self._is_within(watched, self.order_detail_panel)
            inside_risk = any(
                self._is_within(watched, card)
                for card in getattr(self, "risk_item_cards", [])
            )
            if not inside_detail and not inside_risk:
                QTimer.singleShot(0, self._close_order_detail)
        if detail_open and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            self._close_order_detail()
            return True
        return super().eventFilter(watched, event)

    def _build_live_aps_shortage_card(self) -> QFrame:
        filters = QWidget()
        filters_layout = QHBoxLayout(filters)
        filters_layout.setContentsMargins(0, 0, 0, 0)
        filters_layout.setSpacing(8)
        self.aps_channel_checks: dict[str, QCheckBox] = {}
        for channel, checked in (("국내", True), ("해외", True), ("안전재고", False)):
            check = QCheckBox(channel)
            check.setObjectName("RiskChannelCheck")
            check.setChecked(checked)
            self.aps_channel_checks[channel] = check
            filters_layout.addWidget(check)
        card, card_layout = self._dashboard_card_header(
            "공정별 부족수량(APS_S관)",
            "생산3팀 기준 · 단위: pcs",
            filters,
        )
        table = QTableWidget(1, 6)
        self._configure_dashboard_table(table)
        names = ("사출", "분리", "하이드레이션", "검사·접착", "누수·규격")
        table.setHorizontalHeaderLabels(("공장", *names))
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setFixedHeight(73)
        factory_item = QTableWidgetItem("S관")
        factory_item.setTextAlignment(Qt.AlignCenter)
        table.setItem(0, 0, factory_item)
        self.aps_shortage_items = {}
        shortage = self.dashboard_data.get("shortage", {})
        for column, name in enumerate(names, 1):
            item = QTableWidgetItem(f"{float(shortage.get(name, 0)):,.0f}")
            item.setTextAlignment(Qt.AlignCenter)
            table.setItem(0, column, item)
            self.aps_shortage_items[name] = item
        card_layout.addWidget(table)
        for check in self.aps_channel_checks.values():
            check.toggled.connect(lambda _checked=False: self._refresh_dashboard_channel_metrics())
        return card

    def _build_live_requirement_chart_card(self) -> QFrame:
        card, card_layout = self._dashboard_card_header(
            "신규분류요약 기준 공정별 부족수량(APS_S관)",
            "APS 부족수량 · 공정별 색상 · 막대 끝 총 kpcs · 호버 신규분류 상위 항목",
        )
        legend = QHBoxLayout()
        label = QLabel("진한색 Clear  ·  연한색 Color")
        label.setObjectName("ChartLegend")
        legend.addWidget(label)
        legend.addStretch()
        card_layout.addLayout(legend)
        self.requirement_chart = RequirementHorizontalChart(
            self.dashboard_data.get("requirement_clear", {}),
            self.dashboard_data.get("requirement_color", {}),
        )
        card_layout.addWidget(self.requirement_chart, 1)
        card.setMinimumHeight(250)
        return card

    def _build_live_process_matrix_card(self) -> QFrame:
        period_switches = QWidget()
        period_layout = QHBoxLayout(period_switches)
        period_layout.setContentsMargins(0, 0, 0, 0)
        period_layout.setSpacing(6)
        self.production_period_group = QButtonGroup(self)
        self.production_period_group.setExclusive(True)
        self.production_period_buttons: dict[str, QPushButton] = {}
        for text, period_key in (("당월", "current"), ("전월", "previous")):
            button = QPushButton(text)
            button.setObjectName("DashboardFilter")
            button.setCheckable(True)
            button.setChecked(period_key == self.production_period_key)
            self.production_period_group.addButton(button)
            self.production_period_buttons[period_key] = button
            period_layout.addWidget(button)
            button.clicked.connect(
                lambda _checked=False, selected=period_key: self._set_production_period(selected)
            )
        card, card_layout = self._dashboard_card_header(
            "생산실적",
            "선택 월 Clear·Color 공정수율 · 신규분류요약별 공정 양품실적",
            period_switches,
        )
        names = ("사출", "분리", "하이드레이션", "검사·접착", "누수·규격")
        table = QTableWidget(0, 7)
        self._configure_dashboard_table(table)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        table.setColumnWidth(0, 175)
        table.setHorizontalHeaderLabels(("구분", *names, "종합"))
        self.process_matrix_table = table
        self._populate_process_matrix_table()
        table.setMinimumHeight(180)
        card_layout.addWidget(table, 1)
        card.setMinimumHeight(255)
        return card

    def _selected_production_period(self) -> dict:
        periods = self.dashboard_data.get("production_periods", {})
        return periods.get(self.production_period_key) or periods.get("current") or {}

    def _production_period_badge_text(self, period: dict) -> str:
        year_month = str(period.get("year_month") or "-")
        confirmed = str(self.dashboard_data.get("production_confirmed_through") or "")
        if confirmed.startswith(year_month):
            return f"{year_month} · {confirmed[5:]} 확정"
        return f"{year_month} 확정"

    def _populate_process_matrix_table(self) -> None:
        if not hasattr(self, "process_matrix_table"):
            return
        table = self.process_matrix_table
        period = self._selected_production_period()
        names = ("사출", "분리", "하이드레이션", "검사·접착", "누수·규격")
        good_by_classification = period.get("classification_good", {})
        classifications = sorted(good_by_classification, key=classification_sort_key)
        process_totals = {
            name: sum(
                float(values.get(name, 0) or 0)
                for values in good_by_classification.values()
            )
            for name in names
        }
        table.clearContents()
        table.clearSpans()
        table.setRowCount(5 + len(classifications))
        metrics = (
            ("Clear", period.get("yield_clear", {}), True),
            ("Color", period.get("yield_color", {}), True),
            ("Total", period.get("yield", {}), True),
        )
        for row_index, (label, values, percentage) in enumerate(metrics):
            label_item = QTableWidgetItem(label)
            label_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(row_index, 0, label_item)
            numbers = [float(values.get(name, 0)) for name in names]
            for column, value in enumerate(numbers, 1):
                item = QTableWidgetItem(f"{value:.1f}%" if percentage else f"{value:,.0f}")
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row_index, column, item)
            if percentage:
                # 종합수율은 공정별 수율의 산술평균이 아니라 연속 공정 수율의 곱이다.
                total_ratio = 1.0
                for value in numbers:
                    total_ratio *= value / 100.0
                total = total_ratio * 100.0
            else:
                total = sum(numbers)
            total_item = QTableWidgetItem(f"{total:.1f}%" if percentage else f"{total:,.0f}")
            total_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(row_index, 6, total_item)
            if label == "Total":
                for column in range(7):
                    item = table.item(row_index, column)
                    item.setBackground(QColor("#F4F7FB"))
                    item_font = item.font()
                    item_font.setBold(True)
                    item.setFont(item_font)
        table.setRowHeight(3, 9)
        section_item = QTableWidgetItem("신규분류요약별")
        section_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        section_item.setBackground(QColor("#EAF3FF"))
        section_item.setForeground(QColor("#0A67D1"))
        section_font = section_item.font()
        section_font.setBold(True)
        section_item.setFont(section_font)
        table.setItem(4, 0, section_item)
        table.setRowHeight(4, 30)
        section_total_values = [process_totals[name] for name in names]
        section_total_values.append(sum(section_total_values))
        for column, value in enumerate(section_total_values, 1):
            total_item = QTableWidgetItem(f"{value / 1_000:,.0f}k")
            total_item.setTextAlignment(Qt.AlignCenter)
            total_item.setBackground(QColor("#EAF3FF"))
            total_item.setForeground(QColor("#0A67D1"))
            total_font = total_item.font()
            total_font.setBold(True)
            total_item.setFont(total_font)
            process_label = names[column - 1] if column <= len(names) else "종합"
            total_item.setToolTip(f"{process_label} 총 양품실적 {value:,.0f} pcs")
            table.setItem(4, column, total_item)
        for row_index, classification in enumerate(classifications, 5):
            values = good_by_classification.get(classification, {})
            classification_item = QTableWidgetItem(classification)
            classification_item.setToolTip(classification)
            table.setItem(row_index, 0, classification_item)
            numbers = [float(values.get(name, 0)) for name in names]
            for column, value in enumerate(numbers, 1):
                item = QTableWidgetItem(f"{value:,.0f}")
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row_index, column, item)
            total_item = QTableWidgetItem(f"{sum(numbers):,.0f}")
            total_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(row_index, 6, total_item)

    def _build_live_performance_chart_card(self) -> QFrame:
        card, card_layout = self._dashboard_card_header(
            "일자별 생산현황",
            "파랑: 생산실적 · 초록: 금일 생산실적(진행중) · 선: 종합수율",
        )
        legend = QHBoxLayout()
        self.performance_legend = QLabel(
            '<span style="color:#0A7AFF;font-weight:700">■ 생산실적</span>'
            '&nbsp;&nbsp;&nbsp;'
            '<span style="color:#20A66A;font-weight:700">■ 금일 생산실적(진행중)</span>'
            '&nbsp;&nbsp;&nbsp;'
            '<span style="color:#E69000;font-weight:700">● 종합수율</span>'
        )
        self.performance_legend.setObjectName("ChartLegend")
        legend.addWidget(self.performance_legend)
        legend.addStretch()
        self.performance_period_badge = QLabel()
        self.performance_period_badge.setObjectName("MiniBadge")
        self.performance_live_badge = QLabel(f"금일 {date.today().day}일 · 진행 중")
        self.performance_live_badge.setObjectName("LiveBadge")
        legend.addWidget(self.performance_live_badge)
        legend.addWidget(self.performance_period_badge)
        card_layout.addLayout(legend)
        period = self._selected_production_period()
        self.performance_period_badge.setText(self._production_period_badge_text(period))
        self.performance_live_badge.setVisible(
            str(period.get("year_month") or "") == date.today().strftime("%Y-%m")
        )
        self.performance_chart = ProductionTrendChart(period)
        card_layout.addWidget(self.performance_chart, 1)
        card.setMinimumHeight(255)
        return card

    def _set_performance_chart_mode(self, mode: str) -> None:
        return

    def _set_production_period(self, period_key: str) -> None:
        if period_key not in self.dashboard_data.get("production_periods", {}):
            return
        self.production_period_key = period_key
        self._populate_process_matrix_table()
        period = self._selected_production_period()
        if hasattr(self, "performance_chart"):
            self.performance_chart.set_period(period)
        if hasattr(self, "performance_period_badge"):
            self.performance_period_badge.setText(self._production_period_badge_text(period))
        if hasattr(self, "performance_live_badge"):
            self.performance_live_badge.setVisible(
                str(period.get("year_month") or "") == date.today().strftime("%Y-%m")
            )

    def _build_search_bar(self, placeholder: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("SearchCard")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        search = QLineEdit()
        search.setObjectName("SearchInput")
        search.setPlaceholderText(placeholder)
        search.setClearButtonEnabled(True)
        button = QPushButton("조회")
        button.setObjectName("PrimaryButton")
        button.setIcon(qta.icon("fa6s.magnifying-glass", color="#FFFFFF"))
        reset = QPushButton("초기화")
        reset.setObjectName("SecondaryButton")
        reset.clicked.connect(search.clear)
        layout.addWidget(search, 1)
        layout.addWidget(button)
        layout.addWidget(reset)
        return frame

    def _build_search_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(self._build_search_bar("수주번호, 품목코드, 판매코드, 품명 또는 이니셜 입력"))
        layout.addWidget(
            EmptyState(
                "fa6s.magnifying-glass-chart",
                "조회할 항목을 검색해 주세요",
                "검색 결과에서 APS 계획, 최근 생산실적과 BOM을 한 화면으로 연결할 예정입니다.",
            ),
            1,
        )
        return page

    def _build_process_page(self, process_name: str) -> QWidget:
        process_key = {
            "사출 공정": "사출",
            "분리 공정": "분리",
            "하이드레이션 공정": "하이드레이션",
            "검사·접착 공정": "접착",
            "누수·규격 공정": "누수규격",
        }[process_name]
        page = ProcessOverviewPage(
            fixed_process=process_key,
            initial_rows=self.process_overview_page.all_rows,
            monitor_changes=False,
        )
        page.process_requested.connect(self.show_page)
        if not hasattr(self, "fixed_process_pages"):
            self.fixed_process_pages = {}
        page_key = next(
            key for key in self.PROCESS_KEYS
            if self.page_definitions[key].title == process_name
        )
        self.fixed_process_pages[page_key] = page
        if page_key == "injection":
            self.injection_process_page = page
        return page

    def _build_process_overview_page(self) -> QWidget:
        self.process_overview_page = ProcessOverviewPage()
        self.process_overview_page.process_requested.connect(self.show_page)
        self.process_service = self.process_overview_page.service
        return self.process_overview_page

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.process_service = ProcessStatusService()

        filters = QFrame()
        filters.setObjectName("SearchCard")
        filter_layout = QHBoxLayout(filters)
        filter_layout.setContentsMargins(16, 12, 16, 12)
        filter_layout.setSpacing(8)
        fixed_factory = QLabel("S관(3공장)")
        fixed_factory.setObjectName("FixedFactoryChip")
        process_label = QLabel("공정 보기")
        process_label.setObjectName("FilterLabel")
        self.process_filter = QComboBox()
        self.process_filter.addItems(("전체", "사출", "분리", "하이드레이션", "접착", "누수규격", "포장"))
        self.process_filter.setMinimumWidth(125)
        self.process_search = QLineEdit()
        self.process_search.setObjectName("SearchInput")
        self.process_search.setPlaceholderText("수주번호·이니셜·품명·품목코드·신규분류요약")
        self.process_search.setClearButtonEnabled(True)
        search_button = QPushButton("조회")
        search_button.setObjectName("PrimaryButton")
        search_button.setIcon(qta.icon("fa6s.magnifying-glass", color="#FFFFFF"))
        search_button.clicked.connect(self._reload_process_status)
        self.process_search.returnPressed.connect(self._reload_process_status)
        self.process_filter.currentTextChanged.connect(lambda _: self._reload_process_status())
        filter_layout.addWidget(QLabel("관별"))
        filter_layout.addWidget(fixed_factory)
        filter_layout.addSpacing(12)
        filter_layout.addWidget(process_label)
        filter_layout.addWidget(self.process_filter)
        filter_layout.addWidget(self.process_search, 1)
        filter_layout.addWidget(search_button)
        layout.addWidget(filters)

        kpi_layout = QHBoxLayout()
        kpi_layout.setSpacing(12)
        self.process_kpi_values: dict[str, QLabel] = {}
        kpis = (
            ("진행대상", "진행 대상 수주", "#0A7AFF"),
            ("포장진행", "포장 진행 수주", "#F59E0B"),
            ("생산미완료", "생산 미완료 수주", "#7C3AED"),
            ("납기위험", "납기 위험 수주", "#DC2626"),
        )
        for key, title, color in kpis:
            card = QFrame()
            card.setObjectName("KpiCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(18, 13, 18, 13)
            caption = QLabel(f"●  {title}")
            caption.setObjectName("KpiTitle")
            caption.setStyleSheet(f"color:{color};")
            value = QLabel("-")
            value.setObjectName("KpiValue")
            card_layout.addWidget(caption)
            card_layout.addWidget(value)
            self.process_kpi_values[key] = value
            kpi_layout.addWidget(card, 1)
        layout.addLayout(kpi_layout)

        table_card = QFrame()
        table_card.setObjectName("Card")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(14, 12, 14, 14)
        title_row = QHBoxLayout()
        title = QLabel("납기별 상세")
        title.setObjectName("CardTitle")
        self.process_snapshot_note = QLabel()
        self.process_snapshot_note.setObjectName("CardSub")
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.process_snapshot_note)
        table_layout.addLayout(title_row)
        columns = ("신규분류요약", "이니셜", "수주번호", "품명", "POWER", "납기일", "사출", "분리", "하이드레이션", "접착", "누수규격", "포장")
        self.process_table = QTableWidget(0, len(columns))
        self.process_table.setObjectName("DataTable")
        self.process_table.setHorizontalHeaderLabels(columns)
        self.process_table.setAlternatingRowColors(True)
        self.process_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.process_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.process_table.verticalHeader().setVisible(False)
        header = self.process_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.process_table.setColumnWidth(0, 150)
        self.process_table.setColumnWidth(2, 140)
        table_layout.addWidget(self.process_table, 1)
        layout.addWidget(table_card, 1)
        self._reload_process_status()
        return page

    def _reload_process_status(self) -> None:
        rows = self.process_service.load_rows(
            self.process_search.text() if hasattr(self, "process_search") else "",
            self.process_filter.currentText() if hasattr(self, "process_filter") else "전체",
        )
        summary = self.process_service.summary(rows)
        for key, label in self.process_kpi_values.items():
            label.setText(f"{int(summary.get(key) or 0):,}건")
        status = self.process_service.status()
        self.process_snapshot_note.setText(
            f"{len(rows):,}행 · APS 원천 갱신 {status.get('source_refreshed_at') or '-'} · S관 고정"
        )
        columns = ("신규분류요약", "이니셜", "수주번호", "품명", "POWER", "납기일", "사출", "분리", "하이드레이션", "접착", "누수규격", "포장")
        self.process_table.setSortingEnabled(False)
        self.process_table.setRowCount(len(rows))
        numeric = set(columns[6:])
        for row_index, row in enumerate(rows):
            for column_index, key in enumerate(columns):
                value = row.get(key, "")
                text = f"{float(value):,.0f}" if key in numeric and value not in (None, "") else str(value or "-")
                item = QTableWidgetItem(text)
                if key in numeric:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.process_table.setItem(row_index, column_index, item)
        self.process_table.setSortingEnabled(True)

    @staticmethod
    def _progress_filter_button(text: str, checked: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("ProgressFilterButton")
        button.setCheckable(True)
        button.setChecked(checked)
        button.setMinimumHeight(36)
        button.setStyleSheet(
            "QPushButton{padding:0 14px;border:1px solid #D6DFEA;border-radius:9px;background:#FFF;color:#17233B;}"
            "QPushButton:hover{border-color:#74AFFF;background:#F6FAFF;}"
            "QPushButton:checked{border-color:#0A7AFF;background:#EAF3FF;color:#0068E8;font-weight:700;}"
        )
        return button

    def _build_control_tower_progress_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.process_service = ProcessStatusService()
        self.progress_all_rows = self.process_service.load_rows()

        primary = QFrame()
        primary.setObjectName("SearchCard")
        primary_layout = QHBoxLayout(primary)
        primary_layout.setContentsMargins(14, 11, 14, 11)
        primary_layout.setSpacing(8)
        primary_layout.addWidget(QLabel("진행현황"))
        self.progress_market_buttons: dict[str, QPushButton] = {}
        for name in ("전체", "해외", "PB", "국내", "안전"):
            button = self._progress_filter_button(name, name == "전체")
            button.clicked.connect(lambda checked=False, value=name: self._progress_market_changed(value))
            self.progress_market_buttons[name] = button
            primary_layout.addWidget(button)
        primary_layout.addSpacing(14)
        primary_layout.addWidget(QLabel("관별"))
        factory = QLabel("S관(3공장)")
        factory.setObjectName("FixedFactoryChip")
        factory.setAlignment(Qt.AlignCenter)
        factory.setMinimumWidth(108)
        primary_layout.addWidget(factory)
        primary_layout.addStretch()
        primary_layout.addWidget(QLabel("통합검색"))
        self.progress_global_search = QLineEdit()
        self.progress_global_search.setObjectName("SearchInput")
        self.progress_global_search.setPlaceholderText("수주번호·이니셜·고객·제품")
        self.progress_global_search.setMinimumWidth(220)
        self.progress_global_search.returnPressed.connect(self._apply_progress_filters)
        primary_layout.addWidget(self.progress_global_search)
        self.progress_result_chip = QLabel()
        self.progress_result_chip.setObjectName("SnapshotGood")
        self.progress_result_chip.setMinimumWidth(160)
        self.progress_result_chip.setAlignment(Qt.AlignCenter)
        primary_layout.addWidget(self.progress_result_chip)
        lookup = QPushButton("조회")
        lookup.setObjectName("PrimaryButton")
        lookup.setIcon(qta.icon("fa6s.magnifying-glass", color="#FFFFFF"))
        lookup.clicked.connect(self._apply_progress_filters)
        primary_layout.addWidget(lookup)
        layout.addWidget(primary)

        kpi_layout = QHBoxLayout()
        kpi_layout.setSpacing(12)
        self.process_kpi_values = {}
        for key, title, color in (
            ("진행대상", "진행 대상 수주", "#0A7AFF"),
            ("포장진행", "포장 진행 수주", "#F59E0B"),
            ("생산미완료", "생산 미완료 수주", "#7C3AED"),
            ("납기위험", "납기 위험 수주", "#DC2626"),
        ):
            card = QFrame()
            card.setObjectName("KpiCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(18, 13, 18, 13)
            caption = QLabel(f"●  {title}")
            caption.setObjectName("KpiTitle")
            caption.setStyleSheet(f"color:{color};")
            value = QLabel("-")
            value.setObjectName("KpiValue")
            card_layout.addWidget(caption)
            card_layout.addWidget(value)
            self.process_kpi_values[key] = value
            kpi_layout.addWidget(card, 1)
        layout.addLayout(kpi_layout)

        filters = QFrame()
        filters.setObjectName("SearchCard")
        filters_layout = QVBoxLayout(filters)
        filters_layout.setContentsMargins(14, 10, 14, 10)
        filters_layout.setSpacing(8)
        due_row = QHBoxLayout()
        due_row.setSpacing(7)
        due_row.addWidget(QLabel("납기"))
        self.progress_due_group = QButtonGroup(self)
        self.progress_due_group.setExclusive(True)
        for index, name in enumerate(("해제", "직접", "당월", "+7일", "+14일")):
            button = self._progress_filter_button(name, index == 0)
            button.setProperty("dueMode", name)
            button.clicked.connect(self._progress_due_changed)
            self.progress_due_group.addButton(button)
            due_row.addWidget(button)
        self.progress_due_date = QDateEdit(QDate.currentDate())
        self.progress_due_date.setCalendarPopup(True)
        self.progress_due_date.setDisplayFormat("yyyy-MM-dd")
        self.progress_due_date.setEnabled(False)
        self.progress_due_date.dateChanged.connect(self._apply_progress_filters)
        due_row.addWidget(self.progress_due_date)
        due_row.addSpacing(10)
        due_row.addWidget(QLabel("분류"))
        self.progress_classification = QComboBox()
        self.progress_classification.addItem("전체")
        classes = sorted(
            {str(row.get("신규분류요약") or "") for row in self.progress_all_rows if row.get("신규분류요약")},
            key=classification_sort_key,
        )
        self.progress_classification.addItems(classes)
        self.progress_classification.setMinimumWidth(170)
        self.progress_classification.currentTextChanged.connect(self._apply_progress_filters)
        due_row.addWidget(self.progress_classification)
        self.progress_detail_search = QLineEdit()
        self.progress_detail_search.setObjectName("SearchInput")
        self.progress_detail_search.setPlaceholderText("품명·이니셜·수주번호·품목코드")
        self.progress_detail_search.textChanged.connect(self._apply_progress_filters)
        due_row.addWidget(self.progress_detail_search, 1)
        filters_layout.addLayout(due_row)

        display_row = QHBoxLayout()
        display_row.setSpacing(7)
        display_row.addWidget(QLabel("공정 보기"))
        self.progress_process_group = QButtonGroup(self)
        self.progress_process_group.setExclusive(True)
        for name in ("전체", "사출", "분리", "하이드레이션", "접착", "누수규격", "포장"):
            button = self._progress_filter_button(name, name == "누수규격")
            button.setProperty("processName", name)
            button.clicked.connect(self._apply_progress_filters)
            self.progress_process_group.addButton(button)
            display_row.addWidget(button)
        display_row.addSpacing(8)
        display_row.addWidget(QLabel("코드 표시"))
        self.progress_code_checks = {}
        for prefix in ("T", "P", "Q", "R"):
            check = QCheckBox(f"{prefix}코드")
            check.stateChanged.connect(self._update_progress_column_visibility)
            self.progress_code_checks[prefix] = check
            display_row.addWidget(check)
        display_row.addSpacing(8)
        display_row.addWidget(QLabel("품명 기준"))
        self.progress_name_basis = QComboBox()
        self.progress_name_basis.addItem("판매코드", "판매")
        self.progress_name_basis.addItem("P코드", "P")
        self.progress_name_basis.addItem("Q코드", "Q")
        self.progress_name_basis.addItem("R코드", "R")
        self.progress_name_basis.currentIndexChanged.connect(self._apply_progress_filters)
        display_row.addWidget(self.progress_name_basis)
        display_row.addStretch()
        sort_note = QLabel("정렬: 납기일 → POWER → 신규분류요약 → 품번 → CP·AXIS·ADD")
        sort_note.setObjectName("CardSub")
        display_row.addWidget(sort_note)
        filters_layout.addLayout(display_row)
        layout.addWidget(filters)

        table_card = QFrame()
        table_card.setObjectName("Card")
        table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(14, 11, 14, 14)
        title_row = QHBoxLayout()
        title = QLabel("납기별 상세")
        title.setObjectName("CardTitle")
        self.process_snapshot_note = QLabel()
        self.process_snapshot_note.setObjectName("CardSub")
        title_row.addWidget(title)
        title_row.addStretch()
        title_row.addWidget(self.process_snapshot_note)
        table_layout.addLayout(title_row)
        self.progress_columns = (
            "신규분류요약", "이니셜", "수주번호", "품명", "T코드", "P코드", "Q코드", "R코드",
            "POWER", "CP", "AXIS", "ADD", "납기일", "사출", "분리", "하이드레이션", "접착", "누수규격", "포장",
        )
        self.process_table = QTableWidget(0, len(self.progress_columns))
        self.process_table.setObjectName("DataTable")
        self.process_table.setHorizontalHeaderLabels(self.progress_columns)
        self.process_table.setAlternatingRowColors(True)
        self.process_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.process_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.process_table.setSortingEnabled(False)
        self.process_table.verticalHeader().setVisible(False)
        self.process_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.process_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        table_layout.addWidget(self.process_table, 1)
        layout.addWidget(table_card, 1)
        self._update_progress_column_visibility()
        self._apply_progress_filters()
        return page

    def _progress_market_changed(self, selected: str) -> None:
        buttons = self.progress_market_buttons
        if selected == "전체" and buttons["전체"].isChecked():
            for name, button in buttons.items():
                button.setChecked(name == "전체")
        elif selected != "전체":
            buttons["전체"].setChecked(False)
            if not any(button.isChecked() for name, button in buttons.items() if name != "전체"):
                buttons["전체"].setChecked(True)
        self._apply_progress_filters()

    def _progress_due_changed(self) -> None:
        button = self.progress_due_group.checkedButton()
        mode = str(button.property("dueMode")) if button else "해제"
        today = QDate.currentDate()
        if mode == "당월":
            self.progress_due_date.setDate(QDate(today.year(), today.month(), today.daysInMonth()))
        elif mode == "+7일":
            self.progress_due_date.setDate(today.addDays(7))
        elif mode == "+14일":
            self.progress_due_date.setDate(today.addDays(14))
        self.progress_due_date.setEnabled(mode == "직접")
        self._apply_progress_filters()

    def _progress_due_limit(self) -> date | None:
        button = self.progress_due_group.checkedButton()
        mode = str(button.property("dueMode")) if button else "해제"
        today = date.today()
        if mode == "해제":
            return None
        if mode == "당월":
            next_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
            return next_month - timedelta(days=1)
        if mode == "+7일":
            return today + timedelta(days=7)
        if mode == "+14일":
            return today + timedelta(days=14)
        selected = self.progress_due_date.date()
        return date(selected.year(), selected.month(), selected.day())

    def _selected_progress_process(self) -> str:
        button = self.progress_process_group.checkedButton()
        return str(button.property("processName")) if button else "누수규격"

    def _update_progress_column_visibility(self) -> None:
        if not hasattr(self, "process_table") or not hasattr(self, "progress_columns"):
            return
        for prefix, check in self.progress_code_checks.items():
            self.process_table.setColumnHidden(self.progress_columns.index(f"{prefix}코드"), not check.isChecked())
        process_columns = ("사출", "분리", "하이드레이션", "접착", "누수규격", "포장")
        selected = self._selected_progress_process()
        visible_until = len(process_columns) - 1 if selected == "전체" else process_columns.index(selected)
        for index, name in enumerate(process_columns):
            self.process_table.setColumnHidden(self.progress_columns.index(name), index > visible_until)

    def _apply_progress_filters(self) -> None:
        if not hasattr(self, "progress_all_rows"):
            return
        selected_markets = {
            name for name, button in self.progress_market_buttons.items()
            if name != "전체" and button.isChecked()
        }
        category = self.progress_classification.currentText() or "전체"
        process = self._selected_progress_process()
        due_limit = self._progress_due_limit()
        token = " ".join((self.progress_global_search.text(), self.progress_detail_search.text())).strip().casefold()
        name_basis = str(self.progress_name_basis.currentData() or "판매")
        rows = []
        for source in self.progress_all_rows:
            if selected_markets and source.get("진행현황") not in selected_markets:
                continue
            if category != "전체" and source.get("신규분류요약") != category:
                continue
            if process != "전체" and float(source.get(process) or 0) == 0:
                continue
            if due_limit is not None:
                try:
                    row_due = datetime.strptime(str(source.get("납기일") or "")[:10], "%Y-%m-%d").date()
                except ValueError:
                    continue
                if row_due > due_limit:
                    continue
            if token:
                haystack = " ".join(str(source.get(key) or "") for key in (
                    "신규분류요약", "이니셜", "수주번호", "품목코드", "품명", "T코드", "P코드", "Q코드", "R코드",
                )).casefold()
                if any(part not in haystack for part in token.split()):
                    continue
            row = dict(source)
            row["품명"] = source.get(f"품명{name_basis}") or source.get("품명판매") or source.get("품명") or ""
            rows.append(row)
        rows.sort(key=business_sort_key)
        summary = self.process_service.summary(rows)
        for key, label in self.process_kpi_values.items():
            label.setText(f"{int(summary.get(key) or 0):,}건")
        status = self.process_service.status()
        orders = len({row.get("수주번호") for row in rows if row.get("수주번호")})
        self.progress_result_chip.setText(f"완료 · S관(3공장) {orders:,}건")
        self.process_snapshot_note.setText(
            f"{len(rows):,}행 · APS 원천 갱신 {status.get('source_refreshed_at') or '-'} · S관 고정"
        )
        numeric = {"사출", "분리", "하이드레이션", "접착", "누수규격", "포장"}
        self.process_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, key in enumerate(self.progress_columns):
                value = row.get(key, "")
                text = f"{float(value):,.0f}" if key in numeric and value not in (None, "") else str(value or "-")
                item = QTableWidgetItem(text)
                if key in numeric:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if key in {"신규분류요약", "품명", "T코드", "P코드", "Q코드", "R코드"}:
                    item.setToolTip(str(value or ""))
                self.process_table.setItem(row_index, column_index, item)
        self._update_progress_column_visibility()

    def _build_bom_page(self) -> QWidget:
        """Mount the SCM Control Tower BOM page unchanged over the S-only service."""
        self.bom_service = BomExplorerService()
        self.bom_page = BomStatusPage(service=self.bom_service)
        # Compatibility aliases for app-level status and existing navigation hooks.
        self.bom_tabs = self.bom_page.inner_tabs
        return self.bom_page

    def _build_bom_page_legacy(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        self.bom_tabs = QTabWidget()
        self.bom_tabs.setObjectName("BomTabs")
        self.bom_tabs.setDocumentMode(True)
        page_layout.addWidget(self.bom_tabs)

        structure_page = QWidget()
        layout = QVBoxLayout(structure_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self.bom_service = BomExplorerService()
        search_card = QFrame()
        search_card.setObjectName("SearchCard")
        search_layout = QHBoxLayout(search_card)
        search_layout.setContentsMargins(18, 14, 18, 14)
        search_layout.setSpacing(10)
        search_label = QLabel("검색")
        search_label.setObjectName("FilterLabel")
        self.bom_search_mode = QComboBox()
        self.bom_search_mode.setObjectName("BomSearchCombo")
        self.bom_search_mode.addItem("통합 검색", "all")
        self.bom_search_mode.addItem("품번 검색", "code")
        self.bom_search_mode.addItem("품명 검색", "name")
        self.bom_code_scope = QComboBox()
        self.bom_code_scope.setObjectName("BomSearchCombo")
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
            self.bom_code_scope.addItem(label, prefix)
        self.bom_search_input = QLineEdit()
        self.bom_search_input.setObjectName("SearchInput")
        self.bom_search_input.setClearButtonEnabled(True)
        self.bom_search_input.returnPressed.connect(self._submit_bom_search)
        self.bom_search_input.textEdited.connect(self._queue_bom_suggestions)
        self.bom_search_mode.currentIndexChanged.connect(self._bom_search_filter_changed)
        self.bom_code_scope.currentIndexChanged.connect(self._bom_search_filter_changed)
        self.bom_completer_model = QStringListModel(self)
        self.bom_completer = QCompleter(self.bom_completer_model, self)
        self.bom_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.bom_completer.setFilterMode(Qt.MatchContains)
        self.bom_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.bom_completer.setMaxVisibleItems(12)
        self.bom_completer.setWrapAround(False)
        self.bom_completer.activated.connect(self._bom_completion_selected)
        self.bom_search_input.setCompleter(self.bom_completer)
        self.bom_suggestion_timer = QTimer(self)
        self.bom_suggestion_timer.setSingleShot(True)
        self.bom_suggestion_timer.setInterval(140)
        self.bom_suggestion_timer.timeout.connect(self._update_bom_suggestions)
        self._update_bom_search_placeholder()
        search_button = QPushButton("조회")
        search_button.setObjectName("PrimaryButton")
        search_button.setIcon(qta.icon("fa6s.magnifying-glass", color="#FFFFFF"))
        search_button.clicked.connect(self._submit_bom_search)
        reset_button = QPushButton("초기화")
        reset_button.setObjectName("SecondaryButton")
        reset_button.clicked.connect(self._reset_bom_search)
        search_layout.addWidget(search_label)
        search_layout.addWidget(self.bom_search_mode)
        search_layout.addWidget(self.bom_code_scope)
        search_layout.addWidget(self.bom_search_input, 1)
        search_layout.addWidget(search_button)
        search_layout.addWidget(reset_button)
        layout.addWidget(search_card)

        status = self.bom_service.snapshot_status()
        self.bom_snapshot_status = QLabel()
        self.bom_snapshot_status.setObjectName("BomSnapshotStatus")
        if status.get("available"):
            self.bom_snapshot_status.setText(
                f"로컬 BOM DB 전용  ·  API 직접 조회 없음  ·  제품 {int(status.get('product_rows') or 0):,}건  ·  "
                f"BOM {int(status.get('bom_rows') or 0):,}건  ·  갱신 {status.get('source_refreshed_at') or status.get('refreshed_at') or '-'}"
            )
        else:
            self.bom_snapshot_status.setText("로컬 BOM 스냅샷이 없습니다. 수집기를 먼저 실행해 주세요.")
        layout.addWidget(self.bom_snapshot_status)

        board_card = QFrame()
        board_card.setObjectName("Card")
        board_layout = QVBoxLayout(board_card)
        board_layout.setContentsMargins(12, 12, 12, 12)
        board_layout.setSpacing(8)
        stage_headers = QHBoxLayout()
        stage_headers.setContentsMargins(18, 0, 18, 0)
        stage_headers.setSpacing(22)
        self.bom_stage_copy_buttons: list[QPushButton] = []
        self.bom_stage_copy_timers: list[QTimer] = []
        for stage_index, title in enumerate(BomFlowBoard.STAGE_TITLES):
            header_widget = QWidget()
            header_layout = QHBoxLayout(header_widget)
            header_layout.setContentsMargins(0, 0, 0, 0)
            header_layout.setSpacing(5)
            header_layout.addStretch()
            header = QLabel(title)
            header.setObjectName("BomStageHeader")
            header.setAlignment(Qt.AlignCenter)
            header_layout.addWidget(header)
            copy_button = QPushButton("복사")
            copy_button.setObjectName("BomStageCopyButton")
            copy_button.setIcon(qta.icon("fa6s.copy", color="#52677E"))
            copy_button.setEnabled(False)
            copy_button.setToolTip(f"선택 경로에 포함된 {title} 품번만 복사합니다.")
            copy_button.clicked.connect(lambda _checked=False, index=stage_index: self._copy_bom_stage_codes(index))
            header_layout.addWidget(copy_button)
            header_layout.addStretch()
            stage_headers.addWidget(header_widget, 1)
            self.bom_stage_copy_buttons.append(copy_button)
            feedback_timer = QTimer(self)
            feedback_timer.setSingleShot(True)
            feedback_timer.setInterval(1600)
            feedback_timer.timeout.connect(lambda index=stage_index: self._restore_bom_stage_copy_button(index))
            self.bom_stage_copy_timers.append(feedback_timer)
        board_layout.addLayout(stage_headers)
        self.bom_flow_board = BomFlowBoard()
        self.bom_flow_board.item_selected.connect(self._select_bom_code)
        self.bom_flow_board.item_requery_requested.connect(self._requery_bom_code)
        self.bom_flow_board.active_path_changed.connect(self._update_bom_stage_copy_buttons)
        board_layout.addWidget(self.bom_flow_board, 1)
        self.bom_result_note = QLabel("카드에 마우스를 올리면 상세정보가 표시되고, 우클릭하면 재조회·복사 메뉴가 열립니다.")
        self.bom_result_note.setObjectName("BomResultNote")
        board_layout.addWidget(self.bom_result_note)
        layout.addWidget(board_card, 1)
        self.bom_tabs.addTab(structure_page, "BOM 구성 현황")
        self.bom_tabs.addTab(self._build_product_registration_tab(), "제품명 등록 검색")
        self.bom_tabs.addTab(self._build_item_code_tab(), "품목코드 구성 현황")
        self.bom_tabs.addTab(self._build_bom_change_tab(), "BOM 등록·수정 현황")
        return page

    def _plain_data_table(self, columns: tuple[str, ...]) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setObjectName("DataTable")
        table.setHorizontalHeaderLabels(columns)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        return table

    def _build_product_registration_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(12)
        search_card = QFrame()
        search_card.setObjectName("SearchCard")
        search_layout = QHBoxLayout(search_card)
        search_layout.setContentsMargins(18, 12, 18, 12)
        self.product_search_input = QLineEdit()
        self.product_search_input.setObjectName("SearchInput")
        self.product_search_input.setPlaceholderText("제품코드 또는 제품명 입력")
        self.product_search_input.setClearButtonEnabled(True)
        search_button = QPushButton("조회")
        search_button.setObjectName("PrimaryButton")
        search_button.clicked.connect(self._reload_product_registration)
        reset_button = QPushButton("초기화")
        reset_button.setObjectName("SecondaryButton")
        reset_button.clicked.connect(lambda: (self.product_search_input.clear(), self._reload_product_registration()))
        self.product_search_input.returnPressed.connect(self._reload_product_registration)
        search_layout.addWidget(self.product_search_input, 1)
        search_layout.addWidget(search_button)
        search_layout.addWidget(reset_button)
        layout.addWidget(search_card)
        self.product_result_note = QLabel()
        self.product_result_note.setObjectName("BomSnapshotStatus")
        layout.addWidget(self.product_result_note)
        columns = ("제품코드", "제품명", "구분", "생산공장", "신규분류요약", "유효연수", "DIA", "BC", "함수율")
        self.product_registration_table = self._plain_data_table(columns)
        self.product_registration_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.product_registration_table, 1)
        self._reload_product_registration()
        return tab

    def _reload_product_registration(self) -> None:
        query = self.product_search_input.text().strip() if hasattr(self, "product_search_input") else ""
        rows = self.bom_service.product_rows(query, limit=500)
        columns = (
            ("code", "제품코드"), ("name", "제품명"), ("kind", "구분"),
            ("factory", "생산공장"), ("classification_summary", "신규분류요약"),
            ("validity_years", "유효연수"), ("dia", "DIA"), ("bc", "BC"),
            ("water_content", "함수율"),
        )
        self.product_registration_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, (key, _title) in enumerate(columns):
                self.product_registration_table.setItem(row_index, column_index, QTableWidgetItem(str(row.get(key) or "-")))
        suffix = " · 최대 500건 표시" if len(rows) >= 500 else ""
        self.product_result_note.setText(f"제품 기준정보 {len(rows):,}건{suffix}")

    def _build_item_code_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(12)
        search_card = QFrame()
        search_card.setObjectName("SearchCard")
        search_layout = QHBoxLayout(search_card)
        search_layout.setContentsMargins(18, 12, 18, 12)
        self.item_code_search_input = QLineEdit()
        self.item_code_search_input.setObjectName("SearchInput")
        self.item_code_search_input.setPlaceholderText("T·S·P·Q·R 코드 또는 품명 입력")
        self.item_code_search_input.setClearButtonEnabled(True)
        search_button = QPushButton("조회")
        search_button.setObjectName("PrimaryButton")
        search_button.clicked.connect(self._reload_item_code_links)
        self.item_code_search_input.returnPressed.connect(self._reload_item_code_links)
        search_layout.addWidget(self.item_code_search_input, 1)
        search_layout.addWidget(search_button)
        layout.addWidget(search_card)
        self.item_code_result_note = QLabel("코드를 검색하면 직상위·직하위 BOM 구성을 표시합니다.")
        self.item_code_result_note.setObjectName("BomSnapshotStatus")
        layout.addWidget(self.item_code_result_note)
        self.item_code_table = self._plain_data_table(("기준코드", "구분", "연결코드", "품명"))
        self.item_code_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        layout.addWidget(self.item_code_table, 1)
        return tab

    def _reload_item_code_links(self) -> None:
        query = self.item_code_search_input.text().strip()
        if not query:
            self.item_code_result_note.setText("조회할 품목코드 또는 품명을 입력해 주세요.")
            self.item_code_table.setRowCount(0)
            return
        matches = self.bom_service.search(query, limit=1)
        if not matches:
            self.item_code_result_note.setText(f"'{query}'에 해당하는 품목을 찾지 못했습니다.")
            self.item_code_table.setRowCount(0)
            return
        code = matches[0]["code"]
        links = self.bom_service.direct_code_links([code]).get(code, {"parents": [], "children": []})
        product_map = {row["code"]: row["name"] for row in self.bom_service.product_rows("", limit=10000)}
        rows = [(code, "직상위", linked, product_map.get(linked, "")) for linked in links["parents"]]
        rows += [(code, "직하위", linked, product_map.get(linked, "")) for linked in links["children"]]
        self.item_code_table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column_index, value in enumerate(values):
                self.item_code_table.setItem(row_index, column_index, QTableWidgetItem(str(value or "-")))
        self.item_code_result_note.setText(f"{code} · 직상위 {len(links['parents'])}건 · 직하위 {len(links['children'])}건")

    def _build_bom_change_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(12)
        overview = self.bom_service.bom_change_overview(limit=500)
        status = QLabel(
            f"최근 비교 기준 {overview.get('baseline') or '-'} · 신규등록 {len(overview.get('registrations', [])):,}건 · 수정 {len(overview.get('modifications', [])):,}건"
        )
        status.setObjectName("BomSnapshotStatus")
        layout.addWidget(status)
        splitter = QGridLayout()
        registrations = self._plain_data_table(("등록일", "T코드", "제품명", "생산공장"))
        modifications = self._plain_data_table(("변경일", "변경구분", "코드", "제품명", "BOM 단계", "변경내용"))
        for table, rows, keys in (
            (registrations, overview.get("registrations", []), ("detected_at", "code", "product_name", "factory")),
            (modifications, overview.get("modifications", []), ("detected_at", "change_type", "code", "product_name", "stage", "target")),
        ):
            table.setRowCount(len(rows))
            for row_index, row in enumerate(rows):
                for column_index, key in enumerate(keys):
                    table.setItem(row_index, column_index, QTableWidgetItem(str(row.get(key) or "-")))
        splitter.addWidget(registrations, 0, 0)
        splitter.addWidget(modifications, 0, 1)
        splitter.setColumnStretch(0, 4)
        splitter.setColumnStretch(1, 6)
        layout.addLayout(splitter, 1)
        return tab

    # BOM 내부 탭은 SCM Control Tower의 탐색 흐름을 따르되,
    # 모든 결과를 별도 수집된 로컬 SQLite 스냅샷에서만 읽는다.
    def _build_product_registration_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(10)

        toolbar = QHBoxLayout()
        self.product_result_note = QLabel("제품 기준정보를 불러오는 중입니다.")
        self.product_result_note.setObjectName("BomSnapshotStatus")
        toolbar.addWidget(self.product_result_note, 1)
        self.product_composition_button = QPushButton("확장 구성")
        self.product_composition_button.setObjectName("SecondaryButton")
        self.product_composition_button.setCheckable(True)
        self.product_composition_button.setIcon(qta.icon("fa6s.diagram-project", color="#52677E"))
        self.product_composition_button.setToolTip(
            "판매·생산·분리·사출 단계의 직접 상위·하위 품번을 표시합니다."
        )
        self.product_composition_button.toggled.connect(self._toggle_product_composition)
        reset_button = QPushButton("필터 초기화")
        reset_button.setObjectName("SecondaryButton")
        reset_button.setIcon(qta.icon("fa6s.rotate-left", color="#52677E"))
        reset_button.clicked.connect(self._reset_product_filters)
        toolbar.addWidget(self.product_composition_button)
        toolbar.addWidget(reset_button)
        layout.addLayout(toolbar)

        columns = (
            "직상위 코드", "제품명코드", "직하위 코드", "제품명", "구분",
            "공장구분", "유효기간(년)", "DIA", "BC", "분류요약", "함수율",
        )
        self.product_registration_table = self._plain_data_table(columns)
        self.product_registration_table.setObjectName("BomFilterTable")
        # 필터 입력 중 행 전체가 청록색으로 선택되어 글자가 묻히지 않게 한다.
        # 더블클릭 신호는 NoSelection에서도 정상 동작한다.
        self.product_registration_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.product_registration_table.cellDoubleClicked.connect(self._open_product_bom)
        self.product_registration_table.setColumnHidden(0, True)
        self.product_registration_table.setColumnHidden(2, True)
        layout.addWidget(self.product_registration_table, 1)

        self.product_filters: list[QWidget] = []
        self.product_filter_timer = QTimer(self)
        self.product_filter_timer.setSingleShot(True)
        self.product_filter_timer.setInterval(180)
        self.product_filter_timer.timeout.connect(self._apply_product_filters)
        self._product_composition_expanded = False
        self._product_source_rows = self.bom_service.product_rows("", limit=10000)
        self._setup_product_filters()
        self._populate_product_filter_options()
        self._apply_product_filters()
        return tab

    def _setup_product_filters(self) -> None:
        table = self.product_registration_table
        table.setRowCount(1)
        table.setRowHeight(0, 38)
        placeholders = ("예: P0007 / *1186", "예: *Rhapsody", "년")
        filter_columns = (1, 3, 4, 5, 6, 7, 8, 9, 10)
        combo_filter_indexes = {2, 3, 5, 6, 7, 8}
        for filter_index, table_column in enumerate(filter_columns):
            if filter_index in combo_filter_indexes:
                editor = QComboBox()
                editor.setObjectName("BomColumnFilterCombo")
                editor.setEditable(False)
                editor.setMaxVisibleItems(18)
                editor.addItem("전체", "")
                editor.currentIndexChanged.connect(self._queue_product_filter)
            else:
                editor = QLineEdit()
                editor.setObjectName("BomColumnFilter")
                placeholder_index = 0 if filter_index == 0 else 1 if filter_index == 1 else 2
                editor.setPlaceholderText(placeholders[placeholder_index])
                editor.setClearButtonEnabled(True)
                editor.textChanged.connect(self._queue_product_filter)
            self.product_filters.append(editor)
            table.setCellWidget(0, table_column, editor)

    @staticmethod
    def _product_filter_text(value: object) -> str:
        return " ".join(str(value or "").upper().lstrip("*").replace("_", " ").replace("-", " ").split())

    def _product_filter_term(self, editor: QWidget) -> str:
        if isinstance(editor, QComboBox):
            return self._product_filter_text(editor.currentData())
        if isinstance(editor, QLineEdit):
            return self._product_filter_text(editor.text())
        return ""

    @staticmethod
    def _product_display_value(key: str, value: object) -> str:
        text = str(value or "").strip()
        if key == "dia" and text:
            try:
                return f"{float(text):.1f}"
            except ValueError:
                pass
        return text

    def _populate_product_filter_options(
        self,
        terms: list[str] | None = None,
    ) -> bool:
        """Rebuild each dropdown from rows matching every *other* filter.

        This mirrors SCM Control Tower's ERP-style faceted filtering: choosing a
        value in one column narrows the valid choices in the remaining columns,
        while the active column keeps all choices allowed by the other filters.
        """
        filter_keys = (
            "code", "name", "kind", "factory", "validity_years", "dia", "bc",
            "classification_summary", "water_content",
        )
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
        for filter_index, key in choice_keys.items():
            combo = self.product_filters[filter_index]
            if not isinstance(combo, QComboBox):
                continue
            selected = str(combo.currentData() or "")
            candidate_rows = [
                row
                for row in self._product_source_rows
                if all(
                    index == filter_index
                    or not term
                    or self._row_matches_product_filter(row, filter_key, term, index)
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
            if filter_index in numeric_columns:
                def sort_key(value: str) -> tuple[int, float | str]:
                    try:
                        return (0, float(value))
                    except ValueError:
                        return (1, value.upper())
            elif key == "classification_summary":
                def sort_key(value: str) -> tuple:
                    return classification_sort_key(value)
            else:
                def sort_key(value: str) -> tuple[int, str]:
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

    def _queue_product_filter(self, *_args: object) -> None:
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
        self.product_registration_table.setColumnHidden(0, not checked)
        self.product_registration_table.setColumnHidden(2, not checked)
        self.product_composition_button.setText("확장 구성 ✓" if checked else "확장 구성")
        self._apply_product_filters()

    def _row_matches_product_filter(
        self,
        row: dict[str, str],
        key: str,
        term: str,
        column: int,
    ) -> bool:
        value = self._product_filter_text(
            self._product_display_value(key, row.get(key, ""))
        )
        if isinstance(self.product_filters[column], QComboBox):
            return value == term
        return term in value

    def _apply_product_filters(self) -> None:
        keys = (
            "code", "name", "kind", "factory", "validity_years", "dia", "bc",
            "classification_summary", "water_content",
        )
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
                or self._row_matches_product_filter(row, key, term, column)
                for column, (key, term) in enumerate(zip(keys, terms))
            )
        ]

        visible = rows[:500]
        links = (
            self.bom_service.direct_code_links([str(row.get("code") or "") for row in visible])
            if self._product_composition_expanded else {}
        )
        table = self.product_registration_table
        table.setRowCount(len(visible) + 1)
        table.setRowHeight(0, 38)
        table_columns = (1, 3, 4, 5, 6, 7, 8, 9, 10)
        for row_index, row in enumerate(visible, start=1):
            code = str(row.get("code") or "").upper()
            if self._product_composition_expanded:
                relation = links.get(code, {"parents": [], "children": []})
                for column, relation_key in ((0, "parents"), (2, "children")):
                    codes = list(relation.get(relation_key, []))
                    text_value = " · ".join(codes[:3]) + (f" · +{len(codes) - 3}" if len(codes) > 3 else "")
                    item = QTableWidgetItem(text_value or "-")
                    item.setToolTip("\n".join(codes) or "직접 연결 코드 없음")
                    table.setItem(row_index, column, item)
            for column, key in zip(table_columns, keys):
                item = QTableWidgetItem(self._product_display_value(key, row.get(key, "")) or "-")
                if key != "name" and key != "classification_summary":
                    item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row_index, column, item)

        header = table.horizontalHeader()
        for index in range(table.columnCount()):
            header.setSectionResizeMode(index, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        header.setSectionResizeMode(9, QHeaderView.Stretch)
        widths = {0: 180, 1: 120, 2: 180, 4: 84, 5: 96, 6: 94, 7: 68, 8: 68, 10: 82}
        for index, width in widths.items():
            table.setColumnWidth(index, width)
        extra = " · 결과가 많으면 열별 필터를 추가하세요" if len(rows) > 500 else ""
        mode = " · 직상위·직하위 코드 표시" if self._product_composition_expanded else ""
        self.product_result_note.setText(f"필터 결과 {len(rows):,}건 · 화면 표시 {len(visible):,}건{extra}{mode}")

    def _open_product_bom(self, row: int, _column: int) -> None:
        if row <= 0:
            return
        item = self.product_registration_table.item(row, 1)
        if not item or not item.text().strip(" -"):
            return
        self.bom_tabs.setCurrentIndex(0)
        self.bom_search_input.setText(item.text())
        self._submit_bom_search()

    def _build_item_code_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(12)
        search_card = QFrame()
        search_card.setObjectName("SearchCard")
        search_layout = QHBoxLayout(search_card)
        search_layout.setContentsMargins(18, 12, 18, 12)
        search_layout.setSpacing(10)
        self.item_code_search_mode = QComboBox()
        self.item_code_search_mode.addItem("통합 검색", "all")
        self.item_code_search_mode.addItem("코드", "code")
        self.item_code_search_mode.addItem("품명", "name")
        self.item_code_scope = QComboBox()
        for label, value in (("전체 코드", ""), ("T코드", "T"), ("S코드", "S"), ("P코드", "P"), ("Q코드", "Q"), ("R코드", "R")):
            self.item_code_scope.addItem(label, value)
        self.item_code_search_input = QLineEdit()
        self.item_code_search_input.setObjectName("SearchInput")
        self.item_code_search_input.setPlaceholderText("T·S·P·Q·R 코드 또는 품명 입력 · *1186 마스터 검색")
        self.item_code_search_input.setClearButtonEnabled(True)
        self.item_code_completer_model = QStringListModel(self)
        self.item_code_completer = QCompleter(self.item_code_completer_model, self)
        self.item_code_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.item_code_completer.setFilterMode(Qt.MatchContains)
        self.item_code_completer.setCompletionMode(QCompleter.PopupCompletion)
        self.item_code_search_input.setCompleter(self.item_code_completer)
        self.item_code_completer.activated.connect(self._item_code_completion_selected)
        self.item_code_suggestion_timer = QTimer(self)
        self.item_code_suggestion_timer.setSingleShot(True)
        self.item_code_suggestion_timer.setInterval(140)
        self.item_code_suggestion_timer.timeout.connect(self._update_item_code_suggestions)
        self.item_code_search_input.textChanged.connect(lambda _text: self.item_code_suggestion_timer.start())
        search_button = QPushButton("조회")
        search_button.setObjectName("PrimaryButton")
        search_button.setIcon(qta.icon("fa6s.magnifying-glass", color="white"))
        search_button.clicked.connect(self._reload_item_code_links)
        reset_button = QPushButton("초기화")
        reset_button.setObjectName("SecondaryButton")
        reset_button.clicked.connect(self._reset_item_code_tab)
        self.item_code_search_input.returnPressed.connect(self._reload_item_code_links)
        search_layout.addWidget(self.item_code_search_mode)
        search_layout.addWidget(self.item_code_scope)
        search_layout.addWidget(self.item_code_search_input, 1)
        search_layout.addWidget(search_button)
        search_layout.addWidget(reset_button)
        layout.addWidget(search_card)
        self.item_code_result_note = QLabel("코드를 검색하면 직상위·직하위 BOM 구성을 표시합니다. · 로컬 BOM DB 전용")
        self.item_code_result_note.setObjectName("BomSnapshotStatus")
        layout.addWidget(self.item_code_result_note)
        self.item_code_table = self._plain_data_table(("기준코드", "구분", "연결코드", "품명"))
        self.item_code_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.item_code_table.cellDoubleClicked.connect(self._open_item_code_bom)
        header = self.item_code_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Interactive)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.item_code_table.setColumnWidth(0, 140)
        self.item_code_table.setColumnWidth(1, 100)
        self.item_code_table.setColumnWidth(2, 140)
        layout.addWidget(self.item_code_table, 1)
        return tab

    def _update_item_code_suggestions(self) -> None:
        query = self.item_code_search_input.text().strip()
        if not query:
            self.item_code_completer_model.setStringList([])
            return
        rows = self.bom_service.search(
            query, limit=16,
            field=str(self.item_code_search_mode.currentData() or "all"),
            code_prefix=str(self.item_code_scope.currentData() or ""),
        )
        self.item_code_completer_model.setStringList(
            [f"{row.get('code', '')} · {row.get('name', '')}".rstrip(" ·") for row in rows]
        )
        if self.item_code_search_input.hasFocus() and rows:
            self.item_code_completer.complete()

    def _item_code_completion_selected(self, text: str) -> None:
        code = str(text).split(" · ", 1)[0].strip()
        if code:
            self.item_code_search_input.setText(code)
            self._reload_item_code_links()

    def _reset_item_code_tab(self) -> None:
        self.item_code_suggestion_timer.stop()
        self.item_code_search_mode.setCurrentIndex(0)
        self.item_code_scope.setCurrentIndex(0)
        self.item_code_search_input.clear()
        self.item_code_table.setRowCount(0)
        self.item_code_result_note.setText("코드를 검색하면 직상위·직하위 BOM 구성을 표시합니다. · 로컬 BOM DB 전용")

    def _reload_item_code_links(self) -> None:
        query = self.item_code_search_input.text().strip()
        if not query:
            self.item_code_result_note.setText("조회할 품목코드 또는 품명을 입력해 주세요.")
            self.item_code_table.setRowCount(0)
            return
        matches = self.bom_service.search(
            query, limit=1,
            field=str(self.item_code_search_mode.currentData() or "all"),
            code_prefix=str(self.item_code_scope.currentData() or ""),
        )
        if not matches:
            self.item_code_result_note.setText(f"'{query}'에 해당하는 품목을 찾지 못했습니다.")
            self.item_code_table.setRowCount(0)
            return
        code = str(matches[0].get("code") or "").upper()
        links = self.bom_service.direct_code_links([code]).get(code, {"parents": [], "children": []})
        product_map = {row["code"]: row["name"] for row in self.bom_service.product_rows("", limit=10000)}
        rows = [(code, "직상위", linked, product_map.get(linked, "")) for linked in links["parents"]]
        rows += [(code, "직하위", linked, product_map.get(linked, "")) for linked in links["children"]]
        self.item_code_table.setRowCount(len(rows))
        for row_index, values in enumerate(rows):
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(str(value or "-"))
                if column_index < 3:
                    item.setTextAlignment(Qt.AlignCenter)
                self.item_code_table.setItem(row_index, column_index, item)
        self.item_code_result_note.setText(
            f"{code} · {matches[0].get('name', '')} · 직상위 {len(links['parents'])}건 · 직하위 {len(links['children'])}건"
        )

    def _open_item_code_bom(self, row: int, _column: int) -> None:
        item = self.item_code_table.item(row, 2)
        if not item or not item.text().strip(" -"):
            return
        self.bom_tabs.setCurrentIndex(0)
        self.bom_search_input.setText(item.text())
        self._submit_bom_search()

    def _build_bom_change_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(12)
        self.bom_change_overview_data = self.bom_service.bom_change_overview(limit=5000)
        self.bom_change_status = QLabel()
        self.bom_change_status.setObjectName("BomSnapshotStatus")
        layout.addWidget(self.bom_change_status)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        left_title = QLabel("신규등록")
        left_title.setObjectName("SectionTitle")
        left_filter = QHBoxLayout()
        left_filter.addWidget(QLabel("생산공장"))
        self.bom_registration_factory = QComboBox()
        self.bom_registration_factory.addItem("전체 공장", "")
        factories = sorted({str(row.get("factory") or "") for row in self.bom_change_overview_data.get("registrations", []) if row.get("factory")})
        for factory in factories:
            self.bom_registration_factory.addItem(factory, factory)
        self.bom_registration_factory.currentIndexChanged.connect(self._reload_bom_change_tables)
        left_filter.addWidget(self.bom_registration_factory)
        left_filter.addStretch()
        left_head = QWidget()
        left_head_layout = QHBoxLayout(left_head)
        left_head_layout.setContentsMargins(0, 0, 0, 0)
        left_head_layout.addWidget(left_title)
        left_head_layout.addLayout(left_filter)

        right_title = QLabel("수정현황")
        right_title.setObjectName("SectionTitle")
        right_head = QWidget()
        right_head_layout = QHBoxLayout(right_head)
        right_head_layout.setContentsMargins(0, 0, 0, 0)
        right_head_layout.addWidget(right_title)
        right_head_layout.addStretch()
        right_head_layout.addWidget(QLabel("BOM 단계"))
        self.bom_change_stage = QComboBox()
        self.bom_change_stage.addItem("전체 단계", "")
        stages = sorted({str(row.get("stage") or "") for row in self.bom_change_overview_data.get("modifications", []) if row.get("stage")})
        for stage in stages:
            self.bom_change_stage.addItem(stage, stage)
        self.bom_change_stage.currentIndexChanged.connect(self._reload_bom_change_tables)
        right_head_layout.addWidget(self.bom_change_stage)
        reset = QPushButton("필터 초기화")
        reset.setObjectName("SecondaryButton")
        reset.clicked.connect(self._reset_bom_change_filters)
        right_head_layout.addWidget(reset)

        self.bom_registration_table = self._plain_data_table(("등록일", "T코드", "제품명", "생산공장"))
        self.bom_modification_table = self._plain_data_table(("변경일", "변경 구분", "BOM 단계", "상위 품번·품명", "하위 품번·품명", "변경 내용"))
        self.bom_registration_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.bom_modification_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.bom_modification_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.bom_modification_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        grid.addWidget(left_head, 0, 0)
        grid.addWidget(right_head, 0, 1)
        grid.addWidget(self.bom_registration_table, 1, 0)
        grid.addWidget(self.bom_modification_table, 1, 1)
        grid.setColumnStretch(0, 4)
        grid.setColumnStretch(1, 6)
        layout.addLayout(grid, 1)
        self._reload_bom_change_tables()
        return tab

    def _reset_bom_change_filters(self) -> None:
        self.bom_registration_factory.setCurrentIndex(0)
        self.bom_change_stage.setCurrentIndex(0)
        self._reload_bom_change_tables()

    def _reload_bom_change_tables(self, *_args: object) -> None:
        overview = self.bom_change_overview_data
        factory = str(self.bom_registration_factory.currentData() or "")
        stage = str(self.bom_change_stage.currentData() or "")
        registrations = [row for row in overview.get("registrations", []) if not factory or str(row.get("factory") or "") == factory]
        modifications = [row for row in overview.get("modifications", []) if not stage or str(row.get("stage") or "") == stage]
        visible_registrations = registrations[:500]
        visible_modifications = modifications[:500]
        self.bom_registration_table.setRowCount(len(visible_registrations))
        for row_index, row in enumerate(visible_registrations):
            values = (row.get("detected_at"), row.get("code"), row.get("product_name"), row.get("factory"))
            for column, value in enumerate(values):
                self.bom_registration_table.setItem(row_index, column, QTableWidgetItem(str(value or "-")))
        self.bom_modification_table.setRowCount(len(visible_modifications))
        for row_index, row in enumerate(visible_modifications):
            target = str(row.get("target") or "")
            target_name = str(row.get("target_name") or "")
            target_display = f"{target} · {target_name}".strip(" ·")
            before_value = str(row.get("before_value") or "")
            after_value = str(row.get("after_value") or "")
            change_text = " → ".join(value for value in (before_value, after_value) if value) or str(row.get("change_type") or "-")
            values = (
                row.get("detected_at"), row.get("change_type"), row.get("stage"),
                row.get("parent_display") or row.get("code"), target_display, change_text,
            )
            for column, value in enumerate(values):
                self.bom_modification_table.setItem(row_index, column, QTableWidgetItem(str(value or "-")))
        self.bom_change_status.setText(
            f"최근 비교 기준 {overview.get('baseline') or '-'} · 최근 90일 자동 보관 · "
            f"신규등록 {len(registrations):,}건 · 수정 {len(modifications):,}건 · 로컬 BOM DB 전용"
        )

    def _update_bom_search_placeholder(self) -> None:
        field = str(self.bom_search_mode.currentData() or "all")
        scope = str(self.bom_code_scope.currentText() or "전체 코드")
        if field == "code":
            placeholder = f"{scope} 품번 입력 · 예: P1186"
        elif field == "name":
            placeholder = f"{scope} 품명 입력"
        else:
            placeholder = "판매·생산·분리·사출 코드 또는 품명 입력"
        self.bom_search_input.setPlaceholderText(placeholder)

    def _bom_search_filter_changed(self) -> None:
        self._update_bom_search_placeholder()
        if self.bom_search_input.text().strip():
            self._queue_bom_suggestions()

    def _queue_bom_suggestions(self, _text: str = "") -> None:
        self.bom_suggestion_timer.start()

    def _update_bom_suggestions(self) -> None:
        query = self.bom_search_input.text().strip()
        if not query:
            self.bom_completer_model.setStringList([])
            return
        rows = self.bom_service.search(
            query,
            limit=12,
            field=str(self.bom_search_mode.currentData() or "all"),
            code_prefix=str(self.bom_code_scope.currentData() or ""),
        )
        self.bom_completer_model.setStringList(
            [f"{row.get('code', '')} · {row.get('name', '')}".rstrip(" ·") for row in rows]
        )
        # 모델이 타이핑 이후 갱신되므로 팝업도 명시적으로 다시 열어야
        # BC/P/T 등 여러 마스터 후보가 한 줄로 축소되지 않는다.
        if self.bom_search_input.hasFocus() and rows:
            self.bom_completer.complete()

    def _bom_completion_selected(self, text: str) -> None:
        code = str(text).split(" · ", 1)[0].strip()
        if code:
            self.bom_search_input.setText(code)
            self._submit_bom_search()

    def _select_bom_code(self, code: str) -> None:
        self.bom_flow_board.set_selected(code)
        active_count = sum(len(stage) for stage in self.bom_flow_board.active_codes_by_stage())
        self.bom_result_note.setProperty("state", "success")
        self.bom_result_note.setText(
            f"검색 기준 {getattr(self, 'bom_current_root', code)}  ·  상세 선택 {code}  ·  연결 품번 {active_count:,}개"
        )
        self.bom_result_note.style().unpolish(self.bom_result_note)
        self.bom_result_note.style().polish(self.bom_result_note)

    def _requery_bom_code(self, code: str) -> None:
        self.bom_search_input.setText(code)
        self._submit_bom_search()

    def _update_bom_stage_copy_buttons(self, stages: object) -> None:
        active_stages = stages if isinstance(stages, list) else []
        for index, button in enumerate(self.bom_stage_copy_buttons):
            self.bom_stage_copy_timers[index].stop()
            codes = active_stages[index] if index < len(active_stages) and isinstance(active_stages[index], list) else []
            button.setEnabled(bool(codes))
            button.setText(f"복사 {len(codes)}" if codes else "복사")
            button.setProperty("copied", False)
            button.style().unpolish(button)
            button.style().polish(button)

    def _copy_bom_stage_codes(self, stage_index: int) -> None:
        stages = self.bom_flow_board.active_codes_by_stage()
        if not 0 <= stage_index < len(stages):
            return
        codes = list(dict.fromkeys(stages[stage_index]))
        if not codes:
            return
        QApplication.clipboard().setText(", ".join(codes))
        button = self.bom_stage_copy_buttons[stage_index]
        button.setText("복사됨 ✓")
        button.setProperty("copied", True)
        button.style().unpolish(button)
        button.style().polish(button)
        self.bom_stage_copy_timers[stage_index].start()
        self.bom_result_note.setText(
            f"{BomFlowBoard.STAGE_TITLES[stage_index]} 활성 품번 {len(codes):,}개를 복사했습니다."
        )

    def _restore_bom_stage_copy_button(self, stage_index: int) -> None:
        if not 0 <= stage_index < len(self.bom_stage_copy_buttons):
            return
        stages = self.bom_flow_board.active_codes_by_stage()
        codes = stages[stage_index] if stage_index < len(stages) else []
        button = self.bom_stage_copy_buttons[stage_index]
        button.setEnabled(bool(codes))
        button.setText(f"복사 {len(codes)}" if codes else "복사")
        button.setProperty("copied", False)
        button.style().unpolish(button)
        button.style().polish(button)

    def _submit_bom_search(self) -> None:
        query = self.bom_search_input.text().strip()
        if not query:
            self.bom_result_note.setText("조회할 품번 또는 품명을 입력해 주세요.")
            return
        try:
            matches = self.bom_service.search(
                query,
                limit=1,
                field=str(self.bom_search_mode.currentData() or "all"),
                code_prefix=str(self.bom_code_scope.currentData() or ""),
            )
            if not matches:
                raise LookupError(f"'{query}'에 해당하는 품번을 찾지 못했습니다.")
            code = matches[0]["code"]
            result = self.bom_service.graph(code)
        except (FileNotFoundError, LookupError, ValueError) as exc:
            self.bom_result_note.setText(str(exc))
            self.bom_result_note.setProperty("state", "error")
            self.bom_result_note.style().unpolish(self.bom_result_note)
            self.bom_result_note.style().polish(self.bom_result_note)
            self.bom_flow_board.show_empty("검색조건을 확인한 뒤 다시 조회해 주세요.")
            return
        self.bom_current_root = code
        self.bom_result_note.setProperty("state", "success")
        hierarchy = result["hierarchy"]
        linked_count = len({
            str(row.get("code") or "")
            for column in list(hierarchy.get("columns") or [])
            for row in column
            if isinstance(row, dict) and row.get("code")
        })
        self.bom_result_note.setText(
            f"검색 기준 {code}  ·  상세 선택 {code}  ·  연결 품번 {linked_count:,}개"
        )
        self.bom_result_note.style().unpolish(self.bom_result_note)
        self.bom_result_note.style().polish(self.bom_result_note)
        self.bom_flow_board.set_hierarchy(hierarchy)

    def _reset_bom_search(self) -> None:
        self.bom_search_input.clear()
        self.bom_current_root = ""
        self.bom_completer_model.setStringList([])
        self.bom_result_note.setProperty("state", "")
        self.bom_result_note.setText("카드에 마우스를 올리면 상세정보가 표시되고, 우클릭하면 재조회·복사 메뉴가 열립니다.")
        self.bom_result_note.style().unpolish(self.bom_result_note)
        self.bom_result_note.style().polish(self.bom_result_note)
        self.bom_flow_board.show_empty("품번을 검색하면 5단계 BOM 연결관계가 표시됩니다.")

    def _build_settings_page(self) -> QWidget:
        body = QWidget()
        body.setObjectName("PageBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        def check_program_permission() -> None:
            from services.program_gate import ProgramGate

            gate = ProgramGate(APP_VERSION)
            result = gate.check()
            if result.allowed:
                current_display = f"v{APP_VERSION.lstrip('vV')}"
                latest_display = (
                    f"v{result.latest_version.lstrip('vV')}"
                    if result.latest_version
                    else "확인되지 않음"
                )
                if result.update_required:
                    permission_message = (
                        f"이 PC의 사용 권한이 정상입니다.\n\n"
                        f"현재 버전: {current_display}\n"
                        f"최신 버전: {latest_display}\n\n"
                        "최신 버전 업데이트가 필요합니다."
                    )
                else:
                    permission_message = (
                        f"이 PC의 사용 권한이 정상입니다.\n\n"
                        f"현재 버전: {current_display}\n"
                        f"관리 기준 버전: {latest_display}\n\n"
                        "현재 버전과 같거나 낮은 버전으로는 업데이트하지 않습니다."
                    )
                show_app_message(
                    self,
                    "사용 권한 확인",
                    permission_message,
                    kind="success",
                )
                return
            from ui.permission_dialog import show_permission_denied

            show_permission_denied(
                self,
                gate.identity()["pc_id"],
                result.message or "등록되지 않았거나 사용이 중지된 PC입니다.",
            )

        def check_program_update() -> None:
            self._start_runtime_permission_check(manual=True)

        def copy_installer_link() -> None:
            QApplication.clipboard().setText(DEFAULT_UPDATE_URL)
            show_app_message(self, "설치 링크 복사", "최신 설치파일 주소를 복사했습니다.", kind="success")

        def download_latest_installer() -> None:
            QDesktopServices.openUrl(QUrl(DEFAULT_UPDATE_URL))

        cards = (
            (
                "로컬 데이터 저장소",
                "리드지 정보가 필요 없는 공정·PC는 동기화하지 않아도 됩니다. 리드지 정보가 필요하면 네이버웍스 '리드지 시방서' 동기화가 필요합니다.",
                (
                    f"로컬 데이터  {DATA_CENTER_DIR}\n"
                    f"리드지 클론  {LEAD_SHEET_PDF_BACKUP_DIR}\n"
                    f"리드지 수동 등록  {DATA_CENTER_DIR / '리드지 수동 등록'}"
                ),
                "폴더 열기",
                self._open_local_data_store,
            ),
            (
                "데이터 수집 및 갱신",
                "BOM 전체, S관 APS 진행현황, 최근 7일 생산실적을 순서대로 갱신합니다. 최초 설치만 전월~금일을 수집합니다.",
                "상태 확인 중",
                "전체 데이터 수집",
                self._start_full_data_refresh,
            ),
            (
                "사용 권한",
                "Google Sheet 등록정보를 기준으로 이 PC의 사용권한을 확인합니다.",
                "생산3공장 똑딱이",
                "권한 확인",
                check_program_permission,
            ),
            (
                "프로그램 업데이트",
                "새 버전이 있으면 알림을 표시하고 설치파일을 내려받습니다.",
                f"현재 버전 {APP_VERSION}",
                "업데이트 확인",
                check_program_update,
            ),
        )
        for index, (title, description, value, action, callback) in enumerate(cards):
            if index == 1:
                layout.addWidget(self._build_collection_settings_card())
                continue
            card = QFrame()
            card.setObjectName("SettingsCard")
            card_layout = QHBoxLayout(card)
            card_layout.setContentsMargins(20, 18, 20, 18)
            text_layout = QVBoxLayout()
            text_layout.setSpacing(4)
            title_label = QLabel(title)
            title_label.setObjectName("CardTitle")
            desc_label = QLabel(description)
            desc_label.setObjectName("CardSub")
            value_label = QLabel(value)
            value_label.setObjectName("SettingsValue")
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            text_layout.addWidget(title_label)
            text_layout.addWidget(desc_label)
            text_layout.addWidget(value_label)
            button = QPushButton(action)
            button.setObjectName("SecondaryButton")
            button.setEnabled(callback is not None)
            if callback is not None:
                button.clicked.connect(callback)
            if title == "프로그램 업데이트":
                self.settings_update_button = button
            card_layout.addLayout(text_layout, 1)
            card_layout.addWidget(button)
            layout.addWidget(card)

        distribution_card = QFrame()
        distribution_card.setObjectName("SettingsCard")
        distribution_layout = QHBoxLayout(distribution_card)
        distribution_layout.setContentsMargins(20, 18, 20, 18)
        distribution_text = QVBoxLayout()
        distribution_text.setSpacing(4)
        distribution_title = QLabel("프로그램 설치파일")
        distribution_title.setObjectName("CardTitle")
        distribution_description = QLabel("신규 사용자에게 최신 설치파일 주소를 전달할 수 있습니다. 설치 후 등록되지 않은 PC는 사용 권한을 요청합니다.")
        distribution_description.setObjectName("CardSub")
        distribution_value = QLabel(DEFAULT_UPDATE_URL)
        distribution_value.setObjectName("SettingsValue")
        distribution_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        distribution_value.setWordWrap(True)
        distribution_text.addWidget(distribution_title)
        distribution_text.addWidget(distribution_description)
        distribution_text.addWidget(distribution_value)
        distribution_buttons = QHBoxLayout()
        distribution_buttons.setSpacing(8)
        copy_button = QPushButton("링크 복사")
        copy_button.setObjectName("SecondaryButton")
        copy_button.clicked.connect(copy_installer_link)
        download_button = QPushButton("설치파일 다운로드")
        download_button.setObjectName("PrimaryButton")
        download_button.setStyleSheet(
            "QPushButton { background:#0878F9; color:white; border:none; border-radius:10px; "
            "padding:0 18px; min-height:42px; font-weight:700; } "
            "QPushButton:hover { background:#006BE6; }"
        )
        download_button.clicked.connect(download_latest_installer)
        distribution_buttons.addWidget(copy_button)
        distribution_buttons.addWidget(download_button)
        distribution_layout.addLayout(distribution_text, 1)
        distribution_layout.addLayout(distribution_buttons)
        layout.addWidget(distribution_card)
        self._refresh_settings_data_status()
        layout.addStretch()
        return self._scroll_page(body)

    def _build_collection_settings_card(self) -> QWidget:
        card = QFrame()
        self.collection_settings_card = card
        card.setObjectName("SettingsCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(12)

        header = QHBoxLayout()
        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)
        title = QLabel("데이터 수집 및 갱신")
        title.setObjectName("CardTitle")
        description = QLabel(
            "데이터별 자동 수집 주기와 최근 상태를 확인하고 필요한 항목만 즉시 갱신합니다."
        )
        description.setObjectName("CardSub")
        self.settings_data_status = QLabel("상태 확인 중")
        self.settings_data_status.setObjectName("SettingsValue")
        self.settings_data_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        text_layout.addWidget(title)
        text_layout.addWidget(description)
        text_layout.addWidget(self.settings_data_status)
        self.collection_expand_button = QPushButton("상세 설정 펼치기  ▾")
        self.collection_expand_button.setObjectName("SecondaryButton")
        self.collection_expand_button.setCheckable(True)
        self.collection_expand_button.clicked.connect(self._toggle_collection_details)
        header.addLayout(text_layout, 1)
        header.addWidget(self.collection_expand_button)
        card_layout.addLayout(header)

        self.collection_details = QWidget()
        self.collection_details.setObjectName("CollectionDetails")
        self.collection_details.setVisible(False)
        detail_layout = QVBoxLayout(self.collection_details)
        detail_layout.setContentsMargins(0, 8, 0, 0)
        detail_layout.setSpacing(10)
        notice = QLabel(
            "실행 시 누락된 수집을 백그라운드에서 보완하고, 켜진 동안 선택 주기로 갱신합니다. APS는 원천 변경 시에만 S관 DB를 교체합니다."
        )
        notice.setObjectName("CollectionNotice")
        notice.setWordWrap(True)
        detail_layout.addWidget(notice)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        for column, text in enumerate(("데이터", "현재 상태", "마지막 갱신", "저장 건수", "자동 주기", "수동 실행")):
            label = QLabel(text)
            label.setObjectName("CollectionHeader")
            grid.addWidget(label, 0, column)

        self.collection_controls: dict[str, dict[str, QWidget]] = {}
        definitions = (
            ("bom", "BOM", "제품명·BOM 전체 스냅샷", (("수동만", 0), ("30분", 30), ("1시간", 60), ("3시간", 180), ("6시간", 360), ("12시간", 720), ("24시간", 1440))),
            ("aps", "S관 APS", "원천 변경 확인 후 S관만 갱신", (("중지", 0), ("1분 (기본)", 1), ("5분", 5), ("10분", 10), ("30분", 30), ("1시간", 60))),
            ("production", "생산실적", "07시 첫 전체 · 이후 최근 7일", (("수동만", 0), ("30분", 30), ("1시간", 60), ("3시간", 180), ("6시간", 360), ("12시간", 720), ("24시간", 1440))),
        )
        for row_index, (key, name, subtext, choices) in enumerate(definitions, start=1):
            name_box = QVBoxLayout()
            name_label = QLabel(name)
            name_label.setObjectName("CollectionName")
            sub_label = QLabel(subtext)
            sub_label.setObjectName("CollectionSub")
            name_box.addWidget(name_label)
            name_box.addWidget(sub_label)
            grid.addLayout(name_box, row_index, 0)
            state = QLabel("확인 중")
            state.setObjectName("CollectionState")
            refreshed = QLabel("-")
            refreshed.setObjectName("CollectionCell")
            rows = QLabel("-")
            rows.setObjectName("CollectionCell")
            schedule = QComboBox()
            schedule.setObjectName("CollectionSchedule")
            for label, minutes in choices:
                schedule.addItem(label, minutes)
            wanted = int(self.collection_schedule.get(f"{key}_minutes", 0))
            selected = schedule.findData(wanted)
            schedule.setCurrentIndex(selected if selected >= 0 else 0)
            schedule.currentIndexChanged.connect(
                lambda _index, source=key, combo=schedule: self._collection_schedule_changed(source, combo)
            )
            manual = QPushButton("지금 갱신")
            manual.setObjectName("SecondaryButton")
            manual.clicked.connect(lambda _checked=False, source=key: self._start_data_collection(source))
            grid.addWidget(state, row_index, 1)
            grid.addWidget(refreshed, row_index, 2)
            grid.addWidget(rows, row_index, 3)
            grid.addWidget(schedule, row_index, 4)
            grid.addWidget(manual, row_index, 5)
            self.collection_controls[key] = {
                "state": state, "refreshed": refreshed, "rows": rows,
                "schedule": schedule, "manual": manual,
            }
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(2, 1)
        detail_layout.addLayout(grid)

        footer = QHBoxLayout()
        self.collection_schedule_note = QLabel(
            "주기 변경은 즉시 저장됩니다. · 불필요 데이터는 실행 후 자동 정리하고 6시간마다 반복합니다."
        )
        self.collection_schedule_note.setObjectName("CollectionNotice")
        footer.addWidget(self.collection_schedule_note)
        footer.addStretch()
        self.settings_refresh_button = QPushButton("전체 데이터 수집")
        self.settings_refresh_button.setObjectName("PrimaryButton")
        self.settings_refresh_button.clicked.connect(self._start_full_data_refresh)
        footer.addWidget(self.settings_refresh_button)
        detail_layout.addLayout(footer)
        card_layout.addWidget(self.collection_details)
        return card

    def _toggle_collection_details(self, checked: bool) -> None:
        self.collection_details.setVisible(checked)
        self.collection_expand_button.setText(
            "상세 설정 접기  ▴" if checked else "상세 설정 펼치기  ▾"
        )
        if checked:
            self._refresh_settings_data_status()

    def _open_local_data_store(self) -> None:
        DATA_CENTER_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA_CENTER_DIR)))

    @staticmethod
    def _read_refresh_status(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _refresh_settings_data_status(self) -> None:
        if not hasattr(self, "settings_data_status"):
            return
        definitions = (
            ("bom", "BOM", DATA_CENTER_DIR / "bom" / "snapshot" / "refresh_status.json", ("product_rows", "bom_rows")),
            ("aps", "APS", DATA_CENTER_DIR / "process-status" / "snapshot" / "refresh_status.json", ("stored_rows",)),
            ("production", "생산실적", DATA_CENTER_DIR / "production-performance" / "snapshot" / "refresh_status.json", ("stored_rows", "s_factory_rows")),
        )
        parts = []
        for key, name, path, count_keys in definitions:
            status = self._read_refresh_status(path)
            counts = [int(status[key]) for key in count_keys if status.get(key) is not None]
            refreshed = status.get("refreshed_at") or status.get("collected_at") or "-"
            count_text = "/".join(f"{count:,}" for count in counts)
            parts.append(f"{name} {count_text}건 · {refreshed}" if counts else f"{name} 미수집")
            controls = getattr(self, "collection_controls", {}).get(key)
            if controls:
                status_value = str(status.get("status") or "")
                success = status_value in {"success", "skipped"}
                state_text = (
                    "● 정상 · 변경 없음"
                    if status_value == "skipped"
                    else "● 정상" if success else "● 확인 필요"
                )
                if key == "production" and status.get("collection_mode"):
                    mode = "일일 전체" if "전체" in str(status.get("collection_mode")) else "최근 7일"
                    state_text += f" · {mode}"
                controls["state"].setText(state_text)
                controls["state"].setProperty("state", "success" if success else "warning")
                controls["state"].style().unpolish(controls["state"])
                controls["state"].style().polish(controls["state"])
                controls["refreshed"].setText(str(refreshed).replace("T", " ")[:19])
                controls["rows"].setText(f"{count_text}건" if count_text else "-")
                if key == "production":
                    controls["state"].setToolTip(
                        f"07시 전체 완료일 {status.get('daily_full_date') or '미완료'}\n"
                        f"수집 범위 {status.get('collection_from') or '-'} ~ {status.get('date_to') or '-'}"
                    )
        self.settings_data_status.setText("  |  ".join(parts))

    def _collection_schedule_changed(self, source: str, combo: QComboBox) -> None:
        self.collection_schedule[f"{source}_minutes"] = int(combo.currentData() or 0)
        save_schedule(self.collection_schedule)
        self._apply_collection_timers()
        self.collection_schedule_note.setText(
            f"{combo.currentText()} 설정을 저장했습니다. 자동 수집은 프로그램 실행 중에만 동작합니다."
        )

    def _apply_collection_timers(self, *, run_initial: bool = False) -> None:
        minutes = int(self.collection_schedule.get("aps_minutes", 1))
        if minutes <= 0:
            self.aps_monitor_timer.stop()
        else:
            self.aps_monitor_timer.setInterval(minutes * 60_000)
            self.aps_monitor_timer.start()
            if run_initial:
                QTimer.singleShot(3_000, self._start_aps_monitor_check)

    @staticmethod
    def _status_refreshed_at(path: Path) -> datetime | None:
        status = MainWindow._read_refresh_status(path)
        text = str(status.get("refreshed_at") or status.get("collected_at") or "")
        try:
            value = datetime.fromisoformat(text)
            return value.replace(tzinfo=None) if value.tzinfo else value
        except ValueError:
            try:
                return datetime.fromtimestamp(path.stat().st_mtime)
            except OSError:
                return None

    def _run_scheduled_collections(self) -> None:
        if hasattr(self, "settings_collection_process") and self.settings_collection_process.state() != QProcess.NotRunning:
            return
        now = datetime.now()
        definitions = (
            ("bom", DATA_CENTER_DIR / "bom" / "snapshot" / "refresh_status.json"),
            ("production", DATA_CENTER_DIR / "production-performance" / "snapshot" / "refresh_status.json"),
        )
        for source, status_path in definitions:
            minutes = int(self.collection_schedule.get(f"{source}_minutes", 0))
            if minutes <= 0:
                continue
            status = self._read_refresh_status(status_path)
            refreshed = self._status_refreshed_at(status_path)
            attempted = self._collection_last_attempt.get(source)
            daily_full_due = (
                source == "production"
                and now.hour >= 7
                and str(status.get("daily_full_date") or "") != now.date().isoformat()
            )
            due = daily_full_due or refreshed is None or (now - refreshed) >= timedelta(minutes=minutes)
            retry_ready = attempted is None or (now - attempted) >= timedelta(minutes=max(5, minutes))
            if due and retry_ready:
                self._start_data_collection(source, scheduled=True)
                break

    def _start_data_cleanup(self, _checked: bool = False, *, scheduled: bool = False) -> None:
        if hasattr(self, "data_cleanup_process") and self.data_cleanup_process.state() != QProcess.NotRunning:
            return
        if hasattr(self, "settings_collection_process") and self.settings_collection_process.state() != QProcess.NotRunning:
            if scheduled:
                QTimer.singleShot(60_000, lambda: self._start_data_cleanup(scheduled=True))
            else:
                self.collection_schedule_note.setText("데이터 수집 완료 후 정리해 주세요.")
            return
        if hasattr(self, "settings_cleanup_button"):
            self.settings_cleanup_button.setEnabled(False)
            self.settings_cleanup_button.setText("정리 중…")
        process = _background_process(self)
        process.setWorkingDirectory(str(ROOT_DIR))
        program, arguments = _collector_process_command("data_retention_cleanup.py")
        process.setProgram(program)
        process.setArguments(arguments)
        process.finished.connect(self._data_cleanup_finished)
        self.data_cleanup_process = process
        process.start()

    def _data_cleanup_finished(self, exit_code: int, _exit_status) -> None:
        if hasattr(self, "settings_cleanup_button"):
            self.settings_cleanup_button.setEnabled(True)
            self.settings_cleanup_button.setText("불필요 데이터 정리")
        if exit_code != 0:
            error = bytes(self.data_cleanup_process.readAllStandardError()).decode("utf-8", errors="replace").strip()
            if hasattr(self, "collection_schedule_note"):
                self.collection_schedule_note.setText(f"정리 실패: {error[-180:] or '정리 로그를 확인하세요.'}")
            return
        try:
            result = json.loads(bytes(self.data_cleanup_process.readAllStandardOutput()).decode("utf-8", errors="replace"))
        except (ValueError, TypeError):
            result = {}
        if hasattr(self, "collection_schedule_note"):
            removed_mb = float(result.get("removed_bytes") or 0) / (1024 * 1024)
            self.collection_schedule_note.setText(
                f"자동 정리 완료 · 파일 {int(result.get('removed_files') or 0):,}개 · {removed_mb:,.1f}MB 확보 · 6시간마다 실행"
            )

    def _start_full_data_refresh(self) -> None:
        self._start_data_collection("all")

    def _start_data_collection(self, source: str, *, scheduled: bool = False) -> None:
        if hasattr(self, "settings_collection_process") and self.settings_collection_process.state() != QProcess.NotRunning:
            if not scheduled:
                self.settings_data_status.setText("다른 데이터 수집이 진행 중입니다. 완료 후 다시 실행해 주세요.")
            return
        scripts = {
            "all": "refresh_all.py",
            "bom": "bom_snapshot_collector.py",
            "aps": "process_status_collector.py",
            "production": "production_performance_collector.py",
        }
        labels = {"all": "전체", "bom": "BOM", "aps": "S관 APS", "production": "생산실적"}
        if source not in scripts:
            return
        if source in {"all", "aps"} and hasattr(self, "aps_monitor_process") and self.aps_monitor_process.state() != QProcess.NotRunning:
            self.aps_monitor_process.kill()
            self.aps_monitor_process.waitForFinished(1_000)
        self._collection_last_attempt[source] = datetime.now()
        self._set_collection_busy(True, source)
        self.settings_data_status.setText(f"{labels[source]} 수집 중입니다. 프로그램을 종료하지 마세요.")
        process = _background_process(self)
        process.setWorkingDirectory(str(ROOT_DIR))
        program, arguments = _collector_process_command(scripts[source])
        process.setProgram(program)
        process.setArguments(arguments)
        process.finished.connect(
            lambda exit_code, exit_status, selected=source: self._data_collection_finished(selected, exit_code, exit_status)
        )
        self.settings_collection_process = process
        self.settings_refresh_process = process
        process.start()

    def _set_collection_busy(self, busy: bool, source: str = "") -> None:
        if hasattr(self, "settings_refresh_button"):
            self.settings_refresh_button.setEnabled(not busy)
            self.settings_refresh_button.setText("수집 중…" if busy and source == "all" else "전체 데이터 수집")
        for key, controls in getattr(self, "collection_controls", {}).items():
            controls["manual"].setEnabled(not busy)
            controls["manual"].setText("수집 중…" if busy and key == source else "지금 갱신")

    def _data_collection_finished(self, source: str, exit_code: int, _exit_status) -> None:
        self._set_collection_busy(False)
        self._refresh_settings_data_status()
        if exit_code != 0:
            error = bytes(self.settings_collection_process.readAllStandardError()).decode("utf-8", errors="replace").strip()
            self.settings_data_status.setText(f"수집 실패: {error[-240:] or '수집 로그를 확인하세요.'}")
            return
        changed = {"bom", "aps", "production"} if source == "all" else {source}
        self._data_db_signatures = self._current_data_db_signatures()
        self._data_status_signatures = self._current_data_status_signatures()
        self._reload_changed_data_views(changed)
        if int(self.collection_schedule.get("aps_minutes", 0)) > 0 and source in {"all", "aps"}:
            QTimer.singleShot(3_000, self._start_aps_monitor_check)

    def _full_data_refresh_finished(self, exit_code: int, exit_status) -> None:
        """이전 연결 지점과의 호환용."""
        self._data_collection_finished("all", exit_code, exit_status)
