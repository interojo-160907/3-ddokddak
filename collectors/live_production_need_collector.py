from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from services.erp_api_client import request_json
from services.data_location import resolve_data_root


DATA_DIR = Path(
    os.getenv(
        "DDOKDDAK_PROD3_LIVE_NEED_DATA_DIR",
        str(resolve_data_root() / "live-production-need"),
    )
)
DB_PATH = DATA_DIR / "current_production_need.sqlite"
STATUS_PATH = DATA_DIR / "snapshot" / "refresh_status.json"
BACKUP_DIR = DATA_DIR / "backup"
APS_DB_PATH = Path(
    os.getenv(
        "DDOKDDAK_PROD3_PROCESS_DB",
        str(resolve_data_root() / "process-status" / "aps_process_status.sqlite"),
    )
)
BASE_URL = os.getenv("DDOKDDAK_PROD3_API_BASE_URL", "https://plan.interojo.net").rstrip("/")
PROCESS_CODES = ("10", "20", "45", "55", "80")
STAGE_PROCESS = {1: "10", 2: "20", 3: "45", 4: "55", 5: "80"}
PROCESS_STAGE = {value: key for key, value in STAGE_PROCESS.items()}
WAREHOUSES = (
    (1, "Q001", "사출창고"),
    (2, "R001", "분리창고"),
    (3, "G007", "검사접착"),
    (4, "G005", "누수규격검사"),
    (5, "P002", "규격검사완료"),
)
LOT_BASE_PATTERN = re.compile(r"^([A-Z]\d{8}-\d{3})(?:-.+)?$")
CALCULATION_REVISION = 2


COLLECTION_WORKERS = 1


class WipCycleNotReady(RuntimeError):
    """5개 공정창고의 WIP 원천 회차가 아직 하나로 맞춰지지 않은 상태."""


def normalize_text(value: object) -> str:
    return str(value or "").strip().upper()


def lot_identity(value: object) -> tuple[str, str, bool]:
    """전체 LOT는 고유키로, 앞 13자 기본 LOT는 계보 확인에만 사용한다."""
    full = normalize_text(value)
    match = LOT_BASE_PATTERN.match(full)
    base = match.group(1) if match else full
    return full, base, bool(full and base and full != base)


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def allocation_priority(row: dict[str, Any]) -> tuple[str, int, int, str, int]:
    """납기일이 절대 우선이며 같은 납기 안에서만 사용자 지정 순서를 적용한다."""
    due = str(row.get("due_date") or "9999-12-31")[:10]
    initial = str(row.get("initial") or "").strip()
    demand_type = str(row.get("demand_type") or "").strip()
    destination = str(row.get("dest_country") or "").strip()
    if initial:
        channel_rank = 0
    elif demand_type.upper() == "PB":
        channel_rank = 1
    elif "안전" in demand_type:
        channel_rank = 4
    elif not destination:
        channel_rank = 2
    else:
        channel_rank = 3
    try:
        seq = int(row.get("seq") or 0)
    except (TypeError, ValueError):
        seq = 0
    return due, channel_rank, seq, str(row.get("so_id") or ""), int(row.get("id") or 0)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _request(
    endpoint: str,
    params: dict[str, Any],
    api_key: str,
    timeout: int,
    *,
    attempts: int = 3,
    allow_truncated: bool = False,
) -> dict[str, Any]:
    payload = request_json(endpoint, params, api_key=api_key, timeout=min(timeout, 45), attempts=attempts)
    if payload.get("truncated") and not allow_truncated:
        raise RuntimeError(f"{endpoint} 응답이 일부만 반환되었습니다.")
    return payload


def _replace_database(source: Path, destination: Path, attempts: int = 60) -> None:
    last_error: PermissionError | None = None
    for attempt in range(attempts):
        try:
            source.replace(destination)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.25)
    if last_error:
        raise last_error


def _sqlite_copy(source: Path, destination: Path) -> None:
    if destination.exists():
        destination.unlink()
    source_connection = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def _aps_cycle() -> str:
    if not APS_DB_PATH.is_file():
        raise RuntimeError("APS 공정 현황이 아직 수집되지 않았습니다.")
    connection = sqlite3.connect(f"file:{APS_DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT source_refreshed_at FROM sync_meta WHERE id=1"
        ).fetchone()
        cycle = str(row[0] or "").strip() if row else ""
        if not cycle:
            raise RuntimeError("APS 원천 회차시각을 확인할 수 없습니다.")
        return cycle
    finally:
        connection.close()


def _live_meta() -> dict[str, Any]:
    if not DB_PATH.is_file():
        return {}
    connection = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("SELECT * FROM cycle_meta WHERE id=1").fetchone()
        return dict(row) if row else {}
    except sqlite3.Error:
        return {}
    finally:
        connection.close()


def _aps_item_ids() -> set[str]:
    connection = sqlite3.connect(f"file:{APS_DB_PATH.as_posix()}?mode=ro", uri=True)
    try:
        return {
            normalize_text(row[0])
            for row in connection.execute(
                "SELECT DISTINCT item_id FROM aps_plan WHERE oper_id IN ('10','20','45','55','80')"
            )
            if normalize_text(row[0])
        }
    finally:
        connection.close()


