from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from services.data_location import resolve_data_root

DATA_ROOT = resolve_data_root()
STATUS_PATH = DATA_ROOT / "settings" / "cleanup_status.json"

# 현재 SQLite와 상태 JSON은 건드리지 않는다. 재수집·복구에 필요한 최신
# 압축 원천과 DB 백업만 소량 보관한다.
RETENTION_POLICIES = (
    (DATA_ROOT / "bom" / "raw_api", "bom_api_*.json.gz", 3),
    (DATA_ROOT / "bom" / "backup", "product_reference_before_*.sqlite", 2),
    (DATA_ROOT / "process-status" / "raw_api", "aps_s_factory_*.json.gz", 3),
    (DATA_ROOT / "process-status" / "backup", "aps_process_status_before_*.sqlite", 2),
    (DATA_ROOT / "production-performance" / "raw_api", "production_*.json.gz", 7),
    (DATA_ROOT / "production-performance" / "backup", "production_performance_before_*.sqlite", 2),
    (APP_ROOT / "logs", "*", 10),
)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _prune(directory: Path, pattern: str, keep: int) -> tuple[int, int]:
    if not directory.is_dir():
        return 0, 0
    files = sorted(
        (item for item in directory.glob(pattern) if item.is_file()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    bytes_removed = 0
    for item in files[max(0, keep):]:
        try:
            size = item.stat().st_size
            item.unlink()
            removed += 1
            bytes_removed += size
        except OSError:
            continue
    return removed, bytes_removed


def _remove_stale_temporary_files() -> tuple[int, int]:
    removed = 0
    bytes_removed = 0
    cutoff = datetime.now().timestamp() - 24 * 60 * 60
    for directory in (
        DATA_ROOT / "bom",
        DATA_ROOT / "process-status",
        DATA_ROOT / "production-performance",
    ):
        if not directory.is_dir():
            continue
        for pattern in ("*.tmp", "*.building.sqlite"):
            for item in directory.glob(pattern):
                try:
                    if item.is_file() and item.stat().st_mtime < cutoff:
                        size = item.stat().st_size
                        item.unlink()
                        removed += 1
                        bytes_removed += size
                except OSError:
                    continue
    return removed, bytes_removed


def _trim_production_history() -> int:
    database = DATA_ROOT / "production-performance" / "production_performance.sqlite"
    if not database.is_file():
        return 0
    first_this_month = date.today().replace(day=1)
    keep_from = (first_this_month - timedelta(days=1)).replace(day=1).isoformat()
    connection = sqlite3.connect(database, timeout=5)
    try:
        before = connection.total_changes
        connection.execute(
            "DELETE FROM production_performance WHERE substr(pr_dt,1,10) < ?",
            (keep_from,),
        )
        connection.commit()
        return connection.total_changes - before
    finally:
        connection.close()


def cleanup() -> dict[str, Any]:
    removed_files = 0
    removed_bytes = 0
    for directory, pattern, keep in RETENTION_POLICIES:
        count, size = _prune(directory, pattern, keep)
        removed_files += count
        removed_bytes += size
    count, size = _remove_stale_temporary_files()
    removed_files += count
    removed_bytes += size
    trimmed_rows = _trim_production_history()
    result = {
        "status": "success",
        "cleaned_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "removed_files": removed_files,
        "removed_bytes": removed_bytes,
        "trimmed_production_rows": trimmed_rows,
        "policy": {
            "bom_raw": 3,
            "aps_raw": 3,
            "production_raw": 7,
            "database_backups_each": 2,
            "production_months": 2,
        },
    }
    _atomic_json(STATUS_PATH, result)
    return result


def main() -> int:
    print(json.dumps(cleanup(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
