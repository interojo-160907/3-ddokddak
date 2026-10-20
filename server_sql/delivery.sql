-- Existing collector publishes as before. Deferred trigger builds read-only delivery groups.
BEGIN;
SET LOCAL lock_timeout='2s';
CREATE SCHEMA IF NOT EXISTS prod3_delivery;
CREATE TABLE IF NOT EXISTS prod3_delivery.policy (
 dataset text PRIMARY KEY, group_no integer NOT NULL, live boolean NOT NULL DEFAULT false);
INSERT INTO prod3_delivery.policy VALUES
 ('aps_plan',1,false),('aps_backward_plan',1,false),('aps_outbound_status',1,false),
 ('aps_arrange',1,false),('prod3_aps_wip_q001',1,false),('prod3_aps_wip_r001',1,false),
 ('prod3_aps_wip_g005',1,false),('prod3_aps_wip_g007',1,false),('prod3_aps_wip_p002',1,false),
 ('bom_explosion',2,false),('product_names',2,false),('prod3_product_saline',2,false),
 ('inventory_q001',2,true),('inventory_r001',2,true),('inventory_g007',2,true),
 ('inventory_g005',2,true),('inventory_p002',2,true),
 ('prod3_production_today',2,true),('prod3_hydration_jobs',2,true)
ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS prod3_delivery.payload (
 snapshot_id text PRIMARY KEY, rows jsonb NOT NULL, content_hash text NOT NULL);
CREATE TABLE IF NOT EXISTS prod3_delivery.source (
 dataset text PRIMARY KEY, snapshot_id text NOT NULL, started_at timestamptz NOT NULL,
 observed_at timestamptz NOT NULL, received_at timestamptz NOT NULL, descriptor jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS prod3_delivery.release (
 version text PRIMARY KEY, group_no integer NOT NULL, aps_version text NOT NULL,
 aps_key text NOT NULL, published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 started_at timestamptz NOT NULL, received_at timestamptz NOT NULL, members jsonb NOT NULL);
CREATE INDEX IF NOT EXISTS prod3_delivery_release_recent ON prod3_delivery.release(group_no,published_at DESC);
CREATE TABLE IF NOT EXISTS prod3_delivery.state (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton), checked_at timestamptz,
 message text NOT NULL DEFAULT '자료 준비 중', last_error text NOT NULL DEFAULT '',
 details jsonb NOT NULL DEFAULT '{}', retry_after timestamptz);
INSERT INTO prod3_delivery.state(singleton) VALUES(true) ON CONFLICT DO NOTHING;

-- Ignore extraction timestamps, retain every business field and duplicate row.
CREATE OR REPLACE FUNCTION prod3_delivery.arrange_fingerprint(rows_json jsonb) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
 SELECT md5(coalesce(jsonb_agg(v ORDER BY v::text)::text,'[]'))
 FROM (SELECT value-'extracted_at' AS v FROM jsonb_array_elements(rows_json)) t;
$$;

CREATE OR REPLACE FUNCTION prod3_delivery.publish_ready() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE a prod3_delivery.release%ROWTYPE; previous text; signature text; members jsonb;
 apskey text; runrow jsonb; n integer; lo timestamptz; hi timestamptz;
 required_after timestamptz; missing jsonb; reason text; total integer;
