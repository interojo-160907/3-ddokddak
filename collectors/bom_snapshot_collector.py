from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
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
        "DDOKDDAK_PROD3_BOM_DATA_DIR",
        str(resolve_data_root() / "bom"),
    )
)
DB_PATH = DATA_DIR / "product_reference.sqlite"
STATUS_PATH = DATA_DIR / "snapshot" / "refresh_status.json"
BACKUP_DIR = DATA_DIR / "backup"
RAW_DIR = DATA_DIR / "raw_api"
BASE_URL = "https://plan.interojo.net"
RAW_RETENTION_COUNT = 14
BACKUP_RETENTION_COUNT = 10

PRODUCT_FIELDS = (
    "nm_cd", "nm_nm", "nm_gu", "nm_gu_nm", "model_no", "model_nm",
    "fac_cd", "fac_nm", "yy_cnt", "wear_cycle", "color_yn", "dia", "bc",
    "mt_gu", "mt_gu_nm", "cycle_gu", "cycle_gu_nm", "color_gu",
    "color_gu_nm", "optic_gu", "optic_gu_nm", "full_gu", "full_gu_nm",
    "percontent", "stts", "use_yn", "in_dt", "extracted_at",
)
PRODUCT_COMPARE_FIELDS = PRODUCT_FIELDS[:-1]
BOM_FIELDS = (
    "root_cd", "lvl", "parent_cd", "parent_gu", "parent_nm", "child_cd",
    "child_gu", "child_nm", "child_spec", "child_help_gu", "qty", "stts",
    "use_yn", "extracted_at",
)
SALINE_FIELDS = (
    "nm_cd", "nm_nm", "model_no", "model_nm", "nm_fac_cd", "nm_fac_nm",
    "gd_cd", "gd_nm", "saline_ss", "gong_cd", "vision_code", "stts",
    "stts_label", "mid", "mid_nm", "mdt", "cid", "cdt", "extracted_at",
)


