from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def permission_request_text(pc_id: str) -> str:
    return f"고유ID : {pc_id}\nPC 위치 : \n사용자 : "


def show_permission_denied(
    parent: QWidget,
    pc_id: str,
    detail: str = "",
) -> None:
    request_text = permission_request_text(pc_id)
    dialog = QDialog(parent)
    dialog.setObjectName("permissionRequestDialog")
    dialog.setWindowTitle("사용 권한 확인")
    dialog.setModal(True)
    dialog.setFixedWidth(520)
    dialog.setStyleSheet(
        """
        QDialog#permissionRequestDialog {
            background: #ffffff;
        }
        QLabel#permissionIcon {
            min-width: 44px;
            max-width: 44px;
            min-height: 44px;
            max-height: 44px;
            border-radius: 14px;
            background: #eaf3ff;
            color: #0878f9;
            font-size: 24px;
            font-weight: 800;
        }
        QLabel#permissionTitle {
            color: #111827;
            font-size: 18px;
            font-weight: 800;
        }
        QLabel#permissionMessage {
            color: #52637a;
            font-size: 12px;
        }
        QFrame#requestCard {
            background: #f4f8ff;
            border: 1px solid #cfe0f7;
            border-radius: 14px;
        }
        QLabel#requestCaption {
            color: #667892;
            font-size: 11px;
            font-weight: 700;
        }
        QLabel#requestValue {
            color: #10233f;
            font-size: 13px;
            font-weight: 700;
        }
        QLabel#requestId {
            color: #075fce;
            font-family: Consolas;
            font-size: 13px;
            font-weight: 700;
        }
        QPushButton#copyRequestButton {
            min-height: 40px;
            padding: 0 22px;
            border: 1px solid #0878f9;
            border-radius: 9px;
            background: #0878f9;
            color: #ffffff;
            font-weight: 700;
        }
        QPushButton#copyRequestButton:hover {
            background: #006be6;
        }
        QPushButton#closeRequestButton {
            min-height: 40px;
            padding: 0 22px;
            border: 1px solid #d7e0eb;
            border-radius: 9px;
            background: #ffffff;
            color: #334155;
            font-weight: 700;
        }
        QPushButton#closeRequestButton:hover {
            background: #f5f7fa;
        }
        """
    )

    root = QVBoxLayout(dialog)
    root.setContentsMargins(28, 26, 28, 24)
    root.setSpacing(20)

    header = QHBoxLayout()
    header.setSpacing(14)
    icon = QLabel("!")
    icon.setObjectName("permissionIcon")
    icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
    header.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

    heading = QVBoxLayout()
    heading.setSpacing(6)
    title = QLabel("생산기획팀 RD에 사용 신청하세요")
    title.setObjectName("permissionTitle")
    heading.addWidget(title)
    message = QLabel(detail or "등록되지 않았거나 사용이 중지된 PC입니다.")
    message.setObjectName("permissionMessage")
    message.setWordWrap(True)
    heading.addWidget(message)
    header.addLayout(heading, 1)
    root.addLayout(header)

    card = QFrame()
    card.setObjectName("requestCard")
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(20, 17, 20, 17)
    card_layout.setSpacing(12)

    def add_row(caption: str, value: str, object_name: str = "requestValue") -> None:
        row = QHBoxLayout()
        row.setSpacing(16)
        label = QLabel(caption)
        label.setObjectName("requestCaption")
        label.setFixedWidth(72)
        row.addWidget(label)
        data = QLabel(value)
        data.setObjectName(object_name)
        data.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(data, 1)
        card_layout.addLayout(row)

    add_row("고유 ID", pc_id, "requestId")
    add_row("PC 위치", "직접 입력")
    add_row("사용자", "직접 입력")
    root.addWidget(card)

    note = QLabel("신청 양식을 복사한 뒤 PC 위치와 사용자를 입력하여 전달하세요.")
    note.setObjectName("permissionMessage")
    root.addWidget(note)

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    close_button = QPushButton("닫기")
    close_button.setObjectName("closeRequestButton")
    close_button.clicked.connect(dialog.reject)
    buttons.addWidget(close_button)
    copy_button = QPushButton("신청 양식 복사")
    copy_button.setObjectName("copyRequestButton")

    def copy_request() -> None:
        QApplication.clipboard().setText(request_text)
        copy_button.setText("복사 완료")
        QTimer.singleShot(1800, lambda: copy_button.setText("신청 양식 복사"))

    copy_button.clicked.connect(copy_request)
    buttons.addWidget(copy_button)
    root.addLayout(buttons)
    dialog.exec()
