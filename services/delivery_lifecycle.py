"""Bound delivery scratch space independently of successful publication.

Uses deterministic finally cleanup (as in CPython tempfile) and durable bounded
backoff (as in Tenacity), without retrying inside a sleeping collector process.
Caller must hold the operating root's collection lock.
"""
from contextlib import nullcontext
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
import tempfile

from services import delivery_stage_cleanup as safety

JOURNALS = ('backward-v1/settings/delivery_publication.json',
            'backward-v1/settings/delivery_publication.tmp',
            'backward-v1/inventory-status/pending_publication.json',
            'backward-v1/inventory-status/pending_publication.tmp')


def remove_stage(root, stage, *, base=None):
    root, stage = Path(root).resolve(), Path(stage)
    base = Path(base or tempfile.gettempdir()).resolve()
    safety.plain_path(stage)
    if stage.resolve().parent != base or not stage.name.startswith('prod3-delivery-'):
        raise ValueError('생산3팀 임시 폴더 범위 밖 · 삭제 안 함')
    if root == stage.resolve() or root.is_relative_to(stage.resolve()) or stage.resolve().is_relative_to(root):
        raise ValueError('운영 자료 경로 보호')
    safety.owned_marker(stage, root)
    if any((root/name).exists() for name in JOURNALS):
        raise ValueError('반영 복구 기록 있음 · 복구 후 임시 자료 정리')
    guard = safety.collection_guard(stage) if (stage/'backward-v1/collection.lock').exists() else nullcontext()
    with guard:
        files, directories, _ = safety.inventory(stage)  # Reject nested junctions/symlinks.
        size = sum(row[1] for row in files)
        diagnostics = root/'backward-v1/settings/delivery-diagnostics'
        diagnostics.mkdir(parents=True, exist_ok=True)
        destination = diagnostics/stage.name
        if not destination.exists():
            safety.archive_diagnostics(stage, destination)
        # Windows cannot remove an open lock file. The parent's lock remains held.
    # Keep ownership until every payload is gone: an interrupted/denied deletion
    # must remain discoverable and block the next large copy.
    marker=stage/'delivery-owner.json'
    for path, _, _, _ in files:
        if path != marker:
            path.unlink()
    for directory in directories:
        directory.rmdir()
    marker.unlink()
    stage.rmdir()
    entries = sorted((p for p in diagnostics.glob('prod3-delivery-*') if p.is_dir()),
                     key=lambda p:p.stat().st_mtime, reverse=True)
    for old in entries[5:]:
        safety.inventory(old)
        shutil.rmtree(old)
    return {'path':str(stage), 'status':'deleted', 'deleted_bytes':size}


def prune_stages(root, *, base=None):
    """A leftover that cannot be reclaimed blocks creation of another big copy."""
    base = Path(base or tempfile.gettempdir()).resolve()
    removed = []
    for stage in sorted(base.glob('prod3-delivery-*')):
        try:
            marker = safety.read_json(stage/'delivery-owner.json')
        except (OSError, ValueError):
            continue  # Not demonstrably owned by this application/root.
        if (not isinstance(marker,dict) or not isinstance(marker.get('owner'),str)
                or not marker['owner'] or Path(marker['owner']).resolve() != Path(root).resolve()):
            continue
        removed.append(remove_stage(root, stage, base=base))
    return removed


def retry_reason(report, key, now=None):
    if report.get('retry_key') != key:
        return ''
    if report.get('retry_count', 0) >= 3:
        return '동일 자료 계산 3회 실패 · 자동 재시도 중지 · 새 서버 자료 또는 수동 갱신 필요'
    now = now or datetime.now().astimezone()
    if report.get('retry_after') and now < datetime.fromisoformat(report['retry_after']):
        return '계산 재시도 대기 · ' + report['retry_after'][11:19] + ' 이후 확인'
    return ''


def record_failure(report, key, now=None):
    now = now or datetime.now().astimezone()
    count = report.get('retry_count', 0) + 1 if report.get('retry_key') == key else 1
    report.update(retry_key=key, retry_count=count,
                  retry_after=(now+timedelta(minutes=min(30, 5*2**min(count-1,3)))).isoformat())


def clear_retry(report):
    for key in ('retry_key','retry_count','retry_after'):
        report.pop(key,None)
