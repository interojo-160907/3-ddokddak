"""Spread stored-data reads without changing permission or source routing."""
import hashlib
import random
from datetime import datetime, timedelta


def stable_offset(pc_id: str, job: str, window: int) -> int:
    if window <= 0:
        return 0
    digest = hashlib.sha256(f'{pc_id}|{job}'.encode('utf-8')).digest()
    return int.from_bytes(digest[:8], 'big') % window


def periodic_delay(pc_id: str, job: str, now: datetime, window: int = 60) -> float:
    """Next wall-clock slot, including zero when already on that slot."""
    phase = now.timestamp() % window
    offset = stable_offset(pc_id, job, window)
    if 0 <= phase - offset < 1.5:
        return 0.0  # A one-second GUI tick may arrive just after the slot.
    return (offset - phase) % window


def retry_deadline(now: datetime, failures: int, uniform=random.uniform) -> datetime:
    # Persist this once per failure: polling/restarting must not redraw it.
    base = min(3600, 60 * 2 ** min(max(failures - 1, 0), 6))
    return now + timedelta(seconds=base + uniform(0, min(base / 2, 300)))


def retry_blocked(errors: dict, source: str, now: datetime) -> bool:
    targets = ('bom', 'aps', 'production', 'live') if source == 'all' else (source,)
    for key in targets:
        try:
            deadline = datetime.fromisoformat(errors.get(key, {}).get('retry_after', ''))
            if deadline > now:
                return True
        except (ValueError, TypeError):
            continue
    return False
