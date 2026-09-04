from __future__ import annotations

import os
import tempfile
import uuid
from datetime import date, datetime
from pathlib import Path


PROCESS_EXPORT_NAME = {
    "사출": "사출",
    "분리": "분리",
    "하이드레이션": "하이드레이션",
    "접착": "검사접착",
    "누수규격": "누수규격",
}
PROCESS_CODE = {
    "사출": "R코드",
    "분리": "Q코드",
    "하이드레이션": "P코드",
    "접착": "P코드",
    "누수규격": "P코드",
}
PROCESS_NAME_BASIS = {
    "사출": "R",
    "분리": "Q",
    "하이드레이션": "P",
    "접착": "P",
    "누수규격": "P",
}
HIDDEN_CODE_ORDER = {
    "R코드": ("Q코드", "P코드", "T코드"),
    "Q코드": ("R코드", "P코드", "T코드"),
    "P코드": ("Q코드", "R코드", "T코드"),
}
CODE_NAME = {
    "R코드": ("사출명", "품명R"),
    "Q코드": ("분리명", "품명Q"),
    "P코드": ("생산명", "품명P"),
    "T코드": ("판매명", "품명판매"),
}
PROCESS_ORDER = ("사출", "분리", "하이드레이션", "접착", "누수규격")
OVERVIEW_CODE_ORDER = ("T코드", "P코드", "Q코드", "R코드")


def _number(value: object) -> int | float:
    try:
        number = float(value or 0)
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return 0


def _order_list(row: dict) -> list[str]:
    values = str(row.get("_수주목록") or "").splitlines()
    if not values and row.get("수주번호"):
        values = [str(row["수주번호"])]
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def build_process_export_payload(
    process: str,
    detail_rows: list[dict],
    compact_rows: list[dict],
) -> dict:
    if process not in PROCESS_CODE:
        raise ValueError(f"지원하지 않는 공정입니다: {process}")
    dedicated_code = PROCESS_CODE[process]
    dedicated_name = CODE_NAME[dedicated_code][0]
    hidden_codes = HIDDEN_CODE_ORDER[dedicated_code]
    hidden_columns = tuple(
        column
        for code in hidden_codes
        for column in (code, CODE_NAME[code][0])
    )
    stage_columns = PROCESS_ORDER[: PROCESS_ORDER.index(process) + 1]
    detail_specs = (
        ("신규분류요약", "신규분류요약"),
        ("이니셜", "이니셜"),
        ("수주번호", "수주번호"),
        (dedicated_code, dedicated_code),
        (dedicated_name, "품명"),
        ("POWER", "POWER"),
        ("CP", "CP"),
        ("AXIS", "AXIS"),
        ("ADD", "ADD"),
        ("납기일", "납기일"),
        *((column, column) for column in stage_columns),
        *(
            spec
            for code in hidden_codes
            for spec in ((code, code), CODE_NAME[code])
        ),
    )
    detail_columns = tuple(header for header, _source in detail_specs)
    detail_values = [
        [
            _number(row.get(source)) if header in stage_columns else str(row.get(source) or "")
            for header, source in detail_specs
        ]
        for row in detail_rows
    ]
    compact_columns = (
        "신규분류요약", dedicated_code, dedicated_name, "POWER", "CP", "AXIS", "ADD",
        "최우선 납기일", "수주 건수", "수주번호 목록", f"{process} 부족수량",
    )
    compact_values = []
    for row in compact_rows:
        orders = _order_list(row)
        compact_values.append([
            str(row.get("신규분류요약") or ""),
            str(row.get(dedicated_code) or ""),
            str(row.get("품명") or ""),
            str(row.get("POWER") or ""),
            str(row.get("CP") or ""),
            str(row.get("AXIS") or ""),
            str(row.get("ADD") or ""),
            str(row.get("납기일") or ""),
            len(orders),
            " / ".join(orders),
            _number(row.get("공정", {}).get(process, 0)),
        ])
    return {
        "title": f"{datetime.now():%y%m%d} {PROCESS_EXPORT_NAME[process]}",
        "note": "현재 화면 필터 기준 · 코드표시 및 품명기준 선택과 무관한 공정 고정 기준",
        "sheets": [
            {
                "name": "간략히보기",
                "columns": list(compact_columns),
                "rows": compact_values,
                "hiddenColumns": [],
            },
            {
                "name": "납기별 상세",
                "columns": list(detail_columns),
                "rows": detail_values,
                "hiddenColumns": list(hidden_columns),
            },
        ],
    }


