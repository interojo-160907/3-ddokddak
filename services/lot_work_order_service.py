from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from math import floor
from pathlib import Path

from config import DATA_CENTER_DIR
from services.live_production_need_service import DB_PATH
from services.process_status_service import (
    _channel,
    classification_sort_key,
    optical_specs_from_item_code,
    power_sort_key,
)


LOT_DATE_PATTERN = re.compile(r"^[A-Z](\d{8})-")
PRODUCTION_DB_PATH = DATA_CENTER_DIR / "production-performance" / "production_performance.sqlite"
BOM_DB_PATH = DATA_CENTER_DIR / "bom" / "product_reference.sqlite"
R_BASE_CODE_PATTERN = re.compile(r"^(R\d{4})", re.IGNORECASE)
Q_BASE_CODE_PATTERN = re.compile(r"^(Q\d{4})", re.IGNORECASE)
P_BASE_CODE_PATTERN = re.compile(r"^(P\d{4})", re.IGNORECASE)
MIN_REWORK_QTY = 200.0
HYDRATION_SPLIT_UNIT = 100.0


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _compact(values: list[str], limit: int = 3) -> str:
    unique = list(dict.fromkeys(value for value in values if value))
    if not unique:
        return ""
    return " / ".join(unique[:limit]) + (
        f" 외 {len(unique) - limit}건" if len(unique) > limit else ""
    )


def _lot_sort_key(lot_no: object) -> tuple[str, str]:
    text = str(lot_no or "").strip().upper()
    match = LOT_DATE_PATTERN.match(text)
    return (match.group(1) if match else "99999999", text)


def _work_sort_key(row: dict) -> tuple:
    return (
        str(row.get("납기일") or "9999-12-31"),
        power_sort_key(row.get("_POWER_NUM", row.get("POWER"))),
        classification_sort_key(row.get("신규분류요약")),
        str(row.get("R코드") or row.get("Q코드") or ""),
        _lot_sort_key(row.get("체크시트(LOT)")),
    )


