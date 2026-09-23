"""Read-only, sequential full-response timing; store evidence outside live data."""
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from services.api_credentials import api_headers
import requests


def main():
    today = date.today()
    output = ROOT / 'qa' / ('api-check-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    output.mkdir(parents=True)
    jobs = [('aps_meta', '/api/aps-plan/meta', {}),
            ('hydration', '/api/hydration-job-list', {'date_from':str(today-timedelta(days=4)), 'date_to':str(today),'limit':0}),
            ('product_names','/api/product-names',{'limit':0}),
            ('bom','/api/bom-explosion',{'limit':0}),
            ('production','/api/production-performance',{'date_from':str(today-timedelta(days=6)),'date_to':str(today),'limit':0})]
    for warehouse in ['사출창고','분리창고','검사접착','누수규격검사','규격검사완료']:
        jobs.append(('inventory_'+warehouse,'/api/inventory-ledger-product',{'date_from':str(today-timedelta(days=1)),'date_to':str(today),'wh_nm':warehouse,'limit':0}))
        jobs.append(('wip_'+warehouse,'/api/aps-wip',{'wh_name':warehouse,'limit':0}))
    results=[]
    with requests.Session() as session:
        session.headers.update(api_headers())
        for name, endpoint, params in jobs:
            started=time.monotonic()
            result={'name':name,'endpoint':endpoint,'params':params,'started_at':datetime.now().astimezone().isoformat()}
            try:
                with session.get('https://plan.interojo.net'+endpoint,params=params,timeout=(5,60),allow_redirects=False) as response:
                    result.update(http_status=response.status_code,bytes=len(response.content))
                    response.raise_for_status()
                    response.encoding='utf-8'
                    payload=response.json()
                    rows=payload.get('rows',[])
                    result.update(rows=len(rows),total_count=payload.get('total_count'),truncated=payload.get('truncated'),source_refreshed_at=payload.get('source_refreshed_at'))
                    (output/(name+'.json')).write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
            except Exception as exc:
                result['error_type']=type(exc).__name__
            result['seconds']=round(time.monotonic()-started,3)
            results.append(result)
            (output/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(result,ensure_ascii=True),flush=True)
    print('Evidence: '+str(output),flush=True)

if __name__=='__main__': main()
