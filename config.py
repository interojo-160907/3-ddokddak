from __future__ import annotations

import os
from pathlib import Path

from services.data_location import resolve_data_root


APP_NAME = "똑딱이 생산3팀"
APP_DISPLAY_NAME = "생산3팀 납기 통합조회"
APP_VERSION = "0.1.3"
APP_USER_MODEL_ID = "Ddokddak.ProductionTeam3.Source"
DEFAULT_FACTORY = "S관"

# SCM Control Tower의 중앙 저장소와 공유하지 않는 생산3팀 전용 API 저장소입니다.
DATA_CENTER_DIR = resolve_data_root()
LEAD_SHEET_PDF_BACKUP_DIR = DATA_CENTER_DIR / "리드지 PDF 백업"
LEAD_SHEET_PREVIEW_CACHE_DIR = DATA_CENTER_DIR / "리드지 미리보기 캐시"

ROOT_DIR = Path(__file__).resolve().parent
ASSET_DIR = ROOT_DIR / "assets"
STYLE_DIR = ROOT_DIR / "styles"
DATA_DIR = ROOT_DIR / "data"
API_CACHE_DIR = DATA_DIR / "api_cache"
API_RAW_DIR = DATA_DIR / "api_raw"
BACKUP_DIR = DATA_DIR / "backup"
SNAPSHOT_DIR = DATA_DIR / "snapshots"
LOG_DIR = ROOT_DIR / "logs"


def ensure_directories() -> None:
    for path in (
        DATA_CENTER_DIR,
        LEAD_SHEET_PDF_BACKUP_DIR,
        LEAD_SHEET_PREVIEW_CACHE_DIR,
        ASSET_DIR,
        STYLE_DIR,
        DATA_DIR,
        API_CACHE_DIR,
        API_RAW_DIR,
        BACKUP_DIR,
        SNAPSHOT_DIR,
        LOG_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
