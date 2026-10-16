"""Read an immutable, complete server observation bundle for one calculation."""
import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
REQUIRED = {'inventory_q001','inventory_r001','inventory_g007','inventory_g005',
            'inventory_p002','prod3_production_today','prod3_hydration_jobs'}


def observation_label(cycle):
    try:
        start = datetime.fromisoformat(cycle['started_at']).astimezone(KST)
        end = datetime.fromisoformat(cycle['received_at']).astimezone(KST)
        return f"자료 조회 {start:%m-%d %H:%M:%S}~{end:%H:%M:%S}"
    except (KeyError,TypeError,ValueError):
        return '자료 조회 시각 확인 필요'


def validate_bundle(blob, expected_hash, cycle_id, now=None):
    now = now or datetime.now(KST)
    if hashlib.sha256(blob).hexdigest() != expected_hash:
        raise ValueError('서버 자료 묶음 검증 실패')
    data = json.loads(gzip.decompress(blob))
    if data.get('format_version') != 1 or data.get('cycle_id') != cycle_id:
        raise ValueError('서버 자료 회차 확인 실패')
    if data.get('business_date') != now.date().isoformat():
        raise ValueError('오늘 자료 묶음 준비 중 · 이전 자료 유지')
    if set(data.get('members', {})) != REQUIRED:
        raise ValueError('재고·실적·수화 자료 묶음 미완료')
    if set(data.get('aps', {})) != {'aps_plan','aps_backward_plan'}:
        raise ValueError('서버 APS 기준 회차 누락')
    starts, ends = [], []
    for name, member in data['members'].items():
        payload = member['payload']
        rows = payload.get('rows')
        if not isinstance(rows, list) or payload.get('truncated') or int(payload.get('total_count',len(rows))) != len(rows):
            raise ValueError('서버 자료 건수 확인 실패: ' + name)
        if payload.get('date_to') != data['business_date']:
            raise ValueError('서버 조회 날짜 불일치: ' + name)
        if name != 'prod3_hydration_jobs' and payload.get('date_from') != data['business_date']:
            raise ValueError('서버 일별 자료 기간 불일치: ' + name)
        start, end = (datetime.fromisoformat(member[k]) for k in ('started_at','received_at'))
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValueError('서버 자료 시각 확인 실패')
        starts.append(start); ends.append(end)
    span = (max(ends)-min(starts)).total_seconds()
    if span > 180 or abs(span-float(data.get('observation_span_seconds',-1))) > .01:
        raise ValueError('자료 조회 시각 차이가 커서 이전 자료 유지')
    if (now-max(ends)).total_seconds() > 1800 or (max(ends)-now).total_seconds() > 60:
        raise ValueError('서버 최신 자료 준비 지연 · 이전 자료 유지')
    return data


def read_cycle(connection):
    with connection() as c:
        present = c.execute("SELECT to_regclass('scm_client.production_cycle') AS name").fetchone()
        if not present or not present['name']:
            raise ValueError('서버 업데이트 준비 중 · 이전 자료 유지')
        row = c.execute('SELECT id,content_hash,bundle FROM scm_client.production_cycle ORDER BY published_at DESC LIMIT 1').fetchone()
        state = c.execute('SELECT status,started_at,completed_at FROM scm_client.production_cycle_state WHERE singleton').fetchone()
    if not row:
        if state and state['status'] == 'failed':
            raise ValueError('서버 자료 묶음 수집 실패 · 이전 자료 표시 · 자동 재확인')
        raise ValueError('서버에서 첫 재고·실적 자료 묶음 준비 중')
    data = validate_bundle(bytes(row['bundle']),row['content_hash'],row['id'])
    data['server_status'] = dict(state or {})
    return data


def pin_catalog(catalog, bundle):
    """Today's live sources come exclusively from one cohort; history stays dated."""
    for dataset, expected in bundle['aps'].items():
        endpoint = '/api/aps-plan' if dataset == 'aps_plan' else '/api/aps-backward-plan'
        current = next((s for s in catalog if s['endpoint'] == endpoint),None)
        if not current or str(current['snapshot_id']) != str(expected['snapshot_id']):
            raise ValueError('새 APS에 맞는 재고·실적 자료 묶음 준비 중')
    day = bundle['business_date']
    yesterday = (datetime.fromisoformat(day)-timedelta(days=1)).date().isoformat()
    result = []
    for source in catalog:
        ep = source['endpoint']
        if ep == '/api/hydration-job-list':
            continue
        if ep in ('/api/inventory-ledger-product','/api/production-performance') and source.get('date_to','') >= day:
            if source.get('date_from','') >= day:
                continue
            source = dict(source,date_to=yesterday)
        result.append(source)
    payloads = {}
    for name, member in bundle['members'].items():
        payload = member['payload']
        sid = bundle['cycle_id'] + ':' + name
        rows = payload['rows']
        metadata = {k:v for k,v in payload.items() if k not in ('rows','orders')}
        payloads[sid] = {'rows':rows,'metadata':metadata}
        result.insert(0,dict(dataset=name,job_id=name,endpoint=member['endpoint'],
            request_params=member['request_params'],snapshot_id=sid,run_id=bundle['cycle_id'],
            row_count=len(rows),date_from=payload['date_from'],date_to=payload['date_to'],
            published_at=bundle['received_at'],source_version=payload.get('query_date',''),
            content_hash='',production_cycle=True))
    return result,payloads
