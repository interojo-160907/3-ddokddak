from __future__ import annotations

import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from services.process_excel_exporter import (
    build_lot_work_order_export_payload,
    build_overview_export_payload,
    export_lot_work_order_workbook,
    export_overview_workbook,
)


def _row() -> dict:
    return {
        "신규분류요약": "Si_1-Day_Sph",
        "이니셜": "AA1000",
        "수주번호": "R202609030001",
        "T코드": "T1000",
        "P코드": "P1000",
        "Q코드": "Q1000",
        "R코드": "R1000",
        "품명": "선택 품명",
        "품명판매": "판매 품명",
        "품명P": "생산 품명",
        "품명Q": "분리 품명",
        "품명R": "사출 품명",
        "POWER": "-01.00",
        "CP": "-1.25",
        "AXIS": "180",
        "ADD": "",
        "납기일": "2026-09-04",
        "공정": {
            "사출": 500,
            "분리": 400,
            "하이드레이션": 300,
            "접착": 200,
            "누수규격": 100,
        },
    }


class OverviewExcelExporterTest(unittest.TestCase):
    def test_overview_payload_has_three_sheets_and_hidden_detail_identity_columns(self) -> None:
        row = _row()
        payload = build_overview_export_payload([row], [row], [row])
        self.assertEqual(
            [sheet["name"] for sheet in payload["sheets"]],
            ["간략히보기 수주별", "간략히보기 제품별", "납기별 상세"],
        )
        detail = payload["sheets"][2]
        product = payload["sheets"][1]
        self.assertNotIn("T코드", product["columns"])
        self.assertNotIn("P코드", product["columns"])
        self.assertNotIn("Q코드", product["columns"])
        self.assertNotIn("R코드", product["columns"])
        self.assertEqual(detail["columns"][3], "판매명")
        self.assertEqual(
            detail["hiddenColumns"],
            ["T코드", "P코드", "생산명", "Q코드", "분리명", "R코드", "사출명"],
        )
        self.assertEqual(
            detail["columns"][-7:],
            ["T코드", "P코드", "생산명", "Q코드", "분리명", "R코드", "사출명"],
        )
        self.assertEqual(detail["rows"][0][9:14], [500, 400, 300, 200, 100])

    def test_overview_workbook_writes_three_sheets_and_hidden_columns(self) -> None:
        row = _row()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "overview.xlsx"
            export_overview_workbook([row], [row], [row], output_path=output)
            self.assertTrue(output.is_file())
            with zipfile.ZipFile(output) as archive:
                workbook = ET.fromstring(archive.read("xl/workbook.xml"))
                names = [
                    sheet.attrib["name"]
                    for sheet in workbook.findall(
                        ".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"
                    )
                ]
                self.assertEqual(
                    names,
                    ["간략히보기 수주별", "간략히보기 제품별", "납기별 상세"],
                )
                detail_xml = ET.fromstring(archive.read("xl/worksheets/sheet3.xml"))
                hidden = detail_xml.findall(
                    ".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}col[@hidden='1']"
                )
                hidden_count = sum(
                    int(column.attrib["max"]) - int(column.attrib["min"]) + 1
                    for column in hidden
                )
                self.assertEqual(hidden_count, 7)
                self.assertTrue(all(int(column.attrib["min"]) >= 15 for column in hidden))


class LotWorkOrderExcelExporterTest(unittest.TestCase):
    def test_payload_keeps_visible_order_dynamic_headers_and_hidden_columns(self) -> None:
        rows = [{
            "구분": "추가사출",
            "현재위치": "사출 필요",
            "신규분류요약": "1-Day_Sph",
            "R코드": "R0001-01.00",
            "Q코드": "Q0001-01.00",
            "재고수량": 4906,
        }]
        columns = [
            "구분", "현재위치", "신규분류요약", "R코드", "Q코드", "재고수량",
        ]
        payload = build_lot_work_order_export_payload(
            "사출",
            rows,
            columns,
            header_labels={"재고수량": "필요수량"},
            hidden_columns=["Q코드"],
        )
        sheet = payload["sheets"][0]
        self.assertEqual(payload["subject"], "생산3팀 LOT 작업 순서")
        self.assertEqual(sheet["name"], "LOT작업순서_사출")
        self.assertEqual(sheet["columns"], [
            "구분", "현재위치", "신규분류요약", "R코드", "Q코드", "필요수량",
        ])
        self.assertEqual(sheet["hiddenColumns"], ["Q코드"])
        self.assertEqual(sheet["rows"][0][-1], 4906)

    def test_workbook_has_lot_specific_sheet_and_hidden_folded_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "260904_LOT작업순서_분리.xlsx"
            export_lot_work_order_workbook(
                "분리",
                [{"현재위치": "사출창고", "R코드": "R0001", "Q코드": "Q0001", "재고수량": 100}],
                ["현재위치", "R코드", "Q코드", "재고수량"],
                hidden_columns=["Q코드"],
                output_path=output,
            )
            self.assertTrue(output.is_file())
            with zipfile.ZipFile(output) as archive:
                workbook = ET.fromstring(archive.read("xl/workbook.xml"))
                names = [
                    sheet.attrib["name"]
                    for sheet in workbook.findall(
                        ".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"
                    )
                ]
                self.assertEqual(names, ["LOT작업순서_분리"])
                sheet_xml = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
                hidden = sheet_xml.findall(
                    ".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}col[@hidden='1']"
                )
                self.assertEqual(len(hidden), 1)


if __name__ == "__main__":
    unittest.main()
