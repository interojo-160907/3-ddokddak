"""Offline test of the shipped executable; never touches production data."""
import json
import tempfile
from datetime import datetime
from pathlib import Path


def _verify_recovery(root, database, app):
    """Read later-version shaped local data; exercise the shipped legacy UI offline."""
    from contextlib import closing
    import importlib.util
    import sqlite3
    from unittest.mock import patch
    from config import APP_VERSION, collection_directories
    from collectors.live_production_need_collector import _ensure_live_schema
    from services import live_production_need_service as live
    from services import collection_schedule
    from services.lot_work_order_service import LotWorkOrderService
    from ui.live_production_need_page import LiveProductionNeedPage
    from ui.process_overview_page import DataTable
    from PySide6.QtCore import QCoreApplication, QEvent
    from gui_app_pyside6 import COLLECTOR_MODULES

    assert APP_VERSION == '2.7.10'
    assert 'inventory_live_refresh' not in COLLECTOR_MODULES
    assert importlib.util.find_spec('services.hydration_instruction_service') is None
    assert importlib.util.find_spec('ui.inventory_master_page') is None
    # The shared APS/live table schemas are unchanged between 2.6.4 and 2.7.9.
    # Later-version status JSON has extra fields; those must not prevent reads.
    for directory in collection_directories(root):
        directory.mkdir(parents=True, exist_ok=True)
    status = root / 'live-production-need/snapshot/refresh_status.json'
    status.write_text(json.dumps({'status': 'success', 'refreshed_at': '2026-09-19T08:00:00+09:00',
        'aps_source_refreshed_at': '2026-09-18T15:47:03+09:00',
        'collection_started_at': '2026-09-19T07:59:00+09:00',
        'hydration': {'status': 'error'}, 'inventory_sources': {}, 'version': '2.7.9'}), encoding='utf-8')
    schedule = root / 'settings/collection_schedule.json'
    schedule.write_text(json.dumps({'bom_minutes': 120, 'aps_minutes': 1,
        'production_minutes': 60, 'live_minutes': 0, 'hydration_minutes': 60}), encoding='utf-8')
    unused = root / 'inventory-status/hydration_instructions.json'
    unused.parent.mkdir(parents=True, exist_ok=True)
    unused.write_text('{"status":"error","preserve":true}', encoding='utf-8')
    before = {p: p.read_bytes() for p in (status, schedule, unused)}
    with closing(sqlite3.connect(database)) as con, con:
        _ensure_live_schema(con)
        con.execute("INSERT INTO current_inventory(snapshot_key,warehouse_code,warehouse_name,stage,lot_full,item_id,stock_qty) VALUES(?,?,?,?,?,?,?)",
                    ('test', 'G007', '검사접착', 4, 'S20260919-001', 'P1103A-03.00-1.25010', 300))
    database_before = database.read_bytes()
    with patch('requests.sessions.Session.request', side_effect=AssertionError('Recovery smoke must remain offline')), \
            patch.object(live, 'DB_PATH', database), patch.object(live, 'STATUS_PATH', status), \
            patch.object(collection_schedule, 'SETTINGS_PATH', schedule):
        assert collection_schedule.load_schedule()['bom_minutes'] == 120
        assert collection_schedule.load_schedule()['live_minutes'] == 0
        rows = live.LiveProductionNeedService().load_rows()
        assert len(rows) == 1 and rows[0]['누수규격'] == 1008
        lots = LotWorkOrderService(database, root/'missing-production.sqlite', root/'missing-bom.sqlite')
        assert len(lots.load_rows('누수규격')) == 1
        page = LiveProductionNeedPage()
        assert len(page.all_rows) == 1
        assert '계산 완료' in page.calculation_status.text()
        page.resize(1420, 860)
        assert not page.grab().isNull()
        page.snapshot_timer.stop()
        # The legacy hover popups are top-level windows. Detach their filters
        # before destroying the test page so Qt teardown is deterministic.
        for table in page.findChildren(DataTable):
            table.table.viewport().removeEventFilter(table)
            table._product_popup.removeEventFilter(table)
            table._product_list.viewport().removeEventFilter(table)
            table._product_popup.close()
            table._product_popup.deleteLater()
        page.close()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
    assert database.read_bytes() == database_before
    assert all(p.read_bytes() == value for p, value in before.items())
    return ['2.6.4 collector set', 'new inventory/hydration modules absent',
            'later-version shared database and LOT reads', 'existing collection preferences preserved',
            'real legacy live page renders offline', 'database and later-version files unchanged']


def run(report_path: str) -> int:
    import numpy
    from openpyxl import Workbook
    from PySide6.QtWidgets import QApplication, QWidget
    from services.safe_mode import build_snapshot
    from services.process_status_service import ProcessStatusService

    app = QApplication.instance() or QApplication([])
    widget = QWidget()
    widget.resize(100, 100)
    assert not widget.grab().isNull()
    with tempfile.TemporaryDirectory(prefix='ddokddak-package-test-') as directory:
        root = Path(directory)
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(['설비 사이트 코드','고객 이름','이니셜','수주번호',
            '신규분류 요약코드','수요 제품 이름','제품 코드','납기일',
            '[10]사출','[20]분리','[45]하이드레이션','[55]접착','[80]누수','생산 수량'])
        for item, quantities in [('R1103-03.00-1.25010',[1444,0,0,0,0]),
                ('Q1103-03.00-1.25010',[0,1444,0,0,0]),
                ('P1103A-03.00-1.25010',[0,0,1067,1065,1008])]:
            sheet.append(['S관(3공장)','','안전(해외)','SMOKE',
                'Si_1-Day_Toric','SMOKE PRODUCT',item,datetime(2026,9,7),
                *[numpy.int64(v) for v in quantities],sum(quantities)])
        source = root/'input.xlsx'
        workbook.save(source)
        workbook.close()
        database = root/'aps.sqlite'
        build_snapshot(source,database,{'label':'2026-09-07 오전'})
        rows = ProcessStatusService(database_path=database).load_rows()
        assert len(rows) == 1
        assert [rows[0][k] for k in ['사출','분리','하이드레이션','접착','누수규격']] == [1444,1444,1067,1065,1008]
        rollback_checks = _verify_recovery(root, database, app)
    widget.close()
    from config import APP_VERSION
    Path(report_path).write_text(json.dumps({'ok':True,'numpy':numpy.__version__,
        'app_version':APP_VERSION,'restored_from':'2.6.4','rollback_checks':rollback_checks,
        'checks':['GUI imports','Qt rendering','Excel numeric roundtrip','APS snapshot','R/Q/P matching']}),encoding='utf-8')
    return 0