def _collect_wip(
    target_items: set[str], api_key: str, timeout: int
) -> tuple[list[dict[str, Any]], str, int]:
    kept: list[dict[str, Any]] = []
    source_times: set[str] = set()
    source_rows = 0

    def fetch_warehouse(
        warehouse: tuple[int, str, str],
    ) -> tuple[int, str, str, dict[str, Any]]:
        stage, warehouse_code, warehouse_name = warehouse
        payload = _request(
            "/api/aps-wip",
            {
                "wh_name": warehouse_name,
                "limit": 0,
                "prompt_context": "똑딱이 2.3 APS 회차 기준 WIP 전체 수집",
            },
            api_key,
            timeout,
        )
        return stage, warehouse_code, warehouse_name, payload

    # WIP도 기본값은 순차 수집이다. 창고별 원천 회차는 모두 확인한다.
    results = map(fetch_warehouse, WAREHOUSES)
    for stage, warehouse_code, warehouse_name, payload in results:
        source_rows += int(payload.get("total_count") or len(payload.get("rows") or []))
        source_time = str(payload.get("source_refreshed_at") or "").strip()
        if source_time:
            source_times.add(source_time)
        for row in payload.get("rows") or []:
            item_id = normalize_text(row.get("item_id"))
            lot_full, lot_base, derived = lot_identity(row.get("lot_no") or row.get("wip_id"))
            if not lot_full or item_id not in target_items:
                continue
            kept.append(
                {
                    "warehouse_code": warehouse_code,
                    "warehouse_name": warehouse_name,
                    "stage": stage,
                    "lot_full": lot_full,
                    "lot_base": lot_base,
                    "derived": int(derived),
                    "item_id": item_id,
                    "quantity": _number(row.get("wip_qty")),
                    "payload_json": json.dumps(row, ensure_ascii=False, sort_keys=True, default=str),
                }
            )
    if len(source_times) != 1:
        raise WipCycleNotReady(
            f"5개 APS WIP의 원천 회차가 일치하지 않습니다: {sorted(source_times)}"
        )
    return kept, next(iter(source_times)), source_rows


def probe_wip_source(api_key: str, timeout: int) -> dict[str, Any]:
    """5개 공정창고의 WIP 회차만 가볍게 조회해 전체 수집 가능 여부를 확인한다."""
    warehouse_sources: list[dict[str, Any]] = []

    def fetch_warehouse(
        warehouse: tuple[int, str, str],
    ) -> tuple[int, str, str, dict[str, Any]]:
        stage, warehouse_code, warehouse_name = warehouse
        payload = _request(
            "/api/aps-wip",
            {
                "wh_name": warehouse_name,
                "limit": 1,
                "prompt_context": "똑딱이 2.3 WIP 회차 감시",
            },
            api_key,
            timeout,
            allow_truncated=True,
        )
        return stage, warehouse_code, warehouse_name, payload

    results = map(fetch_warehouse, WAREHOUSES)
    for stage, warehouse_code, warehouse_name, payload in results:
        warehouse_sources.append(
            {
                "stage": stage,
                "warehouse_code": warehouse_code,
                "warehouse_name": warehouse_name,
                "source_refreshed_at": str(
                    payload.get("source_refreshed_at") or ""
                ).strip(),
                "source_rows": int(
                    payload.get("total_count") or len(payload.get("rows") or [])
                ),
            }
        )

    source_times = sorted(
        {
            row["source_refreshed_at"]
            for row in warehouse_sources
            if row["source_refreshed_at"]
        }
    )
    missing_warehouses = [
        row["warehouse_name"]
        for row in warehouse_sources
        if not row["source_refreshed_at"]
    ]
    ready = not missing_warehouses and len(source_times) == 1
    return {
        "ready": ready,
        "source_refreshed_at": source_times[0] if ready else "",
        "source_times": source_times,
        "missing_warehouses": missing_warehouses,
        "warehouse_sources": warehouse_sources,
        "source_rows": sum(row["source_rows"] for row in warehouse_sources),
    }


