"""Allowlisted obsolete local artifacts; operating databases and user files stay put.

Called in a separate cleanup process. Each retirement briefly takes the exact
collector lock; bulk deletion runs after that lock is released. A durable record
allows interrupted deletion to resume without touching published paths again.
"""
from datetime import datetime
import json
from pathlib import Path
import re
import time
import uuid

from services import delivery_stage_cleanup as core

POLICIES = (
    ('bom/backup', 'product_reference_before_*.sqlite', 1),
    ('bom/raw_api', 'bom_api_*.json.gz', 1),
    ('production-performance/backup', 'production_performance_before_*.sqlite', 1),
    ('production-performance/raw_api', 'production_*.json.gz', 2),
    ('backward-v1/process-status/backup', 'aps_process_status_before_*.sqlite', 1),
    ('backward-v1/process-status/raw_api', 'aps_s_factory_*.json.gz', 1),
    ('backward-v1/live-production-need/backup', 'current_production_need_before_*.sqlite', 1),
    ('backward-v1/inventory-status/view-snapshots', '*.json.gz', 4),
)
SNAPSHOT_DIRS = ('backward-v1/inventory-status/snapshots', 'inventory-status/snapshots')


def _signature(path):
    core.plain_path(path)
    if path.is_dir():
        files, dirs, latest = core.inventory(path)
        return [(str(p.relative_to(path)), n, m, i) for p,n,m,i in files], latest, sum(f[1] for f in files)
    s = path.stat()
    return [(path.name, s.st_size, s.st_mtime_ns, s.st_ino)], s.st_mtime, s.st_size


def candidates(root, state):
    """No network, database writes, recursive whole-drive scan or content hashes."""
    root = Path(root)
    cutoff = min(time.time()-core.QUIET_SECONDS, datetime.fromisoformat(state['applied_at']).timestamp())
    selected = []
    for relative, pattern, keep in POLICIES:
        directory = root/relative
        core.plain_path(directory)
        files = sorted((p for p in directory.glob(pattern) if p.is_file()), key=lambda p:p.stat().st_mtime, reverse=True)
        selected.extend(files[keep:])
    # Superseded local calculation groups and raw response archives. The old
    # Plan cache is included only at this precise generated-snapshot location.
    for relative in SNAPSHOT_DIRS:
        directory = root/relative
        core.plain_path(directory)
        for folder in directory.glob('*'):
            core.plain_path(folder)
            if not folder.is_dir() or not re.fullmatch(r'\d{8}_\d{6}_\d{6}',folder.name):
                continue
            if (folder/'collection-group.json').is_file():
                marker = core.read_json(folder/'collection-group.json')
                if marker.get('cycle') == folder.name:
                    selected.append(folder)
            else:
                entries = list(folder.iterdir())
                allowed = lambda p: p.is_file() and (re.fullmatch(r'(inventory_.+|item_.+|hydration_instructions)\.json\.gz',p.name) or p.name in ('refresh_status.json','inventory_result.json','current_production_need.sqlite'))
                if entries and all(allowed(p) for p in entries):
                    selected.append(folder)
    cache = root/'backward-v1/dmz-cache'
    core.plain_path(cache)
    files = sorted(cache.glob('*.json.gz'),key=lambda p:p.stat().st_mtime,reverse=True)
    kept_bytes = 0
    for p in files:
        core.plain_path(p)
        if not p.is_file():continue
        kept_bytes += p.stat().st_size
        if kept_bytes > 512*1024**2 or time.time()-p.stat().st_mtime > 2*core.DAY:
            selected.append(p)
    result = []
    for path in selected:
        try:
            signature, latest, size = _signature(path)
            if latest < cutoff:
                result.append((path,signature,size))
        except (OSError,ValueError):
            continue
    return result


def _delete_retired(root, record):
    core.plain_path(record)
    data = core.read_json(record/'record.json')
    if data.get('application') != 'DdokddakProduction3' or data.get('owner') != str(root.resolve()):
        raise ValueError('분리 자료 소유 정보 불일치')
    trash = root/'settings/cleanup-retired'
    if record.resolve().parent != trash.resolve() or not re.fullmatch(r'[a-f0-9]{32}',record.name):
        raise ValueError('분리 자료 범위 밖')
    payload = record/'payload'
    deleted = 0
    if payload.exists():
        if payload.is_dir():
            files,dirs,_ = core.inventory(payload)
        else:
            core.plain_path(payload)
            s=payload.stat();files=[(payload,s.st_size,s.st_mtime_ns,s.st_ino)];dirs=[]
        for p,size,modified,inode in files:
            core.plain_path(p)
            s=p.stat()
            if (s.st_size,s.st_mtime_ns,s.st_ino)!=(size,modified,inode):
                raise ValueError('분리 자료 변경 감지')
            p.unlink();deleted+=size
        for p in dirs:p.rmdir()
        if payload.exists():payload.rmdir()
    (record/'record.json').unlink();record.rmdir()
    return {'path':data['original'],'status':'deleted','deleted_bytes':deleted,'kind':'local'}


def cleanup_local(root, *, budget_seconds=20):
    root=Path(root)
    state=core.operational_state(root)
    # Local groups can be recovery inputs after a partial/manual collection.
    # Only a fully successful new delivery makes their role obsolete.
    if state.get('status')!='success' or not state.get('publication_verified'):
        return []
    started=time.monotonic();results=[]
    trash=root/'settings/cleanup-retired';core.plain_path(trash)
    for record in list(trash.glob('*')):
        if time.monotonic()-started>budget_seconds:return results
        try:results.append(_delete_retired(root,record))
        except (OSError,ValueError,KeyError,TypeError):continue
    for path,signature,size in candidates(root,state):
        if time.monotonic()-started>budget_seconds:
            results.append({'status':'deferred','deleted_bytes':0,'kind':'local','reason':'다음 주기에 이어서 정리'})
            break
        try:
            with core.collection_guard(root):
                latest=core.operational_state(root)
                if latest!=state:break
                # A manual inventory collection may have failed since delivery.
                report=root/'backward-v1/inventory-status/refresh_status.json'
                if report.exists() and core.read_json(report).get('status')!='success':break
                if path.resolve()==root.resolve() or not path.resolve().is_relative_to(root.resolve()):
                    raise ValueError('정리 범위 밖')
                if _signature(path)[0]!=signature:continue
                record=trash/uuid.uuid4().hex
                core.plain_path(record);record.mkdir(parents=True)
                (record/'record.json').write_text(json.dumps({'application':'DdokddakProduction3',
                    'owner':str(root.resolve()),'original':str(path)},ensure_ascii=False),encoding='utf-8')
                path.rename(record/'payload')
            results.append(_delete_retired(root,record))
        except core.BusyError:
            results.append({'path':str(path),'status':'deferred','deleted_bytes':0,'kind':'local'})
            break
        except (OSError,ValueError) as exc:
            results.append({'path':str(path),'status':'protected','deleted_bytes':0,'kind':'local','error':str(exc)})
    return results
