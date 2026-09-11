"""Use successful recovery timestamps consistently across collection indicators."""
from datetime import datetime


def recovered(error: dict, status: dict) -> bool:
    if status.get("status") not in {"success", "skipped"}:
        return False
    try:
        failed_at = datetime.fromisoformat(str(error["occurred_at"]))
        succeeded_at = datetime.fromisoformat(str(status.get("refreshed_at") or status.get("collected_at")))
        # Legacy GUI errors used naive local time; collector statuses include an offset.
        return succeeded_at.astimezone() > failed_at.astimezone()
    except (KeyError, TypeError, ValueError):
        return False


def collection_ready(statuses: dict, errors: dict, connections: dict) -> bool:
    return (
        not errors
        and all(statuses.get(key, {}).get("status") in {"success", "skipped"}
                for key in ("bom_status", "aps_status", "production_status", "live_status"))
        and all(connections.values())
    )
