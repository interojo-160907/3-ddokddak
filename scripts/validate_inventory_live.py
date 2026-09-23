"""Real API end-to-end check with a fresh isolated data root (no registry writes)."""
import json
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services import data_location
source=data_location.resolve_data_root()
target=ROOT/'qa'/('live-validation-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
target.mkdir(parents=True)
data_location.resolve_data_root=lambda: target
import config
config.ensure_directories()
for relative in ['bom/product_reference.sqlite']:
    src=source/relative; dst=target/relative
    with closing(sqlite3.connect(src.as_uri()+'?mode=ro',uri=True)) as a, closing(sqlite3.connect(dst)) as b:
        a.backup(b)
print('ISOLATED '+str(target),flush=True)
from collectors import process_status_collector as aps
from collectors import inventory_live_refresh as inventory
from services import erp_api_client
original=erp_api_client._session
class TimedSession:
    def __init__(self,session): self.session=session
    def get(self,url,**kwargs):
        start=time.monotonic()
        try:
            result=self.session.get(url,**kwargs)
            print(json.dumps({'endpoint':url.split('?')[0],'http':result.status_code,'seconds':round(time.monotonic()-start,2)},ensure_ascii=True),flush=True)
            return result
        except Exception as exc:
            print(json.dumps({'endpoint':url.split('?')[0],'error':type(exc).__name__,'seconds':round(time.monotonic()-start,2)}),flush=True)
            raise
erp_api_client._session=lambda:TimedSession(original())
start=time.monotonic()
report={'aps':aps.refresh(timeout=60)}
print('APS '+json.dumps(report['aps'],ensure_ascii=True),flush=True)
report['inventory']=inventory.refresh()
report['elapsed_seconds']=round(time.monotonic()-start,2)
(target/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('RESULT '+json.dumps(report,ensure_ascii=True),flush=True)
raise SystemExit(0 if report['inventory']['status']=='success' else 2)
