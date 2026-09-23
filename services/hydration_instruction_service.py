"""API-ready hydration instruction snapshot adapter.

The endpoint is intentionally configuration-driven so the requested ERP API can
be connected without changing the inventory calculation or UI.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from services.erp_api_client import request_json


DEFAULT_CONFIG = {
    "config_version": 3,
    "endpoint": "/api/hydration-job-list",
    "item_field": "gd_cd",
    "quantity_field": "job_qty",
    "date_field": "job_dt",
    "rows_field": "rows",
    "lookback_days": 5,
    "params": {"limit": 0},
}


class HydrationInstructionService:
    def __init__(self, cache: Path):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.config_path = self.cache / "hydration_api_config.json"
        self.current_path = self.cache / "hydration_instructions.json"
        if not self.config_path.exists():
            self.config_path.write_text(
                json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    def config(self) -> dict:
        try:
            value = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            value = {}
        # Migrate the placeholder written before the ERP endpoint existed.
        # A configured non-empty endpoint remains authoritative.
        if not str(value.get("endpoint") or "").strip() or int(value.get("config_version") or 0) < DEFAULT_CONFIG["config_version"]:
            value = {**value, **DEFAULT_CONFIG}
            self.config_path.write_text(
                json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        return {**DEFAULT_CONFIG, **value, "params": {**DEFAULT_CONFIG["params"], **value.get("params", {})}}

    @staticmethod
    def _number(value):
        try:
            number = float(str(value).replace(",", ""))
            return number if math.isfinite(number) and number >= 0 else None
        except (TypeError, ValueError):
            return None

    def refresh(self) -> dict:
        started = time.monotonic()
        cfg = self.config();endpoint = str(cfg.get("endpoint") or "").strip()
        if not endpoint:
            return {"status": "unconnected", "message": "ERP 하이드레이션 지시 API 경로 대기 중", "config": str(self.config_path)}
        if endpoint.startswith("/"):
            endpoint = os.getenv("DDOKDDAK_PROD3_API_BASE_URL", "https://plan.interojo.net").rstrip("/") + endpoint
        today = date.today();params = dict(cfg.get("params") or {})
        lookback_days=max(1,int(cfg.get("lookback_days") or 5))
        params["date_from"] = (today - timedelta(days=lookback_days-1)).isoformat()
        params["date_to"] = today.isoformat()
        payload = request_json(endpoint, params, timeout=30)
        rows_field=str(cfg.get("rows_field") or "rows")
        rows = payload.get(rows_field)
        if not isinstance(rows,list):
            rows=next((payload.get(name) for name in ("rows","data","results","items") if isinstance(payload.get(name),list)),None)
        if not isinstance(rows, list) or payload.get("truncated"):
            raise ValueError("하이드레이션 지시 API 응답이 불완전합니다.")
        if payload.get("total_count") is not None and len(rows) != int(payload["total_count"]):
            raise ValueError("하이드레이션 지시 API 전체 건수와 수신 행 수가 다릅니다.")
        item_field = str(cfg.get("item_field") or "item_cd")
        quantity_field = str(cfg.get("quantity_field") or "instruction_qty")
        normalized=[];quantities=Counter();invalid=0;identities=set()
        for source in rows:
            if not isinstance(source, dict):
                invalid += 1;continue
            process = str(source.get("gong_cd") or "").strip()
            instruction_date = str(source.get(str(cfg.get("date_field") or "job_dt")) or "")[:10]
            if process and process != "45":
                raise ValueError("하이드레이션 이외 공정이 포함되어 기존 지시량을 유지합니다.")
            if instruction_date and not params["date_from"] <= instruction_date <= params["date_to"]:
                raise ValueError("수화 지시일자가 요청 기간 밖에 있어 기존 지시량을 유지합니다.")
            item=str(source.get(item_field) or "").strip().upper();qty=self._number(source.get(quantity_field))
            if not item.startswith("P") or qty is None:
                invalid += 1;continue
            job_no=str(source.get("job_no") or source.get("production_order_no") or source.get("pr_no") or "").strip()
            job_seq=str(source.get("job_seq") or "").strip()
            identity=(job_no,job_seq)
            if job_no and identity in identities:
                raise ValueError(f"하이드레이션 지시 고유키 중복: {job_no}/{job_seq}")
            if job_no:identities.add(identity)
            row={"item_cd":item,"item_name":source.get("gd_nm") or "","instruction_qty":qty,
                 "job_seq":job_seq,"factory_code":source.get("fac_cd") or "",
                 "process_code":source.get("gong_cd") or "","process_name":source.get("gong_nm") or ""}
            for target,aliases in {
                "check_sheet_no":("check_no","check_sheet_no","sheet_no"),
                "production_order_no":("job_no","production_order_no","pr_no"),
                "instruction_date":(str(cfg.get("date_field") or "instruction_date"),"instruction_date","pr_dt"),
                "factory_name":("factory_name","factory_nm","fac_nm"),
                "source_menu_path":("source_menu_path",),
                "source_screen_name":("source_screen_name",),
                "source_tab_name":("source_tab_name",),
                "api_endpoint":("api_endpoint",),
                "extracted_at":("extracted_at",),
            }.items():
                row[target]=next((source.get(name) for name in aliases if source.get(name) not in (None,"")),"")
            normalized.append(row);quantities[item]+=qty
        if invalid:
            raise ValueError(f"하이드레이션 지시 API 필드 확인 필요: {item_field}, {quantity_field}")
        captured=datetime.now().isoformat(timespec="seconds")
        snapshot={"status":"success","captured_at":captured,"endpoint":endpoint,"query_params":params,"source_total_count":payload.get("total_count",len(rows)),
                  "row_count":len(normalized),"product_count":len(quantities),"total_qty":sum(quantities.values()),
                  "rows":normalized,"quantities":dict(quantities),"invalid_rows":invalid}
        tmp=self.current_path.with_suffix(".tmp");tmp.write_text(json.dumps(snapshot,ensure_ascii=False),encoding="utf-8");tmp.replace(self.current_path)
        folder=self.cache/"snapshots"/datetime.now().strftime("%Y%m%d_%H%M%S_%f");folder.mkdir(parents=True,exist_ok=True)
        archive=folder/"hydration_instructions.json.gz"
        with gzip.open(archive,"wt",encoding="utf-8") as stream:json.dump(snapshot,stream,ensure_ascii=False)
        return {"status":"success","completed_at":captured,"rows":len(normalized),"products":len(quantities),"total_qty":sum(quantities.values()),"snapshot":str(archive),
                "elapsed_seconds":round(time.monotonic()-started,2),"query_params":params}

    def load(self) -> dict:
        if not self.current_path.exists():
            return {"status":"unconnected","captured_at":"","quantities":{},"rows":0,"products":0,"total_qty":0}
        try:
            data=json.loads(self.current_path.read_text(encoding="utf-8"));quantities=data.get("quantities") or {}
            return {"status":data.get("status","success"),"captured_at":data.get("captured_at",""),"quantities":quantities,
                    "rows":int(data.get("row_count") or len(data.get("rows") or [])),"products":int(data.get("product_count") or len(quantities)),
                    "total_qty":float(data.get("total_qty") or sum(float(v or 0) for v in quantities.values()))}
        except (OSError,ValueError):
            return {"status":"error","captured_at":"","quantities":{},"rows":0,"products":0,"total_qty":0}
