import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication, QPushButton, QLabel
from PySide6.QtCore import QTimer
from ui.bom_page import BomStatusPage


class BomCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_comma_and_excel_column_preserve_active_order_and_deduplicate(self):
        page = SimpleNamespace(
            flow_view=SimpleNamespace(active_codes_by_stage=lambda: [['Q0433', 'BS0314', 'Q0433', 'BS0358']]),
            stage_copy_buttons=[QPushButton()], stage_vertical_copy_buttons=[QPushButton()],
            stage_copy_feedback_timers=[QTimer()], graph_note=QLabel())
        page._restore_stage_copy_button=lambda i: BomStatusPage._restore_stage_copy_button(page,i)
        with patch('ui.bom_page._set_persistent_clipboard_text') as clipboard:
            BomStatusPage._copy_active_stage_codes(page,0)
            clipboard.assert_called_with('Q0433, BS0314, BS0358')
            BomStatusPage._copy_active_stage_codes(page,0,vertical=True)
            clipboard.assert_called_with('Q0433\r\nBS0314\r\nBS0358')
            self.assertEqual(clipboard.call_args.args[0].splitlines(), ['Q0433','BS0314','BS0358'])
            page.stage_copy_feedback_timers[0].stop()
            page.flow_view.active_codes_by_stage=lambda: [[]]
            clipboard.reset_mock()
            BomStatusPage._copy_active_stage_codes(page,0,vertical=True)
            clipboard.assert_not_called()