BEGIN
 SELECT elem INTO runrow FROM prod3_delivery.source s,
 jsonb_array_elements((SELECT rows FROM prod3_delivery.payload WHERE snapshot_id=s.snapshot_id)) elem
 WHERE s.dataset='aps_outbound_status' AND coalesce(elem->>'tbl',elem->>'tbl_nm')='RUN' LIMIT 1;
 apskey:=runrow->>'run_key';
 IF runrow->>'run_state'='D' AND apskey IS NOT NULL AND
 (SELECT count(*) FROM prod3_delivery.source s JOIN prod3_delivery.policy p USING(dataset) WHERE p.group_no=1)=9 AND
 NOT EXISTS(SELECT 1 FROM prod3_delivery.source WHERE dataset IN('aps_plan','aps_backward_plan')
   AND descriptor->>'source_version' IS DISTINCT FROM apskey) AND
 NOT EXISTS(SELECT 1 FROM prod3_delivery.source WHERE dataset LIKE 'prod3_aps_wip_%'
   AND (descriptor->>'source_version')::timestamp < (runrow->>'last_upd')::timestamp)
 THEN
  SELECT jsonb_agg(descriptor ORDER BY s.dataset),md5(apskey || ':' || string_agg(
         CASE WHEN s.dataset='aps_arrange' THEN prod3_delivery.arrange_fingerprint(
           (SELECT rows FROM prod3_delivery.payload WHERE snapshot_id=s.snapshot_id))
         ELSE s.snapshot_id END,',' ORDER BY s.dataset) FILTER (WHERE s.dataset<>'aps_outbound_status')),
         min(s.started_at),max(s.received_at) INTO members,signature,lo,hi
  FROM prod3_delivery.source s JOIN prod3_delivery.policy p USING(dataset) WHERE p.group_no=1;
  signature:='aps-'||signature;
  SELECT version INTO previous FROM prod3_delivery.release WHERE group_no=1 ORDER BY published_at DESC LIMIT 1;
  IF previous IS DISTINCT FROM signature THEN
   INSERT INTO prod3_delivery.release VALUES(signature,1,signature,apskey,clock_timestamp(),lo,hi,members) ON CONFLICT DO NOTHING;
   -- One request for the whole live group; leave in-flight collectors alone.
   UPDATE collector_hub.collection_job SET next_run_at=clock_timestamp() WHERE id IN (
    SELECT j.id FROM collector_hub.collection_job j JOIN prod3_delivery.policy p ON j.dataset=p.dataset
    WHERE p.live AND j.enabled AND j.automatic AND j.status<>'running' FOR UPDATE OF j SKIP LOCKED);
  END IF;
 END IF;
 SELECT * INTO a FROM prod3_delivery.release WHERE group_no=1 ORDER BY published_at DESC LIMIT 1;
 IF a.version IS NULL THEN
  UPDATE prod3_delivery.state SET checked_at=clock_timestamp(),message='APS 자료 준비 중' WHERE singleton; RETURN;
 END IF;
 required_after:=coalesce((SELECT received_at FROM prod3_delivery.release WHERE group_no=2 AND aps_version=a.version ORDER BY published_at DESC LIMIT 1),'-infinity'::timestamptz);
 IF required_after>clock_timestamp()-interval '60 minutes'
    AND NOT EXISTS(SELECT 1 FROM prod3_delivery.source s JOIN prod3_delivery.policy p USING(dataset)
                   WHERE p.live AND s.started_at>required_after) THEN
  UPDATE prod3_delivery.state SET checked_at=clock_timestamp(),message='두 그룹 준비 완료',last_error='' WHERE singleton;
  RETURN;
 END IF;
 SELECT count(*),min(s.started_at),max(s.received_at) INTO n,lo,hi
 FROM prod3_delivery.source s JOIN prod3_delivery.policy p USING(dataset)
 WHERE p.live AND s.started_at>=a.published_at
 AND s.started_at>required_after
 AND s.descriptor->>'date_to'=to_char(clock_timestamp() AT TIME ZONE 'Asia/Seoul','YYYY-MM-DD')
 AND (s.dataset='prod3_hydration_jobs' OR s.descriptor->>'date_from'=s.descriptor->>'date_to')
 AND s.observed_at>=s.started_at-interval '60 seconds' AND s.observed_at<=s.received_at+interval '60 seconds';
 SELECT count(*) INTO total FROM prod3_delivery.source s JOIN prod3_delivery.policy p USING(dataset) WHERE p.group_no=2;
 reason:=CASE WHEN total<>10 THEN '필수 자료 누락'
   WHEN n<>7 THEN '같은 회차 생산 자료 수집 대기'
   WHEN hi>clock_timestamp()+interval '60 seconds' OR hi<lo THEN '자료 시각 확인 필요'
   WHEN hi-lo>interval '30 minutes' THEN '수집 범위 30분 초과'
   WHEN lo<clock_timestamp()-interval '65 minutes' OR hi<clock_timestamp()-interval '30 minutes' THEN '생산 자료 유효시간 초과'
   ELSE NULL END;
 IF reason IS NOT NULL THEN
  SELECT coalesce(jsonb_agg(p.dataset ORDER BY p.dataset),'[]') INTO missing
  FROM prod3_delivery.policy p LEFT JOIN prod3_delivery.source s USING(dataset)
  WHERE p.live AND (s.dataset IS NULL OR NOT coalesce(
   s.started_at>=a.published_at AND s.started_at>required_after
   AND s.descriptor->>'date_to'=to_char(clock_timestamp() AT TIME ZONE 'Asia/Seoul','YYYY-MM-DD')
   AND (s.dataset='prod3_hydration_jobs' OR s.descriptor->>'date_from'=s.descriptor->>'date_to')
   AND s.observed_at BETWEEN s.started_at-interval '60 seconds' AND s.received_at+interval '60 seconds',false));
  UPDATE prod3_delivery.state SET checked_at=clock_timestamp(),message=reason || ' · 이전 완성본 유지',
   details=jsonb_build_object('reason',reason,'ready',n,'required',7,'missing',missing,
     'aps_key',a.aps_key,'window_seconds',extract(epoch from hi-lo),'max_window_seconds',1800)
   WHERE singleton;
  -- Retry a failed/expired collection as one group, not each API in a tight loop.
  -- A normal idle period after a successful release keeps the hourly schedule.
  IF (NOT EXISTS(SELECT 1 FROM prod3_delivery.release WHERE group_no=2 AND aps_version=a.version)
      OR EXISTS(SELECT 1 FROM prod3_delivery.source s JOIN prod3_delivery.policy p USING(dataset)
                WHERE p.live AND s.started_at>required_after))
    AND NOT EXISTS(SELECT 1 FROM collector_hub.collection_job j JOIN prod3_delivery.policy p USING(dataset)
                    WHERE p.live AND j.enabled AND j.automatic
                    AND (j.status='running' OR j.next_run_at<=clock_timestamp()))
    AND coalesce((SELECT retry_after FROM prod3_delivery.state WHERE singleton),'-infinity'::timestamptz)<=clock_timestamp()
    AND clock_timestamp()>=a.published_at+interval '10 minutes' THEN
   UPDATE collector_hub.collection_job SET next_run_at=clock_timestamp() WHERE id IN (
    SELECT j.id FROM collector_hub.collection_job j JOIN prod3_delivery.policy p USING(dataset)
    WHERE p.live AND j.enabled AND j.automatic AND j.status<>'running'
    AND j.next_run_at>clock_timestamp() FOR UPDATE OF j SKIP LOCKED);
   UPDATE prod3_delivery.state SET retry_after=clock_timestamp()+interval '10 minutes' WHERE singleton;
  END IF;
  RETURN;
 END IF;
 SELECT jsonb_agg(descriptor ORDER BY s.dataset),md5(string_agg(descriptor->>'run_id',',' ORDER BY s.dataset))
 INTO members,signature FROM prod3_delivery.source s JOIN prod3_delivery.policy p USING(dataset) WHERE p.group_no=2;
 signature:='live-'||md5(a.version||signature);
 INSERT INTO prod3_delivery.release VALUES(signature,2,a.version,a.aps_key,clock_timestamp(),lo,hi,members) ON CONFLICT DO NOTHING;
 UPDATE prod3_delivery.state SET checked_at=clock_timestamp(),message='두 그룹 준비 완료',last_error='',
  details=jsonb_build_object('ready',7,'required',7,'aps_key',a.aps_key,
    'window_seconds',extract(epoch from hi-lo),'max_window_seconds',1800),retry_after=NULL WHERE singleton;
 DELETE FROM prod3_delivery.release WHERE group_no=2 AND version NOT IN(SELECT version FROM prod3_delivery.release WHERE group_no=2 ORDER BY published_at DESC LIMIT 3);
 DELETE FROM prod3_delivery.release WHERE group_no=1 AND version<>a.version AND version NOT IN(SELECT aps_version FROM prod3_delivery.release WHERE group_no=2);
 DELETE FROM prod3_delivery.payload WHERE snapshot_id NOT IN(SELECT snapshot_id FROM prod3_delivery.source)
 AND snapshot_id NOT IN(SELECT m->>'snapshot_id' FROM prod3_delivery.release rel,jsonb_array_elements(rel.members)m);
