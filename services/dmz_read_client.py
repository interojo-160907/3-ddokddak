"""Read complete stored server snapshots. Never falls back to source HTTP.

Each collector pins the published catalog. A changed APS generation is
validated by the existing collector before the local database is replaced.
"""
from __future__ import annotations
import copy
import gzip
import hashlib
import json
import os
import threading
import uuid
from contextlib import contextmanager
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from services.data_location import resolve_data_root

_lock = threading.RLock()
_catalog = None
_rows = OrderedDict()
WAREHOUSES = {'사출창고':'Q001','분리창고':'R001','검사접착':'G007',
              '누수규격검사':'G005','규격검사완료':'P002'}
DATE_FIELDS = {'/api/production-performance':'pr_dt', '/api/prod-request-domestic':'re_dt',
               '/api/order-status-detail':'od_dt','/api/hydration-job-list':'job_dt'}
REFERENCE_ENDPOINTS = {'/api/aps-plan','/api/aps-backward-plan','/api/aps-arrange',
                      '/api/aps-outbound-status','/api/aps-plan/meta','/api/aps-wip',
                      '/api/product-names','/api/bom-explosion','/api/product-saline',
                      '/api/item-list-bulk','/api/prod-request-domestic','/api/order-status-detail'}


def route_to_dmz(endpoint):
    mode = os.getenv('DDOKDDAK_PROD3_TRANSPORT','').strip()
    path = urlsplit(endpoint).path
    return mode == 'dmz' or (mode == 'manual_live' and path in REFERENCE_ENDPOINTS)


def transport_for_collection(source, scheduled):
    return 'manual_live' if source == 'live' and not scheduled else 'dmz'


def _manual_path(endpoint, params):
    query = {k:v for k,v in (params or {}).items() if k not in ('prompt_context','limit','offset')}
    key = hashlib.sha256(json.dumps([urlsplit(endpoint).path,query],sort_keys=True,
                                   ensure_ascii=False,default=str).encode()).hexdigest()
    return resolve_data_root()/'backward-v1/manual-api-cache'/(key+'.json.gz')


def _source_clock(payload):
    # Use the source query time, never the later local download time.
    for key in ('source_refreshed_at','query_date'):
        try:
            clock=datetime.fromisoformat(str(payload.get(key) or ''))
            if clock.tzinfo is not None:
                clock=clock.astimezone(timezone(timedelta(hours=9)))
            return clock.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def remember_manual(endpoint, params, payload):
    if os.getenv('DDOKDDAK_PROD3_TRANSPORT') != 'manual_live': return payload
    if not isinstance(payload.get('rows'),list) or payload.get('truncated'):
        return payload
    if int(payload.get('total_count',len(payload['rows']))) != len(payload['rows']):
        return payload
    payload=dict(payload,collection_origin='manual API')
    path=_manual_path(endpoint,params)
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with gzip.open(temp,'wt',encoding='utf-8') as f: json.dump(payload,f,ensure_ascii=False,default=str)
    temp.replace(path)
    return payload


def prefer_newer_manual(endpoint,params,stored):
    path=_manual_path(endpoint,params)
    if not path.exists(): return stored
    try:
        with gzip.open(path,'rt',encoding='utf-8') as f: manual=json.load(f)
        a,b=_source_clock(manual),_source_clock(stored)
        if a and b and a>b and not manual.get('truncated'):
            return manual
    except (OSError,ValueError,KeyError): pass
    return stored


@contextmanager
def connection():
    import psycopg
    from psycopg.rows import dict_row
    from services.server_read_settings import resolve_connection
    dsn=resolve_connection()
    try:
        c = psycopg.connect(dsn,connect_timeout=8,row_factory=dict_row,
            application_name='production3-dmz-read',
            options='-c default_transaction_read_only=on -c statement_timeout=90000')
    except Exception:
        raise RuntimeError('사내 서버 연결 실패 · 기존 로컬 자료 유지') from None
    try:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        yield c
    finally:
        c.close()


