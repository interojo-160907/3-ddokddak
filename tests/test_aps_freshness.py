import json
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import patch, MagicMock

from collectors import process_status_collector as collector
from services import dmz_read_client as dmz
from services.collection_health import collection_ready, freshness_note
from datetime import datetime


class ApsFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.status = folder / 'status.json'
        self.db = folder / 'aps.sqlite'
        self.db.write_bytes(b'previous database')
        self.old = dict(status='success', run_key='old', source_identity='source-old',
                        refreshed_at='2026-10-15T08:00:00+09:00', stored_rows=10)
        self.status.write_text(json.dumps(self.old), encoding='utf-8')
        for name, value in [('STATUS_PATH', self.status), ('DB_PATH', self.db)]:
            self.stack.enter_context(patch.object(collector, name, value))
        self.stack.enter_context(patch('services.collection_lock.collection_lock', return_value=nullcontext()))
        self.stack.enter_context(patch('services.aps_collection_policy.ApsPolicy'))
        self.stack.enter_context(patch.object(dmz, 'route_to_dmz', return_value=True))
        self.stack.enter_context(patch.object(dmz, 'reset_catalog'))
        self.stack.enter_context(patch.object(collector, '_request', return_value={}))
        self.state = self.stack.enter_context(patch.object(collector, 'outbound_state'))
        self.state.return_value = dict(run_key='new', source_identity='source-new')
        self.build = self.stack.enter_context(patch.object(collector, '_refresh_snapshot'))

    def test_partial_server_generation_preserves_data_and_reports_wait(self):
        with patch.object(dmz, 'aps_pending_sources', return_value=['plan']):
            result = collector.refresh()
        self.assertEqual(result['status'], 'waiting_source')
        self.assertEqual(result['pending_run_key'], 'new')
        self.assertEqual(result['refreshed_at'], self.old['refreshed_at'])
        self.assertEqual(self.db.read_bytes(), b'previous database')
        self.build.assert_not_called()
        self.assertTrue(result['checked_at'])

    def test_unchanged_recovery_updates_check_without_downloading(self):
        self.status.write_text(json.dumps(dict(self.old, status='check_failed')), encoding='utf-8')
        self.state.return_value = self.old
        with patch.object(collector, '_sync_wip_metadata', return_value=False):
            result = collector.refresh()
        self.assertEqual(result['status'], 'success')
        self.assertFalse(result['changed'])
        self.assertEqual(result['refreshed_at'], self.old['refreshed_at'])
        self.assertTrue(result['checked_at'])
        self.build.assert_not_called()

    def test_probe_error_is_visible_and_retains_database(self):
        self.state.side_effect = RuntimeError('unavailable')
        with self.assertRaises(RuntimeError):
            collector.refresh()
        result = json.loads(self.status.read_text(encoding='utf-8'))
        self.assertEqual(result['status'], 'check_failed')
        self.assertEqual(result['refreshed_at'], self.old['refreshed_at'])
        self.assertEqual(self.db.read_bytes(), b'previous database')
        self.assertIn('확인 실패', freshness_note('aps', result, datetime.now()))

    def test_ready_server_generation_downloads_once(self):
        self.build.return_value = dict(status='success', run_key='new')
        with patch.object(dmz, 'aps_pending_sources', return_value=[]):
            self.assertTrue(collector.refresh()['changed'])
        self.build.assert_called_once()

    def test_wait_and_check_error_are_not_all_green(self):
        statuses = {key + '_status': {'status':'success'} for key in ('bom','aps','production','live')}
        for state in ('waiting_source', 'check_failed'):
            statuses['aps_status']['status'] = state
            self.assertFalse(collection_ready(statuses, {}, {}))


class PublishedApsMetadataTests(unittest.TestCase):
    def test_requires_both_current_timestamps_counts_and_complete_payload(self):
        sources = [dict(endpoint='/api/aps-backward-plan', job_id='b', run_id='1', row_count=10),
                   dict(endpoint='/api/aps-plan', job_id='p', run_id='2', row_count=20)]
        state = dict(backward_count=10, backward_at='new', plan_count=20, plan_at='new')
        for plan_meta, expected in [({'source_refreshed_at':'old'}, ['plan']),
                                    ({'source_refreshed_at':'new', 'truncated':True}, ['plan']),
                                    ({'source_refreshed_at':'new'}, [])]:
            connection = MagicMock()
            connection.execute.return_value.fetchone.side_effect = [
                {'metadata':{'source_refreshed_at':'new'}}, {'metadata':plan_meta}]
            with patch.object(dmz, 'catalog', return_value=sources), \
                 patch.object(dmz, 'connection', return_value=nullcontext(connection)):
                self.assertEqual(dmz.aps_pending_sources(state), expected)


if __name__ == '__main__':
    unittest.main()
