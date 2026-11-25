import json
import tempfile
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from services import delivery_lifecycle as life
from services.collection_lock import collection_lock
from collectors import delivery_refresh as receiver
from tests.test_delivery_stage_cleanup import SafetyTests


class LifecycleSafetyTests(SafetyTests):
    def test_partial_deletion_keeps_owner_for_next_cleanup(self):
        folder=self.stage('access-denied')
        payload=folder/'locked-payload';payload.write_bytes(b'KEEP UNTIL UNLOCKED')
        original=Path.unlink
        def unlink(path,*args,**kwargs):
            if path.resolve()==payload.resolve():raise PermissionError('locked')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',unlink):
            with self.assertRaises(PermissionError):life.prune_stages(self.owner,base=self.base)
            self.assertTrue((folder/'delivery-owner.json').exists())
            with self.assertRaises(PermissionError):life.prune_stages(self.owner,base=self.base)
        self.assertEqual(len(life.prune_stages(self.owner,base=self.base)),1)
        self.assertFalse(folder.exists())

    def test_failed_stages_do_not_require_a_later_success(self):
        folder=self.stage('failed',age=1)
        self.status.update(status='waiting_source',applied_at='2026-01-01T00:00:00+00:00')
        self.save_status()
        with collection_lock(self.owner):
            self.assertEqual(len(life.prune_stages(self.owner,base=self.base)),1)
        self.assertFalse(folder.exists())
        self.assertEqual((self.owner/'bom/product_reference.sqlite').read_bytes(),b'OPERATING DATABASE')

    def test_recovery_journal_blocks_cleanup(self):
        folder=self.stage('recovery')
        (self.settings/'delivery_publication.json').write_text('[]')
        with self.assertRaisesRegex(ValueError,'복구'):
            life.prune_stages(self.owner,base=self.base)
        self.assertTrue(folder.exists())

    def test_live_child_lock_blocks_cleanup(self):
        folder=self.stage('running')
        with collection_lock(folder):
            with self.assertRaises(Exception):life.prune_stages(self.owner,base=self.base)
        self.assertTrue(folder.exists())

    def test_foreign_and_unknown_are_preserved(self):
        foreign=self.stage('foreign',owner=self.root/'other')
        unknown=self.stage('unknown',marker=False)
        self.assertEqual(life.prune_stages(self.owner,base=self.base),[])
        self.assertTrue(foreign.exists());self.assertTrue(unknown.exists())

    def test_diagnostics_are_bounded(self):
        for i in range(12):
            self.stage(str(i));life.prune_stages(self.owner,base=self.base)
        self.assertEqual(len(list((self.settings/'delivery-diagnostics').iterdir())),5)


