import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services import delivery_stage_cleanup as core
from services import local_storage_cleanup as local
from tests.test_delivery_stage_cleanup import SafetyTests


class StorageTests(SafetyTests):
    def setUp(self):
        super().setUp()
        self.status['publication_verified']=True
        self.save_status()

    # Legacy fixture expects a last complete stage to remain without the new
    # publication verification flag. Test that compatibility separately.
    def test_newest_success_kept(self):
        self.status.pop('publication_verified');self.save_status()
        super().test_newest_success_kept()

    def old_file(self, relative, content=b'generated'):
        p=self.owner/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(content)
        os.utime(p,(time.time()-3600,)*2)
        return p

    def test_verified_complete_stage_can_be_reclaimed_immediately(self):
        folder=self.stage('done',complete=True,age=2)
        self.assertEqual(core.clean_one(self.base,self.owner,self.eligible()[0],self.root/'log')['status'],'deleted')
        self.assertFalse(folder.exists())

    def test_local_backups_pruned_but_live_settings_and_user_data_preserved(self):
        old=self.old_file('bom/backup/product_reference_before_1.sqlite')
        newest=self.old_file('bom/backup/product_reference_before_2.sqlite')
        os.utime(newest,(time.time()-1800,)*2)
        keep=[self.old_file(p,b'KEEP') for p in ('settings/custom.json','리드지 PDF 백업/test.pdf',
            '리드지 수동 등록/test.pdf','bom/backup/user.sqlite','inventory-status/hydration_api_config.json')]
        results=local.cleanup_local(self.owner)
        self.assertEqual(len(results),1);self.assertFalse(old.exists());self.assertTrue(newest.exists())
        for p in keep:self.assertEqual(p.read_bytes(),b'KEEP')
        self.assertEqual((self.owner/'bom/product_reference.sqlite').read_bytes(),b'OPERATING DATABASE')

    def test_snapshot_cleanup_preserves_unknown_directory_contents(self):
        base='inventory-status/snapshots/'
        old=self.old_file(base+'20261001_100000_000001/inventory_사출창고.json.gz')
        unknown=self.old_file(base+'20261001_100000_000002/user.xlsx',b'KEEP')
        for p in (old.parent,unknown.parent):os.utime(p,(time.time()-3600,)*2)
        local.cleanup_local(self.owner)
        self.assertFalse(old.exists());self.assertTrue(unknown.exists())

    def test_local_collection_lock_and_recovery_journal_protect_all(self):
        old=self.old_file('bom/raw_api/bom_api_1.json.gz');self.old_file('bom/raw_api/bom_api_2.json.gz')
        with core.collection_guard(self.owner):
            local.cleanup_local(self.owner)
        self.assertTrue(old.exists())
        (self.settings/'delivery_publication.json').write_text('[]')
        with self.assertRaises(ValueError):local.cleanup_local(self.owner)
        self.assertTrue(old.exists())

    def test_local_retirement_resumes_after_interruption(self):
        old=self.old_file('bom/raw_api/bom_api_1.json.gz');new=self.old_file('bom/raw_api/bom_api_2.json.gz')
        os.utime(new,(time.time()-1800,)*2)
        with patch.object(local,'_delete_retired',side_effect=OSError('interrupted')):
            local.cleanup_local(self.owner)
        self.assertFalse(old.exists())
        self.assertEqual(local.cleanup_local(self.owner)[0]['status'],'deleted')
        self.assertEqual(list((self.owner/'settings/cleanup-retired').iterdir()),[])

    def test_partial_inventory_and_new_files_protected(self):
        old=self.old_file('bom/raw_api/bom_api_1.json.gz');self.old_file('bom/raw_api/bom_api_2.json.gz')
        report=self.old_file('backward-v1/inventory-status/refresh_status.json',b'{"status":"partial"}')
        local.cleanup_local(self.owner)
        self.assertTrue(old.exists())


class LeanCollectionTests(unittest.TestCase):
    def test_low_disk_space_is_rejected_before_copying(self):
        from collectors.delivery_refresh import check_working_space
        with tempfile.TemporaryDirectory() as d, patch('collectors.delivery_refresh.shutil.disk_usage',return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(ValueError,'공간 부족'):
                check_working_space(Path(d))

    def test_delivery_child_does_not_make_nested_publication_copy(self):
        from collectors import inventory_live_refresh as group
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with (patch.dict(os.environ,{'DDOKDDAK_PROD3_STAGE_ROOT':d,'DDOKDDAK_DELIVERY_VERSION':'v','DDOKDDAK_PROD3_TRANSPORT':'dmz'}),
                  patch.object(group,'InventoryStatusService',return_value=SimpleNamespace(cache=root)),
                  patch('services.dmz_read_client.begin_production_cycle',return_value={'cycle_id':'v'}),
                  patch.object(group,'STATUS_PATH',root/'live-status.json'),
                  patch('collectors.production_performance_collector.STATUS_PATH',root/'production-status.json'),
                  patch.object(group,'_refresh',return_value={'status':'success','cycle_id':'v'}) as refresh,
                  patch('services.collection_staging.copy_existing') as copy,
                  patch('services.collection_staging.publish_group') as publish):
                self.assertEqual(group.refresh()['status'],'success')
                copy.assert_not_called();publish.assert_not_called();refresh.assert_called_once()

    def test_bom_unchanged_path_not_forced_even_during_full_refresh(self):
        from collectors import refresh_all as refresh
        with (patch.object(refresh,'_write_result'),patch.object(refresh,'resolve_api_key',return_value=''),
              patch.object(refresh,'refresh_aps_full',return_value={'status':'success'}),
              patch.object(refresh,'refresh_bom',return_value={'status':'skipped'}) as bom,
              patch.object(refresh,'refresh_live',return_value={'status':'success','production':{'status':'success'}}),
              patch('builtins.print')):
            self.assertEqual(refresh.main.__wrapped__(True),0)
            self.assertFalse(bom.call_args.kwargs['force'])

    def test_delivery_input_copy_excludes_cache_and_archives(self):
        from collectors.delivery_refresh import copy_inputs
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'root';stage=Path(d)/'stage'
            for name in ('backward-v1/dmz-cache/source.json.gz','bom/backup/old.sqlite','backward-v1/settings/prefs.json'):
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{}')
            copy_inputs(root,stage)
            self.assertFalse((stage/'backward-v1/dmz-cache').exists())
            self.assertFalse((stage/'bom/backup').exists())
            self.assertTrue((stage/'backward-v1/settings/prefs.json').exists())
