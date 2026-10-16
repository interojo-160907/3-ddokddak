"""Independent APS view. Calculation services always retain their paired database."""
import json
from datetime import datetime
from pathlib import Path

from services.data_location import resolve_data_root
from services.process_status_service import ProcessStatusService
from services import safe_mode


def read_manifest():
    try:
        return json.loads((resolve_data_root()/'backward-v1/aps-view/current.json').read_text('utf-8'))
    except (OSError, ValueError):
        return {}


class ApsDisplayService(ProcessStatusService):
    def _view_folder(self):
        if safe_mode.active():
            return None
        manifest = read_manifest()
        base = resolve_data_root()/'backward-v1/aps-view'
        name = manifest.get('folder', '')
        if not name or Path(name).name != name or name in ('.','..'):
            return None
        folder = base/name/'backward-v1/process-status'
        # A newer manual calculation must never be hidden by an older server view.
        try:
            paired = json.loads(super().status_path.read_text('utf-8'))
        except (OSError, ValueError):
            paired = {}
        if str(manifest.get('aps_key', '')) < str(paired.get('run_key', '')):
            return None
        return folder if (folder/'aps_process_status.sqlite').is_file() else None

    @property
    def database_path(self):
        folder = self._view_folder()
        return folder/'aps_process_status.sqlite' if folder else super().database_path

    @property
    def status_path(self):
        folder = self._view_folder()
        return folder/'snapshot/refresh_status.json' if folder else super().status_path


def delivery_note(kind='live'):
    """User-facing purpose and the actual applied cycle, not a connectivity claim."""
    try:
        report = json.loads((resolve_data_root()/'backward-v1/settings/delivery_status.json').read_text('utf-8'))
    except (OSError, ValueError):
        return ''
    if safe_mode.active():
        return ''
    view = read_manifest()
    applied = report.get('aps_key') or '미적용'
    try:
        live=json.loads((resolve_data_root()/'backward-v1/live-production-need/snapshot/refresh_status.json').read_text('utf-8'))
        identity=str(live.get('source_identity','')).split('|')
        if len(identity)>1 and identity[1]:applied=identity[1]
    except (OSError,ValueError):
        pass
    latest = view.get('aps_key') or applied
    if kind == 'aps':
        key = ApsDisplayService().status().get('run_key') or latest
        return f'APS 원본 기준 {key} · 새 APS는 먼저 조회됩니다. 재고·실적을 반영한 계산은 같은 회차의 생산 현황 준비 후 제공됩니다.'
    purpose = '실적 반영·LOT 작업 순서·재고 현황은 APS 계획에 수집된 재고·완료실적을 반영합니다.'
    if latest > applied and applied!='미적용':
        return f'새 APS {latest} 조회 가능 · 계산은 이전 {applied} 회차 유지 · 같은 회차 생산 현황 대기\n{purpose} 서로 다른 회차는 합산하지 않습니다.'
    if applied=='미적용':
        return f'APS {latest} 조회 가능 · 같은 회차 생산 현황을 기다리는 중 · 첫 계산 전\n{purpose}'
    if report.get('status') != 'success':
        return f'계산 기준 APS {applied} · 생산 현황 갱신 대기 · 마지막 정상 계산 표시\n{purpose}'
    try:
        received=datetime.fromisoformat(str(report.get('received_at',''))).astimezone().strftime('%m-%d %H:%M:%S')
    except ValueError:received='확인 중'
    return f'계산 기준 APS {applied} · 생산 현황 {received}\n{purpose}'
