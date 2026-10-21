"""After a healthy update, reclaim only obsolete, owned delivery stages."""
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

from config import APP_VERSION
from services.data_location import resolve_data_root
from services import delivery_stage_cleanup as core


@contextmanager
def cleanup_guard(root):
    path=root/'settings/safe_cleanup.lock'
    core.plain_path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a+b') as stream:
        if not path.stat().st_size:
            stream.write(b'0');stream.flush()
        stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


def cleanup(root, base, *, force=False):
    root=Path(root);base=Path(base)
    core.plain_path(root);core.plain_path(base)
    status_path=root/'settings/cleanup_status.json'
    stamp=datetime.now().astimezone()
    result={'status':'deferred','checked_at':stamp.isoformat(),'app_version':APP_VERSION,
            'removed_files':0,'removed_folders':0,'removed_bytes':0,'results':[]}
    with cleanup_guard(root):
        try:
            previous=core.read_json(status_path) if status_path.exists() else {}
            bootstrap_path=root/'backward-v1/settings/version_collection_bootstrap.json'
            bootstrap=core.read_json(bootstrap_path) if bootstrap_path.exists() else {}
            # Existing verified operating data may safely supersede old temp
            # stages even before the new-version bootstrap has room to run.
            current_bootstrap=bootstrap.get('completed_version')==APP_VERSION
            state=core.operational_state(root)
            if (not force and previous.get('completed_version')==APP_VERSION
                    and previous.get('applied_version')==state.get('applied_version')
                    and stamp.timestamp()-datetime.fromisoformat(previous['cleaned_at']).timestamp()<600):
                return dict(previous,status='skipped',reason='최근 안전 정리를 완료했습니다.')
            started=time.monotonic()
            session=core.log_root()/('auto-'+stamp.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
            rows,issue=core.scan(base,root)
            if issue:raise ValueError(issue)
            # Previously retired folders cannot be used by collection again.
            for record,_ in core.pending_retirements(base,root):
                result['results'].append(core.delete_retired(base,root,record))
            for index,row in enumerate(rows):
                if time.monotonic()-started>30:
                    result['results'].append({'status':'deferred','deleted_bytes':0,'reason':'다음 주기에 이어서 정리'})
                    break
                if row.eligible:
                    try:
                        result['results'].append(core.clean_one(base,root,row,session/str(index)))
                    except (OSError,ValueError,core.BusyError) as exc:
                        result['results'].append({'path':row.path,'status':'protected','error':str(exc),'deleted_bytes':0})
            from services.local_storage_cleanup import cleanup_local
            if current_bootstrap:
                result['results'].extend(cleanup_local(root))
            from services.update_download_cleanup import cleanup_downloads
            result['results'].extend(cleanup_downloads(APP_VERSION))
            result['removed_bytes']=sum(r.get('deleted_bytes',0) for r in result['results'])
            result['removed_folders']=sum(r['status']=='deleted' for r in result['results'])
            if any(r['status']!='deleted' for r in result['results']):
                result['status']='deferred'
                result['reason']='사용 중이거나 상태가 바뀐 자료는 보존하고 다음 점검에서 재확인합니다.'
            else:
                result.update(status='success',cleaned_at=stamp.isoformat(),completed_version=APP_VERSION,
                              applied_version=state.get('applied_version'),
                              reason='운영 자료와 최신 복구 사본을 보존했습니다.')
        except (OSError,ValueError,TypeError,core.BusyError) as exc:
            result['reason']=str(exc)
        temporary=status_path.with_suffix('.tmp')
        core.plain_path(temporary)
        temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(status_path)
    return result


def main():
    if os.name=='nt':
        # Housekeeping yields CPU priority to the interactive production app.
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetCurrentProcess.restype=ctypes.c_void_p
        kernel.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
        kernel.SetPriorityClass(kernel.GetCurrentProcess(),0x00004000)
    if os.getenv('DDOKDDAK_PROD3_PREVIEW')=='1' or os.getenv('DDOKDDAK_PROD3_STAGE_ROOT'):
        result={'status':'skipped','reason':'미리보기·격리 계산에서는 자동 정리하지 않습니다.'}
    else:
        try:
            result=cleanup(resolve_data_root(),Path(tempfile.gettempdir()),force='--force' in sys.argv)
        except (OSError,ValueError,core.BusyError) as exc:
            result={'status':'deferred','reason':str(exc)}
    print(json.dumps(result,ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
