from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

try:
    from collectors.bom_snapshot_collector import refresh as refresh_bom
    from collectors.aps_update_monitor import check_and_refresh as refresh_aps
    from collectors.production_performance_collector import refresh as refresh_production
    from collectors.inventory_live_refresh import refresh as refresh_live
    from services.data_location import resolve_data_root
except ImportError:
    from bom_snapshot_collector import refresh as refresh_bom
    from aps_update_monitor import check_and_refresh as refresh_aps
    from production_performance_collector import refresh as refresh_production
    from inventory_live_refresh import refresh as refresh_live
    from services.data_location import resolve_data_root


from services.api_credentials import resolve_api_key
from collectors.process_status_collector import refresh as refresh_aps_full


RESULT_PATH = resolve_data_root() / "backward-v1" / "settings" / "full_refresh_status.json"


def _write_result(value: dict) -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(str(RESULT_PATH) + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(RESULT_PATH)


from services.collection_lock import serialized_collection


@serialized_collection
def main(force_full: bool | None = None) -> int:
    if force_full is None:
        force_full = "--full" in sys.argv[1:]
    api_key = resolve_api_key()
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    report = {
        "started_at": started_at,
        "completed_at": "",
        "results": {},
        "force_full": force_full,
    }
    _write_result(report)
    base_collectors = (
        ("aps", lambda: (refresh_aps_full if force_full else refresh_aps)(api_key=api_key, timeout=300)),
        ("bom", lambda: refresh_bom(api_key=api_key, timeout=240, force=True)),
        ("production", lambda: refresh_production(api_key=api_key, timeout=240, force_full=force_full)),
    )
    failed = False
    # Finish APS first. Nested BOM/production workers share two network slots;
    # scheduling all sources at once starves queued requests before they start.
    for key, collect in base_collectors:
        if key == 'bom':
            from services.dmz_read_client import route_to_dmz, missing_calculation_sources
            if route_to_dmz('/api/aps-plan'):
                missing = missing_calculation_sources()
                if missing:
                    for remaining in ('bom','production','live'):
                        report['results'][remaining] = {'status':'waiting_server',
                            'message':'사내 서버 자료 준비 대기', 'missing_sources':missing}
                    report['current_source']=''
                    report['completed_at']=datetime.now().astimezone().isoformat(timespec='seconds')
                    _write_result(report)
                    print(json.dumps(report,ensure_ascii=False,default=str))
                    return 2
        report["current_source"] = key
        _write_result(report)
        try:
            result = collect()
            status = result.get("status", "error")
            failed = failed or status not in {"success", "skipped"}
            report["results"][key] = {"status": status, "result": result,
                                       "completed_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        except Exception as exc:
            failed = True
            report["results"][key] = {
                "status": "error", "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        _write_result(report)

    # WIP/performance, warehouses and hydration use the latest base snapshots.
    key = "live"
    report["current_source"] = key
    _write_result(report)
    try:
        if report["results"].get("aps", {}).get("status") not in {"success", "skipped"}:
            raise RuntimeError("Backward APS 수집이 완료되지 않아 실시간 계산을 보류합니다.")
        live_result = refresh_live(force_full=True) if force_full else refresh_live()
        report["results"][key] = {"status": live_result.get('status','error'), "result": live_result,
                                   "completed_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    except Exception as exc:
        failed = True
        report["results"][key] = {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    _write_result(report)
    report["current_source"] = ""
    report["completed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    _write_result(report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 1 if failed else 0 if report['results']['live']['status']=='success' else 2


if __name__ == "__main__":
    raise SystemExit(main())
