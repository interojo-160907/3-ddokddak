from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from collectors.production_performance_collector import should_run_daily_full
from config import collection_directories
from ui.main_window import MainWindow, _collector_executable


class CollectionDirectoryTests(unittest.TestCase):
    def test_update_creates_every_collection_subdirectory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "existing-user-data"
            paths = collection_directories(root)
            for path in paths:
                path.mkdir(parents=True, exist_ok=True)

            expected = {
                root / source / child
                for source, children in {
                    "bom": ("snapshot", "backup", "raw_api"),
                    "process-status": ("snapshot", "backup", "raw_api"),
                    "production-performance": ("snapshot", "backup", "raw_api"),
                    "live-production-need": ("snapshot", "backup"),
                }.items()
                for child in children
            }
            self.assertTrue(all(path.is_dir() for path in expected))
            self.assertTrue((root / "settings").is_dir())


class ProductionCatchupTests(unittest.TestCase):
    def test_long_shutdown_forces_full_history_before_7am(self) -> None:
        now = datetime(2026, 9, 3, 6, 30)
        status = {"date_to": "2026-08-20", "daily_full_date": "2026-08-20"}
        self.assertTrue(
            should_run_daily_full(now, status, database_exists=True)
        )

    def test_short_shutdown_uses_recent_seven_day_increment_before_7am(self) -> None:
        now = datetime(2026, 9, 3, 6, 30)
        status = {"date_to": "2026-09-01", "daily_full_date": "2026-09-01"}
        self.assertFalse(
            should_run_daily_full(now, status, database_exists=True)
        )


class SchedulerCatchupTests(unittest.TestCase):
    @staticmethod
    def _window() -> SimpleNamespace:
        window = SimpleNamespace()
        window.collection_schedule = {
            "bom_minutes": 60,
            "aps_minutes": 1,
            "production_minutes": 60,
            "live_minutes": 60,
        }
        window._collection_last_attempt = {}
        window._start_data_collection = Mock()
        window._start_aps_monitor_check = Mock()
        window._run_scheduled_collections = Mock()
        return window

    def test_startup_after_days_runs_ordered_full_refresh(self) -> None:
        window = self._window()
        stale = datetime.now() - timedelta(days=3)
        status = {
            "status": "success",
            "daily_full_date": date.today().isoformat(),
        }
        window._status_refreshed_at = Mock(return_value=stale)
        window._read_refresh_status = Mock(return_value=status)
        MainWindow._run_scheduled_collections(window)

        window._start_data_collection.assert_called_once_with("all", scheduled=True)
        window._start_aps_monitor_check.assert_not_called()

    def test_initial_timer_checks_missed_work_without_waiting_for_first_tick(self) -> None:
        window = self._window()
        window.aps_monitor_timer = Mock()
        with patch("ui.main_window.QTimer.singleShot") as single_shot:
            MainWindow._apply_collection_timers(window, run_initial=True)

        window.aps_monitor_timer.stop.assert_called_once_with()
        single_shot.assert_called_once_with(
            2_000, window._run_scheduled_collections
        )

    def test_windows_source_collectors_prefer_windowless_python(self) -> None:
        expected = Path(__import__("sys").executable).with_name("pythonw.exe")
        if expected.is_file():
            self.assertEqual(Path(_collector_executable()), expected)


if __name__ == "__main__":
    unittest.main()