class LotWorkOrderService:
    """현재 필요수량과 공정창고 재고를 LOT 전량 작업지시로 연결한다."""

    _overview_cache_signature: tuple[int, int] | None = None
    _overview_cache_rows: list[dict] = []

    def __init__(
        self,
        database_path: Path | str | None = None,
        production_database_path: Path | str | None = None,
        bom_database_path: Path | str | None = None,
    ) -> None:
        self.database_path = Path(database_path) if database_path else Path(DB_PATH)
        self.production_database_path = (
            Path(production_database_path)
            if production_database_path
            else Path(PRODUCTION_DB_PATH)
        )
        self.bom_database_path = (
            Path(bom_database_path) if bom_database_path else Path(BOM_DB_PATH)
        )

    def signature(self) -> tuple[int, int] | None:
        signatures = []
        for path in (
            self.database_path,
            self.production_database_path,
            self.bom_database_path,
        ):
            try:
                stat = path.stat()
                signatures.append((stat.st_size, stat.st_mtime_ns))
            except OSError:
                continue
        if not signatures:
            return None
        return sum(item[0] for item in signatures), max(item[1] for item in signatures)

    def _bom_code_metadata(self, prefix: str) -> dict[str, dict[str, str]]:
        """BOM 제품명 마스터의 공정 기본코드 기준 품명·신규분류를 읽는다."""
        if not self.bom_database_path.is_file():
            return {}
        normalized_prefix = str(prefix or "").strip().upper()[:1]
        if normalized_prefix not in {"P", "Q", "R", "T"}:
            return {}
        connection = sqlite3.connect(
            f"file:{self.bom_database_path.as_posix()}?mode=ro", uri=True
        )
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT nm_cd,nm_nm,full_gu_nm FROM product_name_master "
                "WHERE UPPER(SUBSTR(TRIM(COALESCE(nm_cd,'')),1,1))=?",
                (normalized_prefix,),
            ).fetchall()
        except sqlite3.Error:
            return {}
        finally:
            connection.close()
        result: dict[str, dict[str, str]] = {}
        for row in rows:
            code = str(row["nm_cd"] or "").strip().upper()
            if not re.fullmatch(rf"{normalized_prefix}\d{{4}}", code):
                continue
            result[code] = {
                "품명": str(row["nm_nm"] or "").strip(),
                "신규분류요약": str(row["full_gu_nm"] or "").strip(),
            }
        return result

    def _bom_r_metadata(self) -> dict[str, dict[str, str]]:
        """BOM 제품명 마스터의 R0000 기준 품명·신규분류를 읽는다."""
        return self._bom_code_metadata("R")

    def _bom_q_to_p(self) -> dict[str, set[str]]:
        """BOM의 P(상위) → Q(하위) 관계를 Q 기준 P 후보로 뒤집는다."""
        if not self.bom_database_path.is_file():
            return {}
        connection = sqlite3.connect(
            f"file:{self.bom_database_path.as_posix()}?mode=ro", uri=True
        )
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT parent_cd,child_cd FROM bom_relation "
                "WHERE UPPER(TRIM(COALESCE(parent_cd,''))) LIKE 'P%' "
                "AND UPPER(TRIM(COALESCE(child_cd,''))) LIKE 'Q%'"
            ).fetchall()
        except sqlite3.Error:
            return {}
        finally:
            connection.close()
        result: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            p_base = self._p_base_code(row["parent_cd"])
            q_base = self._q_base_code(row["child_cd"])
            if p_base and q_base:
                result[q_base].add(p_base)
        return dict(result)

    @staticmethod
    def _r_base_code(r_code: object) -> str:
        match = R_BASE_CODE_PATTERN.match(str(r_code or "").strip().upper())
        return match.group(1).upper() if match else ""

    @staticmethod
    def _q_base_code(q_code: object) -> str:
        match = Q_BASE_CODE_PATTERN.match(str(q_code or "").strip().upper())
        return match.group(1).upper() if match else ""

    @staticmethod
    def _p_base_code(p_code: object) -> str:
        match = P_BASE_CODE_PATTERN.match(str(p_code or "").strip().upper())
        return match.group(1).upper() if match else ""

    def load_rows(
        self,
        process: str,
        *,
        allocation_mode: str = "whole",
        markets: set[str] | None = None,
    ) -> list[dict]:
        if not self.database_path.is_file():
            return []
        connection = sqlite3.connect(
            f"file:{self.database_path.as_posix()}?mode=ro", uri=True
        )
        connection.row_factory = sqlite3.Row
        try:
            if process == "사출":
                return self._load_injection_rows(connection)
            if process == "분리":
                return self._load_separation_rows(connection)
            if process == "하이드레이션":
                return self._load_hydration_rows(
                    connection,
                    allocation_mode=allocation_mode,
                    markets=markets,
                )
            if process == "접착":
                return self._load_same_code_rows(
                    connection,
                    inventory_stage=3,
                    process_code="55",
                )
            if process == "누수규격":
                return self._load_same_code_rows(
                    connection,
                    inventory_stage=4,
                    process_code="80",
                )
            return []
        finally:
            connection.close()

    def load_overview_rows(self) -> list[dict]:
        """실제 LOT가 존재하는 공정만 한 표의 공통 컬럼으로 합친다."""
        if not self.database_path.is_file():
            return []
        signature = self.signature()
        if (
            signature is not None
            and signature == self.__class__._overview_cache_signature
            and self.__class__._overview_cache_rows
        ):
            return [dict(row) for row in self.__class__._overview_cache_rows]
        connection = sqlite3.connect(
            f"file:{self.database_path.as_posix()}?mode=ro", uri=True
        )
        connection.row_factory = sqlite3.Row
        try:
            process_rows = {
                "사출": [
                    row for row in self._load_injection_rows(connection)
                    if row.get("구분") == "저장상태"
                ],
                "분리": [
                    row for row in self._load_separation_rows(connection)
                    if row.get("상태") == "배정"
                ],
                "하이드레이션": self._load_hydration_rows(
                    connection,
                    allocation_mode="whole",
                    markets={"전체"},
                ),
                "접착": self._load_same_code_rows(
                    connection,
                    inventory_stage=3,
                    process_code="55",
                ),
                "누수규격": self._load_same_code_rows(
                    connection,
                    inventory_stage=4,
                    process_code="80",
                ),
            }
        finally:
            connection.close()

        result: list[dict] = []
        seen: set[tuple[str, str, str, str]] = set()
        for process_name, rows in process_rows.items():
            for source in rows:
                item_code = str((
                    source.get("R코드")
                    if process_name in {"사출", "분리"}
                    else source.get("Q코드")
                    if process_name == "하이드레이션"
                    else source.get("P코드")
                ) or "").strip().upper()
                lot_no = str(source.get("체크시트(LOT)") or "").strip().upper()
                identity = (
                    process_name,
                    str(source.get("현재위치") or ""),
                    item_code,
                    lot_no,
                )
                if identity in seen:
                    continue
                seen.add(identity)
                row = dict(source)
                row["_공정"] = process_name
                row["품목코드"] = item_code
                row["체크시트"] = lot_no
                if process_name == "하이드레이션":
                    row["재고수량"] = _number(source.get("LOT수량"))
                result.append(row)

        process_rank = {
            "사출": 0,
            "분리": 1,
            "하이드레이션": 2,
            "접착": 3,
            "누수규격": 4,
        }
        result.sort(
            key=lambda row: (
                str(row.get("납기일") or "9999-12-31"),
                process_rank.get(str(row.get("_공정") or ""), 99),
                power_sort_key(row.get("_POWER_NUM", row.get("POWER"))),
                classification_sort_key(row.get("신규분류요약")),
                str(row.get("품목코드") or ""),
                _lot_sort_key(row.get("체크시트")),
            )
        )
        self.__class__._overview_cache_signature = signature
        self.__class__._overview_cache_rows = [dict(row) for row in result]
        return result

    @staticmethod
    def _aps_rows(connection: sqlite3.Connection, process_code: str) -> list[dict]:
        rows = connection.execute(
            "SELECT id,so_id,demand_item_id,demand_item_name,demand_group_id,"
            "item_id,item_name,item_name2,power,due_date,plan_date,seq,"
            "demand_type,dest_country,initial,"
            "COALESCE(plan_qty,0) plan_qty "
            "FROM aps_plan WHERE oper_id=? AND COALESCE(plan_qty,0)>0 "
            "AND TRIM(COALESCE(item_id,''))<>''",
            (process_code,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _load_injection_rows(self, connection: sqlite3.Connection) -> list[dict]:
        bom_r_metadata = self._bom_r_metadata()
        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in self._aps_rows(connection, "10"):
            grouped[str(row.get("item_id") or "").strip().upper()].append(row)
        metadata_grouped: dict[str, list[dict]] = defaultdict(list)
        for row in connection.execute(
            "SELECT id,so_id,demand_item_id,demand_item_name,demand_group_id,"
            "item_id,item_name,item_name2,power,due_date,plan_date,seq,"
            "demand_type,dest_country,COALESCE(plan_qty,0) plan_qty "
            "FROM aps_plan WHERE oper_id='10' "
            "AND TRIM(COALESCE(item_id,''))<>''"
        ).fetchall():
            item = dict(row)
            metadata_grouped[
                str(item.get("item_id") or "").strip().upper()
            ].append(item)
        q_rows = [
            dict(row)
            for row in connection.execute(
                "SELECT so_id,demand_item_id,power,item_id FROM aps_plan "
                "WHERE oper_id='20' AND TRIM(COALESCE(item_id,''))<>''"
            ).fetchall()
        ]
        q_to_r = self._r_q_map(connection, q_rows)
        q_by_r: dict[str, set[str]] = defaultdict(set)
        for q_code, r_codes in q_to_r.items():
            for mapped_r_code in r_codes:
                q_by_r[mapped_r_code].add(q_code)

        if not self.production_database_path.is_file():
            return []
        production = sqlite3.connect(
            f"file:{self.production_database_path.as_posix()}?mode=ro", uri=True
        )
        production.row_factory = sqlite3.Row
        try:
            active_rows = production.execute(
                "SELECT pr_no,pr_dt,gd_cd,gd_nm,full_gu,job_qty,pr_qty,w_power,"
                "stts_label,payload_json "
                "FROM production_performance WHERE gong_cd='10' "
                "AND UPPER(TRIM(COALESCE(stts,'')))='S'"
            ).fetchall()
        finally:
            production.close()

        result: list[dict] = []
        seen_lots: set[str] = set()
        saved_good_by_r: dict[str, float] = defaultdict(float)
        for source in active_rows:
            try:
                payload = json.loads(str(source["payload_json"] or "{}"))
            except (TypeError, ValueError):
                payload = {}
            lot_no = str(payload.get("check_sheet_no") or source["pr_no"] or "").strip().upper()
            if not lot_no or lot_no in seen_lots:
                continue
            seen_lots.add(lot_no)
            r_code = str(source["gd_cd"] or "").strip().upper()
            members = grouped.get(r_code, [])
            metadata = members or metadata_grouped.get(r_code, [])
            bom_metadata = bom_r_metadata.get(self._r_base_code(r_code), {})
            good_qty = _number(source["pr_qty"])
            saved_good_by_r[r_code] += good_qty
            category = str(bom_metadata.get("신규분류요약") or "").strip() or _compact([
                str(member.get("demand_group_id") or "").strip() for member in metadata
            ]) or "분류 확인 필요"
            power = (
                str(metadata[0].get("power") or "").strip()
                if metadata
                else str(source["w_power"] or "").strip()
            )
            specs = optical_specs_from_item_code(r_code, category, power)
            due_date = min(
                (str(member.get("due_date") or "9999-12-31")[:10] for member in members),
                default="",
            )
            result.append(
                {
                    "구분": "저장상태",
                    "상태": "배정",
                    "현재위치": "사출공정",
                    "체크시트(LOT)": lot_no,
                    "R코드": r_code,
                    "Q코드": _compact(sorted(q_by_r.get(r_code, set()))),
                    "품명": str(bom_metadata.get("품명") or "").strip() or str(
                        source["gd_nm"] or ""
                    ).strip() or _compact([
                        str(member.get("item_name") or member.get("item_name2") or "").strip()
                        for member in metadata
                    ]),
                    "신규분류요약": category,
                    **specs,
                    "재고수량": good_qty,
                    "계획일": str(source["pr_dt"] or "")[:10],
                    "납기일": due_date,
                    "필요조치": "저장상태 양품수량 임시 반영",
                    "_수주건수": len({str(member.get("so_id") or "") for member in members}),
                    "_진행현황": list(dict.fromkeys(
                        _channel(member.get("demand_type"), member.get("dest_country"))
                        for member in metadata
                    )),
                }
            )

        # 같은 R코드 안에서 납기가 빠른 부족분부터 저장상태 양품수량을 충당한다.
        # 실제 사출은 같은 R코드를 한 번에 진행하므로 남은 납기 묶음은 한 행으로 합친다.
        for r_code, members in grouped.items():
            bom_metadata = bom_r_metadata.get(self._r_base_code(r_code), {})
            category = str(bom_metadata.get("신규분류요약") or "").strip() or _compact([
                str(member.get("demand_group_id") or "").strip() for member in members
            ]) or "분류 확인 필요"
            power = str(members[0].get("power") or "").strip()
            specs = optical_specs_from_item_code(r_code, category, power)
            saved_remaining = saved_good_by_r.get(r_code, 0.0)
            ordered_members = sorted(
                members,
                key=lambda member: (
                    str(member.get("due_date") or "9999-12-31")[:10],
                    _number(member.get("seq")) if str(member.get("seq") or "").strip() else float("inf"),
                    str(member.get("id") or ""),
                ),
            )
            remaining_details: list[dict] = []
            remaining_members: list[dict] = []
            for member in ordered_members:
                line_required = _number(member.get("plan_qty"))
                covered = min(saved_remaining, line_required)
                saved_remaining = max(0.0, saved_remaining - covered)
                remaining = max(0.0, line_required - covered)
                if remaining > 0:
                    remaining_members.append(member)
                    remaining_details.append(
                        {
                            "납기일": str(member.get("due_date") or "")[:10],
                            "필요수량": remaining,
                            "진행현황": _channel(
                                member.get("demand_type"), member.get("dest_country")
                            ),
                            "수주번호": str(member.get("so_id") or ""),
                            "APS순번": member.get("seq"),
                        }
                    )
            if not remaining_details:
                continue
            due_totals: dict[str, float] = defaultdict(float)
            for detail in remaining_details:
                due_totals[str(detail["납기일"])] += float(detail["필요수량"])
            result.append(
                {
                    "구분": "추가사출",
                    "상태": "배정",
                    "현재위치": "사출 필요",
                    "체크시트(LOT)": "미발행",
                    "R코드": r_code,
                    "Q코드": _compact(sorted(q_by_r.get(r_code, set()))),
                    "품명": str(bom_metadata.get("품명") or "").strip() or _compact([
                        str(member.get("item_name") or member.get("item_name2") or "").strip()
                        for member in remaining_members
                    ]),
                    "신규분류요약": category,
                    **specs,
                    "재고수량": sum(float(detail["필요수량"]) for detail in remaining_details),
                    "계획일": "",
                    # 실제 작업 우선순위는 아직 충당되지 않은 가장 빠른 납기다.
                    "납기일": str(remaining_details[0]["납기일"]),
                    "필요조치": "체크시트 발행 후 동일 R코드 일괄 사출",
                    "_수주건수": len({
                        str(member.get("so_id") or "") for member in remaining_members
                    }),
                    "_진행현황": list(dict.fromkeys(
                        str(detail["진행현황"]) for detail in remaining_details
                    )),
                    "_납기별필요": [
                        {"납기일": due_date, "필요수량": due_totals[due_date]}
                        for due_date in sorted(due_totals, key=lambda value: value or "9999-12-31")
                    ],
                    "_추가사출상세": remaining_details,
                }
            )

        result.sort(key=_work_sort_key)
        for index, row in enumerate(result, start=1):
            row["작업순서"] = index
        return result

    @staticmethod
    def _need_priority(row: dict) -> tuple:
        """실시간 부족수량과 같은 납기·관별·APS 순번 우선순위를 사용한다."""
        due_date = str(row.get("due_date") or "9999-12-31")[:10]
        initial = str(row.get("initial") or "").strip()
        demand_type = str(row.get("demand_type") or "").strip()
        destination = str(row.get("dest_country") or "").strip()
        if initial:
            channel_rank = 0
        elif demand_type.upper() == "PB":
            channel_rank = 1
        elif "안전" in demand_type:
            channel_rank = 4
        elif not destination:
            channel_rank = 2
        else:
            channel_rank = 3
        try:
            sequence = int(row.get("seq") or 0)
        except (TypeError, ValueError):
            sequence = 0
        return (
            due_date,
            channel_rank,
            sequence,
            str(row.get("so_id") or ""),
            int(row.get("id") or 0),
        )

    @staticmethod
    def _hydration_split_quantities(
        lot_qty: float,
        first_need: float,
        second_need: float,
    ) -> tuple[float, float] | None:
        """최소 200개씩 확보하면서 첫 몫을 100개 단위로 끊고 잔량은 둘째에 준다."""
        if lot_qty < MIN_REWORK_QTY * 2 or first_need <= 0 or second_need <= 0:
            return None
        ratio = first_need / (first_need + second_need)
        first_qty = floor((lot_qty * ratio) / HYDRATION_SPLIT_UNIT) * HYDRATION_SPLIT_UNIT
        maximum_first = floor(
            (lot_qty - MIN_REWORK_QTY) / HYDRATION_SPLIT_UNIT
        ) * HYDRATION_SPLIT_UNIT
        first_qty = min(max(first_qty, MIN_REWORK_QTY), maximum_first)
        second_qty = lot_qty - first_qty
        if first_qty < MIN_REWORK_QTY or second_qty < MIN_REWORK_QTY:
            return None
        return first_qty, second_qty

    def _load_hydration_rows(
        self,
        connection: sqlite3.Connection,
        *,
        allocation_mode: str,
        markets: set[str] | None,
    ) -> list[dict]:
        """분리창고 Q LOT를 BOM 및 APS 연결관계로 하이드레이션 P코드에 배정한다."""
        selected_markets = set(markets or {"전체"})
        p_rows = self._aps_rows(connection, "45")
        if "전체" not in selected_markets:
            p_rows = [
                row for row in p_rows
                if _channel(row.get("demand_type"), row.get("dest_country"))
                in selected_markets
            ]
        if not p_rows:
            return []

        p_groups: dict[str, list[dict]] = defaultdict(list)
        p_rows_by_key: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        for row in p_rows:
            p_code = str(row.get("item_id") or "").strip().upper()
            p_groups[p_code].append(row)
            p_rows_by_key[
                (
                    str(row.get("so_id") or ""),
                    str(row.get("demand_item_id") or ""),
                    str(row.get("power") or ""),
                )
            ].append(row)

        bom_q_to_p = self._bom_q_to_p()
        q_to_p_codes: dict[str, set[str]] = defaultdict(set)
        q_rows = connection.execute(
            "SELECT so_id,demand_item_id,power,item_id FROM aps_plan "
            "WHERE oper_id='20' AND TRIM(COALESCE(item_id,''))<>''"
        ).fetchall()
        for source in q_rows:
            q_code = str(source["item_id"] or "").strip().upper()
            allowed_p_bases = bom_q_to_p.get(self._q_base_code(q_code), set())
            key = (
                str(source["so_id"] or ""),
                str(source["demand_item_id"] or ""),
                str(source["power"] or ""),
            )
            for p_row in p_rows_by_key.get(key, []):
                p_code = str(p_row.get("item_id") or "").strip().upper()
                if self._p_base_code(p_code) in allowed_p_bases:
                    q_to_p_codes[q_code].add(p_code)

        p_metadata = self._bom_code_metadata("P")
        needs: dict[str, dict] = {}
        for p_code, members in p_groups.items():
            ordered_members = sorted(members, key=self._need_priority)
            raw_required = sum(_number(member.get("plan_qty")) for member in members)
            target_required = max(raw_required, MIN_REWORK_QTY)
            bom_metadata = p_metadata.get(self._p_base_code(p_code), {})
            category = str(bom_metadata.get("신규분류요약") or "").strip() or _compact([
                str(member.get("demand_group_id") or "").strip()
                for member in ordered_members
            ]) or "분류 확인 필요"
            power = str(ordered_members[0].get("power") or "").strip()
            due_totals: dict[str, float] = defaultdict(float)
            for member in ordered_members:
                due_totals[str(member.get("due_date") or "")[:10]] += _number(
                    member.get("plan_qty")
                )
            needs[p_code] = {
                "p_code": p_code,
                "members": ordered_members,
                "raw_required": raw_required,
                "target_required": target_required,
                "remaining": target_required,
                "priority": self._need_priority(ordered_members[0]),
                "due_date": str(ordered_members[0].get("due_date") or "")[:10],
                "category": category,
                "power": power,
                "name": _compact([
                    str(member.get("item_name") or member.get("item_name2") or "").strip()
                    for member in ordered_members
                ]) or str(bom_metadata.get("품명") or "").strip(),
                "markets": list(dict.fromkeys(
                    _channel(member.get("demand_type"), member.get("dest_country"))
                    for member in ordered_members
                )),
                "due_totals": due_totals,
            }

        lots = [
            dict(row)
            for row in connection.execute(
                "SELECT warehouse_code,MAX(warehouse_name) warehouse_name,lot_full,item_id,"
                "SUM(COALESCE(stock_qty,0)) stock_qty "
                "FROM current_inventory WHERE stage=2 AND COALESCE(stock_qty,0)>0 "
                "GROUP BY warehouse_code,lot_full,item_id"
            ).fetchall()
        ]

        def eligible_needs(lot: dict) -> list[dict]:
            q_code = str(lot.get("item_id") or "").strip().upper()
            return sorted(
                [
                    needs[p_code] for p_code in q_to_p_codes.get(q_code, set())
                    if p_code in needs and float(needs[p_code]["remaining"]) > 0
                ],
                key=lambda need: (*need["priority"], need["p_code"]),
            )

        lots.sort(
            key=lambda lot: (
                (
                    (*eligible_needs(lot)[0]["priority"],)
                    if eligible_needs(lot)
                    else ("9999-12-31", 99, 999999, "", 0)
                ),
                len(q_to_p_codes.get(str(lot.get("item_id") or "").strip().upper(), set())),
                _lot_sort_key(lot.get("lot_full")),
            )
        )

        result: list[dict] = []

        def append_assignment(
            lot: dict,
            need: dict,
            allocated_qty: float,
            split_label: str,
        ) -> None:
            remaining_before = float(need["remaining"])
            need["remaining"] = max(0.0, remaining_before - allocated_qty)
            p_code = str(need["p_code"])
            q_code = str(lot.get("item_id") or "").strip().upper()
            specs = optical_specs_from_item_code(
                p_code, need["category"], need["power"]
            )
            result.append(
                {
                    "상태": "배정",
                    "배정": split_label,
                    "신규분류요약": need["category"],
                    "현재위치": str(lot.get("warehouse_name") or "분리창고"),
                    "체크시트(LOT)": str(lot.get("lot_full") or ""),
                    "Q코드": str(lot.get("item_id") or "").strip().upper(),
                    "P코드": p_code,
                    "품명": need["name"],
                    **specs,
                    "납기일": need["due_date"],
                    "LOT수량": _number(lot.get("stock_qty")),
                    "필요수량": remaining_before,
                    "배정수량": allocated_qty,
                    "초과배정": max(0.0, allocated_qty - remaining_before),
                    "재고수량": allocated_qty,
                    "필요조치": (
                        f"{q_code} LOT를 {p_code} 한 품목으로 전량 투입"
                        if split_label == "통째"
                        else f"동일 LOT {split_label} · {p_code} 투입"
                    ),
                    "_수주건수": len({
                        str(member.get("so_id") or "") for member in need["members"]
                    }),
                    "_진행현황": need["markets"],
                    "_납기별필요": [
                        {"납기일": due_date, "필요수량": quantity}
                        for due_date, quantity in sorted(
                            need["due_totals"].items(),
                            key=lambda item: item[0] or "9999-12-31",
                        )
                    ],
                    "_원필요수량": need["raw_required"],
                    "_기준필요수량": need["target_required"],
                }
            )

        deferred_split_lots: list[dict] = []
        for lot in lots:
            candidates = eligible_needs(lot)
            if not candidates:
                continue
            lot_qty = _number(lot.get("stock_qty"))
            # 어느 P코드든 현재 부족수량이 LOT 전량 이상이면 쪼갤 이유가 없다.
            # 납기 우선순위를 유지한 채 전량을 흡수할 수 있는 첫 P코드에 통째 배정한다.
            full_lot_candidates = [
                need for need in candidates
                if float(need["remaining"]) >= lot_qty
            ]
            if full_lot_candidates:
                append_assignment(lot, full_lot_candidates[0], lot_qty, "통째")
                continue
            if allocation_mode == "split2":
                # 통째로 흡수할 곳이 없는 LOT는 일단 보류한다. 모든 통째 배정을
                # 끝낸 뒤 남은 부족 조각에 한해서만 최대 2개 P코드로 나눈다.
                deferred_split_lots.append(lot)
                continue
            append_assignment(lot, candidates[0], lot_qty, "통째")

        for lot in deferred_split_lots:
            candidates = eligible_needs(lot)
            if not candidates:
                continue
            lot_qty = _number(lot.get("stock_qty"))
            if len(candidates) >= 2:
                split = self._hydration_split_quantities(
                    lot_qty,
                    float(candidates[0]["remaining"]),
                    float(candidates[1]["remaining"]),
                )
                if split is not None:
                    append_assignment(lot, candidates[0], split[0], "최종 보류 1/2")
                    append_assignment(lot, candidates[1], split[1], "최종 보류 2/2")
                    continue
            append_assignment(lot, candidates[0], lot_qty, "통째")

        result.sort(
            key=lambda row: (
                0 if row.get("배정") == "통째" else 1,
                str(row.get("납기일") or "9999-12-31"),
                power_sort_key(row.get("_POWER_NUM", row.get("POWER"))),
                str(row.get("P코드") or ""),
                _lot_sort_key(row.get("체크시트(LOT)")),
                str(row.get("배정") or ""),
            )
        )
        for index, row in enumerate(result, start=1):
            row["작업순서"] = index
        return result

    def _load_same_code_rows(
        self,
        connection: sqlite3.Connection,
        *,
        inventory_stage: int,
        process_code: str,
    ) -> list[dict]:
        """P코드가 유지되는 후공정의 실제 창고 LOT를 전량 작업순서로 만든다."""
        source_rows = self._aps_rows(connection, process_code)
        grouped: dict[str, list[dict]] = defaultdict(list)
        for row in source_rows:
            grouped[str(row.get("item_id") or "").strip().upper()].append(row)
        inventory: dict[str, list[dict]] = defaultdict(list)
        for source in connection.execute(
            "SELECT warehouse_code,MAX(warehouse_name) warehouse_name,lot_full,item_id,"
            "SUM(COALESCE(stock_qty,0)) stock_qty "
            "FROM current_inventory WHERE stage=? AND COALESCE(stock_qty,0)>0 "
            "GROUP BY warehouse_code,lot_full,item_id",
            (inventory_stage,),
        ).fetchall():
            lot = dict(source)
            inventory[str(lot.get("item_id") or "").strip().upper()].append(lot)
        for lots_for_code in inventory.values():
            lots_for_code.sort(key=lambda lot: _lot_sort_key(lot.get("lot_full")))

        p_metadata = self._bom_code_metadata("P")
        result: list[dict] = []
        for p_code, members in grouped.items():
            ordered_members = sorted(members, key=self._need_priority)
            remaining = sum(_number(member.get("plan_qty")) for member in members)
            if remaining <= 0:
                continue
            bom_metadata = p_metadata.get(self._p_base_code(p_code), {})
            category = str(bom_metadata.get("신규분류요약") or "").strip() or _compact([
                str(member.get("demand_group_id") or "").strip()
                for member in ordered_members
            ]) or "분류 확인 필요"
            power = str(ordered_members[0].get("power") or "").strip()
            specs = optical_specs_from_item_code(p_code, category, power)
            for lot in inventory.get(p_code, []):
                if remaining <= 0:
                    break
                lot_qty = _number(lot.get("stock_qty"))
                result.append(
                    {
                        "상태": "배정",
                        "신규분류요약": category,
                        "현재위치": str(lot.get("warehouse_name") or ""),
                        "P코드": p_code,
                        "체크시트(LOT)": str(lot.get("lot_full") or ""),
                        "품명": _compact([
                            str(member.get("item_name") or member.get("item_name2") or "").strip()
                            for member in ordered_members
                        ]) or str(bom_metadata.get("품명") or "").strip(),
                        **specs,
                        "납기일": str(ordered_members[0].get("due_date") or "")[:10],
                        "재고수량": lot_qty,
                        "필요조치": "체크시트 LOT 전량 작업",
                        "_수주건수": len({
                            str(member.get("so_id") or "") for member in ordered_members
                        }),
                        "_진행현황": list(dict.fromkeys(
                            _channel(member.get("demand_type"), member.get("dest_country"))
                            for member in ordered_members
                        )),
                    }
                )
                remaining = max(0.0, remaining - lot_qty)

        result.sort(key=_work_sort_key)
        for index, row in enumerate(result, start=1):
            row["작업순서"] = index
        return result

    @staticmethod
    def _r_q_map(connection: sqlite3.Connection, q_rows: list[dict]) -> dict[str, set[str]]:
        r_rows = [
            dict(row)
            for row in connection.execute(
                "SELECT so_id,demand_item_id,power,item_id FROM aps_plan "
                "WHERE oper_id='10' AND TRIM(COALESCE(item_id,''))<>''"
            ).fetchall()
        ]
        exact: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        broad: dict[tuple[str, str], set[str]] = defaultdict(set)
        known_r_codes: set[str] = set()
        for row in r_rows:
            r_code = str(row.get("item_id") or "").strip().upper()
            if not r_code:
                continue
            exact[
                (
                    str(row.get("so_id") or ""),
                    str(row.get("demand_item_id") or ""),
                    str(row.get("power") or ""),
                )
            ].add(r_code)
            broad[
                (str(row.get("demand_item_id") or ""), str(row.get("power") or ""))
            ].add(r_code)
            known_r_codes.add(r_code)
        known_r_codes.update(
            str(row[0] or "").strip().upper()
            for row in connection.execute(
                "SELECT DISTINCT item_id FROM current_inventory "
                "WHERE stage=1 AND COALESCE(stock_qty,0)>0"
            )
            if row[0]
        )

        mapping: dict[str, set[str]] = defaultdict(set)
        for row in q_rows:
            q_code = str(row.get("item_id") or "").strip().upper()
            key = (
                str(row.get("so_id") or ""),
                str(row.get("demand_item_id") or ""),
                str(row.get("power") or ""),
            )
            candidates = exact.get(key) or broad.get((key[1], key[2])) or set()
            mapping[q_code].update(candidates)
            if not mapping[q_code] and q_code.startswith("Q"):
                inferred = "R" + q_code[1:]
                if inferred in known_r_codes:
                    mapping[q_code].add(inferred)
        return mapping

    def _load_separation_rows(self, connection: sqlite3.Connection) -> list[dict]:
        q_source = self._aps_rows(connection, "20")
        q_all = [
            dict(row)
            for row in connection.execute(
                "SELECT id,so_id,demand_item_id,demand_item_name,demand_group_id,"
                "item_id,item_name,item_name2,power,due_date,plan_date,seq,"
                "demand_type,dest_country,"
                "COALESCE(plan_qty,0) plan_qty FROM aps_plan WHERE oper_id='20' "
                "AND TRIM(COALESCE(item_id,''))<>''"
            ).fetchall()
        ]
        q_groups: dict[str, list[dict]] = defaultdict(list)
        for row in q_source:
            q_groups[str(row.get("item_id") or "").strip().upper()].append(row)
        q_metadata: dict[str, list[dict]] = defaultdict(list)
        for row in q_all:
            q_metadata[str(row.get("item_id") or "").strip().upper()].append(row)
        mapping = self._r_q_map(connection, q_all)

        inventory: dict[str, list[dict]] = defaultdict(list)
        for row in connection.execute(
            "SELECT warehouse_code,MAX(warehouse_name) warehouse_name,lot_full,item_id,"
            "SUM(COALESCE(stock_qty,0)) stock_qty "
            "FROM current_inventory WHERE stage=1 AND COALESCE(stock_qty,0)>0 "
            "GROUP BY warehouse_code,lot_full,item_id"
        ).fetchall():
            item = dict(row)
            inventory[str(item.get("item_id") or "").strip().upper()].append(item)
        for lots in inventory.values():
            lots.sort(key=lambda row: _lot_sort_key(row.get("lot_full")))

        inverse: dict[str, set[str]] = defaultdict(set)
        for q_code, r_codes in mapping.items():
            for r_code in r_codes:
                inverse[r_code].add(q_code)

        result: list[dict] = []
        used_lots: set[tuple[str, str, str]] = set()
        ordered_needs = sorted(
            q_groups.items(),
            key=lambda item: _work_sort_key(
                {
                    "납기일": min(
                        str(row.get("due_date") or "9999-12-31")[:10]
                        for row in item[1]
                    ),
                    "신규분류요약": str(item[1][0].get("demand_group_id") or ""),
                    "POWER": str(item[1][0].get("power") or ""),
                    "Q코드": item[0],
                }
            ),
        )
        for q_code, members in ordered_needs:
            category = _compact(
                [str(member.get("demand_group_id") or "").strip() for member in members]
            )
            power = str(members[0].get("power") or "").strip()
            specs = optical_specs_from_item_code(q_code, category, power)
            due_date = min(
                (str(member.get("due_date") or "9999-12-31")[:10] for member in members),
                default="",
            )
            required = sum(_number(member.get("plan_qty")) for member in members)
            remaining = required
            r_codes = sorted(mapping.get(q_code, set()))
            ambiguous = len(r_codes) != 1 or (
                len(r_codes) == 1 and len(inverse.get(r_codes[0], set())) > 1
            )
            r_code = r_codes[0] if len(r_codes) == 1 else ""

            if not ambiguous:
                for lot in inventory.get(r_code, []):
                    lot_key = (
                        str(lot.get("warehouse_code") or ""),
                        str(lot.get("lot_full") or ""),
                        r_code,
                    )
                    if lot_key in used_lots or remaining <= 0:
                        continue
                    used_lots.add(lot_key)
                    lot_qty = _number(lot.get("stock_qty"))
                    covered = min(remaining, lot_qty)
                    result.append(
                        {
                            "상태": "배정",
                            "현재위치": str(lot.get("warehouse_name") or "사출창고"),
                            "체크시트(LOT)": str(lot.get("lot_full") or ""),
                            "R코드": r_code,
                            "Q코드": q_code,
                            "품명": _compact(
                                [
                                    str(member.get("item_name") or member.get("item_name2") or "").strip()
                                    for member in members
                                ]
                            ),
                            "신규분류요약": category,
                            **specs,
                            "재고수량": lot_qty,
                            "납기일": due_date,
                            "필요조치": "체크시트 LOT 전량 분리",
                            "_수주건수": len({str(member.get("so_id") or "") for member in members}),
                            "_진행현황": list(dict.fromkeys(
                                _channel(member.get("demand_type"), member.get("dest_country"))
                                for member in members
                            )),
                        }
                    )
                    remaining = max(0.0, remaining - covered)

        # 작업 필요량과 관계없이 실제 사출창고 LOT는 한 번씩만 표시한다.
        # 아직 APS 부족에 연결되지 않은 LOT도 '미배정' 필터에서 확인할 수 있다.
        for r_code, lots in inventory.items():
            q_candidates = sorted(inverse.get(r_code, set()))
            q_code = q_candidates[0] if len(q_candidates) == 1 else ""
            members = q_metadata.get(q_code, []) if q_code else []
            category = _compact([
                str(member.get("demand_group_id") or "").strip() for member in members
            ])
            power = str(members[0].get("power") or "").strip() if members else ""
            specs = optical_specs_from_item_code(q_code or r_code, category, power)
            for lot in lots:
                lot_key = (
                    str(lot.get("warehouse_code") or ""),
                    str(lot.get("lot_full") or ""),
                    r_code,
                )
                if lot_key in used_lots:
                    continue
                lot_qty = _number(lot.get("stock_qty"))
                result.append(
                    {
                        "상태": "미배정",
                        "현재위치": str(lot.get("warehouse_name") or "사출창고"),
                        "체크시트(LOT)": str(lot.get("lot_full") or ""),
                        "R코드": r_code,
                        "Q코드": q_code or (_compact(q_candidates) if q_candidates else "확인 필요"),
                        "품명": _compact(
                            [
                                str(member.get("item_name") or member.get("item_name2") or "").strip()
                                for member in members
                            ]
                        ),
                        "신규분류요약": category,
                        **specs,
                        "재고수량": lot_qty,
                        "납기일": "",
                        "필요조치": (
                            "현재 APS 배정 없음"
                            if len(q_candidates) == 1
                            else "R→Q 연계코드 확인 필요"
                        ),
                        "_수주건수": 0,
                        "_진행현황": list(dict.fromkeys(
                            _channel(member.get("demand_type"), member.get("dest_country"))
                            for member in members
                        )),
                    }
                )

        result.sort(
            key=lambda row: (
                0 if row.get("상태") == "배정" else 1,
                str(row.get("납기일") or "9999-12-31"),
                classification_sort_key(row.get("신규분류요약")),
                power_sort_key(row.get("_POWER_NUM", row.get("POWER"))),
                str(row.get("Q코드") or ""),
                _lot_sort_key(row.get("체크시트(LOT)")),
            )
        )
        for index, row in enumerate(result, start=1):
            row["작업순서"] = index
        return result
