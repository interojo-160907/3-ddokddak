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
    return refresh(api_key=api_key, timeout=max(timeout, 300))


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
