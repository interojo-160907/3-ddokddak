from __future__ import annotations

import json
import os
import sqlite3
import calendar
from datetime import date, timedelta
from pathlib import Path

from config import DATA_CENTER_DIR


ROOT = DATA_CENTER_DIR
APS_DB = ROOT / "process-status" / "aps_process_status.sqlite"
APS_STATUS = ROOT / "process-status" / "snapshot" / "refresh_status.json"
PRODUCTION_DB = ROOT / "production-performance" / "production_performance.sqlite"
PRODUCTION_STATUS = ROOT / "production-performance" / "snapshot" / "refresh_status.json"
BOM_DB = ROOT / "bom" / "product_reference.sqlite"
BOM_STATUS = ROOT / "bom" / "snapshot" / "refresh_status.json"
PROCESS_NAMES = {"10": "사출", "20": "분리", "45": "하이드레이션", "55": "검사·접착", "80": "누수·규격"}
PROCESS_ORDER = tuple(PROCESS_NAMES.values())


def _channel(demand_type: object, initial: object, destination: object = "") -> str:
    demand = str(demand_type or "").strip()
    initial_text = str(initial or "").strip()
    if "안전" in demand or "안전" in initial_text:
        return "안전재고"
    if demand == "국내" or (not str(destination or "").strip() and demand != "PB"):
        return "국내"
    return "해외"


