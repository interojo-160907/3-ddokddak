from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha1
from typing import Any

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


PROGRAM_KEY = "생산3공장 똑딱이"


@dataclass(frozen=True)
class NoticeItem:
    key: str
    category: str
    text: str
    start_date: date | None
    end_date: date | None
    period_minutes: int
    row_order: int


def _row_value(row: dict[str, Any], *names: str) -> Any:
    compact = {
        str(key).strip().lower().replace(" ", "").replace("_", ""): value
        for key, value in row.items()
    }
    for name in names:
        key = name.strip().lower().replace(" ", "").replace("_", "")
        if key in compact:
            return compact[key]
    return ""


def _parse_date(value: Any) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().replace(".", "-").replace("/", "-")
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _as_enabled(value: Any) -> bool:
    return str(value or "").strip().upper() in {"Y", "YES", "TRUE", "1", "ON"}


def _category(value: Any) -> str:
    text = str(value or "").strip()
    for name in ("긴급", "공지", "안내"):
        if name in text:
            return name
    return ""


class NoticeTicker(QWidget):
    """Common header bulletin with horizontal reading and vertical item changes."""

    COLORS = {
        "긴급": ("#B42318", "#FFF2F0", "#FFCCC7"),
        "공지": ("#087F73", "#ECFDF8", "#A7E8D8"),
        "안내": ("#0A67D8", "#EFF6FF", "#BEDBFF"),
    }
    PRIORITY = {"긴급": 0, "공지": 1, "안내": 2}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("NoticeTicker")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedHeight(34)
        self.setMinimumWidth(300)
        self.setMaximumWidth(760)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setVisible(False)
        self._font = QFont("Malgun Gothic", 10)
        self._font.setWeight(QFont.Weight.DemiBold)
        self._notices: list[NoticeItem] = []
        self._queue: list[NoticeItem] = []
        self._current: NoticeItem | None = None
        self._next: NoticeItem | None = None
        self._current_y = 0.0
        self._next_y = float(self.height())
        self._content_x: float | None = None
        self._seen_slots: set[tuple[str, str]] = set()

        self._clock = QTimer(self)
        self._clock.setInterval(1_000)
        self._clock.timeout.connect(self._schedule_tick)
        self._clock.start()

        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._finish_current)

        self._marquee = QVariantAnimation(self)
        self._marquee.valueChanged.connect(self._marquee_changed)
        self._marquee.finished.connect(self._finish_current)

        self._vertical = QVariantAnimation(self)
        self._vertical.setDuration(480)
        self._vertical.setStartValue(0.0)
        self._vertical.setEndValue(1.0)
        self._vertical.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._vertical.valueChanged.connect(self._vertical_changed)
        self._vertical.finished.connect(self._vertical_finished)

    def replace_notices(self, rows: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> None:
        normalized: list[NoticeItem] = []
        for row_order, row in enumerate(rows or ()):
            if not isinstance(row, dict):
                continue
            program = str(_row_value(row, "프로그램", "program") or "").strip()
            category = _category(_row_value(row, "구분", "category", "type"))
            text = str(_row_value(row, "공지내용", "내용", "content", "message") or "").strip()
            enabled_raw = _row_value(row, "사용여부", "enabled", "active", "use")
            period_raw = _row_value(row, "주기(min)", "주기", "period_minutes", "period", "interval")
            if not all((program, category, text, str(enabled_raw).strip(), str(period_raw).strip())):
                continue
            if program not in {PROGRAM_KEY, "전체"} or not _as_enabled(enabled_raw):
                continue
            try:
                period = int(float(str(period_raw).strip()))
            except ValueError:
                continue
            if period <= 0 or period > 60:
                continue
            start_raw = _row_value(row, "시작일", "start_date", "start")
            end_raw = _row_value(row, "종료일", "end_date", "end")
            start_date = _parse_date(start_raw)
            end_date = _parse_date(end_raw)
            if str(start_raw).strip() and start_date is None:
                continue
            if str(end_raw).strip() and end_date is None:
                continue
            fingerprint = "|".join(
                (program, category, text, str(start_date), str(end_date), str(period), str(row_order))
            )
            normalized.append(
                NoticeItem(
                    key=sha1(fingerprint.encode("utf-8")).hexdigest(),
                    category=category,
                    text=text,
                    start_date=start_date,
                    end_date=end_date,
                    period_minutes=period,
                    row_order=row_order,
                )
            )
        normalized.sort(key=lambda item: (self.PRIORITY[item.category], item.row_order))
        valid_keys = {item.key for item in normalized}
        self._notices = normalized
        self._queue = []
        self._seen_slots = {entry for entry in self._seen_slots if entry[0] in valid_keys}
        if self._current is not None and self._current.key not in valid_keys:
            self._stop_animations()
            self._current = None
            self._next = None
            self.setVisible(False)
        self._schedule_tick()

    @staticmethod
    def _date_active(item: NoticeItem, today: date) -> bool:
        if item.start_date is not None and today < item.start_date:
            return False
        if item.end_date is not None and today > item.end_date:
            return False
        return True

    def _schedule_tick(self) -> None:
        now = datetime.now()
        slot = now.strftime("%Y%m%d%H%M")
        due: list[NoticeItem] = []
        for item in self._notices:
            marker = (item.key, slot)
            if marker in self._seen_slots:
                continue
            if self._date_active(item, now.date()) and now.minute % item.period_minutes == 0:
                self._seen_slots.add(marker)
                due.append(item)
        if due:
            queued_keys = {item.key for item in self._queue}
            if self._current is not None:
                queued_keys.add(self._current.key)
            for item in due:
                if item.key not in queued_keys:
                    self._queue.append(item)
                    queued_keys.add(item.key)
            self._queue.sort(key=lambda item: (self.PRIORITY[item.category], item.row_order))
        if self._current is None and not self._vertical.state():
            self._show_next()

    def _show_next(self) -> None:
        if not self._queue:
            self.setVisible(False)
            return
        self._current = self._queue.pop(0)
        self._current_y = 0.0
        self.setVisible(True)
        self._start_content_animation()

    def _stop_animations(self) -> None:
        self._hold.stop()
        self._marquee.stop()
        self._vertical.stop()

    def _content_width(self, item: NoticeItem) -> int:
        metrics = QFontMetrics(self._font)
        badge = metrics.horizontalAdvance(item.category) + 24
        return badge + 12 + metrics.horizontalAdvance(item.text)

    def _start_content_animation(self) -> None:
        if self._current is None:
            return
        self._hold.stop()
        self._marquee.stop()
        content_width = self._content_width(self._current)
        available = max(120, self.width() - 28)
        if content_width <= available:
            self._content_x = None
            self.update()
            self._hold.start(5_500)
            return
        start_x = float(self.width() - 14)
        end_x = float(-content_width - 14)
        duration = max(8_000, min(36_000, int((start_x - end_x) / 48 * 1_000)))
        self._content_x = start_x
        self._marquee.setDuration(duration)
        self._marquee.setStartValue(start_x)
        self._marquee.setEndValue(end_x)
        self._marquee.setEasingCurve(QEasingCurve.Type.Linear)
        self._marquee.start()

    def _marquee_changed(self, value: Any) -> None:
        self._content_x = float(value)
        self.update()

    def _finish_current(self) -> None:
        if self._vertical.state() == QVariantAnimation.State.Running:
            return
        self._next = self._queue.pop(0) if self._queue else None
        self._vertical.start()

    def _vertical_changed(self, value: Any) -> None:
        progress = float(value)
        self._current_y = -self.height() * progress
        self._next_y = self.height() * (1.0 - progress)
        self.update()

    def _vertical_finished(self) -> None:
        self._current = self._next
        self._next = None
        self._current_y = 0.0
        self._next_y = float(self.height())
        self._content_x = None
        if self._current is None:
            self.setVisible(False)
            return
        self._start_content_animation()

    def _draw_notice(self, painter: QPainter, item: NoticeItem, y_offset: float, *, incoming: bool = False) -> None:
        text_color, _background, _border = self.COLORS[item.category]
        metrics = QFontMetrics(self._font)
        badge_width = metrics.horizontalAdvance(item.category) + 24
        content_width = self._content_width(item)
        if incoming:
            x = max(14.0, (self.width() - content_width) / 2)
        elif self._content_x is None:
            x = max(14.0, (self.width() - content_width) / 2)
        else:
            x = self._content_x
        badge_rect = QRectF(x, y_offset + 6, badge_width, self.height() - 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(text_color))
        painter.drawRoundedRect(badge_rect, 8, 8)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, item.category)
        text_rect = QRectF(
            badge_rect.right() + 12,
            y_offset,
            metrics.horizontalAdvance(item.text) + 4,
            self.height(),
        )
        painter.setPen(QColor(text_color))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, item.text)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._current is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text_color, background, border = self.COLORS[self._current.category]
        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setBrush(QColor(background))
        painter.setPen(QPen(QColor(border), 1))
        painter.drawRoundedRect(outer, 11, 11)
        clip = QPainterPath()
        clip.addRoundedRect(outer.adjusted(2, 2, -2, -2), 9, 9)
        painter.setClipPath(clip)
        painter.setFont(self._font)
        self._draw_notice(painter, self._current, self._current_y)
        if self._next is not None:
            self._draw_notice(painter, self._next, self._next_y, incoming=True)
