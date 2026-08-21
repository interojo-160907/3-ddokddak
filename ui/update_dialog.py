from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def _display_version(value: str) -> str:
    version = str(value or "").strip()
    if not version:
        return "-"
    return version if version.lower().startswith("v") else f"v{version}"


class RequiredUpdateDialog(QDialog):
    def reject(self) -> None:
        return


def show_required_update(
    parent: QWidget,
    current_version: str,
    latest_version: str,
    message: str,
    update_url: str,
) -> None:
    dialog = RequiredUpdateDialog(parent)
    dialog.setObjectName("requiredUpdateDialog")
    dialog.setWindowTitle("필수 업데이트")
    dialog.setModal(True)
    dialog.setFixedWidth(510)
    dialog.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
    dialog.setStyleSheet(
        """
        QDialog#requiredUpdateDialog {
            background: #ffffff;
        }
        QLabel#updateIcon {
            min-width: 48px;
            max-width: 48px;
            min-height: 48px;
            max-height: 48px;
            border-radius: 15px;
            background: #e8f3ff;
            color: #0878f9;
            font-size: 15px;
            font-weight: 900;
        }
        QLabel#updateTitle {
            color: #111827;
            font-size: 19px;
            font-weight: 800;
        }
        QLabel#updateMessage {
            color: #5b6b82;
            font-size: 12px;
        }
        QFrame#versionCard {
            background: #f4f8ff;
            border: 1px solid #cfe0f7;
            border-radius: 14px;
        }
        QLabel#versionCaption {
            color: #718198;
            font-size: 11px;
            font-weight: 700;
        }
        QLabel#currentVersion {
            color: #52637a;
            font-size: 18px;
            font-weight: 800;
        }
        QLabel#latestVersion {
            color: #0878f9;
            font-size: 18px;
            font-weight: 900;
        }
        QLabel#versionArrow {
            color: #8ca0ba;
            font-size: 20px;
            font-weight: 800;
        }
        QPushButton#updateButton {
            min-height: 44px;
            border: 1px solid #0878f9;
            border-radius: 10px;
            background: #0878f9;
            color: #ffffff;
            font-size: 13px;
            font-weight: 800;
        }
        QPushButton#updateButton:hover {
            background: #006be6;
        }
        """
    )

    root = QVBoxLayout(dialog)
    root.setContentsMargins(30, 28, 30, 26)
    root.setSpacing(21)

    header = QHBoxLayout()
    header.setSpacing(15)
    icon = QLabel("UP")
    icon.setObjectName("updateIcon")
    icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
    header.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)

    heading = QVBoxLayout()
    heading.setSpacing(6)
    title = QLabel("새 버전 업데이트")
    title.setObjectName("updateTitle")
    heading.addWidget(title)
    subtitle = QLabel("최신 버전으로 업데이트한 후 프로그램을 사용할 수 있습니다.")
    subtitle.setObjectName("updateMessage")
    subtitle.setWordWrap(True)
    heading.addWidget(subtitle)
    header.addLayout(heading, 1)
    root.addLayout(header)

    version_card = QFrame()
    version_card.setObjectName("versionCard")
    version_layout = QHBoxLayout(version_card)
    version_layout.setContentsMargins(22, 17, 22, 17)
    version_layout.setSpacing(20)

    current_box = QVBoxLayout()
    current_caption = QLabel("현재 버전")
    current_caption.setObjectName("versionCaption")
    current_box.addWidget(current_caption)
    current_value = QLabel(_display_version(current_version))
    current_value.setObjectName("currentVersion")
    current_box.addWidget(current_value)
    version_layout.addLayout(current_box, 1)

    arrow = QLabel("→")
    arrow.setObjectName("versionArrow")
    version_layout.addWidget(arrow)

    latest_box = QVBoxLayout()
    latest_caption = QLabel("최신 버전")
    latest_caption.setObjectName("versionCaption")
    latest_box.addWidget(latest_caption)
    latest_value = QLabel(_display_version(latest_version))
    latest_value.setObjectName("latestVersion")
    latest_box.addWidget(latest_value)
    version_layout.addLayout(latest_box, 1)
    root.addWidget(version_card)

    detail = QLabel(message or "필수 업데이트를 진행합니다.")
    detail.setObjectName("updateMessage")
    detail.setWordWrap(True)
    root.addWidget(detail)

    update_button = QPushButton("업데이트 진행")
    update_button.setObjectName("updateButton")

    def start_update() -> None:
        target = str(update_url or "").strip()
        if target.lower().startswith(("https://", "http://")):
            QDesktopServices.openUrl(QUrl(target))
        dialog.accept()

    update_button.clicked.connect(start_update)
    root.addWidget(update_button)
    dialog.exec()

