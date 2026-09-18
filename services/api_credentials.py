"""Resolve ERP credentials consistently in the GUI and collector subprocesses."""
from __future__ import annotations

import os


def credential_value(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        import keyring
    except ImportError:
        return ""
    for service in ("APS_YIELD_DASHBOARD", "DDOKDDAK"):
        try:
            value = (keyring.get_password(service, name) or "").strip()
        except Exception:
            value = ""
        if value:
            return value
    return ""


def resolve_api_key(explicit: str = "") -> str:
    # Explicit/environment overrides precede both saved credential aliases.
    for value in (explicit, os.getenv("DDOKDDAK_PROD3_API_KEY", ""), os.getenv("PLAN_API_KEY", "")):
        if value and value.strip():
            return value.strip()
    return credential_value("DDOKDDAK_PROD3_API_KEY") or credential_value("PLAN_API_KEY")


def api_headers(api_key: str = "") -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "Ddokddak-Production3"}
    key = resolve_api_key(api_key)
    if key:
        headers["X-API-Key"] = key
    return headers
