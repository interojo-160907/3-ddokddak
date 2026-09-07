import unittest
from threading import Event
from unittest.mock import patch

from services.program_gate import ProgramGate
from services.program_presence import PRESENCE_INTERVAL_MS, ProgramPresence


class PresenceTest(unittest.TestCase):
    def test_payloads(self):
        with patch('services.program_gate.resolve_management_api_url', return_value='https://example.invalid'), patch('services.program_gate.requests.post') as post:
            gate = ProgramGate('2.4')
            for connected, label in [(True, '🟢 사용중'), (False, '🔴 미사용')]:
                gate.set_connection(connected)
                payload = post.call_args.kwargs['json']
                self.assertEqual(payload['action'], 'presence')
                self.assertEqual(payload['connection_status'], label)
                self.assertEqual(payload['connected'], connected)

    def test_shutdown_follows_inflight_heartbeat_and_never_queues_duplicates(self):
        started, release, finished = Event(), Event(), Event()
        calls = []
        def send(connected):
            calls.append(connected)
            if connected:
                started.set()
                release.wait(5)
            else:
                finished.set()
            return True
        with patch('services.program_presence.ProgramGate') as gate:
            gate.return_value.set_connection.side_effect = send
            monitor = ProgramPresence('2.4')
            try:
                monitor.heartbeat()
                self.assertTrue(started.wait(2))
                monitor.heartbeat()
                monitor.close()
                monitor.close()
                monitor.heartbeat()
            finally:
                release.set()
                monitor.close()
            self.assertTrue(finished.wait(2))
            self.assertEqual(calls, [True, False])

    def test_failed_report_does_not_stop_future_reports(self):
        self.assertEqual(PRESENCE_INTERVAL_MS, 30 * 60 * 1000)
        with patch('services.program_presence.ProgramGate') as gate:
            gate.return_value.set_connection.return_value = False
            monitor = ProgramPresence('2.4')
            try:
                for _ in range(2):
                    monitor.heartbeat()
                    monitor._future.result(timeout=2)
            finally:
                monitor.close()
                monitor._future.result(timeout=2)
            self.assertEqual(gate.return_value.set_connection.call_count, 3)


if __name__ == '__main__':
    unittest.main()
