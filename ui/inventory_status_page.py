from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import date,datetime,timedelta
from pathlib import Path
from PySide6.QtCore import QAbstractTableModel,QDate,Qt,QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QCheckBox,QComboBox,QDateEdit,QLineEdit,QTabBar,QTableView,QHeaderView,QFileDialog,QMessageBox
from services.inventory_status_service import InventoryStatusService,HEADERS,export_inventory

class InventoryModel(QAbstractTableModel):
    def __init__(self):super().__init__();self.rows=[];self.hydration_meta={}
    def rowCount(self,parent=None):return len(self.rows)
    def columnCount(self,parent=None):return len(HEADERS)
    def headerData(self,section,orientation,role=Qt.DisplayRole):
        if role==Qt.DisplayRole:return HEADERS[section] if orientation==Qt.Horizontal else section+1
    def data(self,index,role=Qt.DisplayRole):
        if not index.isValid():return None
        row,col=index.row(),index.column();v=self.rows[row][col]
        if role==Qt.UserRole:return v
        if role in (Qt.BackgroundRole,Qt.ForegroundRole) and getattr(self,'app_style',False):return None
        if role==Qt.DisplayRole:
            return ('-' if not v else f'{v:,.0f}') if isinstance(v,(int,float)) else str(v or '')
        if role==Qt.TextAlignmentRole:return int(Qt.AlignVCenter|(Qt.AlignRight if 6<=col<=12 else Qt.AlignCenter if col in (0,2,13) else Qt.AlignLeft))
        if role==Qt.BackgroundRole:
            if col==1:return QColor('#DEFFB4')
            if col==2:return QColor('#C9E7F5')
            if col in (11,12) and isinstance(v,(int,float)) and v>0:return QColor('#FFF0F0')
            return QColor('#FFFFFF' if row%2==0 else '#F6F6F6')
        if role==Qt.ForegroundRole:return QColor('#B91C1C' if col in (11,12) and isinstance(v,(int,float)) and v>0 else '#111827')
        if role==Qt.ToolTipRole:
            if col==8:
                if self.hydration_meta.get('status')=='success':return f"{float(v or 0):,.0f}\n수화 지시 API 기준: {self.hydration_meta.get('captured_at','-')}"
                return f'{v or 0}\n수화 지시목록 미확보: 임시 0 표시. 실제 지시량 0을 확인한 값이 아닙니다.'
            if col==11:return f"{float(v or 0):,.0f}\n완제품 부족 - 누수규격검사 - 검사접착 - 수화 지시량 (최소 0)"
            if col==12:return f"{float(v or 0):,.0f}\n실시간 실적 반영 후 누수·규격 공정(80) 잔여 부족량"
            return f'{v:,.0f}' if isinstance(v,(int,float)) else str(v or '')
    def load(self,rows):self.beginResetModel();self.rows=rows;self.endResetModel()

