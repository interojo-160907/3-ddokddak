"""Prune marked updater cache only, never the user's Downloads folder."""
import os
from pathlib import Path
import re
import tempfile
import time

from services.delivery_stage_cleanup import plain_path, read_json, DAY


def version(value):
    text=str(value).lstrip('vV')
    if not re.fullmatch(r'\d+\.\d+\.\d+',text):return None
    return tuple(map(int,text.split('.')))


def cleanup_downloads(app_version, roots=None):
    current=version(app_version)
    if current is None:return []
    roots=roots if roots is not None else (
        Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir())/'DdokddakProduction3/updates',
        Path(tempfile.gettempdir())/'DdokddakProduction3/updates')
    candidates=[]
    for base in dict.fromkeys(Path(p).absolute() for p in roots):
        if not base.exists():continue
        try:plain_path(base)
        except (OSError,ValueError):continue
        for folder in base.glob('download-*'):
            try:
                plain_path(folder)
                if not folder.is_dir() or folder.resolve().parent!=base.resolve():continue
                marker=read_json(folder/'production3-download.json')
                target=version(marker.get('version'))
                name=marker.get('installer','')
                if (marker.get('application')!='DdokddakProduction3' or target is None
                    or target>=current or Path(name).name!=name or not name.endswith('.exe')):continue
                entries=list(folder.iterdir())
                for p in entries:
                    plain_path(p)
                    if not p.is_file() or p.name not in (name,'production3-download.json'):
                        raise ValueError('Unknown cache contents')
                if not (folder/name).is_file():continue
                signature={p.name:(p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_ino) for p in entries}
                latest=max([folder.stat().st_mtime,*[p.stat().st_mtime for p in entries]])
                candidates.append((target,latest,folder,base,signature))
            except (OSError,ValueError,TypeError):continue
    # Keep one most recent prior installer for recovery, across both cache roots.
    candidates.sort(key=lambda row:(row[0],row[1]),reverse=True)
    results=[]
    for _,latest,folder,base,signature in candidates[1:]:
        if time.time()-latest<DAY:continue
        try:
            plain_path(folder)
            if folder.resolve().parent!=base.resolve():continue
            entries=list(folder.iterdir())
            for p in entries:plain_path(p)
            if {p.name:(p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_ino) for p in entries}!=signature:continue
            size=0
            # Marker last: interruption leaves ownership evidence in place.
            for p in sorted(entries,key=lambda p:p.name=='production3-download.json'):
                plain_path(p)
                size+=p.stat().st_size;p.unlink()
            folder.rmdir()
            results.append({'path':str(folder),'status':'deleted','deleted_bytes':size})
        except (OSError,ValueError):continue
    return results