def _collect_inventory(
    target_items: set[str], date_from: str, date_to: str, api_key: str, timeout: int,
    observer=None, progress=None, reuse=None, on_error=None,
) -> tuple[list[dict[str, Any]], int]:
    kept: list[dict[str, Any]] = []
    source_rows = 0

    def fetch_warehouse(
        warehouse: tuple[int, str, str],
    ) -> tuple[int, str, str, dict[str, Any]]:
        stage, warehouse_code, warehouse_name = warehouse
        if progress is not None:progress(warehouse_name)
        query = {'date_from': date_from, 'date_to': date_to, 'wh_nm': warehouse_name}
        payload = reuse(warehouse_name, query) if reuse else None
        if payload is None:payload = _request(
            "/api/inventory-ledger-product",
            {
                "date_from": date_from,
                "date_to": date_to,
                "wh_nm": warehouse_name,
                "limit": 0,
                "prompt_context": "똑딱이 2.1 공정창고 수불",
            },
            api_key,
            min(timeout, 45),
            attempts=2,
        )
        if not isinstance(payload.get('rows'),list) or payload.get('truncated'):
            raise ValueError(warehouse_name+' 수불 응답 불완전')
        if payload.get('total_count') is not None and len(payload['rows'])!=int(payload['total_count']):
            raise ValueError(warehouse_name+' 수불 행 수 불일치')
        payload = dict(payload, _inventory_query=query)
        if observer is not None:observer(warehouse_name,payload)
        return stage, warehouse_code, warehouse_name, payload

    # Persist successful warehouses even if a different warehouse fails. Never
    # turn transport errors into an empty warehouse or publish partial live data.
    errors = []
    def collect_each():
        for warehouse in WAREHOUSES:
            try:yield fetch_warehouse(warehouse)
            except Exception as exc:
                errors.append(warehouse[2] + ': ' + str(exc))
                if on_error:on_error(warehouse[2], str(exc))
    results = collect_each()
    for stage, warehouse_code, warehouse_name, payload in results:
        source_rows += int(payload.get("total_count") or len(payload.get("rows") or []))
        for row in payload.get("rows") or []:
            item_id = normalize_text(row.get("gd_cd"))
            lot_full, lot_base, derived = lot_identity(row.get("lot_no"))
            if not lot_full or item_id not in target_items:
                continue
            sub_lot = normalize_text(row.get("sub_lot"))
            kept.append(
                {
                    "snapshot_key": "|".join((warehouse_code, lot_full, sub_lot, item_id)),
                    "warehouse_code": warehouse_code,
                    "warehouse_name": warehouse_name,
                    "stage": stage,
                    "lot_full": lot_full,
                    "lot_base": lot_base,
                    "derived": int(derived),
                    "sub_lot": sub_lot,
                    "item_id": item_id,
                    "lm_qty": _number(row.get("lm_qty")),
                    "ip_qty": _number(row.get("ip_qty")),
                    "chul_qty": _number(row.get("chul_qty")),
                    "stock_qty": _number(row.get("stock_qty")),
                    "payload_json": json.dumps(row, ensure_ascii=False, sort_keys=True, default=str),
                }
            )
    if errors:raise RuntimeError('; '.join(errors))
    return kept, source_rows


def _production_key(row: dict[str, Any]) -> str:
    primary = normalize_text(row.get("pr_no"))
    if primary:
        return primary
    return "|".join(
        normalize_text(row.get(field))
        for field in ("check_sheet_no", "gong_cd", "gd_cd", "pr_dt", "from_dtm")
    )


def _collect_production(
    target_items: set[str], date_from: str, date_to: str, api_key: str, timeout: int
) -> tuple[list[dict[str, Any]], int]:
    payload = _request(
        "/api/production-performance",
        {
            "date_from": date_from,
            "date_to": date_to,
            # 서버에서 실제 적용되는 S관 조건이다. 완료·공정·APS 품목 조건은
            # 서버가 무시할 수 있으므로 아래에서 다시 검증한다.
            "fac_cd": "04",
            "stts": "C",
            "limit": 0,
            "prompt_context": "똑딱이 2.1 완료 생산실적",
        },
        api_key,
        timeout,
    )
    kept_by_key: dict[str, dict[str, Any]] = {}
    for row in payload.get("rows") or []:
        process = normalize_text(row.get("gong_cd"))
        item_id = normalize_text(row.get("gd_cd"))
        lot_full, lot_base, derived = lot_identity(row.get("check_sheet_no"))
        s_factory = normalize_text(row.get("fac_cd")) == "04" or normalize_text(row.get("sachul_fac_cd")) == "04"
        completed = normalize_text(row.get("stts")) == "C"
        if not (lot_full and process in PROCESS_STAGE and item_id in target_items and s_factory and completed):
            continue
        key = _production_key(row)
        kept_by_key[key] = {
            "row_key": key,
            "pr_no": normalize_text(row.get("pr_no")),
            "production_date": str(row.get("pr_dt") or "")[:10],
            "process_code": process,
            "stage": PROCESS_STAGE[process],
            "lot_full": lot_full,
            "lot_base": lot_base,
            "derived": int(derived),
            "item_id": item_id,
            "quantity": _number(row.get("pr_qty")),
            "status": "C",
            "payload_json": json.dumps(row, ensure_ascii=False, sort_keys=True, default=str),
        }
    return list(kept_by_key.values()), int(payload.get("total_count") or len(payload.get("rows") or []))


