import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from services import safe_mode as safe
from services.process_status_service import ProcessStatusService
from services import dashboard_service as dashboard

HEADERS = ['설비 사이트 코드','고객 이름','이니셜','수주번호','신규분류 요약코드','수요 제품 이름','제품 코드','납기일','[10]사출조립','[20]분리','[45]하이드레이션/전면검사','[55]접착/멸균','[80]누수/규격검사','생산 수량']

class SafeModeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [patch.object(safe,'SAFE_ROOT',self.root/'안전모드'), patch.object(safe,'STATE',self.root/'안전모드'/'적용정보.json'), patch.object(safe,'_active',{}), patch.object(safe,'_restored',True)]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()

    def build(self, quantity=10):
        detail = ['S관(3공장)','고객','AA1494','202609010001','FRP_Clear_Sph','품명','R1000-01.00',datetime(2026,9,15),quantity,4,3,2,1,20]
        # Identical details are additive. Totals and other sites must be excluded.
        rows = [HEADERS,detail,detail,['총합계']*8+[20,8,6,4,2,40],['A관(1공장)']+detail[1:]]
        book = SimpleNamespace(worksheets=[SimpleNamespace(iter_rows=lambda **kw: iter(rows))],close=lambda:None)
        info={'digest':'a'*64,'name':'260907_오전.xlsx','label':'2026-09-07 오전','selection_id':'test'}
        folder=safe.SAFE_ROOT/info['digest'];folder.mkdir(parents=True,exist_ok=True)
        with patch.object(safe,'load_workbook',return_value=book):
            safe.build_snapshot(self.root/'input.xlsx',folder/'aps.sqlite',info)
        return info

    def test_damaged_snapshot_is_not_reused(self):
        info = self.build()
        folder = safe.SAFE_ROOT / info['digest']
        status_path = folder / '스냅샷.json'
        status = json.loads(status_path.read_text(encoding='utf-8'))
        status['snapshot_revision'] = safe.SNAPSHOT_REVISION
        status_path.write_text(json.dumps(status), encoding='utf-8')
        self.assertTrue(safe.snapshot_valid(folder, info))
        (folder / 'aps.sqlite').write_bytes(b'broken database')
        self.assertFalse(safe.snapshot_valid(folder, info))

    def test_failed_deactivation_keeps_active_snapshot(self):
        info = self.build()
        safe.activate(info)
        with patch.object(Path, 'unlink', side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError): safe.deactivate()
        self.assertEqual(safe.active(), info)
        self.assertTrue(safe.STATE.exists())

    def test_download_failure_preserves_current_snapshot(self):
        info = self.build()
        safe.activate(info)
        asset = {'name': '260908_오전.xlsx', 'url': 'https://raw.githubusercontent.com/interojo-160907/3-ddokddak/' + 'a'*40 + '/test.xlsx'}
        with patch.object(safe.requests, 'get', side_effect=safe.requests.Timeout):
            with self.assertRaises(safe.requests.Timeout): safe.prepare(asset)
        self.assertEqual(safe.active(), info)
        self.assertTrue(safe.database(self.root/'auto.sqlite').is_file())

    def test_filename_order_and_validation(self):
        names=['260908_오전.xlsx','260907_오후.xlsx','260908_오후.xlsx']
        self.assertEqual(max(names,key=safe.file_key),'260908_오후.xlsx')
        for name in ['260908 오전.xlsx','260231_오전.xlsx','../260908_오전.xlsx']:
            with self.assertRaises(ValueError): safe.file_key(name)

    def test_import_totals_routing_restart_and_cleanup(self):
        info=self.build()
        automatic=self.root/'automatic.sqlite';automatic.write_bytes(b'untouched')
        service=ProcessStatusService()
        safe.activate(info)
        rows=service.load_rows()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['사출'],20)
        self.assertEqual(rows[0]['누수규격'],2)
        self.assertEqual(rows[0]['진행현황'],'해외')
        with patch.object(dashboard,'APS_DB',automatic):
            result=dashboard.DashboardService().load()
            self.assertEqual(result['shortage']['사출'],20)
            self.assertFalse(dashboard._live_cycle_matches())
        safe._active={};safe._restored=False
        self.assertEqual(safe.active()['selection_id'],'test')
        safe.cleanup();self.assertTrue(service.database_path.exists())
        safe.deactivate();safe.cleanup()
        self.assertFalse((safe.SAFE_ROOT/info['digest']).exists())
        self.assertEqual(automatic.read_bytes(),b'untouched')

    def test_invalid_quantity_never_activates(self):
        with self.assertRaises(ValueError):self.build(-1)
        self.assertFalse(safe.active())

    def test_process_codes_join_by_demand_and_exact_optical_spec(self):
        def row(item, qty, order='R202608110001', name='Toric 14.5', due=datetime(2026,9,5)):
            return ['S관(3공장)','','안전(해외)',order,'Si_1-Day_Toric',name,item,due,*qty,sum(qty)]
        rows=[HEADERS,
            row('R1103+03.75-1.25010',[1443,0,0,0,0]),
            row('Q1103+03.75-1.25010',[0,1443,0,0,0]),
            row('P1103A+03.75-1.25010',[0,0,1067,1065,1008]),
            row('Q1103+03.75-1.25020',[0,20,0,0,0]),
            row('Q1103+03.75-1.25010',[0,30,0,0,0],order='OTHER'),
            row('Q1103+03.75-1.25010',[0,40,0,0,0],name='Other product'),
            row('Q1103+03.75-1.25010',[0,50,0,0,0],due=datetime(2026,9,6))]
        book=SimpleNamespace(worksheets=[SimpleNamespace(iter_rows=lambda **kw:iter(rows))],close=lambda:None)
        path=self.root/'joined.sqlite'
        with patch.object(safe,'load_workbook',return_value=book):
            safe.build_snapshot(self.root/'input.xlsx',path,{'label':'2026-09-07 오전'})
        result=ProcessStatusService(database_path=path).load_rows()
        self.assertEqual(len(result),5)
        match=next(r for r in result if r['사출']==1443)
        self.assertEqual([match[k] for k in ['사출','분리','하이드레이션','접착','누수규격']],[1443,1443,1067,1065,1008])
        self.assertEqual((match['POWER'],match['CP'],match['AXIS']),('+03.75','-1.25','010'))
        self.assertEqual(match['Q코드'],'Q1103+03.75-1.25010')
        self.assertEqual(match['P코드'],'P1103A+03.75-1.25010')

    def test_old_api_does_not_silently_switch_to_auto(self):
        response=Mock();response.json.return_value={'result':{'allowed':True}}
        with patch.object(safe,'ProgramGate') as gate,patch.object(safe.requests,'post',return_value=response):
            gate.return_value.endpoint='https://example.invalid';gate.return_value.identity.return_value={}
            with self.assertRaises(RuntimeError):safe.fetch_control()

    def test_disabled_pages_and_restore(self):
        from PySide6.QtWidgets import QApplication,QMainWindow,QPushButton,QStackedWidget,QWidget
        from ui.main_window import MainWindow
        app=QApplication.instance() or QApplication([])
        w=MainWindow.__new__(MainWindow);QMainWindow.__init__(w)
        w.nav_buttons={};w.page_indexes={};w.stack=QStackedWidget(w);w._current_page='live_need';w.show_page=Mock()
        for key in ['live_need','lot_work_order',*w.LIVE_PROCESS_NAV,*w.LOT_PROCESS_NAV]:
            w.nav_buttons[key]=QPushButton(w);w.page_indexes[key]=w.stack.addWidget(QWidget())
        safe.activate(self.build());w._sync_mode_controls()
        self.assertTrue(all(not b.isEnabled() for b in w.nav_buttons.values()))
        w.show_page.assert_called_once_with('process_overview')
        safe.deactivate();w._sync_mode_controls()
        self.assertTrue(all(b.isEnabled() for b in w.nav_buttons.values()))
        w.deleteLater()

    def test_color_process_aliases_join_but_sales_names_stay_separate(self):
        def row(name, item, qty):
            return ['S관(3공장)','','안전(해외)','R202609010003','1-Day_Color_Sph',name,item,datetime(2026,10,11),*qty,sum(qty)]
        rows=[HEADERS,
            row('PIA_BROWN MANEGE','Q0007-04.00RHA2',[0,704,0,0,0]),
            row('PIA_BROWN MANEGE','P0106A-04.00CRG3',[0,0,0,525,512]),
            row('IRIS_Rhapsody(NEW)','Q0007-04.00RHA2',[0,1551,0,0,0]),
            row('Nocturne','Q5362-04.00MNO',[0,1227,0,0,0])]
        book=SimpleNamespace(worksheets=[SimpleNamespace(iter_rows=lambda **kw:iter(rows))],close=lambda:None)
        path=self.root/'colors.sqlite'
        with patch.object(safe,'load_workbook',return_value=book):
            safe.build_snapshot(self.root/'input.xlsx',path,{'label':'2026-09-07 오전'})
        result=ProcessStatusService(database_path=path).load_rows()
        self.assertEqual(len(result),3)
        pia=next(r for r in result if r['품명']=='PIA_BROWN MANEGE')
        self.assertEqual([pia[k] for k in ['분리','접착','누수규격']],[704,525,512])
        nocturne=next(r for r in result if r['품명']=='Nocturne')
        self.assertEqual([nocturne[k] for k in ['사출','분리','하이드레이션','접착','누수규격']],[0,1227,0,0,0])

if __name__=='__main__':unittest.main()
