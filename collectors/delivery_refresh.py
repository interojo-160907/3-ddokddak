"""Receive a paired server release in isolation, publish after all calculations succeed."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime
from contextlib import closing

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from services.data_location import resolve_data_root
from services.collection_staging import publish_group,recover_publication
from services.collection_lock import serialized_collection

FOLDERS=('bom','production-performance','backward-v1/process-status',
         'backward-v1/live-production-need','backward-v1/inventory-status','backward-v1/settings','backward-v1/dmz-cache')
EXCLUDE={'backup','backups','snapshots','raw_api','before-publication','recovery'}


def copy_inputs(source,destination):
    for folder in FOLDERS:
        base=source/folder
        if not base.exists():continue
        for file in base.rglob('*'):
            if not file.is_file() or EXCLUDE.intersection(file.relative_to(base).parts):continue
            if file.name.endswith(('-wal','-shm','.lock','.tmp')):continue
            target=destination/file.relative_to(source);target.parent.mkdir(parents=True,exist_ok=True)
            if file.suffix in ('.sqlite','.db'):
                with closing(sqlite3.connect(file.as_uri()+'?mode=ro',uri=True)) as a,closing(sqlite3.connect(target)) as b:a.backup(b)
            else:shutil.copy2(file,target)


@serialized_collection
def main():
    from services.dmz_read_client import connection
    from services.delivery_groups import read_pair,head_status
    root=resolve_data_root();settings=root/'backward-v1/settings';settings.mkdir(parents=True,exist_ok=True)
    status_path=settings/'delivery_status.json'
    journal=settings/'delivery_publication.json';recover_publication(journal)
    previous=json.loads(status_path.read_text('utf-8')) if status_path.exists() else {}
    report=dict(previous,checked_at=datetime.now().astimezone().isoformat())
    def save():
        tmp=status_path.with_suffix('.tmp');tmp.write_text(json.dumps(report,ensure_ascii=False,default=str,indent=2),encoding='utf-8');tmp.replace(status_path)
    try:
        report['server']=head_status(connection)
        report['connection_status']='success'
        latest_aps=next((h for h in report['server']['groups'] if h['group_no']==1),None)
        if latest_aps:
            from collectors.aps_display_refresh import receive_aps_view
            try:
                report['aps_view']=receive_aps_view(root,latest_aps)
                report.pop('aps_view_error',None)
            except Exception as exc:
                report['aps_view_error']=str(exc)
            save()
        aps,data=read_pair(connection)
        if latest_aps and latest_aps['version']!=aps['version']:
            raise ValueError('새 APS 조회 가능 · 같은 회차 생산 현황 준비 중 · 이전 계산 유지')
        report['available_version']=data['version']
        if previous.get('applied_version')==data['version']:
            pending=any(h['group_no']==1 and h['version']!=aps['version'] for h in report['server']['groups'])
            report.update(status='waiting_source' if pending else 'success',message='새 APS의 생산 현황 준비 중 · 이전 완성본 유지' if pending else '양호');save();return 0
        report.update(status='receiving',message='수집계산중');save()
        stage=Path(tempfile.mkdtemp(prefix='prod3-delivery-'))
        marker=stage/'delivery-owner.json'
        marker.write_text(json.dumps({'owner':str(root.resolve()),'complete':False}),encoding='utf-8')
        report['stage_path']=str(stage);save()
        copy_inputs(root,stage)
        env=os.environ.copy();env['DDOKDDAK_PROD3_STAGE_ROOT']=str(stage)
        env['DDOKDDAK_PROD3_TRANSPORT']='dmz';env['DDOKDDAK_DELIVERY_VERSION']=data['version']
        env.pop('DDOKDDAK_APS_VIEW_VERSION',None)
        command=[sys.executable]+([] if getattr(sys,'frozen',False) else [str(ROOT/'gui_app_pyside6.py')])+['--collector','refresh_all']
        if '--full' in sys.argv[1:]:command.append('--full')
        result=subprocess.run(command,env=env,capture_output=True,timeout=1200,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        (stage/'receive.log').write_bytes(result.stdout+result.stderr)
        if result.returncode!=0:
            raise ValueError('완성본 계산 확인 필요 · 기존 자료 유지 (수신 로그 보관)')
        full=json.loads((stage/'backward-v1/settings/full_refresh_status.json').read_text('utf-8'))
        if full.get('results',{}).get('live',{}).get('status')!='success':raise ValueError('생산 현황 계산 미완료 · 기존 유지')
        pairs=[]
        for folder in FOLDERS:
            base=stage/folder
            if not base.exists():continue
            for file in base.rglob('*'):
                if not file.is_file() or EXCLUDE.intersection(file.relative_to(base).parts):continue
                if file.name in ('delivery_status.json','delivery_publication.json') or file.name.endswith(('-wal','-shm','.lock','.tmp')):continue
                # Settings may have changed in the GUI while this copy calculated.
                if folder=='backward-v1/settings' and file.name!='full_refresh_status.json':continue
                if file.suffix=='.json':
                    def relocate(value):
                        if isinstance(value,dict):return {k:relocate(v) for k,v in value.items()}
                        if isinstance(value,list):return [relocate(v) for v in value]
                        if isinstance(value,str) and value.startswith(str(stage)):return str(root)+value[len(str(stage)):]
                        return value
                    file.write_text(json.dumps(relocate(json.loads(file.read_text('utf-8'))),ensure_ascii=False,default=str),encoding='utf-8')
                pairs.append((file,root/file.relative_to(stage)))
        publish_group(pairs,stage,journal)
        report.update(status='success',message='양호',applied_version=data['version'],
                      aps_version=aps['version'],aps_key=aps['aps_key'],started_at=data['started_at'],
                      received_at=data['received_at'],applied_at=datetime.now().astimezone().isoformat())
        save()
        marker.write_text(json.dumps({'owner':str(root.resolve()),'complete':True}),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,default=str));return 0
    except Exception as exc:
        (settings/'delivery_error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        server_state = report.get('server', {}).get('state') or {}
        report['package_details'] = server_state.get('details') or {}
        message = str(exc)
        if report['package_details'].get('reason'):
            message += ' · ' + str(server_state.get('message') or report['package_details']['reason'])
        report.update(status='waiting_source',message=message);save()
        print(json.dumps(report,ensure_ascii=False,default=str));return 2

if __name__=='__main__':raise SystemExit(main())
