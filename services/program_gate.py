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
STARTUP_PERMISSION_CACHE_AGE = timedelta(hours=24)
NOTICE_CACHE_MAX_AGE = timedelta(hours=24)
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
    def __init__(self, current_version: str, timeout: int = 8) -> None:
        self.current_version = current_version
        self.timeout = timeout
        self.endpoint = resolve_management_api_url()
        self.cache_path = resolve_data_root() / "settings" / "program_gate_cache.json"
        self.notice_cache_path = resolve_data_root() / "settings" / "management_notices.json"

    def identity(self) -> dict[str, str]:
        return {
            "program": PROGRAM_KEY,
            "version": self.current_version,
            "pc_id": pc_identifier(),
        }

    def cached_permission(self) -> GateResult | None:
        """Use the last approved identity so startup never waits on version data."""
        cached = self._cached_result(max_age=STARTUP_PERMISSION_CACHE_AGE)
        if cached is None or not bool(cached.get("allowed")):
            return None
        return GateResult(
            allowed=True,
            update_required=False,
            latest_version=str(cached.get("latest_version") or ""),
            update_url=_update_url(cached.get("update_url")),
            notices=self._cached_notices(),
            source="startup_cache",
            message="최근 확인된 사용 권한 정상",
        )

    def check(
        self,
        *,
        allow_cache_fallback: bool = True,
        report_connection: bool = False,
    ) -> GateResult:
        if not self.endpoint:
            return GateResult(
                allowed=True,
                message="관리시트 중계 API 등록 대기 · 로컬 실행",
                source="not_configured",
            )
        payload = self.identity()
        if report_connection:
            payload.update(
                {
                    "connected": True,
                    "connection_status": "접속중",
                    "heartbeat_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                }
            )
        try:
            response = requests.post(
                self.endpoint,
                json=payload,
                timeout=(3, self.timeout),
                headers={"User-Agent": "Ddokddak-Production3-Gate"},
            )
            response.raise_for_status()
            body = response.json()
            data = body.get("result", body) if isinstance(body, dict) else {}
            if not isinstance(data, dict):
                raise ValueError("관리 API 응답 형식이 올바르지 않습니다.")
        except (requests.RequestException, ValueError, TypeError) as exc:
            cached = self._cached_result() if allow_cache_fallback else None
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
        notices, notices_provided = self._extract_notices(body, data)
        if not notices_provided:
            notices = self._cached_notices()
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
            if notices_provided:
                self._save_notice_cache(notices)
        return result

    @staticmethod
    def _notice_rows(value: object) -> tuple[dict[str, Any], ...]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                return ()
        if isinstance(value, dict):
            value = value.get("rows", value.get("items", value.get("data", ())))
        if not isinstance(value, (list, tuple)):
            return ()
        return tuple(row for row in value if isinstance(row, dict))

    @classmethod
    def _extract_notices(
        cls,
        body: object,
        data: dict[str, Any],
    ) -> tuple[tuple[dict[str, Any], ...], bool]:
        aliases = ("notices", "notice_list", "noticeList", "announcements")
        for container in (data, body):
            if not isinstance(container, dict):
                continue
            for key in aliases:
                if key in container:
                    return cls._notice_rows(container.get(key)), True
        return (), False

    def set_connection(self, connected: bool) -> bool:
        if not self.endpoint:
            return False
        payload: dict[str, Any] = self.identity()
        payload.update(
            {
                "action": "presence",
                "connected": bool(connected),
                "connection_status": "접속중" if connected else "",
                "heartbeat_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
        )
        try:
            response = requests.post(self.endpoint, json=payload, timeout=self.timeout)
            response.raise_for_status()
            return True
        except requests.RequestException:
            return False

    def _cached_result(
        self,
        *,
        max_age: timedelta = CACHE_MAX_AGE,
    ) -> dict[str, Any] | None:
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            checked_at = datetime.fromisoformat(str(payload.get("checked_at") or ""))
        except (OSError, ValueError, TypeError):
            return None
        if datetime.now().astimezone() - checked_at.astimezone() > max_age:
            return None
        return {
            "allowed": bool(payload.get("allowed")),
            "update_required": bool(payload.get("update_required")),
            "latest_version": str(payload.get("latest_version") or ""),
            "update_url": _update_url(payload.get("update_url")),
            "notices": tuple(payload.get("notices") or ()),
            "reason": "",
        }

    def _cached_notices(self) -> tuple[dict[str, Any], ...]:
        for path in (self.notice_cache_path, self.cache_path):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                checked_at = datetime.fromisoformat(str(payload.get("checked_at") or ""))
            except (OSError, ValueError, TypeError):
                continue
            if datetime.now().astimezone() - checked_at.astimezone() > NOTICE_CACHE_MAX_AGE:
                continue
            notices = self._notice_rows(payload.get("notices"))
            if notices or "notices" in payload:
                return notices
        return ()

    def _save_notice_cache(self, notices: tuple[dict[str, Any], ...]) -> None:
        self.notice_cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.notice_cache_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "notices": list(notices),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(self.notice_cache_path)

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
