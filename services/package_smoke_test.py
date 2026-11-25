"""Offline test of the shipped executable; never touches production data."""
import json
import tempfile
from datetime import datetime
from pathlib import Path


def run(report_path: str) -> int:
    import psycopg
    import psycopg_binary
    from services import server_read_settings,local_read_cache
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
    from ui.lidding_inventory_page import LiddingInventoryPage
    from unittest.mock import patch
    with patch("services.data_location.resolve_data_root",return_value=Path(directory)):
        lidding=LiddingInventoryPage();lidding.timer.stop();app.processEvents();lidding.close();lidding.deleteLater()
    widget.close()
    from services.update_smoke_test import run as update_buttons
    update_checks = update_buttons()
    from services.inventory_simulation import run as simulate_inventory
    inventory_simulation = simulate_inventory()
    from services.delivery_lifecycle import prune_stages, record_failure, retry_reason
    from services.collection_lock import collection_lock
    with tempfile.TemporaryDirectory(prefix='ddokddak-cleanup-test-') as scratch:
        base=Path(scratch); owner=base/'operating'; owner.mkdir()
        sentinel=owner/'user.txt'; sentinel.write_text('KEEP')
        stage=base/'prod3-delivery-failed'; stage.mkdir()
        (stage/'delivery-owner.json').write_text(json.dumps({'owner':str(owner.resolve()),'complete':False}))
        (stage/'payload').write_bytes(b'temporary calculation')
        with collection_lock(owner):
            assert len(prune_stages(owner,base=base))==1
        assert not stage.exists() and sentinel.read_text()=='KEEP'
        retry={};record_failure(retry,'package')
        assert retry_reason(retry,'package')
    Path(report_path).write_text(json.dumps({'ok':True,'app_version':__import__('config').APP_VERSION,'numpy':numpy.__version__,
        'inventory_simulation':inventory_simulation, 'update_buttons':update_checks,
        'checks':['GUI imports','Qt rendering','Excel numeric roundtrip','APS snapshot','R/Q/P matching','failed scratch cleanup','bounded retry']}),encoding='utf-8')
    return 0
