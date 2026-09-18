from __future__ import annotations

from datetime import date, datetime, timedelta

import qtawesome as qta
from PySide6.QtCore import QDate, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDateEdit,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from services.live_production_need_service import LiveProductionNeedService
from services.lot_work_order_service import LotWorkOrderService
from services.process_excel_exporter import (
    export_hydration_lot_work_order_workbook,
    export_lot_work_order_workbook,
)
from services.process_status_service import classification_sort_key, power_sort_key
from ui.message_dialog import show_app_message
from ui.process_overview_page import (
    CLASSIFICATION_BUTTON_STYLE,
    FILTER_BUTTON_SELECTION_STYLE,
    Card,
    DataTable,
    ProcessOverviewPage,
    _repolish,
)


LOT_COLUMNS = [
    "현재위치", "신규분류요약", "R코드", "Q코드", "체크시트(LOT)", "품명",
    "POWER", "CP", "AXIS", "ADD", "납기일", "재고수량",
]
INJECTION_COLUMNS = ["구분", *LOT_COLUMNS]
HYDRATION_COLUMNS = [
    "배정", "현재위치", "신규분류요약", "Q코드", "P코드", "체크시트(LOT)",
    "품명", "POWER", "CP", "AXIS", "ADD", "납기일", "LOT수량", "배정수량",
]
HYDRATION_SINGLE_COLUMNS = HYDRATION_COLUMNS[:-1]
HYDRATION_SPLIT_COLUMNS = HYDRATION_COLUMNS
SAME_CODE_COLUMNS = [
    "현재위치", "신규분류요약", "P코드", "체크시트(LOT)", "품명",
    "POWER", "CP", "AXIS", "ADD", "납기일", "재고수량",
]
OVERVIEW_COLUMNS = [
    "현재위치", "신규분류요약", "품목코드", "체크시트", "품명",
    "POWER", "CP", "AXIS", "ADD", "납기일", "재고수량",
]
LOT_WIDTHS = {
    "신규분류요약": 160, "현재위치": 105, "R코드": 188, "Q코드": 188,
    "체크시트(LOT)": 142, "품명": 300, "POWER": 78, "CP": 70,
    "AXIS": 64, "ADD": 68, "납기일": 98, "재고수량": 100,
}
INJECTION_WIDTHS = {"구분": 92, **LOT_WIDTHS}
HYDRATION_WIDTHS = {
    "배정": 96, "신규분류요약": 136, "현재위치": 96, "Q코드": 162,
    "P코드": 162, "체크시트(LOT)": 132, "품명": 240, "POWER": 72,
    "CP": 58, "AXIS": 58, "ADD": 58, "납기일": 92, "LOT수량": 86,
    "배정수량": 90,
}
SAME_CODE_WIDTHS = {
    "신규분류요약": 160, "현재위치": 125, "P코드": 188,
    "체크시트(LOT)": 142, "품명": 300, "POWER": 78, "CP": 70,
    "AXIS": 64, "ADD": 68, "납기일": 98, "재고수량": 100,
}
OVERVIEW_WIDTHS = {
    "신규분류요약": 165, "현재위치": 135, "품목코드": 195,
    "체크시트": 145, "품명": 320, "POWER": 78, "CP": 70,
    "AXIS": 64, "ADD": 68, "납기일": 98, "재고수량": 105,
}
LOT_MARKET_CATEGORIES = {"해외", "PB", "국내", "안전"}


def normalize_lot_market_selection(markets: set[str]) -> set[str]:
    """네 관별 버튼을 모두 고르면 미분류 LOT까지 포함하는 전체 조회로 본다."""
    selected = set(markets) or {"전체"}
    if "전체" in selected or LOT_MARKET_CATEGORIES.issubset(selected):
        return {"전체"}
    return selected


def _display_time(value: object) -> str:
    text = str(value or "").strip().replace("T", " ")
    if "+" in text:
        text = text.split("+", 1)[0]
    return text[:19] or "-"


def _date_clock_time(value: object) -> str:
    display = _display_time(value)
    return display[:16] if len(display) >= 16 else display


