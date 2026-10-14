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


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))
from services.erp_api_client import request_json
from services.data_location import resolve_data_root
from services.backward_source import SOURCE, adapt, outbound_state, validate_payload, supplement_order_quantities

DATA_DIR = Path(
    os.getenv(
        "DDOKDDAK_PROD3_PROCESS_DATA_DIR",
        str(resolve_data_root() / "backward-v1/process-status"),
    )
)
DB_PATH = DATA_DIR / "aps_process_status.sqlite"
STATUS_PATH = DATA_DIR / "snapshot" / "refresh_status.json"
BACKUP_DIR = DATA_DIR / "backup"
RAW_DIR = DATA_DIR / "raw_api"
BASE_URL = "https://plan.interojo.net"
FACTORY = "S관(3공장)"
PROCESS_CODES = ("10", "20", "45", "55", "80")
SAFETY_DEMAND_TYPES = {"안전", "안전(국내)", "안전(해외)", "안전재고(국내)", "안전재고(해외)"}
FIELDS = (
    "plan_date", "oper_id", "res_id", "res_site_id", "demand_id",
    "demand_item_id", "demand_item_name", "item_id", "plan_qty", "due_date",
    "cust_id", "cust_name", "demand_group_id", "demand_qty", "demand_priority",
    "demand_type", "mark_type", "dest_country", "item_cd", "pack_unit",
    "item_name2", "initial", "so_id", "seq", "item_group_id", "item_name",
    "item_cd_5", "power", "target_datetime",
)


