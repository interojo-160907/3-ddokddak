import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from collectors import delivery_refresh as receiver


class PublicationTests(unittest.TestCase):
    def test_full_refresh_is_forwarded_and_concurrent_preferences_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'published';stage=Path(directory)/'stage'
            settings=root/'backward-v1/settings';settings.mkdir(parents=True);stage.mkdir()
            preference=settings/'collection_schedule.json'
            preference.write_text('{"aps_minutes":1}')
            (settings/'delivery_status.json').write_text(json.dumps({'applied_version':'live'}))
            now=datetime.now(timezone.utc)
            pair=({'version':'aps','aps_key':'run'},{'version':'live','started_at':now,'received_at':now})
            def calculate(command,**kwargs):
                self.assertIn('--full',command)
                self.assertEqual(kwargs['env']['DDOKDDAK_PROD3_STAGE_ROOT'],str(stage))
                preference.write_text('{"aps_minutes":5}')
                (stage/'backward-v1/settings/full_refresh_status.json').write_text(json.dumps({'results':{'live':{'status':'success'}}}))
                return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
            def receive_lidding(**kwargs):
                from tests.test_lidding import bundle
                b=bundle();b['version']='live'
                target=kwargs['destination'];target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(json.dumps(b),encoding='utf-8')
            with (patch.object(receiver,'resolve_data_root',return_value=root),
                  patch('services.delivery_groups.head_status',return_value={'groups':[]}),
                  patch('services.delivery_groups.read_pair',return_value=pair),
                  patch.object(receiver.tempfile,'mkdtemp',return_value=str(stage)),
                  patch.object(receiver.subprocess,'run',side_effect=calculate),
                  patch('services.lidding_data.download',side_effect=receive_lidding),
                  patch.object(sys,'argv',['delivery_refresh','--full']),
                  patch('builtins.print')):
                self.assertEqual(receiver.main.__wrapped__(),0)
            from services.lidding_data import cache_ready
            self.assertTrue(cache_ready(root,'live'))
            with patch.object(receiver,'resolve_data_root',return_value=root), patch('services.delivery_groups.head_status',return_value={'groups':[]}), patch('services.delivery_groups.read_pair',return_value=pair), patch.object(receiver.subprocess,'run') as child, patch('services.lidding_data.download') as download, patch('builtins.print'):
                self.assertEqual(receiver.main.__wrapped__(),0)
                child.assert_not_called();download.assert_not_called()
            self.assertEqual(json.loads(preference.read_text())['aps_minutes'],5)
            self.assertEqual(json.loads((settings/'delivery_status.json').read_text('utf-8'))['applied_version'],'live')
            # Same-version reinstall cleared completion metadata but retained lidding.
            completion = settings/'full_refresh_status.json'
            completion.unlink()
            with (patch.object(receiver,'resolve_data_root',return_value=root),
                  patch('services.delivery_groups.head_status',return_value={'groups':[]}),
                  patch('services.delivery_groups.read_pair',return_value=pair),
                  patch.object(receiver.tempfile,'mkdtemp',return_value=str(stage)),
                  patch.object(receiver.subprocess,'run',side_effect=calculate) as child,
                  patch('services.lidding_data.download',side_effect=receive_lidding),
                  patch.object(sys,'argv',['delivery_refresh','--full']),
                  patch('builtins.print')):
                self.assertEqual(receiver.main.__wrapped__(),0)
                child.assert_called_once()
            self.assertTrue(completion.is_file())

    def test_failed_child_never_publishes_partial_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'published';stage=Path(directory)/'stage'
            base=root/'backward-v1/settings';base.mkdir(parents=True);stage.mkdir()
            status=base/'full_refresh_status.json';status.write_text('{"old":true}')
            now=datetime.now(timezone.utc)
            def calculate(*args,**kwargs):
                (stage/'backward-v1/settings/full_refresh_status.json').write_text('{"partial":true}')
                return SimpleNamespace(returncode=2,stdout=b'',stderr=b'failed')
            with (patch.object(receiver,'resolve_data_root',return_value=root),
                  patch('services.delivery_groups.head_status',return_value={'groups':[]}),
                  patch('services.delivery_groups.read_pair',return_value=({'version':'aps','aps_key':'run'},{'version':'live','started_at':now,'received_at':now})),
                  patch.object(receiver.tempfile,'mkdtemp',return_value=str(stage)),
                  patch.object(receiver.subprocess,'run',side_effect=calculate),patch('builtins.print')):
                self.assertEqual(receiver.main.__wrapped__(),2)
            self.assertEqual(json.loads(status.read_text()),{'old':True})

    def test_failed_lidding_never_publishes_other_successful_calculations(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'published';stage=Path(directory)/'stage'
            base=root/'backward-v1/settings';base.mkdir(parents=True);stage.mkdir()
            status=base/'full_refresh_status.json';status.write_text('{"old":true}')
            now=datetime.now(timezone.utc)
            def calculate(*args,**kwargs):
                (stage/'backward-v1/settings/full_refresh_status.json').write_text('{"results":{"live":{"status":"success"}}}')
                return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
            with (patch.object(receiver,'resolve_data_root',return_value=root),
                  patch('services.delivery_groups.head_status',return_value={'groups':[]}),
                  patch('services.delivery_groups.read_pair',return_value=({'version':'aps','aps_key':'run'},{'version':'live','started_at':now,'received_at':now})),
                  patch.object(receiver.tempfile,'mkdtemp',return_value=str(stage)),
                  patch.object(receiver.subprocess,'run',side_effect=calculate),
                  patch('services.lidding_data.download',side_effect=ValueError('missing issue')),
                  patch('builtins.print')):
                self.assertEqual(receiver.main.__wrapped__(),2)
            self.assertEqual(json.loads(status.read_text()),{'old':True})
