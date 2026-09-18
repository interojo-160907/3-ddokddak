from __future__ import annotations

import qtawesome as qta
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton

from services.live_production_need_service import LiveProductionNeedService
from ui.process_overview_page import ProcessOverviewPage, _repolish


def _display_time(value: object) -> str:
    text = str(value or "").strip().replace("T", " ")
    if "+" in text:
        text = text.split("+", 1)[0]
    return text[:19] or "-"


def _clock_time(value: object) -> str:
    display = _display_time(value)
    return display[11:16] if len(display) >= 16 else display


def _date_clock_time(value: object) -> str:
    display = _display_time(value)
    return display[:16] if len(display) >= 16 else display


class LiveProductionNeedPage(ProcessOverviewPage):
    """APS 공정 화면과 같은 UI에 실적 반영 결과만 연결한 화면."""

    refresh_requested = Signal()

    PROCESS_NAV_KEYS = {
        "사출": "live_injection",
        "분리": "live_separation",
        "하이드레이션": "live_hydration",
        "접착": "live_inspection",
        "누수규격": "live_leak",
    }

    def __init__(self, parent=None, *, fixed_process: str | None = None) -> None:
        # APS 화면과 필터·표시·동작을 공유하고 데이터 서비스만 아래에서 교체한다.
        super().__init__(
            parent,
            fixed_process=fixed_process,
            initial_rows=[],
            monitor_changes=False,
            include_packaging=False,
        )
        self.service = LiveProductionNeedService()
        self._refreshing = False

        self.live_refresh_button = QPushButton("지금 갱신", self)
        self.live_refresh_button.setObjectName("LiveRefreshButton")
        self.live_refresh_button.setIcon(
            qta.icon("fa6s.magnifying-glass", color="#FFFFFF")
        )
        self.live_refresh_button.setFixedHeight(30)
        self.live_refresh_button.setMinimumWidth(90)
        self.live_refresh_button.setToolTip(
            "현재 5개 공정창고와 완료 생산실적을 다시 수집해 계산합니다."
        )
        self.live_refresh_button.clicked.connect(self._request_refresh)
        self.live_refresh_button.setVisible(fixed_process is None)

        self.calculation_status = QLabel("첫 계산 전", self)
        self.calculation_status.setObjectName("LiveCalculationStatus")
        self.calculation_status.setMinimumHeight(30)
        self.calculation_status.setVisible(fixed_process is None)

        self.kpi_all.clicked.disconnect()
        self.kpi_all.clicked.connect(lambda: self.process_requested.emit("live_need"))
        self.kpi_all.set_clickable(True, "실시간 실적 반영 전체 보기")
        for process_name, card in self.process_kpis.items():
            card.clicked.disconnect()
            card.clicked.connect(
                lambda selected=process_name: self.process_requested.emit(
                    self.PROCESS_NAV_KEYS[selected]
                )
            )
            card.set_clickable(True, f"{process_name} 공정만 보기")

        self.reload_data()

    def _export_excel(self) -> None:
        if self.fixed_process:
            self._export_process_excel(self.fixed_process, filename_tag="실적반영")
        else:
            self._export_overview_excel(filename_tag="실적반영")

    def _request_refresh(self) -> None:
        if self._refreshing:
            return
        self.set_refreshing(True)
        self.refresh_requested.emit()

    def set_refreshing(self, refreshing: bool) -> None:
        self._refreshing = refreshing
        self.live_refresh_button.setEnabled(not refreshing)
        self.live_refresh_button.setText("계산 중…" if refreshing else "지금 갱신")
        if refreshing:
            self.calculation_status.setText("새 APS 기준 WIP·재고·실적 수집 중")
            self.calculation_status.setProperty("status", "warning")
            self.calculation_status.setToolTip(
                "최신 APS 회차와 연결되는 WIP를 확인한 뒤 재고·완료실적을 수집해 계산합니다."
            )
            _repolish(self.calculation_status)

    def reload_data(self) -> None:
        super().reload_data()
        self._show_cycle_status()

    def _apply_market_view(self) -> None:
        super()._apply_market_view()
        if isinstance(getattr(self, "service", None), LiveProductionNeedService):
            self._show_cycle_status()

    def _show_cycle_status(self) -> None:
        status = self.service.status()
        aps_time = _display_time(status.get("aps_source_refreshed_at"))
        calculated = _display_time(status.get("refreshed_at"))
        state = str(status.get("status") or "")
        target = self.calculation_status
        if state in {"retained", "waiting_wip"}:
            attempted_aps = _display_time(
                status.get("attempted_aps_source_refreshed_at")
            )
            if state == "waiting_wip" and attempted_aps != "-":
                target.setText(
                    f"새 APS {_clock_time(attempted_aps)} 감지 · WIP 준비 중 · "
                    f"기존 {_clock_time(aps_time)} 계산 유지"
                )
            else:
                target.setText(f"새 회차 계산 보류 · 기존 {_clock_time(aps_time)} 계산 유지")
            target.setProperty("status", "warning")
            target.setToolTip(
                f"{status.get('retained_reason') or '마지막 정상 계산을 유지합니다.'}\n"
                f"현재 표시 APS 기준 {aps_time}\n"
                f"감지한 새 APS {attempted_aps}"
            )
        elif state == "success":
            target.setText(f"계산 완료  {_date_clock_time(calculated)}")
            target.setProperty("status", "success")
            target.setToolTip(
                f"APS 기준 {aps_time}\n"
                f"재고·완료실적 계산 {calculated}\n"
                f"인정 완료수량 {float(status.get('recognized_qty') or 0):,.0f} pcs"
            )
        else:
            target.setText("첫 계산 전")
            target.setProperty("status", "warning")
            target.setToolTip(
                "지금 갱신을 누르면 현재 APS 회차를 기준으로 계산을 시작합니다."
            )
        target.setVisible(self.fixed_process is None)
        _repolish(target)

    def _update_kpis(self, rows: list[dict]) -> None:
        super()._update_kpis(rows)
        for card in self.process_kpis.values():
            detail = card.detail.text().replace(" · 클릭하여 이동", "")
            card.detail.setText(detail)
