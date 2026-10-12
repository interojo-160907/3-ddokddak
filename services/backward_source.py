"""Backward quantity adapter; Plan contributes descriptive metadata only."""
from __future__ import annotations

from collections import defaultdict
import math
import re

SOURCE = "aps-backward-plan:v2"
PROCESS_CODES = {"10", "20", "45", "55", "80"}
COMMON_FIELDS = (
    "demand_item_name", "cust_id", "cust_name", "demand_group_id",
    "demand_priority", "demand_type", "mark_type", "dest_country",
    "item_cd", "pack_unit",
)
DEMAND_FIELDS = COMMON_FIELDS + ("demand_qty", "initial", "so_id", "seq", "power")


def norm(value):
    return str(value or "").strip().upper()


def applicable_factories(original, registered):
    """Explicit outbound site wins; a blank site permits every registered factory."""
    site = norm(original.get("res_site_id"))
    if not site:
        return set(registered)
    aliases = {"01": "01", "02": "02", "04": "04",
               "A관(1공장)": "01", "C관(2공장)": "02", "S관(3공장)": "04"}
    factory = aliases.get(site)
    if factory is None and norm(original.get("fac_basis")) == "A":
        # The outbound API also resolves explicit site identifiers.
        factory = norm(original.get("eff_fac_cd"))
    if factory not in {"01", "02", "04"}:
        raise RuntimeError(f"아웃바운드 사이트 공장 미확인: {site}")
    return {factory}


def outbound_state(payload):
    if payload.get("truncated"):
        raise RuntimeError("아웃바운드 상태 응답이 불완전합니다.")
    rows = {r.get("tbl"): r for r in payload.get("rows", [])}
    run = rows.get("RUN", {})
    if run.get("run_state") != "D" or not run.get("run_key"):
        raise RuntimeError("APS 아웃바운드 적재 완료 회차가 아닙니다.")
    result = {"source_kind": SOURCE, "run_key": run["run_key"]}
    for key, table in (("backward", "IF_APS_BACKWARD_PLAN"),
                       ("plan", "IF_APS_PROP_PLAN"), ("wip", "IF_APS_ODV_WIP")):
        row = rows.get(table, {})
        if not row.get("last_upd") or int(row.get("row_cnt") or 0) <= 0:
            raise RuntimeError(f"{table} 원천 상태를 확인할 수 없습니다.")
        result[key + "_at"] = row["last_upd"]
        result[key + "_count"] = int(row["row_cnt"])
    result["source_identity"] = "|".join((SOURCE, result["run_key"], result["backward_at"]))
    return result


def validate_payload(payload, state, key):
    rows = payload.get("rows") or []
    if (payload.get("truncated") or len(rows) != state[key + "_count"]
            or payload.get("source_refreshed_at") != state[key + "_at"]):
        raise RuntimeError(f"{key} 원천 회차 또는 전체 행 수가 맞지 않습니다.")


def unique_fields(rows, fields):
    result = {}
    conflicts = []
    for field in fields:
        values = {r[field] for r in rows if r.get(field) not in (None, "")}
        if len(values) == 1:
            result[field] = next(iter(values))
        elif len(values) > 1:
            conflicts.append(field)
    return result, conflicts