class RetryTests(unittest.TestCase):
    def test_copy_error_timeout_and_success_always_remove_scratch(self):
        for outcome in ('copy-error','timeout','success'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                base=Path(directory)/'temp';base.mkdir()
                root=Path(directory)/'root';root.mkdir()
                sentinel=root/'user.txt';sentinel.write_text('KEEP')
                now=datetime.now(timezone.utc)
                def calculate(command,env,**kwargs):
                    if outcome=='timeout':raise subprocess.TimeoutExpired(command,1200)
                    stage=Path(env['DDOKDDAK_PROD3_STAGE_ROOT'])
                    settings=stage/'backward-v1/settings';settings.mkdir(parents=True,exist_ok=True)
                    (settings/'full_refresh_status.json').write_text(json.dumps({'results':{'live':{'status':'success'}}}))
                    return SimpleNamespace(returncode=0,stdout=b'ok',stderr=b'')
                with (patch.object(receiver,'resolve_data_root',return_value=root),
                      patch.object(receiver.tempfile,'gettempdir',return_value=str(base)),
                      patch.object(receiver,'copy_inputs',side_effect=OSError('copy failure') if outcome=='copy-error' else None),
                      patch.object(receiver,'check_working_space'),
                      patch('services.delivery_groups.head_status',return_value={'groups':[]}),
                      patch('services.delivery_groups.read_pair',return_value=({'version':'aps','aps_key':'run'},dict(version='data',started_at=now,received_at=now))),
                      patch('services.lidding_data.download'),patch('services.lidding_data.cache_ready',return_value=True),
                      patch.object(receiver,'publish_group'),
                      patch.object(receiver.subprocess,'run',side_effect=calculate),patch('builtins.print')):
                    self.assertEqual(receiver.main.__wrapped__(),0 if outcome=='success' else 2)
                self.assertEqual(list(base.glob('prod3-delivery-*')),[])
                self.assertEqual(sentinel.read_text(),'KEEP')
                report=json.loads((root/'backward-v1/settings/delivery_status.json').read_text('utf-8'))
                self.assertNotIn('cleanup_error',report)
                self.assertEqual(report['stage_path'],'')

    def test_persistent_backoff_and_cap(self):
        report={};now=datetime.now(timezone.utc)
        life.record_failure(report,'v',now)
        self.assertTrue(life.retry_reason(report,'v',now+timedelta(minutes=4)))
        self.assertFalse(life.retry_reason(report,'v',now+timedelta(minutes=5)))
        life.record_failure(report,'v',now);life.record_failure(report,'v',now)
        self.assertIn('3회',life.retry_reason(report,'v',now+timedelta(days=1)))
        self.assertFalse(life.retry_reason(report,'new',now))
        life.clear_retry(report);self.assertEqual(report,{})

    def test_one_hundred_failures_leave_no_payload_and_only_five_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)/'temp';base.mkdir()
            root=Path(directory)/'root';root.mkdir()
            keep=root/'user.txt';keep.write_text('KEEP')
            now=datetime.now(timezone.utc)
            def calculate(command,env,**kwargs):
                stage=Path(env['DDOKDDAK_PROD3_STAGE_ROOT'])
                settings=stage/'backward-v1/settings';settings.mkdir(parents=True,exist_ok=True)
                (stage/'large-payload').write_bytes(b'x'*4096)
                (settings/'full_refresh_status.json').write_text(json.dumps({'results':{'live':{'result':{'result':{'message':'test failure'}}}}}))
                return SimpleNamespace(returncode=2,stdout=b'failed',stderr=b'')
            with (patch.object(receiver,'resolve_data_root',return_value=root),
                  patch.object(receiver.tempfile,'gettempdir',return_value=str(base)),
                  patch.object(receiver,'copy_inputs'),patch.object(receiver,'check_working_space'),
                  patch('services.delivery_groups.head_status',return_value={'groups':[]}),
                  patch('services.delivery_groups.read_pair') as pair,
                  patch.object(receiver.subprocess,'run',side_effect=calculate) as child,
                  patch('builtins.print')):
                for i in range(100):
                    pair.return_value=({'version':'aps','aps_key':'run'},dict(version=str(i),started_at=now,received_at=now))
                    self.assertEqual(receiver.main.__wrapped__(),2)
                    self.assertEqual(list(base.glob('prod3-delivery-*')),[])
                # Same failed package in the backoff window must not calculate.
                count=child.call_count
                self.assertEqual(receiver.main.__wrapped__(),2)
                self.assertEqual(child.call_count,count)
            self.assertEqual(keep.read_text(),'KEEP')
            self.assertEqual(len(list((root/'backward-v1/settings/delivery-diagnostics').iterdir())),5)

    def test_compact_item_copy_preserves_rows_without_duplicate_orders(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'root';stage=Path(d)/'stage'
            p=root/'backward-v1/inventory-status/item_P0001.json';p.parent.mkdir(parents=True)
            p.write_text(json.dumps({'rows':[{'gd_cd':'P0001A'}],'orders':[{'large':'duplicate'}]*100}))
            receiver.copy_inputs(root,stage)
            result=json.loads((stage/p.relative_to(root)).read_text('utf-8'))
            self.assertEqual(result,{'rows':[{'gd_cd':'P0001A'}]})
            self.assertIn('orders',json.loads(p.read_text()))
