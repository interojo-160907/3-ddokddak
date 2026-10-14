import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from services import dmz_read_client as dmz
from services.erp_api_client import request_json


def source(endpoint, sid='snapshot', **extra):
    return dict(dict(endpoint=endpoint,snapshot_id=sid,request_params={},row_count=1),**extra)


class DmzTransportTests(unittest.TestCase):
    def test_automatic_never_calls_http(self):
        row=source('/api/aps-plan')
        with patch.dict(os.environ,{'DDOKDDAK_PROD3_TRANSPORT':'dmz'}), \
             patch.object(dmz,'catalog',return_value=[row]), \
             patch.object(dmz,'snapshot',return_value={'metadata':{'source_refreshed_at':'2026-10-14 07:45:23'},'rows':[{'item_id':'P1'}]}), \
             patch.object(dmz,'prefer_newer_manual',side_effect=lambda e,p,v:v), \
             patch('services.erp_api_client._session',side_effect=AssertionError('HTTP must not be called')):
            self.assertEqual(request_json('/api/aps-plan')['total_count'],1)

    def test_missing_server_data_has_no_http_fallback(self):
        with patch.dict(os.environ,{'DDOKDDAK_PROD3_TRANSPORT':'dmz'}), \
             patch.object(dmz,'catalog',return_value=[]), \
             patch('services.erp_api_client._session',side_effect=AssertionError('HTTP must not be called')):
            with self.assertRaisesRegex(RuntimeError,'미준비'):
                request_json('/api/hydration-job-list')

    def test_manual_live_keeps_wip_and_references_on_server(self):
        with patch.dict(os.environ,{'DDOKDDAK_PROD3_TRANSPORT':'manual_live'}):
            self.assertTrue(dmz.route_to_dmz('/api/aps-wip'))
            self.assertTrue(dmz.route_to_dmz('/api/item-list-bulk'))
            for endpoint in ('/api/production-performance','/api/inventory-ledger-product','/api/hydration-job-list'):
                self.assertFalse(dmz.route_to_dmz(endpoint))

    def test_source_clock_prevents_older_auto_from_overwriting_manual(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(dmz,'resolve_data_root',return_value=Path(directory)), \
             patch.dict(os.environ,{'DDOKDDAK_PROD3_TRANSPORT':'manual_live'}):
            params={'date_from':'2026-10-14','date_to':'2026-10-14','limit':0}
            manual={'rows':[{'qty':20}],'total_count':1,'query_date':'2026-10-14 09:12'}
            dmz.remember_manual('/api/production-performance',params,manual)
            old={'rows':[{'qty':10}],'query_date':'2026-10-14 09:00','downloaded_at':'2026-10-14 09:30'}
            self.assertEqual(dmz.prefer_newer_manual('/api/production-performance',params,old)['rows'],manual['rows'])
            newer={'rows':[{'qty':30}],'query_date':'2026-10-14 09:20'}
            self.assertIs(dmz.prefer_newer_manual('/api/production-performance',params,newer),newer)

    def test_truncated_manual_is_not_saved(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(dmz,'resolve_data_root',return_value=Path(directory)), \
             patch.dict(os.environ,{'DDOKDDAK_PROD3_TRANSPORT':'manual_live'}):
            dmz.remember_manual('/api/production-performance',{},
                                {'rows':[{}],'total_count':2,'truncated':True})
            self.assertFalse(list(Path(directory).rglob('*.gz')))

    def test_source_clock_compares_the_same_time_zone(self):
        self.assertEqual(dmz._source_clock({'query_date':'2026-10-14T01:10:00Z'}),
                         dmz._source_clock({'query_date':'2026-10-14T10:10:00+09:00'}))

    def test_item_specs_read_each_server_partition_once(self):
        from services.inventory_status_service import InventoryStatusService
        def reply(endpoint,params,**kwargs):
            return {'rows':[{'gd_cd':base+'-03.00'} for base in ('P1000','P1001','R1000')
                            if base.startswith(params['gd_cd'])],'truncated':False}
        with patch.dict(os.environ,{'DDOKDDAK_PROD3_TRANSPORT':'dmz'}), \
             patch('services.inventory_status_service.request_json',side_effect=reply) as read:
            instance=object.__new__(InventoryStatusService)
            instance.save_response=lambda key,data:saved.update({key:data})
            saved={}
            instance._warm_specs(['P1000','P1001','R1000'])
            self.assertEqual(read.call_count,2)
            self.assertEqual(saved['item_P1001']['rows'],[{'gd_cd':'P1001-03.00'}])

    def test_each_day_is_owned_by_one_latest_snapshot(self):
        rows=[source('/api/production-performance','today',date_from='2026-10-14',date_to='2026-10-14'),
              source('/api/production-performance','recent',date_from='2026-10-13',date_to='2026-10-14')]
        def load(s):
            data=[{'pr_dt':'2026-10-14','qty':20}] if s['snapshot_id']=='today' else [
                {'pr_dt':'2026-10-13','qty':10},{'pr_dt':'2026-10-14','qty':5}]
            return {'metadata':{},'rows':data}
        with patch.object(dmz,'catalog',return_value=rows),patch.object(dmz,'snapshot',side_effect=load), \
             patch.object(dmz,'prefer_newer_manual',side_effect=lambda e,p,v:v):
            result=dmz.request_json('/api/production-performance',{'date_from':'2026-10-13','date_to':'2026-10-14'})
            self.assertEqual(sum(r['qty'] for r in result['rows']),30)
            with self.assertRaisesRegex(RuntimeError,'미수집'):
                dmz.request_json('/api/production-performance',{'date_from':'2026-10-12','date_to':'2026-10-14'})

    def test_collection_route(self):
        self.assertEqual(dmz.transport_for_collection('live',False),'manual_live')
        self.assertEqual(dmz.transport_for_collection('live',True),'dmz')
        self.assertEqual(dmz.transport_for_collection('all',False),'dmz')

    def test_daily_ledger_range_preserves_opening_movements_and_closing(self):
        yesterday='2026-10-13';today='2026-10-14'
        rows=[source('/api/inventory-ledger-product',day,date_from=day,date_to=day,
                     request_params={'wh_cd':'Q001'}) for day in (today,yesterday)]
        def load(s):
            a,b,c,d=(100,20,5,115) if s['snapshot_id']==yesterday else (115,30,10,135)
            return {'metadata':{'query_date':today+' 10:00'},'rows':[
                {'fwh_cd':'Q001','gd_cd':'R1','lot_no':'LOT','sub_lot':'A','unit_cd':'PCS',
                 'lm_qty':a,'ip_qty':b,'chul_qty':c,'stock_qty':d}]}
        with patch.object(dmz,'catalog',return_value=rows),patch.object(dmz,'snapshot',side_effect=load), \
             patch.object(dmz,'prefer_newer_manual',side_effect=lambda e,p,v:v):
            value=dmz.request_json('/api/inventory-ledger-product',
                {'wh_cd':'Q001','date_from':yesterday,'date_to':today})['rows'][0]
            self.assertEqual([value[k] for k in ('lm_qty','ip_qty','chul_qty','stock_qty')],
                             [100,50,15,135])

    def test_wider_period_ledger_cannot_substitute_daily_totals(self):
        rows=[source('/api/inventory-ledger-product',date_from='2026-10-08',
                     date_to='2026-10-14',request_params={'wh_cd':'Q001'})]
        with patch.object(dmz,'catalog',return_value=rows):
            with self.assertRaisesRegex(RuntimeError,'미수집'):
                dmz.request_json('/api/inventory-ledger-product',
                    {'wh_cd':'Q001','date_from':'2026-10-13','date_to':'2026-10-14'})

    def test_s_factory_includes_injection_factory_evidence(self):
        self.assertTrue(dmz._matches({'fac_cd':'01','sachul_fac_cd':'04','stts':'C'},
                                    {'fac_cd':'04','stts':'C'},set()))
        self.assertFalse(dmz._matches({'fac_cd':'01','sachul_fac_cd':'02','stts':'C'},
                                     {'fac_cd':'04','stts':'C'},set()))

    def test_snapshot_hash_rejects_same_count_different_quantity(self):
        import hashlib,json
        rows=[{'qty':20}]
        digest=hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True,
                    separators=(',',':')).encode()).hexdigest()
        s={'row_count':1,'content_hash':digest}
        self.assertTrue(dmz._valid_rows(rows,s))
        self.assertFalse(dmz._valid_rows([{'qty':10}],s))

    def test_aps_update_waits_for_server_inputs_without_source_collection(self):
        from collectors import refresh_all
        with tempfile.TemporaryDirectory() as directory, \
             patch('services.collection_lock.resolve_data_root',return_value=Path(directory)), \
             patch.object(refresh_all,'RESULT_PATH',Path(directory)/'report.json'), \
             patch.object(refresh_all,'refresh_aps_full',return_value={'status':'success'}), \
             patch.object(refresh_all,'refresh_bom') as bom, \
             patch.object(refresh_all,'refresh_production') as production, \
             patch.object(refresh_all,'refresh_live') as live, \
             patch.dict(os.environ,{'DDOKDDAK_PROD3_TRANSPORT':'dmz'}), \
             patch.object(dmz,'missing_calculation_sources',return_value=['/api/aps-wip']), \
             patch('builtins.print'):
            self.assertEqual(refresh_all.main(force_full=True),2)
            bom.assert_not_called()
            production.assert_not_called()
            live.assert_not_called()


if __name__=='__main__': unittest.main()
