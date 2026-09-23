"""Open a separate 2.8.2 review window with an isolated writable data directory.

Uses real permission verification. Existing installed apps and their data are
not stopped/modified. The preview does not start an order collector.
"""
from pathlib import Path
from contextlib import closing
import os
import shutil
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def prepare():
    from services.data_location import resolve_data_root
    source=resolve_data_root()
    # Check authorization against the real configured permission service/cache.
    from services.program_gate import ProgramGate
    gate=ProgramGate('2.8.2')
    result=gate.cached_permission() or gate.check()
    if not result.allowed:
        raise RuntimeError('프로그램 사용 권한 확인이 필요합니다.')
    destination=ROOT/'qa'/'preview-data'
    destination.mkdir(parents=True,exist_ok=True)
    for relative in (
        'bom/product_reference.sqlite','bom/bom_change_history.sqlite',
        'process-status/aps_process_status.sqlite',
        'production-performance/production_performance.sqlite',
        'live-production-need/current_production_need.sqlite',
    ):
        src=source/relative;dst=destination/relative
        if not src.is_file():continue
        dst.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(src.as_uri()+'?mode=ro',uri=True)) as a, closing(sqlite3.connect(dst)) as b:
            a.backup(b)
    for folder in ('bom','process-status','production-performance','live-production-need','inventory-status'):
        for name in ('snapshot/refresh_status.json','refresh_status.json'):
            src=source/folder/name;dst=destination/folder/name
            if src.is_file():dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    hydration=Path('inventory-status/hydration_instructions.json')
    if (source/hydration).is_file():
        (destination/hydration).parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source/hydration,destination/hydration)
    os.environ['DDOKDDAK_PROD3_PREVIEW']='1'
    os.environ['DDOKDDAK_PROD3_ALLOW_PARALLEL']='1'
    os.environ['DDOKDDAK_PROD3_PREVIEW_DATA_DIR']=str(destination)
    # Recompute only the isolated copy with this development version's logic.
    from collectors.live_production_need_collector import calculate_completion_evidence, allocate_evidence
    live=destination/'live-production-need'/'current_production_need.sqlite'
    if live.exists():
        with closing(sqlite3.connect(live)) as db:
            allocate_evidence(db,calculate_completion_evidence(db));db.commit()
    return result


def main():
    permission=prepare()
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont,QIcon
    from config import ASSET_DIR,APP_VERSION,ensure_directories
    from gui_app_pyside6 import load_styles,configure_windows_identity
    from ui.table_clipboard import install_table_clipboard
    import ui.main_window as mw
    from services.windows_icon import apply_taskbar_icon

    # Review windows must not replace the installed application's online status.
    class PreviewPresence:
        def __init__(self,*args):pass
        def heartbeat(self):pass
        def close(self):pass
    mw.ProgramPresence=PreviewPresence

    class PreviewWindow(mw.MainWindow):
        def _start_runtime_permission_check(self,*args,**kwargs):pass
        def _start_notice_check(self,*args,**kwargs):pass
        def _start_api_health_check(self,*args,**kwargs):pass
        def _start_mode_check(self,*args,**kwargs):pass

    configure_windows_identity();ensure_directories()
    app=QApplication(sys.argv);app.setFont(QFont('Malgun Gothic',10))
    app.setWindowIcon(QIcon(str(ASSET_DIR/'ddokddak_app_icon.ico')))
    load_styles(app);install_table_clipboard(app)
    window=PreviewWindow(permission.notices)
    window.setWindowTitle(f'똑딱이 생산3팀 전용 · v{APP_VERSION} 개발 검토용')
    window.showMaximized();window.show_page('process_overview')
    if '--order-quantity' in sys.argv:
        window.process_overview_page.detail_page.order_quantity_check.setChecked(True)
    apply_taskbar_icon(window,ASSET_DIR/'ddokddak_app_icon.ico')
    def capture():
        window.grab().save(str(ROOT/'qa'/'order-quantity-preview.png'))
    QTimer.singleShot(18000,capture)
    return app.exec()


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception:
        import traceback
        (ROOT/'qa').mkdir(exist_ok=True)
        (ROOT/'qa'/'preview-error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        raise
