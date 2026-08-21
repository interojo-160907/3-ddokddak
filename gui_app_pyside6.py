from __future__ import annotations

import contextlib
import ctypes
import importlib
import io
import os
import sys
import traceback

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from config import (
    APP_DISPLAY_NAME,
    APP_USER_MODEL_ID,
    ASSET_DIR,
    DATA_CENTER_DIR,
    STYLE_DIR,
    ensure_directories,
)
from services.program_gate import ProgramGate
from ui.main_window import APP_VERSION, MainWindow
from ui.message_dialog import ask_app_confirmation
from ui.permission_dialog import show_permission_denied
from ui.startup_splash import StartupSplash


COLLECTOR_MODULES = {
    "aps_update_monitor": "collectors.aps_update_monitor",
    "bom_snapshot_collector": "collectors.bom_snapshot_collector",
    "data_retention_cleanup": "collectors.data_retention_cleanup",
    "process_status_collector": "collectors.process_status_collector",
    "production_performance_collector": "collectors.production_performance_collector",
    "refresh_all": "collectors.refresh_all",
}


def run_collector_mode(arguments: list[str]) -> int:
    collector_name = arguments[0] if arguments else ""
    module_name = COLLECTOR_MODULES.get(collector_name)
    if not module_name:
        return 2
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    exit_code = 0
    try:
        sys.argv = [collector_name, *arguments[1:]]
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            module = importlib.import_module(module_name)
            exit_code = int(module.main() or 0)
    except SystemExit as exc:
        exit_code = int(exc.code or 0)
    except Exception:
        traceback.print_exc(file=stderr_buffer)
        exit_code = 1
    for descriptor, output in ((1, stdout_buffer.getvalue()), (2, stderr_buffer.getvalue())):
        if not output:
            continue
        try:
            os.write(descriptor, output.encode("utf-8", errors="replace"))
        except OSError:
            pass
    return exit_code


def load_styles(app: QApplication) -> None:
    qss_path = STYLE_DIR / "app.qss"
    if qss_path.exists():
        app.setStyleSheet(qss_path.read_text(encoding="utf-8"))


def configure_windows_identity() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


def hide_collector_process_window() -> None:
    if sys.platform != "win32":
        return
    try:
        console = ctypes.windll.kernel32.GetConsoleWindow()
        if console:
            ctypes.windll.user32.ShowWindow(console, 0)
    except (AttributeError, OSError):
        pass


def collection_status_text() -> str:
    sources = (
        DATA_CENTER_DIR / "bom",
        DATA_CENTER_DIR / "process-status",
        DATA_CENTER_DIR / "production-performance",
    )
    ready = sum(1 for path in sources if path.exists())
    return f"API 수집 상태 확인 · {ready}/{len(sources)} 준비"


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "--collector":
        hide_collector_process_window()
        return run_collector_mode(sys.argv[2:])
    ensure_directories()
    configure_windows_identity()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(APP_DISPLAY_NAME)
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setOrganizationName("Ddokddak")
    app.setFont(QFont("Malgun Gothic", 10))

    icon = QIcon(str(ASSET_DIR / "ddokddak_app_icon.ico"))
    if not icon.isNull():
        app.setWindowIcon(icon)

    load_styles(app)
    splash = StartupSplash(ASSET_DIR / "ddokddak_mascot.png", module_count=6)
    splash.show_centered()
    splash.set_progress(1, 6, "사용 권한 확인")
    app.processEvents()

    gate = ProgramGate(APP_VERSION)
    identity = gate.identity()
    gate_result = gate.cached_permission()
    while gate_result is None:
        splash.set_progress(1, 6, "관리 서버에서 사용 권한 확인")
        app.processEvents()
        gate_result = gate.check()
        if gate_result.reason != "network":
            break
        retry = ask_app_confirmation(
            splash,
            "관리 서버 연결 확인",
            f"{gate_result.message}\n\n네트워크를 확인한 뒤 다시 시도해 주세요.",
            kind="warning",
            accept_text="다시 시도",
            reject_text="프로그램 종료",
        )
        if not retry:
            splash.close()
            return 2

    if not gate_result.allowed:
        show_permission_denied(
            splash,
            identity["pc_id"],
            gate_result.message or "등록되지 않았거나 사용이 중지된 PC입니다.",
        )
        splash.close()
        return 3

    splash.set_progress(2, 6, collection_status_text())
    app.processEvents()
    splash.set_progress(3, 6, "필수 모듈 백그라운드 활성화")
    app.processEvents()
    window = MainWindow()
    window.management_notices = list(gate_result.notices)
    if not icon.isNull():
        window.setWindowIcon(icon)

    screen = app.primaryScreen().availableGeometry()
    frame = window.frameGeometry()
    frame.moveCenter(screen.center())
    window.move(frame.topLeft())
    splash.set_progress(4, 6, "업무 화면 구성")
    app.processEvents()
    splash.set_progress(5, 6, "백그라운드 점검 예약")
    app.processEvents()
    splash.set_progress(6, 6, "준비 완료")
    app.processEvents()
    splash.finish(window)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