def build_overview_export_payload(
    detail_rows: list[dict],
    order_rows: list[dict],
    product_rows: list[dict],
    *,
    filename_tag: str = "",
) -> dict:
    """공정 전체 화면 전용 3시트 내보내기 자료를 만든다."""

    def process_values(row: dict) -> list[int | float]:
        process = row.get("공정") or {}
        return [_number(process.get(name)) for name in PROCESS_ORDER]

    order_columns = (
        "신규분류요약", "이니셜", "수주번호", "품명", "최우선 납기일", *PROCESS_ORDER,
    )
    order_values = [
        [
            str(row.get("신규분류요약") or ""),
            str(row.get("이니셜") or ""),
            str(row.get("수주번호") or ""),
            str(row.get("품명") or ""),
            str(row.get("납기일") or ""),
            *process_values(row),
        ]
        for row in order_rows
    ]

    product_columns = (
        "신규분류요약", "이니셜", "수주번호", "품명",
        "POWER", "CP", "AXIS", "ADD", "최우선 납기일", *PROCESS_ORDER,
    )
    product_values = [
        [
            str(row.get("신규분류요약") or ""),
            str(row.get("이니셜") or ""),
            str(row.get("수주번호") or ""),
            str(row.get("품명") or ""),
            str(row.get("POWER") or ""),
            str(row.get("CP") or ""),
            str(row.get("AXIS") or ""),
            str(row.get("ADD") or ""),
            str(row.get("납기일") or ""),
            *process_values(row),
        ]
        for row in product_rows
    ]

    detail_specs: list[tuple[str, str]] = [
        ("신규분류요약", "신규분류요약"),
        ("이니셜", "이니셜"),
        ("수주번호", "수주번호"),
        ("판매명", "품명판매"),
        ("POWER", "POWER"),
        ("CP", "CP"),
        ("AXIS", "AXIS"),
        ("ADD", "ADD"),
        ("납기일", "납기일"),
        *((process, process) for process in PROCESS_ORDER),
    ]
    # 개별 공정 내보내기와 동일하게 보이는 핵심 열을 먼저 배치하고,
    # 필요할 때 Excel에서 펼쳐볼 코드·품명 열은 표의 맨 오른쪽에 둔다.
    # 판매명은 화면의 기본 품명이므로 보이는 열에 두고 T코드만 숨김으로 보낸다.
    detail_specs.append(("T코드", "T코드"))
    hidden_columns: list[str] = ["T코드"]
    for code in ("P코드", "Q코드", "R코드"):
        name_header, name_source = CODE_NAME[code]
        detail_specs.extend(((code, code), (name_header, name_source)))
        hidden_columns.extend((code, name_header))
    detail_columns = tuple(header for header, _source in detail_specs)
    detail_values = [
        [
            _number((row.get("공정") or {}).get(source))
            if header in PROCESS_ORDER
            else str(row.get(source) or "")
            for header, source in detail_specs
        ]
        for row in detail_rows
    ]

    tag = str(filename_tag or "").strip()
    title_label = "실시간 실적 반영" if tag else "공정 현황(APS)"
    return {
        "title": f"{datetime.now():%y%m%d} {title_label}",
        "note": (
            "현재 화면 필터 기준 · 간략히보기 수주별/제품별 · "
            "납기별 상세는 판매명 기본 표시, 품목코드·공정별 품명은 오른쪽 숨김"
        ),
        "sheets": [
            {
                "name": "간략히보기 수주별",
                "columns": list(order_columns),
                "rows": order_values,
                "hiddenColumns": [],
            },
            {
                "name": "간략히보기 제품별",
                "columns": list(product_columns),
                "rows": product_values,
                "hiddenColumns": [],
            },
            {
                "name": "납기별 상세",
                "columns": list(detail_columns),
                "rows": detail_values,
                "hiddenColumns": hidden_columns,
            },
        ],
    }