class LotWorkOrderPage(ProcessOverviewPage):
    """공정현황 UI를 재사용하는 LOT 작업순서 화면의 초기 골격."""

    refresh_requested = Signal()

    PROCESS_NAV_KEYS = {
        "사출": "lot_injection",
        "분리": "lot_separation",
        "하이드레이션": "lot_hydration",
        "접착": "lot_inspection",
        "누수규격": "lot_leak",
    }

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        fixed_process: str | None = None,
    ) -> None:
        # LOT 전용 산출 결과가 연결되기 전까지 APS 행을 대신 표시하지 않는다.
        super().__init__(
            parent,
            fixed_process=fixed_process,
            initial_rows=[],
            monitor_changes=False,
            include_packaging=False,
        )
        self.source_status_service = LiveProductionNeedService()
        self.lot_service = LotWorkOrderService()
        self._lot_rows: list[dict] = []
        self._overview_rows: list[dict] = []
        self._refreshing = False
        self.kpi_all.title_label.setText("작업 대상 LOT")

        self.lot_refresh_button = QPushButton("지금 갱신", self)
        self.lot_refresh_button.setObjectName("LiveRefreshButton")
        self.lot_refresh_button.setIcon(
            qta.icon("fa6s.magnifying-glass", color="#FFFFFF")
        )
        self.lot_refresh_button.setFixedHeight(30)
        self.lot_refresh_button.setMinimumWidth(90)
        self.lot_refresh_button.setToolTip(
            "현재 WIP·완료 생산실적·공정 재고와 수화 지시를 같은 회차로 다시 수집해 LOT 작업 순서 기준을 계산합니다."
        )
        self.lot_refresh_button.clicked.connect(self._request_refresh)
        self.lot_refresh_button.setVisible(fixed_process is None)

        self.calculation_status = QLabel("계산 전", self)
        self.calculation_status.setObjectName("LiveCalculationStatus")
        self.calculation_status.setMinimumHeight(30)
        self.calculation_status.setVisible(fixed_process is None)

        self.kpi_all.clicked.disconnect()
        self.kpi_all.clicked.connect(
            lambda: self.process_requested.emit("lot_work_order")
        )
        self.kpi_all.set_clickable(True, "LOT 작업 순서 전체 보기")
        for process_name, card in self.process_kpis.items():
            card.clicked.disconnect()
            card.clicked.connect(
                lambda selected=process_name: self.process_requested.emit(
                    self.PROCESS_NAV_KEYS[selected]
                )
            )
            card.set_clickable(True, f"{process_name} LOT 작업 순서 보기")

        self.refresh_button.setToolTip("저장된 최신 자료로 LOT 작업 순서를 다시 조회합니다.")
        self.export_button.setEnabled(True)
        self.export_button.setToolTip(
            "현재 보이는 LOT 작업순서를 전용 파일명으로 바탕화면에 저장합니다."
        )
        if fixed_process in {"사출", "분리", "하이드레이션", "접착", "누수규격"}:
            self._install_work_order_table(fixed_process)
            self.reload_data()
        else:
            self._install_overview_table()
            self.reload_data()
        self.refresh_calculation_status()

    def _install_overview_table(self) -> None:
        self.detail_page.hide()
        self.search.setPlaceholderText(
            "품목코드·체크시트·품명·분류·현재위치 검색 / 쉼표(,) OR / * 전체"
        )
        self._build_lot_filters()
        self.lot_columns = OVERVIEW_COLUMNS
        self.lot_table = DataTable(
            "LOT 작업 순서 · 전체 공정",
            self.lot_columns,
            OVERVIEW_WIDTHS,
            "품명",
            False,
            self,
        )
        self.layout().addWidget(self.lot_table, 1)

    def _install_work_order_table(self, process: str) -> None:
        self.detail_page.hide()
        placeholders = {
            "사출": "R코드·품명·분류 검색 / 쉼표(,) OR / * 전체",
            "분리": "LOT·R코드·Q코드·품명·분류 검색 / 쉼표(,) OR / * 전체",
            "하이드레이션": "LOT·Q코드·P코드·품명·분류 검색 / 쉼표(,) OR / * 전체",
            "접착": "LOT·P코드·품명·분류 검색 / 쉼표(,) OR / * 전체",
            "누수규격": "LOT·P코드·품명·분류 검색 / 쉼표(,) OR / * 전체",
        }
        self.search.setPlaceholderText(placeholders.get(process, "LOT·품명·분류 검색"))
        self._build_lot_filters()
        if process == "사출":
            self.lot_columns = INJECTION_COLUMNS
            widths = INJECTION_WIDTHS
        elif process == "하이드레이션":
            self.lot_columns = HYDRATION_COLUMNS
            widths = HYDRATION_WIDTHS
        elif process in {"접착", "누수규격"}:
            self.lot_columns = SAME_CODE_COLUMNS
            widths = SAME_CODE_WIDTHS
        else:
            self.lot_columns = LOT_COLUMNS
            widths = LOT_WIDTHS
        process_title = {
            "접착": "검사접착",
            "누수규격": "누수규격",
        }.get(process, process)
        self.lot_table = DataTable(
            process_title,
            self.lot_columns,
            widths,
            "품명",
            False,
            self,
        )
        self._update_downstream_code_visibility()
        self._update_hydration_column_visibility()
        self._update_injection_quantity_header()
        self.layout().addWidget(self.lot_table, 1)

    def _build_lot_filters(self) -> None:
        self.lot_filter_card = Card(self)
        filter_layout = QVBoxLayout(self.lot_filter_card)
        filter_layout.setContentsMargins(16, 10, 16, 10)
        filter_layout.setSpacing(7)

        due_row = QHBoxLayout()
        due_row.setSpacing(7)
        due_label = QLabel("납기")
        due_label.setObjectName("FilterLabel")
        due_row.addWidget(due_label)
        self.lot_due_group = QButtonGroup(self)
        self.lot_due_group.setExclusive(True)
        self.lot_due_buttons: dict[str, QPushButton] = {}
        for index, mode in enumerate(("해제", "직접", "당월", "+7일", "+14일")):
            button = QPushButton(mode)
            button.setObjectName("FilterButton")
            button.setStyleSheet(FILTER_BUTTON_SELECTION_STYLE)
            button.setCheckable(True)
            button.setChecked(index == 0)
            button.clicked.connect(self._lot_due_changed)
            self.lot_due_group.addButton(button)
            self.lot_due_buttons[mode] = button
            due_row.addWidget(button)
        self.lot_due_end = QDateEdit(QDate.currentDate())
        self.lot_due_end.setObjectName("FilterInput")
        self.lot_due_end.setCalendarPopup(True)
        self.lot_due_end.setDisplayFormat("yyyy-MM-dd")
        self.lot_due_end.setEnabled(False)
        self.lot_due_end.dateChanged.connect(self._apply_market_view)
        due_row.addWidget(self.lot_due_end)
        due_row.addStretch()
        if self.fixed_process == "사출":
            kind_label = QLabel("구분")
            kind_label.setObjectName("FilterLabel")
            due_row.addWidget(kind_label)
            self.injection_kind_group = QButtonGroup(self)
            self.injection_kind_group.setExclusive(True)
            self.injection_kind_buttons: dict[str, QPushButton] = {}
            for kind in ("전체", "저장상태", "추가사출"):
                button = QPushButton(kind)
                button.setObjectName("FilterButton")
                button.setStyleSheet(FILTER_BUTTON_SELECTION_STYLE)
                button.setCheckable(True)
                button.setChecked(kind == "추가사출")
                button.clicked.connect(self._injection_kind_changed)
                self.injection_kind_group.addButton(button)
                self.injection_kind_buttons[kind] = button
                due_row.addWidget(button)
        elif self.fixed_process == "하이드레이션":
            allocation_label = QLabel("배정안")
            allocation_label.setObjectName("FilterLabel")
            due_row.addWidget(allocation_label)
            self.hydration_mode_group = QButtonGroup(self)
            self.hydration_mode_group.setExclusive(True)
            self.hydration_mode_buttons: dict[str, QPushButton] = {}
            for label, mode in (("단일구성", "whole"), ("분할구성", "split2")):
                button = QPushButton(label)
                button.setObjectName("FilterButton")
                button.setStyleSheet(FILTER_BUTTON_SELECTION_STYLE)
                button.setCheckable(True)
                button.setChecked(mode == "whole")
                button.setProperty("allocationMode", mode)
                button.setToolTip(
                    "한 LOT를 한 P코드에 전량 배정합니다. 표에는 LOT수량까지만 표시합니다."
                    if mode == "whole"
                    else "단일 배정을 모두 끝낸 뒤 남는 LOT만 최대 2개 P코드로 나눕니다. 최소 200개, 100개 단위입니다."
                )
                button.clicked.connect(self._hydration_mode_changed)
                self.hydration_mode_group.addButton(button)
                self.hydration_mode_buttons[mode] = button
                due_row.addWidget(button)
        filter_layout.addLayout(due_row)

        self.classification_panel = QWidget(self.lot_filter_card)
        # 분류 버튼은 최대 2줄이므로 구분 필터를 바꿔도 표 위치가 흔들리지 않게 고정한다.
        self.classification_panel.setFixedHeight(72)
        self.classification_layout = QGridLayout(self.classification_panel)
        self.classification_layout.setContentsMargins(0, 0, 0, 0)
        self.classification_layout.setHorizontalSpacing(6)
        self.classification_layout.setVerticalSpacing(4)
        class_label = QLabel("분류")
        class_label.setObjectName("FilterLabel")
        self.classification_layout.addWidget(class_label, 0, 0)
        self.lot_classification_buttons: dict[str, QPushButton] = {}
        filter_layout.addWidget(self.classification_panel)

        display_row = QHBoxLayout()
        display_row.setSpacing(10)
        process_label = QLabel("공정 보기")
        process_label.setObjectName("FilterLabel")
        display_row.addWidget(process_label)
        self.lot_process_button = QPushButton(self.fixed_process or "전체")
        self.lot_process_button.setObjectName("FilterButton")
        self.lot_process_button.setStyleSheet(FILTER_BUTTON_SELECTION_STYLE)
        self.lot_process_button.setCheckable(True)
        self.lot_process_button.setChecked(True)
        self.lot_process_group = QButtonGroup(self)
        self.lot_process_group.setExclusive(True)
        self.lot_process_group.addButton(self.lot_process_button)
        display_row.addWidget(self.lot_process_button)
        if self.fixed_process in {"사출", "분리"}:
            display_row.addSpacing(14)
            downstream_label = QLabel("후공정 코드")
            downstream_label.setObjectName("FilterLabel")
            display_row.addWidget(downstream_label)
            self.downstream_code_check = QCheckBox("Q코드")
            self.downstream_code_check.setChecked(False)
            self.downstream_code_check.setToolTip(
                "체크하면 현재 LOT가 다음 공정에서 사용하는 Q코드를 표에 표시합니다."
            )
            self.downstream_code_check.stateChanged.connect(
                self._update_downstream_code_visibility
            )
            display_row.addWidget(self.downstream_code_check)
        elif self.fixed_process == "하이드레이션":
            rule_note = QLabel("단일구성 우선 · 마지막 잔여 LOT만 2분할 · 최소 200개 · 100개 단위")
            rule_note.setObjectName("CardSub")
            display_row.addWidget(rule_note)
        display_row.addStretch()
        sort_text = (
            "정렬: 납기일 → POWER → P코드 → LOT"
            if self.fixed_process in {"하이드레이션", "접착", "누수규격"}
            else "정렬: 납기일 → POWER → 신규분류요약 → 품번 → LOT"
        )
        sort_note = QLabel(sort_text)
        sort_note.setObjectName("CardSub")
        display_row.addWidget(sort_note)
        filter_layout.addLayout(display_row)
        self.layout().addWidget(self.lot_filter_card)

    def _update_downstream_code_visibility(self, *_args: object) -> None:
        if (
            not hasattr(self, "lot_table")
            or not hasattr(self, "downstream_code_check")
            or "Q코드" not in self.lot_columns
        ):
            return
        self.lot_table.table.setColumnHidden(
            self.lot_columns.index("Q코드"),
            not self.downstream_code_check.isChecked(),
        )

    def _selected_injection_kind(self) -> str:
        if not hasattr(self, "injection_kind_group"):
            return "전체"
        button = self.injection_kind_group.checkedButton()
        return button.text() if button is not None else "전체"

    def _selected_hydration_mode(self) -> str:
        if not hasattr(self, "hydration_mode_group"):
            return "whole"
        button = self.hydration_mode_group.checkedButton()
        return str(button.property("allocationMode") or "whole") if button else "whole"

    def _hydration_mode_changed(self) -> None:
        self._update_hydration_column_visibility()
        self._reload_hydration_rows()

    def _update_hydration_column_visibility(self) -> None:
        if self.fixed_process != "하이드레이션" or not hasattr(self, "lot_table"):
            return
        self.lot_table.table.setColumnHidden(
            self.lot_columns.index("배정수량"),
            self._selected_hydration_mode() == "whole",
        )

    def _reload_hydration_rows(self) -> None:
        if self.fixed_process != "하이드레이션" or not hasattr(self, "lot_table"):
            return
        self._lot_rows = self.lot_service.load_rows(
            "하이드레이션",
            allocation_mode=self._selected_hydration_mode(),
            markets=self._selected_markets(),
        )
        self._db_signature = self.lot_service.signature()
        self._rebuild_classification_filters()
        self._apply_market_view()

    def _injection_kind_changed(self) -> None:
        self._update_injection_quantity_header()
        self._rebuild_classification_filters()
        self._apply_market_view()

    def _update_injection_quantity_header(self) -> None:
        if self.fixed_process != "사출" or not hasattr(self, "lot_table"):
            return
        header = {
            "저장상태": "재고수량",
            "추가사출": "필요수량",
            "전체": "수량",
        }.get(self._selected_injection_kind(), "수량")
        self.lot_table.model.set_header_label("재고수량", header)

    def _work_target_rows(self) -> list[dict]:
        if self.fixed_process is None:
            return list(self._lot_rows)
        if self.fixed_process == "사출":
            selected_kind = self._selected_injection_kind()
            return [
                row for row in self._lot_rows
                if selected_kind == "전체" or row.get("구분") == selected_kind
            ]
        return [row for row in self._lot_rows if row.get("상태") == "배정"]

    def _selected_markets(self) -> set[str]:
        return normalize_lot_market_selection({
            str(button.property("market"))
            for button in self.market_buttons
            if button.isChecked()
        })

    def _row_for_selected_markets(
        self,
        row: dict,
        selected_markets: set[str],
    ) -> dict | None:
        """관별 필터에 맞춰 추가사출 R코드의 APS 잔량과 납기를 다시 계산한다."""
        if "전체" in selected_markets:
            return row

        if self.fixed_process == "사출" and row.get("구분") == "추가사출":
            details = [
                detail for detail in row.get("_추가사출상세", [])
                if str(detail.get("진행현황") or "") in selected_markets
            ]
            if not details:
                return None
            filtered = dict(row)
            filtered["재고수량"] = sum(float(detail.get("필요수량") or 0) for detail in details)
            filtered["납기일"] = str(details[0].get("납기일") or "")[:10]
            filtered["_진행현황"] = list(dict.fromkeys(
                str(detail.get("진행현황") or "") for detail in details
            ))
            due_totals: dict[str, float] = {}
            for detail in details:
                due_date = str(detail.get("납기일") or "")[:10]
                due_totals[due_date] = due_totals.get(due_date, 0.0) + float(
                    detail.get("필요수량") or 0
                )
            filtered["_납기별필요"] = [
                {"납기일": due_date, "필요수량": due_totals[due_date]}
                for due_date in sorted(due_totals, key=lambda value: value or "9999-12-31")
            ]
            filtered["_추가사출상세"] = details
            return filtered

        row_markets = set(row.get("_진행현황") or [])
        return row if selected_markets & row_markets else None

    @staticmethod
    def _filtered_work_sort_key(row: dict) -> tuple:
        return (
            str(row.get("납기일") or "9999-12-31"),
            power_sort_key(row.get("_POWER_NUM", row.get("POWER"))),
            classification_sort_key(row.get("신규분류요약")),
            str(
                row.get("P코드")
                or row.get("R코드")
                or row.get("Q코드")
                or row.get("품목코드")
                or ""
            ),
            str(row.get("체크시트(LOT)") or row.get("체크시트") or ""),
        )

    def _rebuild_classification_filters(self) -> None:
        selected = {
            category
            for category, button in getattr(self, "lot_classification_buttons", {}).items()
            if button.isChecked()
        } or {"전체"}
        while self.classification_layout.count() > 1:
            item = self.classification_layout.takeAt(1)
            if item.widget() is not None:
                item.widget().deleteLater()
        selected_markets = self._selected_markets()
        quantities: dict[str, float] = {}
        work_rows = self._work_target_rows()
        for row in work_rows:
            filtered_row = self._row_for_selected_markets(row, selected_markets)
            if filtered_row is None:
                continue
            category = str(filtered_row.get("신규분류요약") or "미확인").strip() or "미확인"
            quantities.setdefault(category, 0.0)
            if self.fixed_process is not None or filtered_row.get("_공정") == "누수규격":
                quantities[category] += float(filtered_row.get("재고수량") or 0)
        if "전체" not in selected and not (selected & set(quantities)):
            selected = {"전체"}
        self.lot_classification_buttons = {}
        options = [
            ("전체", sum(quantities.values())),
            *sorted(quantities.items(), key=lambda item: classification_sort_key(item[0])),
        ]
        # 실제 BOM 연결 결과에는 복합 분류가 추가될 수 있어도 버튼 영역은 항상 2줄 안에 둔다.
        column_count = max(6, (len(options) + 1) // 2)
        for index, (category, quantity) in enumerate(options):
            button = QPushButton(f"{category} ({quantity:,.0f})")
            button.setObjectName("FilterButton")
            button.setStyleSheet(CLASSIFICATION_BUTTON_STYLE)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setMinimumWidth(90)
            button.setCheckable(True)
            button.setProperty("classification", category)
            button.setToolTip(f"{category} · {self.fixed_process or '누수규격'} 기준 {quantity:,.0f} pcs")
            button.setChecked(category in selected or (category == "전체" and selected == {"전체"}))
            button.clicked.connect(
                lambda _checked=False, selected_button=button: self._classification_changed(selected_button)
            )
            row_index, column = divmod(index, column_count)
            self.classification_layout.addWidget(button, row_index, column + 1)
            self.lot_classification_buttons[category] = button
        self.classification_layout.setColumnStretch(column_count + 1, 1)

    def _market_changed(self, selected: QPushButton) -> None:
        market = str(selected.property("market") or "")
        all_button = self.market_buttons[0]
        category_buttons = self.market_buttons[1:]
        if market == "전체":
            all_button.setChecked(True)
            for button in category_buttons:
                button.setChecked(False)
        else:
            if selected.isChecked():
                all_button.setChecked(False)
            if not any(button.isChecked() for button in category_buttons):
                all_button.setChecked(True)
        if self.fixed_process is None:
            self._rebuild_classification_filters()
            self._apply_market_view()
            return
        if self.fixed_process == "하이드레이션":
            # Q LOT의 P코드 배정 자체가 관별 부족수량에 따라 달라진다.
            self._reload_hydration_rows()
            return
        self._rebuild_classification_filters()
        self._apply_market_view()

    def _classification_changed(self, selected_button: QPushButton) -> None:
        category = str(selected_button.property("classification") or "전체")
        all_button = self.lot_classification_buttons.get("전체")
        category_buttons = [
            button for name, button in self.lot_classification_buttons.items() if name != "전체"
        ]
        if category == "전체":
            selected_button.setChecked(True)
            for button in category_buttons:
                button.setChecked(False)
        else:
            if selected_button.isChecked() and all_button is not None:
                all_button.setChecked(False)
            if not any(button.isChecked() for button in category_buttons) and all_button is not None:
                all_button.setChecked(True)
        self._apply_market_view()

    def _lot_due_changed(self) -> None:
        mode = self.lot_due_group.checkedButton().text() if self.lot_due_group.checkedButton() else "해제"
        today = QDate.currentDate()
        if mode == "당월":
            self.lot_due_end.setDate(QDate(today.year(), today.month(), today.daysInMonth()))
        elif mode == "+7일":
            self.lot_due_end.setDate(today.addDays(7))
        elif mode == "+14일":
            self.lot_due_end.setDate(today.addDays(14))
        self.lot_due_end.setEnabled(mode == "직접")
        self._apply_market_view()

    def _lot_due_limit(self) -> date | None:
        button = self.lot_due_group.checkedButton()
        mode = button.text() if button is not None else "해제"
        today = date.today()
        if mode == "해제":
            return None
        if mode == "당월":
            return (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        if mode == "+7일":
            return today + timedelta(days=7)
        if mode == "+14일":
            return today + timedelta(days=14)
        selected = self.lot_due_end.date()
        return date(selected.year(), selected.month(), selected.day())

    def _apply_market_view(self) -> None:
        if not hasattr(self, "lot_table"):
            super()._apply_market_view()
            return
        if self.fixed_process is None:
            self._apply_overview_view()
            return
        rows = self._filtered_lot_rows()
        inventory_qty = sum(float(row.get("재고수량") or 0) for row in rows)
        if self.fixed_process == "사출":
            saved_rows = [row for row in rows if row.get("구분") == "저장상태"]
            additional_rows = [row for row in rows if row.get("구분") == "추가사출"]
            saved_qty = sum(float(row.get("재고수량") or 0) for row in saved_rows)
            additional_qty = sum(float(row.get("재고수량") or 0) for row in additional_rows)
            caption = (
                f"저장상태 {len(saved_rows):,} LOT · 양품수량 {saved_qty:,.0f} pcs"
                f" · 추가사출 {len(additional_rows):,}건 · 필요수량 {additional_qty:,.0f} pcs"
            )
        elif self.fixed_process == "하이드레이션":
            lot_count = len({
                (str(row.get("Q코드") or ""), str(row.get("체크시트(LOT)") or ""))
                for row in rows
            })
            split_lot_count = len({
                (str(row.get("Q코드") or ""), str(row.get("체크시트(LOT)") or ""))
                for row in rows
                if str(row.get("배정") or "").startswith("분할구성")
            })
            if self._selected_hydration_mode() == "whole":
                caption = (
                    f"단일구성 {lot_count:,} LOT · 작업행 {len(rows):,}개 · "
                    f"LOT수량 {inventory_qty:,.0f} pcs"
                )
            else:
                caption = (
                    f"분할구성안 {lot_count:,} LOT · 작업행 {len(rows):,}개 · "
                    f"배정수량 {inventory_qty:,.0f} pcs · 분할 {split_lot_count:,} LOT"
                )
        else:
            caption = f"LOT {len(rows):,}개 · 재고수량 {inventory_qty:,.0f} pcs"
        self.lot_table.load(rows, caption)
        self._update_lot_kpis(rows, inventory_qty)

    def _filtered_lot_rows(self, source_rows: list[dict] | None = None) -> list[dict]:
        """화면과 Excel에 같은 LOT 필터와 정렬을 적용한다."""
        raw_search = self.search.text().replace("，", ",").strip()
        tokens = tuple(dict.fromkeys(
            token.strip().casefold() for token in raw_search.split(",") if token.strip()
        ))
        search_all = not tokens or "*" in tokens
        fields = tuple(self.lot_table.columns)
        selected_markets = self._selected_markets()
        selected_categories = {
            category
            for category, button in self.lot_classification_buttons.items()
            if button.isChecked()
        } or {"전체"}
        due_limit = self._lot_due_limit()
        rows: list[dict] = []
        candidates = self._work_target_rows() if source_rows is None else source_rows
        for source_row in candidates:
            if source_row.get("상태") not in {None, "배정"}:
                continue
            row = self._row_for_selected_markets(source_row, selected_markets)
            if row is None:
                continue
            category = str(row.get("신규분류요약") or "미확인").strip() or "미확인"
            if "전체" not in selected_categories and category not in selected_categories:
                continue
            if due_limit is not None:
                try:
                    row_due = datetime.strptime(
                        str(row.get("납기일") or "")[:10], "%Y-%m-%d"
                    ).date()
                except ValueError:
                    continue
                if row_due > due_limit:
                    continue
            if not search_all and not any(
                token in str(row.get(field) or "").casefold()
                for token in tokens
                for field in fields
            ):
                continue
            rows.append(row)
        rows.sort(key=self._filtered_work_sort_key)
        return rows

    def _apply_overview_view(self) -> None:
        raw_search = self.search.text().replace("，", ",").strip()
        tokens = tuple(dict.fromkeys(
            token.strip().casefold() for token in raw_search.split(",") if token.strip()
        ))
        search_all = not tokens or "*" in tokens
        selected_markets = self._selected_markets()
        selected_categories = {
            category
            for category, button in self.lot_classification_buttons.items()
            if button.isChecked()
        } or {"전체"}
        due_limit = self._lot_due_limit()
        rows = [
            row for row in self._overview_rows
            if (
                "전체" in selected_markets
                or selected_markets & set(row.get("_진행현황") or [])
            )
            and (
                "전체" in selected_categories
                or str(row.get("신규분류요약") or "미확인").strip() in selected_categories
            )
            and (
                due_limit is None
                or self._row_due_on_or_before(row, due_limit)
            )
            and (
                search_all
                or any(
                    token in str(row.get(field) or "").casefold()
                    for token in tokens
                    for field in OVERVIEW_COLUMNS
                )
            )
        ]
        rows.sort(key=self._filtered_work_sort_key)
        quantity = sum(float(row.get("재고수량") or 0) for row in rows)
        self.lot_table.load(
            rows,
            f"작업 대상 LOT {len(rows):,}개 · 재고수량 {quantity:,.0f} pcs",
        )
        self._populate_lot_kpis(rows)
        self.status.setText(f"완료 · LOT {len(rows):,}개")
        self.status.setProperty("status", "success" if rows else "warning")
        _repolish(self.status)

    @staticmethod
    def _row_due_on_or_before(row: dict, due_limit: date) -> bool:
        try:
            row_due = datetime.strptime(str(row.get("납기일") or "")[:10], "%Y-%m-%d").date()
        except ValueError:
            return False
        return row_due <= due_limit

    def _update_lot_kpis(
        self,
        rows: list[dict],
        inventory_qty: float,
    ) -> None:
        selected_markets = self._selected_markets()
        overview_rows = [
            row for row in self._overview_rows
            if "전체" in selected_markets
            or selected_markets & set(row.get("_진행현황") or [])
        ]
        self._populate_lot_kpis(overview_rows)

    def _populate_lot_kpis(self, rows: list[dict]) -> None:
        total_quantity = sum(float(row.get("재고수량") or 0) for row in rows)
        self.kpi_all.set_data(
            f"{len(rows):,} LOT",
            f"재고수량 {total_quantity:,.0f} pcs",
        )
        for process_name, card in self.process_kpis.items():
            process_rows = [row for row in rows if row.get("_공정") == process_name]
            process_quantity = sum(
                float(row.get("재고수량") or 0) for row in process_rows
            )
            card.set_data(
                f"{len(process_rows):,} LOT",
                f"재고수량 {process_quantity:,.0f} pcs",
            )

    def reset_all_filters(self) -> None:
        if not hasattr(self, "lot_table"):
            super().reset_all_filters()
            return
        self.lot_due_buttons["해제"].setChecked(True)
        self.lot_due_end.setDate(QDate.currentDate())
        self.lot_due_end.setEnabled(False)
        for index, button in enumerate(self.market_buttons):
            button.setChecked(index == 0)
        for category, button in self.lot_classification_buttons.items():
            button.setChecked(category == "전체")
        if hasattr(self, "injection_kind_buttons"):
            self.injection_kind_buttons["추가사출"].setChecked(True)
            self._update_injection_quantity_header()
        if hasattr(self, "hydration_mode_buttons"):
            self.hydration_mode_buttons["whole"].setChecked(True)
            self._update_hydration_column_visibility()
        if hasattr(self, "downstream_code_check"):
            self.downstream_code_check.setChecked(False)
        self.search.clear()
        if self.fixed_process == "하이드레이션":
            self._reload_hydration_rows()
            return
        self._apply_market_view()

    def _request_refresh(self) -> None:
        if self._refreshing:
            return
        self.set_refreshing(True)
        self.refresh_requested.emit()

    def set_refreshing(self, refreshing: bool) -> None:
        self._refreshing = refreshing
        self.lot_refresh_button.setEnabled(not refreshing)
        self.lot_refresh_button.setText("계산 중…" if refreshing else "지금 갱신")
        if refreshing:
            self.calculation_status.setText("WIP·재고·완료실적·수화 지시 수집 중")
            self.calculation_status.setProperty("status", "warning")
            self.calculation_status.setToolTip(
                "실시간 실적 반영과 같은 원천 데이터를 수집한 뒤 LOT 작업 순서 기준을 다시 계산합니다."
            )
            _repolish(self.calculation_status)

    def reload_data(self) -> None:
        if not hasattr(self, "lot_table"):
            return
        self._overview_rows = self.lot_service.load_overview_rows()
        if self.fixed_process is None:
            self._lot_rows = list(self._overview_rows)
            self._db_signature = self.lot_service.signature()
            self._rebuild_classification_filters()
            self._apply_market_view()
        elif self.fixed_process in {"사출", "분리", "하이드레이션", "접착", "누수규격"}:
            if self.fixed_process == "하이드레이션":
                self._lot_rows = self.lot_service.load_rows(
                    self.fixed_process,
                    allocation_mode=self._selected_hydration_mode(),
                    markets=self._selected_markets(),
                )
            else:
                self._lot_rows = self.lot_service.load_rows(self.fixed_process)
            self._db_signature = self.lot_service.signature()
            self._rebuild_classification_filters()
            self._apply_market_view()
        self.refresh_calculation_status()

    def refresh_calculation_status(self) -> None:
        """LOT 배정이 사용하는 최신 부족수량/WIP 산출 기준을 표시한다."""
        status = self.source_status_service.status()
        calculated = _display_time(status.get("refreshed_at"))
        wip_time = _display_time(status.get("wip_source_refreshed_at"))
        state = str(status.get("status") or "")
        target = self.calculation_status
        if state == "success" and calculated != "-":
            target.setText(f"계산 완료  {_date_clock_time(calculated)}")
            target.setProperty("status", "success")
            target.setToolTip(
                f"LOT 작업 순서의 부족수량·재공 배정 기준\n"
                f"계산 완료 {calculated}\n"
                f"WIP 원천 갱신 {wip_time}"
            )
        elif state in {"retained", "waiting_wip"} and calculated != "-":
            attempted_wip = _display_time(
                status.get("attempted_wip_source_refreshed_at")
            )
            if state == "waiting_wip":
                target.setText(
                    f"WIP 새 회차 감시 중 · 기존 {_date_clock_time(calculated)} 계산 유지"
                )
            else:
                target.setText(
                    f"새 회차 계산 보류 · 기존 {_date_clock_time(calculated)} 계산 유지"
                )
            target.setProperty("status", "warning")
            target.setToolTip(
                f"{status.get('retained_reason') or '마지막 정상 계산을 유지합니다.'}\n"
                f"기존 WIP 기준 {wip_time}\n"
                f"확인한 WIP {attempted_wip}"
            )
        else:
            target.setText("계산 전")
            target.setProperty("status", "warning")
            target.setToolTip(
                "실시간 실적 반영 계산이 완료되면 LOT 작업 순서의 기준 시각을 표시합니다."
            )
        target.setVisible(self.fixed_process is None)
        _repolish(target)

    def _show_scaffold_caption(self) -> None:
        self.detail_page.table.caption.setText("LOT 작업 순서 데이터 연결 준비 중")

    def _export_excel(self) -> None:
        if not hasattr(self, "export_button") or not hasattr(self, "lot_table"):
            return
        button = self.export_button
        original_text = button.text()
        button.setEnabled(False)
        button.setText("내보내는 중…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            table = self.lot_table.table
            model = self.lot_table.model
            hidden_columns = [
                column
                for index, column in enumerate(self.lot_table.columns)
                if table.isColumnHidden(index)
            ]
            process_label = self.fixed_process or "전체"
            if self.fixed_process == "하이드레이션":
                markets = self._selected_markets()
                single_rows = self._filtered_lot_rows(self.lot_service.load_rows(
                    "하이드레이션",
                    allocation_mode="whole",
                    markets=markets,
                ))
                split_rows = self._filtered_lot_rows(self.lot_service.load_rows(
                    "하이드레이션",
                    allocation_mode="split2",
                    markets=markets,
                ))
                filter_note = (
                    "하이드레이션 · 현재 화면의 납기·분류·관별·검색 필터를 두 구성안에 동일 적용 · "
                    f"단일구성 {len(single_rows):,}행 · 분할구성 {len(split_rows):,}행"
                )
                output = export_hydration_lot_work_order_workbook(
                    single_rows,
                    split_rows,
                    list(HYDRATION_SINGLE_COLUMNS),
                    list(HYDRATION_SPLIT_COLUMNS),
                    note=filter_note,
                )
            else:
                filter_note = (
                    f"{process_label} · 현재 화면의 필터·정렬 결과 {len(model.rows):,}행 · "
                    "접힌 열은 Excel에서도 숨김 처리"
                )
                output = export_lot_work_order_workbook(
                    self.fixed_process,
                    list(model.rows),
                    list(self.lot_table.columns),
                    header_labels=dict(model.header_labels),
                    hidden_columns=hidden_columns,
                    note=filter_note,
                )
            button.setText("저장 완료 ✓")
            button.setToolTip(f"저장 완료: {output}")
            QTimer.singleShot(2500, lambda: button.setText(original_text))
        except Exception as exc:
            button.setText(original_text)
            show_app_message(
                self,
                "LOT 작업순서 엑셀 내보내기 실패",
                f"엑셀 파일을 만들지 못했습니다.\n\n{exc}",
                kind="error",
            )
        finally:
            QApplication.restoreOverrideCursor()
            button.setEnabled(True)
