"""Read-only demand/BOM join. Inventory is never allocated again by order filters."""
from __future__ import annotations
from contextlib import closing
import collections
import hashlib
import json
import gzip
import os
import re
import shutil
import sqlite3
import threading
import time
from datetime import date, datetime
from pathlib import Path
from config import DATA_CENTER_DIR, DATA_DIR
from services.process_status_service import _channel, classification_sort_key
from services.live_production_need_service import DB_PATH as LIVE_DB_PATH
from services.hydration_instruction_service import HydrationInstructionService
from services.erp_api_client import request_json
from services.collection_parallel import bounded_map

HEADERS = ['신규분류요약','품명','파워 / CP / AXIS / ADD','사출코드','분리코드','제품코드',
           '사출창고','분리창고','수화 지시량','검사접착',
           '누수규격검사','수화 부족','완제품 부족','최우선 납기']
WAREHOUSES = ['사출창고','분리창고','검사접착','누수규격검사']
norm = lambda x: str(x or '').strip().upper()

def number(x):
    try: return float(x)
    except (ValueError, TypeError): return None

def specs(x):
    return tuple(number(x.get(k)) for k in ('spec30','spec40','spec50','spec60'))

def spec_key(x):
    pw,cp,ax,add=specs(x)
    return (-(pw or 0),-(cp or 0),ax or 0,add or 0,norm(x['gd_cd']))

def spec_text(x):
    parts=[]
    for i,n in enumerate(specs(x)):
        if n is None: continue
        parts.append(f'{int(n):03d}' if i==2 else ('-' if n<=0 else '+')+f'{abs(n):05.2f}' if i==0 else f'{n:+.2f}')
    return ' / '.join(parts)

def hydration_shortage(final_shortage, leak_inventory, inspection_inventory, instruction_qty):
    return max(0.0,float(final_shortage or 0)-float(leak_inventory or 0)-float(inspection_inventory or 0)-float(instruction_qty or 0))

def read_tables(path, tables):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as c:
        c.row_factory=sqlite3.Row
        c.execute('BEGIN')
        return {t:[dict(r) for r in c.execute('SELECT * FROM '+t)] for t in tables}

