from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from config import DATA_CENTER_DIR


PRODUCT_REFERENCE_DIR = DATA_CENTER_DIR / "bom"
PRODUCT_REFERENCE_DB = PRODUCT_REFERENCE_DIR / "product_reference.sqlite"
PRODUCT_REFERENCE_BACKUP_DIR = PRODUCT_REFERENCE_DIR / "backup"
PRODUCT_REFERENCE_CHANGE_DB = PRODUCT_REFERENCE_DIR / "bom_change_history.sqlite"
CHANGE_HISTORY_RETENTION_DAYS = 90
APS_YIELD_DB = DATA_CENTER_DIR / "aps" / "aps_yield.db"

FACTORY_NAMES = {
    "01": "A관(1공장)",
    "02": "C관(2공장)",
    "03": "L관",
    "04": "S관(3공장)",
}


MONITORED_PRODUCT_PREFIXES = frozenset("TSPQR")


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _quantity(value: Any) -> str:
    if value in (None, ""):
        return "-"
    number = float(value)
    return f"{number:,.6f}".rstrip("0").rstrip(".")


def _factory_name(fac_nm: Any, fac_cd: Any) -> str:
    name = _text(fac_nm)
    if name:
        return name
    code = _text(fac_cd).zfill(2)
    if not code or code == "00":
        return ""
    return FACTORY_NAMES.get(code, f"공장코드 {code}")


