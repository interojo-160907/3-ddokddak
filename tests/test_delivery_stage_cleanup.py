import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from datetime import datetime, timezone
from services.delivery_stage_cleanup import scan, clean_one, collection_guard, BusyError, DAY


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.base = self.root / 'temp'
        self.base.mkdir()
        self.owner = self.root / 'production3'
        self.settings = self.owner / 'backward-v1/settings'
        self.settings.mkdir(parents=True)
        (self.owner / 'backward-v1/collection.lock').write_bytes(b'0')
        (self.owner / 'bom').mkdir()
        (self.owner / 'bom/product_reference.sqlite').write_bytes(b'OPERATING DATABASE')
        self.status = {'status': 'success', 'applied_version': 'test', 'applied_at': datetime.now(timezone.utc).isoformat()}
        self.save_status()

    def tearDown(self):
        self.tmp.cleanup()

    def save_status(self):
        (self.settings / 'delivery_status.json').write_text(json.dumps(self.status))

    def stage(self, name, complete=False, age=3 * DAY, owner=None, marker=True):
        folder = self.base / ('prod3-delivery-' + name)
        folder.mkdir()
        (folder / 'bom').mkdir()
        (folder / 'bom/product_reference.sqlite').write_bytes(b'temporary copy')
        (folder / 'receive.log').write_text('test failure log')
        if marker:
            (folder / 'delivery-owner.json').write_text(json.dumps({'owner': str(owner or self.owner), 'complete': complete}))
        stamp = time.time() - age
        for p in [*folder.rglob('*'), folder]:
            os.utime(p, (stamp, stamp))
        return folder

    def eligible(self):
        return [r for r in scan(self.base, self.owner)[0] if r.eligible]

    def test_old_failed_removed_operating_db_and_diagnostics_preserved(self):
        folder = self.stage('failed')
        row = self.eligible()[0]
        result = clean_one(self.base, self.owner, row, self.root / 'archive')
        self.assertEqual(result['status'], 'deleted')
        self.assertFalse(folder.exists())
        self.assertEqual((self.owner / 'bom/product_reference.sqlite').read_bytes(), b'OPERATING DATABASE')
        self.assertTrue((self.root / 'archive/receive.log').exists())

    def test_recent_unknown_and_other_owner_protected(self):
        self.stage('recent', age=10)
        self.stage('unknown', marker=False)
        self.stage('foreign', owner=self.root / 'other')
        self.assertFalse(self.eligible())

    def test_newest_success_kept(self):
        self.stage('older', complete=True, age=5 * DAY)
        self.stage('newer', complete=True)
        self.assertEqual(len(self.eligible()), 1)
        self.assertTrue(self.eligible()[0].path.endswith('older'))

    def test_current_stage_protected(self):
        p = self.stage('current')
        self.status['stage_path'] = str(p)
        self.save_status()
        self.assertFalse(self.eligible())

    def test_recovery_journal_and_temporary_journal_block(self):
        self.stage('failed')
        for name in ('delivery_publication.json', 'delivery_publication.tmp'):
            p = self.settings / name
            p.write_text('corrupt recovery record')
            self.assertFalse(self.eligible())
            p.unlink()

    def test_rescan_before_delete_prevents_stale_selection(self):
        folder = self.stage('failed')
        row = self.eligible()[0]
        self.status['stage_path'] = str(folder)
        self.save_status()
        with self.assertRaises(ValueError):
            clean_one(self.base, self.owner, row, self.root / 'archive')
        self.assertTrue(folder.exists())

    def test_lock_contention_never_deletes(self):
        folder = self.stage('failed')
        row = self.eligible()[0]
        with collection_guard(self.owner):
            with self.assertRaises(BusyError):
                clean_one(self.base, self.owner, row, self.root / 'archive')
        self.assertTrue(folder.exists())

    def test_receiving_and_broken_status_block(self):
        self.stage('failed')
        self.status['status'] = 'receiving'
        self.save_status()
        self.assertFalse(self.eligible())
        (self.settings / 'delivery_status.json').write_text('{broken')
        self.assertFalse(self.eligible())

    def test_no_success_after_stage_protected(self):
        self.stage('failed')
        self.status['applied_at'] = datetime.fromtimestamp(time.time() - 10 * DAY, timezone.utc).isoformat()
        self.save_status()
        self.assertFalse(self.eligible())

    def test_changed_files_block_before_delete(self):
        folder = self.stage('failed')
        row = self.eligible()[0]
        from services.delivery_stage_cleanup import archive_diagnostics
        def change(*args):
            archive_diagnostics(*args)
            (folder / 'bom/product_reference.sqlite').write_text('changed')
        with patch('services.delivery_stage_cleanup.archive_diagnostics', side_effect=change):
            with self.assertRaises(ValueError):
                clean_one(self.base, self.owner, row, self.root / 'archive')
        self.assertTrue(folder.exists())

    def test_bulk_delete_releases_collection_lock(self):
        self.stage('failed')
        from services.delivery_stage_cleanup import delete_retired
        def checked_delete(*args):
            with collection_guard(self.owner):
                pass
            return delete_retired(*args)
        with patch('services.delivery_stage_cleanup.delete_retired', side_effect=checked_delete):
            result = clean_one(self.base, self.owner, self.eligible()[0], self.root / 'archive')
        self.assertEqual(result['status'], 'deleted')

    def test_interrupted_retirement_can_resume(self):
        self.stage('failed')
        from services.delivery_stage_cleanup import pending_retirements, delete_retired
        logs = self.root / 'logs'
        destination = logs / 'session' / 'folder'
        with patch('services.delivery_stage_cleanup.delete_retired', side_effect=OSError('simulated interruption')):
            with self.assertRaises(OSError):
                clean_one(self.base, self.owner, self.eligible()[0], destination)
        with patch('services.delivery_stage_cleanup.log_root', return_value=logs):
            pending = pending_retirements(self.base, self.owner)
        self.assertEqual(len(pending), 1)
        self.assertEqual(delete_retired(self.base, self.owner, pending[0][0])['status'], 'deleted')

    @unittest.skipUnless(os.name == 'nt', 'Windows junction test')
    def test_junction_outside_is_never_followed(self):
        import subprocess
        folder = self.stage('linked')
        outside = self.root / 'outside'
        outside.mkdir()
        (outside / 'important.txt').write_text('KEEP')
        junction = folder / 'external'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)], capture_output=True)
        self.assertEqual(result.returncode, 0)
        try:
            self.assertFalse(self.eligible())
            self.assertEqual((outside / 'important.txt').read_text(), 'KEEP')
        finally:
            junction.rmdir()


if __name__ == '__main__':
    unittest.main()
