import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from services.recovery_backup import ensure_recovery_backup


class RecoveryBackupTests(unittest.TestCase):
    def test_consistent_database_and_settings_are_preserved_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'live-production-need').mkdir()
            (root/'settings').mkdir()
            source=root/'live-production-need/current_production_need.sqlite'
            with closing(sqlite3.connect(source)) as db:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('CREATE TABLE sample(qty INTEGER)')
                db.execute('INSERT INTO sample VALUES(10)');db.commit()
                (root/'settings/collection_schedule.json').write_text('{"live":60}')
                folder=ensure_recovery_backup(root)
                db.execute('UPDATE sample SET qty=20');db.commit()
                self.assertEqual(ensure_recovery_backup(root),folder)
            with closing(sqlite3.connect(folder/'live-production-need/current_production_need.sqlite')) as backup:
                self.assertEqual(backup.execute('SELECT qty FROM sample').fetchone()[0],10)
            self.assertEqual(len(json.loads((folder/'manifest.json').read_text())['files']),2)

    def test_invalid_database_does_not_mark_backup_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'bom').mkdir()
            (root/'bom/product_reference.sqlite').write_text('invalid database')
            with self.assertRaises(sqlite3.DatabaseError):ensure_recovery_backup(root)
            self.assertFalse((root/'recovery/before-2.8.1/manifest.json').exists())
