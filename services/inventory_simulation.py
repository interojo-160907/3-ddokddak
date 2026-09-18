"""Offline release simulation: real collectors/SQLite/views, isolated API replies.

Only invoked by regression tests and --package-smoke-test. All production paths
and transports are replaced for the lifetime of the simulation.
"""
from contextlib import ExitStack, closing
from datetime import datetime
import gc
import io
import json
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse


def run() -> dict:
    from collectors import inventory_live_refresh as batch
    from collectors import live_production_need_collector as live
    from collectors.process_status_collector import _initialize
    from services import inventory_status_service as inventory
    from services import hydration_instruction_service as hydration
    from services import live_production_need_service as live_service

    started = datetime.now()
    calls = []
    failed = set()
    truncated = set()
    empty = set()
    cycle = datetime.now().strftime('%Y-%m-%d') + ' 00:00:00'
    codes = {'P1000':'P1000A-03.00', 'Q1000':'Q1000-03.00', 'R1000':'R1000-03.00'}
    warehouses = [name for _,_,name in live.WAREHOUSES]

    def response(endpoint, params):
        if endpoint == '/api/aps-wip':
            return {'source_refreshed_at':cycle,'total_count':0,'rows':[]}
        if endpoint == '/api/production-performance':return {'total_count':0,'rows':[]}
        if endpoint == '/api/inventory-ledger-product':
            wh = params['wh_nm'];calls.append(wh)
            if wh in failed:raise TimeoutError('simulated warehouse timeout')
            pc, qty = {'사출창고':('R1000',2111),'분리창고':('Q1000',24458),
                       '검사접착':('P1000',1000),'누수규격검사':('P1000',1770),
                       '규격검사완료':('P1000',0)}[wh]
            rows = [] if wh in empty else [{'gd_cd':codes[pc],'wh_nm':wh,'stock_qty':qty,'lot_no':''}]
            return {'total_count':len(rows),'rows':rows,'truncated':wh in truncated}
        if endpoint == '/api/hydration-job-list':
            return {'total_count':1,'rows':[{'gd_cd':codes['P1000'],'job_qty':2000,'job_no':'TEST1','job_seq':1}]}
        if endpoint == '/api/item-list-bulk':
            base = params['gd_cd']
            return {'total_count':1,'rows':[{'gd_cd':codes[base],'sale_cd':base,'spec30':-3,
                'spec40':None,'spec50':None,'spec60':None,'gd_nm':'TEST PRODUCT','stop_yn':'0'}]}
        raise AssertionError('Unexpected external request: '+endpoint)

    class Reply:
        encoding = 'utf-8'
        def __init__(self,payload):self.payload=payload
        def raise_for_status(self):pass
        def json(self):return self.payload
        def close(self):pass

    def get(url, *, params, **kwargs):return Reply(response(urlparse(url).path,params))
    def urlopen(request, **kwargs):
        parsed=urlparse(request.full_url)
        params={k:v[0] for k,v in parse_qs(parsed.query).items()}
        return io.BytesIO(json.dumps(response(parsed.path,params)).encode())

    with tempfile.TemporaryDirectory(prefix='production3-upgrade-simulation-') as directory, ExitStack() as stack:
        root=Path(directory);data=root/'live-production-need';data.mkdir()
        (data/'snapshot').mkdir();(data/'backup').mkdir()
        aps=root/'aps.sqlite';database=data/'current_production_need.sqlite';cache=root/'inventory-status';cache.mkdir()
        bom=root/'bom';bom.mkdir()
        # Model a 2.6.4 PC: existing APS/BOM; no inventory or hydration folders,
        # old placeholder configuration, and no version bootstrap marker.
        (cache/'hydration_api_config.json').write_text(json.dumps({'endpoint':'','lookback_days':31}),encoding='utf-8')
        sentinel=root/'settings-user.txt';sentinel.write_text('keep existing user settings',encoding='utf-8')
        with closing(sqlite3.connect(aps)) as con, con:
            _initialize(con)
            con.execute("INSERT INTO sync_meta VALUES(1,'S관',4,4,1,?,?, '')",(cycle,cycle))
            for oper,base in [('10','R1000'),('20','Q1000'),('45','P1000'),('80','P1000')]:
                con.execute('INSERT INTO aps_plan(oper_id,item_id,plan_qty,demand_id,demand_group_id,so_id,due_date,seq,payload_json) VALUES(?,?,?,?,?,?,?,?,?)',
                            (oper,codes[base],13970,'DEMAND1','1-Day_Sph','ORDER1','2026-12-31',1,'{}'))
        with closing(sqlite3.connect(bom/'product_reference.sqlite')) as con, con:
            con.executescript("CREATE TABLE product_name_master(nm_cd TEXT,nm_nm TEXT,full_gu_nm TEXT); CREATE TABLE bom_relation(parent_cd TEXT,child_cd TEXT,use_yn TEXT);")
            con.execute("INSERT INTO product_name_master VALUES('P1000','TEST PRODUCT','1-Day_Sph')")
            con.executemany('INSERT INTO bom_relation VALUES(?,?,?)',[('P1000','Q1000','Y'),('Q1000','R1000','Y')])
        for module,attributes in [
            (live,{'APS_DB_PATH':aps,'DATA_DIR':data,'DB_PATH':database,'STATUS_PATH':data/'snapshot/refresh_status.json','BACKUP_DIR':data/'backup'}),
            (batch,{'DB_PATH':database,'STATUS_PATH':data/'snapshot/refresh_status.json'}),
            (inventory,{'DATA_CENTER_DIR':root,'DATA_DIR':root/'legacy','LIVE_DB_PATH':database}),
            (live_service,{'DATA_DIR':data,'DB_PATH':database,'STATUS_PATH':data/'snapshot/refresh_status.json'}),
        ]:
            for key,value in attributes.items():stack.enter_context(patch.object(module,key,value))
        stack.enter_context(patch.dict('os.environ',{'DDOKDDAK_INVENTORY_STATUS_DATA_DIR':str(cache)}))
        stack.enter_context(patch.object(live.requests,'get',side_effect=get))
        stack.enter_context(patch.object(inventory.urllib.request,'urlopen',side_effect=urlopen))
        for module in (inventory,hydration,batch):stack.enter_context(patch.object(module,'credential_value',return_value=''))
        stack.enter_context(patch.object(live.time,'sleep',return_value=None))

        failed.add('사출창고')
        first=batch.refresh()
        assert first['status']=='partial' and first['result']['status']=='retained'
        assert not database.exists(), 'Failed first collection must not publish incomplete DB'
        assert first['sources']['분리창고']['status']=='success', 'Continue after one failed warehouse'
        assert warehouses[1:]==calls[-4:], calls
        saved_time=first['sources']['분리창고']['completed_at']
        failed.clear();calls.clear()
        second=batch.refresh()
        assert second['status']=='success', second
        assert calls==['사출창고'], calls
        assert second['sources']['분리창고']['completed_at']==saved_time
        service=inventory.InventoryStatusService()
        result=service.build()
        row=result['sheets'][0]['products'][0]['rows'][0]
        assert result['rows']==1 and row[11:13]==[9200.0,13970.0],row
        assert service.hydration_service.config()['lookback_days']==5
        assert sentinel.read_text('utf-8')=='keep existing user settings'
        old_database=database.read_bytes();old_result=(cache/'latest_result.json').read_bytes()

        # Truncated HTTP success is still failure; preserve the published DB/view.
        truncated.add('분리창고');calls.clear()
        third=batch.refresh()
        assert third['status']=='partial'
        assert database.read_bytes()==old_database and (cache/'latest_result.json').read_bytes()==old_result
        assert service.build()['_retained']
        assert service.build()['sheets']==result['sheets']
        try:service.build({'search':'DIFFERENT FILTER'})
        except ValueError:pass
        else:raise AssertionError('Must never substitute a cached result for a different filter')

        # A successful zero-row response replaces old nonzero stock, and the
        # Windows file is replaceable immediately with garbage collection off.
        truncated.clear();empty.add('분리창고');calls.clear()
        fourth=batch.refresh()
        assert fourth['status']=='success',fourth
        assert calls==['분리창고'],calls
        assert service.build()['sheets'][0]['products'][0]['rows'][0][7]==0
        was_enabled=gc.isenabled();gc.disable()
        try:
            inventory.read_tables(database,['aps_plan','cycle_meta'])
            try:inventory.read_tables(database,['does_not_exist'])
            except sqlite3.OperationalError:pass
            replacement=data/'replace-check.sqlite';replacement.write_bytes(database.read_bytes())
            replacement.replace(database)
        finally:
            if was_enabled:gc.enable()

        calls.clear();fifth=batch.refresh()
        assert fifth['status']=='success' and calls==warehouses
        # Render the real inventory page against the collected data.
        from PySide6.QtWidgets import QApplication
        from ui.inventory_master_page import InventoryStatusPage
        import time
        app=QApplication.instance() or QApplication([])
        page=InventoryStatusPage(lambda:{})
        deadline=time.monotonic()+10
        while time.monotonic()<deadline and not page.result:
            app.processEvents()
            if page.future is not None and page.future.done():page.finish()
        assert page.result and page.model.rowCount()==1, page.inventory_status.text()
        page.resize(1200,650);page.show();app.processEvents()
        assert not page.grab().isNull()
        page.debounce.stop();page.poll.stop();page.executor.shutdown(wait=True);page.close();page.deleteLater();app.processEvents()

    return {'seconds':round((datetime.now()-started).total_seconds(),3),
        'checks':['2.6.4-shaped data upgrade','first-run timeout recovery','sequential shared warehouse requests',
                  'retry failed warehouse only','preserve source timestamps','zero stock',
                  'truncated response rejected','last valid view retained','exact filter retention',
                  'Windows SQLite handle release','automatic repeat collection','hydration formula','real inventory table render']}
