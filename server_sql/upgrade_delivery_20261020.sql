-- Apply with the matching client (30-minute observation policy).
-- Existing collectors/triggers and immutable published payloads are retained.
BEGIN;
SET LOCAL lock_timeout='2s';
SET LOCAL statement_timeout='30s';
SELECT pg_advisory_xact_lock(323,2);
ALTER TABLE prod3_delivery.state ADD COLUMN IF NOT EXISTS details jsonb NOT NULL DEFAULT '{}';
ALTER TABLE prod3_delivery.state ADD COLUMN IF NOT EXISTS retry_after timestamptz;
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
COMMIT;