class BomExplorerService:
    """Read-only product and BOM graph access for the control tower."""

    def __init__(
        self,
        database_path: Path = PRODUCT_REFERENCE_DB,
        backup_dir: Path = PRODUCT_REFERENCE_BACKUP_DIR,
        change_history_path: Path = PRODUCT_REFERENCE_CHANGE_DB,
    ) -> None:
        self.database_path = Path(database_path)
        self.backup_dir = Path(backup_dir)
        self.change_history_path = Path(change_history_path)
        self._cache_lock = threading.RLock()
        self._product_rows_cache: tuple[tuple[int, int], list[dict[str, str]]] | None = None
        self._search_catalog_cache: tuple[tuple[int, int], list[dict[str, str]]] | None = None
        self._change_overview_cache: tuple[tuple[Any, ...], dict[str, Any]] | None = None

    def available(self) -> bool:
        return self.database_path.is_file()

    def snapshot_status(self) -> dict[str, Any]:
        """Return compact current-snapshot metadata for the local GUI."""
        if not self.available():
            return {
                "available": False,
                "database": str(self.database_path),
                "product_rows": 0,
                "bom_rows": 0,
                "refreshed_at": "",
            }
        with self._connect(self.database_path) as connection:
            product_rows = int(
                connection.execute("SELECT count(*) FROM product_name_master").fetchone()[0]
            )
            bom_rows = int(
                connection.execute("SELECT count(*) FROM bom_relation").fetchone()[0]
            )
            latest = connection.execute(
                "SELECT refreshed_at,source_refreshed_at FROM sync_log "
                "WHERE status IN ('success','skipped') ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return {
            "available": True,
            "database": str(self.database_path),
            "product_rows": product_rows,
            "bom_rows": bom_rows,
            "refreshed_at": _text(latest["refreshed_at"]) if latest else "",
            "source_refreshed_at": _text(latest["source_refreshed_at"]) if latest else "",
        }

    @staticmethod
    def _file_signature(path: Path) -> tuple[int, int]:
        try:
            stat = path.stat()
        except OSError:
            return (0, 0)
        return (stat.st_size, stat.st_mtime_ns)

    def invalidate_cache(self) -> None:
        """Discard read caches after the collector replaces the current snapshot."""
        with self._cache_lock:
            self._product_rows_cache = None
            self._search_catalog_cache = None
            self._change_overview_cache = None

    def warmup(self) -> dict[str, Any]:
        """Prepare local-only BOM datasets in a worker thread during app startup."""
        started = time.perf_counter()
        products = self.product_rows("", limit=10000)
        catalog = self._search_catalog()
        overview = self.bom_change_overview(limit=5000)
        return {
            "product_rows": len(products),
            "search_codes": len(catalog),
            "registrations": len(overview.get("registrations", [])),
            "modifications": len(overview.get("modifications", [])),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        }

    def product_rows(
        self,
        query: str = "",
        *,
        field: str = "all",
        code_prefix: str = "",
        limit: int = 500,
    ) -> list[dict[str, str]]:
        """Return the useful subset of the ERP product-name master."""
        if not self.available():
            return []
        cacheable = not _text(query) and field == "all" and not _text(code_prefix) and limit >= 10000
        signature = self._file_signature(self.database_path)
        if cacheable:
            with self._cache_lock:
                if self._product_rows_cache and self._product_rows_cache[0] == signature:
                    return [dict(row) for row in self._product_rows_cache[1]]
        field = field if field in {"all", "code", "name"} else "all"
        code_prefix = _text(code_prefix).upper()[:1]
        term = _text(query).upper().lstrip("*")
        normalized = " ".join(term.replace("_", " ").replace("-", " ").split())
        escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions: list[str] = []
        params: list[Any] = []
        if term:
            matches: list[str] = []
            if field in {"all", "code"}:
                matches.append("UPPER(nm_cd) LIKE ? ESCAPE '\\'")
                params.append(f"%{term}%")
            if field in {"all", "name"}:
                matches.append(
                    "REPLACE(REPLACE(UPPER(COALESCE(nm_nm,'')),'_',' '),'-',' ') "
                    "LIKE ? ESCAPE '\\'"
                )
                params.append(f"%{escaped}%")
            conditions.append(f"({' OR '.join(matches)})")
        if code_prefix:
            conditions.append("UPPER(nm_cd) LIKE ?")
            params.append(f"{code_prefix}%")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect(self.database_path) as connection:
            rows = connection.execute(
                f"""
                SELECT nm_cd,nm_nm,nm_gu_nm,fac_cd,fac_nm,yy_cnt,dia,bc,full_gu_nm,
                       percontent,payload_json
                FROM product_name_master
                {where}
                ORDER BY
                    CASE UPPER(SUBSTR(nm_cd,1,1))
                        WHEN 'R' THEN 0
                        WHEN 'Q' THEN 1
                        WHEN 'P' THEN 2
                        WHEN 'T' THEN 3
                        WHEN 'S' THEN 4
                        ELSE 9
                    END,
                    UPPER(nm_cd),
                    CASE WHEN use_yn='Y' THEN 0 ELSE 1 END
                LIMIT ?
                """,
                (*params, max(1, min(limit, 10000))),
            ).fetchall()
        result = [
            {
                "code": _text(row["nm_cd"]),
                "name": _text(row["nm_nm"]),
                "kind": _text(row["nm_gu_nm"]),
                "factory": _factory_name(row["fac_nm"], row["fac_cd"]),
                "validity_years": "" if row["yy_cnt"] is None else _quantity(row["yy_cnt"]),
                "dia": "" if row["dia"] is None else _quantity(row["dia"]),
                "bc": "" if row["bc"] is None else _quantity(row["bc"]),
                "classification_summary": _text(row["full_gu_nm"]),
                "colored_outer_diameter": self._payload_value(
                    row["payload_json"],
                    "colored_outer_diameter", "color_outer_diameter", "color_od",
                ),
                "water_content": "" if row["percontent"] is None else _quantity(row["percontent"]),
            }
            for row in rows
        ]
        if cacheable:
            with self._cache_lock:
                self._product_rows_cache = (signature, [dict(row) for row in result])
        return result

    def saline_registered_product_rows(
        self,
        *,
        limit: int = 10000,
    ) -> list[dict[str, str]]:
        """Return active P-codes registered in ERP product-saline mapping."""
        if not self.available():
            return []
        with self._connect(self.database_path) as connection:
            try:
                registered = connection.execute(
                    """
                    SELECT UPPER(nm_cd) AS code,MAX(nm_nm) AS name,
                           MAX(model_nm) AS kind,MAX(nm_fac_nm) AS factory
                    FROM product_saline_registration
                    WHERE UPPER(COALESCE(nm_cd,'')) LIKE 'P%'
                      AND (stts='S' OR stts_label='사용')
                    GROUP BY UPPER(nm_cd)
                    ORDER BY UPPER(nm_cd)
                    LIMIT ?
                    """,
                    (max(1, min(limit, 10000)),),
                ).fetchall()
            except sqlite3.DatabaseError:
                return []

        product_rows = {
            row["code"].upper(): row
            for row in self.product_rows("", code_prefix="P", limit=10000)
        }
        rows: list[dict[str, str]] = []
        for registration in registered:
            code = _text(registration["code"]).upper()
            product = dict(product_rows.get(code, {}))
            rows.append(
                {
                    "code": code,
                    "name": product.get("name") or _text(registration["name"]),
                    "kind": product.get("kind") or _text(registration["kind"]),
                    "factory": product.get("factory") or _text(registration["factory"]),
                    "validity_years": product.get("validity_years", ""),
                    "dia": product.get("dia", ""),
                    "bc": product.get("bc", ""),
                    "classification_summary": product.get(
                        "classification_summary", ""
                    ),
                    "colored_outer_diameter": product.get(
                        "colored_outer_diameter", ""
                    ),
                    "water_content": product.get("water_content", ""),
                }
            )
        return rows

    def saline_lead_details(self, code: str) -> dict[str, Any]:
        """Return active BOM lead-foil and saline registrations for one P-code."""
        normalized = _text(code).upper()
        if not normalized.startswith("P") or not self.available():
            return {"product": {}, "leads": [], "salines": []}
        with self._connect(self.database_path) as connection:
            lead_rows = connection.execute(
                """
                SELECT child_cd AS code,MAX(child_nm) AS name,
                       MAX(child_spec) AS spec,MAX(qty) AS quantity,
                       MAX(use_yn) AS use_yn
                FROM bom_relation
                WHERE UPPER(parent_cd)=?
                  AND INSTR(COALESCE(child_nm,''),'리드지')>0
                  AND use_yn='Y'
                GROUP BY UPPER(child_cd)
                ORDER BY UPPER(child_cd)
                """,
                (normalized,),
            ).fetchall()
            try:
                saline_rows = connection.execute(
                    """
                    SELECT gd_cd AS code,gd_nm AS name,saline_ss AS site_code,
                           gong_cd AS process_code,stts,stts_label,mdt
                    FROM product_saline_registration
                    WHERE UPPER(nm_cd)=?
                      AND (stts='S' OR stts_label='사용')
                    ORDER BY UPPER(gd_cd),mdt DESC
                    """,
                    (normalized,),
                ).fetchall()
            except sqlite3.DatabaseError:
                saline_rows = []
        product = next(
            (
                row
                for row in self.saline_registered_product_rows(limit=10000)
                if row.get("code", "").upper() == normalized
            ),
            {},
        )
        return {
            "product": dict(product),
            "leads": [
                {
                    "code": _text(row["code"]),
                    "name": _text(row["name"]),
                    "spec": _text(row["spec"]),
                    "quantity": _quantity(row["quantity"]),
                    "status": "사용" if _text(row["use_yn"]) == "Y" else "미사용",
                }
                for row in lead_rows
            ],
            "salines": [
                {
                    "code": _text(row["code"]),
                    "name": _text(row["name"]),
                    "site_code": _text(row["site_code"]),
                    "process_code": _text(row["process_code"]),
                    "status": _text(row["stts_label"]) or _text(row["stts"]),
                    "updated_at": _text(row["mdt"]),
                }
                for row in saline_rows
            ],
        }

    def direct_code_links(
        self,
        codes: list[str],
        *,
        allowed_prefixes: tuple[str, ...] = ("T", "S", "P", "Q", "R"),
    ) -> dict[str, dict[str, list[str]]]:
        """Return direct parent/child codes for many items in one read-only query."""
        normalized_codes = sorted({_text(code).upper() for code in codes if _text(code)})
        result = {
            code: {"parents": [], "children": []}
            for code in normalized_codes
        }
        if not normalized_codes or not self.available():
            return result

        allowed = {prefix.upper()[:1] for prefix in allowed_prefixes if prefix}
        parent_sets = {code: set() for code in normalized_codes}
        child_sets = {code: set() for code in normalized_codes}
        with self._connect(self.database_path) as connection:
            for offset in range(0, len(normalized_codes), 400):
                chunk = normalized_codes[offset:offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                rows = connection.execute(
                    f"""
                    SELECT parent_cd,child_cd
                    FROM bom_relation
                    WHERE parent_cd IN ({placeholders})
                       OR child_cd IN ({placeholders})
                    """,
                    (*chunk, *chunk),
                ).fetchall()
                for row in rows:
                    parent = _text(row["parent_cd"]).upper()
                    child = _text(row["child_cd"]).upper()
                    if child in parent_sets and parent[:1] in allowed:
                        parent_sets[child].add(parent)
                    if parent in child_sets and child[:1] in allowed:
                        child_sets[parent].add(child)

        stage_order = {"T": 0, "S": 1, "P": 2, "Q": 3, "R": 4}
        sort_key = lambda code: (stage_order.get(code[:1], 9), code)
        for code in normalized_codes:
            result[code] = {
                "parents": sorted(parent_sets[code], key=sort_key),
                "children": sorted(child_sets[code], key=sort_key),
            }
        return result

    @staticmethod
    def _payload_value(payload_json: Any, *keys: str) -> str:
        try:
            payload = json.loads(_text(payload_json) or "{}")
        except (TypeError, ValueError):
            return ""
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return _text(value)
        return ""

    def code_composition_rows(
        self,
        query: str = "",
        *,
        code_prefix: str = "",
        limit: int = 500,
    ) -> list[dict[str, str]]:
        """Summarize how each item code participates in the BOM graph."""
        if not self.available():
            return []
        term = _text(query).upper().lstrip("*")
        prefix = _text(code_prefix).upper()[:1]
        conditions: list[str] = []
        params: list[Any] = []
        if term:
            conditions.append("(UPPER(p.nm_cd) LIKE ? OR UPPER(COALESCE(p.nm_nm,'')) LIKE ?)")
            params.extend((f"%{term}%", f"%{term}%"))
        if prefix:
            conditions.append("UPPER(p.nm_cd) LIKE ?")
            params.append(f"{prefix}%")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connect(self.database_path) as connection:
            rows = connection.execute(
                f"""
                WITH parent_count AS (
                    SELECT child_cd AS code,COUNT(DISTINCT parent_cd) AS cnt
                    FROM bom_relation GROUP BY child_cd
                ), child_count AS (
                    SELECT parent_cd AS code,COUNT(DISTINCT child_cd) AS cnt
                    FROM bom_relation GROUP BY parent_cd
                )
                SELECT p.nm_cd,p.nm_nm,p.nm_gu_nm,p.use_yn,p.in_dt,
                       COALESCE(pc.cnt,0) AS parent_count,
                       COALESCE(cc.cnt,0) AS child_count
                FROM product_name_master p
                LEFT JOIN parent_count pc ON pc.code=p.nm_cd
                LEFT JOIN child_count cc ON cc.code=p.nm_cd
                {where}
                ORDER BY CASE SUBSTR(UPPER(p.nm_cd),1,1)
                    WHEN 'T' THEN 0 WHEN 'S' THEN 1 WHEN 'P' THEN 2
                    WHEN 'Q' THEN 3 WHEN 'R' THEN 4 WHEN 'B' THEN 5
                    WHEN 'A' THEN 6 ELSE 7 END,p.nm_cd
                LIMIT ?
                """,
                (*params, max(1, min(limit, 2000))),
            ).fetchall()
        return [
            {
                "code": _text(row["nm_cd"]),
                "name": _text(row["nm_nm"]),
                "kind": _text(row["nm_gu_nm"]),
                "use_yn": _text(row["use_yn"]),
                "parent_count": str(int(row["parent_count"] or 0)),
                "child_count": str(int(row["child_count"] or 0)),
                "registered_at": _text(row["in_dt"]),
            }
            for row in rows
        ]

    def bom_change_overview(self, limit: int = 1000) -> dict[str, Any]:
        """Accumulate T registrations, factory changes, and operational BOM changes."""
        backups = sorted(
            (path for path in self.backup_dir.glob("*.sqlite") if path.stat().st_size > 0),
            key=lambda path: path.stat().st_mtime,
        )
        snapshots = backups + ([self.database_path] if self.available() else [])
        baseline = backups[-1].name if backups else "비교 백업 없음"
        source_signature: tuple[Any, ...] = (
            datetime.now().date().isoformat(),
            max(1, limit),
            *(
                (str(path), *self._file_signature(path))
                for path in snapshots
            ),
        )
        with self._cache_lock:
            if self._change_overview_cache and self._change_overview_cache[0] == source_signature:
                cached = self._change_overview_cache[1]
                return {
                    **cached,
                    "registrations": [dict(row) for row in cached["registrations"][:max(1, limit)]],
                    "modifications": [dict(row) for row in cached["modifications"][:max(1, limit)]],
                    "changes": [dict(row) for row in cached["modifications"][:max(1, limit)]],
                }
        try:
            self._sync_change_history(snapshots)
            with closing(sqlite3.connect(self.change_history_path)) as history:
                history.row_factory = sqlite3.Row
                registrations = [
                    dict(row) for row in history.execute(
                        """
                        SELECT detected_at,code,product_name,factory
                        FROM bom_change_event
                        WHERE category='registration'
                        ORDER BY detected_at DESC,code DESC LIMIT ?
                        """,
                        (max(1, limit),),
                    ).fetchall()
                ]
                modifications = [
                    dict(row) for row in history.execute(
                        """
                        SELECT detected_at,change_type,code,product_name,stage,target,before_value,after_value
                        FROM bom_change_event
                        WHERE category='modification'
                        ORDER BY detected_at DESC,id DESC
                        """,
                    ).fetchall()
                ]
            # API 현재 원장이 제공하는 실제 등록일(in_dt, 원천 cdt)로 최근
            # 90일 T코드 신규등록을 복원한다. 과거 수정이력은 추측하지 않는다.
            if self.available():
                with self._connect(self.database_path) as current:
                    current_registrations = [
                        {
                            "detected_at": _text(row["in_dt"]).replace("T", " ")[:19],
                            "code": _text(row["nm_cd"]).upper(),
                            "product_name": _text(row["nm_nm"]),
                            "factory": _factory_name(row["fac_nm"], row["fac_cd"]) or "미등록",
                        }
                        for row in current.execute(
                            """
                            SELECT nm_cd,nm_nm,fac_cd,fac_nm,in_dt
                            FROM product_name_master
                            WHERE UPPER(nm_cd) LIKE 'T%'
                              AND NULLIF(TRIM(in_dt),'') IS NOT NULL
                              AND date(substr(in_dt,1,10)) >= date('now','localtime','-90 days')
                            ORDER BY in_dt DESC,nm_cd DESC
                            """
                        ).fetchall()
                    ]
                merged_registrations = {
                    (_text(row.get("code")).upper(), _text(row.get("detected_at"))[:10]): row
                    for row in registrations
                }
                for row in current_registrations:
                    merged_registrations[
                        (_text(row.get("code")).upper(), _text(row.get("detected_at"))[:10])
                    ] = row
                registrations = sorted(
                    merged_registrations.values(),
                    key=lambda row: (_text(row.get("detected_at")), _text(row.get("code"))),
                    reverse=True,
                )[:max(1, limit)]
            modifications = self._collapse_bom_replacements(modifications)[:max(1, limit)]
            product_names: dict[str, str] = {}
            if self.available():
                with self._connect(self.database_path) as current:
                    product_names = {
                        _text(row["nm_cd"]).upper(): _text(row["nm_nm"])
                        for row in current.execute("SELECT nm_cd,nm_nm FROM product_name_master")
                        if _text(row["nm_cd"])
                    }
                    try:
                        relation_names = current.execute(
                            """
                            SELECT parent_cd AS code,parent_nm AS name FROM bom_relation
                            UNION ALL
                            SELECT child_cd AS code,child_nm AS name FROM bom_relation
                            """
                        ).fetchall()
                    except sqlite3.DatabaseError:
                        relation_names = []
                    for relation_name in relation_names:
                        code = _text(relation_name["code"]).upper()
                        name = _text(relation_name["name"])
                        if code and name and not product_names.get(code):
                            product_names[code] = name
            for row in modifications:
                code = _text(row.get("code")).upper()
                target = _text(row.get("target")).upper()
                parent_name = _text(row.get("product_name")) or product_names.get(code, "")
                target_name = product_names.get(target, "")
                row["parent_display"] = f"{code} · {parent_name}" if parent_name else code
                if row.get("change_type") == "하위 품번 수정":
                    previous_codes = [
                        value for value in _text(row.get("previous_targets")).split("|") if value
                    ]
                    new_codes = [
                        value for value in _text(row.get("new_targets")).split("|") if value
                    ]

                    def display_codes(codes: list[str]) -> str:
                        return " / ".join(
                            f"{item} · {product_names[item]}"
                            if product_names.get(item) else item
                            for item in codes
                        ) or "-"

                    row["target_display"] = (
                        f"기존 {display_codes(previous_codes)} → "
                        f"변경 {display_codes(new_codes)}"
                    )
                    row["change_comment"] = (
                        f"하위 품번 수정 · 기존 {len(previous_codes)}개 → "
                        f"변경 {len(new_codes)}개"
                    )
                elif row.get("change_type") == "생산공장 변경":
                    row["target_display"] = "-"
                    row["change_comment"] = (
                        f"생산공장 변경: {_text(row.get('before_value'))} → "
                        f"{_text(row.get('after_value'))}"
                    )
                else:
                    row["target_display"] = (
                        f"{target} · {target_name}" if target_name else target
                    )
                    if row.get("change_type") == "하위 품번 추가":
                        quantity = _text(row.get("after_value")).split("소요량", 1)[-1].strip()
                        row["change_comment"] = f"하위 품번 추가 · 소요량 {quantity}"
                    elif row.get("change_type") == "하위 품번 제외":
                        quantity = _text(row.get("before_value")).split("소요량", 1)[-1].strip()
                        row["change_comment"] = f"하위 품번 제외 · 기존 소요량 {quantity}"
                    else:
                        row["change_comment"] = (
                            f"소요량 변경: {_text(row.get('before_value'))} → "
                            f"{_text(row.get('after_value'))}"
                        )
        except (sqlite3.DatabaseError, OSError):
            registrations, modifications = [], []
        result = {
            "registrations": registrations,
            "modifications": modifications,
            "changes": modifications,
            "baseline": baseline,
            "history_db": self.change_history_path.name,
            "retention_days": CHANGE_HISTORY_RETENTION_DAYS,
        }
        with self._cache_lock:
            self._change_overview_cache = (
                source_signature,
                {
                    **result,
                    "registrations": [dict(row) for row in registrations],
                    "modifications": [dict(row) for row in modifications],
                    "changes": [dict(row) for row in modifications],
                },
            )
        return result

    @staticmethod
    def _collapse_bom_replacements(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Show relation changes only when a removal and addition form a replacement."""
        passthrough: list[dict[str, Any]] = []
        relation_groups: dict[
            tuple[str, str, str], dict[str, list[dict[str, Any]]]
        ] = {}
        for row in rows:
            change_type = _text(row.get("change_type"))
            if change_type not in {"하위 품번 추가", "하위 품번 제외"}:
                passthrough.append(row)
                continue
            key = (
                _text(row.get("detected_at")),
                _text(row.get("code")).upper(),
                _text(row.get("stage")),
            )
            bucket = relation_groups.setdefault(key, {"added": [], "removed": []})
            bucket["added" if change_type == "하위 품번 추가" else "removed"].append(row)

        for (_detected_at, _code, _stage), bucket in relation_groups.items():
            added = sorted(bucket["added"], key=lambda row: _text(row.get("target")))
            removed = sorted(bucket["removed"], key=lambda row: _text(row.get("target")))
            if not added or not removed:
                continue
            replacement = dict(added[0])
            replacement["change_type"] = "하위 품번 수정"
            replacement["previous_targets"] = "|".join(
                _text(row.get("target")).upper() for row in removed
            )
            replacement["new_targets"] = "|".join(
                _text(row.get("target")).upper() for row in added
            )
            replacement["before_value"] = " | ".join(
                _text(row.get("before_value")) for row in removed
            )
            replacement["after_value"] = " | ".join(
                _text(row.get("after_value")) for row in added
            )
            passthrough.append(replacement)

        return sorted(
            passthrough,
            key=lambda row: (
                _text(row.get("detected_at")),
                _text(row.get("code")),
                _text(row.get("stage")),
            ),
            reverse=True,
        )

    def _sync_change_history(self, snapshots: list[Path]) -> None:
        self.change_history_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.change_history_path)) as history:
            history.row_factory = sqlite3.Row
            history.executescript(
                """
                PRAGMA busy_timeout=5000;
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS bom_change_event (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_key TEXT NOT NULL UNIQUE,
                    detected_at TEXT NOT NULL,
                    category TEXT NOT NULL,
                    change_type TEXT NOT NULL,
                    code TEXT NOT NULL,
                    product_name TEXT,
                    factory TEXT,
                    stage TEXT,
                    target TEXT,
                    before_value TEXT,
                    after_value TEXT,
                    baseline TEXT,
                    current_snapshot TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS ix_bom_change_event_category_date
                    ON bom_change_event(category,detected_at DESC);
                CREATE TABLE IF NOT EXISTS processed_comparison (
                    pair_key TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS history_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            cleanup_version = history.execute(
                "SELECT value FROM history_meta WHERE key='baseline_cleanup_version'"
            ).fetchone()
            if not cleanup_version or cleanup_version[0] != "2":
                history.execute(
                    """
                    DELETE FROM bom_change_event
                    WHERE detected_at IN (
                        SELECT detected_at
                        FROM bom_change_event
                        GROUP BY detected_at
                        HAVING SUM(CASE WHEN category='registration' THEN 1 ELSE 0 END) > 1000
                           AND SUM(CASE WHEN category='modification' THEN 1 ELSE 0 END) > 5000
                    )
                    """
                )
                history.execute(
                    "INSERT OR REPLACE INTO history_meta(key,value) VALUES('baseline_cleanup_version','2')"
                )
                history.commit()
            history.execute(
                "DELETE FROM bom_change_event WHERE detected_at < datetime('now','localtime',?)",
                (f"-{CHANGE_HISTORY_RETENTION_DAYS} days",),
            )
            for previous_path, current_path in zip(snapshots, snapshots[1:]):
                previous_stat = previous_path.stat()
                current_stat = current_path.stat()
                pair_key = (
                    f"{previous_path.name}:{previous_stat.st_size}:{previous_stat.st_mtime_ns}|"
                    f"{current_path.name}:{current_stat.st_size}:{current_stat.st_mtime_ns}"
                )
                if history.execute(
                    "SELECT 1 FROM processed_comparison WHERE pair_key=?", (pair_key,)
                ).fetchone():
                    continue
                with self._connect(previous_path) as previous, self._connect(current_path) as current:
                    detected_at = self._snapshot_time(current, current_path)
                    self._record_snapshot_changes(
                        history,
                        previous,
                        current,
                        detected_at=detected_at,
                        baseline=previous_path.name,
                        current_snapshot=current_path.name,
                    )
                history.execute(
                    "INSERT OR IGNORE INTO processed_comparison(pair_key) VALUES(?)",
                    (pair_key,),
                )
                history.commit()
            history.execute(
                "DELETE FROM bom_change_event WHERE detected_at < datetime('now','localtime',?)",
                (f"-{CHANGE_HISTORY_RETENTION_DAYS} days",),
            )
            history.commit()

    @staticmethod
    def _snapshot_time(connection: sqlite3.Connection, path: Path) -> str:
        for table in ("product_name_master", "bom_relation"):
            try:
                row = connection.execute(
                    f"SELECT MAX(extracted_at) FROM {table}"
                ).fetchone()
            except sqlite3.DatabaseError:
                row = None
            if row and _text(row[0]):
                return _text(row[0]).replace("T", " ")[:19]
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(
            timespec="seconds"
        )

    @staticmethod
    def _operational_stage(parent: str, child: str) -> str:
        parent_prefix = parent[:1].upper()
        child_prefix = child[:1].upper()
        if parent_prefix in {"S", "T"} and child_prefix == "P":
            return "판매→생산"
        if parent_prefix == "P" and child_prefix not in {"S", "T", "P", "R"}:
            return "생산→분리"
        if parent_prefix == "Q" and child_prefix == "R":
            return "분리→사출"
        if parent_prefix == "R":
            return "사출→사출 하위"
        return ""

    @classmethod
    def _record_snapshot_changes(
        cls,
        history: sqlite3.Connection,
        previous: sqlite3.Connection,
        current: sqlite3.Connection,
        *,
        detected_at: str,
        baseline: str,
        current_snapshot: str,
    ) -> None:
        def products(connection: sqlite3.Connection) -> dict[str, dict[str, str]]:
            return {
                _text(row["nm_cd"]).upper(): {
                    "name": _text(row["nm_nm"]),
                    "factory_code": _text(row["fac_cd"]),
                    "factory": _factory_name(row["fac_nm"], row["fac_cd"]) or "미등록",
                }
                for row in connection.execute(
                    "SELECT nm_cd,nm_nm,fac_cd,fac_nm FROM product_name_master "
                    "WHERE UPPER(nm_cd) LIKE 'T%'"
                )
                if _text(row["nm_cd"])
            }

        previous_products = products(previous)
        current_products = products(current)

        def add_event(**values: str) -> None:
            identity = json.dumps(
                {
                    "detected_at": detected_at,
                    "baseline": baseline,
                    **values,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            event_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            history.execute(
                """
                INSERT OR IGNORE INTO bom_change_event(
                    event_key,detected_at,category,change_type,code,product_name,
                    factory,stage,target,before_value,after_value,baseline,current_snapshot
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_key,
                    detected_at,
                    values.get("category", ""),
                    values.get("change_type", ""),
                    values.get("code", ""),
                    values.get("product_name", ""),
                    values.get("factory", ""),
                    values.get("stage", ""),
                    values.get("target", ""),
                    values.get("before_value", ""),
                    values.get("after_value", ""),
                    baseline,
                    current_snapshot,
                ),
            )

        if previous_products:
            for code in sorted(current_products.keys() - previous_products.keys()):
                product = current_products[code]
                add_event(
                    category="registration",
                    change_type="T코드 신규등록",
                    code=code,
                    product_name=product["name"] or "품명 정보 없음",
                    factory=product["factory"],
                    stage="제품정보",
                    target="신규등록",
                    before_value="-",
                    after_value=product["factory"],
                )

        for code in sorted(current_products.keys() & previous_products.keys()):
            before_product = previous_products[code]
            after_product = current_products[code]
            if before_product["factory_code"] != after_product["factory_code"]:
                add_event(
                    category="modification",
                    change_type="생산공장 변경",
                    code=code,
                    product_name=after_product["name"] or before_product["name"],
                    factory=after_product["factory"],
                    stage="제품정보",
                    target=after_product["name"] or "생산공장",
                    before_value=before_product["factory"],
                    after_value=after_product["factory"],
                )

        def relations(connection: sqlite3.Connection) -> dict[tuple[str, str], str]:
            result: dict[tuple[str, str], str] = {}
            for row in connection.execute("SELECT parent_cd,child_cd,qty FROM bom_relation"):
                parent = _text(row["parent_cd"]).upper()
                child = _text(row["child_cd"]).upper()
                if parent and child and cls._operational_stage(parent, child):
                    result[(parent, child)] = _quantity(row["qty"])
            return result

        previous_relations = relations(previous)
        current_relations = relations(current)
        if previous_relations:
            for parent, child in sorted(current_relations.keys() - previous_relations.keys()):
                add_event(
                    category="modification",
                    change_type="하위 품번 추가",
                    code=parent,
                    stage=cls._operational_stage(parent, child),
                    target=child,
                    before_value="-",
                    after_value=f"{child} · 소요량 {current_relations[(parent, child)]}",
                )
            for parent, child in sorted(previous_relations.keys() - current_relations.keys()):
                add_event(
                    category="modification",
                    change_type="하위 품번 제외",
                    code=parent,
                    stage=cls._operational_stage(parent, child),
                    target=child,
                    before_value=f"{child} · 소요량 {previous_relations[(parent, child)]}",
                    after_value="-",
                )
            for parent, child in sorted(current_relations.keys() & previous_relations.keys()):
                before_value = previous_relations[(parent, child)]
                after_value = current_relations[(parent, child)]
                if before_value != after_value:
                    add_event(
                        category="modification",
                        change_type="소요량 변경",
                        code=parent,
                        stage=cls._operational_stage(parent, child),
                        target=child,
                        before_value=before_value,
                        after_value=after_value,
                    )

    @staticmethod
    @contextmanager
    def _connect(path: Path) -> Iterator[sqlite3.Connection]:
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
        finally:
            connection.close()

    def _search_catalog(self) -> list[dict[str, str]]:
        """Load the deduplicated code/name catalog once per DB snapshot."""
        signature = self._file_signature(self.database_path)
        with self._cache_lock:
            if self._search_catalog_cache and self._search_catalog_cache[0] == signature:
                return self._search_catalog_cache[1]
        with self._connect(self.database_path) as connection:
            rows = connection.execute(
                """
                WITH candidates AS (
                    SELECT nm_cd AS code,nm_nm AS name,nm_gu_nm AS kind,use_yn
                    FROM product_name_master
                    UNION ALL
                    SELECT parent_cd,parent_nm,parent_gu,use_yn FROM bom_relation
                    UNION ALL
                    SELECT child_cd,child_nm,child_gu,use_yn FROM bom_relation
                )
                SELECT UPPER(code) AS code,MAX(name) AS name,
                       MAX(kind) AS kind,MAX(use_yn) AS use_yn
                FROM candidates
                WHERE trim(COALESCE(code,''))<>''
                GROUP BY UPPER(code)
                """
            ).fetchall()
        catalog = [
            {
                "code": _text(row["code"]).upper(),
                "name": _text(row["name"]),
                "kind": _text(row["kind"]),
                "use_yn": _text(row["use_yn"]),
            }
            for row in rows
        ]
        with self._cache_lock:
            self._search_catalog_cache = (signature, catalog)
        return catalog

    def search(
        self,
        query: str,
        limit: int = 20,
        *,
        field: str = "all",
        code_prefix: str = "",
    ) -> list[dict[str, str]]:
        term = _text(query).upper()
        if not term or not self.available():
            return []
        field = field if field in {"all", "code", "name"} else "all"
        code_prefix = _text(code_prefix).upper()[:1]
        master_search = term.startswith("*")
        search_term = term[1:].strip() if master_search else term
        if not search_term:
            return []
        normalized_term = " ".join(
            search_term.replace("_", " ").replace("-", " ").split()
        )
        stage_order = {"T": 0, "S": 1, "P": 2, "Q": 3, "R": 4, "B": 5, "A": 6}
        matches: list[dict[str, str]] = []
        for row in self._search_catalog():
            code = row["code"]
            if code_prefix and not code.startswith(code_prefix):
                continue
            normalized_name = " ".join(
                row["name"].upper().replace("_", " ").replace("-", " ").split()
            )
            code_value = code[1:] if master_search else code
            code_match = (
                code_value.startswith(search_term)
                if master_search else search_term in code_value
            )
            name_match = normalized_term in normalized_name
            if not (
                (field in {"all", "code"} and code_match)
                or (field in {"all", "name"} and name_match)
            ):
                continue
            candidate = dict(row)
            if master_search:
                candidate["_score"] = str(
                    0 if code_value == search_term else 1 if code_value.startswith(search_term) else 2
                )
            else:
                candidate["_score"] = str(
                    0 if code == search_term else 1 if code.startswith(search_term) else 2
                )
            matches.append(candidate)

        if master_search:
            matches.sort(
                key=lambda row: (
                    int(row["_score"]),
                    row["code"][1:],
                    stage_order.get(row["code"][:1], 7),
                    row["code"],
                )
            )
        else:
            matches.sort(key=lambda row: (int(row["_score"]), row["code"]))
        return [
            {key: value for key, value in row.items() if key != "_score"}
            for row in matches[:max(1, limit)]
        ]

    def detail(self, code: str) -> dict[str, Any]:
        """Return detail/change data without rebuilding or changing the tree."""
        normalized = _text(code).upper()
        if not normalized:
            raise ValueError("조회할 품번을 입력해주세요.")
        if not self.available():
            raise FileNotFoundError(
                f"제품·BOM 기준정보 DB를 찾을 수 없습니다.\n{self.database_path}"
            )
        with self._connect(self.database_path) as connection:
            item = self._item(connection, normalized)
        if not item:
            raise LookupError(f"'{normalized}' 품번을 제품정보와 BOM에서 찾지 못했습니다.")
        return {
            "code": normalized,
            "item": item,
            "changes": self._change_history(normalized),
        }

    def code_configuration(self, code: str) -> dict[str, Any]:
        """Resolve one BOM line and its observed APS full item-code variants."""
        normalized = _text(code).upper()
        if not normalized:
            raise ValueError("조회할 품번을 입력해주세요.")
        if not self.available():
            raise FileNotFoundError(f"제품·BOM 기준정보 DB가 없습니다.\n{self.database_path}")

        with self._connect(self.database_path) as connection:
            prefix = normalized[:1]
            direct_q_codes: list[str] = []
            if prefix == "P":
                production_codes = [normalized]
            elif prefix in {"S", "T"}:
                production_codes = self._linked_codes(
                    connection, normalized, parent=False, prefixes={"P"}
                )
                direct_q_codes = self._linked_codes(
                    connection, normalized, parent=False, prefixes={"Q"}
                )
                # DRY products can skip the ordinary T/S -> P edge and connect
                # directly to Q. Keep the parallel P code available for its ERP
                # item-code column, but preserve the real T/S -> Q route.
                production_codes = sorted(set(production_codes) | {
                    production
                    for q_code in direct_q_codes
                    for production in self._linked_codes(
                        connection, q_code, parent=True, prefixes={"P"}
                    )
                })
            elif prefix == "Q":
                production_codes = self._linked_codes(
                    connection, normalized, parent=True, prefixes={"P"}
                )
            elif prefix == "R":
                q_codes = self._linked_codes(
                    connection, normalized, parent=True, prefixes={"Q"}
                )
                production_codes = sorted({
                    production
                    for q_code in q_codes
                    for production in self._linked_codes(
                        connection, q_code, parent=True, prefixes={"P"}
                    )
                })
            else:
                production_codes = []

            if not production_codes:
                raise LookupError(f"'{normalized}'에서 연결된 생산코드(P)를 찾지 못했습니다.")
            production = production_codes[0]
            production_sales_codes = self._linked_codes(
                connection, production, parent=True, prefixes={"S", "T"}
            )
            if prefix == "Q":
                q_codes = [normalized]
            elif prefix == "R":
                q_codes = self._linked_codes(
                    connection, normalized, parent=True, prefixes={"Q"}
                )
            elif direct_q_codes:
                q_codes = direct_q_codes
            else:
                q_codes = self._linked_codes(
                    connection, production, parent=False, prefixes={"Q"}
                )
            separation = q_codes[0] if q_codes else ""
            direct_sales_codes = (
                self._linked_codes(
                    connection, separation, parent=True, prefixes={"S", "T"}
                )
                if separation else []
            )
            sales_codes = sorted(set(production_sales_codes) | set(direct_sales_codes))
            if prefix in {"S", "T"} and normalized in sales_codes:
                sales_codes.remove(normalized)
                sales_codes.insert(0, normalized)
            r_codes = (
                self._linked_codes(
                    connection, separation, parent=False, prefixes={"R"}
                )
                if separation else []
            )
            injection = r_codes[0] if r_codes else ""

            def option(item_code: str) -> dict[str, str]:
                item = self._item(connection, item_code)
                return {
                    "code": item_code,
                    "name": _text(item.get("name")),
                    "label": f"{item_code} · {_text(item.get('name'))}".rstrip(" ·"),
                }

            result = {
                "searched_code": normalized,
                "production_options": [option(item) for item in production_codes],
                "sales_options": [option(item) for item in sales_codes],
                "direct_sales_codes": direct_sales_codes,
                "production": option(production),
                "separation": option(separation) if separation else {},
                "injection": option(injection) if injection else {},
            }
        return result

    @staticmethod
    def _linked_codes(
        connection: sqlite3.Connection,
        code: str,
        *,
        parent: bool,
        prefixes: set[str],
    ) -> list[str]:
        select_column = "parent_cd" if parent else "child_cd"
        where_column = "child_cd" if parent else "parent_cd"
        rows = connection.execute(
            f"""
            SELECT DISTINCT {select_column} AS code
            FROM bom_relation
            WHERE {where_column}=? AND trim(COALESCE({select_column},''))<>''
            """,
            (code,),
        ).fetchall()
        return sorted({
            _text(row["code"]).upper()
            for row in rows
            if _text(row["code"])[:1].upper() in prefixes
        })

    @staticmethod
    def _variant_tail(full_code: str, base_code: str) -> str:
        tail = full_code[len(base_code):] if full_code.upper().startswith(base_code) else full_code
        if len(tail) > 1 and tail[:1].upper() in {"A", "S"} and tail[1] in "+-":
            tail = tail[1:]
        return tail

    @staticmethod
    def _variant_spec(tail: str) -> str:
        match = re.match(r"([+-]\d+(?:\.\d+)?)", tail)
        return match.group(1) if match else tail

    @classmethod
    def _variant_sort_key(cls, tail: str) -> tuple[float, str]:
        spec = cls._variant_spec(tail)
        try:
            return (abs(float(spec)), tail.upper())
        except ValueError:
            return (float("inf"), tail.upper())

    def _full_code_rows(
        self,
        production: str,
        separation: str,
        injection: str,
    ) -> list[dict[str, str]]:
        bases = {
            "production_full": production,
            "separation_full": separation,
            "injection_full": injection,
        }
        grouped: dict[str, dict[str, str]] = {}
        if APS_YIELD_DB.is_file():
            with closing(
                sqlite3.connect(f"file:{APS_YIELD_DB.as_posix()}?mode=ro", uri=True)
            ) as connection:
                for key, base in bases.items():
                    if not base:
                        continue
                    rows = connection.execute(
                        """
                        SELECT DISTINCT item_id
                        FROM aps_need_item
                        WHERE UPPER(item_id) LIKE ? AND UPPER(item_id)<>?
                        """,
                        (f"{base}%", base),
                    ).fetchall()
                    for (full_code,) in rows:
                        full_text = _text(full_code).upper()
                        tail = self._variant_tail(full_text, base)
                        if tail:
                            # P/Q/R은 같은 도수여도 뒤쪽 제조 규격(RHA2, CRG3 등)이
                            # 다를 수 있다. 전체 꼬리값이 아니라 도수를 기준으로 같은
                            # 행에 맞춰야 한 BOM의 풀코드를 좌우로 비교할 수 있다.
                            spec = self._variant_spec(tail)
                            grouped.setdefault(spec, {}).setdefault(key, full_text)
        return [
            {
                "variant": spec,
                "spec": spec,
                "production_full": values.get("production_full", ""),
                "separation_full": values.get("separation_full", ""),
                "injection_full": values.get("injection_full", ""),
            }
            for spec, values in sorted(
                grouped.items(), key=lambda item: self._variant_sort_key(item[0])
            )
        ]

    def graph(self, code: str) -> dict[str, Any]:
        normalized = _text(code).upper()
        if not normalized:
            raise ValueError("조회할 품번을 입력해주세요.")
        if not self.available():
            raise FileNotFoundError(f"제품·BOM 기준정보 DB를 찾을 수 없습니다.\n{self.database_path}")

        with self._connect(self.database_path) as connection:
            item = self._item(connection, normalized)
            if not item:
                raise LookupError(f"'{normalized}' 품번을 제품정보와 BOM에서 찾지 못했습니다.")
            parents = self._neighbors(connection, normalized, parent=True)
            children = self._neighbors(connection, normalized, parent=False)
            source = connection.execute(
                """
                SELECT source_refreshed_at,refreshed_at,row_count
                FROM sync_log
                WHERE dataset='product-reference' AND status IN ('success','skipped')
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()

        return {
            "code": normalized,
            "item": item,
            "parents": parents,
            "children": children,
            "changes": self._change_history(normalized),
            "source_refreshed_at": _text(source["source_refreshed_at"]) if source else "",
            "local_refreshed_at": (
                _text(source["refreshed_at"])
                if source and _text(source["refreshed_at"])
                else datetime.fromtimestamp(self.database_path.stat().st_mtime).astimezone().isoformat(timespec="minutes")
            ),
            "source_rows": int(source["row_count"] or 0) if source else 0,
            "hierarchy": self._hierarchy(normalized),
        }

    def _hierarchy(self, selected_code: str) -> dict[str, Any]:
        """Return the operational five-stage BOM tree around one item."""
        with self._connect(self.database_path) as connection:
            nodes: dict[str, dict[str, str]] = {}
            source_edges: dict[tuple[str, str], dict[str, str]] = {}

            def add_node(code: str) -> None:
                if code not in nodes:
                    item = self._item(connection, code)
                    if item:
                        nodes[code] = item

            add_node(selected_code)

            def add_edge(row: sqlite3.Row) -> None:
                parent_code = _text(row["parent_cd"])
                child_code = _text(row["child_cd"])
                add_node(parent_code)
                add_node(child_code)
                source_edges[(parent_code, child_code)] = {
                    "parent": parent_code,
                    "child": child_code,
                    "qty": _quantity(row["qty"]),
                    "use_yn": _text(row["use_yn"]),
                }

            # Find upstream paths, stopping at the S/T finished-product stage.
            frontier = {selected_code}
            visited = {selected_code}
            for _depth in range(4):
                next_frontier: set[str] = set()
                for child_code in frontier:
                    rows = connection.execute(
                        """
                        SELECT parent_cd,child_cd,qty,use_yn
                        FROM bom_relation WHERE child_cd=?
                        """,
                        (child_code,),
                    ).fetchall()
                    for row in rows:
                        parent_code = _text(row["parent_cd"])
                        if child_code[:1].upper() in {"S", "T"}:
                            continue
                        add_edge(row)
                        if parent_code not in visited:
                            visited.add(parent_code)
                            next_frontier.add(parent_code)
                frontier = next_frontier
                if not frontier:
                    break

            # Follow only through the direct children of R. The ERP BOM screen
            # treats those four rows as the final visible injection sub-items.
            frontier = {selected_code}
            visited = {selected_code}
            for _depth in range(4):
                next_frontier = set()
                for parent_code in frontier:
                    rows = connection.execute(
                        """
                        SELECT parent_cd,child_cd,qty,use_yn
                        FROM bom_relation WHERE parent_cd=?
                        """,
                        (parent_code,),
                    ).fetchall()
                    for row in rows:
                        child_code = _text(row["child_cd"])
                        add_edge(row)
                        if child_code not in visited:
                            visited.add(child_code)
                            next_frontier.add(child_code)
                frontier = next_frontier
                if not frontier:
                    break

            # When Q/R/material is searched, show siblings hanging from every
            # upstream P as well (Q and its lid/blister packaging share a line).
            p_codes = [code for code in nodes if code[:1].upper() == "P"]
            for p_code in p_codes:
                rows = connection.execute(
                    """
                    SELECT parent_cd,child_cd,qty,use_yn
                    FROM bom_relation WHERE parent_cd=?
                    """,
                    (p_code,),
                ).fetchall()
                for row in rows:
                    add_edge(row)

        def stage(code: str) -> int:
            prefix = code[:1].upper()
            if prefix in {"S", "T"}:
                return 0
            if prefix == "P":
                return 1
            if prefix == "Q":
                return 2
            if prefix == "R":
                return 3
            if any(
                edge["child"] == code and edge["parent"][:1].upper() == "P"
                for edge in source_edges.values()
            ):
                return 2
            return 4

        columns: list[list[dict[str, str]]] = [[] for _ in range(5)]
        for code, item in nodes.items():
            columns[stage(code)].append(item)

        def separation_order(item: dict[str, str]) -> tuple[int, str]:
            code = item["code"].upper()
            name = item.get("name", "")
            if code.startswith("Q"):
                return (0, code)
            if "블리스터" in name:
                return (1, code)
            if "리드지" in name:
                return (2, code)
            return (3, code)

        def injection_child_order(item: dict[str, str]) -> tuple[int, int, str]:
            code = item["code"].upper()
            if code.startswith("B") and not code.startswith(("BC", "BS")):
                return (0, 0, code)
            if code.startswith("BS"):
                return (1, 0, code)
            if code.startswith("BC"):
                number = int(code[2:]) if code[2:].isdigit() else 0
                return (2, -number, code)
            return (3, 0, code)

        for column in columns:
            column.sort(key=lambda item: item["code"])
        columns[2].sort(key=separation_order)
        direct_r_children = {
            edge["child"]
            for edge in source_edges.values()
            if edge["parent"][:1].upper() == "R"
        }
        columns[4] = [
            item for item in columns[4] if item["code"] in direct_r_children
        ]
        columns[4].sort(key=injection_child_order)

        display_edges: dict[tuple[str, str], dict[str, str]] = {}
        for key, edge in source_edges.items():
            if stage(edge["parent"]) < stage(edge["child"]):
                display_edges[key] = edge

        return {
            "selected_code": selected_code,
            "selected_column": stage(selected_code),
            "columns": columns,
            "edges": list(display_edges.values()),
        }

    @staticmethod
    def _item(connection: sqlite3.Connection, code: str) -> dict[str, str]:
        product = connection.execute(
            "SELECT * FROM product_name_master WHERE nm_cd=?",
            (code,),
        ).fetchone()
        relation = connection.execute(
            """
            SELECT parent_cd,parent_nm,parent_gu,child_cd,child_nm,child_gu,use_yn,
                   child_spec,extracted_at
            FROM bom_relation
            WHERE parent_cd=? OR child_cd=?
            ORDER BY CASE WHEN parent_cd=? THEN 0 ELSE 1 END,use_yn DESC
            LIMIT 1
            """,
            (code, code, code),
        ).fetchone()
        if not product and not relation:
            return {}

        if relation and relation["parent_cd"] == code:
            relation_name = relation["parent_nm"]
            relation_kind = relation["parent_gu"]
        elif relation:
            relation_name = relation["child_nm"]
            relation_kind = relation["child_gu"]
        else:
            relation_name = ""
            relation_kind = ""

        def pick(field: str, fallback: Any = "") -> str:
            if product and field in product.keys() and _text(product[field]):
                return _text(product[field])
            return _text(fallback)

        return {
            "code": code,
            "name": pick("nm_nm", relation_name),
            "kind": pick("nm_gu_nm", relation_kind),
            "model_no": pick("model_no"),
            "model_name": pick("model_nm"),
            "factory": _factory_name(
                product["fac_nm"] if product and "fac_nm" in product.keys() else "",
                product["fac_cd"] if product and "fac_cd" in product.keys() else "",
            ),
            "wear_cycle": pick("wear_cycle"),
            "color_yn": pick("color_yn"),
            "dia": pick("dia"),
            "bc": pick("bc"),
            "status": pick("stts"),
            "use_yn": pick("use_yn", relation["use_yn"] if relation else ""),
            "registered_at": pick("in_dt"),
            "extracted_at": pick(
                "extracted_at", relation["extracted_at"] if relation else ""
            ),
        }

    @staticmethod
    def _neighbors(
        connection: sqlite3.Connection,
        code: str,
        *,
        parent: bool,
    ) -> list[dict[str, str]]:
        if parent:
            sql = """
                SELECT parent_cd AS code,MAX(parent_nm) AS name,
                       MAX(parent_gu) AS kind,MAX(qty) AS qty,MAX(use_yn) AS use_yn,
                       MAX(extracted_at) AS extracted_at
                FROM bom_relation WHERE child_cd=?
                GROUP BY parent_cd
            """
        else:
            sql = """
                SELECT child_cd AS code,MAX(child_nm) AS name,
                       MAX(child_gu) AS kind,MAX(qty) AS qty,MAX(use_yn) AS use_yn,
                       MAX(extracted_at) AS extracted_at
                FROM bom_relation WHERE parent_cd=?
                GROUP BY child_cd
            """
        rows = connection.execute(sql, (code,)).fetchall()

        prefix_order = (
            {"S": 0, "T": 1, "P": 2, "Q": 3, "R": 4}
            if parent
            else {"Q": 0, "R": 1, "P": 2, "B": 3, "A": 4}
        )
        result = [
            {
                "code": _text(row["code"]),
                "name": _text(row["name"]),
                "kind": _text(row["kind"]),
                "qty": _quantity(row["qty"]),
                "use_yn": _text(row["use_yn"]),
                "extracted_at": _text(row["extracted_at"]),
            }
            for row in rows
        ]
        result.sort(
            key=lambda item: (
                prefix_order.get(item["code"][:1].upper(), 9),
                item["code"],
            )
        )
        return result

    def _change_history(self, code: str) -> list[dict[str, str]]:
        backups = sorted(
            self.backup_dir.glob("*.sqlite"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        previous_path = next(
            (path for path in backups if path.stat().st_size > 0),
            None,
        )
        if previous_path is None:
            return []

        try:
            with self._connect(self.database_path) as current, self._connect(previous_path) as previous:
                return self._compare_code(current, previous, code)
        except (sqlite3.DatabaseError, OSError):
            return []

    @staticmethod
    def _compare_code(
        current: sqlite3.Connection,
        previous: sqlite3.Connection,
        code: str,
    ) -> list[dict[str, str]]:
        changes: list[dict[str, str]] = []
        current_item = current.execute(
            "SELECT * FROM product_name_master WHERE nm_cd=?", (code,)
        ).fetchone()
        previous_item = previous.execute(
            "SELECT * FROM product_name_master WHERE nm_cd=?", (code,)
        ).fetchone()
        if (
            current_item
            and not previous_item
            and code[:1].upper() in MONITORED_PRODUCT_PREFIXES
        ):
            changes.append(
                {
                    "type": "신규 제품 등록",
                    "target": _text(current_item["nm_nm"]) or "품명 정보 없음",
                    "before": "-",
                    "after": "신규 등록",
                }
            )

        def relations(connection: sqlite3.Connection) -> dict[tuple[str, str], str]:
            rows = connection.execute(
                """
                SELECT parent_cd,child_cd,qty
                FROM bom_relation WHERE parent_cd=? OR child_cd=?
                """,
                (code, code),
            ).fetchall()
            return {
                (_text(row["parent_cd"]), _text(row["child_cd"])): _quantity(row["qty"])
                for row in rows
            }

        current_relations = relations(current)
        previous_relations = relations(previous)
        for key in sorted(current_relations.keys() - previous_relations.keys()):
            changes.append(
                {
                    "type": "BOM 연결 추가",
                    "target": f"{key[0]} → {key[1]}",
                    "before": "-",
                    "after": f"소요량 {current_relations[key]}",
                }
            )
        for key in sorted(previous_relations.keys() - current_relations.keys()):
            changes.append(
                {
                    "type": "BOM 연결 제외",
                    "target": f"{key[0]} → {key[1]}",
                    "before": f"소요량 {previous_relations[key]}",
                    "after": "-",
                }
            )
        for key in sorted(current_relations.keys() & previous_relations.keys()):
            before = previous_relations[key]
            after = current_relations[key]
            if before != after:
                changes.append(
                    {
                        "type": "BOM 소요량 수정",
                        "target": f"{key[0]} → {key[1]}",
                        "before": before,
                        "after": after,
                    }
                )
        return changes[:50]