class InventoryStatusPage(QWidget):
    def __init__(self,filter_provider,parent=None):
        super().__init__(parent);self.filter_provider=filter_provider;self.service=InventoryStatusService();self.result=None;self.future=None;self.extra={};self.pending=None
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='inventory-view')
        root=QVBoxLayout(self);root.setContentsMargins(0,0,0,0);root.setSpacing(10)
        top=QHBoxLayout();top.addWidget(QLabel('진행현황'));self.markets={}
        for name in ('전체','해외','PB','국내','안전'):
            b=QPushButton(name);b.setCheckable(True);b.setChecked(name=='전체');b.setObjectName('FilterButton');b.clicked.connect(lambda checked,n=name:self.change_market(n));self.markets[name]=b;top.addWidget(b)
        top.addWidget(QLabel('S관(3공장)'));top.addStretch()
        self.search=QLineEdit();self.search.setPlaceholderText('수주·생산품명·P코드 검색 / 쉼표 OR');self.search.setClearButtonEnabled(True);self.search.returnPressed.connect(self.reload);top.addWidget(self.search)
        for title,callback in [('조회',self.reload),('메인 필터 적용',self.inherit_filters),('초기화',self.reset_filters)]:
            b=QPushButton(title);b.setObjectName('SecondaryButton');b.clicked.connect(callback);top.addWidget(b)
        self.export_button=QPushButton('엑셀 내보내기');self.export_button.setObjectName('SecondaryButton');self.export_button.clicked.connect(self.export);top.addWidget(self.export_button);root.addLayout(top)
        second=QHBoxLayout();second.addWidget(QLabel('납기'));self.due=QComboBox();self.due.addItems(['해제','직접','당월','+7일','+14일']);second.addWidget(self.due)
        self.due_date=QDateEdit(QDate.currentDate());self.due_date.setCalendarPopup(True);self.due_date.setDisplayFormat('yyyy-MM-dd');second.addWidget(self.due_date)
        self.process=QComboBox()
        for name,code in [('전체 공정','전체'),('사출','10'),('분리','20'),('하이드레이션','45'),('검사접착','55'),('누수규격','80')]:self.process.addItem(name,code)
        second.addWidget(self.process)
        self.codes=QCheckBox('R·Q·P코드 표시');self.codes.toggled.connect(self.toggle_codes);second.addWidget(self.codes)
        self.refresh_button=QPushButton('ERP 재고 갱신');self.refresh_button.setObjectName('SecondaryButton');self.refresh_button.clicked.connect(lambda:self.reload(refresh=True));second.addWidget(self.refresh_button)
        second.addStretch();self.filter_label=QLabel('전체 수주 · 납기 제한 없음');second.addWidget(self.filter_label);root.addLayout(second)
        self.notice=QLabel('수화 지시량: 미확보분 임시 0(-)  |  부족제품의 전체 파워 표시  |  공용 Q/R 재고 반복 표시')
        self.notice.setWordWrap(True);self.notice.setStyleSheet('background:#FFF6DD;color:#714B0A;padding:8px;border:1px solid #E3CCA0;');root.addWidget(self.notice)
        self.status=QLabel('조회 준비');self.status.setWordWrap(True);root.addWidget(self.status)
        self.tabs=QTabBar();self.tabs.setExpanding(False);self.tabs.setUsesScrollButtons(True);self.tabs.currentChanged.connect(self.display_tab);root.addWidget(self.tabs)
        self.table=QTableView();self.model=InventoryModel();self.table.setModel(self.model);self.table.setWordWrap(False);self.table.setSortingEnabled(False)
        self.table.setStyleSheet('QTableView {background:white;gridline-color:#C8C8C8;font-size:12px;color:#111827;} QHeaderView::section {background:#F4C8EF;color:#111111;font-weight:bold;border:1px solid #AFAFAF;padding:6px;}')
        self.table.verticalHeader().setDefaultSectionSize(25);self.table.horizontalHeader().setMinimumHeight(48);self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for c,width in enumerate([340,190,220,220,220,105,105,110,105,120,120,125,125,110]):self.table.setColumnWidth(c,width)
        root.addWidget(self.table,1);self.toggle_codes(False)
        self.timer=QTimer(self);self.timer.setInterval(120);self.timer.timeout.connect(self.finish)
        self.export_button.setEnabled(False)

    def change_market(self,name):
        if name=='전체':
            for n,b in self.markets.items():b.setChecked(n=='전체')
        else:self.markets['전체'].setChecked(not any(b.isChecked() for n,b in self.markets.items() if n!='전체'))
        self.reload()

    def toggle_codes(self,checked):
        for c in (3,4,5):self.table.setColumnHidden(c,not checked)

    def filters(self):
        f=dict(self.extra);f.update(markets=[n for n,b in self.markets.items() if b.isChecked()],search=self.search.text().strip(),process=self.process.currentData())
        today=date.today();mode=self.due.currentText();limit=None
        if mode=='직접':limit=self.due_date.date().toPython()
        elif mode=='당월':limit=(today.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)
        elif mode in ('+7일','+14일'):limit=today+timedelta(days=7 if mode=='+7일' else 14)
        f['due']=limit.isoformat() if limit else None
        return f

    def inherit_filters(self):
        f=self.filter_provider();self.extra={k:v for k,v in f.items() if k in ('classes','detail_search')}
        for n,b in self.markets.items():b.setChecked(n in f.get('markets',['전체']))
        self.search.setText(f.get('search',''));self.process.setCurrentIndex(max(0,self.process.findData(f.get('process','전체'))))
        self.due.setCurrentText('직접' if f.get('due') else '해제')
        if f.get('due'):self.due_date.setDate(QDate.fromString(f['due'],'yyyy-MM-dd'))
        self.filter_label.setText('메인 필터 적용 · 분류: '+', '.join(f.get('classes') or ['전체']))
        self.reload()

    def reset_filters(self):
        self.extra={};self.search.clear();self.due.setCurrentText('해제');self.process.setCurrentIndex(0)
        for n,b in self.markets.items():b.setChecked(n=='전체')
        self.filter_label.setText('전체 수주 · 납기 제한 없음');self.reload()

    def reload(self,_checked=False,*,refresh=False):
        request=(self.filters(),refresh)
        if self.future is not None:self.pending=request;return
        self._start(request)

    def _start(self,request):
        f,refresh=request;self.status.setText('ERP 재고 수집 중…' if refresh else '부족제품과 전체 규격 조회 중…')
        self.export_button.setEnabled(False);self.refresh_button.setEnabled(False)
        def work():
            if refresh:self.service.refresh_stock()
            return self.service.build(f)
        self.future=self.executor.submit(work);self.timer.start()

    def finish(self):
        if self.future is None or not self.future.done():return
        future=self.future;self.future=None;self.timer.stop();self.refresh_button.setEnabled(True)
        if self.pending is not None:
            request=self.pending;self.pending=None
            try:future.result()
            except Exception:pass
            self._start(request);return
        try:result=future.result()
        except Exception as exc:
            self.status.setText('조회 실패: '+str(exc));self.result=None;self.model.load([]);return
        self.result=result;prior=self.tabs.tabText(self.tabs.currentIndex());self.tabs.blockSignals(True)
        while self.tabs.count():self.tabs.removeTab(0)
        for s in result['sheets']:self.tabs.addTab(s['name'])
        index=next((i for i,s in enumerate(result['sheets']) if s['name']==prior),0)
        self.tabs.setCurrentIndex(index);self.tabs.blockSignals(False);self.display_tab(index)
        self.status.setText(f"{result['products']:,}제품 · 전체 규격 {result['rows']:,}행 · 완제품 부족 {result['total80']:,.0f} · 연결 미확인 {result['unmapped']}행\n부족 기준 {result['cycle'].get('current_captured_at','-')} / 재고 {' ~ '.join(result['inventory_times'])}")
        self.export_button.setEnabled(bool(result['rows']))

    def display_tab(self,index):
        if not self.result or not 0<=index<len(self.result['sheets']):self.model.load([]);return
        self.model.load([r for p in self.result['sheets'][index]['products'] for r in p['rows']])

    def export(self):
        if not self.result:return
        path,_=QFileDialog.getSaveFileName(self,'재고 현황 엑셀 저장',str(Path.home()/'Desktop'/('생산3팀_재고현황_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.xlsx')),'Excel (*.xlsx)')
        if not path:return
        try:export_inventory(self.result,path)
        except Exception as exc:QMessageBox.warning(self,'저장 실패',str(exc));return
        QMessageBox.information(self,'저장 완료','현재 조회 조건의 전체 분류를 저장했습니다.\n'+path)