def build_lot_work_order_export_payload(
    process: str | None,
    rows: list[dict],
    columns: list[str] | tuple[str, ...],
    *,
    header_labels: dict[str, str] | None = None,
    hidden_columns: list[str] | tuple[str, ...] = (),
    note: str = "",
) -> dict:
    """현재 LOT 작업순서 표를 공정별 Excel 규칙에 맞춰 내보낸다."""
    labels = dict(header_labels or {})
    source_columns = [str(column) for column in columns]
    process_name = str(process or "")
    hidden_sources = {str(column) for column in hidden_columns}
    if process_name in {"사출", "분리"} and "Q코드" in source_columns:
        source_columns = [column for column in source_columns if column != "Q코드"] + ["Q코드"]
        hidden_sources.add("Q코드")
    export_columns = [str(labels.get(column, column)) for column in source_columns]
    export_rows: list[list[object]] = []
    for row in rows:
        values: list[object] = []
        for source, header in zip(source_columns, export_columns):
            value = row.get(source, "")
            values.append(_number(value) if "수량" in header else str(value or ""))
        export_rows.append(values)

    process_label = PROCESS_EXPORT_NAME.get(process_name, "전체")
    title = f"{datetime.now():%y%m%d} LOT 작업 순서 · {process_label}"
    return {
        "title": title,
        "subject": "생산3팀 LOT 작업 순서",
        "note": note or "현재 화면의 필터·정렬·열 접기 상태 기준",
        "sheets": [
            {
                "name": f"LOT작업순서_{process_label}",
                "columns": export_columns,
                "rows": export_rows,
                "hiddenColumns": [
                    labels.get(column, column)
                    for column in source_columns
                    if column in hidden_sources
                ],
            }
        ],
    }


def _windows_user_folder(value_name: str, fallback: Path) -> Path:
    if os.name != "nt":
        return fallback
    try:
        import winreg

        registry_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
            value, _value_type = winreg.QueryValueEx(key, value_name)
        resolved = os.path.expandvars(str(value or "").strip())
        if resolved:
            return Path(resolved).expanduser()
    except (OSError, ValueError):
        pass
    return fallback


def desktop_export_path(process: str, *, filename_tag: str = "") -> Path:
    desktop = _windows_user_folder("Desktop", Path.home() / "Desktop")
    desktop.mkdir(parents=True, exist_ok=True)
    tag = str(filename_tag or "").strip()
    tag_part = f"_{tag}" if tag else ""
    return desktop / f"{datetime.now():%y%m%d}{tag_part}_{PROCESS_EXPORT_NAME[process]}.xlsx"


def desktop_overview_export_path(*, filename_tag: str = "") -> Path:
    desktop = _windows_user_folder("Desktop", Path.home() / "Desktop")
    desktop.mkdir(parents=True, exist_ok=True)
    tag = str(filename_tag or "").strip()
    label = "실시간실적반영" if tag else "공정현황_APS"
    return desktop / f"{datetime.now():%y%m%d}_{label}.xlsx"


def desktop_lot_work_order_export_path(process: str | None = None) -> Path:
    desktop = _windows_user_folder("Desktop", Path.home() / "Desktop")
    desktop.mkdir(parents=True, exist_ok=True)
    process_label = PROCESS_EXPORT_NAME.get(str(process or ""), "전체")
    return desktop / f"{datetime.now():%y%m%d}_LOT작업순서_{process_label}.xlsx"


