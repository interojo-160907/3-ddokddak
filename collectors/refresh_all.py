from __future__ import annotations

import json
import os
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
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


RESULT_PATH = resolve_data_root() / "settings" / "full_refresh_status.json"


def _write_result(value: dict) -> None:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(str(RESULT_PATH) + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(RESULT_PATH)


def main() -> int:
    api_key = os.getenv("PLAN_API_KEY", "")
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    report = {
        "started_at": started_at,
        "completed_at": "",
        "results": {},
    }
    _write_result(report)
    base_collectors = (
        ("bom", lambda: refresh_bom(api_key=api_key, timeout=240, force=True)),
        ("aps", lambda: refresh_aps(api_key=api_key, timeout=300)),
        ("production", lambda: refresh_production(api_key=api_key, timeout=240)),
    )
    failed = False
    # These source snapshots are independent. Running them together keeps a
    # first launch from waiting for every large API response in series.
    with ThreadPoolExecutor(max_workers=len(base_collectors)) as pool:
        jobs = {pool.submit(collect): key for key, collect in base_collectors}
        for job in as_completed(jobs):
            key = jobs[job]
            try:
                report["results"][key] = {"status": "success", "result": job.result(),
                                           "completed_at": datetime.now().astimezone().isoformat(timespec="seconds")}
            except Exception as exc:
                failed = True
                report["results"][key] = {
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                }
            _write_result(report)

    # WIP/performance, warehouses and hydration use the latest base snapshots.
    key = "live"
    try:
        live_result = refresh_live()
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
    report["completed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    _write_result(report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 1 if failed else 0 if report['results']['live']['status']=='success' else 2


if __name__ == "__main__":
    raise SystemExit(main())
