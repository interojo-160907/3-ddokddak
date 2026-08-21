from __future__ import annotations

import os
from pathlib import Path


DEFAULT_DATA_ROOT = Path(r"C:\똑딱이 생산3팀 API DATA")
REGISTRY_PATH = r"Software\Interojo\DdokddakProduction3"
DATA_ROOT_VALUE = "DataRoot"
MANAGEMENT_API_VALUE = "ManagementApiUrl"


def read_registry_value(name: str) -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH) as key:
            value, _value_type = winreg.QueryValueEx(key, name)
        return str(value or "").strip()
    except (OSError, ValueError):
        return ""


def write_registry_value(name: str, value: str) -> None:
    if os.name != "nt":
        return
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))


def resolve_data_root() -> Path:
    configured = read_registry_value(DATA_ROOT_VALUE)
    if not configured:
        configured = os.getenv("DDOKDDAK_PROD3_DATA_DIR", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_DATA_ROOT


def save_data_root(path: Path | str) -> Path:
    resolved = Path(path).expanduser().resolve()
    write_registry_value(DATA_ROOT_VALUE, str(resolved))
    return resolved


def resolve_management_api_url() -> str:
    return (
        read_registry_value(MANAGEMENT_API_VALUE)
        or os.getenv("DDOKDDAK_MANAGEMENT_API_URL", "").strip()
    )
