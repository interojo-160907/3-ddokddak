import unittest
from datetime import date
from unittest.mock import patch

from services.collection_health import recovered, collection_ready
from collectors.production_performance_collector import _fetch


class CollectionHealthTests(unittest.TestCase):
    def test_only_a_later_success_resolves_error(self):
        error = {"occurred_at": "2026-09-11T15:46:22+09:00"}
        status = {"status": "success", "refreshed_at": "2026-09-11T15:56:36+09:00"}
        self.assertTrue(recovered(error, status))
        self.assertFalse(recovered(error, {**status, "status": "retained"}))
        self.assertFalse(recovered(error, {**status, "refreshed_at": "2026-09-11T15:40:00+09:00"}))
        self.assertFalse(recovered(error, {**status, "refreshed_at": "invalid"}))

    def test_header_includes_live_errors_and_connection_failures(self):
        statuses = {k: {"status": "success"} for k in
                    ("bom_status", "aps_status", "production_status", "live_status")}
        self.assertTrue(collection_ready(statuses, {}, {"bom": True}))
        self.assertFalse(collection_ready(statuses, {"aps": {"message": "error"}}, {}))
        self.assertFalse(collection_ready(statuses, {}, {"bom": False}))
        self.assertFalse(collection_ready({**statuses, "live_status": {"status": "waiting_wip"}}, {}, {}))

    @patch("collectors.production_performance_collector.time.sleep")
    @patch("collectors.production_performance_collector._fetch_once")
    def test_inflight_partial_day_recovers_without_accepting_partial_rows(self, fetch, sleep):
        fetch.side_effect = [RuntimeError("응답이 일부만 반환"), {"rows": [{"pr_no": "1"}]}]
        self.assertEqual(_fetch(date(2026, 9, 11), date(2026, 9, 11), "", 30)["rows"], [{"pr_no": "1"}])
        self.assertEqual(fetch.call_count, 2)

    @patch("collectors.production_performance_collector.time.sleep")
    @patch("collectors.production_performance_collector._fetch_once")
    def test_persistent_partial_day_fails_after_three_attempts(self, fetch, sleep):
        fetch.side_effect = RuntimeError("응답이 일부만 반환")
        with self.assertRaises(RuntimeError):
            _fetch(date(2026, 9, 11), date(2026, 9, 11), "", 30)
        self.assertEqual(fetch.call_count, 3)
