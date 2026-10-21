import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from collectors import delivery_refresh, aps_display_refresh
from services import dmz_read_client
from services.aps_display import ApsDisplayService
from services.process_status_service import ProcessStatusService
from services.api_health import _probe


class DisplayDeliveryTests(unittest.TestCase):
    def test_aps_publishes_before_missing_live_and_never_starts_calculation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            head={'group_no':1,'version':'aps-new','aps_key':'20261016-1510'}
            with (patch.object(delivery_refresh,'resolve_data_root',return_value=root),
                  patch('services.delivery_groups.head_status',return_value={'groups':[head]}),
                  patch.object(aps_display_refresh,'receive_aps_view',return_value={'aps_key':head['aps_key']}) as receive,
                  patch('services.delivery_groups.read_pair',side_effect=ValueError('waiting')),
                  patch.object(delivery_refresh.subprocess,'run') as calculate,patch('builtins.print')):
                self.assertEqual(delivery_refresh.main.__wrapped__(),2)
            receive.assert_called_once_with(root,head)
            calculate.assert_not_called()
            status=json.loads((root/'backward-v1/settings/delivery_status.json').read_text('utf-8'))
            self.assertEqual(status['aps_view']['aps_key'],head['aps_key'])
            self.assertEqual(status['connection_status'],'success')

    def test_new_aps_does_not_apply_an_older_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            head={'group_no':1,'version':'new','aps_key':'afternoon'}
            with (patch.object(delivery_refresh,'resolve_data_root',return_value=Path(directory)),
                  patch('services.delivery_groups.head_status',return_value={'groups':[head]}),
                  patch.object(aps_display_refresh,'receive_aps_view',return_value={}),
                  patch('services.delivery_groups.read_pair',return_value=({'version':'old'},{'version':'old-live'})),
                  patch.object(delivery_refresh.subprocess,'run') as calculate,patch('builtins.print')):
                self.assertEqual(delivery_refresh.main.__wrapped__(),2)
            calculate.assert_not_called()

    def test_failed_view_keeps_previous_manifest_and_calculation_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=root/'backward-v1/aps-view';base.mkdir(parents=True)
            manifest=base/'current.json';manifest.write_text('{"version":"old"}')
            paired=root/'backward-v1/process-status/aps_process_status.sqlite';paired.parent.mkdir();paired.write_bytes(b'paired')
            with patch.object(aps_display_refresh.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout=b'',stderr=b'fail')):
                with self.assertRaises(ValueError):
                    aps_display_refresh.receive_aps_view(root,{'version':'new','aps_key':'new'})
            self.assertEqual(json.loads(manifest.read_text()),{'version':'old'})
            self.assertEqual(paired.read_bytes(),b'paired')

    def test_view_isolated_publication_and_repeat_skips_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def collect(command,**kwargs):
                env=kwargs['env']
                self.assertEqual(env['DDOKDDAK_APS_VIEW_VERSION'],'new')
                self.assertNotIn('DDOKDDAK_DELIVERY_VERSION',env)
                base=Path(env['DDOKDDAK_PROD3_STAGE_ROOT'])/'backward-v1/process-status'
                (base/'snapshot').mkdir(parents=True)
                (base/'aps_process_status.sqlite').write_bytes(b'new')
                (base/'snapshot/refresh_status.json').write_text('{"status":"success","run_key":"20261016-1510"}')
                return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
            with patch.object(aps_display_refresh.subprocess,'run',side_effect=collect) as collect_mock:
                head={'version':'new','aps_key':'20261016-1510'}
                result=aps_display_refresh.receive_aps_view(root,head)
                self.assertEqual(result,aps_display_refresh.receive_aps_view(root,head))
                self.assertEqual(collect_mock.call_count,1)
            self.assertFalse((root/'backward-v1/process-status').exists())

    def test_display_service_never_changes_calculation_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=root/'backward-v1/aps-view';view=base/'aps-test/backward-v1/process-status'
            view.mkdir(parents=True);(view/'aps_process_status.sqlite').write_bytes(b'view')
            (base/'current.json').write_text('{"folder":"aps-test","aps_key":"20261016-1510"}')
            with (patch('services.aps_display.resolve_data_root',return_value=root),
                  patch('services.process_status_service.STATUS_PATH',root/'paired-status.json'),
                  patch('services.aps_display.safe_mode.active',return_value={})):
                display=ApsDisplayService()
                self.assertEqual(display.database_path,view/'aps_process_status.sqlite')
                self.assertNotEqual(display.database_path,ProcessStatusService().database_path)
            with (patch('services.aps_display.resolve_data_root',return_value=root),patch('services.aps_display.safe_mode.active',return_value={'label':'safe','digest':'test'})):
                self.assertNotEqual(ApsDisplayService().database_path,view/'aps_process_status.sqlite')

    def test_view_catalog_cannot_start_production_calculation(self):
        connection=MagicMock();connection.return_value.__enter__.return_value.execute.return_value.fetchone.return_value={'members':[{'endpoint':'/api/aps-plan'}]}
        dmz_read_client.reset_catalog()
        try:
            with patch.dict(os.environ,{'DDOKDDAK_APS_VIEW_VERSION':'view'}),patch.object(dmz_read_client,'connection',connection),patch('services.delivery_groups.read_pair') as pair:
                self.assertEqual(dmz_read_client.catalog(),[{'endpoint':'/api/aps-plan'}])
                with self.assertRaises(ValueError):dmz_read_client.begin_production_cycle()
                pair.assert_not_called()
        finally:dmz_read_client.reset_catalog()

    def test_connection_success_is_independent_of_package_readiness(self):
        with (patch.dict(os.environ,{'DDOKDDAK_PROD3_TRANSPORT':'dmz'}),
              patch.object(dmz_read_client,'connection') as connection,
              patch.object(dmz_read_client,'catalog',side_effect=ValueError('old package')) as catalog):
            self.assertEqual(_probe('/api/aps-plan',{},8)['status'],'success')
            connection.assert_called_once();catalog.assert_not_called()

