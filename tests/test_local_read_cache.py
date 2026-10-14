import sqlite3,tempfile,unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock
from services import local_read_cache as cache

class LocalReadCacheTests(unittest.TestCase):
    def test_cached_rows_cannot_be_mutated_by_another_tab(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'data.sqlite';path.write_bytes(b'first')
            load=Mock(return_value=[{'qty':10,'lots':['A']}])
            first=cache.read((path,),('view',),load);first[0]['lots'].append('B')
            self.assertEqual(cache.read((path,),('view',),load)[0]['lots'],['A'])
            self.assertEqual(load.call_count,1)
    def test_wal_commit_invalidates_cache_without_db_file_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'data.sqlite'
            with closing(sqlite3.connect(path)) as c:
                c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE x(qty)');c.commit()
                load=Mock(side_effect=lambda:[{'qty':c.execute('SELECT COUNT(*) FROM x').fetchone()[0]}])
                self.assertEqual(cache.read((path,),('wal-view',),load)[0]['qty'],0)
                c.execute('INSERT INTO x VALUES(5)');c.commit()
                self.assertEqual(cache.read((path,),('wal-view',),load)[0]['qty'],1)
                self.assertEqual(load.call_count,2)
