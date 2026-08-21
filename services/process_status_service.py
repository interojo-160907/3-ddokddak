from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import date
from pathlib import Path

from config import DATA_CENTER_DIR


DATA_DIR = Path(
    os.getenv(
        "DDOKDDAK_PROD3_PROCESS_DATA_DIR",
        str(DATA_CENTER_DIR / "process-status"),
    )
)
DB_PATH = DATA_DIR / "aps_process_status.sqlite"
STATUS_PATH = DATA_DIR / "snapshot" / "refresh_status.json"
PROCESS_NAMES = {"10": "사출", "20": "분리", "45": "하이드레이션", "55": "접착", "80": "누수규격", "85": "포장"}
PROCESS_ORDER = tuple(PROCESS_NAMES.values())


def _number(value: object) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def power_sort_key(value: object) -> tuple[int, float]:
    """SCM 업무 순서: 양수(큰 값부터), 0, 음수(0에 가까운 값부터), 미확인."""
    number = _number(value)
    if number is None:
        return (3, float("inf"))
    if number > 0:
        return (0, -number)
    if number == 0:
        return (1, 0.0)
    return (2, -number)


def classification_sort_key(value: object) -> tuple:
    """SCM Control Tower와 Obsidian에 정의된 신규분류요약 업무 순서."""
    text = str(value or "").strip()
    lowered = text.casefold()
    color = 1 if "color" in lowered else 0
    cycle = 0 if "frp" in lowered else 1 if "1-day" in lowered else 2
    material = 1 if lowered.startswith("si_") else 0
    if "fix2" in lowered:
        lens_type = 4
    elif "fix" in lowered:
        lens_type = 3
    elif "toric" in lowered:
        lens_type = 2
    elif "m/f" in lowered or "multi" in lowered:
        lens_type = 1
    elif "sph" in lowered:
        lens_type = 0
    else:
        lens_type = 5
    return (color, cycle, material, lens_type, lowered)


def optical_specs_from_item_code(
    item_code: object,
    classification: object,
    power: object,
) -> dict[str, object]:
    """APS 품목코드의 규격 꼬리를 POWER/CP/AXIS/ADD로 분리한다.

    Obsidian 운영 규칙:
    - 멀티: POWER + ADD (예: R1121-05.25+1.00)
    - 토릭: POWER + CP + AXIS 3자리 (예: R1052-01.50-1.75020)
    """
    code = str(item_code or "").strip().upper()
    category = str(classification or "").strip().casefold()
    power_text = str(power or "").strip()
    result: dict[str, object] = {
        "POWER": power_text,
        "CP": "",
        "AXIS": "",
        "ADD": "",
        "_POWER_NUM": _number(power_text),
        "_CP_NUM": None,
        "_AXIS_NUM": None,
        "_ADD_NUM": None,
    }
    if not code or not power_text:
        return result
    position = code.rfind(power_text.upper())
    suffix = code[position + len(power_text):] if position >= 0 else ""
    if "m/f" in category or "multi" in category:
        match = re.fullmatch(r"([+-]\d+(?:\.\d+)?)", suffix)
        if match:
            result["ADD"] = match.group(1)
            result["_ADD_NUM"] = _number(match.group(1))
    elif "toric" in category:
        match = re.fullmatch(r"([+-]\d+(?:\.\d+)?)(\d{3})", suffix)
        if match:
            result["CP"] = match.group(1)
            result["AXIS"] = match.group(2)
            result["_CP_NUM"] = _number(match.group(1))
            result["_AXIS_NUM"] = int(match.group(2))
    return result


def business_sort_key(row: dict) -> tuple:
    return (
        row.get("납기일") or "9999-12-31",
        power_sort_key(row.get("_POWER_NUM", row.get("POWER"))),
        classification_sort_key(row.get("신규분류요약")),
        row.get("_제품정렬") or row.get("T코드") or row.get("품목코드") or "",
        row.get("수주번호") or "",
        row.get("_CP_NUM") if row.get("_CP_NUM") is not None else float("inf"),
        row.get("_AXIS_NUM") if row.get("_AXIS_NUM") is not None else 999,
        row.get("_ADD_NUM") if row.get("_ADD_NUM") is not None else float("inf"),
    )


def _channel(demand_type: str, destination: str) -> str:
    demand = str(demand_type or "").strip()
    if demand == "PB":
        return "PB"
    if demand in {"안전", "안전(국내)", "안전(해외)", "안전재고(국내)", "안전재고(해외)"}:
        return "안전"
    # 현재 원천 스냅샷 일부 한글이 손상돼도 목적국이 있으면 해외로 안정적으로 판별한다.
    if str(destination or "").strip():
        return "해외"
    return "국내"


