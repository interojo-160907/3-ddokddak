from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from services.process_excel_exporter import export_process_workbook
from services.process_status_service import ProcessStatusService
from ui.process_overview_page import DueDetailPage, ProcessOverviewPage


def main() -> None:
    app = QApplication.instance() or QApplication([])
    rows = ProcessOverviewPage._normalize(ProcessStatusService().load_rows())
    page = DueDetailPage(fixed_process="사출")
    page.load(rows)
    detail_rows, compact_rows = page.export_rows(rows)
    baseline = [
        (row.get("수주번호"), row.get("R코드"), row.get("품명"))
        for row in detail_rows
    ]
    page.name_basis.setCurrentIndex(page.name_basis.findData("판매"))
    for checkbox in page.code_checks.values():
        checkbox.setChecked(True)
    page.compact_view.setChecked(True)
    option_detail_rows, option_compact_rows = page.export_rows(rows)
    assert baseline == [
        (row.get("수주번호"), row.get("R코드"), row.get("품명"))
        for row in option_detail_rows
    ], "코드표시/품명기준/간략히보기 선택이 엑셀 상세 데이터에 영향을 줌"
    assert len(compact_rows) == len(option_compact_rows), "간략히보기 선택이 엑셀 간략 시트에 영향을 줌"
    output_dir = Path("outputs") / "process_excel_export_qa"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = export_process_workbook(
        "사출",
        detail_rows,
        compact_rows,
        output_path=output_dir / "260819_사출.xlsx",
        preview_dir=output_dir / "preview",
    )
    print(f"output={output.resolve()}")
    print(f"source={len(rows)} detail={len(detail_rows)} compact={len(compact_rows)}")
    print("display_option_independence=ok")
    app.quit()


if __name__ == "__main__":
    main()
