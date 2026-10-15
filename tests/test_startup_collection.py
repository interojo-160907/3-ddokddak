from datetime import datetime, timedelta
from unittest import TestCase
from unittest.mock import Mock, patch

import test_collection_catchup as catchup
from services.collection_health import freshness_note
from ui.main_window import MainWindow


class StartupCollectionTests(TestCase):
    def window(self, *, startup=True):
        window = catchup.SchedulerCatchupTests._window()
        window._startup_collection_pending = startup
        window._read_refresh_status = Mock(side_effect=catchup.SchedulerCatchupTests._completed_bootstrap)
        window._status_refreshed_at = Mock(return_value=datetime.now())
        window._read_collection_errors = Mock(return_value={})
        return window

    def test_every_launch_syncs_even_with_recent_success_or_manual_schedule(self):
        for minutes in (0, 60, 360):
            for age in (timedelta(minutes=1), timedelta(hours=16), timedelta(days=5)):
                with self.subTest(minutes=minutes, age=age):
                    window = self.window()
                    window.collection_schedule = {f'{key}_minutes': minutes for key in ('aps', 'bom', 'production', 'live')}
                    window._status_refreshed_at.return_value = datetime.now() - age
                    MainWindow._run_scheduled_collections(window)
                    window._start_data_collection.assert_called_once_with('all', scheduled=True)

    def test_started_batch_consumes_startup_once_then_normal_schedule(self):
        window = self.window()
        window._set_collection_busy = Mock()
        window.settings_data_status = Mock()
        window.collection_elapsed_timer = Mock()
        window.collection_watchdog_timer = Mock()
        MainWindow._data_collection_started(window, 'all')
        self.assertFalse(window._startup_collection_pending)
        MainWindow._run_scheduled_collections(window)
        window._start_data_collection.assert_not_called()
        window._start_aps_monitor_check.assert_not_called()

    def test_startup_honors_persisted_backoff_and_pending_slot(self):
        window = self.window()
        window._read_collection_errors.return_value = {
            'production': {'retry_after': (datetime.now() + timedelta(minutes=2)).isoformat()}}
        MainWindow._run_scheduled_collections(window)
        window._start_data_collection.assert_not_called()
        self.assertTrue(window._startup_collection_pending)
        window._read_collection_errors.return_value = {}
        window._distributed_pending = {'all'}
        MainWindow._run_scheduled_collections(window)
        window._start_data_collection.assert_not_called()
        window._distributed_pending.clear()
        MainWindow._run_scheduled_collections(window)
        window._start_data_collection.assert_called_once_with('all', scheduled=True)

    def test_failed_startup_source_retries_before_normal_six_hour_interval(self):
        for minutes in (0, 360):
            with self.subTest(minutes=minutes):
                window = self.window(startup=False)
                window.collection_schedule = {f'{key}_minutes': minutes for key in ('aps', 'bom', 'production', 'live')}
                window._read_collection_errors.return_value = {'bom': {'retry_after': (datetime.now() - timedelta(seconds=1)).isoformat()}}
                window._collection_last_attempt['bom'] = datetime.now() - timedelta(minutes=6)
                MainWindow._run_scheduled_collections(window)
                window._start_data_collection.assert_called_once_with('bom', scheduled=True)

    def test_freshness_does_not_call_yesterdays_snapshot_current(self):
        now = datetime(2026, 10, 15, 9, 13)
        prior = {'status': 'success', 'daily_full_date': '2026-10-14'}
        self.assertTrue(freshness_note('production', prior, now))
        self.assertTrue(freshness_note('bom', prior, now, startup_pending=True))
        self.assertTrue(freshness_note('live', {'status': 'retained'}, now))
        self.assertTrue(freshness_note('hydration', prior, now, inventory_cycle={'hydration': {'status': 'error'}}))
        current = dict(prior, daily_full_date='2026-10-15')
        self.assertEqual(freshness_note('production', current, now), '')
        self.assertEqual(freshness_note('aps', prior, now), '')