class InventoryStatusService:
    def __init__(self, cache=None):
        configured=os.getenv('DDOKDDAK_INVENTORY_STATUS_DATA_DIR','').strip()
        self.cache=Path(cache or configured or (DATA_CENTER_DIR/'inventory-status'))
        self.cache.mkdir(parents=True,exist_ok=True)
        legacy=DATA_DIR/'inventory-status'
        if cache is None and self.cache.resolve()!=legacy.resolve() and legacy.exists():
            # Installed updates replace application files, so operational data
            # lives in the shared data root. Migrate old local files once and
            # never overwrite a newer persistent snapshot or API config.
            for source in legacy.rglob('*'):
                relative=source.relative_to(legacy);target=self.cache/relative
                if source.is_dir():target.mkdir(parents=True,exist_ok=True)
                elif not target.exists():
                    target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        self.view_cache=self.cache/'view-snapshots';self.view_cache.mkdir(parents=True,exist_ok=True)
        self._memory_results=collections.OrderedDict()
        self._stock_cache_signature=None;self._stock_cache_value=None
        self._stock_cache_lock=threading.Lock()
        self.hydration_service=HydrationInstructionService(self.cache)

    def _source_signature(self):
        paths=[LIVE_DB_PATH,DATA_CENTER_DIR/'bom/product_reference.sqlite',
               *(self.cache/('inventory_'+wh+'.json') for wh in WAREHOUSES),self.hydration_service.current_path]
        values=[]
        for path in paths:
            try:
                stat=path.stat();values.append(f'{path}|{stat.st_size}|{stat.st_mtime_ns}')
            except OSError:values.append(f'{path}|missing')
        return hashlib.sha256('\n'.join(values).encode('utf-8')).hexdigest()

    @staticmethod
    def _filter_key(filters):
        clean={'markets':['전체'],'classes':['전체'],'search':'','detail_search':'','due':None,'process':'전체', **(filters or {})}
        for key in ('markets','classes'):
            if key in clean:clean[key]=sorted(str(x) for x in (clean[key] or []))
        return json.dumps(clean,ensure_ascii=False,sort_keys=True,separators=(',',':'))

    def _snapshot_path(self,key):
        return self.view_cache/(hashlib.sha256(key.encode('utf-8')).hexdigest()+'.json.gz')

    def _cached_result(self,key,signature):
        cached=self._memory_results.get(key)
        if cached and cached[0]==signature:
            self._memory_results.move_to_end(key);return cached[1]
        path=self._snapshot_path(key)
        try:
            with gzip.open(path,'rt',encoding='utf-8') as stream:payload=json.load(stream)
            if payload.get('signature')==signature and payload.get('filter_key')==key:
                result=payload['result'];self._remember_result(key,signature,result);return result
        except (OSError,ValueError,KeyError):pass
        return None

    def _save_result(self,key,signature,result):
        self._remember_result(key,signature,result)
        target=self._snapshot_path(key);tmp=target.with_suffix('.tmp')
        try:
            with gzip.open(tmp,'wt',encoding='utf-8') as stream:
                json.dump({'signature':signature,'filter_key':key,'saved_at':datetime.now().isoformat(timespec='seconds'),'result':result},stream,ensure_ascii=False)
            tmp.replace(target)
            snapshots=sorted(self.view_cache.glob('*.json.gz'),key=lambda p:p.stat().st_mtime,reverse=True)
            for old in snapshots[24:]:old.unlink(missing_ok=True)
        except OSError:
            tmp.unlink(missing_ok=True)

    def _remember_result(self,key,signature,result):
        self._memory_results[key]=(signature,result);self._memory_results.move_to_end(key)
        while len(self._memory_results)>8:self._memory_results.popitem(last=False)

    def _request(self, name, endpoint, params):
        data = request_json(endpoint, params, timeout=30)
        return self.save_response(name, data)

    def _warm_specs(self, missing):
        # Save successful items individually; resume only missing items later.
        bounded_map(lambda base: self._request('item_'+base, '/api/item-list-bulk',
                                              {'gd_cd':base, 'limit':0}), missing)

    def save_response(self,name,data):
        if not isinstance(data,dict) or data.get('truncated') or not isinstance(data.get('rows'),list):
            raise ValueError(name+' 불완전 응답')
        if data.get('total_count') is not None and len(data['rows'])!=int(data['total_count']):
            raise ValueError(name+' 행 수 불일치')
        data=dict(data)
        if data.pop('_reused',False):
            return {'status':'success','completed_at':data['_collected_at'],'rows':len(data['rows']),'reused':True}
        data['_collected_at']=datetime.now().isoformat(timespec='seconds')
        target=self.cache/(name+'.json');tmp=target.with_suffix('.tmp')
        tmp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');tmp.replace(target)
        archive=self.cache/'snapshots'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        archive.mkdir(parents=True,exist_ok=True)
        with gzip.open(archive/(name+'.json.gz'),'wt',encoding='utf-8') as stream:json.dump(data,stream,ensure_ascii=False)
        return {'status':'success','completed_at':data['_collected_at'],'snapshot':str(archive/(name+'.json.gz')),'rows':len(data['rows'])}

    def refresh_stock(self):
        today=date.today().isoformat()
        for wh in WAREHOUSES:
            self._request('inventory_'+wh,'/api/inventory-ledger-product',
                          {'date_from':today,'date_to':today,'wh_nm':wh,'limit':0})

    def _stock_counts(self):
        with self._stock_cache_lock:
            paths=[self.cache/('inventory_'+wh+'.json') for wh in WAREHOUSES]
            signature=[]
            for wh,path in zip(WAREHOUSES,paths):
                try:
                    stat=path.stat();signature.append((stat.st_size,stat.st_mtime_ns))
                except OSError:raise ValueError(wh+' 재고 스냅샷 미수집')
            signature=tuple(signature)
            if signature==self._stock_cache_signature and self._stock_cache_value is not None:return self._stock_cache_value
            stock={};times=[]
            for wh,path in zip(WAREHOUSES,paths):
                data=json.loads(path.read_text('utf-8'));counts=collections.Counter()
                for x in data['rows']:
                    if x.get('wh_nm')==wh:counts[norm(x['gd_cd'])]+=float(x.get('stock_qty') or 0)
                stock[wh]=counts;times.append(str(data.get('_collected_at') or data.get('query_date') or '시각 미확인'))
            self._stock_cache_signature=signature;self._stock_cache_value=(stock,sorted(set(times)))
            return self._stock_cache_value

    def warm_source_cache(self):
        """Warm compact warehouse totals after the first view is visible."""
        self._stock_counts()

    def build(self, filters=None, *, allow_network=False, collecting=False):
        f=filters or {};key=self._filter_key(f);signature=self._source_signature()
        if not collecting:
            try:report=json.loads((self.cache/'refresh_status.json').read_text('utf-8'))
            except (OSError,ValueError):report={}
            if report and (report.get('status')!='success' or report.get('result',{}).get('status')!='success'):
                cached=self.retained_result(f)
                if cached is not None:return cached
                self._stock_counts()  # Name any missing warehouse immediately.
                raise ValueError('재고·실적·수화 지시 수집 대기 · 완료 후 자동으로 표시됩니다.')
        cached=self._cached_result(key,signature)
        if cached is not None:return cached
        # Fail before requesting hundreds of product specifications on a new PC.
        stock,times=self._stock_counts();hydration=self.hydration_service.load()
        if hydration.get('status')!='success':raise ValueError('수화 지시 스냅샷 미수집')
        hydration_qty=hydration.get('quantities') or {}
        live=read_tables(LIVE_DB_PATH,['aps_plan','cycle_meta'])
        bom=read_tables(DATA_CENTER_DIR/'bom/product_reference.sqlite',['product_name_master','bom_relation'])
        plans=live['aps_plan'];master={norm(x['nm_cd']):x for x in bom['product_name_master']}
        children=collections.defaultdict(set)
        for x in bom['bom_relation']:
            if x['use_yn']=='Y': children[norm(x['parent_cd'])].add(norm(x['child_cd']))
        demands=collections.defaultdict(list)
        for row in plans: demands[row['demand_id']].append(row)
        selected=[];bases=set();tokens=[x.strip().casefold() for x in f.get('search','').replace('，',',').split(',') if x.strip()]
        op=f.get('process','전체');markets=set(f.get('markets') or ['전체']);classes=set(f.get('classes') or ['전체'])
        for rows in demands.values():
            r=rows[0]
            if '전체' not in markets and _channel(r.get('demand_type'),r.get('dest_country')) not in markets: continue
            if f.get('due') and str(r.get('due_date') or '')[:10]>f['due']: continue
            has_shortage=any(float(x.get('plan_qty') or 0)>0 and (op=='전체' or x['oper_id']==op) and x['oper_id'] in ('10','20','45','55','80') for x in rows)
            if op!='전체' and not has_shortage:continue
            pcodes={norm(x['item_id']) for x in rows if norm(x['item_id']).startswith('P')}
            if '전체' not in classes and r.get('demand_group_id') not in classes: continue
            haystack=' '.join(str(x.get(k) or '') for x in rows for k in ('so_id','initial','demand_item_id','demand_item_name','item_id','item_name','power','demand_group_id')).casefold()
            if tokens and '*' not in tokens and not any(t in haystack for t in tokens): continue
            detail=str(f.get('detail_search') or '').casefold().strip()
            if detail and detail not in haystack: continue
            selected.extend(rows)
            if has_shortage:bases.update(c[:5] for c in pcodes)
        codes=set(bases)
        for b in bases:
            qs={c for c in children[b] if c.startswith('Q')};codes.update(qs)
            codes.update(c for q in qs for c in children[q] if c.startswith('R'))
            codes.update(c for c in children[b] if c.startswith('R'))
        codes.update(norm(x['item_id'])[:5] for x in selected if norm(x['item_id']).startswith(('Q','R')))
        missing=[b for b in codes if not (self.cache/('item_'+b+'.json')).exists()]
        if missing and not allow_network:
            raise ValueError(f'제품 규격 {len(missing)}종 수집 대기')
        self._warm_specs(missing)
        items={};byopt={};lookup={}
        for b in codes:
            data=json.loads((self.cache/('item_'+b+'.json')).read_text('utf-8'))
            rr={norm(x['gd_cd']):x for x in data['rows'] if norm(x.get('sale_cd'))==b}
            items[b]=rr;lookup.update(rr);byopt[b]=collections.defaultdict(set)
            for code,row in rr.items():byopt[b][specs(row)].add(code)
        need=collections.defaultdict(collections.Counter);due={}
        for x in selected:
            code=norm(x['item_id']);need[code][x['oper_id']]+=float(x.get('plan_qty') or 0)
            if float(x.get('plan_qty') or 0)>0:due[code]=min(due.get(code,'9999-12-31'),str(x.get('due_date') or '')[:10])
        explicit=collections.defaultdict(lambda:collections.defaultdict(set))
        for rows in demands.values():
            for pc in {norm(x['item_id']) for x in rows if norm(x['item_id']).startswith('P')}:
                for prefix in ('Q','R'):explicit[pc][prefix].update(norm(x['item_id']) for x in rows if norm(x['item_id']).startswith(prefix))
        def resolve(pc,prefix):
            if explicit[pc][prefix]:return sorted(explicit[pc][prefix])
            qs={c for c in children[pc[:5]] if c.startswith('Q')}
            cb=qs if prefix=='Q' else {c for q in qs for c in children[q] if c.startswith('R')}|{c for c in children[pc[:5]] if c.startswith('R')}
            candidates={v for b in cb for v in byopt.get(b,{}).get(specs(lookup[pc]),set())}
            return sorted(candidates) if len(candidates)==1 else []
        groups=collections.defaultdict(list);unmapped=0
        for b in sorted(bases):
            rr=items[b].copy();m=master.get(b,{})
            absent={norm(x['item_id']) for x in selected if norm(x['item_id']).startswith(b)}-set(rr)
            if absent:raise ValueError('ERP 전체규격 목록에 APS 품목 누락: '+', '.join(sorted(absent)[:5]))
            rows=[]
            for pc,x in sorted(rr.items(),key=lambda z:spec_key(z[1])):
                r=resolve(pc,'R');q=resolve(pc,'Q');note=[]
                if not r:note.append('R 연결 미확인')
                if not q:note.append('Q 연결 미확인')
                if not r or not q:unmapped+=1
                if str(x.get('stop_yn'))=='1':note.append('ERP 중지품목')
                hydration_instruction=float(hydration_qty.get(pc) or 0);inspection=stock['검사접착'][pc];leak=stock['누수규격검사'][pc]
                rows.append([m.get('full_gu_nm') or '분류미확인',m.get('nm_nm') or x.get('gd_nm') or '',spec_text(x),' / '.join(r) or '확인 필요',' / '.join(q) or '확인 필요',pc,
                    sum(stock['사출창고'][c] for c in r) if r else '미확인',sum(stock['분리창고'][c] for c in q) if q else '미확인',hydration_instruction,
                    inspection,leak,hydration_shortage(need[pc]['80'],leak,inspection,hydration_instruction),need[pc]['80'],due.get(pc,'')])
            groups[m.get('full_gu_nm') or '분류미확인'].append({'base':b,'rows':rows,'due':min((due[k] for k in rr if k in due),default='9999-12-31')})
        sheets=[{'name':c,'products':sorted(groups[c],key=lambda x:(x['due'],x['base']))} for c in sorted(groups,key=classification_sort_key)]
        allrows=[r for s in sheets for prod in s['products'] for r in prod['rows']]
        for col,oper in ((12,'80'),):
            expected=sum(float(x.get('plan_qty') or 0) for x in selected if x['oper_id']==oper)
            if abs(sum(r[col] for r in allrows)-expected)>.001:raise ValueError('P코드 부족수량 합계 불일치: '+oper)
        result={'sheets':sheets,'rows':len(allrows),'products':len(bases),'unmapped':unmapped,'cycle':live['cycle_meta'][0] if live['cycle_meta'] else {},
                'inventory_times':sorted(set(times)),'filters':f,'total80':sum(r[12] for r in allrows),'total45':sum(r[11] for r in allrows),
                'hydration':{**{k:v for k,v in hydration.items() if k!='quantities'},
                             'calculation_mode':'downstream_inventory_formula'}}
        final_signature=self._source_signature()
        if final_signature!=signature:raise ValueError('원천 데이터 갱신 중 · 완료 후 다시 표시됩니다.')
        result['_complete_cycle']=True
        self._save_result(key,signature,result)
        return result

    def retained_result(self,filters=None):
        """Only reuse an exact-filter, previously completed result; never stale raw inputs."""
        key=self._filter_key(filters or {})
        cached=self._memory_results.get(key)
        result=cached[1] if cached else None
        if result is None:
            try:
                with gzip.open(self._snapshot_path(key),'rt',encoding='utf-8') as stream:
                    payload=json.load(stream)
                if payload.get('filter_key')==key:result=payload.get('result')
            except (OSError,ValueError):pass
        if not result or not result.get('_complete_cycle'):return None
        return {**result,'_retained':True}