def _repair_legacy_cp949(value: Any) -> Any:
    """이미 Latin-1 문자로 깨진 CP949 한글만 안전하게 복원한다."""
    if isinstance(value, dict):
        return {key: _repair_legacy_cp949(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_repair_legacy_cp949(item) for item in value]
    if not isinstance(value, str) or any("가" <= char <= "힣" for char in value):
        return value
    try:
        repaired = value.encode("latin-1").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    return repaired if any("가" <= char <= "힣" for char in repaired) else value


def _request(endpoint: str, params: dict[str, Any], api_key: str, timeout: int) -> dict[str, Any]:
    payload = request_json(endpoint, params, api_key=api_key, timeout=timeout)
    return _repair_legacy_cp949(payload)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    temporary.replace(path)


def _read_status(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


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
        date_token = order_no[1:9]
        if not order_no.startswith("R") or len(date_token) != 8 or not date_token.isdigit():
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


def _sync_wip_metadata(local, source):
    if (local.get('wip_at'), local.get('wip_count')) == (source.get('wip_at'), source.get('wip_count')):
        return False
    connection = sqlite3.connect(DB_PATH)
    try:
        row = connection.execute('SELECT run_key,payload_json FROM source_provenance WHERE id=1').fetchone()
        if not row or row[0] != source['run_key']:
            raise RuntimeError('APS 저장 회차가 일치하지 않아 WIP 상태 갱신을 보류합니다.')
        payload = json.loads(row[1])
        payload.update(wip_at=source['wip_at'], wip_count=source['wip_count'])
        connection.execute('UPDATE source_provenance SET wip_at=?,payload_json=? WHERE id=1',
                           (source['wip_at'], json.dumps(payload,ensure_ascii=False)))
        connection.commit()
    finally:
        connection.close()
    local.update(wip_at=source['wip_at'], wip_count=source['wip_count'])
    _atomic_json(STATUS_PATH, local)
    return True


def refresh(api_key: str = "", timeout: int = 300) -> dict[str, Any]:
    from services.collection_lock import collection_lock
    from services.aps_collection_policy import ApsPolicy
    with collection_lock():
        policy = ApsPolicy()
        from services.dmz_read_client import route_to_dmz
        stored_mode = route_to_dmz('/api/aps-plan')
        if not stored_mode and policy.blocked():
            raise RuntimeError('APS 실패 후 서버 보호 대기 중입니다. 재시도 간격은 10·20·40·60분입니다.')
        if not stored_mode:
            policy.begin()
        try:
            before = outbound_state(_request('/api/aps-outbound-status', {'limit':0}, api_key, timeout))
            local = _read_status(STATUS_PATH)
            if DB_PATH.exists() and local.get('run_key') == before['run_key'] and local.get('status') == 'success':
                wip_changed = _sync_wip_metadata(local, before)
                policy.succeeded(before['run_key'])
                return {**local, 'changed':False, 'wip_metadata_changed':wip_changed}
            result = _refresh_snapshot(api_key, timeout, before)
            if result.get('status') != 'success':
                raise RuntimeError('APS 전체 수집이 완료되지 않아 이전 자료를 유지합니다.')
            policy.succeeded(before['run_key'])
            return {**result, 'changed':True}
        except Exception:
            if not stored_mode:
                policy.failed()
            raise


def _refresh_snapshot(api_key, timeout, before):
    payload = _request("/api/aps-backward-plan", {"limit": 0}, api_key, timeout)
    plan = _request("/api/aps-plan", {"limit": 0}, api_key, timeout)
    arrange = _request("/api/aps-arrange", {"limit": 0}, api_key, timeout)
    if arrange.get("truncated") or not arrange.get("rows"):
        raise RuntimeError("기계등록 전체 정보를 확인할 수 없습니다.")
    meta_after = outbound_state(_request("/api/aps-outbound-status", {"limit": 0}, api_key, timeout))
    # WIP can finish publishing while APS is being read. Only APS inputs
    # determine whether this snapshot changed during collection.
    aps_keys = ('source_identity', 'run_key', 'backward_at', 'backward_count', 'plan_at', 'plan_count')
    if any(before.get(key) != meta_after.get(key) for key in aps_keys):
        raise RuntimeError("APS 수집 도중 아웃바운드 회차가 변경되었습니다.")
    validate_payload(payload, meta_after, "backward")
    validate_payload(plan, meta_after, "plan")
    source_rows = list(payload.get("rows") or [])
    rows = adapt(source_rows, plan["rows"], arrange["rows"])
    supplements, supplement_errors = supplement_order_quantities(rows, _request, api_key, timeout)
    now = datetime.now().astimezone()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    raw_path = RAW_DIR / f"aps_s_factory_{stamp}.json.gz"
    _write_raw(raw_path, {"collected_at": now.isoformat(timespec="seconds"), "factory": FACTORY, "meta": meta_after, "payload": payload, "plan_metadata": plan, "arrange": arrange, "supplements": supplements})
    if not rows:
        previous = _read_status(STATUS_PATH)
        if DB_PATH.exists() and int(previous.get("stored_rows") or 0) > 0:
            result = dict(previous)
            result.update({
                "status": "retained",
                "checked_at": now.isoformat(timespec="seconds"),
                "retained_reason": "APS 원천 응답의 S관 공정 데이터가 0건이어서 마지막 정상 데이터를 유지합니다.",
                "attempted_source_rows": len(source_rows),
                "attempted_source_refreshed_at": str(payload.get("source_refreshed_at") or ""),
                "attempted_raw_snapshot": str(raw_path),
            })
            _atomic_json(STATUS_PATH, result)
            return result
        raise RuntimeError("S관 공정 진행 데이터가 없습니다.")

    order_remarks, remark_error = _collect_domestic_order_remarks(rows, api_key, timeout)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_db = DATA_DIR / "aps_process_status.building.sqlite"
    if temporary_db.exists():
        temporary_db.unlink()
    connection = sqlite3.connect(temporary_db)
    try:
        _initialize(connection)
        connection.execute("CREATE TABLE source_provenance (id INTEGER PRIMARY KEY CHECK(id=1), source_kind TEXT, source_identity TEXT, run_key TEXT, backward_at TEXT, plan_at TEXT, wip_at TEXT, payload_json TEXT)")
        connection.execute("INSERT INTO source_provenance VALUES (1,?,?,?,?,?,?,?)", (
            SOURCE, meta_after["source_identity"], meta_after["run_key"], meta_after["backward_at"],
            meta_after["plan_at"], meta_after["wip_at"], json.dumps(meta_after, ensure_ascii=False)))
        connection.execute("CREATE TABLE backward_mapping (aps_id INTEGER PRIMARY KEY, original_site TEXT, effective_factory TEXT, factory_basis TEXT, metadata_source TEXT, demand_qty_status TEXT, original_json TEXT)")
        connection.executemany("INSERT INTO backward_mapping VALUES (?,?,?,?,?,?,?)", [
            (i, row["_backward"].get("res_site_id"), row["_backward"].get("eff_fac_cd"),
             row["_backward"].get("fac_basis"), row["_metadata"]["source"],
             row["_metadata"]["demand_qty_status"], json.dumps(row["_backward"], ensure_ascii=False))
            for i, row in enumerate(rows, 1)])
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
        **meta_after,
        "unknown_order_qty_rows": sum(row.get("demand_qty") is None for row in rows),
        "supplement_errors": supplement_errors,
        "source_rows": len(source_rows), "stored_rows": len(rows), "order_count": order_count,
        "source_refreshed_at": str(payload.get("source_refreshed_at") or ""),
        "refreshed_at": now.isoformat(timespec="seconds"), "raw_snapshot": str(raw_path),
        "remark_count": len(order_remarks), "remark_error": remark_error,
    }
    _atomic_json(STATUS_PATH, result)
    return result


from services.collection_lock import serialized_collection


@serialized_collection
def main() -> int:
    parser = argparse.ArgumentParser(description="생산3팀 S관 세부 진행 현황 스냅샷 수집")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    print(json.dumps(refresh(args.api_key, args.timeout), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
