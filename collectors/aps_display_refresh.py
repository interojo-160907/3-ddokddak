"""Publish APS-only data for viewing without touching any calculation inputs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]


def receive_aps_view(root, head):
    base = root/'backward-v1/aps-view'
    base.mkdir(parents=True, exist_ok=True)
    manifest_path = base/'current.json'
    try:
        previous = json.loads(manifest_path.read_text('utf-8'))
    except (OSError, ValueError):
        previous = {}
    if previous.get('version') == head['version'] and (base/previous.get('folder', '')/'backward-v1/process-status/aps_process_status.sqlite').is_file():
        return previous
    # Each successful generation is immutable; current.json is the only switch.
    stage = Path(tempfile.mkdtemp(prefix='aps-', dir=base))
    env = os.environ.copy()
    env['DDOKDDAK_DMZ_CACHE_DIR'] = str(root/'backward-v1/dmz-cache')
    env.update(DDOKDDAK_PROD3_STAGE_ROOT=str(stage), DDOKDDAK_PROD3_TRANSPORT='dmz',
               DDOKDDAK_APS_VIEW_VERSION=head['version'])
    env.pop('DDOKDDAK_DELIVERY_VERSION', None)
    env.pop('DDOKDDAK_PROD3_PROCESS_DATA_DIR', None)
    command = [sys.executable] + ([] if getattr(sys, 'frozen', False) else [str(ROOT/'gui_app_pyside6.py')])
    command += ['--collector', 'process_status_collector']
    try:
        result = subprocess.run(command, env=env, capture_output=True, timeout=600,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        (stage/'receive.log').write_bytes(result.stdout+result.stderr)
        status = json.loads((stage/'backward-v1/process-status/snapshot/refresh_status.json').read_text('utf-8')) if result.returncode == 0 else {}
        if (status.get('status') != 'success' or status.get('run_key') != head['aps_key']
                or not (stage/'backward-v1/process-status/aps_process_status.sqlite').is_file()):
            raise ValueError('새 APS 조회 자료 수신 확인 필요 · 기존 조회 유지')
        manifest = dict(version=head['version'], aps_key=head['aps_key'], folder=stage.name,
                        applied_at=datetime.now().astimezone().isoformat())
        (stage/'aps-view-owner.json').write_text(json.dumps({'owner':str(base.resolve())}),encoding='utf-8')
        tmp = manifest_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        tmp.replace(manifest_path)
    except Exception:
        # Only this newly created private directory is removed on failure.
        if stage.resolve().parent==base.resolve() and not stage.is_symlink() and not stage.is_junction():
            shutil.rmtree(stage, ignore_errors=True)
        raise
    # Keep the last three successfully published view generations for readers.
    owned = sorted((p for p in base.glob('aps-*') if p.is_dir() and not p.is_symlink()
                    and not p.is_junction() and (p/'aps-view-owner.json').is_file()
                    and p.resolve().parent==base.resolve()), key=lambda p:p.stat().st_mtime, reverse=True)
    for old in owned[3:]:
        if old != stage:
            shutil.rmtree(old, ignore_errors=True)
    return manifest
