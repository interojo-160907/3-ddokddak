from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from services.lot_work_order_service import LotWorkOrderService


APS_COLUMNS = (
    "id INTEGER, so_id TEXT, demand_item_id TEXT, demand_item_name TEXT, "
    "demand_group_id TEXT, item_id TEXT, item_name TEXT, item_name2 TEXT, "
    "power TEXT, due_date TEXT, plan_date TEXT, seq INTEGER, demand_type TEXT, "
    "dest_country TEXT, initial TEXT, plan_qty REAL, oper_id TEXT"
)


class SyntheticLotWorkOrderService(LotWorkOrderService):
    def _bom_q_to_p(self) -> dict[str, set[str]]:
        return {"Q0001": {"P0001", "P0002"}}

    def _bom_code_metadata(self, prefix: str) -> dict[str, dict[str, str]]:
        return {
            "P0001": {"품명": "제품 1", "신규분류요약": "1-Day_Sph"},
            "P0002": {"품명": "제품 2", "신규분류요약": "1-Day_Sph"},
        } if prefix == "P" else {}


def _insert_plan(
    connection: sqlite3.Connection,
    *,
    row_id: int,
    so_id: str,
    demand_item_id: str,
    item_id: str,
    due_date: str,
    plan_qty: float,
    oper_id: str,
) -> None:
    connection.execute(
        "INSERT INTO aps_plan VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            row_id, so_id, demand_item_id, "", "1-Day_Sph", item_id,
            item_id, item_id, "-01.25", due_date, due_date, row_id,
            "", "", "", plan_qty, oper_id,
        ),
    )


class HydrationAllocationTest(unittest.TestCase):
    def _database(self, p_needs: tuple[float, float], lot_qty: float) -> tuple[tempfile.TemporaryDirectory, Path]:
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "live.sqlite"
        connection = sqlite3.connect(path)
        try:
            connection.execute(f"CREATE TABLE aps_plan ({APS_COLUMNS})")
            connection.execute(
                "CREATE TABLE current_inventory ("
                "warehouse_code TEXT, warehouse_name TEXT, lot_full TEXT, "
                "item_id TEXT, stock_qty REAL, stage INTEGER)"
            )
            for index, (p_code, need, due) in enumerate((
                ("P0001-01.25", p_needs[0], "2026-09-10"),
                ("P0002-01.25", p_needs[1], "2026-09-11"),
            ), start=1):
                so_id = f"SO{index}"
                demand_item_id = f"D{index}"
                _insert_plan(
                    connection,
                    row_id=index,
                    so_id=so_id,
                    demand_item_id=demand_item_id,
                    item_id=p_code,
                    due_date=due,
                    plan_qty=need,
                    oper_id="45",
                )
                _insert_plan(
                    connection,
                    row_id=index + 10,
                    so_id=so_id,
                    demand_item_id=demand_item_id,
                    item_id="Q0001-01.25",
                    due_date=due,
                    plan_qty=need,
                    oper_id="20",
                )
            connection.execute(
                "INSERT INTO current_inventory VALUES (?,?,?,?,?,?)",
                ("R001", "분리창고", "S20260831-490", "Q0001-01.25", lot_qty, 2),
            )
            connection.commit()
        finally:
            connection.close()
        return directory, path

    def test_lot_goes_whole_to_later_need_when_it_can_absorb_entire_lot(self) -> None:
        directory, path = self._database((362, 4906), 1719)
        self.addCleanup(directory.cleanup)
        service = SyntheticLotWorkOrderService(database_path=path)
        rows = service.load_rows("하이드레이션", allocation_mode="split2")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["체크시트(LOT)"], "S20260831-490")
        self.assertEqual(rows[0]["P코드"], "P0002-01.25")
        self.assertEqual(rows[0]["배정"], "단일구성")
        self.assertEqual(rows[0]["배정수량"], 1719)

    def test_only_final_unabsorbed_lot_is_split_at_most_twice(self) -> None:
        directory, path = self._database((100, 100), 500)
        self.addCleanup(directory.cleanup)
        service = SyntheticLotWorkOrderService(database_path=path)
        rows = service.load_rows("하이드레이션", allocation_mode="split2")

        self.assertEqual(len(rows), 2)
        self.assertEqual({row["배정"] for row in rows}, {"분할구성 1/2", "분할구성 2/2"})
        self.assertEqual(sum(row["배정수량"] for row in rows), 500)
        self.assertTrue(all(row["배정수량"] >= 200 for row in rows))


if __name__ == "__main__":
    unittest.main()