class ProcessStatusService:
    def status(self) -> dict:
        try:
            return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def load_rows(self, search: str = "", process: str = "전체") -> list[dict]:
        if not DB_PATH.is_file():
            return []
        connection = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            clauses = []
            params: list[object] = []
            if search.strip():
                token = f"%{search.strip()}%"
                clauses.append("(so_id LIKE ? OR initial LIKE ? OR demand_item_id LIKE ? OR demand_item_name LIKE ? OR demand_group_id LIKE ?)")
                params.extend([token] * 5)
            if process != "전체":
                code = next((key for key, value in PROCESS_NAMES.items() if value == process), "")
                if code:
                    clauses.append("oper_id=?")
                    params.append(code)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            raw = connection.execute(
                "SELECT so_id,initial,demand_group_id,demand_item_id,demand_item_name,due_date,power,"
                "demand_type,dest_country,item_cd,oper_id,GROUP_CONCAT(DISTINCT item_id) item_ids,"
                "SUM(COALESCE(plan_qty,0)) plan_qty,MAX(COALESCE(demand_qty,0)) demand_qty "
                f"FROM aps_plan{where} GROUP BY so_id,initial,demand_group_id,demand_item_id,demand_item_name,"
                "due_date,power,demand_type,dest_country,item_cd,oper_id",
                params,
            ).fetchall()
            item_names = {
                str(row["item_id"] or "").strip(): str(
                    row["item_name"] or row["item_name2"] or ""
                ).strip()
                for row in connection.execute(
                    "SELECT item_id,MAX(NULLIF(item_name,'')) item_name,"
                    "MAX(NULLIF(item_name2,'')) item_name2 FROM aps_plan "
                    "WHERE substr(item_id,1,1) IN ('P','Q','R') GROUP BY item_id"
                ).fetchall()
                if row["item_id"]
            }
        finally:
            connection.close()

        grouped: dict[tuple, dict] = {}
        for item in raw:
            key = tuple(item[name] or "" for name in (
                "so_id", "initial", "demand_group_id", "demand_item_id", "demand_item_name",
                "due_date", "power", "demand_type", "dest_country", "item_cd",
            ))
            specs = optical_specs_from_item_code(key[3], key[2], key[6])
            target = grouped.setdefault(
                key,
                {
                    "신규분류요약": key[2], "이니셜": key[1], "수주번호": key[0],
                    "품목코드": key[3], "T코드": key[3], "P코드": "", "Q코드": "", "R코드": "",
                    "품명": key[4], "품명판매": key[4], "품명P": "", "품명Q": "", "품명R": "",
                    "POWER": specs["POWER"], "CP": specs["CP"], "AXIS": specs["AXIS"],
                    "ADD": specs["ADD"], "납기일": key[5],
                    "진행현황": _channel(key[7], key[8]), "수주수량": float(item["demand_qty"] or 0),
                    "_POWER_NUM": specs["_POWER_NUM"], "_CP_NUM": specs["_CP_NUM"],
                    "_AXIS_NUM": specs["_AXIS_NUM"], "_ADD_NUM": specs["_ADD_NUM"],
                    "_제품정렬": key[9] or key[3],
                    **{name: 0.0 for name in PROCESS_ORDER},
                },
            )
            for code in str(item["item_ids"] or "").split(","):
                code = code.strip()
                if code[:1] in {"P", "Q", "R"}:
                    field = f"{code[0]}코드"
                    values = [value for value in str(target[field]).split(" / ") if value]
                    if code not in values:
                        values.append(code)
                        target[field] = " / ".join(values)
                    name = item_names.get(code, "")
                    if name:
                        name_field = f"품명{code[0]}"
                        names = [value for value in str(target[name_field]).split(" / ") if value]
                        if name not in names:
                            names.append(name)
                            target[name_field] = " / ".join(names)
                    # 판매 품목코드에 일부 규격이 생략된 예외는 실제 공정코드에서 보완한다.
                    code_specs = optical_specs_from_item_code(code, key[2], key[6])
                    for field in ("CP", "AXIS", "ADD"):
                        if not target.get(field) and code_specs.get(field):
                            target[field] = code_specs[field]
                            target[f"_{field}_NUM"] = code_specs[f"_{field}_NUM"]
            target[PROCESS_NAMES.get(str(item["oper_id"]), str(item["oper_id"]))] += float(item["plan_qty"] or 0)
        return sorted(grouped.values(), key=business_sort_key)

    def summary(self, rows: list[dict]) -> dict:
        orders = {row["수주번호"] for row in rows if row["수주번호"]}
        packaging = {row["수주번호"] for row in rows if row.get("포장", 0) > 0}
        production = {
            row["수주번호"] for row in rows
            if any(float(row.get(name) or 0) > 0 for name in PROCESS_ORDER[:-1])
        }
        today = date.today().isoformat()
        risks = {
            row["수주번호"] for row in rows
            if row.get("납기일") and row["납기일"] < today
            and any(float(row.get(name) or 0) > 0 for name in PROCESS_ORDER[:-1])
        }
        return {"진행대상": len(orders), "포장진행": len(packaging), "생산미완료": len(production), "납기위험": len(risks)}
