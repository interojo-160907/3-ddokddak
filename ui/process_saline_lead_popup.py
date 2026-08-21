from __future__ import annotations

import re

import qtawesome as qta
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.bom_page import BomStatusPage


class ProcessSalineLeadPopup(QDialog):
    """Right-side saline/lead detail card reused from the BOM lookup page."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("ProcessSalineLeadPopup")
        self.setMinimumSize(760, 680)
        self.setStyleSheet(
            "QDialog#ProcessSalineLeadPopup {"
            " background: #f7faff; border: 1px solid #b8d5fa; border-radius: 14px;"
            "}"
            "QWidget#ProcessSalineLeadPopupHeader {"
            " background: #eef6ff; border-bottom: 1px solid #d5e5f7;"
            "}"
            "QLabel#ProcessSalineLeadPopupTitle {"
            " color: #102a43; font-size: 15px; font-weight: 700;"
            "}"
            "QToolButton#ProcessSalineLeadPopupClose {"
            " background: #ffffff; border: 1px solid #cbdcf0; border-radius: 7px;"
            " padding: 5px;"
            "}"
            "QToolButton#ProcessSalineLeadPopupClose:hover {"
            " background: #e7f2ff; border-color: #0a7aff;"
            "}"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        header = QWidget(self)
        header.setObjectName("ProcessSalineLeadPopupHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 10, 10)
        header_layout.setSpacing(8)
        icon = QLabel(header)
        icon.setPixmap(qta.icon("fa5s.link", color="#0a7aff").pixmap(18, 18))
        header_layout.addWidget(icon)
        self.title_label = QLabel("식염수 리드지 조회", header)
        self.title_label.setObjectName("ProcessSalineLeadPopupTitle")
        header_layout.addWidget(self.title_label, 1)
        close_button = QToolButton(header)
        close_button.setObjectName("ProcessSalineLeadPopupClose")
        close_button.setIcon(qta.icon("fa5s.times", color="#52667a"))
        close_button.setToolTip("닫기")
        close_button.clicked.connect(self.close)
        header_layout.addWidget(close_button)
        root.addWidget(header)

        self.page = BomStatusPage(parent=self)
        self.page.hide()
        self.page.inner_tabs.setCurrentIndex(3)
        saline_tab = self.page.inner_tabs.widget(3)
        saline_tab.setParent(self)
        left_panel = self.page.saline_lead_table.parentWidget()
        if left_panel is not None:
            left_panel.hide()
        root.addWidget(saline_tab, 1)
        # The tab was a child of the hidden BomStatusPage. Reparenting alone
        # keeps its explicit hidden state, leaving only the popup header visible.
        saline_tab.show()

    @staticmethod
    def base_product_code(value: str) -> str:
        match = re.match(r"\s*(P\d{4})", str(value or ""), re.IGNORECASE)
        return match.group(1).upper() if match else ""

    def show_product(self, product_code: str, product_name: str = "") -> None:
        base_code = self.base_product_code(product_code)
        if not base_code:
            return
        self.title_label.setText(
            f"식염수 리드지 조회  ·  {base_code}  {str(product_name or '').strip()}"
        )
        self.page._show_saline_lead_details(base_code)
        parent = self.parentWidget()
        if parent is not None:
            top_left = parent.mapToGlobal(parent.rect().topLeft())
            width = min(900, max(760, int(parent.width() * 0.48)))
            height = min(860, max(680, parent.height() - 90))
            self.resize(width, height)
            self.move(
                top_left.x() + parent.width() - width - 18,
                top_left.y() + 64,
            )
        self.show()
        self.raise_()
