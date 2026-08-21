from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
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
PROCESS_ORDER = ("사출", "분리", "하이드레이션", "접착", "누수규격")


def _runtime_paths() -> tuple[Path, Path]:
    node = Path(os.getenv(
        "DDOKDDAK_ARTIFACT_NODE",
        str(Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"),
    ))
    modules = Path(os.getenv(
        "DDOKDDAK_ARTIFACT_NODE_MODULES",
        str(Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"),
    ))
    if not node.is_file() or not modules.is_dir():
        raise RuntimeError("엑셀 내보내기 구성요소를 찾을 수 없습니다. 프로그램 설치 구성을 확인해 주세요.")
    return node, modules


def _prepare_runtime() -> tuple[Path, Path]:
    node, modules = _runtime_paths()
    runtime = Path(tempfile.gettempdir()) / "ddokddak_process_excel_runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    builder_source = Path(__file__).resolve().parents[1] / "scripts" / "process_excel_builder.mjs"
    builder = runtime / builder_source.name
    shutil.copy2(builder_source, builder)
    link = runtime / "node_modules"
    if not link.exists():
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(modules)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0 and not link.exists():
            raise RuntimeError(f"엑셀 런타임 준비 실패: {result.stderr or result.stdout}")
    return node, builder


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
    hidden_codes = HIDDEN_CODE_ORDER[dedicated_code]
    stage_columns = PROCESS_ORDER[: PROCESS_ORDER.index(process) + 1]
    detail_columns = (
        "신규분류요약", "이니셜", "수주번호", dedicated_code, "품명",
        "POWER", "CP", "AXIS", "ADD", "납기일", *stage_columns, *hidden_codes,
    )
    detail_values = [
        [
            _number(row.get(column)) if column in stage_columns else str(row.get(column) or "")
            for column in detail_columns
        ]
        for row in detail_rows
    ]
    compact_columns = (
        "신규분류요약", dedicated_code, "품명", "POWER", "CP", "AXIS", "ADD",
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
                "hiddenColumns": list(hidden_codes),
            },
        ],
    }


def desktop_export_path(process: str) -> Path:
    desktop = Path(os.getenv("USERPROFILE", str(Path.home()))) / "Desktop"
    desktop.mkdir(parents=True, exist_ok=True)
    return desktop / f"{datetime.now():%y%m%d}_{PROCESS_EXPORT_NAME[process]}.xlsx"


def export_process_workbook(
    process: str,
    detail_rows: list[dict],
    compact_rows: list[dict],
    *,
    output_path: Path | None = None,
    preview_dir: Path | None = None,
) -> Path:
    output = (Path(output_path) if output_path else desktop_export_path(process)).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    node, builder = _prepare_runtime()
    runtime = builder.parent
    token = uuid.uuid4().hex
    input_path = runtime / f"process_export_{token}.json"
    # artifact-tool의 Windows 경로 처리 안정성을 위해 ASCII 임시 경로에서 만든 뒤 이동한다.
    temporary_output = runtime / f"process_output_{token}.xlsx"
    temporary_preview = runtime / f"process_preview_{token}" if preview_dir else None
    payload = build_process_export_payload(process, detail_rows, compact_rows)
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    command = [str(node), str(builder), str(input_path), temporary_output.name]
    if temporary_preview:
        command.append(temporary_preview.name)
    try:
        result = subprocess.run(
            command,
            cwd=runtime,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        # 대용량 워크북 종료 시 Node가 Windows에서 비정상 종료 코드를 남기는 경우가 있어도
        # 완성된 XLSX가 존재하면 원자적으로 교체해 사용한다.
        if not temporary_output.is_file():
            raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "엑셀 파일 생성 실패")
        os.replace(temporary_output, output)
        if preview_dir and temporary_preview:
            preview_target = Path(preview_dir).resolve()
            preview_target.mkdir(parents=True, exist_ok=True)
            shutil.copytree(temporary_preview, preview_target, dirs_exist_ok=True)
        return output
    finally:
        input_path.unlink(missing_ok=True)
        temporary_output.unlink(missing_ok=True)
        if temporary_preview:
            shutil.rmtree(temporary_preview, ignore_errors=True)
