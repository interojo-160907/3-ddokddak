from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from collectors.production_performance_collector import should_run_daily_full
from config import collection_directories
from ui.main_window import MainWindow, _collector_executable, _version_bootstrap_ready


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


class VersionBootstrapTests(unittest.TestCase):
    def test_partial_inventory_does_not_repeat_every_base_collector(self) -> None:
        report = {
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "results": {
                key: {"status": "success"}
                for key in ("bom", "aps", "production", "live")
            },
        }
        self.assertTrue(_version_bootstrap_ready(report))

    def test_failed_base_collector_keeps_bootstrap_incomplete(self) -> None:
        report = {
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "results": {
                "bom": {"status": "error"},
                "aps": {"status": "success"},
                "production": {"status": "success"},
                "live": {"status": "success"},
            },
        }
        self.assertFalse(_version_bootstrap_ready(report))


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

    @staticmethod
    def _completed_bootstrap(path: Path) -> dict:
        if path.name == "version_collection_bootstrap.json":
            from config import APP_VERSION

            return {"completed_version": APP_VERSION}
        return {
            "status": "success",
            "daily_full_date": date.today().isoformat(),
        }

    def test_startup_after_days_runs_ordered_full_refresh(self) -> None:
        window = self._window()
        stale = datetime.now() - timedelta(days=3)
        status = {
            "status": "success",
            "daily_full_date": date.today().isoformat(),
        }
        window._status_refreshed_at = Mock(return_value=stale)
        window._read_refresh_status = Mock(
            side_effect=lambda path: (
                self._completed_bootstrap(path)
                if path.name == "version_collection_bootstrap.json"
                else status
            )
        )
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

    def test_waiting_wip_is_checked_each_minute_even_when_live_schedule_is_off(self) -> None:
        window = self._window()
        window.collection_schedule = {
            "bom_minutes": 0,
            "aps_minutes": 0,
            "production_minutes": 0,
            "live_minutes": 0,
        }
        window._read_refresh_status = Mock(
            side_effect=lambda path: self._completed_bootstrap(path)
            if path.name == "version_collection_bootstrap.json"
            else {
                "status": "waiting_wip",
                "refreshed_at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        window._status_refreshed_at = Mock(return_value=datetime.now())

        MainWindow._run_scheduled_collections(window)

        window._start_data_collection.assert_called_once_with("live", scheduled=True)

    def test_wip_monitor_has_priority_over_simultaneous_aps_poll(self) -> None:
        window = self._window()
        window.collection_schedule = {
            "bom_minutes": 0,
            "aps_minutes": 1,
            "production_minutes": 0,
            "live_minutes": 60,
        }
        window._read_refresh_status = Mock(
            side_effect=lambda path: self._completed_bootstrap(path)
            if path.name == "version_collection_bootstrap.json"
            else {
                "status": "waiting_wip",
                "refreshed_at": (
                    datetime.now() - timedelta(minutes=10)
                ).isoformat(timespec="seconds"),
            }
        )
        window._status_refreshed_at = Mock(
            return_value=datetime.now() - timedelta(minutes=10)
        )

        MainWindow._run_scheduled_collections(window)

        window._start_data_collection.assert_called_once_with("live", scheduled=True)
        window._start_aps_monitor_check.assert_not_called()

    def test_update_first_run_collects_live_when_inventory_or_hydration_is_missing(self) -> None:
        window = self._window()
        now = datetime.now()

        def read_status(path: Path) -> dict:
            if path.name == "version_collection_bootstrap.json":
                return self._completed_bootstrap(path)
            if path.name in {"hydration_instructions.json", "refresh_status.json"} and "inventory-status" in str(path):
                return {}
            return {
                "status": "success",
                "refreshed_at": now.isoformat(timespec="seconds"),
                "daily_full_date": date.today().isoformat(),
            }

        window._read_refresh_status = Mock(side_effect=read_status)
        window._status_refreshed_at = Mock(return_value=now)

        MainWindow._run_scheduled_collections(window)

        window._start_data_collection.assert_called_once_with("live", scheduled=True)
        window._start_aps_monitor_check.assert_not_called()

    def test_failed_first_inventory_cycle_retries_even_when_regular_schedule_is_off(self) -> None:
        window = self._window()
        window.collection_schedule = {
            "bom_minutes": 0,
            "aps_minutes": 0,
            "production_minutes": 0,
            "live_minutes": 0,
        }
        now = datetime.now()

        def read_status(path: Path) -> dict:
            if path.name == "version_collection_bootstrap.json":
                return self._completed_bootstrap(path)
            if path.name == "hydration_instructions.json":
                return {"status": "success"}
            if path.name == "refresh_status.json" and "inventory-status" in str(path):
                return {"status": "partial"}
            return {"status": "success", "refreshed_at": now.isoformat(timespec="seconds")}

        window._read_refresh_status = Mock(side_effect=read_status)
        window._status_refreshed_at = Mock(return_value=now)

        MainWindow._run_scheduled_collections(window)

        window._start_data_collection.assert_called_once_with("live", scheduled=True)

    def test_new_version_forces_one_full_collection_before_regular_schedule(self) -> None:
        window = self._window()
        now = datetime.now()
        window._read_refresh_status = Mock(return_value={})
        window._status_refreshed_at = Mock(return_value=now)

        MainWindow._run_scheduled_collections(window)

        window._start_data_collection.assert_called_once_with("all", scheduled=True)
        self.assertIn("version_bootstrap", window._collection_last_attempt)

    def test_live_collection_busy_state_reaches_main_and_internal_tabs(self) -> None:
        live_main = Mock()
        lot_main = Mock()
        live_internal = [Mock(), Mock()]
        lot_internal = [Mock(), Mock()]
        window = SimpleNamespace(
            live_need_page=live_main,
            lot_work_order_page=lot_main,
            live_fixed_process_pages={str(i): page for i, page in enumerate(live_internal)},
            lot_fixed_process_pages={str(i): page for i, page in enumerate(lot_internal)},
            collection_controls={},
        )

        MainWindow._set_collection_busy(window, True, "live")

        for page in [live_main, lot_main, *live_internal, *lot_internal]:
            page.set_refreshing.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
