-- DB 관리자 실행용. 비밀번호는 포함하지 않으며 로그인 활성화는 별도 보안 경로로 수행.
BEGIN;
CREATE ROLE production3_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 32;
GRANT CONNECT ON DATABASE sangho_db TO production3_reader;
GRANT USAGE ON SCHEMA collector_hub, scm_client TO production3_reader;
GRANT SELECT ON collector_hub.collection_job, collector_hub.snapshot_alias,
    collector_hub.snapshot, collector_hub.snapshot_record, collector_hub.raw_payload,
    scm_client.snapshot_scope TO production3_reader;
ALTER ROLE production3_reader SET default_transaction_read_only = on;
COMMIT;
