"""Read only complete package-2 snapshots. No source API access."""
import json
import math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

SCHEMA_VERSION = 1

def cache_path(root=None):
    from services.data_location import resolve_data_root
    return (Path(root) if root is not None else resolve_data_root())/'backward-v1/lidding/current.json'

def cache_ready(root,version):
    try:
        b=json.loads(cache_path(root).read_text('utf-8'))
        if b.get('schema_version')!=SCHEMA_VERSION or b.get('version')!=version:return False
        build_model(b)
        return True
    except (OSError,ValueError,KeyError,TypeError):
        return False

STOCK_WAREHOUSES = {'300':'L관창고(자재)','P010':'A관 공정부자재','P030':'C관 공정부자재','S100':'S관 공정부자재'}

def download(previous_version='', *, version=None, destination=None):
    from contextlib import contextmanager
    from services.dmz_read_client import connection
    from services.delivery_groups import read_pair
    with connection() as c:
        @contextmanager
        def same_transaction():
            yield c
        aps, package = read_pair(same_transaction,version=version)
        members = {r['dataset']:r for r in aps['members']+package['members']}
        if 'prod3_lidding_stock_all' not in members:
            raise ValueError('전 창고 리드지 포함 생산 현황 패키지 준비 중')
        if previous_version == package['version']:
            return None
        payloads = {}
        for ds, member in members.items():
            if not (ds.startswith('prod3_lidding_') or ds in ('bom_explosion','aps_arrange','aps_backward_plan')):
                continue
            if ds=='aps_backward_plan':
                query="SELECT coalesce(jsonb_agg(r),'[]') rows FROM prod3_delivery.payload p,jsonb_array_elements(p.rows) r WHERE snapshot_id=%s AND r->>'oper_cd'='55'"
            elif ds=='aps_arrange':
                query="SELECT coalesce(jsonb_agg(r),'[]') rows FROM prod3_delivery.payload p,jsonb_array_elements(p.rows) r WHERE snapshot_id=%s AND r->>'oper_id'='55'"
            elif ds=='bom_explosion':
                query="SELECT coalesce(jsonb_agg(r),'[]') rows FROM prod3_delivery.payload p,jsonb_array_elements(p.rows) r WHERE snapshot_id=%s AND r->>'parent_gu'='P' AND r->>'child_cd' LIKE 'BS%%' AND r->>'use_yn'='Y'"
            else:
                query='SELECT rows FROM prod3_delivery.payload WHERE snapshot_id=%s'
            row=c.execute(query,(member['snapshot_id'],)).fetchone()
            if row is None: raise ValueError('패키지 원본 보관 확인 필요')
            payloads[ds]=row['rows']
            if ds.startswith('prod3_lidding_') and len(row['rows']) != member['row_count']:
                raise ValueError('리드지 패키지 건수 불일치')
        bundle=dict(schema_version=SCHEMA_VERSION,version=package['version'], aps_key=aps['aps_key'],
            received_at=str(package['received_at']), aps_at=members['aps_backward_plan']['metadata'].get('source_refreshed_at',''),
            day=members['prod3_lidding_stock_all']['metadata']['filters_applied']['std_dt'],
            members=members,payloads=payloads,stage='55',target_factor=1.5,stock_warehouses=list(STOCK_WAREHOUSES))
    # Validate the linked model before replacing the last good local cache.
    build_model(bundle)
    target=Path(destination) if destination else cache_path()
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_suffix('.tmp')
    temp.write_text(json.dumps(bundle,ensure_ascii=False,default=str),encoding='utf-8')
    temp.replace(target)
    return bundle