def _status(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


class DashboardService:
    def available(self) -> bool:
        return APS_DB.is_file() and PRODUCTION_DB.is_file()

    def load(self) -> dict:
        result = {
            "available": self.available(),
            "shortage": {name: 0.0 for name in PROCESS_ORDER},
            "requirement_clear": {name: 0.0 for name in PROCESS_ORDER},
            "requirement_color": {name: 0.0 for name in PROCESS_ORDER},
            "shortage_by_channel": {
                channel: {name: 0.0 for name in PROCESS_ORDER}
                for channel in ("국내", "해외", "안전재고")
            },
            "requirement_by_channel": {
                channel: {
                    lens: {name: 0.0 for name in PROCESS_ORDER}
                    for lens in ("clear", "color")
                }
                for channel in ("국내", "해외", "안전재고")
            },
            "requirement_detail_by_channel": {
                channel: {
                    lens: {name: {} for name in PROCESS_ORDER}
                    for lens in ("clear", "color")
                }
                for channel in ("국내", "해외", "안전재고")
            },
            "production": {name: 0.0 for name in PROCESS_ORDER},
            "yield": {name: 0.0 for name in PROCESS_ORDER},
            "yield_clear": {name: 0.0 for name in PROCESS_ORDER},
            "yield_color": {name: 0.0 for name in PROCESS_ORDER},
            "classification_good": {},
            "production_periods": {},
            "default_production_period": "previous" if date.today().day == 1 else "current",
            "risks": [],
            "aps_status": _status(APS_STATUS),
            "production_status": _status(PRODUCTION_STATUS),
            "bom_status": _status(BOM_STATUS),
        }
        if APS_DB.is_file():
            self._load_aps(result)
        if PRODUCTION_DB.is_file():
            self._load_production(result)
        return result

    def order_details(self, order_no: str) -> dict:
        if not APS_DB.is_file() or not str(order_no).strip():
            return {"order": {}, "items": []}
        with _connect(APS_DB) as connection:
            source = connection.execute(
                "SELECT so_id,MAX(initial) initial,MIN(due_date) due_date,MAX(cust_name) cust_name,"
                "MAX(dest_country) dest_country,MAX(demand_type) demand_type,MAX(res_site_id) factory "
                "FROM aps_plan WHERE so_id=? GROUP BY so_id",
                (str(order_no).strip(),),
            ).fetchone()
            rows = connection.execute(
                "SELECT demand_group_id,demand_item_id,demand_item_name,power,"
                "MAX(COALESCE(demand_qty,0)) demand_qty,oper_id,SUM(COALESCE(plan_qty,0)) plan_qty "
                "FROM aps_plan WHERE so_id=? AND oper_id IN ('10','20','45','55','80') "
                "GROUP BY demand_group_id,demand_item_id,demand_item_name,power,oper_id "
                "ORDER BY demand_item_id,power,oper_id",
                (str(order_no).strip(),),
            ).fetchall()
        if source is None:
            return {"order": {}, "items": []}
        grouped: dict[tuple[str, ...], dict] = {}
        for row in rows:
            key = (
                str(row["demand_group_id"] or ""), str(row["demand_item_id"] or ""),
                str(row["demand_item_name"] or ""), str(row["power"] or ""),
            )
            target = grouped.setdefault(
                key,
                {
                    "classification": key[0], "item_code": key[1], "item_name": key[2],
                    "power": key[3], "order_qty": float(row["demand_qty"] or 0),
                    **{name: 0.0 for name in PROCESS_ORDER},
                },
            )
            name = PROCESS_NAMES.get(str(row["oper_id"]))
            if name:
                target[name] += float(row["plan_qty"] or 0)
        items = sorted(grouped.values(), key=lambda row: (row["item_code"], row["power"]))
        products_by_code: dict[tuple[str, str], dict] = {}
        for row in items:
            product_key = (str(row["classification"] or ""), str(row["item_name"] or ""))
            product = products_by_code.setdefault(
                product_key,
                {
                    "classification": product_key[0],
                    "item_name": product_key[1],
                    "order_qty": 0.0,
                    "spec_count": 0,
                    **{name: 0.0 for name in PROCESS_ORDER},
                },
            )
            product["order_qty"] += float(row["order_qty"] or 0)
            product["spec_count"] += 1
            for name in PROCESS_ORDER:
                product[name] += float(row.get(name) or 0)
        products = sorted(
            products_by_code.values(),
            key=lambda row: (row["classification"], row["item_name"]),
        )
        process_totals = {
            name: sum(float(row.get(name) or 0) for row in products)
            for name in PROCESS_ORDER
        }
        order = dict(source)
        order["item_count"] = len(products)
        order["spec_count"] = len(items)
        order["order_qty"] = sum(float(row["order_qty"] or 0) for row in items)
        order["remaining_qty"] = sum(process_totals.values())
        return {
            "order": order,
            "items": items,
            "products": products,
            "process_totals": process_totals,
        }

    @staticmethod
    def _load_aps(result: dict) -> None:
        with _connect(APS_DB) as connection:
            for row in connection.execute(
                "SELECT oper_id,SUM(COALESCE(plan_qty,0)) qty FROM aps_plan "
                "WHERE oper_id IN ('10','20','45','55','80') GROUP BY oper_id"
            ):
                name = PROCESS_NAMES.get(str(row["oper_id"]))
                if name:
                    result["shortage"][name] = float(row["qty"] or 0)
            for row in connection.execute(
                "SELECT oper_id,CASE WHEN lower(COALESCE(demand_group_id,'')) LIKE '%color%' "
                "THEN 'color' ELSE 'clear' END lens_type,SUM(COALESCE(plan_qty,0)) qty "
                "FROM aps_plan WHERE oper_id IN ('10','20','45','55','80') "
                "GROUP BY oper_id,lens_type"
            ):
                name = PROCESS_NAMES.get(str(row["oper_id"]))
                if name:
                    result[f"requirement_{row['lens_type']}"][name] = float(row["qty"] or 0)

            channel_rows = connection.execute(
                "SELECT oper_id,demand_type,initial,dest_country,demand_group_id,"
                "CASE WHEN lower(COALESCE(demand_group_id,'')) LIKE '%color%' THEN 'color' ELSE 'clear' END lens_type,"
                "SUM(COALESCE(plan_qty,0)) qty FROM aps_plan "
                "WHERE oper_id IN ('10','20','45','55','80') "
                "GROUP BY oper_id,demand_type,initial,dest_country,demand_group_id,lens_type"
            ).fetchall()
            for row in channel_rows:
                name = PROCESS_NAMES.get(str(row["oper_id"]))
                if not name:
                    continue
                channel = _channel(row["demand_type"], row["initial"], row["dest_country"])
                quantity = float(row["qty"] or 0)
                result["shortage_by_channel"][channel][name] += quantity
                result["requirement_by_channel"][channel][str(row["lens_type"])][name] += quantity
                classification = str(row["demand_group_id"] or "").strip() or "미분류"
                detail_target = result["requirement_detail_by_channel"][channel][str(row["lens_type"])][name]
                detail_target[classification] = detail_target.get(classification, 0.0) + quantity

            risk_rows = connection.execute(
                "SELECT so_id,MAX(initial) initial,MIN(due_date) due_date,MAX(demand_group_id) classification,"
                "MAX(demand_type) demand_type,MAX(dest_country) dest_country,"
                "SUM(CASE WHEN oper_id IN ('10','20','45','55','80') THEN COALESCE(plan_qty,0) ELSE 0 END) risk_qty "
                "FROM aps_plan GROUP BY so_id HAVING risk_qty>0 ORDER BY due_date,so_id"
            ).fetchall()
        today = date.today()
        warning_limit = today + timedelta(days=7)
        risks = []
        for row in risk_rows:
            try:
                due = date.fromisoformat(str(row["due_date"] or "")[:10])
            except ValueError:
                continue
            if due > warning_limit:
                continue
            days = (due - today).days
            tone = "danger" if days <= 3 else "warning"
            due_label = f"{abs(days)}일 경과" if days < 0 else ("오늘" if days == 0 else f"D-{days}")
            channel = _channel(row["demand_type"], row["initial"], row["dest_country"])
            risks.append(
                {
                    "order_no": str(row["so_id"] or ""),
                    "initial": str(row["initial"] or ""),
                    "due": due.isoformat(),
                    "due_label": due_label,
                    "classification": str(row["classification"] or ""),
                    "channel": channel,
                    "risk_qty": float(row["risk_qty"] or 0),
                    "tone": tone,
                }
            )
        result["risks"] = risks

    @staticmethod
    def _load_production(result: dict) -> None:
        today = date.today()
        confirmed_through = today - timedelta(days=1)
        result["production_confirmed_through"] = confirmed_through.isoformat()
        current_first = today.replace(day=1)
        previous_last = current_first - timedelta(days=1)
        previous_first = previous_last.replace(day=1)
        period_months = {
            "current": current_first,
            "previous": previous_first,
        }
        periods: dict[str, dict] = {}
        for period_key, first_day in period_months.items():
            days_in_month = calendar.monthrange(first_day.year, first_day.month)[1]
            day_keys = tuple(str(day) for day in range(1, days_in_month + 1))
            periods[period_key] = {
                "year_month": first_day.strftime("%Y-%m"),
                "label": f"{first_day.month}월",
                "days": day_keys,
                "production": {name: 0.0 for name in PROCESS_ORDER},
                "yield": {name: 0.0 for name in PROCESS_ORDER},
                "yield_clear": {name: 0.0 for name in PROCESS_ORDER},
                "yield_color": {name: 0.0 for name in PROCESS_ORDER},
                "classification_good": {},
                "daily_total": {day: 0.0 for day in day_keys},
                "daily_final_good": {day: 0.0 for day in day_keys},
                "live_day": "",
                "live_final_good": 0.0,
                "daily_overall_yield": {day: None for day in day_keys},
                "daily_yield_by_process": {
                    name: {day: None for day in day_keys}
                    for name in PROCESS_ORDER
                },
                "daily_by_process": {
                    name: {day: 0.0 for day in day_keys}
                    for name in PROCESS_ORDER
                },
            }
        with _connect(PRODUCTION_DB) as connection:
            rows = connection.execute(
                "SELECT pr_dt,gong_cd,sale_cd,"
                "SUM(COALESCE(tot_qty,0)) production_qty,SUM(COALESCE(pr_qty,0)) good_qty,"
                "SUM(COALESCE(ng_qty,0)) ng_qty FROM production_performance "
                "WHERE pr_dt>=? AND pr_dt<=? AND COALESCE(stts,'')='C' "
                "GROUP BY pr_dt,gong_cd,sale_cd",
                (previous_first.isoformat(), confirmed_through.isoformat()),
            ).fetchall()
            live_row = connection.execute(
                "SELECT SUM(COALESCE(pr_qty,0)) live_good FROM production_performance "
                "WHERE substr(pr_dt,1,10)=? AND gong_cd='80' "
                "AND COALESCE(stts,'') IN ('C','S')",
                (today.isoformat(),),
            ).fetchone()
        periods["current"]["live_day"] = str(today.day)
        periods["current"]["live_final_good"] = float(live_row["live_good"] or 0)
        color_codes: set[str] = set()
        classification_by_code: dict[str, str] = {}
        if BOM_DB.is_file():
            with _connect(BOM_DB) as connection:
                product_rows = connection.execute(
                    "SELECT nm_cd,full_gu_nm,color_yn FROM product_name_master"
                ).fetchall()
                for product_row in product_rows:
                    code = str(product_row["nm_cd"] or "").strip().upper()
                    classification = str(product_row["full_gu_nm"] or "").strip() or "미분류"
                    if code:
                        classification_by_code[code] = classification
                    if "color" in classification.lower() or str(product_row["color_yn"] or "").strip().lower() in {
                        "y", "yes", "beauty", "color"
                    }:
                        color_codes.add(code)
        totals_by_period = {
            period_key: {name: [0.0, 0.0, 0.0] for name in PROCESS_ORDER}
            for period_key in periods
        }
        types_by_period = {
            period_key: {
                lens: {name: [0.0, 0.0] for name in PROCESS_ORDER}
                for lens in ("clear", "color")
            }
            for period_key in periods
        }
        daily_by_period = {
            period_key: {
                name: {day: [0.0, 0.0] for day in period["days"]}
                for name in PROCESS_ORDER
            }
            for period_key, period in periods.items()
        }
        for row in rows:
            name = PROCESS_NAMES.get(str(row["gong_cd"]))
            if not name:
                continue
            row_date = str(row["pr_dt"] or "")[:10]
            row_month = row_date[:7]
            period_key = next(
                (key for key, period in periods.items() if period["year_month"] == row_month),
                None,
            )
            if period_key is None:
                continue
            day_key = str(int(row_date[-2:]))
            period = periods[period_key]
            production = float(row["production_qty"] or 0)
            good = float(row["good_qty"] or 0)
            ng = float(row["ng_qty"] or 0)
            totals = totals_by_period[period_key]
            totals[name][0] += production
            totals[name][1] += good
            totals[name][2] += ng
            period["daily_by_process"][name][day_key] += production
            daily_by_period[period_key][name][day_key][0] += production
            daily_by_period[period_key][name][day_key][1] += good
            if name == "누수·규격":
                period["daily_final_good"][day_key] += good
                period["daily_total"][day_key] += good
            sale_code = str(row["sale_cd"] or "").strip().upper()
            lens_type = "color" if sale_code in color_codes else "clear"
            types = types_by_period[period_key]
            types[lens_type][name][0] += good
            types[lens_type][name][1] += ng
            classification = classification_by_code.get(sale_code, "미분류")
            period["classification_good"].setdefault(
                classification,
                {process_name: 0.0 for process_name in PROCESS_ORDER},
            )[name] += good
        for period_key, period in periods.items():
            for name, (production, good, ng) in totals_by_period[period_key].items():
                period["production"][name] = production
                period["yield"][name] = good / production * 100 if production else 0.0
            for lens_type in ("clear", "color"):
                for name, (good, ng) in types_by_period[period_key][lens_type].items():
                    period[f"yield_{lens_type}"][name] = good / (good + ng) * 100 if good + ng else 0.0
            for day in period["days"]:
                ratios: list[float] = []
                for name in PROCESS_ORDER:
                    production, good = daily_by_period[period_key][name][day]
                    if production <= 0:
                        period["daily_yield_by_process"][name][day] = None
                        ratios = []
                        break
                    ratio = good / production
                    period["daily_yield_by_process"][name][day] = ratio * 100
                    ratios.append(ratio)
                if len(ratios) == len(PROCESS_ORDER):
                    overall = 1.0
                    for ratio in ratios:
                        overall *= ratio
                    period["daily_overall_yield"][day] = overall * 100
        result["production_periods"] = periods
        selected = periods[result["default_production_period"]]
        for key in ("production", "yield", "yield_clear", "yield_color", "classification_good"):
            result[key] = selected[key]
