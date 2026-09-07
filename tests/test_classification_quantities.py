import os
import unittest
from shiboken6 import delete
from unittest.mock import Mock
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QWidget, QGridLayout, QLabel
from ui.process_overview_page import DueDetailPage
from ui.lot_work_order_page import LotWorkOrderPage

class ClassificationQuantityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_overview_and_each_process_show_single_stage(self):
        values = {"사출": 1258296, "분리": 339105, "하이드레이션": 247555,
                  "접착": 252339, "누수규격": 236789, "포장": 999999}
        rows = [{"신규분류요약": "Toric", "공정": values}]
        for process in [None, "사출", "분리", "하이드레이션", "접착", "누수규격"]:
            page = DueDetailPage(fixed_process=process)
            page._apply_filter = Mock()
            page.load(rows)
            expected = values[process or "누수규격"]
            self.assertEqual(page.classification_buttons["전체"].text(), f"전체 ({expected:,})")
            self.assertEqual(page.classification_buttons["Toric"].text(), f"Toric ({expected:,})")
            if process is None:
                for button in page.process_buttons:
                    button.setChecked(button.property("processName") == "사출")
                page.load(rows)
                self.assertEqual(page.classification_buttons["전체"].text(), "전체 (1,258,296)")
            delete(page)

    def test_lot_overview_keeps_zero_categories_and_internal_stage_quantity(self):
        rows = [{"신규분류요약": "Toric", "_공정": "사출", "재고수량": 900},
                {"신규분류요약": "Toric", "_공정": "누수규격", "재고수량": 120},
                {"신규분류요약": "Sph", "_공정": "분리", "재고수량": 80}]
        for process in [None, "사출", "분리", "하이드레이션", "접착", "누수규격"]:
            page = LotWorkOrderPage.__new__(LotWorkOrderPage)
            QWidget.__init__(page)
            page.fixed_process = process
            page.classification_layout = QGridLayout(page)
            page.classification_layout.addWidget(QLabel("분류"), 0, 0)
            source = rows if process is None else [r for r in rows if r["_공정"] == process]
            page._work_target_rows = Mock(return_value=source)
            page._selected_markets = Mock(return_value={"전체"})
            page._row_for_selected_markets = Mock(side_effect=lambda row, markets: row)
            page._rebuild_classification_filters()
            expected = 120 if process is None else sum(r["재고수량"] for r in source)
            self.assertEqual(page.lot_classification_buttons["전체"].text(), f"전체 ({expected:,})")
            if process is None:
                self.assertEqual(page.lot_classification_buttons["Sph"].text(), "Sph (0)")
            delete(page)
