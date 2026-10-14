"""Read-only deployment preflight. Never calls source APIs or changes DMZ data.

Run with a Python environment containing psycopg and DATABASE_URL in its
environment. Output contains dataset status and counts, never credentials.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta


ENDPOINTS = (
    '/api/aps-outbound-status', '/api/aps-backward-plan', '/api/aps-plan',
    '/api/aps-arrange', '/api/product-names', '/api/bom-explosion',
    '/api/product-saline', '/api/production-performance', '/api/aps-wip',
    '/api/inventory-ledger-product', '/api/hydration-job-list',
    '/api/item-list-bulk', '/api/prod-request-domestic',
)


def audit(connection):
    now = connection.execute('SELECT current_timestamp AS checked_at').fetchone()['checked_at']
    jobs = connection.execute('''
        SELECT j.dataset,j.endpoint,j.request_params,j.status,j.enabled,
               j.last_started_at,j.last_success_at,j.max_runtime_seconds,
               s.row_count,s.source_version,s.published_at,
               sc.date_from,sc.date_to,sc.truncated
        FROM collector_hub.collection_job j
        LEFT JOIN collector_hub.snapshot_alias a ON a.job_id=j.id
        LEFT JOIN collector_hub.snapshot s ON s.id=a.snapshot_id
        LEFT JOIN scm_client.snapshot_scope sc ON sc.snapshot_id=s.id
        WHERE j.endpoint=ANY(%s) ORDER BY j.endpoint,j.dataset
    ''', (list(ENDPOINTS),)).fetchall()
    missing = [ep for ep in ENDPOINTS
               if not any(j['endpoint'] == ep and j['enabled'] and
                          j['published_at'] and not j['truncated'] for j in jobs)]
    stuck = [j['dataset'] for j in jobs if j['status'] == 'running'
             and j['last_started_at'] and now - j['last_started_at'] >
             timedelta(seconds=max(j['max_runtime_seconds'] or 600, 600))]
    return {'checked_at': now, 'missing_published_endpoints': missing,
            'overdue_running_jobs': stuck, 'jobs': jobs,
            'note': 'Endpoint presence alone does not validate date coverage, '
                    'warehouse coverage, fields, APS/WIP generation, or quantities.'}


def main():
    import psycopg
    from psycopg.rows import dict_row
    dsn = os.environ.get('DATABASE_URL', '')
    if not dsn:
        raise SystemExit('DATABASE_URL is required; do not pass credentials on the command line.')
    with psycopg.connect(dsn, connect_timeout=8, row_factory=dict_row,
            options='-c default_transaction_read_only=on -c statement_timeout=15000 '
                    '-c timezone=Asia/Seoul') as connection:
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        result = audit(connection)
    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    return 2 if result['missing_published_endpoints'] or result['overdue_running_jobs'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
