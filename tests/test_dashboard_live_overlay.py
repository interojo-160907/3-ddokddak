from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

import services.dashboard_service as dashboard_module


APS_SCHEMA = """
CREATE TABLE aps_plan (
    so_id TEXT, initial TEXT, due_date TEXT, demand_group_id TEXT,
    demand_type TEXT, dest_country TEXT, plan_qty REAL, oper_id TEXT,
    item_id TEXT, demand_item_id TEXT, demand_item_name TEXT, power TEXT,
    demand_qty REAL, cust_name TEXT, res_site_id TEXT,
    original_plan_qty REAL
)
"""


def _write_status(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_database(path: Path, *, current_qty: float) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(APS_SCHEMA)
        connection.execute(
            "INSERT INTO aps_plan VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "O1", "AA1000", date.today().isoformat(), "1-Day_Sph",
                "해외", "그리스", current_qty, "80", "P1000", "T1000",
                "판매품명", "-01.00", 125, "거래처", "S관(3공장)", 424,
            ),
        )
        connection.commit()


class DashboardLiveOverlayTest(unittest.TestCase):
    def test_keeps_aps_shortage_and_marks_current_completion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            aps_db = root / "aps.sqlite"
            live_db = root / "live.sqlite"
            aps_status = root / "aps.json"
            live_status = root / "live.json"
            _write_database(aps_db, current_qty=424)
            _write_database(live_db, current_qty=0)
            _write_status(aps_status, {"source_refreshed_at": "2026-09-03 08:03:42"})
            _write_status(
                live_status,
                {
                    "status": "success",
                    "aps_source_refreshed_at": "2026-09-03 08:03:42",
                },
            )

            with patch.multiple(
                dashboard_module,
                APS_DB=aps_db,
                APS_STATUS=aps_status,
                LIVE_DB=live_db,
                LIVE_STATUS=live_status,
                PRODUCTION_DB=root / "missing-production.sqlite",
            ):
                service = dashboard_module.DashboardService()
                loaded = service.load()
                self.assertEqual(loaded["risks"][0]["risk_qty"], 424)
                self.assertEqual(loaded["risks"][0]["current_qty"], 0)
                self.assertTrue(loaded["risks"][0]["work_completed"])

                detail = service.order_details("O1")
                self.assertEqual(detail["order"]["aps_remaining_qty"], 424)
                self.assertEqual(detail["order"]["current_remaining_qty"], 0)
                self.assertTrue(detail["order"]["work_completed"])
                self.assertEqual(detail["products"][0]["누수·규격"], 424)
                self.assertEqual(detail["products"][0]["current"]["누수·규격"], 0)

    def test_ignores_live_result_from_another_aps_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            aps_db = root / "aps.sqlite"
            live_db = root / "live.sqlite"
            aps_status = root / "aps.json"
            live_status = root / "live.json"
            _write_database(aps_db, current_qty=424)
            _write_database(live_db, current_qty=0)
            _write_status(aps_status, {"source_refreshed_at": "NEW"})
            _write_status(
                live_status,
                {"status": "success", "aps_source_refreshed_at": "OLD"},
            )

            with patch.multiple(
                dashboard_module,
                APS_DB=aps_db,
                APS_STATUS=aps_status,
                LIVE_DB=live_db,
                LIVE_STATUS=live_status,
                PRODUCTION_DB=root / "missing-production.sqlite",
            ):
                loaded = dashboard_module.DashboardService().load()
                self.assertEqual(loaded["risks"][0]["risk_qty"], 424)
                self.assertNotIn("work_completed", loaded["risks"][0])


if __name__ == "__main__":
    unittest.main()
