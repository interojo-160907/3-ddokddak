"""Exercise publication on PostgreSQL, in isolated schemas rolled back afterward."""
import json
import os
from pathlib import Path
import unittest
import uuid


@unittest.skipUnless(os.getenv('DDOKDDAK_TEST_POSTGRES_DSN'), 'isolated PostgreSQL test DSN required')
class DeliverySqlTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.rows import dict_row
        self.c=psycopg.connect(os.environ['DDOKDDAK_TEST_POSTGRES_DSN'],row_factory=dict_row,
                             options='-c statement_timeout=20000 -c lock_timeout=2000')
        self.addCleanup(self.c.close)
        self.addCleanup(self.c.rollback)
        self.schema='delivery_test_'+uuid.uuid4().hex[:12]
        self.hub=self.schema+'_hub'
        self.c.execute(f'CREATE SCHEMA {self.hub}')
        self.c.execute(f'''CREATE TABLE {self.hub}.collection_job (
            id text PRIMARY KEY,dataset text,enabled boolean DEFAULT true,automatic boolean DEFAULT true,
            status text DEFAULT 'success',next_run_at timestamptz DEFAULT clock_timestamp()+interval '1 hour')''')
        self.c.execute(f'CREATE TABLE {self.hub}.job_run (id text,status text,finished_at timestamptz)')
        sql=(Path(__file__).resolve().parents[1]/'server_sql/delivery.sql').read_text('utf-8')
        sql=sql.replace('BEGIN;','',1).rsplit('COMMIT;',1)[0]
        sql=sql.replace('prod3_delivery',self.schema).replace('collector_hub',self.hub)
        self.c.execute(sql)
        self.c.execute(f"INSERT INTO {self.hub}.collection_job(id,dataset) SELECT dataset,dataset FROM {self.schema}.policy")

    def seed(self, minutes=9):
        s=self.schema
        self.c.execute(f"""INSERT INTO {s}.release VALUES('aps-fixture',1,'aps-fixture','today',
            clock_timestamp()-interval '45 minutes',clock_timestamp()-interval '46 minutes',
            clock_timestamp()-interval '45 minutes','[]')""")
        self.c.execute(f"""INSERT INTO {s}.source
            SELECT dataset,dataset,clock_timestamp()-(%s * interval '1 minute'),clock_timestamp()-interval '10 seconds',
            clock_timestamp(),jsonb_build_object('dataset',dataset,'snapshot_id',dataset,'run_id','run-'||dataset,
            'date_from',to_char(clock_timestamp() AT TIME ZONE 'Asia/Seoul','YYYY-MM-DD'),
            'date_to',to_char(clock_timestamp() AT TIME ZONE 'Asia/Seoul','YYYY-MM-DD'))
            FROM {s}.policy WHERE group_no=2""",(minutes,))

    def publish(self):
        self.c.execute(f'SELECT {self.schema}.publish_ready()')
        return self.c.execute(f'SELECT * FROM {self.schema}.release WHERE group_no=2').fetchall()

    def test_nine_minute_complete_group_publishes_once(self):
        self.seed()
        self.assertEqual(len(self.publish()),1)
        self.assertEqual(len(self.publish()),1)

    def test_missing_wrong_date_or_old_aps_inputs_never_publish(self):
        self.seed()
        self.c.execute(f"DELETE FROM {self.schema}.source WHERE dataset='inventory_q001'")
        self.assertEqual(self.publish(),[])
        state=self.c.execute(f'SELECT details FROM {self.schema}.state').fetchone()['details']
        self.assertIn('inventory_q001',state['missing'])

    def test_old_cycle_cannot_be_used_as_current(self):
        self.seed()
        self.c.execute(f"UPDATE {self.schema}.source SET started_at=clock_timestamp()-interval '50 minutes' WHERE dataset='inventory_q001'")
        self.assertEqual(self.publish(),[])

    def test_wrong_date_rejected(self):
        self.seed()
        self.c.execute(f"UPDATE {self.schema}.source SET descriptor=descriptor || '{{\"date_to\":\"2000-01-01\"}}'::jsonb WHERE dataset='inventory_q001'")
        self.assertEqual(self.publish(),[])

    def test_wide_group_reports_reason_and_queues_bounded_retry(self):
        self.seed(31)
        self.assertEqual(self.publish(),[])
        state=self.c.execute(f'SELECT details,retry_after FROM {self.schema}.state').fetchone()
        self.assertEqual(state['details']['reason'],'수집 범위 30분 초과')
        self.assertIsNotNone(state['retry_after'])
        before=self.c.execute(f'SELECT dataset,next_run_at FROM {self.hub}.collection_job ORDER BY dataset').fetchall()
        self.publish()
        after=self.c.execute(f'SELECT dataset,next_run_at FROM {self.hub}.collection_job ORDER BY dataset').fetchall()
        self.assertEqual(before,after)

    def test_pending_scheduled_jobs_do_not_trigger_duplicate_retry(self):
        self.seed(31)
        self.c.execute(f"UPDATE {self.hub}.collection_job SET next_run_at=clock_timestamp()-interval '1 second' WHERE dataset='inventory_q001'")
        self.assertEqual(self.publish(),[])
        self.assertIsNone(self.c.execute(f'SELECT retry_after FROM {self.schema}.state').fetchone()['retry_after'])

    def test_extraction_only_changes_do_not_change_fingerprint(self):
        def fingerprint(rows):
            return self.c.execute(f'SELECT {self.schema}.arrange_fingerprint(%s::jsonb) AS hash',(json.dumps(rows),)).fetchone()['hash']
        a=[{'id':1,'qty':12,'extracted_at':'08:00'},{'id':2,'qty':3}]
        b=[{'id':2,'qty':3},{'id':1,'qty':12,'extracted_at':'09:00'}]
        self.assertEqual(fingerprint(a),fingerprint(b))
        b[1]['qty']=13
        self.assertNotEqual(fingerprint(a),fingerprint(b))
        self.assertNotEqual(fingerprint(a),fingerprint(a+a))

    def test_aps_package_stays_stable_when_arrange_extraction_clock_changes(self):
        s=self.schema
        for row in self.c.execute(f'SELECT dataset FROM {s}.policy WHERE group_no=1').fetchall():
            dataset=row['dataset']
            rows=[{'tbl':'RUN','run_key':'today','run_state':'D','last_upd':'2026-10-20 07:00:00'}] if dataset=='aps_outbound_status' else [{'id':1,'qty':12,'extracted_at':'08:00'}]
            version='2026-10-20 07:54:32' if dataset.startswith('prod3_aps_wip_') else 'today'
            descriptor={'dataset':dataset,'snapshot_id':dataset,'source_version':version}
            self.c.execute(f'INSERT INTO {s}.payload VALUES(%s,%s::jsonb,%s)',(dataset,json.dumps(rows),'hash'))
            self.c.execute(f'INSERT INTO {s}.source VALUES(%s,%s,now(),now(),now(),%s::jsonb)',(dataset,dataset,json.dumps(descriptor)))
        self.publish()
        before=self.c.execute(f'SELECT version FROM {s}.release WHERE group_no=1').fetchall()
        self.assertEqual(len(before),1)
        self.c.execute(f'INSERT INTO {s}.payload VALUES(%s,%s::jsonb,%s)',('arrange-new',json.dumps([{'id':1,'qty':12,'extracted_at':'09:00'}]),'different'))
        self.c.execute(f"UPDATE {s}.source SET snapshot_id='arrange-new' WHERE dataset='aps_arrange'")
        self.publish()
        self.assertEqual(self.c.execute(f'SELECT version FROM {s}.release WHERE group_no=1').fetchall(),before)
        self.c.execute(f'UPDATE {s}.payload SET rows=%s::jsonb WHERE snapshot_id=%s',(json.dumps([{'id':1,'qty':13,'extracted_at':'09:00'}]),'arrange-new'))
        self.publish()
        self.assertEqual(len(self.c.execute(f'SELECT version FROM {s}.release WHERE group_no=1').fetchall()),2)


if __name__=='__main__':unittest.main()
