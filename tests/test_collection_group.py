import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collectors import live_production_need_collector as live
from services.collection_staging import publish_group, recover_publication
from services.dmz_read_client import transport_for_collection


class GroupTests(unittest.TestCase):
    def test_manual_uses_original_api_and_automatic_uses_server(self):
        for source in ('all', 'production', 'live', 'hydration'):
            self.assertEqual(transport_for_collection(source, False), 'manual_live')
            self.assertEqual(transport_for_collection(source, True), 'dmz')

    def test_unchanged_wip_is_checked_without_full_download(self):
        previous = dict(aps_source_refreshed_at='2026-10-15 07:30:00',
                        wip_source_refreshed_at='2026-10-15 07:45:00',
                        baseline_date_from='2026-10-14',
                        calculation_revision=live.CALCULATION_REVISION)
        with (patch.object(live, '_aps_cycle', return_value=previous['aps_source_refreshed_at']),
              patch.object(live, '_source_provenance', return_value={}),
              patch.object(live, '_live_meta', return_value=previous),
              patch.object(live, '_read_json', return_value=previous),
              patch.object(live, '_aps_item_ids', return_value={'P1'}),
              patch.object(live, 'probe_wip_source', return_value={
                  'ready': True, 'source_refreshed_at': previous['wip_source_refreshed_at']}) as probe,
              patch.object(live, '_collect_wip') as collect,
              patch.object(live, '_collect_inventory', side_effect=RuntimeError('inventory reached'))):
            with self.assertRaisesRegex(RuntimeError, 'inventory reached'):
                live.refresh()
            probe.assert_called_once()
            collect.assert_not_called()

    def test_publication_failure_restores_every_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pairs = []
            for name in ('performance', 'inventory', 'hydration'):
                source, target = root / (name + '.new'), root / name
                source.write_text('new'); target.write_text('old')
                pairs.append((source, target))
            replace = Path.replace
            def fail(path, target):
                if path.name == 'inventory.publish':
                    raise PermissionError('simulated Windows reader')
                return replace(path, target)
            with patch.object(Path, 'replace', fail):
                with self.assertRaises(PermissionError):
                    publish_group(pairs, root / 'cycle', root / 'journal.json')
            self.assertEqual([p.read_text() for _, p in pairs], ['old'] * 3)
            self.assertFalse((root / 'journal.json').exists())

    def test_interrupted_publication_recovers_on_next_start(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, backup, journal = root/'live', root/'saved', root/'journal.json'
            target.write_text('incomplete'); backup.write_text('complete')
            journal.write_text(json.dumps([dict(target=str(target), backup=str(backup), existed=True)]))
            recover_publication(journal)
            self.assertEqual(target.read_text(), 'complete')
            self.assertFalse(journal.exists())
