"""Provision a common reader after Google permission; never ship credentials."""
import os
import threading
import time
import keyring
import requests

SERVICE='DDOKDDAK_PROD3_DMZ'
_lock=threading.RLock()
_permission_until=0.0
_dsn=''


def _gate():
    from config import APP_VERSION
    from services.program_gate import ProgramGate
    return ProgramGate(APP_VERSION,timeout=15)


def _assert_permission():
    global _permission_until
    if time.monotonic()<_permission_until: return
    gate=_gate()
    result=gate.check(allow_cache_fallback=False)
    if not gate.endpoint or not result.allowed or result.source!='remote':
        raise RuntimeError('구글시트 사용 허가를 확인하지 못해 서버 자료 받기를 보류합니다.')
    _permission_until=time.monotonic()+300


def validate_shared_reader(dsn):
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    try:
        settings=conninfo_to_dict(dsn)
        if (settings.get('host')!='192.168.2.29' or settings.get('dbname')!='sangho_db'
                or str(settings.get('port','5432'))!='5432'):
            raise ValueError()
        tables=('collector_hub.collection_job','collector_hub.snapshot_alias',
                'collector_hub.snapshot','collector_hub.snapshot_record','collector_hub.raw_payload')
        with psycopg.connect(dsn,connect_timeout=8,
                options='-c default_transaction_read_only=on -c statement_timeout=10000') as c:
            for table in tables:
                readable,writable=c.execute('''SELECT has_table_privilege(current_user,%s,'SELECT'),
                    has_table_privilege(current_user,%s,'INSERT,UPDATE,DELETE,TRUNCATE')''',
                    (table,table)).fetchone()
                if not readable or writable: raise ValueError()
            if not c.execute("SELECT has_table_privilege(current_user,'scm_client.snapshot_scope','SELECT')").fetchone()[0]:
                raise ValueError()
    except Exception:
        raise RuntimeError('공통 서버 연결은 생산3팀 조회용 읽기 전용 계정으로 설정해야 합니다.') from None


def _provision(gate):
    if not gate.endpoint: raise RuntimeError('공통 서버 조회 설정을 관리 API에 등록해야 합니다.')
    try:
        response=requests.post(gate.endpoint,json={**gate.identity(),'action':'server_read_config'},
            timeout=(5,20),headers={'User-Agent':'Ddokddak-Production3-ServerSettings'})
        response.raise_for_status()
        body=response.json();data=body.get('result',{})
        if body.get('ok') is not True or data.get('allowed') is not True:
            raise ValueError()
        dsn=data.get('server_read',{}).get('dsn','')
        if not isinstance(dsn,str) or not dsn.strip(): raise ValueError()
    except (requests.RequestException,ValueError,TypeError,AttributeError):
        raise RuntimeError('공통 서버 조회 설정 등록 대기 · 기존 로컬 자료를 유지합니다.') from None
    validate_shared_reader(dsn)
    keyring.set_password(SERVICE,'DATABASE_URL',dsn)
    return dsn


def resolve_connection():
    global _dsn
    with _lock:
        _assert_permission()
        if _dsn: return _dsn
        dsn=os.getenv('DDOKDDAK_PROD3_DMZ_DSN','').strip()
        if not dsn: dsn=keyring.get_password(SERVICE,'DATABASE_URL') or ''
        if not dsn:
            # Existing SCM credentials stay on this Windows account. They are
            # reused locally, never returned by or uploaded to the management API.
            dsn=keyring.get_password('SCM_DMZ_CLIENT','DATABASE_URL') or ''
            if dsn: keyring.set_password(SERVICE,'DATABASE_URL',dsn)
        if not dsn: dsn=_provision(_gate())
        _dsn=dsn
        return dsn