def catalog():
    global _catalog
    with _lock:
        if _catalog is None:
            with connection() as c:
                _catalog = c.execute('''SELECT j.id AS job_id,j.dataset,j.endpoint,j.request_params,
                    j.last_success_at,s.id AS snapshot_id,s.run_id,s.row_count,s.source_version,s.content_hash,
                    s.published_at,sc.date_from,sc.date_to
                    FROM collector_hub.collection_job j
                    JOIN collector_hub.snapshot_alias a ON a.job_id=j.id
                    JOIN collector_hub.snapshot s ON s.id=a.snapshot_id
                    LEFT JOIN scm_client.snapshot_scope sc ON sc.snapshot_id=s.id
                    WHERE j.enabled AND s.status='published' AND NOT COALESCE(sc.truncated,false)
                    ORDER BY j.last_success_at DESC NULLS LAST''').fetchall()
                # Retain one latest complete daily ledger per date. Range totals
                # cannot be filtered like transaction rows.
                history = c.execute('''SELECT DISTINCT ON (j.id,sc.date_from)
                    j.id AS job_id,j.dataset,j.endpoint,j.request_params,
                    s.published_at AS last_success_at,s.id AS snapshot_id,s.run_id,
                    s.row_count,s.source_version,s.content_hash,s.published_at,
                    sc.date_from,sc.date_to
                    FROM collector_hub.collection_job j
                    JOIN collector_hub.snapshot s ON s.job_id=j.id
                    JOIN scm_client.snapshot_scope sc ON sc.snapshot_id=s.id
                    WHERE j.enabled AND j.endpoint='/api/inventory-ledger-product'
                    AND s.status='published' AND NOT sc.truncated
                    AND sc.date_from=sc.date_to
                    AND sc.date_from >= to_char(current_date-30,'YYYY-MM-DD')
                    ORDER BY j.id,sc.date_from,s.published_at DESC''').fetchall()
                ids={r['snapshot_id'] for r in _catalog}
                _catalog.extend(r for r in history if r['snapshot_id'] not in ids)
                _catalog.sort(key=lambda r:r['published_at'],reverse=True)
        return _catalog


def reset_catalog():
    global _catalog
    with _lock: _catalog=None


def missing_production_days(start, end):
    """Check published coverage before downloading any production snapshots.

    Coverage is independent of row counts: an explicitly collected empty day
    is valid, while an absent day must never be turned into zero production.
    """
    sources = [s for s in catalog() if s['endpoint'] == '/api/production-performance']
    return [(start + timedelta(days=i)).isoformat()
            for i in range((end - start).days + 1)
            if not any(s.get('date_from') and s.get('date_to')
                       and s['date_from'] <= (start + timedelta(days=i)).isoformat() <= s['date_to']
                       for s in sources)]


def missing_calculation_sources():
    rows=catalog()
    required=('/api/product-names','/api/bom-explosion','/api/product-saline',
              '/api/production-performance','/api/hydration-job-list','/api/item-list-bulk')
    missing=[ep for ep in required if not any(r['endpoint']==ep for r in rows)]
    today=date.today().isoformat()
    yesterday=(date.today()-timedelta(days=1)).isoformat()
    def covers(r,start,end):
        return r.get('date_from') and r.get('date_to') and r['date_from']<=start<=end<=r['date_to']
    for endpoint,start in (('/api/production-performance',today),
                           ('/api/hydration-job-list',(date.today()-timedelta(days=4)).isoformat())):
        if not any(r['endpoint']==endpoint and covers(r,start,today) for r in rows):
            missing.append(endpoint+' '+start+'~'+today)
    for endpoint,names in (('/api/aps-wip',WAREHOUSES),
                           ('/api/inventory-ledger-product',list(WAREHOUSES))):
        for name in names:
            code=WAREHOUSES[name]
            matching=[r for r in rows if r['endpoint']==endpoint and (r['request_params'].get('wh_cd') or
                WAREHOUSES.get(r['request_params'].get('wh_name') or r['request_params'].get('wh_nm')))==code
                ]
            ready=bool(matching) if endpoint=='/api/aps-wip' else all(
                any(r['date_from']==r['date_to']==day for r in matching) for day in (yesterday,today))
            if not ready:
                missing.append(endpoint+' '+name)
    return missing


def _remember_rows(sid,value):
    _rows[sid]=value
    _rows.move_to_end(sid)
    while len(_rows)>3: _rows.popitem(last=False)


def _valid_rows(rows,source):
    if len(rows)!=source['row_count']: return False
    expected=source.get('content_hash')
    if not expected: return True
    actual=hashlib.sha256(json.dumps(rows,ensure_ascii=False,sort_keys=True,
                         separators=(',',':'),default=str).encode('utf-8')).hexdigest()
    return actual==expected