def _available_output_path(output_path: Path) -> Path:
    if not output_path.exists():
        return output_path
    sequence = 2
    while True:
        candidate = output_path.with_name(
            f"{output_path.stem} ({sequence}){output_path.suffix}"
        )
        if not candidate.exists():
            return candidate
        sequence += 1


def _column_width(header: str) -> float:
    if header == "품명" or header in {name for name, _source in CODE_NAME.values()}:
        return 34
    if "수주번호 목록" in header:
        return 36
    if header == "신규분류요약":
        return 22
    if "코드" in header:
        return 23
    if header == "수주번호":
        return 18
    if header == "이니셜":
        return 12
    if "납기일" in header:
        return 14
    if header in {"POWER", "CP", "AXIS", "ADD"}:
        return 11
    if "수량" in header or header in {*PROCESS_ORDER, "수주 건수"}:
        return 13
    return 15


def _excel_date(value: object) -> date | None:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text) if len(text) == 10 else None
    except ValueError:
        return None


def _write_sheet(workbook, payload: dict, title: str, note: str, table_index: int) -> None:
    worksheet = workbook.add_worksheet(str(payload["name"])[:31])
    columns = [str(column) for column in payload["columns"]]
    rows = list(payload.get("rows") or [])
    hidden_columns = set(payload.get("hiddenColumns") or [])
    last_column = len(columns) - 1
    visible_columns = [index for index, header in enumerate(columns) if header not in hidden_columns]
    visible_last_column = max(visible_columns, default=last_column)

    title_format = workbook.add_format({
        "bold": True,
        "font_color": "#FFFFFF",
        "font_size": 15,
        "bg_color": "#0A7AFF",
        "valign": "vcenter",
    })
    note_format = workbook.add_format({
        "font_color": "#52677E",
        "font_size": 10,
        "bg_color": "#EEF5FF",
        "valign": "vcenter",
    })
    header_format = workbook.add_format({
        "bold": True,
        "font_color": "#173B63",
        "bg_color": "#E7EEF7",
        "align": "center",
        "valign": "vcenter",
        "border": 1,
        "border_color": "#D6E0EB",
    })
    date_format = workbook.add_format({"num_format": "yyyy-mm-dd", "valign": "vcenter"})
    number_format = workbook.add_format({"num_format": "#,##0", "align": "right", "valign": "vcenter"})

    if visible_last_column > 0:
        worksheet.merge_range(0, 0, 0, visible_last_column, title, title_format)
        worksheet.merge_range(1, 0, 1, visible_last_column, note, note_format)
    else:
        worksheet.write(0, 0, title, title_format)
        worksheet.write(1, 0, note, note_format)
    worksheet.set_row(0, 25.5)
    worksheet.set_row(1, 18.75)
    worksheet.set_row(3, 21)

    numeric_headers = {*PROCESS_ORDER, "수주 건수"}
    for row_index, row in enumerate(rows, start=4):
        worksheet.set_row(row_index, 16.5)
        for column_index, header in enumerate(columns):
            value = row[column_index] if column_index < len(row) else ""
            parsed_date = _excel_date(value) if "납기일" in header else None
            if parsed_date is not None:
                worksheet.write_datetime(row_index, column_index, parsed_date, date_format)
            elif "수량" in header or header in numeric_headers:
                worksheet.write_number(row_index, column_index, _number(value), number_format)
            else:
                worksheet.write(row_index, column_index, value)

    table_columns = []
    for header in columns:
        options = {"header": header}
        if "납기일" in header:
            options["format"] = date_format
        elif "수량" in header or header in numeric_headers:
            options["format"] = number_format
        table_columns.append(options)
    if rows:
        worksheet.add_table(
            3,
            0,
            len(rows) + 3,
            last_column,
            {
                "name": f"ProcessExport{table_index}",
                "style": "Table Style Medium 2",
                "columns": table_columns,
            },
        )
    else:
        for column_index, header in enumerate(columns):
            worksheet.write(3, column_index, header, header_format)
        worksheet.autofilter(3, 0, 3, last_column)

    for column_index, header in enumerate(columns):
        options = {"hidden": True} if header in hidden_columns else None
        worksheet.set_column(
            column_index,
            column_index,
            _column_width(header),
            None,
            options,
        )
    worksheet.freeze_panes(4, 0)
    worksheet.hide_gridlines(2)


