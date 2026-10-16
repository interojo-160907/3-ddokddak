import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from config import APP_VERSION, collection_directories
from services.collection_lock import collection_lock
from services import inventory_status_service as inventory
from collectors import aps_update_monitor as monitor, refresh_all


class UpgradeTests(unittest.TestCase):
    def test_initial_full_refresh_bypasses_unchanged_aps_and_incremental_sources(self):
        with patch.object(refresh_all, 'refresh_bom', return_value={'status':'success'}) as bom, patch.object(refresh_all, 'refresh_aps') as monitor_call, patch.object(refresh_all, 'refresh_aps_full', return_value={'status':'success'}) as aps, patch.object(refresh_all, 'refresh_production', return_value={'status':'success'}) as production, patch.object(refresh_all, 'refresh_live', return_value={'status':'success'}) as live, patch.object(refresh_all, '_write_result'), patch('builtins.print'):
            self.assertEqual(refresh_all.main(force_full=True), 0)
            self.assertTrue(bom.call_args.kwargs['force'])
            production.assert_not_called()  # Production is now inside the staged live group.
            live.assert_called_once_with(force_full=True)
            aps.assert_called_once()
            monitor_call.assert_not_called()

    def test_initial_collection_runs_even_with_all_schedules_disabled(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from ui.main_window import MainWindow
        window = SimpleNamespace(collection_schedule={key + '_minutes':0 for key in ('bom','aps','production','live')}, _collection_last_attempt={}, _read_refresh_status=Mock(return_value={}), _start_data_collection=Mock())
        MainWindow._run_scheduled_collections(window)
        window._start_data_collection.assert_called_once_with('all', scheduled=True)

    def test_partial_initial_collection_retries_live_without_redownloading_base(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from ui.main_window import MainWindow
        window = SimpleNamespace(_collection_last_attempt={}, _read_refresh_status=Mock(return_value={'base_completed_version':APP_VERSION, 'completed_version':''}), _start_data_collection=Mock())
        MainWindow._run_scheduled_collections(window)
        window._start_data_collection.assert_called_once_with('live', scheduled=True)

    def test_old_results_are_not_imported_and_configuration_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / 'inventory-status'
            old.mkdir()
            config = old / 'hydration_api_config.json'
            config.write_text('{"lookback_days": 10}', encoding='utf-8')
            stale = old / 'latest_result.json'
            stale.write_text('{"old_plan": true}', encoding='utf-8')
            with patch.object(inventory, 'DATA_CENTER_DIR', root), patch.object(inventory, 'DATA_DIR', root/'application'), patch.dict(os.environ, {'DDOKDDAK_INVENTORY_STATUS_DATA_DIR': ''}):
                service = inventory.InventoryStatusService()
                self.assertEqual(service.cache, root/'backward-v1/inventory-status')
                target = service.cache / config.name
                self.assertEqual(target.read_bytes(), config.read_bytes())
                self.assertFalse((service.cache / stale.name).exists())
                target.write_text('{"lookback_days": 20}', encoding='utf-8')
                inventory.InventoryStatusService()
                self.assertEqual(json.loads(target.read_text())['lookback_days'], 20)
                self.assertTrue(stale.exists())
                self.assertEqual(json.loads(config.read_text())['lookback_days'], 10)

    def test_fresh_install_and_reinstall_keep_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root/'process-status/aps_process_status.sqlite'
            legacy.parent.mkdir()
            legacy.write_bytes(b'old-plan-data')
            for _ in range(2):
                for path in collection_directories(root):
                    path.mkdir(parents=True, exist_ok=True)
            self.assertEqual(legacy.read_bytes(), b'old-plan-data')
            self.assertTrue((root/'backward-v1/process-status/snapshot').is_dir())
            self.assertTrue((root/'backward-v1/settings').is_dir())
            self.assertFalse((root/'backward-v1/process-status/aps_process_status.sqlite').exists())

    def test_collectors_and_readers_use_same_new_paths(self):
        from collectors import process_status_collector as aps, live_production_need_collector as live
        from services import process_status_service as aps_view, live_production_need_service as live_view, dashboard_service as dashboard
        self.assertEqual(aps.DB_PATH, aps_view.DB_PATH)
        self.assertEqual(aps.DB_PATH, live.APS_DB_PATH)
        self.assertEqual(aps.DB_PATH, dashboard.APS_DB)
        self.assertEqual(live.DB_PATH, live_view.DB_PATH)
        self.assertEqual(live.DB_PATH, dashboard.LIVE_DB)
        self.assertIn('backward-v1', aps.DB_PATH.parts)
        self.assertIn('backward-v1', live.DB_PATH.parts)

    def test_monitor_recollects_missing_database_even_with_success_status(self):
        state = dict(source_identity='x', backward_count=1, backward_at='now', plan_count=1, plan_at='now', wip_at='now', wip_count=1)
        with tempfile.TemporaryDirectory() as directory, patch.object(monitor, 'DB_PATH', Path(directory)/'missing.sqlite'), patch.object(monitor, '_local_status', return_value=dict(state, status='success')), patch.object(monitor, '_request', return_value={}), patch('services.backward_source.outbound_state', return_value=state), patch.object(monitor, 'refresh', return_value={'status':'success','changed':True}) as refresh:
            self.assertTrue(monitor.check_and_refresh()['changed'])
            refresh.assert_called_once()

    def test_failed_aps_does_not_mark_bootstrap_success_or_run_live(self):
        with patch.object(refresh_all, 'refresh_bom', return_value={'status':'success'}), patch.object(refresh_all, 'refresh_aps', return_value={'status':'retained'}), patch.object(refresh_all, 'refresh_production', return_value={'status':'success'}), patch.object(refresh_all, 'refresh_live') as live, patch.object(refresh_all, '_write_result'), patch('builtins.print'):
            self.assertEqual(refresh_all.main(), 1)
            live.assert_not_called()

    def test_version_metadata_is_consistent(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(APP_VERSION, '3.2.3')
        self.assertIn(f'"{APP_VERSION}"', (root/'installer/production3.iss').read_text(encoding='utf-8'))
        version_tuple = tuple(int(part) for part in APP_VERSION.split('.')) + (0,)
        self.assertIn(f'filevers={version_tuple}', (root/'installer/version_info.txt').read_text(encoding='utf-8'))
        self.assertNotIn('DelTree(', (root/'installer/production3.iss').read_text(encoding='utf-8'))
        self.assertIn('if not UninstallSilent then MsgBox(', (root/'installer/production3.iss').read_text(encoding='utf-8'))


class CollectionLockTests(unittest.TestCase):
    def child(self, root):
        return subprocess.run([sys.executable, '-c', 'from services.collection_lock import collection_lock; import sys\nwith collection_lock(sys.argv[1]): print("acquired")', str(root)], capture_output=True, timeout=10)

    def test_competing_process_is_blocked_and_exception_releases_lock(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, 'interrupt'):
                with collection_lock(root):
                    with collection_lock(root):
                        self.assertNotEqual(self.child(root).returncode, 0)
                    raise ValueError('interrupt')
            self.assertEqual(self.child(root).returncode, 0)

    def test_killed_collector_does_not_leave_stale_lock(self):
        with tempfile.TemporaryDirectory() as root:
            process = subprocess.Popen([sys.executable, '-u', '-c', 'from services.collection_lock import collection_lock; import sys,time\nwith collection_lock(sys.argv[1]):\n print("ready", flush=True)\n time.sleep(30)', root], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), 'ready')
                self.assertNotEqual(self.child(root).returncode, 0)
            finally:
                process.kill()
                process.communicate(timeout=10)
            self.assertEqual(self.child(root).returncode, 0)
