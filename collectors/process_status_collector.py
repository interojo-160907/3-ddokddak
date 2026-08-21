from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from services.data_location import resolve_data_root

DATA_DIR = Path(
    os.getenv(
        "DDOKDDAK_PROD3_PROCESS_DATA_DIR",
        str(resolve_data_root() / "process-status"),
    )
)
DB_PATH = DATA_DIR / "aps_process_status.sqlite"
STATUS_PATH = DATA_DIR / "snapshot" / "refresh_status.json"
BACKUP_DIR = DATA_DIR / "backup"
RAW_DIR = DATA_DIR / "raw_api"
BASE_URL = "https://plan.interojo.net"
FACTORY = "S관(3공장)"
PROCESS_CODES = ("10", "20", "45", "55", "80", "85")
SAFETY_DEMAND_TYPES = {"안전", "안전(국내)", "안전(해외)", "안전재고(국내)", "안전재고(해외)"}
FIELDS = (
    "plan_date", "oper_id", "res_id", "res_site_id", "demand_id",
    "demand_item_id", "demand_item_name", "item_id", "plan_qty", "due_date",
    "cust_id", "cust_name", "demand_group_id", "demand_qty", "demand_priority",
    "demand_type", "mark_type", "dest_country", "item_cd", "pack_unit",
    "item_name2", "initial", "so_id", "seq", "item_group_id", "item_name",
    "item_cd_5", "power", "target_datetime",
)