def _write_workbook(output_path: Path, payload: dict) -> None:
    try:
        import xlsxwriter
    except ImportError as exc:
        raise RuntimeError(
            "엑셀 내보내기 구성요소(XlsxWriter)가 설치되지 않았습니다. 프로그램을 다시 설치해 주세요."
        ) from exc

    with xlsxwriter.Workbook(str(output_path)) as workbook:
        workbook.set_properties({
            "title": str(payload.get("title") or "똑딱이 공정 현황"),
            "subject": str(payload.get("subject") or "생산3팀 공정 현황"),
            "author": "생산기획팀 RD",
            "company": "Interojo",
        })
        for table_index, sheet in enumerate(payload.get("sheets") or [], start=1):
            _write_sheet(
                workbook,
                sheet,
                str(payload.get("title") or ""),
                str(payload.get("note") or ""),
                table_index,
            )


def export_process_workbook(
    process: str,
    detail_rows: list[dict],
    compact_rows: list[dict],
    *,
    output_path: Path | None = None,
    preview_dir: Path | None = None,
    filename_tag: str = "",
) -> Path:
    requested_output = (
        Path(output_path)
        if output_path
        else desktop_export_path(process, filename_tag=filename_tag)
    ).resolve()
    output = _available_output_path(requested_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = build_process_export_payload(process, detail_rows, compact_rows)
    temporary_output = Path(tempfile.gettempdir()) / f"ddokddak_process_{uuid.uuid4().hex}.xlsx"
    try:
        _write_workbook(temporary_output, payload)
        os.replace(temporary_output, output)
        return output
    finally:
        temporary_output.unlink(missing_ok=True)
        # preview_dir is retained in the public signature for existing QA callers.
        _ = preview_dir


def export_overview_workbook(
    detail_rows: list[dict],
    order_rows: list[dict],
    product_rows: list[dict],
    *,
    output_path: Path | None = None,
    filename_tag: str = "",
) -> Path:
    requested_output = (
        Path(output_path)
        if output_path
        else desktop_overview_export_path(filename_tag=filename_tag)
    ).resolve()
    output = _available_output_path(requested_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = build_overview_export_payload(
        detail_rows,
        order_rows,
        product_rows,
        filename_tag=filename_tag,
    )
    temporary_output = Path(tempfile.gettempdir()) / f"ddokddak_overview_{uuid.uuid4().hex}.xlsx"
    try:
        _write_workbook(temporary_output, payload)
        os.replace(temporary_output, output)
        return output
    finally:
        temporary_output.unlink(missing_ok=True)


def export_lot_work_order_workbook(
    process: str | None,
    rows: list[dict],
    columns: list[str] | tuple[str, ...],
    *,
    header_labels: dict[str, str] | None = None,
    hidden_columns: list[str] | tuple[str, ...] = (),
    note: str = "",
    output_path: Path | None = None,
) -> Path:
    requested_output = (
        Path(output_path)
        if output_path
        else desktop_lot_work_order_export_path(process)
    ).resolve()
    output = _available_output_path(requested_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = build_lot_work_order_export_payload(
        process,
        rows,
        columns,
        header_labels=header_labels,
        hidden_columns=hidden_columns,
        note=note,
    )
    temporary_output = Path(tempfile.gettempdir()) / (
        f"ddokddak_lot_work_order_{uuid.uuid4().hex}.xlsx"
    )
    try:
        _write_workbook(temporary_output, payload)
        os.replace(temporary_output, output)
        return output
    finally:
        temporary_output.unlink(missing_ok=True)
