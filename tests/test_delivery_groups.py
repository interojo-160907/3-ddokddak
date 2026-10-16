import json
import os
import sys
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import MagicMock,patch
from services.delivery_groups import read_pair,pin_sources,mapped_rows


class DeliveryTests(unittest.TestCase):
    def test_stage_root_survives_collector_argv_reset(self):
        from services.data_location import resolve_data_root
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {'DDOKDDAK_PROD3_STAGE_ROOT':d}), patch.object(sys,'argv',['refresh_all']):
            self.assertEqual(resolve_data_root(),Path(d).resolve())

    def pair(self):
        now=datetime.now(timezone.utc)
        return ({'version':'aps1','aps_key':'key','members':[]},
                {'version':'live1','aps_version':'aps1','aps_key':'key','started_at':now-timedelta(seconds=30),
                 'received_at':now,'members':[]})

    def reader(self,a,b):
        connect=MagicMock();c=connect.return_value.__enter__.return_value
        c.execute.side_effect=[MagicMock(fetchone=lambda:{'name':'release'}),MagicMock(fetchone=lambda:b),MagicMock(fetchone=lambda:a)]
        return connect

    def test_pair_uses_its_own_aps_version_and_rejects_mismatch(self):
        a,b=self.pair()
        with tempfile.TemporaryDirectory() as d,patch('services.data_location.resolve_data_root',return_value=Path(d)):
            c=self.reader(a,b);self.assertEqual(read_pair(c)[1]['version'],'live1')
            self.assertEqual(c.return_value.__enter__.return_value.execute.call_args.args[1],('aps1',))
            a['aps_key']='different'
            with self.assertRaises(ValueError):read_pair(self.reader(a,b))

    def test_newer_manual_data_blocks_old_automatic_group(self):
        a,b=self.pair()
        with tempfile.TemporaryDirectory() as d,patch('services.data_location.resolve_data_root',return_value=Path(d)):
            file=Path(d)/'backward-v1/settings/manual_refresh_barrier.json';file.parent.mkdir(parents=True)
            file.write_text(json.dumps({'completed_at':datetime.now(timezone.utc).isoformat()}))
            with self.assertRaisesRegex(ValueError,'수동'):read_pair(self.reader(a,b))

    def test_wide_or_stale_group_is_not_accepted(self):
        for seconds in (181,4000):
            a,b=self.pair()
            if seconds==181:b['started_at']=b['received_at']-timedelta(seconds=seconds)
            else:b['started_at']-=timedelta(seconds=seconds);b['received_at']-=timedelta(seconds=seconds)
            with self.assertRaises(ValueError):read_pair(self.reader(a,b))

    def test_mapping_matches_collector_numeric_and_json_contract(self):
        mapping=[{'source':'qty','target':'qty','type':'number'},
                 {'source':'item','target':'item','type':'string'},
                 {'source':'info','target':'info','type':'json'}]
        self.assertEqual(mapped_rows([{'qty':'12.5','item':123,'info':{'a':1},'extra':'ignored'}],mapping),
                         [{'qty':12.5,'item':'123','info':{'a':1}}])

    def test_today_rows_outside_group_are_excluded_but_history_remains(self):
        a,b=self.pair();day=b['received_at'].astimezone(timezone(timedelta(hours=9))).date().isoformat()
        catalog=[{'dataset':'history','endpoint':'/api/production-performance','snapshot_id':'old',
                  'date_from':'2026-01-01','date_to':day},
                 {'dataset':'other','endpoint':'/api/inventory-ledger-product','snapshot_id':'partial',
                  'date_from':day,'date_to':day}]
        result=pin_sources(catalog,a,b)
        self.assertEqual([s['snapshot_id'] for s in result],['old'])
        self.assertLess(result[0]['date_to'],day)

if __name__=='__main__':unittest.main()
