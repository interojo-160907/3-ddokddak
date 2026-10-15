import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

from collectors import production_performance_collector as production
from services import dmz_read_client as dmz
from services.collection_health import header_status
from ui.main_window import _bootstrap_resume_source, _source_wait_due, MainWindow
from config import APP_VERSION
import test_collection_catchup as catchup


class ProductionSourceWaitTests(unittest.TestCase):
    def test_month_end_uses_published_history_without_duplicating_current_alias(self):
        old={'endpoint':'/api/production-performance','snapshot_id':'old',
             'date_from':'2026-09-01','date_to':'2026-09-16','published_at':'2026-10-15'}
        latest={'endpoint':'/api/production-performance','snapshot_id':'latest',
                'date_from':'2026-09-17','date_to':'2026-10-31','published_at':'2026-10-31'}
        connection=MagicMock()
        connection.execute.side_effect=[Mock(fetchall=Mock(return_value=[latest])),
                                        Mock(fetchall=Mock(return_value=[])),
                                        Mock(fetchall=Mock(return_value=[old, latest]))]
        with patch.object(dmz,'_catalog',None), patch.object(dmz,'connection') as connect, \
             patch.object(dmz,'datetime') as clock:
            connect.return_value.__enter__.return_value=connection
            clock.now.return_value=datetime(2026,10,31,tzinfo=timezone(timedelta(hours=9)))
            self.assertEqual([r['snapshot_id'] for r in dmz.catalog()],['latest','old'])
            self.assertEqual(dmz.missing_production_days(date(2026,9,1),date(2026,10,31)),[])
            self.assertEqual(connection.execute.call_args.args[1],('2026-09-01','2026-10-31'))
            sql=connection.execute.call_args.args[0]
            self.assertIn("s.status='published' AND NOT sc.truncated",sql)

    def test_coverage_holes_and_explicit_empty_days(self):
        sources = [{'endpoint':'/api/production-performance', 'date_from':'2026-09-30',
                    'date_to':'2026-09-30', 'row_count':0},
                   {'endpoint':'/api/production-performance', 'date_from':'2026-10-02',
                    'date_to':'2026-10-02'}]
        with patch.object(dmz, 'catalog', return_value=sources):
            self.assertEqual(dmz.missing_production_days(date(2026,9,30),date(2026,10,2)), ['2026-10-01'])
        with patch.object(dmz, 'catalog', side_effect=RuntimeError('connection failed')):
            with self.assertRaisesRegex(RuntimeError,'connection failed'):
                dmz.missing_production_days(date(2026,10,1),date(2026,10,1))

    def test_wait_preserves_database_and_success_markers_then_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {'DATA_DIR':root, 'DB_PATH':root/'production.sqlite',
                     'STATUS_PATH':root/'snapshot/status.json', 'RAW_DIR':root/'raw', 'BACKUP_DIR':root/'backup'}
            prior = {'status':'success','date_to':'2026-10-14','refreshed_at':'2026-10-14T16:30:38+09:00',
                     'daily_full_date':'2026-10-14','stored_rows':18517}
            paths['DB_PATH'].write_bytes(b'previous database preserved')
            with patch.multiple(production, **paths), patch.dict(os.environ, {'DDOKDDAK_PROD3_TRANSPORT':'dmz'}), \
                 patch.object(production,'collection_window',return_value=(date(2026,9,1),date(2026,10,15))), \
                 patch.object(production,'_read_status',return_value=prior), \
                 patch.object(dmz,'missing_production_days',return_value=['2026-10-15']) as coverage, \
                 patch.object(production,'_fetch',return_value={'rows':[], 'total_count':0}) as fetch:
                result = production.refresh(force_full=True)
                self.assertEqual(result['status'],'waiting_source')
                for key in ('daily_full_date','refreshed_at','stored_rows'):
                    self.assertEqual(result[key],prior[key])
                self.assertEqual(paths['DB_PATH'].read_bytes(),b'previous database preserved')
                fetch.assert_not_called()
                self.assertFalse(paths['RAW_DIR'].exists())
                self.assertFalse(paths['BACKUP_DIR'].exists())
                self.assertFalse(_source_wait_due(result,datetime.now()))
                self.assertEqual(json.loads(paths['STATUS_PATH'].read_text(encoding='utf-8'))['missing_dates'],['2026-10-15'])
                coverage.return_value=[]
                result=production.refresh(force_full=True)
                self.assertEqual(result['status'],'success')
                self.assertNotIn('missing_dates', result)
                self.assertEqual(result['date_to'],'2026-10-15')
                self.assertEqual(fetch.call_count,45)

    def test_new_pc_wait_does_not_create_empty_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.multiple(production, DB_PATH=root/'missing.sqlite', STATUS_PATH=root/'status.json'), \
                 patch.dict(os.environ,{'DDOKDDAK_PROD3_TRANSPORT':'dmz'}), \
                 patch.object(dmz,'missing_production_days',return_value=['2026-10-15']), \
                 patch.object(production,'_fetch') as fetch:
                result=production.refresh()
                self.assertEqual(result['status'],'waiting_source')
                self.assertNotIn('refreshed_at',result)
                self.assertFalse((root/'missing.sqlite').exists())
                fetch.assert_not_called()

    def test_bootstrap_resumes_only_incomplete_source_for_current_version(self):
        report={'app_version':APP_VERSION,'completed_at':'2026-10-15T08:00:00+09:00',
                'results':{'aps':{'status':'success'},'bom':{'status':'success'},
                           'production':{'status':'waiting_source'},'live':{'status':'waiting_wip'}}}
        self.assertEqual(_bootstrap_resume_source(report),'production')
        report['results']['production']['status']='success'
        self.assertEqual(_bootstrap_resume_source(report),'live')
        report['app_version']='3.1.4'
        self.assertEqual(_bootstrap_resume_source(report),'all')

    def test_six_hour_schedule_and_restart_obey_persisted_five_minute_wait(self):
        window=catchup.SchedulerCatchupTests._window()
        window.collection_schedule={'bom_minutes':0,'aps_minutes':0,'production_minutes':360,'live_minutes':0}
        now=datetime.now()
        status={'status':'waiting_source','retry_after':(now+timedelta(minutes=4)).astimezone().isoformat(),
                'daily_full_date':date.today().isoformat()}
        def read(path):
            if path.name=='version_collection_bootstrap.json': return {'completed_version':APP_VERSION}
            if 'production-performance' in str(path): return status
            return {'status':'success'}
        window._read_refresh_status=Mock(side_effect=read)
        window._status_refreshed_at=Mock(return_value=now)
        MainWindow._run_scheduled_collections(window)
        window._start_data_collection.assert_not_called()
        status['retry_after']=(now-timedelta(minutes=1)).astimezone().isoformat()
        window._collection_last_attempt={'production':now-timedelta(minutes=6)}
        MainWindow._run_scheduled_collections(window)
        window._start_data_collection.assert_called_once_with('live',scheduled=True)

    def test_header_does_not_claim_success_for_wait(self):
        text,_,ok=header_status({'production_status':{'status':'waiting_source'}},{},{})
        self.assertIn('서버 자료 대기',text)
        self.assertFalse(ok)


if __name__ == '__main__':
    unittest.main()
