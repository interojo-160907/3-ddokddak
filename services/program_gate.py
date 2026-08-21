from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import platform
import socket
from typing import Any

import requests

from services.data_location import resolve_data_root, resolve_management_api_url


PROGRAM_KEY = "생산3공장 똑딱이"
CACHE_MAX_AGE = timedelta(hours=24)
DEFAULT_UPDATE_URL = (
    "https://github.com/interojo-160907/3-ddokddak/"
    "releases/latest/download/ddokddak-production3-setup.exe"
)


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    update_required: bool = False
    latest_version: str = ""
    message: str = ""
    update_url: str = ""
    notices: tuple[dict[str, Any], ...] = ()
    source: str = "remote"
    reason: str = ""


def _machine_seed() -> str:
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
                0,
                winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as key:
                value, _value_type = winreg.QueryValueEx(key, "MachineGuid")
            if value:
                return str(value)
        except OSError:
            pass
    return "|".join((platform.node(), platform.machine(), platform.system()))


def pc_identifier() -> str:
    return hashlib.sha256(_machine_seed().encode("utf-8")).hexdigest()[:32].upper()


def _version_key(value: str) -> tuple[int, ...]:
    text = str(value or "").strip().lstrip("vV")
    try:
        parts = tuple(int(part) for part in text.split("."))
    except ValueError:
        return ()
    return parts + (0,) * max(0, 3 - len(parts))


def internal_ip() -> str:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return str(probe.getsockname()[0])
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return ""
    finally:
        probe.close()


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().upper() in {"1", "Y", "YES", "TRUE", "ON"}


def _update_url(value: object) -> str:
    target = str(value or "").strip()
    return target if target.lower().startswith(("https://", "http://")) else DEFAULT_UPDATE_URL


class ProgramGate:
    def __init__(self, current_version: str, timeout: int = 30) -> None:
        self.current_version = current_version
        self.timeout = timeout
        self.endpoint = resolve_management_api_url()
        self.cache_path = resolve_data_root() / "settings" / "program_gate_cache.json"

    def identity(self) -> dict[str, str]:
        return {
            "program": PROGRAM_KEY,
            "version": self.current_version,
            "pc_id": pc_identifier(),
        }

    def check(self) -> GateResult:
        if not self.endpoint:
            return GateResult(
                allowed=True,
                message="관리시트 중계 API 등록 대기 · 로컬 실행",
                source="not_configured",
            )
        payload = self.identity()
        try:
            response = requests.post(self.endpoint, json=payload, timeout=self.timeout)
            response.raise_for_status()
            body = response.json()
            data = body.get("result", body) if isinstance(body, dict) else {}
            if not isinstance(data, dict):
                raise ValueError("관리 API 응답 형식이 올바르지 않습니다.")
        except (requests.RequestException, ValueError, TypeError) as exc:
            cached = self._cached_result()
            if cached is not None:
                return GateResult(
                    **cached,
                    source="cache",
                    message="관리 API 일시 오류 · 최근 정상 권한으로 실행",
                )
            return GateResult(
                allowed=False,
                message=f"관리 API에 연결하지 못했습니다: {exc}",
                source="network",
                reason="network",
            )

        latest_version = str(data.get("latest_version") or data.get("latestVersion") or "").strip()
        allowed = _as_bool(data.get("allowed", data.get("use_allowed", False)))
        latest_key = _version_key(latest_version)
        current_key = _version_key(self.current_version)
        update_required = (
            latest_key > current_key
            if latest_key and current_key
            else _as_bool(data.get("update_required"))
        )
        notices_value = data.get("notices") or []
        notices = tuple(row for row in notices_value if isinstance(row, dict))
        result = GateResult(
            allowed=allowed,
            update_required=update_required,
            latest_version=latest_version,
            message=str(data.get("message") or "권한 및 버전 확인 완료"),
            update_url=_update_url(data.get("update_url") or data.get("installer_url")),
            notices=notices,
            reason="denied" if not allowed else "",
        )
        if result.allowed:
            self._save_cache(result)
        return result

    def _cached_result(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            checked_at = datetime.fromisoformat(str(payload.get("checked_at") or ""))
        except (OSError, ValueError, TypeError):
            return None
        if datetime.now().astimezone() - checked_at.astimezone() > CACHE_MAX_AGE:
            return None
        return {
            "allowed": bool(payload.get("allowed")),
            "update_required": bool(payload.get("update_required")),
            "latest_version": str(payload.get("latest_version") or ""),
            "update_url": _update_url(payload.get("update_url")),
            "notices": tuple(payload.get("notices") or ()),
            "reason": "",
        }

    def _save_cache(self, result: GateResult) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "allowed": result.allowed,
                    "update_required": result.update_required,
                    "latest_version": result.latest_version,
                    "update_url": result.update_url,
                    "notices": list(result.notices),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.cache_path)
