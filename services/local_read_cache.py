"""Bounded shared local query results, invalidated by every DB and WAL change."""
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from threading import RLock

_cache=OrderedDict()
_lock=RLock()

def _clone(rows):
    result=[]
    for row in rows:
        copied=dict(row)
        for key,value in copied.items():
            if isinstance(value,(dict,list,set,tuple)):
                copied[key]=deepcopy(value)
        result.append(copied)
    return result

def signature(paths):
    result=[]
    for path in paths:
        path=Path(path)
        for file in (path,Path(str(path)+'-wal')):
            try:
                stat=file.stat();result.append((str(file.resolve()),stat.st_size,stat.st_mtime_ns))
            except OSError:result.append((str(file.resolve()),None,None))
    return tuple(result)

def read(paths,variant,loader):
    paths=tuple(paths);stamp=signature(paths);key=(variant,stamp)
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _clone(_cache[key])
        rows=loader()
        if signature(paths)==stamp:
            # Discard the previous generation of the same view.
            for old in list(_cache):
                if old[0]==variant and tuple(x[0] for x in old[1])==tuple(x[0] for x in stamp):
                    del _cache[old]
            _cache[key]=_clone(rows)
            while len(_cache)>12:_cache.popitem(last=False)
        return rows
