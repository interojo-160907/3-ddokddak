from __future__ import annotations

import os
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.main_window import MainWindow  # noqa: E402


class SidebarAndRiskUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _bare_window(self) -> MainWindow:
        window = MainWindow.__new__(MainWindow)
        QMainWindow.__init__(window)
        return window

    def test_live_process_children_start_collapsed_and_keep_manual_state(self) -> None:
        window = self._bare_window()
        window.live_process_container = QWidget(window)
        window.live_process_toggle = QPushButton(window)

        window._set_live_process_expanded(False)
        self.assertTrue(window.live_process_container.isHidden())
        self.assertFalse(window.live_process_expanded)
        self.assertIn("펼치기", window.live_process_toggle.accessibleName())

        window._toggle_live_process_container()
        self.assertFalse(window.live_process_container.isHidden())
        self.assertTrue(window.live_process_expanded)
        self.assertIn("접기", window.live_process_toggle.accessibleName())

        # 화면 이동 코드는 이 수동 상태를 변경하지 않는다.
        window._current_page = "dashboard"
        self.assertTrue(window.live_process_expanded)
        self.assertFalse(window.live_process_container.isHidden())
        window.deleteLater()

    def test_live_main_navigation_expands_children_and_other_pages_preserve_state(self) -> None:
        window = self._bare_window()
        window.live_process_container = QWidget(window)
        window.live_process_toggle = QPushButton(window)
        window.show_page = Mock()

        window._set_live_process_expanded(False)
        window._handle_sidebar_navigation("live_need")
        self.assertTrue(window.live_process_expanded)
        self.assertFalse(window.live_process_container.isHidden())
        window.show_page.assert_called_once_with("live_need")

        window.show_page.reset_mock()
        window._handle_sidebar_navigation("dashboard")
        self.assertTrue(window.live_process_expanded)
        self.assertFalse(window.live_process_container.isHidden())
        window.show_page.assert_called_once_with("dashboard")
        window.deleteLater()

    def test_risk_list_shows_badge_only_for_completed_work(self) -> None:
        window = self._bare_window()
        container = QWidget(window)
        window.risk_list_layout = QVBoxLayout(container)
        window.risk_count_summary = QLabel(window)
        window.risk_watcher = QLabel(window)
        window.risk_channel_checks = {}
        for channel in ("국내", "해외"):
            check = QCheckBox(window)
            check.setChecked(True)
            window.risk_channel_checks[channel] = check
        window.dashboard_data = {
            "risks": [
                {
                    "channel": "해외",
                    "work_completed": False,
                    "tone": "danger",
                    "order_no": "ACTIVE",
                    "initial": "A",
                    "due": "2026-09-03",
                    "due_label": "오늘",
                    "classification": "1-Day_Sph",
                    "risk_qty": 100,
                    "live_available": True,
                    "current_qty": 50,
                },
                {
                    "channel": "해외",
                    "work_completed": True,
                    "tone": "danger",
                    "order_no": "DONE",
                    "initial": "B",
                    "due": "2026-09-03",
                    "due_label": "오늘",
                    "classification": "1-Day_Sph",
                    "risk_qty": 100,
                    "live_available": True,
                    "current_qty": 0,
                },
            ]
        }

        window._refresh_risk_alerts()
        badges = container.findChildren(QLabel, "RiskCompletionBadge")
        self.assertEqual(2, len(badges))
        self.assertTrue(badges[0].isHidden())
        self.assertEqual("작업완료", badges[1].text())
        self.assertFalse(badges[1].isHidden())
        window.deleteLater()

    def test_live_refresh_reloads_live_inventory_and_lot_overview(self) -> None:
        window = self._bare_window()
        window.dashboard_service = Mock()
        window.dashboard_service.load.return_value = {}
        window._close_order_detail = Mock()
        window._refresh_risk_alerts = Mock()
        window._refresh_dashboard_channel_metrics = Mock()
        window._populate_process_matrix_table = Mock()
        window._selected_production_period = Mock(return_value={})
        window._refresh_settings_data_status = Mock()
        window._refresh_header_status = Mock()
        window.live_need_page = Mock()
        window.inventory_page = Mock()
        window.live_fixed_process_pages = {"live_hydration": Mock()}
        window.lot_work_order_page = Mock()
        window.lot_fixed_process_pages = {"lot_hydration": Mock()}

        window._reload_changed_data_views({"live"})

        window.live_need_page.reload_data.assert_called_once_with()
        window.inventory_page.reload_data.assert_called_once_with()
        window.lot_work_order_page.reload_data.assert_called_once_with()
        window.lot_fixed_process_pages["lot_hydration"].reload_data.assert_called_once_with()
        window.deleteLater()


if __name__ == "__main__":
    unittest.main()