END $$;

CREATE OR REPLACE FUNCTION prod3_delivery.capture_run(target_run text) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE r record; metadata jsonb; observed timestamptz; actual integer; clocktext text;
BEGIN
 SELECT j.dataset,j.id job_id,j.endpoint,j.request_params,j.field_mappings,
        jr.id run_id,jr.started_at,s.id snapshot_id,s.row_count,s.content_hash,s.source_version,s.published_at,
        raw.collected_at,raw.payload
 INTO r FROM collector_hub.job_run jr JOIN collector_hub.collection_job j ON j.id=jr.job_id
 JOIN prod3_delivery.policy pol ON pol.dataset=j.dataset
 JOIN collector_hub.snapshot_alias al ON al.job_id=j.id JOIN collector_hub.snapshot s ON s.id=al.snapshot_id
 JOIN collector_hub.raw_payload raw ON raw.run_id=jr.id
 WHERE jr.id=target_run AND jr.status IN('success','unchanged') AND s.status='published'
 ORDER BY raw.collected_at DESC LIMIT 1;
 IF NOT FOUND THEN RETURN; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(r.field_mappings) m WHERE m->>'type' NOT IN ('json','string','number','integer','boolean') OR m->>'source' IS DISTINCT FROM m->>'target') OR jsonb_typeof(r.payload->'rows')<>'array' OR
 lower(coalesce(r.payload->>'truncated','false')) IN('true','1','yes') THEN RETURN; END IF;
 actual:=jsonb_array_length(r.payload->'rows');
 IF actual<>r.row_count OR actual<>coalesce((r.payload->>'total_count')::integer,actual) THEN RETURN; END IF;
 PERFORM pg_advisory_xact_lock(323,2);
 IF EXISTS(SELECT 1 FROM prod3_delivery.source WHERE dataset=r.dataset AND received_at>r.collected_at) THEN RETURN; END IF;
 metadata:=r.payload-'rows'-'orders';
 BEGIN
  clocktext:=coalesce(r.payload#>>'{source,extracted_at}',r.payload->>'query_date');
  IF clocktext ~ '(Z|[+-][0-9]{2}:[0-9]{2})$' THEN observed:=clocktext::timestamptz;
  ELSE observed:=clocktext::timestamp AT TIME ZONE 'Asia/Seoul'; END IF;
 EXCEPTION WHEN others THEN observed:=NULL; END;
 IF observed IS NULL AND EXISTS(SELECT 1 FROM prod3_delivery.policy WHERE dataset=r.dataset AND live) THEN RETURN; END IF;
 observed:=coalesce(observed,r.collected_at);
 INSERT INTO prod3_delivery.payload VALUES(r.snapshot_id,r.payload->'rows',r.content_hash) ON CONFLICT DO NOTHING;
 INSERT INTO prod3_delivery.source VALUES(r.dataset,r.snapshot_id,r.started_at,observed,r.collected_at,
 jsonb_build_object('dataset',r.dataset,'job_id',r.job_id,'endpoint',r.endpoint,'request_params',r.request_params,
 'field_mappings',r.field_mappings,'snapshot_id',r.snapshot_id,'run_id',r.run_id,'row_count',actual,'content_hash',r.content_hash,
 'source_version',r.source_version,'published_at',r.published_at,'last_success_at',r.published_at,
 'date_from',coalesce(metadata->>'date_from',''),'date_to',coalesce(metadata->>'date_to',''),
 'observed_at',observed,'started_at',r.started_at,'received_at',r.collected_at,'metadata',metadata,'delivery',true))
 ON CONFLICT(dataset) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id,started_at=EXCLUDED.started_at,
 observed_at=EXCLUDED.observed_at,received_at=EXCLUDED.received_at,descriptor=EXCLUDED.descriptor;
 PERFORM prod3_delivery.publish_ready();
END $$;

CREATE OR REPLACE FUNCTION prod3_delivery.on_finished_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.status IN('success','unchanged') AND NEW.finished_at IS NOT NULL THEN
  BEGIN PERFORM prod3_delivery.capture_run(NEW.id);
  EXCEPTION WHEN others THEN
   RAISE WARNING 'production3 mirror retained previous release: %',SQLSTATE;
   UPDATE prod3_delivery.state SET checked_at=clock_timestamp(),last_error=SQLSTATE,message='미러링 확인 필요 · 이전 자료 유지' WHERE singleton;
  END;
 END IF;
 RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER prod3_delivery_finished AFTER INSERT OR UPDATE ON collector_hub.job_run
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION prod3_delivery.on_finished_run();
-- Match the existing client read permissions. No new write permission is granted.
DO $$ DECLARE r record; BEGIN
 FOR r IN SELECT DISTINCT grantee FROM information_schema.role_table_grants
 WHERE table_schema='scm_client' AND table_name='snapshot_scope' AND privilege_type='SELECT' AND grantee<>current_user LOOP
  IF r.grantee='PUBLIC' THEN
   GRANT USAGE ON SCHEMA prod3_delivery TO PUBLIC;
   GRANT SELECT ON ALL TABLES IN SCHEMA prod3_delivery TO PUBLIC;
  ELSE
   EXECUTE format('GRANT USAGE ON SCHEMA prod3_delivery TO %I',r.grantee);
   EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA prod3_delivery TO %I',r.grantee);
  END IF;
 END LOOP;
END $$;
COMMIT;