def _ensure_live_schema(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(aps_plan)")}
    if "original_plan_qty" not in columns:
        connection.execute("ALTER TABLE aps_plan ADD COLUMN original_plan_qty REAL")
    if "allocated_qty" not in columns:
        connection.execute("ALTER TABLE aps_plan ADD COLUMN allocated_qty REAL NOT NULL DEFAULT 0")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS cycle_meta (
            id INTEGER PRIMARY KEY CHECK(id=1),
            aps_source_refreshed_at TEXT NOT NULL,
            wip_source_refreshed_at TEXT NOT NULL,
            baseline_date_from TEXT NOT NULL,
            baseline_captured_at TEXT NOT NULL,
            current_captured_at TEXT NOT NULL,
            inventory_source_rows INTEGER NOT NULL DEFAULT 0,
            production_source_rows INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS baseline_wip (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            warehouse_code TEXT, warehouse_name TEXT, stage INTEGER,
            lot_full TEXT, lot_base TEXT, derived INTEGER,
            item_id TEXT, quantity REAL, payload_json TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_live_wip_lot ON baseline_wip(lot_full,lot_base,stage);
        CREATE TABLE IF NOT EXISTS baseline_inventory (
            snapshot_key TEXT PRIMARY KEY,
            warehouse_code TEXT, warehouse_name TEXT, stage INTEGER,
            lot_full TEXT, lot_base TEXT, derived INTEGER, sub_lot TEXT,
            item_id TEXT, lm_qty REAL, ip_qty REAL, chul_qty REAL,
            stock_qty REAL, payload_json TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_live_baseline_inventory_bucket
            ON baseline_inventory(lot_full,stage,item_id);
        CREATE TABLE IF NOT EXISTS current_inventory AS SELECT * FROM baseline_inventory WHERE 0;
        CREATE UNIQUE INDEX IF NOT EXISTS ix_live_current_inventory_key ON current_inventory(snapshot_key);
        CREATE INDEX IF NOT EXISTS ix_live_current_inventory_bucket
            ON current_inventory(lot_full,stage,item_id);
        CREATE TABLE IF NOT EXISTS baseline_production (
            row_key TEXT PRIMARY KEY, pr_no TEXT, production_date TEXT,
            process_code TEXT, stage INTEGER, lot_full TEXT, lot_base TEXT,
            derived INTEGER, item_id TEXT, quantity REAL, status TEXT, payload_json TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_live_baseline_production_bucket
            ON baseline_production(lot_full,stage,item_id,status);
        CREATE TABLE IF NOT EXISTS current_production AS SELECT * FROM baseline_production WHERE 0;
        CREATE UNIQUE INDEX IF NOT EXISTS ix_live_current_production_key ON current_production(row_key);
        CREATE INDEX IF NOT EXISTS ix_live_current_production_bucket
            ON current_production(lot_full,stage,item_id,status);
        CREATE TABLE IF NOT EXISTS completion_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lot_full TEXT, lot_base TEXT, derived INTEGER, stage INTEGER,
            process_code TEXT, item_id TEXT, baseline_stage INTEGER,
            inventory_qty REAL, production_qty REAL, recognized_qty REAL,
            family_cap REAL, reason TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_live_evidence_bucket ON completion_evidence(process_code,item_id);
        CREATE TABLE IF NOT EXISTS allocation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER, process_code TEXT, item_id TEXT, so_id TEXT,
            due_date TEXT, priority_rank INTEGER, original_qty REAL,
            allocated_qty REAL, remaining_qty REAL
        );
        CREATE INDEX IF NOT EXISTS ix_live_allocation_plan ON allocation(plan_id);
        CREATE INDEX IF NOT EXISTS ix_live_aps_process_item
            ON aps_plan(oper_id,item_id);
        """
    )


INVENTORY_COLUMNS = (
    "snapshot_key", "warehouse_code", "warehouse_name", "stage", "lot_full",
    "lot_base", "derived", "sub_lot", "item_id", "lm_qty", "ip_qty",
    "chul_qty", "stock_qty", "payload_json",
)
PRODUCTION_COLUMNS = (
    "row_key", "pr_no", "production_date", "process_code", "stage", "lot_full",
    "lot_base", "derived", "item_id", "quantity", "status", "payload_json",
)
WIP_COLUMNS = (
    "warehouse_code", "warehouse_name", "stage", "lot_full", "lot_base",
    "derived", "item_id", "quantity", "payload_json",
)


def _replace_rows(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    rows: Iterable[dict[str, Any]],
) -> None:
    connection.execute(f"DELETE FROM {table}")
    marks = ",".join("?" for _ in columns)
    connection.executemany(
        f"INSERT OR REPLACE INTO {table} ({','.join(columns)}) VALUES ({marks})",
        [tuple(row.get(column) for column in columns) for row in rows],
    )


def _inventory_levels(connection: sqlite3.Connection, table: str) -> dict[tuple[str, int, str], tuple[float, float, float]]:
    levels: dict[tuple[str, int, str], tuple[float, float, float]] = {}
    rows = connection.execute(
        f"SELECT lot_full,stage,item_id,SUM(COALESCE(ip_qty,0)) arrival,"
        f"SUM(stock_qty) stock,SUM(chul_qty) outbound FROM {table} "
        "GROUP BY lot_full,stage,item_id"
    )
    for lot_full, stage, item_id, arrival, stock, outbound in rows:
        levels[(str(lot_full), int(stage), str(item_id))] = (
            _number(arrival), _number(stock), _number(outbound)
        )
    return levels


def _production_levels(connection: sqlite3.Connection, table: str) -> dict[tuple[str, int, str], float]:
    return {
        (str(lot_full), int(stage), str(item_id)): _number(quantity)
        for lot_full, stage, item_id, quantity in connection.execute(
            f"SELECT lot_full,stage,item_id,SUM(quantity) FROM {table} "
            "WHERE status='C' GROUP BY lot_full,stage,item_id"
        )
    }


def _wip_origins(connection: sqlite3.Connection) -> tuple[dict[str, tuple[int, float]], dict[str, tuple[int, float]]]:
    exact: dict[str, tuple[int, float]] = {}
    family_rows: dict[str, list[tuple[str, int, float]]] = defaultdict(list)
    for lot_full, lot_base, stage, quantity in connection.execute(
        "SELECT lot_full,lot_base,stage,SUM(quantity) FROM baseline_wip "
        "GROUP BY lot_full,lot_base,stage"
    ):
        current = exact.get(str(lot_full))
        stage_value = int(stage)
        qty = _number(quantity)
        if current is None or stage_value > current[0]:
            exact[str(lot_full)] = (stage_value, qty)
        elif stage_value == current[0]:
            exact[str(lot_full)] = (stage_value, current[1] + qty)
        family_rows[str(lot_base)].append((str(lot_full), stage_value, qty))

    family: dict[str, tuple[int, float]] = {}
    for lot_base, rows in family_rows.items():
        parent_rows = [row for row in rows if row[0] == lot_base]
        selected = parent_rows or rows
        origin_stage = max(row[1] for row in selected)
        capacity = sum(row[2] for row in selected if row[1] == origin_stage)
        family[lot_base] = (origin_stage, capacity)
    return exact, family


def calculate_completion_evidence(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    baseline_inventory = _inventory_levels(connection, "baseline_inventory")
    current_inventory = _inventory_levels(connection, "current_inventory")
    baseline_production = _production_levels(connection, "baseline_production")
    current_production = _production_levels(connection, "current_production")
    exact_wip, family_wip = _wip_origins(connection)
    keys = set(current_inventory) | set(current_production)
    candidates: list[dict[str, Any]] = []
    for lot_full, stage, item_id in sorted(keys):
        full, lot_base, derived = lot_identity(lot_full)
        current_arrival, current_stock, current_outbound = current_inventory.get(
            (lot_full, stage, item_id), (0.0, 0.0, 0.0)
        )
        base_arrival, base_stock, _base_outbound = baseline_inventory.get(
            (lot_full, stage, item_id), (0.0, 0.0, 0.0)
        )
        origin = exact_wip.get(full)
        family_match = False
        if origin is None and derived and lot_base in family_wip:
            origin = family_wip[lot_base]
            family_match = True
        origin_stage, cap = origin or (0, 0.0)
        if stage <= origin_stage:
            continue

        # APS WIP의 당시 공정을 기준점으로 삼고, 현재 확인되는 각 공정의
        # 조회기간 입고/완료실적을 절대수량으로 비교한다. 이월재고(lm_qty)는
        # APS 이전 물량이므로 신규 완료 근거가 아니다. 현재 재고도 이월분을
        # 포함할 수 있어 입고량의 대체 근거로 사용하지 않는다. 늦게 실행해도
        # APS 이후 이미 통과한 중간공정이 기준선에 묻혀 사라지면 안 된다.
        # APS WIP에 없던 신규/외부 LOT는 회차 비교 기준선 이후 증가분을
        # 인정한다. 새 회차의 기준선은 비워 두므로 첫 수집의 최신 상태부터
        # 즉시 반영되고, 이후 갱신에서도 같은 회차의 현재 상태를 유지한다.
        if origin is not None:
            inventory_qty = max(0.0, current_arrival)
            production_qty = max(
                0.0,
                current_production.get((lot_full, stage, item_id), 0.0),
            )
        else:
            inventory_qty = max(0.0, current_arrival - base_arrival)
            production_qty = max(
                0.0,
                current_production.get((lot_full, stage, item_id), 0.0)
                - baseline_production.get((lot_full, stage, item_id), 0.0),
            )
        recognized = max(inventory_qty, production_qty)
        if recognized <= 0:
            continue
        candidates.append(
            {
                "lot_full": full,
                "lot_base": lot_base,
                "derived": int(derived),
                "stage": stage,
                "process_code": STAGE_PROCESS[stage],
                "item_id": item_id,
                "baseline_stage": origin_stage,
                "inventory_qty": inventory_qty,
                "production_qty": production_qty,
                "recognized_qty": recognized,
                "family_cap": cap,
                "outbound_qty": current_outbound,
                "family_match": family_match or origin is not None,
            }
        )

    cap_used: dict[tuple[str, int], float] = defaultdict(float)
    result: list[dict[str, Any]] = []
    for row in candidates:
        recognized = _number(row["recognized_qty"])
        cap = _number(row["family_cap"])
        if row["family_match"] and cap > 0:
            cap_key = (str(row["lot_base"]), int(row["stage"]))
            available = max(0.0, cap - cap_used[cap_key])
            recognized = min(recognized, available)
            cap_used[cap_key] += recognized
        if recognized <= 0:
            continue
        row["recognized_qty"] = recognized
        row["reason"] = (
            (
                "APS WIP 이후 공정별 생산실적·창고입고 중 큰 값 1회 인정"
                if int(row["baseline_stage"]) > 0
                else "신규·외부 LOT의 현재 생산실적·창고입고 중 큰 값 1회 인정"
            )
            + (" · APS LOT 계보수량 상한" if row["family_match"] and cap > 0 else "")
            + (" · 출고 후에도 완료 유지" if _number(row["outbound_qty"]) > 0 else "")
        )
        result.append(row)
    return result


def allocate_evidence(connection: sqlite3.Connection, evidence: list[dict[str, Any]]) -> tuple[float, int]:
    connection.execute("DELETE FROM completion_evidence")
    connection.execute("DELETE FROM allocation")
    connection.execute(
        "UPDATE aps_plan SET original_plan_qty=COALESCE(original_plan_qty,plan_qty),"
        "plan_qty=COALESCE(original_plan_qty,plan_qty),allocated_qty=0 "
        "WHERE oper_id IN ('10','20','45','55','80')"
    )
    evidence_columns = (
        "lot_full", "lot_base", "derived", "stage", "process_code", "item_id",
        "baseline_stage", "inventory_qty", "production_qty", "recognized_qty",
        "family_cap", "reason",
    )
    marks = ",".join("?" for _ in evidence_columns)
    connection.executemany(
        f"INSERT INTO completion_evidence ({','.join(evidence_columns)}) VALUES ({marks})",
        [tuple(row.get(column) for column in evidence_columns) for row in evidence],
    )
    credits: dict[tuple[str, str], float] = defaultdict(float)
    for row in evidence:
        credits[(str(row["process_code"]), normalize_text(row["item_id"]))] += _number(row["recognized_qty"])

    connection.row_factory = sqlite3.Row
    allocated_total = 0.0
    completed_rows = 0
    for (process_code, item_id), credit in sorted(credits.items()):
        plans = [
            dict(row)
            for row in connection.execute(
                "SELECT id,so_id,due_date,initial,demand_type,dest_country,seq,"
                "COALESCE(original_plan_qty,plan_qty,0) original_qty "
                "FROM aps_plan WHERE oper_id=? AND UPPER(TRIM(item_id))=? "
                "AND COALESCE(original_plan_qty,plan_qty,0)>0",
                (process_code, item_id),
            )
        ]
        plans.sort(key=allocation_priority)
        remaining_credit = credit
        for plan in plans:
            original = _number(plan["original_qty"])
            allocated = min(original, remaining_credit)
            remaining = max(0.0, original - allocated)
            rank = allocation_priority(plan)[1]
            connection.execute(
                "UPDATE aps_plan SET plan_qty=?,allocated_qty=? WHERE id=?",
                (remaining, allocated, int(plan["id"])),
            )
            connection.execute(
                "INSERT INTO allocation(plan_id,process_code,item_id,so_id,due_date,priority_rank,"
                "original_qty,allocated_qty,remaining_qty) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    int(plan["id"]), process_code, item_id, str(plan.get("so_id") or ""),
                    str(plan.get("due_date") or ""), rank, original, allocated, remaining,
                ),
            )
            allocated_total += allocated
            if original > 0 and remaining <= 0:
                completed_rows += 1
            remaining_credit -= allocated
            if remaining_credit <= 0:
                break
    return allocated_total, completed_rows


def _initialize_cycle_database(
    temporary_db: Path,
    aps_cycle: str,
    wip_source: str,
    baseline_from: str,
    captured_at: str,
    wip_rows: list[dict[str, Any]],
    inventory_rows: list[dict[str, Any]],
    production_rows: list[dict[str, Any]],
    inventory_source_rows: int,
    production_source_rows: int,
) -> tuple[list[dict[str, Any]], float, int]:
    _sqlite_copy(APS_DB_PATH, temporary_db)
    connection = sqlite3.connect(temporary_db)
    try:
        _ensure_live_schema(connection)
        connection.execute(
            "UPDATE aps_plan SET original_plan_qty=COALESCE(plan_qty,0),allocated_qty=0"
        )
        _replace_rows(connection, "baseline_wip", WIP_COLUMNS, wip_rows)
        # 새 APS 회차를 처음 계산하는 시점의 재고·실적은 과거 기준선이 아니라
        # 사용자가 보려는 최신 현재값이다. 이를 기준선에도 복사하면 앱을 늦게
        # 실행한 만큼 신규/외부 LOT의 완료량이 0으로 상쇄되므로 기준선은 비운다.
        _replace_rows(connection, "baseline_inventory", INVENTORY_COLUMNS, [])
        _replace_rows(connection, "current_inventory", INVENTORY_COLUMNS, inventory_rows)
        _replace_rows(connection, "baseline_production", PRODUCTION_COLUMNS, [])
        _replace_rows(connection, "current_production", PRODUCTION_COLUMNS, production_rows)
        connection.execute("DELETE FROM completion_evidence")
        connection.execute("DELETE FROM allocation")
        connection.execute(
            "INSERT OR REPLACE INTO cycle_meta VALUES (1,?,?,?,?,?,?,?)",
            (
                aps_cycle, wip_source, baseline_from, captured_at, captured_at,
                inventory_source_rows, production_source_rows,
            ),
        )
        evidence = calculate_completion_evidence(connection)
        allocated, completed_rows = allocate_evidence(connection, evidence)
        connection.commit()
        return evidence, allocated, completed_rows
    finally:
        connection.close()


def _recalculate_database(
    temporary_db: Path,
    inventory_rows: list[dict[str, Any]],
    production_rows: list[dict[str, Any]],
    captured_at: str,
    inventory_source_rows: int,
    production_source_rows: int,
) -> tuple[list[dict[str, Any]], float, int]:
    _sqlite_copy(DB_PATH, temporary_db)
    connection = sqlite3.connect(temporary_db)
    try:
        _ensure_live_schema(connection)
        _replace_rows(connection, "current_inventory", INVENTORY_COLUMNS, inventory_rows)
        _replace_rows(connection, "current_production", PRODUCTION_COLUMNS, production_rows)
        evidence = calculate_completion_evidence(connection)
        allocated, completed_rows = allocate_evidence(connection, evidence)
        connection.execute(
            "UPDATE cycle_meta SET current_captured_at=?,inventory_source_rows=?,"
            "production_source_rows=? WHERE id=1",
            (captured_at, inventory_source_rows, production_source_rows),
        )
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("실시간 생산 필요 SQLite 무결성 오류")
        return evidence, allocated, completed_rows
    finally:
        connection.close()


def _backup_and_replace(temporary_db: Path, stamp: str) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.is_file():
        shutil.copy2(DB_PATH, BACKUP_DIR / f"current_production_need_before_{stamp}.sqlite")
    _replace_database(temporary_db, DB_PATH)
    backups = sorted(
        BACKUP_DIR.glob("current_production_need_before_*.sqlite"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in backups[3:]:
        try:
            path.unlink()
        except OSError:
            pass


def refresh(api_key: str = "", timeout: int = 240, *, inventory_observer=None, inventory_progress=None, inventory_reuse=None, inventory_error=None) -> dict[str, Any]:
    refresh_started = time.perf_counter()
    phase_seconds: dict[str, float] = {}
    now = datetime.now().astimezone()
    captured_at = now.isoformat(timespec="seconds")
    stamp = now.strftime("%Y%m%d_%H%M%S")
    aps_cycle = _aps_cycle()
    previous_meta = _live_meta()
    previous_status = _read_json(STATUS_PATH)
    aps_changed = str(previous_meta.get("aps_source_refreshed_at") or "") != aps_cycle
    try:
        previous_revision = int(previous_status.get("calculation_revision") or 0)
    except (TypeError, ValueError):
        previous_revision = 0
    # 0.1.21의 이전 계산 DB는 새 회차 첫 수집값을 기준선에도 복사했다.
    # 같은 APS 회차여도 개정 전 결과라면 한 번 재초기화해 최신값을 온전히 반영한다.
    new_cycle = aps_changed or previous_revision != CALCULATION_REVISION
    target_items = _aps_item_ids()
    if not target_items:
        raise RuntimeError("현재 APS 회차의 생산 품목코드가 없습니다.")

    baseline_from = (
        (now.date() - timedelta(days=1)).isoformat()
        if new_cycle
        else str(previous_meta.get("baseline_date_from") or (now.date() - timedelta(days=1)).isoformat())
    )
    date_to = date.today().isoformat()
    wip_rows: list[dict[str, Any]] = []
    wip_source = str(previous_meta.get("wip_source_refreshed_at") or "")
    wip_source_rows = 0
    if new_cycle:
        if inventory_progress:inventory_progress('WIP 회차 확인')
        previous_wip = str(previous_meta.get("wip_source_refreshed_at") or "")
        phase_started = time.perf_counter()
        wip_probe = probe_wip_source(api_key, min(timeout, 30))
        phase_seconds["wip_probe"] = round(time.perf_counter() - phase_started, 3)
        probed_wip_source = str(wip_probe.get("source_refreshed_at") or "")
        wip_not_ready = not bool(wip_probe.get("ready"))
        wip_not_changed = bool(
            aps_changed
            and previous_wip
            and probed_wip_source
            and probed_wip_source <= previous_wip
        )
        wip_before_aps = bool(
            aps_changed
            and probed_wip_source
            and probed_wip_source < aps_cycle
        )
        if wip_not_ready or wip_not_changed or wip_before_aps:
            if wip_not_ready:
                source_times = wip_probe.get("source_times") or []
                missing = wip_probe.get("missing_warehouses") or []
                detail = (
                    f"확인된 WIP 회차 {source_times or '없음'}"
                    + (f" · 회차 미확인 {', '.join(missing)}" if missing else "")
                )
            elif wip_not_changed:
                detail = f"현재 WIP 회차 {probed_wip_source or '미확인'}"
            else:
                detail = (
                    f"WIP 회차 {probed_wip_source} · APS 회차 {aps_cycle}보다 이전"
                )
            result = {
                **previous_status,
                "status": "waiting_wip",
                "checked_at": captured_at,
                "wip_checked_at": captured_at,
                "wip_monitoring": True,
                "attempted_aps_source_refreshed_at": aps_cycle,
                "attempted_wip_source_refreshed_at": probed_wip_source,
                "wip_source_refreshed_at_values": wip_probe.get("source_times") or [],
                "wip_missing_warehouses": wip_probe.get("missing_warehouses") or [],
                "wip_warehouse_sources": wip_probe.get("warehouse_sources") or [],
                "retained_reason": (
                    "새 APS 회차에 맞는 5개 공정창고 WIP 갱신을 감시 중입니다. "
                    f"{detail}. 이전 정상 계산을 유지하며 1분 뒤 다시 확인합니다."
                ),
            }
            _atomic_json(STATUS_PATH, result)
            return result
        phase_started = time.perf_counter()
        try:
            if inventory_progress:inventory_progress('WIP 기준 스냅샷 수집')
            wip_rows, wip_source, wip_source_rows = _collect_wip(
                target_items, api_key, min(timeout,60)
            )
        except WipCycleNotReady as exc:
            result = {
                **previous_status,
                "status": "waiting_wip",
                "checked_at": captured_at,
                "wip_checked_at": captured_at,
                "wip_monitoring": True,
                "attempted_aps_source_refreshed_at": aps_cycle,
                "attempted_wip_source_refreshed_at": probed_wip_source,
                "wip_source_refreshed_at_values": wip_probe.get("source_times") or [],
                "wip_warehouse_sources": wip_probe.get("warehouse_sources") or [],
                "retained_reason": (
                    f"전체 WIP 수집 중 회차가 전환되었습니다. {exc}. "
                    "이전 정상 계산을 유지하며 1분 뒤 다시 확인합니다."
                ),
            }
            _atomic_json(STATUS_PATH, result)
            return result
        phase_seconds["wip"] = round(time.perf_counter() - phase_started, 3)
        if aps_changed and previous_wip and wip_source <= previous_wip:
            result = {
                **previous_status,
                "status": "waiting_wip",
                "checked_at": captured_at,
                "wip_checked_at": captured_at,
                "wip_monitoring": True,
                "attempted_aps_source_refreshed_at": aps_cycle,
                "attempted_wip_source_refreshed_at": wip_source,
                "retained_reason": (
                    "전체 WIP 수집 결과가 아직 이전 회차라 기존 정상 계산을 유지하며 "
                    "1분 뒤 다시 확인합니다."
                ),
            }
            _atomic_json(STATUS_PATH, result)
            return result

    phase_started = time.perf_counter()
    inventory_rows, inventory_source_rows = _collect_inventory(
        target_items, baseline_from, date_to, api_key, timeout,
        **({'observer':inventory_observer} if inventory_observer is not None else {}),
        **({'reuse':inventory_reuse} if inventory_reuse is not None else {}),
        **({'on_error':inventory_error} if inventory_error is not None else {}),
        **({'progress':inventory_progress} if inventory_progress is not None else {}),
    )
    phase_seconds["inventory"] = round(time.perf_counter() - phase_started, 3)
    phase_started = time.perf_counter()
    if inventory_progress:inventory_progress('완료실적 수집')
    production_rows, production_source_rows = _collect_production(
        target_items, baseline_from, date_to, api_key, timeout
    )
    phase_seconds["production"] = round(time.perf_counter() - phase_started, 3)
    if inventory_progress:inventory_progress('부족수량 계산')
    if _aps_cycle() != aps_cycle:
        raise RuntimeError("계산 수집 중 APS 회차가 바뀌어 마지막 정상 결과를 유지합니다.")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_db = DATA_DIR / "current_production_need.building.sqlite"
    if temporary_db.exists():
        temporary_db.unlink()
    if new_cycle:
        phase_started = time.perf_counter()
        evidence, allocated, completed_rows = _initialize_cycle_database(
            temporary_db,
            aps_cycle,
            wip_source,
            baseline_from,
            captured_at,
            wip_rows,
            inventory_rows,
            production_rows,
            inventory_source_rows,
            production_source_rows,
        )
    else:
        phase_started = time.perf_counter()
        evidence, allocated, completed_rows = _recalculate_database(
            temporary_db,
            inventory_rows,
            production_rows,
            captured_at,
            inventory_source_rows,
            production_source_rows,
        )
    phase_seconds["calculation"] = round(time.perf_counter() - phase_started, 3)
    _backup_and_replace(temporary_db, stamp)

    with closing(sqlite3.connect(DB_PATH)) as connection:
        stored_rows = int(connection.execute("SELECT COUNT(*) FROM aps_plan").fetchone()[0])
        remaining_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM aps_plan WHERE oper_id IN ('10','20','45','55','80') AND plan_qty>0"
            ).fetchone()[0]
        )
    result = {
        "status": "success",
        "calculation_revision": CALCULATION_REVISION,
        "database": str(DB_PATH),
        "aps_source_refreshed_at": aps_cycle,
        "wip_source_refreshed_at": wip_source,
        "wip_checked_at": captured_at if new_cycle else previous_status.get("wip_checked_at"),
        "wip_monitoring": False,
        "baseline_date_from": baseline_from,
        "refreshed_at": datetime.now().astimezone().isoformat(timespec='seconds'),
        "collection_started_at": captured_at,
        "reset": new_cycle,
        "aps_changed": aps_changed,
        "stored_rows": stored_rows,
        "remaining_rows": remaining_rows,
        "baseline_wip_rows": len(wip_rows) if new_cycle else None,
        "wip_source_rows": (
            wip_source_rows if new_cycle else previous_status.get("wip_source_rows")
        ),
        "inventory_rows": len(inventory_rows),
        "inventory_source_rows": inventory_source_rows,
        "production_rows": len(production_rows),
        "production_source_rows": production_source_rows,
        "evidence_rows": len(evidence),
        "recognized_qty": sum(_number(row.get("recognized_qty")) for row in evidence),
        "allocated_qty": allocated,
        "completed_plan_rows": completed_rows,
        "phase_seconds": phase_seconds,
        "collection_workers": COLLECTION_WORKERS,
        "elapsed_seconds": round(time.perf_counter() - refresh_started, 3),
    }
    _atomic_json(STATUS_PATH, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="APS 회차 기준 실시간 생산 필요수량 계산")
    parser.add_argument(
        "--api-key",
        default="",
    )
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    try:
        result = refresh(args.api_key, args.timeout)
    except Exception as exc:
        previous = _read_json(STATUS_PATH)
        retained = {
            **previous,
            "status": "retained",
            "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "retained_reason": f"{type(exc).__name__}: {exc}",
        }
        _atomic_json(STATUS_PATH, retained)
        print(json.dumps(retained, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
