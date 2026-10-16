import copy
import gzip
import hashlib
import json
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime

from services.production_cycle import REQUIRED,validate_bundle,pin_catalog


class CycleTests(unittest.TestCase):
    def bundle(self):
        members={}
        for name in REQUIRED:
            endpoint='/api/inventory-ledger-product' if name.startswith('inventory_') else ('/api/production-performance' if 'production' in name else '/api/hydration-job-list')
            members[name]=dict(endpoint=endpoint,request_params={'wh_cd':name[-4:].upper()},
                started_at='2026-10-16T09:00:00+09:00',received_at='2026-10-16T09:00:20+09:00',
                payload=dict(rows=[],total_count=0,date_from='2026-10-16',date_to='2026-10-16'))
        return dict(format_version=1,cycle_id='cycle1',business_date='2026-10-16',
            received_at='2026-10-16T09:00:20+09:00',observation_span_seconds=20,
            aps={'aps_plan':{'snapshot_id':'plan'},'aps_backward_plan':{'snapshot_id':'backward'}},members=members)

    def validate(self,data):
        blob=gzip.compress(json.dumps(data).encode())
        return validate_bundle(blob,hashlib.sha256(blob).hexdigest(),'cycle1',datetime.fromisoformat('2026-10-16T09:02:00+09:00'))

    def test_empty_day_is_valid(self):
        self.assertEqual(self.validate(self.bundle())['cycle_id'],'cycle1')

    def test_missing_member_wrong_day_and_wide_span_rejected(self):
        for change in ('missing','date','span','aps'):
            with self.subTest(change=change):
                b=self.bundle()
                if change=='missing':b['members'].pop('inventory_q001')
                elif change=='date':b['business_date']='2026-10-15'
                elif change=='span':b['members']['inventory_q001']['received_at']='2026-10-16T09:04:00+09:00'
                else:b['aps']={}
                with self.assertRaises(ValueError):self.validate(b)

    def test_newer_partial_snapshots_cannot_leak_into_pinned_cohort(self):
        b=self.bundle()
        catalog=[dict(endpoint='/api/aps-plan',snapshot_id='plan'),dict(endpoint='/api/aps-backward-plan',snapshot_id='backward'),
            dict(endpoint='/api/production-performance',snapshot_id='history',date_from='2026-10-10',date_to='2026-10-16'),
            dict(endpoint='/api/inventory-ledger-product',snapshot_id='newer',date_from='2026-10-16',date_to='2026-10-16')]
        pinned,payloads=pin_catalog(catalog,b)
        self.assertNotIn('newer',[s['snapshot_id'] for s in pinned])
        self.assertEqual(next(s for s in pinned if s['snapshot_id']=='history')['date_to'],'2026-10-15')
        self.assertEqual(len(payloads),7)
        catalog[0]['snapshot_id']='changed-plan'
        with self.assertRaises(ValueError):pin_catalog(catalog,b)

    def test_unready_server_preserves_local_data_and_reports_waiting(self):
        from collectors import inventory_live_refresh as group
        from collectors import production_performance_collector as production
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); db=root/'untouched.sqlite';db.write_bytes(b'previous-good-data')
            with patch.dict('os.environ',{'DDOKDDAK_PROD3_TRANSPORT':'dmz'}), \
                 patch.object(group,'InventoryStatusService',return_value=SimpleNamespace(cache=root)), \
                 patch.object(group,'STATUS_PATH',root/'live.json'), \
                 patch.object(production,'STATUS_PATH',root/'production.json'), \
                 patch('services.collection_staging.recover_publication') as recover, \
                 patch('services.dmz_read_client.begin_production_cycle',side_effect=ValueError('preparing')):
                report=group.refresh()
            recover.assert_called_once_with(root/'pending_publication.json')
            self.assertEqual(report['status'],'waiting_source')
            self.assertEqual(report['production']['status'],'waiting_source')
            self.assertEqual(db.read_bytes(),b'previous-good-data')
            self.assertFalse((root/'snapshots').exists())


if __name__=='__main__':unittest.main()
