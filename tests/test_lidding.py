"""Synthetic lidding contracts; no employee data or server access."""
import copy,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from services.lidding_data import build_model,select_rows,cache_path,cache_ready

def bundle():
    return dict(schema_version=1,version='live-test',aps_key='test',aps_at='2026-11-18 07:00:00',day='2026-11-18',payloads={
        'aps_arrange':[dict(sale_cd=p,fac_cd='04') for p in ('PTEST1','PTEST2','PTEST3')],
        'aps_backward_plan':[dict(sale_cd=p,demand_id=f'{market}_ORDER_{i}_1',item_id=p+'-POWER',backward_qty=qty,due_date=due)
            for i,(p,market,qty,due) in enumerate([('PTEST1','국내',100,'2026-11-20'),('PTEST2','PB',50,'2026-12-01')])],
        'bom_explosion':[dict(parent_cd=p,parent_nm=p,child_cd='BSTEST',child_nm='테스트 리드지',child_spec='SPEC',qty=1) for p in ('PTEST1','PTEST2','PTEST3')],
        'prod3_lidding_stock_all':[dict(itm_cd='BSTEST',wh_cd=w,wh_nm=w,stock_qty=q,um_bc_nm='EA') for w,q in [('300',1000),('S100',14)]],
        'prod3_lidding_issue_bstest':[dict(dt1='2026-11-17',cd2='LE200500',out_qty=70,in_qty=0)]})

class LiddingContracts(unittest.TestCase):
    def test_bootstrap_accepts_verified_unchanged_bom_but_requires_lidding(self):
        from ui.main_window import _version_bootstrap_ready
        report={'completed_at':'2026-11-18','results':{k:{'status':'success'} for k in ('bom','aps','production','live')}}
        report['results']['bom']['status']='skipped'
        self.assertFalse(_version_bootstrap_ready(report))
        report['results']['lidding']={'status':'success'}
        self.assertTrue(_version_bootstrap_ready(report))

    def test_total_stock_available_not_only_s(self):
        r=select_rows(build_model(bundle()))[0][0]
        self.assertEqual((r['stock'],r['rate'],r['days']),(1014,10,101.4))
        self.assertTrue(r['normal']);self.assertFalse(r['lowdays'])
        self.assertEqual(len(r['products']),3)  # Includes no-current-demand product.
        self.assertEqual((r['need'],r['goal']),(150,225))
    def test_multiselect_due_and_stock_not_multiplied(self):
        m=build_model(bundle())
        full=select_rows(m)[0]
        self.assertEqual(full,select_rows(m,{'해외','PB','국내','안전'})[0])
        r=select_rows(m,{'국내','PB'},'2026-11-25')[0][0]
        self.assertEqual((r['need'],r['goal'],r['stock']),(100,150,1014))
    def test_missing_data_preserves_unknown_or_rejects(self):
        b=bundle();b['payloads']['prod3_lidding_stock_all']=[]
        r=select_rows(build_model(b))[0][0]
        self.assertIsNone(r['stock']);self.assertTrue(r['unknown']);self.assertFalse(r['normal'])
        del b['payloads']['prod3_lidding_issue_bstest']
        with self.assertRaises(ValueError):build_model(b)
    def test_reject_nonfinite_quantities(self):
        for ds,field in [('prod3_lidding_stock_all','stock_qty'),('prod3_lidding_issue_bstest','out_qty')]:
            b=bundle();b['payloads'][ds][0][field]=float('nan')
            with self.assertRaises(ValueError):build_model(b)
    def test_one_time_same_version_upgrade_gate(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(cache_ready(d,'live-test'))
            p=cache_path(d);p.parent.mkdir(parents=True);p.write_text(json.dumps(bundle()),encoding='utf-8')
            self.assertTrue(cache_ready(d,'live-test'))
            self.assertFalse(cache_ready(d,'next-live'))
            b=bundle();b.pop('schema_version');p.write_text(json.dumps(b),encoding='utf-8')
            self.assertFalse(cache_ready(d,'live-test'))
    def test_ui_filters_details_dates_and_no_network_poll(self):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt,QPoint,QDate
        from PySide6.QtTest import QTest
        from ui.lidding_inventory_page import LiddingInventoryPage
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as d,patch('services.data_location.resolve_data_root',return_value=Path(d)):
            p=cache_path();p.parent.mkdir(parents=True);p.write_text(json.dumps(bundle()),encoding='utf-8')
            page=LiddingInventoryPage();page.timer.stop();page.resize(1640,950);page.show();QTest.qWait(30)
            page.reload_data();self.assertEqual(len(page.visible_rows),1)
            with patch('ui.lidding_inventory_page.build_model') as build:
                for _ in range(20):page.reload_data()
                build.assert_not_called()
            before=page.table.height();page.show_detail(0);QTest.qWait(30)
            tabs=page.detail_tabs.tabBar();QTest.mouseClick(tabs,Qt.LeftButton,pos=tabs.tabRect(1).center())
            QTest.qWait(30);self.assertTrue(page.detail_panel.isVisible())
            QTest.mouseClick(page,Qt.LeftButton,pos=QPoint(3,3));QTest.qWait(30)
            self.assertFalse(page.detail_panel.isVisible());self.assertEqual(page.table.height(),before)
            page.due.button(4).click();self.assertEqual(page.due_end.date(),QDate.currentDate().addDays(14))
            page.reset_filters()
            for i in range(1,5):page.progress.button(i).click()
            self.assertEqual(page.selected_markets(),{'전체'})
            self.assertEqual(page.table.horizontalScrollBar().maximum(),0)
            page.close();page.deleteLater();app.processEvents()

if __name__=='__main__':unittest.main()
