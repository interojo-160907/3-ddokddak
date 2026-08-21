from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from services.data_location import resolve_data_root

DATA_DIR = Path(
    os.getenv(
        "DDOKDDAK_PROD3_PRODUCTION_DATA_DIR",
        str(resolve_data_root() / "production-performance"),
    )
)
DB_PATH = DATA_DIR / "production_performance.sqlite"
STATUS_PATH = DATA_DIR / "snapshot" / "refresh_status.json"
BACKUP_DIR = DATA_DIR / "backup"
RAW_DIR = DATA_DIR / "raw_api"
BASE_URL = "https://plan.interojo.net"
S_FACTORY_CODE = "04"
PROCESS_CODES = ("10", "20", "45", "55", "80")
DAILY_FULL_HOUR = 7


def _read_status() -> dict[str, Any]:
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def should_run_daily_full(
    now: datetime,
    previous_status: dict[str, Any],
    *,
    database_exists: bool,
    force_full: bool = False,
) -> bool:
    if force_full or not database_exists:
        return True
    return (
        now.hour >= DAILY_FULL_HOUR
        and str(previous_status.get("daily_full_date") or "") != now.date().isoformat()
    )


def collection_window(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    first_this_month = today.replace(day=1)
    previous_month_end = first_this_month - timedelta(days=1)
    # 전일 확정분과 당일 진행 막대를 함께 제공한다. 확정 수율/실적에서
    # 당일을 제외하는 책임은 대시보드 집계가 맡는다.
    return previous_month_end.replace(day=1), today


def _month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        month_end = cursor.replace(day=monthrange(cursor.year, cursor.month)[1])
        chunk_end = min(month_end, end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _simple_day_chunks(start: date, end: date) -> list[tuple[date, date]]:
    """대용량 월 응답 대신 일자 단위로 병렬 수집하는 생산실적 간편 모드."""
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        chunks.append((cursor, cursor))
        cursor += timedelta(days=1)
    return chunks


def _fetch(start: date, end: date, api_key: str, timeout: int) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    response = requests.get(
        f"{BASE_URL}/api/production-performance",
        params={
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "limit": 0,
            "prompt_context": "똑딱이 생산3팀 당월·전월 생산실적 수집",
        },
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    response.encoding = "utf-8"
    payload = response.json()
    if payload.get("truncated"):
        raise RuntimeError(f"생산실적 {start}~{end} 응답이 일부만 반환되었습니다.")
    return payload


def _fetch_complete_range(start: date, end: date, api_key: str, timeout: int) -> list[dict[str, Any]]:
    """월 요청이 잘리면 해당 구간만 일자 단위로 자동 재수집한다."""
    try:
        return [_fetch(start, end, api_key, timeout)]
    except RuntimeError as exc:
        if "일부만 반환" not in str(exc) or start == end:
            raise
    chunks = _simple_day_chunks(start, end)
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(lambda chunk: _fetch(chunk[0], chunk[1], api_key, timeout), chunks))


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _write_raw(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as stream:
        json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), default=str)
    temporary.replace(path)


def _prune(directory: Path, pattern: str, keep: int) -> None:
    if not directory.exists():
        return
    files = sorted(directory.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    for item in files[keep:]:
        try:
            item.unlink()
        except OSError:
            pass


def _row_key(row: dict[str, Any]) -> str:
    primary = str(row.get("pr_no") or "").strip()
    if primary:
        return primary
    source = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE production_performance (
            row_key TEXT PRIMARY KEY,
            pr_no TEXT, pr_dt TEXT, gong_cd TEXT, fac_cd TEXT, sachul_fac_cd TEXT,
            gd_cd TEXT, gd_nm TEXT, sale_cd TEXT, model_no TEXT, model_no2 TEXT,
            full_gu TEXT, percontent REAL, spec TEXT, spec30 TEXT, size_spec TEXT,
            jisi_spec TEXT, unit_cd TEXT, job_qty REAL, pr_qty REAL, ng_qty REAL,
            sample_qty REAL, tot_qty REAL, keep_sample_qty REAL, mate_no TEXT,
            test_yn TEXT, mc_cd TEXT, pre_mc_cd TEXT, mc_10 TEXT, stts TEXT,
            stts_label TEXT, bc_result TEXT, dia_result TEXT, w_power TEXT,
            size80 TEXT, bc80 TEXT, loss_cd TEXT, loss_nm TEXT, ps_cd TEXT,
            ps_nm TEXT, extracted_at TEXT, payload_json TEXT NOT NULL
        );
        CREATE INDEX ix_production_date_process ON production_performance(pr_dt,gong_cd);
        CREATE INDEX ix_production_product ON production_performance(gd_cd,sale_cd,model_no);
        CREATE TABLE sync_meta (
            id INTEGER PRIMARY KEY CHECK(id=1), date_from TEXT NOT NULL,
            date_to TEXT NOT NULL, source_rows INTEGER NOT NULL,
            s_factory_rows INTEGER NOT NULL, refreshed_at TEXT NOT NULL,
            source_refreshed_at TEXT, raw_snapshot_path TEXT NOT NULL
        );
        """
    )


FIELDS = (
    "pr_no", "pr_dt", "gong_cd", "fac_cd", "sachul_fac_cd", "gd_cd", "gd_nm",
    "sale_cd", "model_no", "model_no2", "full_gu", "percontent", "spec", "spec30",
    "size_spec", "jisi_spec", "unit_cd", "job_qty", "pr_qty", "ng_qty", "sample_qty",
    "tot_qty", "keep_sample_qty", "mate_no", "test_yn", "mc_cd", "pre_mc_cd", "mc_10",
    "stts", "stts_label", "bc_result", "dia_result", "w_power", "size80", "bc80",
    "loss_cd", "loss_nm", "ps_cd", "ps_nm", "extracted_at",
)


def refresh(api_key: str = "", timeout: int = 240, force_full: bool = False) -> dict[str, Any]:
    history_start, end = collection_window()
    now = datetime.now().astimezone()
    previous_status = _read_status()
    full_refresh = should_run_daily_full(
        now, previous_status, database_exists=DB_PATH.is_file(), force_full=force_full
    )
    start = history_start if full_refresh else end - timedelta(days=6)
    # 매일 07시 이후 첫 수집은 전월~당일 전체를 교체해 저장→확인·폐기
    # 상태 변경을 반영한다. 같은 날 이후 수집은 최근 7일만 갱신한다.
    chunks = _month_chunks(start, end) if full_refresh else _simple_day_chunks(start, end)
    if full_refresh:
        payloads = [
            payload
            for chunk_start, chunk_end in chunks
            for payload in _fetch_complete_range(chunk_start, chunk_end, api_key, timeout)
        ]
    else:
        with ThreadPoolExecutor(max_workers=3) as pool:
            payloads = list(pool.map(lambda chunk: _fetch(chunk[0], chunk[1], api_key, timeout), chunks))
    source_rows = [row for payload in payloads for row in list(payload.get("rows") or [])]
    rows = [
        row for row in source_rows
        if str(row.get("fac_cd") or "").strip() == S_FACTORY_CODE
        and str(row.get("gong_cd") or "").strip() in PROCESS_CODES
        and start.isoformat() <= str(row.get("pr_dt") or "")[:10] <= end.isoformat()
    ]
    stamp = now.strftime("%Y%m%d_%H%M%S")
    raw_path = RAW_DIR / f"production_{start:%Y%m%d}_{end:%Y%m%d}_{stamp}.json.gz"
    _write_raw(
        raw_path,
        {
            "collected_at": now.isoformat(timespec="seconds"),
            "collection_mode": "07시 일일 전체(전월~금일)" if full_refresh else "간편 증분(최근 7일)",
            "date_from": start.isoformat(),
            "date_to": end.isoformat(),
            "source_row_count": len(source_rows),
            "rows": rows,
        },
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, BACKUP_DIR / f"production_performance_before_{stamp}.sqlite")
    source_refreshed_at = max((str(payload.get("source_refreshed_at") or "") for payload in payloads), default="")
    marks = ",".join("?" for _ in range(len(FIELDS) + 2))
    values_by_key: dict[str, tuple[Any, ...]] = {}
    for row in rows:
        row_key = _row_key(row)
        values_by_key[row_key] = (
            row_key,
            *(row.get(field) for field in FIELDS),
            json.dumps(row, ensure_ascii=False, sort_keys=True, default=str),
        )
    values = list(values_by_key.values())
    duplicate_rows = len(rows) - len(values)
    if full_refresh:
        temporary_db = DATA_DIR / "production_performance.building.sqlite"
        if temporary_db.exists():
            temporary_db.unlink()
        connection = sqlite3.connect(temporary_db)
        try:
            _initialize(connection)
            connection.executemany(f"INSERT INTO production_performance VALUES ({marks})", values)
            connection.execute(
                "INSERT INTO sync_meta VALUES (1,?,?,?,?,?,?,?)",
                (history_start.isoformat(), end.isoformat(), len(source_rows), len(rows), now.isoformat(timespec="seconds"), source_refreshed_at, str(raw_path)),
            )
            connection.commit()
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("생산실적 SQLite 무결성 오류")
        finally:
            connection.close()
        temporary_db.replace(DB_PATH)
    else:
        connection = sqlite3.connect(DB_PATH)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM production_performance WHERE pr_dt BETWEEN ? AND ?",
                (start.isoformat(), end.isoformat()),
            )
            connection.executemany(f"INSERT OR REPLACE INTO production_performance VALUES ({marks})", values)
            stored_rows = connection.execute("SELECT COUNT(*) FROM production_performance").fetchone()[0]
            meta = connection.execute("SELECT date_from FROM sync_meta WHERE id=1").fetchone()
            retained_start = meta[0] if meta else history_start.isoformat()
            connection.execute(
                "INSERT OR REPLACE INTO sync_meta VALUES (1,?,?,?,?,?,?,?)",
                (retained_start, end.isoformat(), len(source_rows), stored_rows, now.isoformat(timespec="seconds"), source_refreshed_at, str(raw_path)),
            )
            connection.commit()
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("생산실적 SQLite 무결성 오류")
        finally:
            connection.close()
    _prune(BACKUP_DIR, "production_performance_before_*.sqlite", 10)
    _prune(RAW_DIR, "production_*.json.gz", 14)
    with sqlite3.connect(DB_PATH) as verify_connection:
        stored_rows = int(verify_connection.execute("SELECT COUNT(*) FROM production_performance").fetchone()[0])
    result = {
        "status": "success", "database": str(DB_PATH), "date_from": history_start.isoformat(),
        "date_to": end.isoformat(), "source_rows": len(source_rows), "s_factory_rows": len(rows),
        "deduplicated_rows": duplicate_rows,
        "confirmed_through": (end - timedelta(days=1)).isoformat(),
        "stored_rows": stored_rows,
        "collection_mode": "07시 일일 전체(전월~금일)" if full_refresh else "간편 증분(최근 7일)",
        "collection_from": start.isoformat(),
        "refreshed_at": now.isoformat(timespec="seconds"), "source_refreshed_at": source_refreshed_at,
        "raw_snapshot": str(raw_path),
    }
    if full_refresh and now.hour >= DAILY_FULL_HOUR:
        result["daily_full_date"] = now.date().isoformat()
        result["daily_full_completed_at"] = now.isoformat(timespec="seconds")
    else:
        result["daily_full_date"] = str(previous_status.get("daily_full_date") or "")
        result["daily_full_completed_at"] = str(
            previous_status.get("daily_full_completed_at") or ""
        )
    _atomic_json(STATUS_PATH, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="생산3팀 당월·전월 생산실적 스냅샷 수집")
    parser.add_argument("--api-key", default=os.getenv("PLAN_API_KEY", ""))
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--full", action="store_true", help="기존 DB가 있어도 전월~당일 전체 재수집")
    args = parser.parse_args()
    print(json.dumps(refresh(args.api_key, args.timeout, force_full=args.full), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
