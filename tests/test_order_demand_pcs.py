import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import sqlite3,tempfile,unittest
from pathlib import Path
from collectors.process_status_collector import _initialize
from services.process_status_service import ProcessStatusService
from ui.process_overview_page import DueDetailPage,DUE_DETAIL_COLUMNS
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

class OrderDemandPcsTest(unittest.TestCase):
    def test_pack_conversion_unique_sequences_across_processes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'aps.sqlite'
            db=sqlite3.connect(path);_initialize(db)
            for seq,qty,pack in [(9,125,5),(10,130,30)]:
                for stage in ['10','20','80']:
                    for repeat in range(2):
                        db.execute("INSERT INTO aps_plan(so_id,initial,demand_group_id,demand_item_id,demand_item_name,due_date,power,demand_type,dest_country,item_cd,demand_id,seq,demand_qty,pack_unit,oper_id,plan_qty,item_id,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",('O1','HW1','Sph','T0247A-02.50','Product','2026-11-01','-02.50','이니셜','US','T0247',f'O1_{seq}',seq,qty,pack,stage,10,'P0247A-02.50','{}'))
            db.commit();db.close()
            rows=ProcessStatusService(database_path=path).load_rows()
            self.assertEqual(1,len(rows));self.assertEqual(4525,rows[0]['오더수량PCS'])
            self.assertEqual(130,rows[0]['수주수량'])  # original field remains unchanged

    def test_toggle_fit_and_group_quantity(self):
        app=QApplication.instance() or QApplication([])
        page=DueDetailPage();page.resize(1800,800);page.show();app.processEvents()
        column=DUE_DETAIL_COLUMNS.index('오더 수량')
        self.assertEqual(len(DUE_DETAIL_COLUMNS)-1,column)
        self.assertTrue(page.table.table.isColumnHidden(column))
        height=page.table.table.horizontalHeader().height()
        page.order_quantity_check.setChecked(True);app.processEvents()
        self.assertEqual(height,page.table.table.horizontalHeader().height())
        self.assertEqual('오더수량',page.table.model.headerData(column,Qt.Horizontal))
        self.assertFalse(page.table.table.isColumnHidden(column))
        self.assertEqual(0,page.table.table.horizontalScrollBar().maximum())
        for check in page.code_checks.values():check.setChecked(True)
        app.processEvents();page._fit_order_columns();app.processEvents()
        self.assertEqual(0,page.table.table.horizontalScrollBar().maximum())
        page.reset_filters();app.processEvents()
        self.assertFalse(page.order_quantity_check.isChecked())
        self.assertTrue(page.table.table.isColumnHidden(column))
        self.assertEqual(0,page.table.table.horizontalScrollBar().maximum())
        row={'수주번호':'O1','이니셜':'A','품목코드':'T1','POWER':'-01.00','오더수량PCS':100}
        other=dict(row,수주번호='O2',오더수량PCS=200)
        self.assertEqual(300,page._group_order_quantity([row,row,other]))
        page.table.model.rows=[row]
        self.assertEqual('100',page.table.model.data(page.table.model.index(0,column),Qt.DisplayRole))
        page.close();page.deleteLater()


    def test_all_process_pages_reset_order_quantity(self):
        from ui.process_overview_page import ProcessOverviewPage
        from ui.live_production_need_page import LiveProductionNeedPage
        app=QApplication.instance() or QApplication([])
        for cls in (ProcessOverviewPage,LiveProductionNeedPage):
            for process in (None,'사출','분리','하이드레이션','접착','누수규격'):
                page=cls(fixed_process=process,initial_rows=[],monitor_changes=False,include_packaging=False) if cls is ProcessOverviewPage else cls(fixed_process=process)
                check=page.detail_page.order_quantity_check
                self.assertFalse(check.isChecked());check.setChecked(True)
                page.reset_all_filters();self.assertFalse(check.isChecked())
                page.close();page.deleteLater()
