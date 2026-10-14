import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from datetime import datetime, timedelta
from services.collection_distribution import stable_offset, periodic_delay, retry_deadline, retry_blocked


class DistributionTests(unittest.TestCase):
    def test_same_pc_keeps_slot_and_jobs_are_independent(self):
        self.assertEqual(stable_offset('A', 'aps', 60), stable_offset('A', 'aps', 60))
        values = {stable_offset('A', str(i), 60) for i in range(20)}
        self.assertGreater(len(values), 10)

    def test_clients_spread_across_window_without_fixed_user_order(self):
        values = [stable_offset(str(i), 'initial', 180) for i in range(1000)]
        self.assertTrue(all(0 <= value < 180 for value in values))
        self.assertGreater(len(set(values)), 170)

    def test_periodic_slot_does_not_drift_with_polling(self):
        now = datetime(2026, 10, 14, 14)
        target = now + timedelta(seconds=periodic_delay('A', 'aps', now))
        halfway = now + (target - now) / 2
        self.assertAlmostEqual((target - halfway).total_seconds(), periodic_delay('A', 'aps', halfway), places=5)
        self.assertAlmostEqual(periodic_delay('A', 'aps', target), 0, places=5)

    def test_persisted_failure_blocks_restart_and_resets_on_success(self):
        now = datetime(2026, 10, 14, 14)
        deadline = retry_deadline(now, 2, uniform=lambda a, b: b)
        errors = {'live': {'retry_after': deadline.isoformat()}}
        self.assertEqual(deadline, now + timedelta(seconds=180))
        self.assertTrue(retry_blocked(errors, 'live', now))
        self.assertTrue(retry_blocked(errors, 'all', now))
        self.assertFalse(retry_blocked(errors, 'aps', now))
        self.assertFalse(retry_blocked(errors, 'live', deadline))
        self.assertFalse(retry_blocked({}, 'live', now))

    def test_retry_has_bounded_delay_and_no_immediate_retry(self):
        now = datetime.now()
        for failures in (1, 2, 10, 999):
            delay = (retry_deadline(now, failures) - now).total_seconds()
            self.assertGreaterEqual(delay, 60)
            self.assertLessEqual(delay, 3900)

    def test_gui_coalesces_pending_auto_requests_and_honors_cancel(self):
        from ui.main_window import MainWindow
        from config import APP_VERSION
        window = SimpleNamespace(_distributed_pending=set(), _distribution_pc_id='A',
            _read_collection_errors=Mock(return_value={}),
            _read_refresh_status=Mock(return_value={'completed_version': APP_VERSION}),
            _start_data_collection=Mock())
        callbacks = []
        with patch('ui.main_window.QTimer.singleShot', side_effect=lambda ms, cb: callbacks.append((ms, cb))):
            MainWindow._start_data_collection(window, 'live', scheduled=True)
            MainWindow._start_data_collection(window, 'live', scheduled=True)
        self.assertEqual(len(callbacks), 1)
        self.assertLessEqual(callbacks[0][0], 60000)
        window._collection_resume_at = datetime.now() + timedelta(minutes=5)
        callbacks[0][1]()
        self.assertFalse(window._distributed_pending)
        window._start_data_collection.assert_not_called()

    def test_first_install_is_delayed_and_remains_server_only(self):
        from ui.main_window import MainWindow
        window = SimpleNamespace(_distributed_pending=set(), _distribution_pc_id='A',
            _read_collection_errors=Mock(return_value={}),
            _read_refresh_status=Mock(return_value={}), _start_data_collection=Mock())
        callbacks = []
        with patch('ui.main_window.QTimer.singleShot', side_effect=lambda ms, cb: callbacks.append((ms, cb))):
            MainWindow._start_data_collection(window, 'all', scheduled=True)
        self.assertEqual(callbacks[0][0], max(1, stable_offset('A', 'initial', 180) * 1000))
        callbacks[0][1]()
        window._start_data_collection.assert_called_once_with('all', scheduled=True, _spread_applied=True)
