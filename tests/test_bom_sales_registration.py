from contextlib import closing
from datetime import datetime, timedelta
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from services.bom_explorer import BomExplorerService


class BomSalesRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backups = self.root / 'backup'
        self.backups.mkdir()
        self.current = self.root / 'current.sqlite'
        self.history = self.root / 'history.sqlite'
        self.today = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def snapshot(self, path, products):
        with closing(sqlite3.connect(path)) as con, con:
            con.executescript('''
                CREATE TABLE product_name_master(nm_cd TEXT, nm_nm TEXT,
                    fac_cd TEXT, fac_nm TEXT, in_dt TEXT, extracted_at TEXT);
                CREATE TABLE bom_relation(parent_cd TEXT, child_cd TEXT, qty REAL,
                    parent_nm TEXT, child_nm TEXT, extracted_at TEXT);
            ''')
            con.executemany('INSERT INTO product_name_master VALUES(?,?,?,?,?,?)',
                [(code, code+' product', factory, '', registered, self.today)
                 for code, factory, registered in products])

    def service(self):
        return BomExplorerService(self.current, self.backups, self.history)

    def test_current_registration_dates_include_s_and_t_only_within_retention(self):
        old = (datetime.now()-timedelta(days=100)).isoformat()
        self.snapshot(self.current, [('T1000','04',self.today),
            ('s1000','04',self.today), ('P1000','04',self.today),
            ('S0001','04',old), ('S0002','04','')])
        rows = self.service().bom_change_overview()['registrations']
        self.assertEqual({r['code'] for r in rows}, {'S1000','T1000'})
        self.assertTrue(all(r['factory']=='S관(3공장)' for r in rows))

    def prepare_comparison(self):
        previous = self.backups / 'previous.sqlite'
        self.snapshot(previous, [('T0001','04',''),('S0001','01','')])
        self.snapshot(self.current, [('T0001','04',''),('S0001','04',''),
            ('T1000','04',''),('S1000','04',''),('P1000','04','')])
        return previous

    def test_snapshot_detects_s_registration_and_factory_change(self):
        self.prepare_comparison()
        overview = self.service().bom_change_overview()
        self.assertEqual({r['code'] for r in overview['registrations']}, {'S1000','T1000'})
        self.assertEqual([(r['code'],r['change_type']) for r in overview['modifications']],
                         [('S0001','생산공장 변경')])
        with closing(sqlite3.connect(self.history)) as con:
            self.assertEqual(con.execute("SELECT change_type FROM bom_change_event WHERE code='S1000'").fetchone()[0],
                             'S코드 신규등록')

    def test_legacy_processed_pair_backfills_s_without_duplicating_t(self):
        self.prepare_comparison()
        self.service().bom_change_overview()
        with closing(sqlite3.connect(self.history)) as con, con:
            con.execute("DELETE FROM bom_change_event WHERE code LIKE 'S%'")
            con.execute("UPDATE processed_comparison SET pair_key=replace(pair_key,'sales-ts-v1|','')")
        for _ in range(2):
            result = self.service().bom_change_overview()
            self.assertEqual({r['code'] for r in result['registrations']}, {'S1000','T1000'})
        with closing(sqlite3.connect(self.history)) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM bom_change_event').fetchone()[0], 3)

    def test_ui_displays_and_filters_s_and_t_registrations(self):
        from PySide6.QtWidgets import QApplication, QWidget
        from ui.bom_page import BomStatusPage
        app = QApplication.instance() or QApplication([])
        self.snapshot(self.current, [('T1000','04',self.today),
                                    ('S1000','04',self.today),('S2000','01',self.today)])
        page = BomStatusPage.__new__(BomStatusPage)
        QWidget.__init__(page)
        page.service = self.service()
        tab = page._build_change_tab()
        try:
            page._load_change_rows()
            self.assertEqual(page.registration_table.rowCount(), 3)
            page.registration_factory.setCurrentIndex(page.registration_factory.findData('S관(3공장)'))
            self.assertEqual({page.registration_table.item(i,1).text() for i in range(2)}, {'S1000','T1000'})
            self.assertEqual(page.registration_table.rowCount(), 2)
            self.assertEqual(page.registration_table.horizontalHeaderItem(1).text(), 'T·S코드')
            self.assertIn('신규 T·S코드 2건', page.registration_result.text())
            self.assertFalse(tab.grab().isNull())
        finally:
            tab.close()
            page.close()
            app.processEvents()


if __name__ == '__main__':
    unittest.main()
