"""Build an upgrade for existing installations without recreating triggers.

This only writes a local SQL file; it never connects to the server.
"""
from pathlib import Path
import re


def build() -> str:
    root = Path(__file__).resolve().parents[1]
    source = (root / 'server_sql/delivery.sql').read_text(encoding='utf-8')
    functions = []
    for name in ('arrange_fingerprint', 'publish_ready'):
        match = re.search(
            rf'CREATE OR REPLACE FUNCTION prod3_delivery\.{name}\(.*?\$\$;',
            source, re.DOTALL,
        )
        if match is None:
            raise ValueError(f'Missing function: {name}')
        functions.append(match.group(0))
    return """-- Apply with the matching client (30-minute observation policy).
-- Existing collectors/triggers and immutable published payloads are retained.
BEGIN;
SET LOCAL lock_timeout='2s';
SET LOCAL statement_timeout='30s';
SELECT pg_advisory_xact_lock(323,2);
ALTER TABLE prod3_delivery.state ADD COLUMN IF NOT EXISTS details jsonb NOT NULL DEFAULT '{}';
ALTER TABLE prod3_delivery.state ADD COLUMN IF NOT EXISTS retry_after timestamptz;
""" + '\n\n'.join(functions) + '\nCOMMIT;\n'


if __name__ == '__main__':
    path = Path(__file__).resolve().parents[1] / 'server_sql/upgrade_delivery_20261020.sql'
    path.write_text(build(), encoding='utf-8')
    print(path)
