from __future__ import annotations

import os
from pathlib import Path

from config import DATA_CENTER_DIR
from services.process_status_service import PROCESS_ORDER, ProcessStatusService


DATA_DIR = Path(
    os.getenv(
        "DDOKDDAK_PROD3_LIVE_NEED_DATA_DIR",
        str(DATA_CENTER_DIR / "live-production-need"),
    )
)
DB_PATH = DATA_DIR / "current_production_need.sqlite"
STATUS_PATH = DATA_DIR / "snapshot" / "refresh_status.json"


class LiveProductionNeedService(ProcessStatusService):
    """APS 부족량에서 이번 회차 이후 완료량만 차감한 조회 서비스."""

    def __init__(self) -> None:
        super().__init__(DB_PATH, STATUS_PATH)

    def load_rows(self, search: str = "", process: str = "전체") -> list[dict]:
        rows = super().load_rows(search=search, process=process)
        production_processes = PROCESS_ORDER[:-1]
        return [
            row
            for row in rows
            if any(float(row.get(name) or 0) > 0 for name in production_processes)
        ]
