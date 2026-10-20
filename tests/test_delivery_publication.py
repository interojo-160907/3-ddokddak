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
            now=datetime.now(timezone.utc)
            pair=({'version':'aps','aps_key':'run'},{'version':'live','started_at':now,'received_at':now})
            def calculate(command,**kwargs):
                self.assertIn('--full',command)
                self.assertEqual(kwargs['env']['DDOKDDAK_PROD3_STAGE_ROOT'],str(stage))
                preference.write_text('{"aps_minutes":5}')
                (stage/'backward-v1/settings/full_refresh_status.json').write_text(json.dumps({'results':{'live':{'status':'success'}}}))
                return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
            with (patch.object(receiver,'resolve_data_root',return_value=root),
                  patch('services.delivery_groups.head_status',return_value={'groups':[]}),
                  patch('services.delivery_groups.read_pair',return_value=pair),
                  patch.object(receiver.tempfile,'mkdtemp',return_value=str(stage)),
                  patch.object(receiver.subprocess,'run',side_effect=calculate),
                  patch.object(sys,'argv',['delivery_refresh','--full']),
                  patch('builtins.print')):
                self.assertEqual(receiver.main.__wrapped__(),0)
            self.assertEqual(json.loads(preference.read_text())['aps_minutes'],5)
            self.assertEqual(json.loads((settings/'delivery_status.json').read_text('utf-8'))['applied_version'],'live')

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