def adapt(backward, plan, arrange):
    demands, groups, items, factories, names = (defaultdict(list) for _ in range(5))
    for row in plan:
        key = norm(row.get("demand_id"))
        demands[key].append(row)
        groups[key.rsplit("_", 1)[0]].append(row)
        items[norm(row.get("item_id"))].append(row)
    for row in arrange:
        key = (norm(row.get("sale_cd")), norm(row.get("oper_id")))
        factories[key].append(norm(row.get("fac_cd")))
        names[key].append(row)
    result = []
    for original in backward:
        oper = norm(original.get("oper_cd"))
        if oper not in PROCESS_CODES:
            continue
        machine_key = (norm(original.get("sale_cd")), oper)
        registered = set(factories[machine_key]) - {""}
        eligible = applicable_factories(original, registered)
        if "04" not in eligible:
            continue
        key = norm(original.get("demand_id"))
        metadata, conflicts = unique_fields(demands[key], DEMAND_FIELDS)
        matched = bool(demands[key])
        if not matched:
            metadata, conflicts = unique_fields(groups[key.rsplit("_", 1)[0]], COMMON_FIELDS)
        if conflicts:
            raise RuntimeError(f"수요 보조정보 충돌: {key} / {conflicts}")
        parts = str(original.get("demand_id") or "").rsplit("_", 3)
        if len(parts) != 4 or not parts[2].isdigit():
            raise RuntimeError(f"수요 ID 구조 미확인: {key}")
        raw_initial, order, seq, _ = parts
        qty = float(original["backward_qty"])
        if not math.isfinite(qty) or qty < 0:
            raise RuntimeError(f"Backward 필요수량 오류: {key}")
        special = "안전" in raw_initial or norm(raw_initial) in {"PB", "국내", "해외"}
        row = dict(metadata)
        row.update({"demand_id": original["demand_id"], "so_id": order, "seq": int(seq),
                    "initial": metadata.get("initial") or original.get("initial") or raw_initial,
                    "demand_type": metadata.get("demand_type") or (raw_initial if special else "이니셜"),
                    "item_id": norm(original.get("item_id")),
                    "demand_item_id": norm(original.get("demand_item_id")),
                    "due_date": original.get("due_date"), "oper_id": oper,
                    "res_site_id": "S관(3공장)", "plan_qty": qty,
                    "plan_date": "", "res_id": "", "target_datetime": "",
                    "cust_name": "" if special else metadata.get("cust_name") or original.get("cust_name") or ""})
        row["item_cd"] = row.get("item_cd") or norm(original.get("sale_cd"))
        row["item_cd_5"] = row.get("item_cd_5") or row["item_cd"]
        row["item_group_id"] = row.get("demand_group_id", "")
        if not row.get("power"):
            match = re.search(r"[+-]\d{2}\.\d{2}", row["demand_item_id"])
            row["power"] = match.group() if match else ""
        item_meta, _ = unique_fields(items[row["item_id"]], ("item_name", "item_name2"))
        row.update(item_meta)
        if not row.get("item_name"):
            machine_name, _ = unique_fields(names[machine_key], ("nm_nm",))
            row["item_name"] = machine_name.get("nm_nm", "")
        row["_backward"] = original
        row["_metadata"] = {"source": "plan-demand" if matched else "plan-order-sequence-common",
                            "factory_basis": "outbound-site" if norm(original.get("res_site_id")) else "registered-factories",
                            "applicable_factories": sorted(eligible),
                            "demand_qty_status": "known" if row.get("demand_qty") is not None else "unknown",
                            "customer_status": "not-required" if special else "known" if row["cust_name"] else "unknown"}
        result.append(row)
    return result


def supplement_order_quantities(rows, request, api_key, timeout):
    """Only exact order/sequence/spec matches; export od_qty is PCS."""
    queries = {}
    for row in rows:
        if row.get("demand_qty") is not None:
            continue
        order = row["so_id"]
        domestic = order.startswith("R")
        token = order[1:9] if domestic else order[:8]
        if len(token) == 8 and token.isdigit():
            queries[(domestic, f"{token[:4]}-{token[4:6]}-{token[6:8]}")] = None
    errors, payloads = [], []
    for domestic, day in queries:
        endpoint = "/api/prod-request-domestic" if domestic else "/api/order-status-detail"
        try:
            payload = request(endpoint, {"date_from": day, "date_to": day, "limit": 0}, api_key, timeout)
            if payload.get("truncated"):
                raise RuntimeError("보조 수주 응답 일부 반환")
            payloads.append({"endpoint": endpoint, "payload": payload})
            index = defaultdict(list)
            for item in payload.get("rows") or []:
                key = (str(item.get("re_no" if domestic else "od_no") or "").strip(),
                       str(item.get("re_seq" if domestic else "seq") or ""), norm(item.get("gd_cd")))
                index[key].append(item)
            for row in rows:
                if row.get("demand_qty") is not None:
                    continue
                candidates = index[(row["so_id"], str(row["seq"]), row["demand_item_id"])]
                # Duplicate detail lines are ambiguous even when their numbers agree.
                if len(candidates) != 1:
                    continue
                item = candidates[0]
                pack = float(item.get("pic_qty" if domestic else "pack_qty") or 0)
                if pack <= 0 or pack != float(row.get("pack_unit") or 0):
                    continue
                pcs = float(item["re_pcs_qty" if domestic else "od_qty"])
                quantity = float(item["re_pack_qty"]) if domestic else pcs / pack
                if not math.isfinite(quantity) or quantity < 0 or not math.isclose(quantity * pack, pcs):
                    continue
                row["demand_qty"] = quantity
                row["_metadata"]["demand_qty_status"] = "known"
                row["_metadata"]["quantity_source"] = endpoint
                row["_metadata"]["quantity_original"] = item
        except Exception as exc:
            errors.append(f"{endpoint} {day}: {type(exc).__name__}")
    return payloads, errors
