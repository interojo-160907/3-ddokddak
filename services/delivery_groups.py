"""Immutable SQL delivery manifests; payloads are fetched only for new snapshots."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path


def read_pair(connection, *, version=None):
    with connection() as c:
        if not c.execute("SELECT to_regclass('prod3_delivery.release') AS name").fetchone()['name']:
            raise ValueError('서버 두 그룹 준비 중 · 이전 자료 유지')
        data = c.execute('SELECT * FROM prod3_delivery.release WHERE group_no=2 '
                         + ('AND version=%s ' if version else '')
                         + 'ORDER BY published_at DESC LIMIT 1', (version,) if version else ()).fetchone()
        if not data:
            raise ValueError('생산 현황 그룹 준비 중 · 이전 자료 유지')
        aps = c.execute('SELECT * FROM prod3_delivery.release WHERE version=%s', (data['aps_version'],)).fetchone()
    if not aps or aps['aps_key'] != data['aps_key']:
        raise ValueError('APS 회차 확인 중 · 이전 자료 유지')
    age=(datetime.now(timezone.utc)-data['received_at']).total_seconds()
    if age < -60: raise ValueError('서버 자료 시각 확인 필요')
    if age > 3900:
        raise ValueError('서버 생산 현황 갱신 지연 · 이전 자료 유지')
    kst = timezone(timedelta(hours=9))
    if data['received_at'].astimezone(kst).date() != datetime.now(kst).date():
        raise ValueError('오늘 생산 현황 준비 중 · 이전 자료 유지')
    if (data['received_at']-data['started_at']).total_seconds()>180:
        raise ValueError('서버 자료 조회 시각 차이 확인 필요')
    from services.data_location import resolve_data_root
    barrier=resolve_data_root()/'backward-v1/settings/manual_refresh_barrier.json'
    if barrier.exists():
        stamp=datetime.fromisoformat(json.loads(barrier.read_text('utf-8'))['completed_at'])
        if data['started_at']<stamp:
            raise ValueError('원 API 수동 갱신 자료가 더 최신 · 서버 다음 회차 대기')
    return aps,data


def pin_sources(catalog, aps, data):
    from datetime import timedelta
    members=aps['members']+data['members']
    datasets={s['dataset'] for s in members}
    day=data['received_at'].astimezone(__import__('datetime').timezone(timedelta(hours=9))).date().isoformat()
    yesterday=(datetime.fromisoformat(day)-timedelta(days=1)).date().isoformat()
    result=[]
    for source in catalog:
        ep=source['endpoint']
        if source['dataset'] in datasets and (ep not in ('/api/production-performance','/api/inventory-ledger-product') or source.get('date_from','')>=day):
            continue
        if ep in ('/api/production-performance','/api/inventory-ledger-product') and source.get('date_to','')>=day:
            if source.get('date_from','')>=day:continue
            source=dict(source,date_to=yesterday)
        result.append(source)
    for member in members:
        item=dict(member)
        for key in ('published_at','last_success_at'):
            if isinstance(item.get(key),str):item[key]=datetime.fromisoformat(item[key])
        result.insert(0,item)
    return result


def mapped_rows(rows, mappings):
    if not mappings:return rows
    def convert(value,kind):
        if value is None:return None
        try:
            if kind=='integer':return int(value)
            if kind=='number':return float(value)
            if kind=='boolean':return value if isinstance(value,bool) else str(value).lower() in {'1','true','y','yes'}
            if kind=='json':return value
            return str(value)
        except (ValueError,TypeError):return None
    return [{m['target']:convert(row.get(m['source'],m.get('default')),m['type']) for m in mappings} for row in rows]


def head_status(connection):
    with connection() as c:
        heads=c.execute('SELECT DISTINCT ON(group_no) group_no,version,aps_key,published_at,started_at,received_at '
                        'FROM prod3_delivery.release ORDER BY group_no,published_at DESC').fetchall()
        state=c.execute('SELECT checked_at,message,last_error FROM prod3_delivery.state WHERE singleton').fetchone()
    return {'groups':heads,'state':state}