def _fetch(endpoint: str, api_key: str, timeout: int) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    response = requests.get(
        f"{BASE_URL}{endpoint}",
        params={"limit": 0, "prompt_context": "똑딱이 생산3팀 BOM 스냅샷 갱신"},
        headers=headers,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("truncated"):
        raise RuntimeError(f"{endpoint} 응답이 일부만 반환되었습니다.")
    if not payload.get("rows"):
        raise RuntimeError(f"{endpoint} 응답 데이터가 비어 있습니다.")
    return payload


def _initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS product_name_master (
            nm_cd TEXT PRIMARY KEY, nm_nm TEXT, nm_gu TEXT, nm_gu_nm TEXT,
            model_no TEXT, model_nm TEXT, fac_cd TEXT, fac_nm TEXT, yy_cnt REAL,
            wear_cycle TEXT, color_yn TEXT, dia REAL, bc REAL, mt_gu TEXT,
            mt_gu_nm TEXT, cycle_gu TEXT, cycle_gu_nm TEXT, color_gu TEXT,
            color_gu_nm TEXT, optic_gu TEXT, optic_gu_nm TEXT, full_gu TEXT,
            full_gu_nm TEXT, percontent REAL, stts TEXT, use_yn TEXT, in_dt TEXT,
            extracted_at TEXT, payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_product_name_type
            ON product_name_master(nm_gu_nm,use_yn,fac_cd);
        CREATE TABLE IF NOT EXISTS bom_relation (
            row_key TEXT PRIMARY KEY, root_cd TEXT, lvl INTEGER, parent_cd TEXT,
            parent_gu TEXT, parent_nm TEXT, child_cd TEXT, child_gu TEXT,
            child_nm TEXT, child_spec TEXT, child_help_gu TEXT, qty REAL,
            stts TEXT, use_yn TEXT, extracted_at TEXT, payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_bom_parent ON bom_relation(parent_cd,use_yn);
        CREATE INDEX IF NOT EXISTS ix_bom_child ON bom_relation(child_cd,use_yn);
        CREATE TABLE IF NOT EXISTS product_saline_registration (
            row_key TEXT PRIMARY KEY, nm_cd TEXT, nm_nm TEXT, model_no TEXT,
            model_nm TEXT, nm_fac_cd TEXT, nm_fac_nm TEXT, gd_cd TEXT,
            gd_nm TEXT, saline_ss TEXT, gong_cd TEXT, vision_code TEXT,
            stts TEXT, stts_label TEXT, mid TEXT, mid_nm TEXT, mdt TEXT,
            cid TEXT, cdt TEXT, extracted_at TEXT, payload_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_product_saline_code
            ON product_saline_registration(nm_cd,stts);
        CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, dataset TEXT NOT NULL,
            storage_mode TEXT NOT NULL, status TEXT NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0, source_refreshed_at TEXT,
            source_sha256 TEXT, refreshed_at TEXT NOT NULL, message TEXT
        );
        CREATE TABLE IF NOT EXISTS product_change_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nm_cd TEXT NOT NULL,
            change_type TEXT NOT NULL,
            changed_fields TEXT NOT NULL DEFAULT '[]',
            before_json TEXT,
            after_json TEXT,
            detected_at TEXT NOT NULL,
            source_sha256 TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_product_change_code_time
            ON product_change_history(nm_cd,detected_at DESC);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_product_change_source
            ON product_change_history(source_sha256,nm_cd,change_type);
        """
    )


def _source_hash(
    product_rows: list[dict],
    bom_rows: list[dict],
    saline_rows: list[dict],
) -> str:
    digest = hashlib.sha256()
    for rows, fields in (
        (product_rows, PRODUCT_FIELDS[:-1]),
        (bom_rows, BOM_FIELDS[:-1]),
        (saline_rows, SALINE_FIELDS[:-1]),
    ):
        normalized = [{field: row.get(field) for field in fields} for row in rows]
        normalized.sort(
            key=lambda row: json.dumps(
                row, ensure_ascii=False, sort_keys=True, default=str
            )
        )
        for row in normalized:
            digest.update(
                json.dumps(
                    row,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            )
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _atomic_gzip_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=6) as stream:
        json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"), default=str)
    temporary.replace(path)


def _prune_files(directory: Path, pattern: str, keep: int) -> None:
    if not directory.exists():
        return
    files = sorted(
        directory.glob(pattern),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in files[max(1, keep):]:
        try:
            path.unlink()
        except OSError:
            pass


def _source_refreshed_at(*payloads: dict[str, Any]) -> str:
    candidates: list[str] = []
    for payload in payloads:
        for key in ("source_refreshed_at", "query_date"):
            value = str(payload.get(key) or "").strip()
            if value:
                candidates.append(value)
    return max(candidates, default="")


def _record(row: dict[str, Any], fields: tuple[str, ...]) -> tuple[Any, ...]:
    return (
        *(row.get(field) for field in fields),
        json.dumps(row, ensure_ascii=False, sort_keys=True, default=str),
    )


def _normalize_product_api_row(row: dict[str, Any]) -> dict[str, Any]:
    """현재 API의 등록일 cdt를 로컬 표준 필드 in_dt로 맞춘다."""
    normalized = dict(row)
    if not normalized.get("in_dt"):
        normalized["in_dt"] = normalized.get("cdt")
    return normalized


def _normalized_product(row: dict[str, Any] | sqlite3.Row) -> dict[str, Any]:
    """Keep only ERP business fields so extraction timestamps do not look like edits."""
    return {field: row[field] if field in row.keys() else None for field in PRODUCT_COMPARE_FIELDS}


def _product_changes(
    previous_rows: list[dict[str, Any] | sqlite3.Row],
    current_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    previous = {
        str(row["nm_cd"] or "").strip().upper(): _normalized_product(row)
        for row in previous_rows
        if str(row["nm_cd"] or "").strip()
    }
    current = {
        str(row.get("nm_cd") or "").strip().upper(): _normalized_product(row)
        for row in current_rows
        if str(row.get("nm_cd") or "").strip()
    }
    changes: list[dict[str, Any]] = []
    for code in sorted(current.keys() - previous.keys()):
        changes.append(
            {"nm_cd": code, "change_type": "created", "changed_fields": [],
             "before": None, "after": current[code]}
        )
    for code in sorted(previous.keys() - current.keys()):
        changes.append(
            {"nm_cd": code, "change_type": "deleted", "changed_fields": [],
             "before": previous[code], "after": None}
        )
    for code in sorted(previous.keys() & current.keys()):
        changed_fields = [
            field for field in PRODUCT_COMPARE_FIELDS
            if previous[code].get(field) != current[code].get(field)
        ]
        if changed_fields:
            changes.append(
                {"nm_cd": code, "change_type": "updated", "changed_fields": changed_fields,
                 "before": previous[code], "after": current[code]}
            )
    return changes


def _bom_record(row: dict[str, Any]) -> tuple[Any, ...]:
    payload = json.dumps(
        {field: row.get(field) for field in BOM_FIELDS[:-1]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return (hashlib.sha256(payload.encode("utf-8")).hexdigest(), *_record(row, BOM_FIELDS))


def _saline_record(row: dict[str, Any]) -> tuple[Any, ...]:
    payload = json.dumps(
        {field: row.get(field) for field in SALINE_FIELDS[:-1]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return (
        hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        *_record(row, SALINE_FIELDS),
    )


def refresh(api_key: str, timeout: int = 240, force: bool = False) -> dict[str, Any]:
    now = datetime.now().astimezone()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        product_future = pool.submit(_fetch, "/api/product-names", api_key, timeout)
        bom_future = pool.submit(_fetch, "/api/bom-explosion", api_key, timeout)
        saline_future = pool.submit(_fetch, "/api/product-saline", api_key, timeout)
    product_payload = product_future.result()
    bom_payload = bom_future.result()
    saline_payload = saline_future.result()
    product_rows = [
        _normalize_product_api_row(row)
        for row in list(product_payload.get("rows") or [])
    ]
    bom_rows = list(bom_payload.get("rows") or [])
    saline_rows = list(saline_payload.get("rows") or [])
    raw_snapshot_path = RAW_DIR / f"bom_api_{now:%Y%m%d_%H%M%S}.json.gz"
    _atomic_gzip_json(
        raw_snapshot_path,
        {
            "collected_at": now.isoformat(timespec="seconds"),
            "product_names": product_payload,
            "bom_explosion": bom_payload,
            "product_saline": saline_payload,
        },
    )
    _prune_files(RAW_DIR, "bom_api_*.json.gz", RAW_RETENTION_COUNT)
    source_hash = _source_hash(product_rows, bom_rows, saline_rows)
    source_refreshed_at = _source_refreshed_at(product_payload, bom_payload)

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        _initialize(connection)
        previous = connection.execute(
            "SELECT source_sha256 FROM sync_log "
            "WHERE dataset='product-reference' AND status IN ('success','skipped') "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not force and previous and str(previous[0]) == source_hash:
            result = {
                "status": "skipped",
                "product_rows": connection.execute(
                    "SELECT count(*) FROM product_name_master"
                ).fetchone()[0],
                "bom_rows": connection.execute(
                    "SELECT count(*) FROM bom_relation"
                ).fetchone()[0],
                "saline_rows": connection.execute(
                    "SELECT count(*) FROM product_saline_registration"
                ).fetchone()[0],
                "refreshed_at": now.isoformat(timespec="seconds"),
                "source_refreshed_at": source_refreshed_at,
                "raw_snapshot": str(raw_snapshot_path),
            }
            connection.execute(
                "INSERT INTO sync_log(dataset,storage_mode,status,row_count,"
                "source_refreshed_at,source_sha256,refreshed_at,message) VALUES(?,?,?,?,?,?,?,?)",
                (
                    "product-reference", "refresh_replace", "skipped",
                    len(product_rows) + len(bom_rows) + len(saline_rows),
                    source_refreshed_at, source_hash,
                    result["refreshed_at"], "API 내용이 현재 최신본과 동일함",
                ),
            )
            _atomic_json(STATUS_PATH, result)
            return result

    if DB_PATH.is_file():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            DB_PATH,
            BACKUP_DIR / f"product_reference_before_{now:%Y%m%d_%H%M%S}.sqlite",
        )
        _prune_files(BACKUP_DIR, "product_reference_before_*.sqlite", BACKUP_RETENTION_COUNT)

    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        _initialize(connection)
        previous_products = connection.execute(
            "SELECT " + ",".join(PRODUCT_COMPARE_FIELDS) + " FROM product_name_master"
        ).fetchall()
        # An empty database is the monitoring baseline, not thousands of
        # artificial "new product" events. Changes start with the next source edit.
        product_changes = (
            _product_changes(previous_products, product_rows)
            if previous_products else []
        )
        with connection:
            connection.executemany(
                "INSERT OR IGNORE INTO product_change_history("
                "nm_cd,change_type,changed_fields,before_json,after_json,detected_at,source_sha256"
                ") VALUES(?,?,?,?,?,?,?)",
                (
                    (
                        change["nm_cd"],
                        change["change_type"],
                        json.dumps(change["changed_fields"], ensure_ascii=False),
                        json.dumps(change["before"], ensure_ascii=False, sort_keys=True, default=str)
                        if change["before"] is not None else None,
                        json.dumps(change["after"], ensure_ascii=False, sort_keys=True, default=str)
                        if change["after"] is not None else None,
                        now.isoformat(timespec="seconds"),
                        source_hash,
                    )
                    for change in product_changes
                ),
            )
            connection.execute("DELETE FROM product_name_master")
            connection.execute("DELETE FROM bom_relation")
            connection.execute("DELETE FROM product_saline_registration")
            connection.executemany(
                "INSERT INTO product_name_master("
                + ",".join(PRODUCT_FIELDS)
                + ",payload_json) VALUES("
                + ",".join("?" for _ in range(len(PRODUCT_FIELDS) + 1))
                + ")",
                (_record(row, PRODUCT_FIELDS) for row in product_rows),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO bom_relation(row_key,"
                + ",".join(BOM_FIELDS)
                + ",payload_json) VALUES("
                + ",".join("?" for _ in range(len(BOM_FIELDS) + 2))
                + ")",
                (_bom_record(row) for row in bom_rows),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO product_saline_registration(row_key,"
                + ",".join(SALINE_FIELDS)
                + ",payload_json) VALUES("
                + ",".join("?" for _ in range(len(SALINE_FIELDS) + 2))
                + ")",
                (_saline_record(row) for row in saline_rows),
            )
            refreshed_at = now.isoformat(timespec="seconds")
            connection.execute(
                "INSERT INTO sync_log(dataset,storage_mode,status,row_count,"
                "source_refreshed_at,source_sha256,refreshed_at,message) VALUES(?,?,?,?,?,?,?,?)",
                (
                    "product-reference", "refresh_replace", "success",
                    len(product_rows) + len(bom_rows) + len(saline_rows),
                    source_refreshed_at, source_hash,
                    refreshed_at, "제품명·BOM 현재본 교체 완료",
                ),
            )
        result = {
            "status": "success",
            "product_rows": connection.execute(
                "SELECT count(*) FROM product_name_master"
            ).fetchone()[0],
            "bom_rows": connection.execute(
                "SELECT count(*) FROM bom_relation"
            ).fetchone()[0],
            "saline_rows": connection.execute(
                "SELECT count(*) FROM product_saline_registration"
            ).fetchone()[0],
            "refreshed_at": refreshed_at,
            "database": str(DB_PATH),
            "source_refreshed_at": source_refreshed_at,
            "raw_snapshot": str(raw_snapshot_path),
            "product_changes": {
                "created": sum(change["change_type"] == "created" for change in product_changes),
                "updated": sum(change["change_type"] == "updated" for change in product_changes),
                "deleted": sum(change["change_type"] == "deleted" for change in product_changes),
            },
        }
    _atomic_json(STATUS_PATH, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="제품·BOM 기준정보 중앙 수집")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    result = refresh(
        api_key=os.getenv("PLAN_API_KEY", ""),
        timeout=args.timeout,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
