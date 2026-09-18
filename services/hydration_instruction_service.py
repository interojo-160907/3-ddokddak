"""API-ready hydration instruction snapshot adapter.

The endpoint is intentionally configuration-driven so the requested ERP API can
be connected without changing the inventory calculation or UI.
"""
from __future__ import annotations

import gzip
import json
import os
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path


DEFAULT_CONFIG = {
    "endpoint": "",
    "item_field": "item_cd",
    "quantity_field": "instruction_qty",
    "date_field": "instruction_date",
    "rows_field": "rows",
    "lookback_days": 31,
    "params": {"process_cd": "45", "limit": 0},
}


class HydrationInstructionService:
    def __init__(self, cache: Path):
        self.cache = Path(cache)
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
        return {**DEFAULT_CONFIG, **value, "params": {**DEFAULT_CONFIG["params"], **value.get("params", {})}}

    @staticmethod
    def _number(value):
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    def refresh(self) -> dict:
        cfg = self.config();endpoint = str(cfg.get("endpoint") or "").strip()
        if not endpoint:
            return {"status": "unconnected", "message": "ERP 하이드레이션 지시 API 경로 대기 중", "config": str(self.config_path)}
        if endpoint.startswith("/"):
            endpoint = "https://plan.interojo.net" + endpoint
        today = date.today();params = dict(cfg.get("params") or {})
        params.setdefault("date_from", (today - timedelta(days=int(cfg.get("lookback_days") or 31))).isoformat())
        params.setdefault("date_to", today.isoformat())
        url = endpoint + ("&" if "?" in endpoint else "?") + urllib.parse.urlencode(params)
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        api_key = os.getenv("DDOKDDAK_PROD3_API_KEY", os.getenv("PLAN_API_KEY", "")).strip()
        if api_key: headers["X-API-Key"] = api_key
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120) as response:
            payload = json.load(response)
        rows_field=str(cfg.get("rows_field") or "rows")
        rows = payload.get(rows_field)
        if not isinstance(rows,list):
            rows=next((payload.get(name) for name in ("rows","data","results","items") if isinstance(payload.get(name),list)),None)
        if not isinstance(rows, list) or payload.get("truncated"):
            raise ValueError("하이드레이션 지시 API 응답이 불완전합니다.")
        item_field = str(cfg.get("item_field") or "item_cd")
        quantity_field = str(cfg.get("quantity_field") or "instruction_qty")
        normalized=[];quantities=Counter();invalid=0
        for source in rows:
            item=str(source.get(item_field) or "").strip().upper();qty=self._number(source.get(quantity_field))
            if not item.startswith("P") or qty is None:
                invalid += 1;continue
            row={"item_cd":item,"instruction_qty":qty}
            for target,aliases in {
                "check_sheet_no":("check_sheet_no","sheet_no"),
                "production_order_no":("production_order_no","pr_no"),
                "instruction_date":(str(cfg.get("date_field") or "instruction_date"),"instruction_date","pr_dt"),
                "factory_name":("factory_name","factory_nm","fac_nm"),
            }.items():
                row[target]=next((source.get(name) for name in aliases if source.get(name) not in (None,"")),"")
            normalized.append(row);quantities[item]+=qty
        if rows and not normalized:
            raise ValueError(f"하이드레이션 지시 API 필드 확인 필요: {item_field}, {quantity_field}")
        captured=datetime.now().isoformat(timespec="seconds")
        snapshot={"status":"success","captured_at":captured,"endpoint":endpoint,"source_total_count":payload.get("total_count",len(rows)),
                  "row_count":len(normalized),"product_count":len(quantities),"total_qty":sum(quantities.values()),
                  "rows":normalized,"quantities":dict(quantities),"invalid_rows":invalid}
        tmp=self.current_path.with_suffix(".tmp");tmp.write_text(json.dumps(snapshot,ensure_ascii=False),encoding="utf-8");tmp.replace(self.current_path)
        folder=self.cache/"snapshots"/datetime.now().strftime("%Y%m%d_%H%M%S_%f");folder.mkdir(parents=True,exist_ok=True)
        archive=folder/"hydration_instructions.json.gz"
        with gzip.open(archive,"wt",encoding="utf-8") as stream:json.dump(snapshot,stream,ensure_ascii=False)
        return {"status":"success","completed_at":captured,"rows":len(normalized),"products":len(quantities),"total_qty":sum(quantities.values()),"snapshot":str(archive)}

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
