"""One-time consistent backup before v2.8.1 first changes shared collector data."""
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time


def ensure_recovery_backup(root: Path) -> Path:
    root=Path(root)
    folder=root/'recovery'/'before-2.8.1'
    manifest=folder/'manifest.json'
    if manifest.exists():
        data=json.loads(manifest.read_text(encoding='utf-8'))
        if data.get('complete') is True:
            return folder
    folder.mkdir(parents=True,exist_ok=True)
    candidates=[]
    for name in ('bom','process-status','production-performance','live-production-need'):
        candidates.extend((root/name).glob('*.sqlite'))
    candidates.extend((root/'settings').glob('*.json'))
    candidates.extend((root/'inventory-status').glob('*config*.json'))
    files=[]
    for source in candidates:
        if '.building.' in source.name: continue
        relative=source.relative_to(root)
        destination=folder/relative
        destination.parent.mkdir(parents=True,exist_ok=True)
        temporary=destination.with_suffix(destination.suffix+'.tmp')
        if source.suffix=='.sqlite':
            deadline=time.monotonic()+45
            def progress(*_):
                if time.monotonic()>deadline:
                    raise TimeoutError('복구용 DB 백업이 지연되어 수집을 보류합니다.')
            with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as src, closing(sqlite3.connect(temporary)) as dst:
                src.backup(dst,pages=256,progress=progress)
                if dst.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                    raise ValueError('복구용 DB 백업 무결성 오류')
        else:
            shutil.copy2(source,temporary)
        temporary.replace(destination)
        files.append({'path':relative.as_posix(),'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()})
    data={'complete':True,'version':'2.8.1','recovery_version':'2.7.10','created_at':datetime.now().astimezone().isoformat(),'files':files}
    temporary=manifest.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(manifest)
    return folder
