from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
import os
import json
import time
from pathlib import Path

import requests


BASE_URL = os.getenv("DDOKDDAK_PROD3_API_BASE_URL", "https://plan.interojo.net").rstrip("/")


def _headers() -> dict[str, str]:
    api_key = os.getenv("DDOKDDAK_PROD3_API_KEY", "").strip()
    return {"X-API-Key": api_key} if api_key else {}


def _probe(path: str, params: dict[str, object], timeout: float) -> dict:
    started = time.monotonic()
    result = {"endpoint": path, "checked_at": datetime.now().astimezone().isoformat()}
    try:
        with requests.get(f"{BASE_URL}{path}", params=params, headers=_headers(),
                          timeout=(5, timeout)) as response:
            code = response.status_code
            result.update(status="success" if 200 <= code < 300 else
                          "delayed" if code in {408, 429, 500, 502, 503, 504} else "error",
                          http_status=code)
    except (requests.Timeout, requests.ConnectionError) as exc:
        result.update(status="delayed", error_type=type(exc).__name__)
    except requests.RequestException as exc:
        result.update(status="error", error_type=type(exc).__name__)
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def check_collection_api_details(timeout: float = 30.0) -> dict[str, dict]:
    today = date.today().isoformat()
    probes = {
        "bom": ("/api/product-names", {"limit": 1}),
        "aps": ("/api/aps-plan/meta", {}),
        "production": (
            "/api/production-performance",
            {"date_from": today, "date_to": today, "limit": 1},
        ),
        "live": (
            "/api/inventory-ledger-product",
            {"date_from": today, "date_to": today, "wh_nm": "검사접착", "limit": 1},
        ),
    }
    results = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="api-health") as executor:
        futures = {
            executor.submit(_probe, path, params, timeout): key
            for key, (path, params) in probes.items()
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as exc:
                results[key] = {"status": "error", "error_type": type(exc).__name__}
    return results


def check_collection_apis(timeout: float = 30.0) -> dict[str, bool]:
    return {key: value["status"] == "success"
            for key, value in check_collection_api_details(timeout).items()}


def record_health(results: dict, previous: dict, path: Path) -> dict:
    merged = {}
    for key, result in results.items():
        old = previous.get(key, {})
        success = result.get("status") == "success"
        merged[key] = {**result,
            "consecutive_failures": 0 if success else old.get("consecutive_failures", 0) + 1,
            "last_success_at": result.get("checked_at") if success else old.get("last_success_at")}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass  # Diagnostic storage must not change the measured health result.
    return merged


def connection_label(result: dict) -> str:
    if result.get("status") == "success":
        return "원활"
    if result.get("status") == "delayed" and result.get("consecutive_failures", 1) < 3:
        return "응답 대기"
    return "접속 확인 필요"
