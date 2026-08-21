from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from services.update_service import (
    download_installer,
    installer_destination,
    schedule_install_and_restart,
)


def _display_version(value: str) -> str:
    version = str(value or "").strip()
    if not version:
        return "-"
    return version if version.lower().startswith("v") else f"v{version}"


class UpdateDownloadWorker(QObject):
    progress = Signal(int)
    completed = Signal(str)
    failed = Signal(str)

    def __init__(self, update_url: str, destination: Path) -> None:
        super().__init__()
        self.update_url = update_url
        self.destination = destination

    @Slot()
    def run(self) -> None:
        try:
            path = download_installer(
                self.update_url,
                self.destination,
                self.progress.emit,
            )
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(str(path))


class RequiredUpdateDialog(QDialog):
    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.selected_action = ""
        self.download_thread: QThread | None = None
        self.download_worker: UpdateDownloadWorker | None = None

    def reject(self) -> None:
        return


def show_required_update(
    parent: QWidget,
    current_version: str,
    latest_version: str,
    message: str,
    update_url: str,
) -> str:
    dialog = RequiredUpdateDialog(parent)
    dialog.setObjectName("requiredUpdateDialog")
    dialog.setWindowTitle("새 버전 업데이트")
    dialog.setModal(True)
    dialog.setFixedWidth(540)
    dialog.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
    dialog.setStyleSheet(
        """
        QDialog#requiredUpdateDialog { background: #ffffff; }
        QLabel#updateIcon {
            min-width: 48px; max-width: 48px; min-height: 48px; max-height: 48px;
            border-radius: 15px; background: #e8f3ff; color: #0878f9;
            font-size: 15px; font-weight: 900;
        }
        QLabel#updateTitle { color: #111827; font-size: 19px; font-weight: 800; }
        QLabel#updateMessage { color: #5b6b82; font-size: 12px; }
        QLabel#updateStatus { color: #0878f9; font-size: 12px; font-weight: 700; }
        QLabel#updateStatus[state="error"] { color: #c83e4d; }
        QFrame#versionCard {
            background: #f4f8ff; border: 1px solid #cfe0f7; border-radius: 14px;
        }
        QLabel#versionCaption { color: #718198; font-size: 11px; font-weight: 700; }
        QLabel#currentVersion { color: #52637a; font-size: 18px; font-weight: 800; }
        QLabel#latestVersion { color: #0878f9; font-size: 18px; font-weight: 900; }
        QLabel#versionArrow { color: #8ca0ba; font-size: 20px; font-weight: 800; }
        QProgressBar {
            min-height: 12px; max-height: 12px; border: none; border-radius: 6px;
            background: #e5edf6; color: transparent;
        }
        QProgressBar::chunk { border-radius: 6px; background: #0878f9; }
        QPushButton#downloadButton, QPushButton#updateButton {
            min-height: 44px; border-radius: 10px; padding: 0 20px;
            font-size: 13px; font-weight: 800;
        }
        QPushButton#downloadButton {
            border: 1px solid #bfd0e3; background: #ffffff; color: #40566d;
        }
        QPushButton#downloadButton:hover { background: #f3f7fb; }
        QPushButton#updateButton {
            border: 1px solid #0878f9; background: #0878f9; color: #ffffff;
        }
        QPushButton#updateButton:hover { background: #006be6; }
        QPushButton:disabled {
            background: #e8edf3; border-color: #d7e0e8; color: #99a7b7;
        }
        """
    )

    root = QVBoxLayout(dialog)
    root.setContentsMargins(30, 28, 30, 26)
    root.setSpacing(20)

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
    subtitle = QLabel("설치파일만 내려받거나 지금 바로 자동 업데이트할 수 있습니다.")
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

    detail = QLabel(message or "새 버전이 확인되었습니다.")
    detail.setObjectName("updateMessage")
    detail.setWordWrap(True)
    root.addWidget(detail)
    status = QLabel("")
    status.setObjectName("updateStatus")
    status.setWordWrap(True)
    status.hide()
    root.addWidget(status)
    progress = QProgressBar()
    progress.setRange(0, 100)
    progress.setValue(0)
    progress.hide()
    root.addWidget(progress)

    buttons = QHBoxLayout()
    buttons.setSpacing(10)
    download_button = QPushButton("다운로드")
    download_button.setObjectName("downloadButton")
    update_button = QPushButton("업데이트하기")
    update_button.setObjectName("updateButton")
    buttons.addWidget(download_button, 1)
    buttons.addWidget(update_button, 1)
    root.addLayout(buttons)
    active_action = {"value": ""}

    def set_busy(busy: bool) -> None:
        download_button.setEnabled(not busy)
        update_button.setEnabled(not busy)

    def set_status(text: str, *, error: bool = False) -> None:
        status.setProperty("state", "error" if error else "")
        status.setText(text)
        status.show()
        status.style().unpolish(status)
        status.style().polish(status)

    def update_progress(value: int) -> None:
        if value < 0:
            progress.setRange(0, 0)
            set_status("설치파일 다운로드 중")
            return
        if progress.maximum() == 0:
            progress.setRange(0, 100)
        progress.setValue(value)
        set_status(f"설치파일 다운로드 중 · {value}%")

    def download_failed(error: str) -> None:
        progress.setRange(0, 100)
        progress.setValue(0)
        set_status(f"다운로드 실패 · {error}", error=True)
        set_busy(False)

    def download_completed(path_text: str) -> None:
        path = Path(path_text)
        progress.setRange(0, 100)
        progress.setValue(100)
        if active_action["value"] == "update":
            try:
                schedule_install_and_restart(path)
            except Exception as exc:
                set_status(f"업데이트 실행 실패 · {exc}", error=True)
                set_busy(False)
                return
            dialog.selected_action = "update"
            set_status("다운로드 완료 · 프로그램 종료 후 업데이트를 적용합니다.")
        else:
            dialog.selected_action = "download"
            set_status(f"다운로드 완료 · {path}\n프로그램을 종료합니다.")
        QTimer.singleShot(60 if active_action["value"] == "update" else 700, dialog.accept)

    def start_download(action: str) -> None:
        target_url = str(update_url or "").strip()
        if not target_url.lower().startswith(("https://", "http://")):
            set_status("관리 시트에 올바른 설치파일 주소가 등록되지 않았습니다.", error=True)
            return
        active_action["value"] = action
        destination = installer_destination(
            target_url,
            latest_version,
            save_only=action == "download",
        )
        set_busy(True)
        progress.setRange(0, 100)
        progress.setValue(0)
        progress.show()
        set_status("설치파일 다운로드를 시작합니다.")
        thread = QThread(dialog)
        worker = UpdateDownloadWorker(target_url, destination)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(update_progress)
        worker.completed.connect(download_completed)
        worker.failed.connect(download_failed)
        worker.completed.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        dialog.download_thread = thread
        dialog.download_worker = worker
        thread.start()

    download_button.clicked.connect(lambda: start_download("download"))
    update_button.clicked.connect(lambda: start_download("update"))
    dialog.exec()
    return dialog.selected_action
