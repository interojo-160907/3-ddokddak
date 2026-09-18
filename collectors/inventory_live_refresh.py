"""Collect one shared warehouse set for live need and inventory display."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.inventory_status_service import InventoryStatusService, WAREHOUSES
from services.hydration_instruction_service import HydrationInstructionService
from services.api_credentials import resolve_api_key
from services.live_production_need_service import DB_PATH, STATUS_PATH
from collectors import live_production_need_collector as live_collector


def refresh() -> dict:
    service = InventoryStatusService()
    previous = live_collector._read_json(service.cache / 'refresh_status.json')
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    folder = service.cache / 'snapshots' / stamp
    folder.mkdir(parents=True, exist_ok=True)
    report = {
        'cycle_id': stamp, 'started_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'status': 'running', 'phase': 'WIP·재고·실적·수화 지시 수집',
        'sources': {wh: {'status': 'pending'} for _, _, wh in live_collector.WAREHOUSES},
        'live': {'status': 'running'}, 'hydration': {'status': 'running'},
        'result': {'status': 'pending'}, 'snapshot': str(folder),
    }
    lock = threading.Lock()

    def save_report():
        # Called under the lock while worker threads are active.
        for target in (folder / 'refresh_status.json', service.cache / 'refresh_status.json'):
            tmp = target.with_suffix('.tmp')
            tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(target)

    save_report()

    def warehouse_started(warehouse):
        with lock:
            report['phase'] = warehouse + (' 재고 수집' if warehouse in report['sources'] else '')
            if warehouse in report['sources']:
                report['sources'][warehouse] = {'status': 'running'}
            save_report()

    def warehouse_received(warehouse, payload):
        # stock_qty is the closing balance at date_to. This full response supplies
        # both live LOT movements and product-level inventory without a second call.
        outcome = service.save_response('inventory_' + warehouse, payload)
        with lock:
            report['sources'][warehouse] = outcome
            save_report()

    def warehouse_failed(warehouse, message):
        with lock:
            report['sources'][warehouse] = {'status': 'error', 'message': message,
                'retained': (service.cache / ('inventory_' + warehouse + '.json')).exists()}
            save_report()

    def reuse_warehouse(warehouse, query):
        # During recovery reuse only recent, validated responses for the exact
        # same ledger period. Normal manual/automatic refreshes fetch everything.
        if previous.get('status') not in {'partial', 'running', 'cancelled'}:return None
        if previous.get('sources', {}).get(warehouse, {}).get('status') != 'success':return None
        data = live_collector._read_json(service.cache / ('inventory_' + warehouse + '.json'))
        if data.get('_inventory_query') != query:return None
        try:age = (datetime.now().astimezone() - datetime.fromisoformat(data['_collected_at']).astimezone()).total_seconds()
        except (KeyError, ValueError):return None
        if not 0 <= age <= 900:return None
        return dict(data, _reused=True)

    def hydration_received():
        try:
            outcome = HydrationInstructionService(service.cache).refresh()
        except Exception as exc:
            outcome = {'status': 'error', 'message': str(exc),
                       'retained': service.hydration_service.current_path.exists()}
        with lock:
            report['hydration'] = outcome
            save_report()

    key = resolve_api_key()
    with ThreadPoolExecutor(max_workers=1) as pool:
        hydration_job = pool.submit(hydration_received)
        try:
            status = live_collector.refresh(api_key=key, inventory_observer=warehouse_received,
                                             inventory_progress=warehouse_started,
                                             inventory_reuse=reuse_warehouse, inventory_error=warehouse_failed)
        except Exception as exc:
            status = {**live_collector._read_json(STATUS_PATH), 'status': 'retained',
                      'checked_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                      'retained_reason': f'{type(exc).__name__}: {exc}'}
            live_collector._atomic_json(STATUS_PATH, status)
        with lock:
            report['live'] = {'status': status.get('status', 'error'),
                              'completed_at': status.get('refreshed_at'),
                              'message': status.get('retained_reason', '')}
            save_report()
        hydration_job.result()

    for warehouse, outcome in report['sources'].items():
        if outcome['status'] in {'pending', 'running'}:
            report['sources'][warehouse] = {
                'status': 'waiting_wip' if status.get('status') == 'waiting_wip' else 'error',
                'message': status.get('retained_reason') or '이번 회차 창고 응답 미수집',
                'retained': (service.cache / ('inventory_' + warehouse + '.json')).exists(),
            }
    inputs_ok = (report['live']['status'] == 'success'
                 and report['hydration']['status'] == 'success'
                 and all(value['status'] == 'success' for value in report['sources'].values()))
    if inputs_ok:
        report['phase'] = '재고 현황 규격 조회·계산'
        save_report()
        try:
            with closing(sqlite3.connect(DB_PATH.as_uri() + '?mode=ro', uri=True)) as src:
                with closing(sqlite3.connect(folder / 'current_production_need.sqlite')) as dst:
                    src.backup(dst)
            data = service.build(allow_network=True, collecting=True)
            data['_complete_cycle'] = stamp
            data['_calculated_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
            (folder / 'inventory_result.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            tmp = service.cache / 'latest_result.tmp'
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            tmp.replace(service.cache / 'latest_result.json')
            service._save_result(service._filter_key({}), service._source_signature(), data)
            report['result'] = {'status': 'success', 'rows': data['rows'], 'products': data['products']}
        except Exception as exc:
            report['result'] = {'status': 'error', 'message': str(exc)}
    else:
        report['result'] = {'status': 'retained',
                            'message': '원천 일부 미완료 · 마지막 정상 재고 결과 유지',
                            'available': (service.cache / 'latest_result.json').exists()}
    report['completed_at'] = datetime.now().astimezone().isoformat(timespec='seconds')
    report['status'] = 'success' if inputs_ok and report['result']['status'] == 'success' else 'partial'
    report['phase'] = '완료' if report['status'] == 'success' else '일부 수집 미완료'
    save_report()
    return report


def main() -> int:
    report = refresh()
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report['status'] == 'success' else 2


if __name__ == '__main__':
    raise SystemExit(main())