def snapshot(source):
    sid = source['snapshot_id']
    with _lock:
        if sid in _rows:
            _rows.move_to_end(sid)
            return _rows[sid]
        cache = resolve_data_root()/'backward-v1/dmz-cache'/ (sid+'.json.gz')
        if cache.exists():
            try:
                with gzip.open(cache,'rt',encoding='utf-8') as f: value=json.load(f)
                if _valid_rows(value['rows'],source):
                    _remember_rows(sid,value)
                    return value
            except (OSError,ValueError,KeyError): pass
        with connection() as c:
            meta = c.execute("SELECT payload - 'rows' - 'orders' AS metadata FROM collector_hub.raw_payload WHERE job_id=%s AND run_id=%s LIMIT 1",
                             (source['job_id'],source['run_id'])).fetchone()
            if not meta and source['endpoint']=='/api/inventory-ledger-product' and _source_clock(
                    {'query_date':source.get('source_version')}):
                # Daily snapshots outlive raw payload retention; their recorded
                # source query time and validated date scope remain authoritative.
                meta={'metadata':{'query_date':source['source_version'],
                      'date_from':source['date_from'],'date_to':source['date_to']}}
            if not meta or not isinstance(meta['metadata'],dict):
                raise RuntimeError('사내 서버 원천 메타 미준비: '+source['dataset'])
            rows = [r['record'] for r in c.execute('''SELECT record FROM collector_hub.snapshot_record
                    WHERE snapshot_id=%s ORDER BY ordinal''',(sid,))]
        if not _valid_rows(rows,source):
            raise RuntimeError('사내 서버 저장 건수 또는 내용 검증 실패: '+source['dataset'])
        value={'rows':rows,'metadata':meta['metadata']}
        cache.parent.mkdir(parents=True,exist_ok=True)
        temp=cache.with_name(cache.name+'.'+uuid.uuid4().hex+'.tmp')
        with gzip.open(temp,'wt',encoding='utf-8') as f: json.dump(value,f,ensure_ascii=False,default=str)
        temp.replace(cache)
        _remember_rows(sid,value)
        return value


def _day(value):
    text=str(value or '')[:10]
    if len(text)==8 and text.isdigit(): return text[:4]+'-'+text[4:6]+'-'+text[6:8]
    return text


def _merge_daily_ledgers(loaded,start,end):
    result={}
    keys=('fwh_cd','sa_cd','gd_cd','lot_no','sub_lot','unit_cd')
    for source,value in sorted(loaded,key=lambda pair:pair[0]['date_from']):
        day=source['date_from'];seen=set()
        for row in value['rows']:
            key=tuple(str(row.get(k) or '') for k in keys)
            if key in seen: raise RuntimeError('사내 서버 일별 수불 식별키 중복')
            seen.add(key)
            if key not in result:
                result[key]=dict(row,lm_qty=float(row.get('lm_qty') or 0) if day==start else 0,
                                 ip_qty=0.0,chul_qty=0.0,stock_qty=0.0)
            merged=result[key]
            for name in ('ip_qty','chul_qty'):
                merged[name]+=float(row.get(name) or 0)
            if day==end: merged['stock_qty']=float(row.get('stock_qty') or 0)
    return list(result.values())


def _matches(row,params,ignored):
    aliases={'gd_cd_list':'gd_cd','site_id':'res_site_id','oper':'oper_id','res':'res_id'}
    for key,value in params.items():
        if key in ignored or value in (None,'',0): continue
        actual=str(row.get(aliases.get(key,key)) or '').strip()
        wanted=str(value).strip()
        if key=='fac_cd' and wanted=='04':
            if actual!='04' and str(row.get('sachul_fac_cd') or '').strip()!='04': return False
        elif key=='gd_cd_list':
            if actual.upper() not in {v.strip().upper() for v in wanted.split(',')}: return False
        elif key in ('gd_cd','item_id','nm_nm','gd_nm','od_no'):
            if wanted.upper() not in actual.upper(): return False
        elif actual!=wanted: return False
    return True


