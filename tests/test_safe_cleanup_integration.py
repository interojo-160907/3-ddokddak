from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from config import APP_VERSION
from collectors import safe_stage_cleanup as cleaner
from services.update_download_cleanup import cleanup_downloads
from tests import test_delivery_stage_cleanup as fixture


class AutomaticCleanupTests(unittest.TestCase):
    tearDown = fixture.SafetyTests.tearDown
    save_status = fixture.SafetyTests.save_status
    stage = fixture.SafetyTests.stage
    def setUp(self):
        fixture.SafetyTests.setUp(self)
        (self.settings/'version_collection_bootstrap.json').write_text(json.dumps({'completed_version':APP_VERSION}))
        p=patch('services.update_download_cleanup.cleanup_downloads',return_value=[])
        p.start();self.addCleanup(p.stop)

    def test_version_success_then_daily_cleanup(self):
        old=self.stage('auto-old')
        with patch('services.delivery_stage_cleanup.log_root',return_value=self.root/'logs'):
            result=cleaner.cleanup(self.owner,self.base)
            self.assertEqual(result['status'],'success')
            self.assertFalse(old.exists())
            self.assertEqual(result['removed_folders'],1)
            self.assertEqual(cleaner.cleanup(self.owner,self.base)['status'],'skipped')
        self.assertEqual((self.owner/'bom/product_reference.sqlite').read_bytes(),b'OPERATING DATABASE')

    def test_old_version_and_failed_collection_block_automatic_deletion(self):
        old=self.stage('auto-protected')
        (self.settings/'version_collection_bootstrap.json').write_text('{"completed_version":"2.0.0"}')
        self.assertEqual(cleaner.cleanup(self.owner,self.base)['status'],'deferred')
        self.assertTrue(old.exists())
        (self.settings/'version_collection_bootstrap.json').write_text(json.dumps({'completed_version':APP_VERSION}))
        self.status['status']='waiting_source';self.save_status()
        self.assertEqual(cleaner.cleanup(self.owner,self.base)['status'],'deferred')
        self.assertTrue(old.exists())

    def test_isolated_collector_never_cleans_real_temp(self):
        with patch.dict(os.environ,{'DDOKDDAK_PROD3_STAGE_ROOT':str(self.root)}),patch.object(cleaner,'cleanup') as run,patch('builtins.print'):
            self.assertEqual(cleaner.main(),0)
            run.assert_not_called()


class DownloadCleanupTests(unittest.TestCase):
    def test_only_marked_old_cache_deleted_and_previous_installer_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp)
            def make(name,version,marker=True):
                d=base/name;d.mkdir()
                (d/'setup.exe').write_bytes(b'installer')
                if marker:(d/'production3-download.json').write_text(json.dumps({'application':'DdokddakProduction3','version':version,'installer':'setup.exe'}))
                for p in [*d.iterdir(),d]:os.utime(p,(time.time()-3*86400,)*2)
                return d
            old=make('download-old','3.2.3');previous=make('download-previous','3.2.5')
            current=make('download-current','3.2.6');unknown=make('download-unknown','3.2.2',False)
            self.assertEqual(len(cleanup_downloads('3.2.6',[base])),1)
            self.assertFalse(old.exists())
            for p in (previous,current,unknown):self.assertTrue((p/'setup.exe').exists())

    def test_unknown_file_in_cache_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);d=base/'download-old';d.mkdir()
            (d/'production3-download.json').write_text(json.dumps({'application':'DdokddakProduction3','version':'1.0.0','installer':'setup.exe'}))
            (d/'setup.exe').write_bytes(b'installer');(d/'user.txt').write_text('KEEP')
            self.assertEqual(cleanup_downloads('3.2.6',[base]),[])
            self.assertEqual((d/'user.txt').read_text(),'KEEP')
