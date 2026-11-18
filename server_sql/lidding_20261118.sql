-- Reference of deployed 2026-11-18 schema/functions. Existing server already configured.
-- Do not rerun automatically from clients. Existing delivery triggers and scheduler are retained.
-- Control is deliberately disabled on a fresh server until policies/jobs are reviewed and enabled.
CREATE SCHEMA IF NOT EXISTS prod3_lidding;
CREATE TABLE IF NOT EXISTS prod3_lidding.control (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 enabled boolean NOT NULL DEFAULT false,
 input_signature text NOT NULL DEFAULT '', updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO prod3_lidding.control(singleton) VALUES(true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS prod3_lidding.target (
 material_code text PRIMARY KEY, dataset text UNIQUE NOT NULL,
 active boolean NOT NULL DEFAULT true, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE VIEW prod3_lidding.bom_links AS
 WITH eligible AS (
 SELECT DISTINCT upper(r->>'sale_cd') pcode
 FROM prod3_delivery.source s JOIN prod3_delivery.payload p USING(snapshot_id), jsonb_array_elements(p.rows) r
 WHERE s.dataset='aps_arrange' AND r->>'fac_cd'='04' AND r->>'oper_id'='55'
 UNION
 SELECT DISTINCT upper(r->>'sale_cd') FROM prod3_delivery.source s JOIN prod3_delivery.payload p USING(snapshot_id),jsonb_array_elements(p.rows) r
 WHERE s.dataset='aps_backward_plan' AND r->>'oper_cd'='55' AND (r->>'res_site_id'='S관(3공장)' OR (r->>'fac_basis'='A' AND r->>'eff_fac_cd'='04'))
 )
 SELECT DISTINCT upper(r->>'parent_cd') pcode,r->>'parent_nm' product_name,
 r->>'child_cd' material_code,r->>'child_nm' material_name,r->>'child_spec' specification,(r->>'qty')::numeric usage_qty
 FROM prod3_delivery.source s JOIN prod3_delivery.payload p USING(snapshot_id),jsonb_array_elements(p.rows) r
 WHERE s.dataset='bom_explosion' AND r->>'parent_gu'='P' AND r->>'use_yn'='Y'
 AND r->>'child_cd' LIKE 'BS%' AND (r->>'child_nm' LIKE '%리드지%' OR lower(r->>'child_nm') LIKE '%lidding%')
 AND upper(r->>'parent_cd') IN(SELECT pcode FROM eligible);

CREATE OR REPLACE FUNCTION prod3_delivery.capture_run(target_run text)
 RETURNS void
 LANGUAGE plpgsql
AS $function$
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
 IF r.dataset LIKE 'prod3_lidding_%' THEN
  IF coalesce(r.payload#>>'{filters_applied,wh_cd}','') IS DISTINCT FROM coalesce(r.request_params->>'wh_cd','') OR
   r.payload#>>'{filters_applied,itm_cd}' IS DISTINCT FROM r.request_params->>'itm_cd' THEN RETURN; END IF;
  IF EXISTS(SELECT 1 FROM jsonb_array_elements(r.payload->'rows') x
   WHERE ((r.request_params ? 'wh_cd') AND x->>'wh_cd' IS DISTINCT FROM r.request_params->>'wh_cd') OR coalesce(x->>'wh_cd','')='' OR
    (r.dataset NOT LIKE 'prod3_lidding_stock%' AND x->>'itm_cd' IS DISTINCT FROM r.request_params->>'itm_cd')) THEN RETURN; END IF;
 END IF;
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
END $function$
;

CREATE OR REPLACE FUNCTION prod3_delivery.live_dates()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
DECLARE today date:=(clock_timestamp() AT TIME ZONE 'Asia/Seoul')::date;
BEGIN
 IF EXISTS(SELECT 1 FROM prod3_delivery.policy WHERE dataset=NEW.dataset AND live) THEN
  NEW.request_params:=NEW.request_params || jsonb_build_object('date_to',to_char(today,'YYYY-MM-DD'),
   'date_from',to_char(today-CASE WHEN NEW.dataset LIKE 'prod3_lidding_issue_%' THEN 29 WHEN NEW.dataset='prod3_hydration_jobs' THEN 4 ELSE 0 END,'YYYY-MM-DD'));
  IF NEW.dataset LIKE 'prod3_lidding_stock%' THEN NEW.request_params:=NEW.request_params || jsonb_build_object('std_dt',to_char(today,'YYYY-MM-DD')); END IF;
 END IF;
 RETURN NEW;
END $function$
;

CREATE OR REPLACE FUNCTION prod3_delivery.publish_ready()
 RETURNS void
 LANGUAGE plpgsql
AS $function$
DECLARE a prod3_delivery.release%ROWTYPE; previous text; signature text; members jsonb;
 apskey text; runrow jsonb; n integer; lo timestamptz; hi timestamptz;
 required_after timestamptz; missing jsonb; reason text; total integer; expected_total integer; expected_live integer;
BEGIN
 PERFORM prod3_lidding.sync_jobs();
 SELECT count(*),count(*) FILTER(WHERE live) INTO expected_total,expected_live FROM prod3_delivery.policy WHERE group_no=2;
 IF NOT EXISTS(SELECT 1 FROM prod3_delivery.policy WHERE dataset='prod3_lidding_stock_all' AND live AND group_no=2) OR expected_total<>(11+(SELECT count(*) FROM prod3_lidding.target WHERE active)) OR expected_live<>(8+(SELECT count(*) FROM prod3_lidding.target WHERE active)) OR NOT EXISTS(SELECT 1 FROM prod3_delivery.policy WHERE dataset='prod3_lidding_stock_all' AND live AND group_no=2) THEN
  RAISE EXCEPTION '리드지 포함 패키지 필수 정책 확인 필요';
 END IF;
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
    AND expected_total=(SELECT jsonb_array_length(rel.members) FROM prod3_delivery.release rel WHERE group_no=2 ORDER BY published_at DESC LIMIT 1)
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
 AND prod3_lidding.valid_period(s.dataset,s.descriptor)
 AND s.observed_at>=s.started_at-interval '60 seconds' AND s.observed_at<=s.received_at+interval '60 seconds';
 SELECT count(*) INTO total FROM prod3_delivery.source s JOIN prod3_delivery.policy p USING(dataset) WHERE p.group_no=2;
 reason:=CASE WHEN EXISTS(
     SELECT required FROM unnest(ARRAY['bom_explosion','product_names','prod3_product_saline','inventory_q001','inventory_r001','inventory_g007','inventory_g005','inventory_p002','prod3_production_today','prod3_hydration_jobs']) required
     WHERE NOT EXISTS(SELECT 1 FROM prod3_delivery.policy WHERE dataset=required AND group_no=2)) THEN '기존 필수 정책 누락'
   WHEN total<>expected_total THEN '필수 자료 누락'
   WHEN n<>expected_live THEN '같은 회차 생산 자료 수집 대기'
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
   AND prod3_lidding.valid_period(s.dataset,s.descriptor)
   AND s.observed_at BETWEEN s.started_at-interval '60 seconds' AND s.received_at+interval '60 seconds',false));
  UPDATE prod3_delivery.state SET checked_at=clock_timestamp(),message=reason || ' · 이전 완성본 유지',
   details=jsonb_build_object('reason',reason,'ready',n,'required',expected_live,'missing',missing,
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
  details=jsonb_build_object('ready',n,'required',expected_live,'aps_key',a.aps_key,
    'window_seconds',extract(epoch from hi-lo),'max_window_seconds',1800),retry_after=NULL WHERE singleton;
 DELETE FROM prod3_delivery.release WHERE group_no=2 AND version NOT IN(SELECT version FROM prod3_delivery.release WHERE group_no=2 ORDER BY published_at DESC LIMIT 3);
 DELETE FROM prod3_delivery.release WHERE group_no=1 AND version<>a.version AND version NOT IN(SELECT aps_version FROM prod3_delivery.release WHERE group_no=2);
 DELETE FROM prod3_delivery.payload WHERE snapshot_id NOT IN(SELECT snapshot_id FROM prod3_delivery.source)
 AND snapshot_id NOT IN(SELECT m->>'snapshot_id' FROM prod3_delivery.release rel,jsonb_array_elements(rel.members)m);
END $function$
;

CREATE OR REPLACE FUNCTION prod3_lidding.sync_jobs()
 RETURNS void
 LANGUAGE plpgsql
AS $function$
DECLARE sig text; template collector_hub.collection_job%ROWTYPE; job collector_hub.collection_job%ROWTYPE;
 code text; ds text; jdoc jsonb; target_codes text[];
BEGIN
 IF NOT EXISTS(SELECT 1 FROM prod3_lidding.control WHERE enabled) THEN RETURN; END IF;
 SELECT string_agg(dataset||':'||snapshot_id,',' ORDER BY dataset) INTO sig FROM prod3_delivery.source
 WHERE dataset IN('bom_explosion','aps_arrange','aps_backward_plan');
 IF sig=(SELECT input_signature FROM prod3_lidding.control) THEN RETURN; END IF;
 SELECT array_agg(DISTINCT material_code ORDER BY material_code) INTO target_codes FROM prod3_lidding.bom_links;
 IF coalesce(array_length(target_codes,1),0)=0 THEN RAISE EXCEPTION 'S관 리드지 BOM 대상 없음'; END IF;
 SELECT * INTO STRICT template FROM collector_hub.collection_job WHERE dataset='inventory_g007';
 FOREACH code IN ARRAY array_prepend('__stock_all__',target_codes) LOOP
  ds:=CASE WHEN code='__stock_all__' THEN 'prod3_lidding_stock_all' ELSE 'prod3_lidding_issue_'||lower(code) END;
  SELECT * INTO job FROM collector_hub.collection_job WHERE dataset=ds;
  IF NOT FOUND THEN
   jdoc:=to_jsonb(template)||jsonb_build_object('id',gen_random_uuid()::text,'dataset',ds,
    'name',CASE WHEN code='__stock_all__' THEN '생산3팀 리드지 전 창고 현재고' ELSE '생산3팀 리드지 30일 불출 '||code END,
    'description','생산 현황 ② 필수 자료 · 최신 30일 · 검사접착 55 기준 · 원본/스냅샷 완성 후 게시',
    'endpoint',CASE WHEN code LIKE '__stock%' THEN '/api/warehouse-item-stock' ELSE '/api/item-inout-detail' END,
    'request_params',CASE WHEN code='__stock_all__' THEN jsonb_build_object('itm_cd','BS','limit',0) ELSE jsonb_build_object('wh_cd','S100','itm_cd',code,'limit',0) END,
    'target_table',ds,'current_view','scm.'||ds||'_current','field_mappings','[]'::jsonb,'primary_keys','[]'::jsonb,
    'enabled',true,'automatic',true,'schedule_kind','cron','cron_expression','0 7-19 * * 1-6',
    'source_version_path','query_date','min_rows',CASE WHEN code LIKE '__stock%' THEN 1 ELSE 0 END,
    'status','idle','status_detail','','config_version',1,'last_source_version','','last_snapshot_hash','',
    'last_started_at',NULL,'last_success_at',NULL,'next_run_at',clock_timestamp(),
    'created_at',clock_timestamp(),'updated_at',clock_timestamp(),'change_state','{}'::jsonb);
   INSERT INTO collector_hub.collection_job SELECT (jsonb_populate_record(NULL::collector_hub.collection_job,jdoc)).*;
   INSERT INTO collector_hub.retention_policy(id,job_id,raw_days,snapshot_days,daily_snapshot_days,monthly_snapshot_days,
    run_log_days,failed_payload_days,keep_last,archive_before_delete,cleanup_time,updated_at)
   VALUES(gen_random_uuid()::text,jdoc->>'id',7,3,3,3,30,7,3,false,'02:30',now());
  ELSIF NOT job.enabled THEN
   UPDATE collector_hub.collection_job SET enabled=true,automatic=true,next_run_at=now() WHERE id=job.id;
  END IF;
  INSERT INTO prod3_delivery.policy VALUES(ds,2,true) ON CONFLICT(dataset) DO UPDATE SET group_no=2,live=true;
  IF code NOT LIKE '__stock%' THEN
   INSERT INTO prod3_lidding.target VALUES(code,ds,true,now()) ON CONFLICT(material_code) DO UPDATE SET active=true,updated_at=now();
  END IF;
 END LOOP;
 UPDATE prod3_lidding.target SET active=false,updated_at=now() WHERE NOT(material_code=ANY(target_codes));
 UPDATE collector_hub.collection_job SET enabled=false,automatic=false WHERE dataset IN(SELECT dataset FROM prod3_lidding.target WHERE NOT active);
 DELETE FROM prod3_delivery.policy WHERE dataset IN(SELECT dataset FROM prod3_lidding.target WHERE NOT active);
 UPDATE collector_hub.collection_job SET enabled=false,automatic=false WHERE dataset IN('prod3_lidding_stock','prod3_lidding_stock_l300');
 DELETE FROM prod3_delivery.policy WHERE dataset IN('prod3_lidding_stock','prod3_lidding_stock_l300');
 UPDATE prod3_lidding.control SET input_signature=sig,updated_at=now();
END $function$
;

CREATE OR REPLACE FUNCTION prod3_lidding.valid_period(ds text, descriptor jsonb)
 RETURNS boolean
 LANGUAGE sql
 STABLE
AS $function$
 SELECT CASE
 WHEN ds LIKE 'prod3_lidding_stock%' THEN descriptor#>>'{metadata,filters_applied,std_dt}'=to_char(current_timestamp AT TIME ZONE 'Asia/Seoul','YYYY-MM-DD')
 WHEN ds LIKE 'prod3_lidding_issue_%' THEN descriptor->>'date_from'=to_char((current_timestamp AT TIME ZONE 'Asia/Seoul')::date-29,'YYYY-MM-DD')
   AND descriptor#>>'{metadata,filters_applied,itm_cd}'=upper(replace(ds,'prod3_lidding_issue_',''))
 ELSE ds='prod3_hydration_jobs' OR descriptor->>'date_from'=descriptor->>'date_to' END;
$function$
;