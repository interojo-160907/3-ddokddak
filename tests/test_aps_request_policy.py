import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from services.aps_collection_policy import ApsPolicy
from collectors import process_status_collector as collector
from services.erp_coordination import request_turn


class ApsRequestPolicyTests(unittest.TestCase):
    def test_failures_survive_restart_and_double_wait(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'policy.json'
            for count, delay in enumerate((600,1200,2400,3600), 1):
                p = ApsPolicy(path, clock=lambda:100)
                p.failed()
                self.assertEqual(p.state['failures'],count)
                self.assertEqual(p.state['next_attempt_at'],100+delay)
                self.assertTrue(ApsPolicy(path, clock=lambda:101).blocked())
            p.succeeded('R2')
            self.assertFalse(ApsPolicy(path, clock=lambda:101).blocked())

    def test_same_successful_run_never_downloads_full_even_if_wip_changes(self):
        with tempfile.TemporaryDirectory() as d:
            p = ApsPolicy(Path(d)/'policy.json')
            db = Path(d)/'aps.sqlite';db.touch()
            with patch('services.aps_collection_policy.ApsPolicy',return_value=p), \
                 patch.object(collector,'DB_PATH',db), \
                 patch.object(collector,'_read_status',return_value={'status':'success','run_key':'R1','wip_at':'old'}), \
                 patch.object(collector,'outbound_state',return_value={'run_key':'R1','wip_at':'new'}), \
                 patch.object(collector,'_sync_wip_metadata',return_value=True), \
                 patch.object(collector,'_request',return_value={}) as request, \
                 patch.object(collector,'_refresh_snapshot') as download:
                self.assertFalse(collector.refresh()['changed'])
            download.assert_not_called()
            request.assert_called_once()

    def test_failed_download_not_repeated_by_manual_or_bootstrap(self):
        with tempfile.TemporaryDirectory() as d:
            p = ApsPolicy(Path(d)/'policy.json')
            with patch('services.aps_collection_policy.ApsPolicy',return_value=p), \
                 patch.object(collector,'_request',side_effect=RuntimeError('slow server')) as request:
                for _ in range(2):
                    with self.assertRaises(RuntimeError):collector.refresh()
            self.assertEqual(request.call_count,1)

    def test_old_management_deployment_fails_closed(self):
        response=Mock();response.json.return_value={'ok':True,'result':{'allowed':True}}
        with patch.dict('os.environ',{'DDOKDDAK_PROD3_COORDINATION':'1'}), \
             patch('services.erp_coordination.requests.post',return_value=response) as post:
            with self.assertRaisesRegex(RuntimeError,'準備|준비'):
                with request_turn():self.fail('ERP must not start')
        self.assertEqual(post.call_count,1)

    def test_wip_completion_updates_metadata_without_replacing_aps_rows(self):
        import sqlite3, json
        from contextlib import closing
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'aps.sqlite'
            with closing(sqlite3.connect(db)) as c:
                c.execute('CREATE TABLE source_provenance(id INTEGER PRIMARY KEY,run_key TEXT,wip_at TEXT,payload_json TEXT)')
                c.execute('INSERT INTO source_provenance VALUES(1,?,?,?)',('R1','old',json.dumps({'run_key':'R1','wip_at':'old'})))
                c.execute('CREATE TABLE aps_plan(qty INTEGER)');c.execute('INSERT INTO aps_plan VALUES(123)')
                c.commit()
            local={'run_key':'R1','wip_at':'old','wip_count':1}
            with patch.object(collector,'DB_PATH',db),patch.object(collector,'STATUS_PATH',Path(d)/'status.json'):
                self.assertTrue(collector._sync_wip_metadata(local,{'run_key':'R1','wip_at':'new','wip_count':2}))
            with closing(sqlite3.connect(db)) as c:
                self.assertEqual(c.execute('SELECT qty FROM aps_plan').fetchone()[0],123)
                self.assertEqual(c.execute('SELECT wip_at FROM source_provenance').fetchone()[0],'new')
