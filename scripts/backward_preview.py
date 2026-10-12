"""Run the source GUI against isolated data; never register a production PC session.

python scripts/backward_preview.py [--verify-tabs]
The caller must populate validation-data with collector output/reference copies first.
"""
from pathlib import Path
import json
import os
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Discard inherited per-collector overrides so no test tab can read production DBs.
for key in list(os.environ):
    if key.startswith("DDOKDDAK_PROD3_") and (key.endswith("_DIR") or key.endswith("_DB")):
        os.environ.pop(key)
os.environ["DDOKDDAK_PROD3_PREVIEW"] = "1"
os.environ["DDOKDDAK_PROD3_ALLOW_PARALLEL"] = "1"
os.environ["DDOKDDAK_PROD3_PREVIEW_DATA_DIR"] = str(ROOT / "validation-data")

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QTableView, QTabWidget
from config import APP_VERSION, ensure_directories
from gui_app_pyside6 import load_styles, configure_windows_identity
from ui.main_window import MainWindow
from services.program_presence import ProgramPresence


def no_background(*args, **kwargs):
    return None


# Only this review launcher replaces external management/collection callbacks.
# Production entry point and permission enforcement are unchanged.
for method in ("_start_mode_check", "_start_runtime_permission_check", "_start_notice_check",
               "_start_api_health_check", "_start_aps_monitor_check", "_start_data_cleanup",
               "_run_scheduled_collections", "_start_data_collection", "_start_full_data_refresh",
               "_run_global_refresh", "_apply_collection_timers"):
    setattr(MainWindow, method, no_background)
ProgramPresence.heartbeat = no_background
ProgramPresence.close = no_background

ensure_directories()
configure_windows_identity()
app = QApplication(sys.argv)
app.setFont(QFont("Malgun Gothic", 10))
load_styles(app)
window = MainWindow()
window.setWindowTitle(f"생산3팀 v{APP_VERSION} · Backward 전환 검증 · 격리 데이터")
window.showMaximized()
ARTIFACTS = ROOT / "validation-artifacts" / "gui"
ARTIFACTS.mkdir(parents=True, exist_ok=True)
report = {"pages": [], "errors": []}


def exception_hook(kind, value, tb):
    report["errors"].append("".join(traceback.format_exception(kind, value, tb)))
    (ARTIFACTS / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    traceback.print_exception(kind, value, tb)


sys.excepthook = exception_hook


def verify_next():
    if not pending:
        window.show_page("process_overview")
        (ARTIFACTS / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("GUI verification complete", json.dumps(report, ensure_ascii=False), flush=True)
        return
    key = pending.pop(0)
    try:
        window.show_page(key)
        app.processEvents()
        page = window.stack.currentWidget()
        tables = [{"class": type(table.model()).__name__, "rows": table.model().rowCount(),
                   "columns": table.model().columnCount()} for table in page.findChildren(QTableView) if table.model()]
        tabs = []
        for tab in page.findChildren(QTabWidget):
            for i in range(tab.count()):
                tab.setCurrentIndex(i)
                app.processEvents()
                tabs.append(tab.tabText(i))
            tab.setCurrentIndex(0)
        if hasattr(page, "reset_all_filters"):
            page.reset_all_filters()
        if hasattr(page, "detail_page") and hasattr(page.detail_page, "order_quantity_check"):
            check = page.detail_page.order_quantity_check
            check.setChecked(True)
            app.processEvents()
            check.setChecked(False)
        app.processEvents()
        window.grab().save(str(ARTIFACTS / (key + ".png")))
        report["pages"].append({"key": key, "tables": tables, "subtabs": tabs, "rendered": True})
        print("verified", key, tables, flush=True)
    except Exception:
        exception_hook(*sys.exc_info())
    QTimer.singleShot(700, verify_next)


pending = list(window.page_indexes)
if "--verify-tabs" in sys.argv:
    QTimer.singleShot(4000, verify_next)
else:
    QTimer.singleShot(500, lambda: window.show_page("process_overview"))
sys.exit(app.exec())
