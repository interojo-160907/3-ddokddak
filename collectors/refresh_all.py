from __future__ import annotations

import json
import os
from datetime import datetime

try:
    from collectors.bom_snapshot_collector import refresh as refresh_bom
    from collectors.process_status_collector import refresh as refresh_aps
    from collectors.production_performance_collector import refresh as refresh_production
except ImportError:
    from bom_snapshot_collector import refresh as refresh_bom
    from process_status_collector import refresh as refresh_aps
    from production_performance_collector import refresh as refresh_production


def main() -> int:
    api_key = os.getenv("PLAN_API_KEY", "")
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    results = {
        "started_at": started_at,
        "bom": refresh_bom(api_key=api_key, timeout=240, force=True),
        "aps": refresh_aps(api_key=api_key, timeout=300),
        "production": refresh_production(api_key=api_key, timeout=240),
    }
    results["completed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
