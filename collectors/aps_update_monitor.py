from __future__ import annotations

import argparse
import json
import os
from typing import Any

try:
    from collectors.process_status_collector import DB_PATH, STATUS_PATH, _request, refresh
except ImportError:
    from process_status_collector import DB_PATH, STATUS_PATH, _request, refresh


def _local_status() -> dict[str, Any]:
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def check_and_refresh(api_key: str = "", timeout: int = 60) -> dict[str, Any]:
    local = _local_status()
    from services.backward_source import outbound_state
    source = outbound_state(_request("/api/aps-outbound-status", {"limit": 0}, api_key, timeout))
    source_count = source["backward_count"]
    source_refreshed_at = source["backward_at"]
    unchanged = (
        DB_PATH.is_file()
        and local.get("source_identity") == source["source_identity"]
        and local.get("backward_count") == source["backward_count"]
        and local.get("plan_count") == source["plan_count"]
        and local.get("plan_at") == source["plan_at"]
        and local.get("wip_at") == source["wip_at"]
        and local.get("wip_count") == source["wip_count"]
        and local.get("status") == "success"
    )
    if unchanged:
        return {
            "status": "success",
            "changed": False,
            "source_refreshed_at": source_refreshed_at,
            "source_rows": source_count,
        }

    result = refresh(api_key=api_key, timeout=max(timeout, 300))
    return {**result, "changed": True}


from services.collection_lock import serialized_collection


@serialized_collection
def main() -> int:
    parser = argparse.ArgumentParser(description="APS 원천 갱신 감시 및 S관 스냅샷 자동 교체")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()
    print(
        json.dumps(
            check_and_refresh(api_key=args.api_key, timeout=args.timeout),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
