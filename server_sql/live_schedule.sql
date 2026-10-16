BEGIN;
SET LOCAL lock_timeout='20s';
-- Freeze Korean business dates when the existing scheduler updates a live job.
CREATE OR REPLACE FUNCTION prod3_delivery.live_dates() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE today date:=(clock_timestamp() AT TIME ZONE 'Asia/Seoul')::date;
BEGIN
 IF EXISTS(SELECT 1 FROM prod3_delivery.policy WHERE dataset=NEW.dataset AND live) THEN
  NEW.request_params:=NEW.request_params || jsonb_build_object('date_to',to_char(today,'YYYY-MM-DD'),
    'date_from',to_char(today-CASE WHEN NEW.dataset='prod3_hydration_jobs' THEN 4 ELSE 0 END,'YYYY-MM-DD'));
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER prod3_delivery_business_date BEFORE UPDATE ON collector_hub.collection_job
FOR EACH ROW EXECUTE FUNCTION prod3_delivery.live_dates();
-- Keep the existing one-hour default and request the first coordinated cycle now.
UPDATE collector_hub.collection_job j
SET schedule_kind='cron',cron_expression='0 7-19 * * 1-6',next_run_at=clock_timestamp(),
    updated_at=clock_timestamp(),config_version=config_version+1
FROM prod3_delivery.policy p WHERE p.dataset=j.dataset AND p.live AND j.status<>'running';
COMMIT;
