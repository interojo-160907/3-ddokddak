from __future__ import annotations

import argparse
import json
import os
from typing import Any

try:
    from collectors.process_status_collector import STATUS_PATH, _request, refresh
except ImportError:
    from process_status_collector import STATUS_PATH, _request, refresh


def _local_status() -> dict[str, Any]:
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def check_and_refresh(api_key: str = "", timeout: int = 60) -> dict[str, Any]:
    local = _local_status()
    source = _request("/api/aps-plan/meta", {}, api_key, timeout)
    source_count = int(source.get("total_count") or 0)
    source_refreshed_at = str(source.get("last_refreshed_at") or "")
    if source_count <= 0 or not source_refreshed_at:
        raise RuntimeError("APS 원천 갱신 중이거나 메타정보가 없습니다.")

    unchanged = (
        str(local.get("source_refreshed_at") or "") == source_refreshed_at
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


def main() -> int:
    parser = argparse.ArgumentParser(description="APS 원천 갱신 감시 및 S관 스냅샷 자동 교체")
    parser.add_argument("--api-key", default=os.getenv("PLAN_API_KEY", ""))
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
