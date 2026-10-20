"""Conservative, offline cleanup of production3 delivery stages only."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import uuid

PREFIX = 'prod3-delivery-'
DAY = 86400


class BusyError(RuntimeError):
    pass


def linked(path):
    return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT) if os.name == 'nt' else path.is_symlink()


def plain_path(path):
    for p in (path, *path.parents):
        if p.exists() and linked(p):
            raise ValueError('연결 폴더는 보호합니다: ' + str(p))


def read_json(path):
    plain_path(path)
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('상태 기록이 너무 큽니다')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def discover():
    configured = ''
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Interojo\DdokddakProduction3') as key:
                configured = str(winreg.QueryValueEx(key, 'DataRoot')[0]).strip()
        except FileNotFoundError:
            pass
    configured = configured or os.getenv('DDOKDDAK_PROD3_DATA_DIR', '').strip()
    return Path(tempfile.gettempdir()).absolute(), Path(configured or r'C:\똑딱이 생산3팀 API DATA').absolute()


def operational_state(owner):
    plain_path(owner)
    settings = owner / 'backward-v1/settings'
    for name in ('delivery_publication.json', 'delivery_publication.tmp'):
        if (settings / name).exists():
            raise ValueError('복구 대기 기록이 있어 모든 임시 자료를 보호합니다')
    status = read_json(settings / 'delivery_status.json')
    if not isinstance(status, dict) or not status.get('applied_version') or not status.get('applied_at'):
        raise ValueError('정상 반영 기록을 확인할 수 없어 보호합니다')
    if status.get('status') == 'receiving':
        raise BusyError('생산3팀 수집 중입니다. 완료 후 자동으로 다시 확인합니다.')
    if status.get('status') != 'success':
        raise ValueError('최근 수집이 정상 완료되지 않아 자료를 보호합니다. 정상 수집 후 다시 검사해 주세요.')
    for relative in ('bom/product_reference.sqlite', 'backward-v1/collection.lock'):
        p = owner / relative
        plain_path(p)
        if not p.is_file() or not p.stat().st_size:
            raise ValueError('운영 자료를 확인할 수 없어 보호합니다: ' + relative)
    return status


@contextmanager
def collection_guard(owner):
    """Use the application's exact lock, never create a missing data root."""
    p = owner / 'backward-v1/collection.lock'
    plain_path(p)
    with p.open('r+b') as f:
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise BusyError('생산3팀 수집 중입니다. 완료 후 자동으로 다시 확인합니다.') from exc
        else:
            import fcntl
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise BusyError('수집 중') from exc
        try:
            yield
        finally:
            f.seek(0)
            if os.name == 'nt':
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)


