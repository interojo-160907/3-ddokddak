from __future__ import annotations

import json
from pathlib import Path

from config import DATA_CENTER_DIR


SETTINGS_PATH = DATA_CENTER_DIR / "settings" / "collection_schedule.json"
DEFAULT_SCHEDULE = {
    "bom_minutes": 60,
    "aps_minutes": 1,
    "production_minutes": 60,
}


def load_schedule() -> dict[str, int]:
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        saved = {}
    result = dict(DEFAULT_SCHEDULE)
    for key in result:
        try:
            result[key] = max(0, int(saved.get(key, result[key])))
        except (TypeError, ValueError):
            pass
    return result


def save_schedule(schedule: dict[str, int]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        key: max(0, int(schedule.get(key, default)))
        for key, default in DEFAULT_SCHEDULE.items()
    }
    temporary = SETTINGS_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(SETTINGS_PATH)
