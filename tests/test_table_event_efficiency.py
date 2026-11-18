import unittest
from unittest.mock import patch
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QTableWidget, QTableWidgetItem
from PySide6.QtGui import QKeyEvent
from ui.table_clipboard import TableClipboardController
from ui.process_overview_page import RowTableModel


class TableEventEfficiencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_paint_does_not_walk_parents_but_copy_still_works(self):
        table=QTableWidget(1,1);table.setHorizontalHeaderLabels(['POWER'])
        table.setItem(0,0,QTableWidgetItem('-0.00'));table.setCurrentCell(0,0)
        controller=TableClipboardController()
        with patch('ui.table_clipboard._table_for') as lookup:
            controller.eventFilter(table.viewport(),QEvent(QEvent.Paint))
            lookup.assert_not_called()
        event=QKeyEvent(QEvent.KeyPress,Qt.Key_C,Qt.ControlModifier)
        self.assertTrue(controller.eventFilter(table.viewport(),event))
        self.assertIn('-0.00',self.app.clipboard().text())
        self.app.clipboard().clear()
        table.deleteLater()

    def test_model_display_copy_and_order_tooltip_are_preserved(self):
        model=RowTableModel(['사출','오더 수량','수주번호'])
        row={'공정':{'사출':1234},'오더수량PCS':None,'수주번호':'R1','비고':'검토'}
        model.set_rows([row])
        self.assertEqual(model.data(model.index(0,0),Qt.DisplayRole),'1,234')
        self.assertEqual(model.data(model.index(0,0),Qt.UserRole),1234.0)
        self.assertIs(model.data(model.index(0,0),Qt.UserRole+1),row)
        self.assertEqual(model.data(model.index(0,1),Qt.DisplayRole),'미확인')
        self.assertEqual(model.data(model.index(0,2),Qt.ToolTipRole),'R1\n비고: 검토')
        self.assertIsNone(model.data(model.index(0,0),Qt.BackgroundRole))