def build_model(bundle):
    from services.backward_source import applicable_factories
    raw=bundle['payloads']; registered=defaultdict(set)
    if 'prod3_lidding_stock_all' not in raw:raise ValueError('전 창고 재고 포함 패키지 준비 중')
    for r in raw['aps_arrange']:
        registered[str(r.get('sale_cd','')).upper()].add(str(r.get('fac_cd','')))
    eligible={p for p,fac in registered.items() if '04' in fac}
    orders=defaultdict(list)
    for r in raw['aps_backward_plan']:
        p=str(r.get('sale_cd','')).upper()
        if '04' not in applicable_factories(r,registered[p]): continue
        eligible.add(p)
        parts=str(r['demand_id']).rsplit('_',3)
        if len(parts)!=4: raise ValueError('APS 수주 식별자 확인 필요')
        initial=r.get('initial') or parts[0]
        market='안전' if '안전' in initial else initial if initial in ('PB','국내','해외') else '해외'
        qty=float(r['backward_qty'])
        if not math.isfinite(qty) or qty<0: raise ValueError('APS 필요수량 오류')
        orders[p].append(dict(initial=initial,order=parts[1],seq=parts[2],demand=r['demand_id'],
            item=r['item_id'],pcode=p,due=r.get('due_date') or '',market=market,qty=qty))
    model={}; links=set(); coefficients={}
    for r in raw['bom_explosion']:
        p=str(r.get('parent_cd','')).upper()
        if p not in eligible or not ('리드지' in str(r.get('child_nm')) or 'lidding' in str(r.get('child_nm')).lower()):continue
        code=r['child_cd']; key=(p,code,r.get('child_spec'),float(r['qty']))
        if not math.isfinite(key[3]) or key[3]<0:raise ValueError('BOM 소요량 확인 필요')
        if (p,code) in coefficients and coefficients[(p,code)]!=key[3]:raise ValueError('BOM 소요량 충돌')
        coefficients[(p,code)]=key[3]
        if key in links: continue
        links.add(key)
        item=model.setdefault(code,dict(code=code,name=r['child_nm'],products={},orders=[],stock=None,warehouses={},units=set(),daily={},transactions=[],specs=set()))
        item['specs'].add(r.get('child_spec') or '')
        item['products'][p]=r.get('parent_nm') or p
        for order in orders[p]:
            item['orders'].append(dict(order,product=item['products'][p],material_qty=order['qty']*float(r['qty'])))
    for r in raw['prod3_lidding_stock_all']:
        if r['itm_cd'] not in model:continue
        if r['wh_cd'] not in STOCK_WAREHOUSES:continue
        item=model[r['itm_cd']]
        qty=float(r['stock_qty'])
        if not math.isfinite(qty):raise ValueError('리드지 재고 수량 오류')
        item['stock']=(item['stock'] or 0)+qty
        warehouse=item['warehouses'].setdefault(r['wh_cd'],dict(name=r['wh_nm'],qty=0))
        warehouse['qty']+=float(r['stock_qty'])
        item['units'].add(r.get('um_bc_nm'))
    today=date.fromisoformat(bundle['day'])
    for code,item in model.items():
        dataset='prod3_lidding_issue_'+code.lower()
        if dataset not in raw: raise ValueError(f'{code} 불출 자료 미완료')
        daily=defaultdict(float); kinds=set()
        for r in raw[dataset]:
            if not r.get('dt1'):continue  # opening-balance summary, not usage
            day=str(r['dt1'])[:10]
            if not today-timedelta(days=29)<=date.fromisoformat(day)<=today:
                raise ValueError(f'{code} 불출 조회기간 불일치')
            kinds.add((r.get('cd1'),r.get('cd2'),r.get('cd2_nm')))
            # Confirmed production material issue category. Other transfers excluded.
            if r.get('cd2')=='LE200500':
                quantity=float(r.get('out_qty') or 0)-float(r.get('in_qty') or 0)
                if not math.isfinite(quantity):raise ValueError('리드지 불출 수량 오류')
                daily[day]+=quantity
            item['transactions'].append({k:r.get(k) for k in ('dt1','cd2_nm','io_no','lot_no','in_qty','out_qty')})
        item['daily']=dict(daily)
        item['rate']=sum(daily.get((today-timedelta(days=n)).isoformat(),0) for n in range(1,8))/7
        available_stock=item['stock']
        item['days']=available_stock/item['rate'] if available_stock is not None and item['rate']>0 else None
        item['kinds']=sorted(kinds,key=str)
    return model

def select_rows(model,market='전체',due=None,search='',local_search='',risk='전체 리드지'):
    markets={market} if isinstance(market,str) else set(market)
    all_markets=not markets or '전체' in markets or {'해외','PB','국내','안전'}.issubset(markets)
    def match(term,text):
        terms=[t.strip().lower() for t in term.split(',') if t.strip()]
        return not terms or any(t=='*' or t in text.lower() for t in terms)
    scoped=[]
    for item in model.values():
        selected=[o for o in item['orders'] if (all_markets or o['market'] in markets) and (not due or o['due']<=due)]
        if (not all_markets or due) and not selected:continue
        text=' '.join([item['code'],item['name'],*item['products'],*item['products'].values(),*(o['order']+' '+o['initial']+' '+o['item'] for o in selected)])
        if not match(search,text) or not match(local_search,text):continue
        # Material search selects rows, not an arbitrary subset of a shared material's needs.
        need=sum(o['material_qty'] for o in selected)
        goal=math.ceil(need*1.5)
        remaining=item['stock']-goal if item['stock'] is not None else None
        shortage=remaining is not None and remaining<0
        lowdays=item['days'] is not None and item['days']<=3
        unknown=item['stock'] is None or item['rate']<0 or item['units']!={'EA'}
        normal=not unknown and not shortage and not lowdays
        scoped.append(dict(item,selected=selected,need=need,goal=goal,remaining=remaining,shortage=shortage,lowdays=lowdays,unknown=unknown,normal=normal))
    visible=[r for r in scoped if risk=='전체 리드지' or
        (risk=='리스크 관리' and (r['shortage'] or r['lowdays'] or r['unknown'])) or
        (risk=='1.5배 목표 부족' and r['shortage']) or (risk=='가용 3일 이하' and r['lowdays']) or (risk=='정상' and r['normal'])]
    return sorted(visible,key=lambda r:(not r['shortage'],not r['lowdays'],r['code'])),scoped
