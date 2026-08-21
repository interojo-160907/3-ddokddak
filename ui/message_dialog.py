from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


_TONES = {
    "info": ("fa5s.info-circle", "#0B7CFF", "#EAF3FF", "#086BDB"),
    "success": ("fa5s.check-circle", "#07966B", "#E8F8F2", "#057B58"),
    "warning": ("fa5s.exclamation-triangle", "#D97706", "#FFF5E5", "#B86100"),
    "error": ("fa5s.times-circle", "#DC3F4F", "#FFF0F2", "#B82E3D"),
    "question": ("fa5s.question-circle", "#0B7CFF", "#EAF3FF", "#086BDB"),
}


class AppMessageDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        message: str,
        *,
        kind: str = "info",
        accept_text: str = "확인",
        reject_text: str = "",
    ) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(title)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumWidth(460)
        self.setMaximumWidth(580)

        icon_name, accent, tint, hover = _TONES.get(kind, _TONES["info"])
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)

        card = QFrame()
        card.setObjectName("messageCard")
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(21, 48, 76, 70))
        card.setGraphicsEffect(shadow)
        root.addWidget(card)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(26, 24, 26, 22)
        card_layout.setSpacing(20)

        header = QHBoxLayout()
        header.setSpacing(14)
        badge = QLabel()
        badge.setObjectName("messageBadge")
        badge.setFixedSize(48, 48)
        badge.setAlignment(Qt.AlignCenter)
        badge.setPixmap(qta.icon(icon_name, color=accent).pixmap(24, 24))
        header.addWidget(badge)

        heading = QVBoxLayout()
        heading.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("messageTitle")
        heading.addWidget(title_label)
        caption = QLabel("생산3팀 똑딱이")
        caption.setObjectName("messageCaption")
        heading.addWidget(caption)
        header.addLayout(heading, 1)

        close_button = QPushButton("×")
        close_button.setObjectName("messageClose")
        close_button.setFixedSize(32, 32)
        close_button.setCursor(Qt.PointingHandCursor)
        close_button.clicked.connect(self.reject)
        header.addWidget(close_button, 0, Qt.AlignTop)
        card_layout.addLayout(header)

        message_label = QLabel(message)
        message_label.setObjectName("messageBody")
        message_label.setWordWrap(True)
        message_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card_layout.addWidget(message_label)

        divider = QFrame()
        divider.setObjectName("messageDivider")
        divider.setFixedHeight(1)
        card_layout.addWidget(divider)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch()
        if reject_text:
            reject_button = QPushButton(reject_text)
            reject_button.setObjectName("messageSecondary")
            reject_button.setMinimumSize(112, 42)
            reject_button.setCursor(Qt.PointingHandCursor)
            reject_button.clicked.connect(self.reject)
            buttons.addWidget(reject_button)
        accept_button = QPushButton(accept_text)
        accept_button.setObjectName("messagePrimary")
        accept_button.setMinimumSize(112, 42)
        accept_button.setCursor(Qt.PointingHandCursor)
        accept_button.clicked.connect(self.accept)
        buttons.addWidget(accept_button)
        card_layout.addLayout(buttons)
        accept_button.setDefault(True)
        accept_button.setFocus()

        self.setStyleSheet(
            f"""
            QFrame#messageCard {{
                background: #FFFFFF;
                border: 1px solid #D8E3EE;
                border-radius: 20px;
            }}
            QLabel#messageBadge {{
                background: {tint};
                border: 1px solid #D6E6F5;
                border-radius: 24px;
            }}
            QLabel#messageTitle {{
                color: #10233F;
                font-family: '맑은 고딕';
                font-size: 18px;
                font-weight: 700;
            }}
            QLabel#messageCaption {{
                color: {accent};
                font-family: '맑은 고딕';
                font-size: 11px;
                font-weight: 700;
            }}
            QLabel#messageBody {{
                color: #334A62;
                font-family: '맑은 고딕';
                font-size: 13px;
                padding: 4px 2px;
            }}
            QFrame#messageDivider {{ background: #E7EDF4; border: none; }}
            QPushButton#messageClose {{
                background: #F4F7FA;
                color: #718096;
                border: none;
                border-radius: 16px;
                font-size: 20px;
            }}
            QPushButton#messageClose:hover {{ background: #E9EEF4; color: #24384D; }}
            QPushButton#messagePrimary {{
                background: {accent};
                color: #FFFFFF;
                border: none;
                border-radius: 10px;
                padding: 0 20px;
                font-family: '맑은 고딕';
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#messagePrimary:hover {{ background: {hover}; }}
            QPushButton#messageSecondary {{
                background: #FFFFFF;
                color: #40566D;
                border: 1px solid #CAD7E3;
                border-radius: 10px;
                padding: 0 20px;
                font-family: '맑은 고딕';
                font-size: 12px;
                font-weight: 700;
            }}
            QPushButton#messageSecondary:hover {{ background: #F3F7FA; border-color: #AFC0D1; }}
            """
        )

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            self.move(parent.frameGeometry().center() - self.rect().center())


def show_app_message(
    parent: QWidget | None,
    title: str,
    message: str,
    *,
    kind: str = "info",
    button_text: str = "확인",
) -> None:
    AppMessageDialog(
        parent, title, message, kind=kind, accept_text=button_text
    ).exec()


def ask_app_confirmation(
    parent: QWidget | None,
    title: str,
    message: str,
    *,
    kind: str = "question",
    accept_text: str = "확인",
    reject_text: str = "취소",
) -> bool:
    dialog = AppMessageDialog(
        parent,
        title,
        message,
        kind=kind,
        accept_text=accept_text,
        reject_text=reject_text,
    )
    return dialog.exec() == QDialog.DialogCode.Accepted
