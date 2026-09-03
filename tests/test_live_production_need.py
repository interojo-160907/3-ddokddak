from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from collectors.live_production_need_collector import (
    INVENTORY_COLUMNS,
    PRODUCTION_COLUMNS,
    WIP_COLUMNS,
    _ensure_live_schema,
    _collect_production,
    _replace_rows,
    _initialize_cycle_database,
    allocate_evidence,
    allocation_priority,
    calculate_completion_evidence,
    lot_identity,
)


def _inventory(
    key: str,
    lot: str,
    stage: int,
    item: str,
    *,
    lm: float = 0,
    inbound: float = 0,
    outbound: float = 0,
    stock: float = 0,
) -> dict:
    return {
        "snapshot_key": key,
        "warehouse_code": f"W{stage}",
        "warehouse_name": f"창고{stage}",
        "stage": stage,
        "lot_full": lot,
        "lot_base": lot_identity(lot)[1],
        "derived": int(lot_identity(lot)[2]),
        "sub_lot": "",
        "item_id": item,
        "lm_qty": lm,
        "ip_qty": inbound,
        "chul_qty": outbound,
        "stock_qty": stock,
        "payload_json": "{}",
    }


def _production(key: str, lot: str, stage: int, item: str, quantity: float) -> dict:
    process = {1: "10", 2: "20", 3: "45", 4: "55", 5: "80"}[stage]
    return {
        "row_key": key,
        "pr_no": key,
        "production_date": "2026-09-02",
        "process_code": process,
        "stage": stage,
        "lot_full": lot,
        "lot_base": lot_identity(lot)[1],
        "derived": int(lot_identity(lot)[2]),
        "item_id": item,
        "quantity": quantity,
        "status": "C",
        "payload_json": "{}",
    }


class LiveNeedCalculationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute(
            """
            CREATE TABLE aps_plan (
                id INTEGER PRIMARY KEY, oper_id TEXT, item_id TEXT, plan_qty REAL,
                original_plan_qty REAL, allocated_qty REAL DEFAULT 0,
                so_id TEXT, due_date TEXT, initial TEXT, demand_type TEXT,
                dest_country TEXT, seq INTEGER
            )
            """
        )
        _ensure_live_schema(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_full_lot_is_unique_and_base_is_lineage_only(self) -> None:
        self.assertEqual(
            lot_identity(" c20260722-305-c3 "),
            ("C20260722-305-C3", "C20260722-305", True),
        )
        self.assertEqual(
            lot_identity("S20260902-369"),
            ("S20260902-369", "S20260902-369", False),
        )

    def test_same_process_inventory_and_production_count_once(self) -> None:
        current_inventory = [_inventory("i1", "S20260902-369", 2, "Q1113-01.50", inbound=500, stock=500)]
        current_production = [_production("p1", "S20260902-369", 2, "Q1113-01.50", 480)]
        _replace_rows(self.connection, "current_inventory", INVENTORY_COLUMNS, current_inventory)
        _replace_rows(self.connection, "current_production", PRODUCTION_COLUMNS, current_production)
        evidence = calculate_completion_evidence(self.connection)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["recognized_qty"], 500)

    def test_outbound_does_not_erase_completed_quantity(self) -> None:
        current_inventory = [
            _inventory("i1", "S20260902-369", 2, "Q1113-01.50", inbound=500, outbound=500, stock=0)
        ]
        _replace_rows(self.connection, "current_inventory", INVENTORY_COLUMNS, current_inventory)
        evidence = calculate_completion_evidence(self.connection)
        self.assertEqual(evidence[0]["recognized_qty"], 500)
        self.assertIn("출고 후에도 완료 유지", evidence[0]["reason"])

    def test_each_traversed_process_uses_its_own_completed_quantity(self) -> None:
        wip = [{
            "warehouse_code": "G007", "warehouse_name": "검사접착창고", "stage": 3,
            "lot_full": "S20260901-003", "lot_base": "S20260901-003", "derived": 0,
            "item_id": "P1190A-01.50", "quantity": 2479, "payload_json": "{}",
        }]
        stage4 = _inventory(
            "i4", "S20260901-003", 4, "P1190A-01.50",
            inbound=2280, outbound=2280, stock=0,
        )
        stage5 = _inventory(
            "i5", "S20260901-003", 5, "P1190A-01.50",
            inbound=2275, stock=2275,
        )
        _replace_rows(self.connection, "baseline_wip", WIP_COLUMNS, wip)
        # 앱이 늦게 켜져 동일 자료가 기준선에 들어가도 APS WIP 이후 공정은
        # 현재 절대수량으로 공정별 인정되어야 한다.
        _replace_rows(self.connection, "baseline_inventory", INVENTORY_COLUMNS, [stage4])
        _replace_rows(self.connection, "current_inventory", INVENTORY_COLUMNS, [stage4, stage5])
        _replace_rows(
            self.connection,
            "baseline_production",
            PRODUCTION_COLUMNS,
            [_production("p4", "S20260901-003", 4, "P1190A-01.50", 2280)],
        )
        _replace_rows(
            self.connection,
            "current_production",
            PRODUCTION_COLUMNS,
            [
                _production("p4", "S20260901-003", 4, "P1190A-01.50", 2280),
                _production("p5", "S20260901-003", 5, "P1190A-01.50", 2275),
            ],
        )
        evidence = calculate_completion_evidence(self.connection)
        by_process = {row["process_code"]: row["recognized_qty"] for row in evidence}
        self.assertEqual(by_process, {"55": 2280, "80": 2275})

    def test_derived_children_share_parent_wip_cap(self) -> None:
        wip = [{
            "warehouse_code": "Q001", "warehouse_name": "사출창고", "stage": 1,
            "lot_full": "C20260722-305", "lot_base": "C20260722-305", "derived": 0,
            "item_id": "R1000", "quantity": 100, "payload_json": "{}",
        }]
        current_inventory = [
            _inventory("i1", "C20260722-305-C1", 2, "Q1000", inbound=70, stock=70),
            _inventory("i2", "C20260722-305-C3", 2, "Q1000", inbound=70, stock=70),
            _inventory("i3", "C20260722-305-C4", 1, "R1000", inbound=50, stock=50),
        ]
        _replace_rows(self.connection, "baseline_wip", WIP_COLUMNS, wip)
        _replace_rows(self.connection, "current_inventory", INVENTORY_COLUMNS, current_inventory)
        evidence = calculate_completion_evidence(self.connection)
        self.assertEqual(sum(row["recognized_qty"] for row in evidence), 100)
        self.assertTrue(all(row["stage"] == 2 for row in evidence))

    def test_external_g007_lot_is_accepted_without_injection_wip(self) -> None:
        current_inventory = [
            _inventory("i1", "A20260722-305-C3", 3, "P1000-01.00", inbound=250, stock=250)
        ]
        _replace_rows(self.connection, "current_inventory", INVENTORY_COLUMNS, current_inventory)
        evidence = calculate_completion_evidence(self.connection)
        self.assertEqual(evidence[0]["process_code"], "45")
        self.assertEqual(evidence[0]["recognized_qty"], 250)

    def test_no_wip_existing_inventory_is_not_recounted(self) -> None:
        existing = _inventory(
            "i1", "A20240722-305-C3", 3, "P1000-01.00",
            inbound=250, stock=250,
        )
        _replace_rows(self.connection, "baseline_inventory", INVENTORY_COLUMNS, [existing])
        _replace_rows(self.connection, "current_inventory", INVENTORY_COLUMNS, [existing])
        self.assertEqual(calculate_completion_evidence(self.connection), [])

    def test_new_cycle_first_collection_is_current_evidence_not_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live_db = root / "live.sqlite"

            def create_aps_copy(_source: Path, destination: Path) -> None:
                connection = sqlite3.connect(destination)
                try:
                    connection.execute(
                        """
                        CREATE TABLE aps_plan (
                            id INTEGER PRIMARY KEY, oper_id TEXT, item_id TEXT, plan_qty REAL,
                            original_plan_qty REAL, allocated_qty REAL DEFAULT 0,
                            so_id TEXT, due_date TEXT, initial TEXT, demand_type TEXT,
                            dest_country TEXT, seq INTEGER
                        )
                        """
                    )
                    connection.execute(
                        "INSERT INTO aps_plan VALUES (1,'45','P1000',500,NULL,0,"
                        "'R1','2026-09-03','','국내','',1)"
                    )
                    connection.commit()
                finally:
                    connection.close()

            current_inventory = [
                _inventory(
                    "i1", "A20260903-001-C1", 3, "P1000",
                    inbound=250, stock=250,
                )
            ]
            current_production = [
                _production("p1", "A20260903-001-C1", 3, "P1000", 240)
            ]
            with patch(
                "collectors.live_production_need_collector._sqlite_copy",
                side_effect=create_aps_copy,
            ):
                evidence, allocated, completed = _initialize_cycle_database(
                    live_db,
                    "2026-09-03 08:03:42",
                    "2026-09-03 08:17:27",
                    "2026-09-02",
                    "2026-09-03T08:18:00+09:00",
                    [],
                    current_inventory,
                    current_production,
                    1,
                    1,
                )

            connection = sqlite3.connect(live_db)
            try:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM baseline_inventory").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM baseline_production").fetchone()[0],
                    0,
                )
                self.assertEqual(
                    connection.execute("SELECT plan_qty FROM aps_plan WHERE id=1").fetchone()[0],
                    250,
                )
            finally:
                connection.close()
            self.assertEqual(evidence[0]["recognized_qty"], 250)
            self.assertEqual(allocated, 250)
            self.assertEqual(completed, 0)

    def test_production_collection_requests_s_factory_and_rechecks_completion(self) -> None:
        captured: dict = {}

        def fake_request(endpoint, params, api_key, timeout):
            captured.update(params)
            return {
                "total_count": 4,
                "rows": [
                    {
                        "pr_no": "P1", "check_sheet_no": "S20260903-001",
                        "gong_cd": "10", "gd_cd": "R1000", "fac_cd": "04",
                        "stts": "C", "pr_qty": 100, "pr_dt": "2026-09-03",
                    },
                    {
                        "pr_no": "P2", "check_sheet_no": "S20260903-002",
                        "gong_cd": "10", "gd_cd": "R1000", "fac_cd": "04",
                        "stts": "S", "pr_qty": 90, "pr_dt": "2026-09-03",
                    },
                    {
                        "pr_no": "P3", "check_sheet_no": "S20260903-003",
                        "gong_cd": "00", "gd_cd": "R1000", "fac_cd": "04",
                        "stts": "C", "pr_qty": 80, "pr_dt": "2026-09-03",
                    },
                    {
                        "pr_no": "P4", "check_sheet_no": "A20260903-004",
                        "gong_cd": "10", "gd_cd": "R1000", "fac_cd": "01",
                        "stts": "C", "pr_qty": 70, "pr_dt": "2026-09-03",
                    },
                ],
            }

        with patch("collectors.live_production_need_collector._request", side_effect=fake_request):
            rows, source_count = _collect_production(
                {"R1000"}, "2026-09-02", "2026-09-03", "", 10
            )

        self.assertEqual(captured["fac_cd"], "04")
        self.assertEqual(captured["stts"], "C")
        self.assertEqual(source_count, 4)
        self.assertEqual([row["row_key"] for row in rows], ["P1"])

    def test_due_date_is_absolute_then_same_due_channel_priority(self) -> None:
        rows = [
            {"id": 1, "due_date": "2026-09-01", "initial": "", "demand_type": "안전", "dest_country": "", "seq": 1, "so_id": "EARLY"},
            {"id": 2, "due_date": "2026-09-02", "initial": "ABC", "demand_type": "", "dest_country": "US", "seq": 1, "so_id": "INITIAL"},
            {"id": 3, "due_date": "2026-09-02", "initial": "", "demand_type": "PB", "dest_country": "", "seq": 1, "so_id": "PB"},
            {"id": 4, "due_date": "2026-09-02", "initial": "", "demand_type": "국내", "dest_country": "", "seq": 1, "so_id": "DOMESTIC"},
            {"id": 5, "due_date": "2026-09-02", "initial": "", "demand_type": "해외", "dest_country": "US", "seq": 1, "so_id": "EXPORT"},
            {"id": 6, "due_date": "2026-09-02", "initial": "", "demand_type": "안전", "dest_country": "", "seq": 1, "so_id": "SAFETY"},
        ]
        self.assertEqual(
            [row["so_id"] for row in sorted(rows, key=allocation_priority)],
            ["EARLY", "INITIAL", "PB", "DOMESTIC", "EXPORT", "SAFETY"],
        )

    def test_allocation_removes_completed_rows_in_priority_order(self) -> None:
        plans = [
            (1, "45", "P1000", 100, 100, 0, "R2", "2026-09-02", "INIT", "해외", "US", 1),
            (2, "45", "P1000", 100, 100, 0, "R1", "2026-09-01", "", "안전", "", 1),
        ]
        self.connection.executemany(
            "INSERT INTO aps_plan VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            plans,
        )
        evidence = [{
            "lot_full": "S20260902-001", "lot_base": "S20260902-001", "derived": 0,
            "stage": 3, "process_code": "45", "item_id": "P1000", "baseline_stage": 0,
            "inventory_qty": 120, "production_qty": 110, "recognized_qty": 120,
            "family_cap": 0, "reason": "test",
        }]
        allocated, completed = allocate_evidence(self.connection, evidence)
        quantities = dict(self.connection.execute("SELECT so_id,plan_qty FROM aps_plan"))
        self.assertEqual(allocated, 120)
        self.assertEqual(completed, 1)
        self.assertEqual(quantities["R1"], 0)
        self.assertEqual(quantities["R2"], 80)


if __name__ == "__main__":
    unittest.main()
