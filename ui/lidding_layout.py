from PySide6.QtCore import Qt, QTimer, QSize, QDate
from PySide6.QtGui import QColor
from ui.process_overview_page import Card, KpiCard, FILTER_BUTTON_SELECTION_STYLE
from ui.process_overview_page import ProcessOverviewPage
import qtawesome as qta
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit, QComboBox, QTextBrowser,
    QAbstractItemView, QButtonGroup, QDateEdit)

class LiddingLayout(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        filters_card = Card()
        filters = QHBoxLayout(filters_card)
        filters.setContentsMargins(16, 12, 16, 12)
        filters.setSpacing(9)
        self.progress = self.add_buttons(filters, '진행현황', ['전체', '해외', 'PB', '국내', '안전'])
        filters.addSpacing(14)
        label = QLabel('관별'); label.setObjectName('FilterLabel'); filters.addWidget(label)
        factory = QLabel('S관(3공장)'); factory.setObjectName('FixedFactoryChip')
        factory.setAlignment(Qt.AlignCenter); factory.setMinimumWidth(108); filters.addWidget(factory)
        filters.addStretch()
        label = QLabel('통합검색'); label.setObjectName('FilterLabel'); filters.addWidget(label)
        self.search = QLineEdit()
        self.search.setObjectName('SearchInput')
        self.search.setMinimumWidth(210)
        self.search.setMaximumWidth(320)
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText('리드지 코드 · 품명 · 연결 P코드 통합검색')
        filters.addWidget(self.search, 1)
        lookup = QPushButton('조회'); lookup.setObjectName('PrimaryButton')
        lookup.setIcon(qta.icon('fa6s.magnifying-glass', color='#FFFFFF'))
        lookup.clicked.connect(self.filter_rows); filters.addWidget(lookup)
        reset = QPushButton('필터 초기화'); reset.setObjectName('SecondaryButton')
        reset.setIcon(qta.icon('fa6s.arrow-rotate-left', color='#52677E'))
        reset.clicked.connect(self.reset_filters); filters.addWidget(reset)
        layout.addWidget(filters_card)
        kpis = QHBoxLayout(); kpis.setSpacing(10)
        self.kpis = []
        for title, color, value, hint in [
            ('조회 리드지', '#0A7AFF', '1종', ''),
            ('1.5배 목표 부족', '#EF4444', '1종', 'APS 생산필요 × 1.5 기준'),
            ('가용 3일 이하', '#F59E0B', '1종', '최근 7일 달력 기준 사용속도'),
            ('정상', '#10B981', '0종', '자료 부족은 정상에서 제외')]:
            card = KpiCard(title, color)
            card.value.setText(value); card.detail.setText(hint)
            self.kpis.append(card); kpis.addWidget(card)
        layout.addLayout(kpis)
        secondary = Card(); secondary_layout = QVBoxLayout(secondary)
        secondary_layout.setContentsMargins(14, 8, 14, 8); secondary_layout.setSpacing(8)
        due_row = QHBoxLayout(); due_row.setSpacing(8)
        self.due = self.add_buttons(due_row, '납기', ['해제', '직접', '당월', '+7일', '+14일'])
        self.due_end = QDateEdit(QDate.currentDate()); self.due_end.setObjectName('FilterInput')
        self.due_end.setCalendarPopup(True); self.due_end.setDisplayFormat('yyyy-MM-dd')
        self.due_end.setEnabled(False); self.due_end.dateChanged.connect(self.filter_rows)
        due_row.addWidget(self.due_end)
        self.local_search = QLineEdit(); self.local_search.setObjectName('SearchInput')
        self.local_search.setPlaceholderText('현재 결과 내: 리드지 코드·품명·연결 P코드')
        self.local_search.setClearButtonEnabled(True); due_row.addWidget(self.local_search, 1)
        secondary_layout.addLayout(due_row)
        riskrow = QHBoxLayout()
        riskrow.setSpacing(8)
        self.risk = self.add_buttons(riskrow, '리스크', ['전체 리드지', '리스크 관리', '1.5배 목표 부족', '가용 3일 이하', '정상'])
        riskrow.addStretch()
        secondary_layout.addLayout(riskrow)
        layout.addWidget(secondary)
        table_card = Card(); table_layout = QVBoxLayout(table_card)
        table_layout.setContentsMargins(16, 12, 16, 12)
        heading = QHBoxLayout()
        title = QLabel('리드지별 재고 현황'); title.setObjectName('CardTitle'); heading.addWidget(title)
        self.result = QLabel()
        self.result.setObjectName('CardSub'); heading.addStretch(); heading.addWidget(self.result)
        table_layout.addLayout(heading)
        headers = ['리드지 코드', '리드지 품명', '사용 P코드 품명', '현재고', 'P코드 생산필요', '1.5배 목표', '계획 후 잔여',
                   '최근 사용속도', '가용일수', 'APS 납기', '리스크 수주', '비고']
        self.table = QTableWidget(0, len(headers))
        self.table.setObjectName('DataTable')
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setMinimumSectionSize(64)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.setShowGrid(False)
        self.table.setMinimumHeight(190)
        self.table.cellClicked.connect(self.show_detail)
        table_layout.addWidget(self.table)
        layout.addWidget(table_card, 1)
        self.detail = QTextBrowser()
        self.detail.setMinimumHeight(210)
        self.detail.setStyleSheet('QTextBrowser { background:white; border:1px solid #E2E7ED; border-radius:14px; padding:16px; color:#173655; }')
        self.detail.setHtml('<h3>리드지 행을 선택해 주세요</h3><p>연결 P코드 · 불출 추이 · 위험 근거를 이 영역에서 확인합니다.</p>')
        layout.addWidget(self.detail, 1)
        self.search.textChanged.connect(self.filter_rows)
        self.local_search.textChanged.connect(self.filter_rows)
        self.filter_rows()

    def add_buttons(self, layout, title, items):
        label = QLabel(title); label.setObjectName('FilterLabel'); layout.addWidget(label)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for index, name in enumerate(items):
            button = QPushButton(name); button.setObjectName('FilterButton')
            button.setStyleSheet(FILTER_BUTTON_SELECTION_STYLE)
            button.setCheckable(True); button.setChecked(index == 0)
            group.addButton(button, index); layout.addWidget(button)
        group.buttonClicked.connect(self.filter_rows)
        return group
