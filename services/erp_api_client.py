"""Small shared ERP transport; no data cache and no implicit nested retries.

Sessions are thread-local. At most two requests in a collector process reach
the ERP concurrently, even when BOM/performance workers are nested.
Socket timeouts bound connection and read inactivity, not total transfer time.
"""
from __future__ import annotations

import os
import threading
import time
from urllib.parse import urlsplit

import requests
from urllib3.exceptions import ReadTimeoutError

from services.api_credentials import api_headers

_local = threading.local()
_slots = threading.BoundedSemaphore(2)
TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504}


class ApiRequestError(RuntimeError):
    def __init__(self, endpoint: str, kind: str, elapsed: float, attempts: int, http_status=None):
        self.endpoint = urlsplit(endpoint).path
        self.kind = kind
        self.elapsed_seconds = round(elapsed, 2)
        self.attempts = attempts
        self.http_status = http_status
        reason = {
            "ReadTimeout": "서버 응답 대기 시간 초과 · 다음 갱신에서 재시도",
            "ConnectTimeout": "서버 연결 시간 초과",
            "QueueTimeout": "다른 API 요청 처리 대기",
            "InvalidJSON": "JSON 응답 형식 확인 필요",
        }.get(kind, "API 요청 실패")
        if http_status in (401, 403):
            reason = "API 인증키·조회 권한 확인 필요"
        elif http_status == 429:
            reason = "서버 요청 제한 · 다음 갱신에서 재시도"
        suffix = f" · HTTP {http_status}" if http_status is not None else ""
        super().__init__(f"{self.endpoint}: {reason} ({kind}{suffix}, {self.elapsed_seconds:g}초, {attempts}회)")


def _session() -> requests.Session:
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()  # Includes pooling, gzip and TLS verification.
        _local.session = session
    return session


def _read_timed_out(error: Exception) -> bool:
    # requests can wrap a body-read timeout inside ConnectionError.
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:continue
        seen.add(id(current))
        if isinstance(current, (requests.ReadTimeout, ReadTimeoutError)):return True
        pending.extend(value for value in (*current.args, current.__cause__, current.__context__)
                       if isinstance(value, Exception))
    return False


def request_json(endpoint: str, params: dict | None = None, *, api_key: str = "",
                 timeout: float = 30, attempts: int = 2, base_url: str | None = None) -> dict:
    base = (base_url or os.getenv("DDOKDDAK_PROD3_API_BASE_URL", "https://plan.interojo.net")).rstrip("/")
    url = endpoint if endpoint.startswith(("https://", "http://")) else base + endpoint
    headers = api_headers(api_key)
    read_timeout = max(1.0, min(float(timeout), 60.0))
    max_attempts = max(1, min(int(attempts), 2))
    started = time.monotonic()
    for attempt in range(1, max_attempts + 1):
        if not _slots.acquire(timeout=90):
            raise ApiRequestError(endpoint, "QueueTimeout", time.monotonic() - started, attempt - 1)
        retry = False
        delay = 1.0
        status = None
        kind = "RequestError"
        try:
            # Do not follow redirects with an ERP API key to a different origin.
            with _session().get(url, params=params or {}, headers=headers,
                                timeout=(5, read_timeout), allow_redirects=False) as response:
                status = response.status_code
                if status != 200:
                    kind = "HTTPError"
                    retry = status in TRANSIENT_STATUSES
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = max(1.0, float(retry_after))
                            retry = retry and delay <= 5
                        except ValueError:
                            retry = False  # An HTTP date also means wait until a later cycle.
                else:
                    response.encoding = "utf-8"
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = None
                    if isinstance(payload, dict) and payload.get("ok") is not False and payload.get("success") is not False:
                        return payload
                    kind = "APIError" if isinstance(payload, dict) else "InvalidJSON"
        except requests.ReadTimeout:
            # A timed-out database query may still run on the server. Avoid
            # immediately launching the same expensive query a second time.
            kind = "ReadTimeout"
        except requests.exceptions.SSLError:
            kind = "SSLError"
        except (requests.ConnectTimeout, requests.ConnectionError) as exc:
            kind = "ReadTimeout" if _read_timed_out(exc) else type(exc).__name__
            retry = kind != "ReadTimeout"
        except requests.RequestException as exc:
            kind = type(exc).__name__
        finally:
            _slots.release()
        if retry and attempt < max_attempts:
            time.sleep(delay)
            continue
        raise ApiRequestError(endpoint, kind, time.monotonic() - started, attempt, status)
    raise AssertionError("unreachable")
