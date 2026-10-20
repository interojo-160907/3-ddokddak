from contextlib import nullcontext
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import gui_app_pyside6 as entry


class CollectorLifecycleTests(unittest.TestCase):
    def test_isolated_calculation_does_not_create_another_recovery_backup(self):
        with patch.dict(os.environ, {'DDOKDDAK_PROD3_STAGE_ROOT':'isolated-test'}), \
                patch('services.collection_lock.collection_lock', return_value=nullcontext()), \
                patch('services.recovery_backup.ensure_recovery_backup') as backup, \
                patch.object(entry.importlib, 'import_module', return_value=SimpleNamespace(main=lambda:0)), \
                patch.object(entry.sys, 'argv', []):
            self.assertEqual(entry.run_collector_mode(['process_status_collector']),0)
            backup.assert_not_called()

    def test_cleanup_entrypoint_does_not_hold_collection_lock_for_bulk_delete(self):
        with patch('services.collection_lock.collection_lock') as lock, \
                patch('services.recovery_backup.ensure_recovery_backup') as backup, \
                patch.object(entry.importlib, 'import_module', return_value=SimpleNamespace(main=lambda:0)), \
                patch.object(entry.sys, 'argv', []):
            self.assertEqual(entry.run_collector_mode(['safe_stage_cleanup']),0)
            lock.assert_not_called()
            backup.assert_not_called()