def export_inventory(result, filename):
    # Application exporter follows the existing application's XlsxWriter runtime.
    import xlsxwriter
    saved_at=datetime.now().astimezone().isoformat(timespec='seconds')
    cycle=result.get('cycle',{})
    inventory_times=' ~ '.join(result.get('inventory_times') or []) or '-'
    hydration=result.get('hydration') or {}
    hydration_basis=(f"{hydration.get('captured_at') or '-'} · {hydration.get('rows',0):,}건 · {hydration.get('total_qty',0):,.0f}"
                     if hydration.get('status')=='success' else 'API 미연결 · 임시 0(-)')
    with xlsxwriter.Workbook(str(filename)) as book:
        pink=book.add_format({'bg_color':'#F5C8F0','bold':True,'border':1,'align':'center','text_wrap':True})
        product_group=book.add_format({'bg_color':'#E7EEF8','bold':True,'border':1,'align':'center'})
        stock_group=book.add_format({'bg_color':'#DDEFEA','bold':True,'border':1,'align':'center'})
        shortage_group=book.add_format({'bg_color':'#F4E4E7','bold':True,'border':1,'align':'center'})
        normal=book.add_format({'font_name':'맑은 고딕','font_size':10,'border':1,'border_color':'#D3D3D3'})
        qty=book.add_format({'font_name':'맑은 고딕','font_size':10,'border':1,'border_color':'#D3D3D3','num_format':'#,##0;[Red](#,##0);"-"'})
        power=book.add_format({'bg_color':'#BCE0F3','align':'center','border':1,'border_color':'#D3D3D3'})
        green=book.add_format({'bg_color':'#D6FFA2','border':1,'border_color':'#D3D3D3'})
        red=book.add_format({'font_color':'#B91C1C','bg_color':'#FFF1F1','bold':True})
        # Keep workbook tabs in the same business order used by the app and
        # documented in Obsidian, regardless of the incoming result order.
        export_groups=sorted(result['sheets'],key=lambda x:classification_sort_key(x.get('name'))) or [{'name':'조회결과','products':[]}]
        for group in export_groups:
            sh=book.add_worksheet(re.sub(r'[\\/*?:\[\]]','_',group['name'])[:31])
            # Clear families use blue tabs and Color families use magenta tabs
            # so the two blocks remain obvious when many sheets are open.
            sh.set_tab_color('#B14688' if 'color' in str(group['name']).casefold() else '#3C78B5')
            sh.hide_gridlines(2);sh.set_zoom(85);sh.set_column('A:A',22);sh.set_column('B:B',49);sh.set_column('C:C',25)
            for code_column in ('D:D','E:E','F:F'):sh.set_column(code_column,27,None,{'hidden':True})
            sh.set_column('G:M',16);sh.set_column('N:N',14)
            sh.merge_range('A1:N1',group['name']+' · 재고 현황',pink)
            sh.merge_range('A2:N2','저장 시각: '+saved_at+' / APS 갱신: '+str(cycle.get('aps_source_refreshed_at','-')),normal)
            sh.merge_range('A3:N3','실시간 부족 계산: '+str(cycle.get('current_captured_at','-'))+' / WIP 갱신: '+str(cycle.get('wip_source_refreshed_at','-')),normal)
            sh.merge_range('A4:N4','재고 수집: '+inventory_times+' / 수화 지시: '+hydration_basis,normal)
            sh.merge_range('A5:N5','적용 조건: '+json.dumps(result.get('filters',{}),ensure_ascii=False)+' / 화면 검색·간략히 보기·정렬 제외',normal)
            # Match the on-screen two-row grouped header. Standalone fields
            # span both rows; only code, inventory, and shortage have children.
            for column in ('A','B','C','N'):
                index={'A':0,'B':1,'C':2,'N':13}[column]
                sh.merge_range(f'{column}7:{column}8',HEADERS[index],product_group)
            sh.merge_range('D7:F7','제품 코드',product_group)
            sh.write_row('D8',HEADERS[3:6],pink)
            sh.merge_range('G7:K7','재고',stock_group)
            sh.write_row('G8',HEADERS[6:11],pink)
            sh.merge_range('L7:M7','부족수량',shortage_group)
            sh.write_row('L8',HEADERS[11:13],pink)
            sh.set_row(6,22);sh.set_row(7,32)
            at=8
            for prod in group['products']:
                for row in prod['rows']:
                    for c,value in enumerate(row):sh.write(at,c,value,power if c==2 else green if c==1 else qty if 6<=c<=12 else normal)
                    at+=1
            sh.autofilter(7,0,max(7,at-1),13);sh.freeze_panes(8,2);sh.set_selection('A9')
            if at>8:sh.conditional_format(8,11,at-1,12,{'type':'cell','criteria':'>','value':0,'format':red})