def _request(endpoint: str, params: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    response = requests.get(f"{BASE_URL}{endpoint}", params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    response.encoding = "utf-8"
    return response.json()


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


def _collect_domestic_order_remarks(
    rows: list[dict[str, Any]], api_key: str, timeout: int,
) -> tuple[dict[str, tuple[str, str, str]], str]:
    """국내 생산요청의 비고를 수주번호 기준으로 보조 수집한다.

    비고 API 장애가 APS 본 수집을 막지 않도록 날짜별 오류는 기록만 하고
    정상 응답에서 확인된 비고는 계속 저장한다.
    """
    orders_by_date: dict[str, set[str]] = {}
    for row in rows:
        order_no = str(row.get("so_id") or "").strip()
        demand_type = str(row.get("demand_type") or "").strip()
        destination = str(row.get("dest_country") or "").strip()
        date_token = order_no[1:9]
        if (
            not order_no.startswith("R") or len(date_token) != 8 or not date_token.isdigit()
            or destination or demand_type == "PB" or demand_type in SAFETY_DEMAND_TYPES
        ):
            continue
        request_date = f"{date_token[:4]}-{date_token[4:6]}-{date_token[6:8]}"
        orders_by_date.setdefault(request_date, set()).add(order_no)

    remarks: dict[str, tuple[str, str, str]] = {}
    errors: list[str] = []
    for request_date, target_orders in sorted(orders_by_date.items()):
        try:
            payload = _request(
                "/api/prod-request-domestic",
                {
                    "date_from": request_date,
                    "date_to": request_date,
                    "limit": 0,
                    "prompt_context": "똑딱이 생산3팀 국내 생산요청 비고 수집",
                },
                api_key,
                timeout,
            )
            if payload.get("truncated"):
                errors.append(f"{request_date}: 일부 응답")
                continue
            source_updated_at = str(payload.get("source_refreshed_at") or "")
            for item in payload.get("rows") or []:
                order_no = str(item.get("re_no") or "").strip()
                remark = str(item.get("remark") or "").strip()
                if order_no in target_orders and remark:
                    remarks[order_no] = (
                        remark,
                        str(item.get("re_dt") or request_date)[:10],
                        source_updated_at,
                    )
        except Exception as exc:  # 비고 보조 수집은 APS 스냅샷 생성을 중단하지 않는다.
            errors.append(f"{request_date}: {exc}")
    return remarks, " | ".join(errors)


def _initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE aps_plan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_date TEXT, oper_id TEXT, res_id TEXT, res_site_id TEXT,
            demand_id TEXT, demand_item_id TEXT, demand_item_name TEXT,
            item_id TEXT, plan_qty REAL, due_date TEXT, cust_id TEXT,
            cust_name TEXT, demand_group_id TEXT, demand_qty REAL,
            demand_priority TEXT, demand_type TEXT, mark_type TEXT,
            dest_country TEXT, item_cd TEXT, pack_unit REAL, item_name2 TEXT,
            initial TEXT, so_id TEXT, seq INTEGER, item_group_id TEXT,
            item_name TEXT, item_cd_5 TEXT, power TEXT, target_datetime TEXT,
            payload_json TEXT NOT NULL
        );
        CREATE INDEX ix_aps_order_due ON aps_plan(so_id,due_date);
        CREATE INDEX ix_aps_process ON aps_plan(oper_id,res_site_id);
        CREATE INDEX ix_aps_product ON aps_plan(demand_item_id,item_cd,power);
        CREATE INDEX ix_aps_classification ON aps_plan(demand_group_id,demand_type);
        CREATE TABLE sync_meta (
            id INTEGER PRIMARY KEY CHECK(id=1), factory TEXT NOT NULL,
            source_rows INTEGER NOT NULL, stored_rows INTEGER NOT NULL,
            order_count INTEGER NOT NULL, source_refreshed_at TEXT,
            refreshed_at TEXT NOT NULL, raw_snapshot_path TEXT NOT NULL
        );
        CREATE TABLE order_remark (
            order_no TEXT PRIMARY KEY,
            remark TEXT NOT NULL,
            request_date TEXT,
            source_updated_at TEXT
        );
        """
    )


def refresh(api_key: str = "", timeout: int = 300) -> dict[str, Any]:
    meta_before = _request("/api/aps-plan/meta", {}, api_key, timeout)
    if int(meta_before.get("total_count") or 0) <= 0:
        raise RuntimeError("APS 원천 갱신 중이거나 데이터가 없습니다.")
    payload = _request(
        "/api/aps-plan",
        {"site": "S관", "limit": 0, "prompt_context": "똑딱이 생산3팀 세부 진행 현황 수집"},
        api_key,
        timeout,
    )
    meta_after = _request("/api/aps-plan/meta", {}, api_key, timeout)
    before_signature = (meta_before.get("last_refreshed_at"), int(meta_before.get("total_count") or 0))
    after_signature = (meta_after.get("last_refreshed_at"), int(meta_after.get("total_count") or 0))
    payload_signature = (payload.get("source_refreshed_at"), int(payload.get("source_total_count") or 0))
    if before_signature != after_signature or payload_signature != after_signature:
        raise RuntimeError("APS 수집 도중 원천 데이터가 갱신되었습니다. 잠시 후 다시 실행해 주세요.")
    if payload.get("truncated"):
        raise RuntimeError("APS 응답이 일부만 반환되었습니다.")
    source_rows = list(payload.get("rows") or [])
    rows = [
        row for row in source_rows
        if str(row.get("res_site_id") or "").strip() == FACTORY
        and str(row.get("oper_id") or "").strip() in PROCESS_CODES
    ]
    if not rows:
        raise RuntimeError("S관 공정 진행 데이터가 없습니다.")

    order_remarks, remark_error = _collect_domestic_order_remarks(rows, api_key, timeout)

    now = datetime.now().astimezone()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    raw_path = RAW_DIR / f"aps_s_factory_{stamp}.json.gz"
    _write_raw(raw_path, {"collected_at": now.isoformat(timespec="seconds"), "factory": FACTORY, "meta": meta_after, "payload": payload})

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_db = DATA_DIR / "aps_process_status.building.sqlite"
    if temporary_db.exists():
        temporary_db.unlink()
    connection = sqlite3.connect(temporary_db)
    try:
        _initialize(connection)
        marks = ",".join("?" for _ in range(len(FIELDS) + 1))
        connection.executemany(
            f"INSERT INTO aps_plan ({','.join(FIELDS)},payload_json) VALUES ({marks})",
            [(*[row.get(field) for field in FIELDS], json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)) for row in rows],
        )
        connection.executemany(
            "INSERT INTO order_remark(order_no,remark,request_date,source_updated_at) VALUES (?,?,?,?)",
            [(order_no, *values) for order_no, values in sorted(order_remarks.items())],
        )
        order_count = len({str(row.get("so_id") or "").strip() for row in rows if str(row.get("so_id") or "").strip()})
        connection.execute(
            "INSERT INTO sync_meta VALUES (1,?,?,?,?,?,?,?)",
            (FACTORY, len(source_rows), len(rows), order_count, str(payload.get("source_refreshed_at") or ""), now.isoformat(timespec="seconds"), str(raw_path)),
        )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"공정현황 SQLite 무결성 오류: {integrity}")
    finally:
        connection.close()

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, BACKUP_DIR / f"aps_process_status_before_{stamp}.sqlite")
    temporary_db.replace(DB_PATH)
    _prune(BACKUP_DIR, "aps_process_status_before_*.sqlite", 10)
    _prune(RAW_DIR, "aps_s_factory_*.json.gz", 14)
    result = {
        "status": "success", "database": str(DB_PATH), "factory": FACTORY,
        "source_rows": len(source_rows), "stored_rows": len(rows), "order_count": order_count,
        "source_refreshed_at": str(payload.get("source_refreshed_at") or ""),
        "refreshed_at": now.isoformat(timespec="seconds"), "raw_snapshot": str(raw_path),
        "remark_count": len(order_remarks), "remark_error": remark_error,
    }
    _atomic_json(STATUS_PATH, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="생산3팀 S관 세부 진행 현황 스냅샷 수집")
    parser.add_argument("--api-key", default=os.getenv("PLAN_API_KEY", ""))
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    print(json.dumps(refresh(args.api_key, args.timeout), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