def inventory(folder):
    """Never traverse junctions. Return exact manifest for revalidation/deletion."""
    plain_path(folder)
    files, dirs = [], []
    latest = folder.stat().st_mtime
    def walk(base):
        nonlocal latest
        with os.scandir(base) as entries:
            for entry in entries:
                p = Path(entry.path)
                s = p.stat(follow_symlinks=False)
                if stat.S_ISLNK(s.st_mode) or getattr(s, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise ValueError('연결 파일/폴더 포함: 보호')
                latest = max(latest, s.st_mtime)
                if entry.is_dir(follow_symlinks=False):
                    walk(p)
                    dirs.append(p)
                elif entry.is_file(follow_symlinks=False):
                    files.append((p, s.st_size, s.st_mtime_ns, s.st_ino))
                else:
                    raise ValueError('알 수 없는 파일 유형: 보호')
    walk(folder)
    return files, dirs, latest


@dataclass
class Row:
    path: str
    size: int = 0
    eligible: bool = False
    reason: str = ''
    latest: float = 0


def owned_marker(folder, owner):
    marker = read_json(folder / 'delivery-owner.json')
    if not isinstance(marker, dict) or type(marker.get('complete')) is not bool:
        raise ValueError('소유 표식 불명확: 보호')
    raw = marker.get('owner')
    if not isinstance(raw, str) or not Path(raw).is_absolute() or Path(raw).resolve() != owner.resolve():
        raise ValueError('현재 생산3팀 데이터와 소유 정보가 다름: 보호')
    return marker


def scan(base, owner, now=None):
    now = time.time() if now is None else now
    plain_path(base)
    issue = ''
    try:
        status = operational_state(owner)
        applied = datetime.fromisoformat(status['applied_at']).timestamp()
        current = status.get('stage_path', '')
    except (OSError, ValueError, TypeError, BusyError) as exc:
        issue, current, applied = str(exc), '', 0
    rows, completed = [], []
    for folder in sorted(base.glob(PREFIX + '*')):
        row = Row(str(folder))
        rows.append(row)
        try:
            if not folder.is_dir() or folder.resolve().parent != base.resolve():
                raise ValueError('검사 범위 밖: 보호')
            plain_path(folder)
            if owner.resolve().is_relative_to(folder.resolve()) or folder.resolve().is_relative_to(owner.resolve()):
                raise ValueError('운영 데이터와 겹치는 경로: 보호')
            files, _, row.latest = inventory(folder)
            row.size = sum(f[1] for f in files)
            marker = owned_marker(folder, owner)
            if marker['complete']:
                completed.append(row)
            if issue:
                raise ValueError(issue)
            if current and Path(current).resolve() == folder.resolve():
                raise ValueError('최근 반영 자료: 보호')
            if now - row.latest < DAY:
                raise ValueError('최근 24시간 자료: 보호')
            if row.latest >= applied:
                raise ValueError('이후 정상 반영을 확인할 수 없음: 보호')
            row.eligible = True
            row.reason = '반영 완료 임시 사본' if marker['complete'] else '이후 정상 반영된 과거 실패/중단 사본'
        except (OSError, ValueError, TypeError) as exc:
            row.reason = str(exc)
    if completed:
        newest = max(completed, key=lambda r: r.latest)
        newest.eligible, newest.reason = False, '가장 최근 성공 사본 1개: 보호'
    return rows, issue


def log_root():
    return Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'DdokddakProduction3Cleaner/logs'


def archive_diagnostics(folder, destination):
    plain_path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for name in ('delivery-owner.json', 'receive.log', 'backward-v1/settings/full_refresh_status.json'):
        source = folder / name
        if source.is_file():
            plain_path(source)
            with source.open('rb') as f:
                if source.stat().st_size > 1024 * 1024:
                    f.seek(-1024 * 1024, 2)
                (destination / name.replace('/', '_')).write_bytes(f.read(1024 * 1024))


def clean_one(base, owner, row, destination):
    """Revalidate before retiring an obsolete, application-owned staging copy."""
    folder = Path(row.path)
    # Expensive inspection never holds the application's lock.
    fresh, issue = scan(base, owner)
    match = next((r for r in fresh if r.path == row.path and r.eligible), None)
    if match is None:
        if '수집 중' in issue:
            raise BusyError(issue)
        raise ValueError(issue or '상태가 변경되어 보호 대상으로 전환했습니다')
    if folder.resolve().parent != base.resolve() or not folder.name.startswith(PREFIX):
        raise ValueError('삭제 범위 밖')
    owned_marker(folder, owner)
    files, dirs, _ = inventory(folder)
    # Archive once even if the shared lock is temporarily busy on a retry.
    if not destination.exists():
        archive_diagnostics(folder, destination)
    for p, size, modified, inode in files:
        plain_path(p)
        s = p.stat()
        if (s.st_size, s.st_mtime_ns, s.st_ino) != (size, modified, inode):
            raise ValueError('파일 변경 감지: 정리 중단')
    retired = base / ('prod3-cleaner-retired-' + uuid.uuid4().hex)
    record = {'original': str(folder), 'retired': str(retired), 'owner': str(owner), 'temp': str(base)}
    # Durable record precedes rename so an interrupted cleanup can be resumed.
    (destination / 'retirement.json').write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    with collection_guard(owner):
        state = operational_state(owner)
        if state.get('stage_path') and Path(state['stage_path']).resolve() == folder.resolve():
            raise ValueError('사용 중인 자료: 보호')
        owned_marker(folder, owner)
        plain_path(folder)
        current_files, _, latest = inventory(folder)
        if current_files != files or time.time()-latest < DAY or latest >= datetime.fromisoformat(state['applied_at']).timestamp():
            raise ValueError('분리 직전 자료 변경 감지: 보호')
        # Atomic same-volume rename; releases collection lock before bulk I/O.
        folder.rename(retired)
    return delete_retired(base, owner, record)


def delete_retired(base, owner, record):
    folder = Path(record['retired'])
    plain_path(folder)
    if folder.resolve().parent != base.resolve() or not folder.name.startswith('prod3-cleaner-retired-'):
        raise ValueError('분리 폴더 범위 밖')
    if Path(record['owner']).resolve() != owner.resolve() or Path(record['temp']).resolve() != base.resolve():
        raise ValueError('분리 기록 불일치')
    if folder.resolve().is_relative_to(owner.resolve()) or owner.resolve().is_relative_to(folder.resolve()):
        raise ValueError('운영 경로 보호')
    files, dirs, _ = inventory(folder)
    deleted = 0
    try:
        for p, size, modified, inode in files:
            plain_path(p)
            s = p.stat()
            if (s.st_size, s.st_mtime_ns, s.st_ino) != (size, modified, inode):
                raise ValueError('파일 변경 감지: 정리 중단')
            p.unlink()
            deleted += size
        for p in dirs:
            plain_path(p)
            p.rmdir()
        folder.rmdir()
    except (OSError, ValueError) as exc:
        return {'path': record['original'], 'retired': str(folder), 'deleted_bytes': deleted, 'status': 'partial', 'error': str(exc)}
    return {'path': record['original'], 'deleted_bytes': deleted, 'status': 'deleted'}


def pending_retirements(base, owner):
    pending = []
    for p in log_root().glob('*/*/retirement.json'):
        try:
            data = read_json(p)
            folder = Path(data['retired'])
            if (folder.is_dir() and folder.parent.resolve() == base.resolve()
                    and folder.name.startswith('prod3-cleaner-retired-')
                    and Path(data['owner']).resolve() == owner.resolve()
                    and Path(data['temp']).resolve() == base.resolve()):
                files, _, _ = inventory(folder)
                pending.append((data, sum(f[1] for f in files)))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return pending


def gib(value):
    return f'{value / 1024 ** 3:,.2f} GB'
