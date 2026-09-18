"""One refresh transaction report for live need, LOT and full-spec inventory."""
import sys,json,sqlite3,traceback
from pathlib import Path
from datetime import datetime,date
from concurrent.futures import ThreadPoolExecutor
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from services.inventory_status_service import InventoryStatusService,WAREHOUSES
from services.hydration_instruction_service import HydrationInstructionService
from services.live_production_need_service import DB_PATH,STATUS_PATH

def main():
    service=InventoryStatusService();stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f');folder=service.cache/'snapshots'/stamp;folder.mkdir(parents=True,exist_ok=True)
    report={'cycle_id':stamp,'started_at':datetime.now().isoformat(timespec='seconds'),'sources':{}}
    today=date.today().isoformat()
    def stock(wh):
        try:
            outcome=service._request('inventory_'+wh,'/api/inventory-ledger-product',{'date_from':today,'date_to':today,'wh_nm':wh,'limit':0})
            return wh,outcome
        except Exception as exc:return wh,{'status':'error','message':str(exc),'retained':True}
    # Start all displayed warehouse calls and hydration instructions in the same
    # collection cycle. Live WIP/performance runs concurrently on this thread;
    # the inventory result is built only after every input has settled.
    with ThreadPoolExecutor(max_workers=len(WAREHOUSES)+1) as pool:
        jobs=[pool.submit(stock,wh) for wh in WAREHOUSES]
        hydration_job=pool.submit(HydrationInstructionService(service.cache).refresh)
        try:
            from collectors.live_production_need_collector import main as collect_live
            code=collect_live()
            status=json.loads(STATUS_PATH.read_text('utf-8')) if STATUS_PATH.exists() else {}
            report['live']={'status':status.get('status','error'),'exit_code':code,'completed_at':status.get('refreshed_at'),'message':status.get('retained_reason','')}
        except Exception as exc:report['live']={'status':'error','message':str(exc)}
        for job in jobs:
            wh,outcome=job.result();report['sources'][wh]=outcome
        try:report['hydration']=hydration_job.result()
        except Exception as exc:report['hydration']={'status':'error','message':str(exc),'retained':True}
    if DB_PATH.exists():
        with sqlite3.connect(DB_PATH.as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(folder/'current_production_need.sqlite') as dst:src.backup(dst)
    try:
        data=service.build();(folder/'inventory_result.json').write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        tmp=service.cache/'latest_result.tmp';tmp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');tmp.replace(service.cache/'latest_result.json')
        report['result']={'status':'success','rows':data['rows'],'products':data['products']}
    except Exception as exc:report['result']={'status':'error','message':str(exc)}
    report['completed_at']=datetime.now().isoformat(timespec='seconds')
    all_ok=(report.get('live',{}).get('status')=='success' and report.get('hydration',{}).get('status')=='success'
            and all(x.get('status')=='success' for x in report.get('sources',{}).values()))
    report['status']='success' if all_ok else 'partial'
    report['snapshot']=str(folder)
    for target in (folder/'refresh_status.json',service.cache/'refresh_status.json'):
        tmp=target.with_suffix('.tmp');tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(target)
    print(json.dumps(report,ensure_ascii=False),flush=True)
    # Partial results remain usable; each source failure is exposed in the UI manifest.
    return 0

if __name__=='__main__':raise SystemExit(main())
