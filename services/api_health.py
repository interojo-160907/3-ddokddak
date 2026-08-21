from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import os

import requests


BASE_URL = os.getenv("DDOKDDAK_PROD3_API_BASE_URL", "https://plan.interojo.net").rstrip("/")


def _headers() -> dict[str, str]:
    api_key = os.getenv("DDOKDDAK_PROD3_API_KEY", "").strip()
    return {"X-API-Key": api_key} if api_key else {}


def _probe(path: str, params: dict[str, object], timeout: float) -> bool:
    try:
        with requests.get(
            f"{BASE_URL}{path}",
            params=params,
            headers=_headers(),
            timeout=timeout,
        ) as response:
            return 200 <= response.status_code < 300
    except requests.RequestException:
        return False


def check_collection_apis(timeout: float = 6.0) -> dict[str, bool]:
    today = date.today().isoformat()
    probes = {
        "bom": ("/api/product-names", {"limit": 1}),
        "aps": ("/api/aps-plan/meta", {}),
        "production": (
            "/api/production-performance",
            {"date_from": today, "date_to": today, "limit": 1},
        ),
    }
    results = {key: False for key in probes}
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="api-health") as executor:
        futures = {
            executor.submit(_probe, path, params, timeout): key
            for key, (path, params) in probes.items()
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = bool(future.result())
            except Exception:
                results[key] = False
    return results