def request_json(endpoint,params=None,**kwargs):
    endpoint=urlsplit(endpoint).path
    params=dict(params or {})
    is_meta=endpoint=='/api/aps-plan/meta'
    if is_meta: endpoint='/api/aps-plan'
    sources=[r for r in catalog() if r['endpoint']==endpoint]
    wh=params.get('wh_cd') or WAREHOUSES.get(params.get('wh_nm') or params.get('wh_name'))
    if endpoint in ('/api/aps-wip','/api/inventory-ledger-product'):
        if not wh: raise RuntimeError('사내 서버 창고 조회조건 미확인')
        sources=[r for r in sources if (r['request_params'].get('wh_cd') or
                 WAREHOUSES.get(r['request_params'].get('wh_name') or r['request_params'].get('wh_nm')))==wh]
    if endpoint=='/api/item-list-bulk':
        prefix=str(params.get('gd_cd') or '').upper()
        sources=[r for r in sources if prefix.startswith(str(r['request_params'].get('gd_cd') or '').upper())
                 or str(r['request_params'].get('gd_cd') or '').upper().startswith(prefix)]
        expected={letter+str(i) for letter in 'PQR' for i in range(10)
                  if not prefix or (letter+str(i)).startswith(prefix) or prefix.startswith(letter+str(i))}
        if {r['request_params'].get('gd_cd') for r in sources}!=expected:
            raise RuntimeError('사내 서버 품목코드 분할 자료 미준비')
    if not sources: raise RuntimeError('사내 서버 수집 자료 미준비: '+endpoint)
    start,end=str(params.get('date_from') or ''),str(params.get('date_to') or '')
    owners={}
    if start and end:
        first,last=date.fromisoformat(start),date.fromisoformat(end)
        if last<first: raise ValueError('조회기간 순서 오류')
        for i in range((last-first).days+1):
            day=(first+timedelta(days=i)).isoformat()
            source=next((s for s in sources if s.get('date_from') and s.get('date_to')
                         and (s['date_from']==s['date_to']==day if endpoint=='/api/inventory-ledger-product'
                              else s['date_from']<=day<=s['date_to'])),None)
            if source is None: raise RuntimeError('사내 서버 조회기간 미수집: '+endpoint+' '+day)
            owners[day]=source['snapshot_id']
        selected=[s for s in sources if s['snapshot_id'] in set(owners.values())]
    else:
        selected=sources if endpoint=='/api/item-list-bulk' else sources[:1]
    loaded=[(s,snapshot(s)) for s in selected]
    meta=copy.copy(loaded[0][1]['metadata'])
    if owners and len(loaded)>1:
        clocks=[_source_clock(v['metadata']) for _,v in loaded]
        if all(clocks):
            meta['query_date']=min(clocks).isoformat(sep=' ')
            if 'source_refreshed_at' in meta:
                meta['source_refreshed_at']=min(clocks).isoformat(sep=' ')
    if endpoint=='/api/inventory-ledger-product' and owners:
        # The least recent component determines freshness of the composed range.
        loaded=[(selected[0],{'rows':_merge_daily_ledgers(loaded,start,end)})]
    if is_meta:
        return dict(meta,last_refreshed_at=meta.get('source_refreshed_at'),
                    total_count=selected[0]['row_count'])
    ignored={'date_from','date_to','limit','offset','prompt_context'}
    if wh: ignored.update(('wh_cd','wh_nm','wh_name'))
    rows=[]
    field=DATE_FIELDS.get(endpoint)
    for source,value in loaded:
        for row in value['rows']:
            if owners and field and owners.get(_day(row.get(field)))!=source['snapshot_id']: continue
            if _matches(row,params,ignored): rows.append(row)
    if owners and not field and endpoint!='/api/inventory-ledger-product' and len(selected)!=1:
        raise RuntimeError('사내 서버 수불 기간은 하나의 완전한 저장본으로 조회해야 합니다.')
    total=len(rows);limit=int(params.get('limit') or 0);offset=int(params.get('offset') or 0)
    page=rows[offset:offset+limit] if limit>0 else rows[offset:]
    meta.update(rows=page,orders=page,total_count=total,returned_count=len(page),
                truncated=len(page)<total,date_from=start or meta.get('date_from'),
                date_to=end or meta.get('date_to'),dmz_snapshot_ids=[s['snapshot_id'] for s in selected],
                collection_origin='DMZ',downloaded_at=datetime.now().astimezone().isoformat())
    return prefer_newer_manual(endpoint,params,meta)
