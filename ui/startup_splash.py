from __future__ import annotations

from pathlib import Path
import time

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


class SmoothActivityBar(QWidget):
    """Fast, time-based loading sweep independent of real startup progress."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(7)
        self._started_at = time.perf_counter()
        self._cycle_seconds = 0.72
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)
        self._timer.start()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bounds = QRectF(0, 0, self.width(), self.height())
        radius = self.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#E8EEF5"))
        painter.drawRoundedRect(bounds, radius, radius)

        width = float(self.width())
        segment_width = max(72.0, width * 0.32)
        phase = ((time.perf_counter() - self._started_at) % self._cycle_seconds) / self._cycle_seconds
        x = -segment_width + phase * (width + segment_width)
        segment = QRectF(x, 0, segment_width, self.height())
        gradient = QLinearGradient(x, 0, x + segment_width, 0)
        gradient.setColorAt(0.0, QColor("#58A5FF"))
        gradient.setColorAt(0.5, QColor("#0A7AFF"))
        gradient.setColorAt(1.0, QColor("#58A5FF"))
        painter.setBrush(gradient)
        painter.drawRoundedRect(segment, radius, radius)


class StartupSplash(QWidget):
    """Compact, real-progress startup surface for the integrated launcher."""

    def __init__(self, mascot_path: Path, module_count: int = 7) -> None:
        super().__init__(
            None,
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.module_count = max(1, module_count)
        self._dots: list[QLabel] = []
        self._fade: QPropertyAnimation | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(540, 430)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        card = QFrame()
        card.setObjectName("startupCard")
        card.setStyleSheet(
            "QFrame#startupCard{background:#FFFFFF;border:1px solid #DDEAF7;"
            "border-radius:26px;}"
            "QLabel{background:transparent;border:0;font-family:'Malgun Gothic','Segoe UI';}"
        )
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(32)
        shadow.setOffset(0, 9)
        shadow.setColor(QColor(35, 66, 101, 48))
        card.setGraphicsEffect(shadow)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(42, 22, 42, 24)
        layout.setSpacing(0)

        mascot = QLabel()
        mascot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if mascot_path.is_file():
            pixmap = QPixmap(str(mascot_path))
            # The source keeps generous transparent breathing room for reuse.
            # Crop that padding so the character has the same presence as the
            # selected centered splash concept.
            pixmap = pixmap.copy(165, 70, 690, 720)
            mascot.setPixmap(
                pixmap.scaled(
                    210,
                    190,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        mascot.setFixedHeight(190)
        layout.addWidget(mascot)

        title = QLabel(
            '<span style="color:#172033">똑딱이 </span>'
            '<span style="color:#0A7AFF">생산3팀</span>'
        )
        title.setTextFormat(Qt.TextFormat.RichText)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size:28px;font-weight:800;")
        layout.addWidget(title)
        layout.addSpacing(7)

        description = QLabel("납기 통합조회 업무 환경을 안전하게 준비하고 있습니다")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        description.setStyleSheet("color:#788697;font-size:13px;font-weight:500;")
        layout.addWidget(description)
        layout.addSpacing(14)

        self.activity = SmoothActivityBar()
        layout.addWidget(self.activity)
        layout.addSpacing(15)

        dots = QHBoxLayout()
        dots.setSpacing(14)
        dots.addStretch()
        for _index in range(self.module_count):
            dot = QLabel()
            dot.setFixedSize(11, 11)
            self._dots.append(dot)
            dots.addWidget(dot)
        dots.addStretch()
        layout.addLayout(dots)
        layout.addSpacing(14)

        self.status = QLabel("시스템 확인 중")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setStyleSheet("color:#0A7AFF;font-size:13px;font-weight:700;")
        layout.addWidget(self.status)
        self.set_progress(0, self.module_count, "시스템 확인 중")

    def show_centered(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            geometry = screen.availableGeometry()
            self.move(
                geometry.center().x() - self.width() // 2,
                geometry.center().y() - self.height() // 2,
            )
        self.show()
        self.raise_()

    def set_progress(self, completed: int, total: int, status: str) -> None:
        completed = max(0, min(int(completed), self.module_count))
        if total > 0:
            completed = round(completed * self.module_count / total)
        for index, dot in enumerate(self._dots):
            color = "#0A7AFF" if index < completed else "#D9DEE5"
            dot.setStyleSheet(f"background:{color};border-radius:5px;")
        label = status or "업무 프로그램 준비 중"
        self.status.setText(f"{label}  ·  {completed}/{self.module_count}")

    def finish(self, main_window: QWidget) -> None:
        main_window.show()
        main_window.raise_()
        main_window.activateWindow()
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(220)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self.close)
        self._fade.start()
