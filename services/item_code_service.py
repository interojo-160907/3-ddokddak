from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import DATA_CENTER_DIR


def credential_value(name: str) -> str:
    """Resolve the same API credential without importing Control Tower settings."""
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        import keyring
    except ImportError:
        return ""
    for service, user in (
        ("APS_YIELD_DASHBOARD", name),
        ("DDOKDDAK", name),
    ):
        try:
            value = (keyring.get_password(service, user) or "").strip()
        except Exception:
            value = ""
        if value:
            return value
    return ""


BASE_URL = "https://plan.interojo.net"
ENDPOINT = "/api/item-list-bulk"
CACHE_DIR = DATA_CENTER_DIR / "item-codes"
CACHE_DB = CACHE_DIR / "item_codes.sqlite"


def _text(value: object) -> str:
    return str(value or "").strip()


def _number(value: object) -> float | None:
    try:
        return float(_text(value))
    except ValueError:
        return None


class ItemCodeService:
    """Read actual ERP item codes and retain a short-lived local cache."""

    def __init__(self, database_path: Path = CACHE_DB) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            self._initialize(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS item_code_current (
                base_code TEXT NOT NULL,
                gd_cd TEXT NOT NULL,
                gd_nm TEXT,
                model_nm TEXT,
                power_text TEXT,
                power_value REAL,
                cp_text TEXT,
                cp_value REAL,
                axis_text TEXT,
                axis_value INTEGER,
                add_text TEXT,
                add_value REAL,
                stop_yn TEXT,
                extracted_at TEXT,
                payload_json TEXT NOT NULL,
                PRIMARY KEY(base_code,gd_cd)
            );
            CREATE INDEX IF NOT EXISTS ix_item_code_base_spec
                ON item_code_current(base_code,power_value,cp_value,axis_value,add_value);
            CREATE TABLE IF NOT EXISTS item_code_snapshot (
                base_code TEXT PRIMARY KEY,
                row_count INTEGER NOT NULL,
                source_refreshed_at TEXT,
                fetched_at TEXT NOT NULL
            );
            """
        )

    @staticmethod
    def _normalize_base(code: str) -> str:
        normalized = _text(code).upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9]{2,9}", normalized):
            raise ValueError(f"품번 형식이 올바르지 않습니다: {code}")
        return normalized

    def cached(self, code: str) -> tuple[list[dict[str, Any]], datetime | None]:
        base = self._normalize_base(code)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM item_code_current WHERE base_code=?",
                (base,),
            ).fetchall()
            meta = connection.execute(
                "SELECT fetched_at FROM item_code_snapshot WHERE base_code=?",
                (base,),
            ).fetchone()
        fetched_at = None
        if meta:
            try:
                fetched_at = datetime.fromisoformat(_text(meta["fetched_at"]))
            except ValueError:
                fetched_at = None
        return ([dict(row) for row in rows], fetched_at)

    def cached_many(self, codes: list[str]) -> dict[str, list[dict[str, Any]]]:
        return self.cached_many_state(codes)[0]

    def cached_many_state(
        self,
        codes: list[str],
        *,
        max_age_seconds: int = 900,
    ) -> tuple[dict[str, list[dict[str, Any]]], bool]:
        result: dict[str, list[dict[str, Any]]] = {}
        fresh_after = datetime.now() - timedelta(seconds=max(0, max_age_seconds))
        all_fresh = True
        for code in dict.fromkeys(codes):
            rows, fetched_at = self.cached(code)
            if rows:
                result[self._normalize_base(code)] = rows
            if not rows or not fetched_at or fetched_at < fresh_after:
                all_fresh = False
        return result, all_fresh

    def load_many(
        self,
        codes: list[str],
        *,
        max_age_seconds: int = 900,
        force: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        bases = list(dict.fromkeys(self._normalize_base(code) for code in codes if code))
        data: dict[str, list[dict[str, Any]]] = {}
        sources: dict[str, str] = {}
        to_fetch: list[str] = []
        fresh_after = datetime.now() - timedelta(seconds=max(0, max_age_seconds))
        for base in bases:
            rows, fetched_at = self.cached(base)
            if rows and not force and fetched_at and fetched_at >= fresh_after:
                data[base] = rows
                sources[base] = "cache"
            else:
                to_fetch.append(base)

        errors: dict[str, str] = {}
        if to_fetch:
            pool = ThreadPoolExecutor(max_workers=min(4, len(to_fetch)))
            cancelled = False
            try:
                futures = {pool.submit(self._fetch_one, base): base for base in to_fetch}
                pending = set(futures)
                while pending:
                    if cancel_event is not None and cancel_event.is_set():
                        cancelled = True
                        for future in pending:
                            future.cancel()
                        break
                    done, pending = wait(
                        pending,
                        timeout=0.2,
                        return_when=FIRST_COMPLETED,
                    )
                    for future in done:
                        base = futures[future]
                        try:
                            rows, source_refreshed_at = future.result()
                            if cancel_event is not None and cancel_event.is_set():
                                cancelled = True
                                break
                            self._publish(base, rows, source_refreshed_at)
                            data[base] = rows
                            sources[base] = "api"
                        except Exception as exc:  # cache fallback must survive network errors
                            cached_rows, _fetched_at = self.cached(base)
                            if cached_rows:
                                data[base] = cached_rows
                                sources[base] = "stale-cache"
                            errors[base] = str(exc)
                    if cancelled:
                        for future in pending:
                            future.cancel()
                        break
            finally:
                pool.shutdown(wait=not cancelled, cancel_futures=True)
            if cancelled:
                return {
                    "rows_by_code": data,
                    "sources": sources,
                    "errors": errors,
                    "cancelled": True,
                }
        return {"rows_by_code": data, "sources": sources, "errors": errors, "cancelled": False}

    @staticmethod
    def _session() -> requests.Session:
        retry = Retry(
            total=2,
            connect=2,
            read=2,
            status=2,
            backoff_factor=0.7,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        session = requests.Session()
        session.mount("https://", HTTPAdapter(max_retries=retry))
        return session

    def _fetch_one(self, base: str) -> tuple[list[dict[str, Any]], str]:
        headers = {"Accept": "application/json"}
        api_key = credential_value("PLAN_API_KEY")
        if api_key:
            headers["X-API-Key"] = api_key
        with self._session() as session:
            response = session.get(
                f"{BASE_URL}{ENDPOINT}",
                params={
                    "gd_cd": base,
                    "limit": 0,
                    "prompt_context": "SCM Control Tower 품목코드 구성 조회",
                },
                headers=headers,
                timeout=(15, 120),
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("truncated"):
            raise RuntimeError(f"{base} 품목코드 API 응답이 잘렸습니다.")
        raw_rows = [
            row for row in (payload.get("rows") or [])
            if _text(row.get("gd_cd")).upper().startswith(base)
        ]
        expected = int(payload.get("total_count") or 0)
        if expected and len(raw_rows) != expected:
            raise RuntimeError(
                f"{base} 품목코드 건수 불일치: API {expected:,} / 유효 {len(raw_rows):,}"
            )
        rows = [self._normalize_row(base, row) for row in raw_rows]
        return rows, _text(payload.get("source_refreshed_at"))

    @staticmethod
    def _normalize_row(base: str, row: dict[str, Any]) -> dict[str, Any]:
        power_text = _text(row.get("spec30"))
        cp_text = _text(row.get("spec40"))
        axis_text = _text(row.get("spec50"))
        add_text = _text(row.get("spec60"))
        axis_value = None
        try:
            axis_value = int(axis_text) if axis_text else None
        except ValueError:
            pass
        return {
            "base_code": base,
            "gd_cd": _text(row.get("gd_cd")).upper(),
            "gd_nm": _text(row.get("gd_nm")),
            "model_nm": _text(row.get("model_nm")),
            "power_text": power_text,
            "power_value": _number(power_text),
            "cp_text": cp_text,
            "cp_value": _number(cp_text),
            "axis_text": axis_text,
            "axis_value": axis_value,
            "add_text": add_text,
            "add_value": _number(add_text),
            "stop_yn": _text(row.get("stop_yn")),
            "extracted_at": _text(row.get("extracted_at")),
            "payload_json": json.dumps(row, ensure_ascii=False, sort_keys=True, default=str),
        }

    def _publish(
        self,
        base: str,
        rows: list[dict[str, Any]],
        source_refreshed_at: str,
    ) -> None:
        fetched_at = datetime.now().isoformat(timespec="seconds")
        columns = (
            "base_code", "gd_cd", "gd_nm", "model_nm", "power_text",
            "power_value", "cp_text", "cp_value", "axis_text", "axis_value",
            "add_text", "add_value", "stop_yn", "extracted_at", "payload_json",
        )
        with self._connect() as connection:
            with connection:
                connection.execute("DELETE FROM item_code_current WHERE base_code=?", (base,))
                connection.executemany(
                    f"INSERT INTO item_code_current({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                    (tuple(row.get(column) for column in columns) for row in rows),
                )
                connection.execute(
                    "INSERT INTO item_code_snapshot(base_code,row_count,source_refreshed_at,fetched_at) "
                    "VALUES(?,?,?,?) ON CONFLICT(base_code) DO UPDATE SET "
                    "row_count=excluded.row_count,source_refreshed_at=excluded.source_refreshed_at,"
                    "fetched_at=excluded.fetched_at",
                    (base, len(rows), source_refreshed_at, fetched_at),
                )
