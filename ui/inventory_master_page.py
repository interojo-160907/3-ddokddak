from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import json
import os
from PySide6.QtCore import QDate,QRect,Qt,QTimer
from PySide6.QtGui import QColor,QFont,QPainter,QPen
from PySide6.QtWidgets import QLabel,QTabBar,QTableView,QHeaderView,QFrame,QVBoxLayout,QAbstractItemView
from services.inventory_status_service import InventoryStatusService,export_inventory
from ui.inventory_status_page import InventoryModel
from ui.live_production_need_page import LiveProductionNeedPage

class InventoryHeader(QHeaderView):
    GROUPS=((3,5,'제품 코드'),(6,10,'재고'),(11,12,'부족수량'))
    ROW_SPAN={0,1,2,13}
    GROUP_HEIGHT=24
    def __init__(self,parent=None,reference_header=None):
        super().__init__(Qt.Horizontal,parent);self.setFixedHeight(62);self.setDefaultAlignment(Qt.AlignCenter)
        font=QFont(reference_header.font()) if reference_header is not None else self.font()
        font.setPixelSize(11);font.setWeight(QFont.Weight.ExtraBold);self.setFont(font);self.setMinimumSectionSize(54)
    def paintSection(self,painter,rect,logicalIndex):
        if not rect.isValid():return
        painter.save()
        if logicalIndex in self.ROW_SPAN:
            painter.fillRect(rect,QColor('#E7EEF8'));painter.setPen(QPen(QColor('#E2E7ED')));painter.drawRect(rect.adjusted(0,0,-1,-1));painter.setPen(QColor('#52627A'))
            text=str(self.model().headerData(logicalIndex,Qt.Horizontal,Qt.DisplayRole) or '');painter.drawText(rect.adjusted(5,2,-5,-2),Qt.AlignCenter|Qt.TextWordWrap,text);painter.restore();return
        bottom=QRect(rect.x(),rect.y()+self.GROUP_HEIGHT,rect.width(),rect.height()-self.GROUP_HEIGHT)
        painter.fillRect(bottom,QColor('#F5F7FA'));painter.setPen(QPen(QColor('#E2E7ED')));painter.drawRect(bottom.adjusted(0,0,-1,-1))
        painter.setPen(QColor('#52627A'));text=str(self.model().headerData(logicalIndex,Qt.Horizontal,Qt.DisplayRole) or '')
        painter.drawText(bottom.adjusted(5,2,-5,-2),Qt.AlignCenter|Qt.TextWordWrap,text)
        painter.restore()

    def paintEvent(self,event):
        super().paintEvent(event)
        painter=QPainter(self.viewport())
        for start,end,label in self.GROUPS:
            visible=[i for i in range(start,end+1) if not self.isSectionHidden(i)]
            if not visible:continue
            x=self.sectionViewportPosition(visible[0]);width=sum(self.sectionSize(i) for i in visible)
            rect=QRect(x,0,width,self.GROUP_HEIGHT);fill='#E5F3EF' if label=='재고' else '#F7E8EB' if label=='부족수량' else '#EEF3F9'
            painter.fillRect(rect,QColor(fill));painter.setPen(QPen(QColor('#E2E7ED')));painter.drawRect(rect.adjusted(0,0,-1,-1));painter.setPen(QColor('#52627A'));painter.drawText(rect,Qt.AlignCenter,label)
        painter.end()

    def clipboard_header_rows(self,columns):
        top=[];bottom=[]
        for col in columns:
            label=str(self.model().headerData(col,Qt.Horizontal,Qt.DisplayRole) or '')
            if col in self.ROW_SPAN:
                top.append(label);bottom.append('');continue
            group=next((g for g in self.GROUPS if g[0]<=col<=g[1]),None)
            visible=[i for i in range(group[0],group[1]+1) if i in columns] if group else []
            top.append(group[2] if group and visible and col==visible[0] else '')
            bottom.append(label)
        return [top,bottom]

class InventoryStatusPage(LiveProductionNeedPage):
    """Same master filters and KPI cards; only the detail table is replaced."""
    def __init__(self,filter_provider,parent=None):
        self.inventory_ready=False
        super().__init__(parent)
        self.filter_provider=filter_provider;self.inventory_service=InventoryStatusService();self.result=None;self.future=None;self.pending=None
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='inventory-detail')
        self.debounce=QTimer(self);self.debounce.setSingleShot(True);self.debounce.setInterval(200);self.debounce.timeout.connect(self.rebuild)
        self.poll=QTimer(self);self.poll.setInterval(150);self.poll.timeout.connect(self.finish)
        detail=self.detail_page;detail.table.hide();detail.pagination_bar.hide()
        # Inventory classification is a single local view filter. Do not rebuild
        # APS/BOM/inventory joins when only this button changes.
        detail._classification_changed=self._inventory_classification_changed
        detail.layout().removeWidget(detail.pagination_bar);detail.pagination_bar.setFixedHeight(0)
        detail.name_basis.currentIndexChanged.disconnect();detail.name_basis.clear();detail.name_basis.addItem('기본 정렬','default');detail.name_basis.addItem('납기일 빠른 순','due');detail.name_basis.setEnabled(True);detail.name_basis.setMinimumWidth(145);detail.name_basis.currentIndexChanged.connect(self.sort_changed)
        detail.summary_mode.clear()
        for text in ('해제','수화 부족','완제품 부족'):detail.summary_mode.addItem(text,'detail')
        detail.summary_mode.setEnabled(True);detail.summary_mode.setMinimumWidth(125)
        detail.summary_mode.setToolTip('해제: 전체 파워 / 수화 부족: 수화 부족량 > 0 / 완제품 부족: 완제품 부족량 > 0. 상단 진행현황 조건을 먼저 적용합니다.')
        detail.summary_mode.currentIndexChanged.connect(self.compact_view)
        detail.code_checks['T코드'].setEnabled(False)
        for key,check in detail.code_checks.items():check.setChecked(False);check.toggled.connect(self.code_visibility)
        self.card=QFrame();self.card.setObjectName('Card');layout=QVBoxLayout(self.card);layout.setContentsMargins(14,12,14,12)
        title=QLabel('납기별 상세 · 재고 현황');title.setStyleSheet('font-weight:bold;color:#172B4D;');layout.addWidget(title)
        self.inventory_status=QLabel('조회 준비');self.inventory_status.setWordWrap(True);layout.addWidget(self.inventory_status)
        self.notice=QLabel('수화 지시 미연결: 임시 0(-) · 부족제품 전체 파워 · 공용 Q/R 재고 반복 표시')
        self.notice.setStyleSheet('color:#805200;background:#FFF6DF;padding:5px;');self.notice.setWordWrap(True);layout.addWidget(self.notice)
        self.tabs=QTabBar();self.tabs.setExpanding(False);self.tabs.setUsesScrollButtons(True);self.tabs.currentChanged.connect(self.display_tab);layout.addWidget(self.tabs)
        self.inventory_table=QTableView();self.model=InventoryModel();self.inventory_table.setModel(self.model);self.inventory_table.setHorizontalHeader(InventoryHeader(self.inventory_table,detail.table.table.horizontalHeader()));self.inventory_table.setWordWrap(False)
        self.inventory_table.setObjectName('DataTable');self.model.app_style=True;self.inventory_table.setTextElideMode(Qt.ElideRight)
        self.inventory_table.setAlternatingRowColors(True);self.inventory_table.setPalette(detail.table.table.palette());self.inventory_table.viewport().setPalette(detail.table.table.palette())
        self.inventory_table.setSelectionBehavior(QAbstractItemView.SelectRows);self.inventory_table.setSelectionMode(QAbstractItemView.SingleSelection);self.inventory_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.inventory_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inventory_table.verticalHeader().hide();self.inventory_table.verticalHeader().setDefaultSectionSize(34);self.inventory_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.inventory_column_widths={0:125,1:320,2:108,3:155,4:155,5:155,6:82,7:82,8:94,9:86,10:104,11:110,12:110,13:96}
        self.inventory_minimum_widths={0:105,1:200,2:100,3:115,4:115,5:115,6:65,7:65,8:82,9:72,10:88,11:90,12:90,13:82}
        for i,width in self.inventory_column_widths.items():self.inventory_table.setColumnWidth(i,width)
        layout.addWidget(self.inventory_table,1);detail.layout().addWidget(self.card,1)
        self.inventory_page_index=0;self.inventory_page_size=3000;self.visible_rows=[]
        detail.pagination_bar.setFixedHeight(42);detail.layout().addWidget(detail.pagination_bar);detail.pagination_bar.show()
        detail.previous_page_button.clicked.disconnect();detail.next_page_button.clicked.disconnect()
        detail.previous_page_button.clicked.connect(lambda:self.change_inventory_page(-1));detail.next_page_button.clicked.connect(lambda:self.change_inventory_page(1))
        # Inventory has one classification filter and one continuous detail table.
        for card in [self.kpi_all,*self.process_kpis.values()]:card.hide()
        for button in detail.process_buttons:button.setChecked(button.property('processName')=='전체');button.hide()
        detail.code_checks['T코드'].hide()
        for label in detail.findChildren(QLabel):
            if label.text()=='공정 보기' or label.text().startswith('정렬:'):label.hide()
            elif label.text()=='품명 기준':label.setText('정렬')
        for widget in (title,self.inventory_status,self.notice,self.tabs):widget.hide()
        layout.setContentsMargins(8,8,8,8);layout.setSpacing(0)
        self.inventory_ready=True;self.code_visibility();self.fit_inventory_columns();self.export_button.setToolTip('진행현황·납기·신규분류요약 조건의 전체 페이지를 바탕화면에 저장합니다.');self.live_refresh_button.hide();self.calculation_status.hide();self.rebuild()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if getattr(self,'inventory_ready',False):QTimer.singleShot(0,self.fit_inventory_columns)

    def fit_inventory_columns(self):
        if not hasattr(self,'inventory_table'):return
        table=self.inventory_table;available=max(1,table.viewport().width()-2)
        visible=[c for c in range(self.model.columnCount()) if not table.isColumnHidden(c)]
        if not visible:return
        minimum={c:self.inventory_minimum_widths[c] for c in visible}
        min_total=sum(minimum.values())
        if available<min_total:
            # A narrow window still stays on one screen. Text is elided and the
            # model tooltip exposes the full cell value on hover.
            ratio=available/min_total
            widths={c:max(44,int(minimum[c]*ratio)) for c in visible}
        else:
            widths=dict(minimum);remaining=available-min_total
            gaps={c:max(0,self.inventory_column_widths[c]-widths[c]) for c in visible}
            gap_total=sum(gaps.values());fill=min(remaining,gap_total)
            if gap_total:
                for c in visible:widths[c]+=int(fill*gaps[c]/gap_total)
                used=sum(widths.values())-min_total
                for c in visible:
                    if used>=fill:break
                    if widths[c]<self.inventory_column_widths[c]:widths[c]+=1;used+=1
            remaining=available-sum(widths.values())
            if remaining>0:
                weights={c:(8 if c==1 else 2 if c in (3,4,5) else 1) for c in visible}
                total_weight=sum(weights.values())
                for c in visible:widths[c]+=remaining*weights[c]//total_weight
        # Absorb integer rounding so the last visible column ends exactly at
        # the viewport edge, with no blank strip and no horizontal scrollbar.
        anchor=1 if 1 in widths else visible[-1]
        widths[anchor]+=available-sum(widths.values())
        for c in visible:table.setColumnWidth(c,max(1,widths[c]))
        table.horizontalScrollBar().setValue(0)

    def _show_cycle_status(self):
        super()._show_cycle_status()
        if not getattr(self,'inventory_ready',False):
            self.calculation_status.hide();self.live_refresh_button.hide();return
        if getattr(self,'_refreshing',False):return
        try:report=json.loads((self.inventory_service.cache/'refresh_status.json').read_text('utf-8'))
        except (OSError,ValueError):report={}
        if report.get('status') != 'success':
            self.calculation_status.setText('일부 수집 대기 · 기존 결과 유지' if self.result else '최초 재고 수집 대기')
            self.calculation_status.setToolTip('수집 완료 후 자동으로 표시됩니다. '+str(report.get('result',{}).get('message','')))

    def _update_kpis(self,rows):
        super()._update_kpis(rows)
        if getattr(self,'inventory_ready',False):
            self.detail_page.table.hide();self.debounce.start()

    def filters(self):
        d=self.detail_page;limit=d._due_limit()
        return {'markets':[str(b.property('market')) for b in self.market_buttons if b.isChecked()],
                'search':self.search.text(),'detail_search':d.search.text(),'classes':['전체'],
                'due':limit.isoformat() if limit else None,'process':'전체'}

    def _select_single_classification(self,preferred=None):
        buttons=self.detail_page.classification_buttons
        wanted=[str(x) for x in (preferred or []) if str(x) in buttons and str(x)!='전체']
        selected=wanted[0] if wanted else '전체'
        for name,button in buttons.items():button.setChecked(name==selected)
        return selected

    def _inventory_classification_changed(self,selected_button):
        category=str(selected_button.property('classification') or '전체')
        # Clicking the selected button again keeps one valid choice.
        self._select_single_classification([category])
        if hasattr(self,'model'):self.display_tab(0)

    def inherit_filters(self):
        f=self.filter_provider();self._inherited_filters=f
        for b in self.market_buttons:b.setChecked(str(b.property('market')) in (f.get('markets') or ['전체']))
        self.search.setText(f.get('search',''));d=self.detail_page;d.search.setText(f.get('detail_search',''))
        for b in d.due_buttons:b.setChecked(b.property('dueMode')==('직접' if f.get('due') else '해제'))
        if f.get('due'):d.due_end.setDate(QDate.fromString(f['due'],'yyyy-MM-dd'))
        d.due_end.setEnabled(bool(f.get('due')))
        process='전체'
        for b in d.process_buttons:b.setChecked(b.property('processName')==process)
        self._apply_market_view()
        self._select_single_classification(f.get('classes') or ['전체'])
        d._apply_filter();self.debounce.start()

    def code_visibility(self,*_):
        if not hasattr(self,'inventory_table'):return
        for c,key in ((3,'R코드'),(4,'Q코드'),(5,'P코드')):self.inventory_table.setColumnHidden(c,not self.detail_page.code_checks[key].isChecked())
        self.fit_inventory_columns()

    def rebuild(self):
        request=self.filters()
        if self.future is not None:self.pending=request;return
        self._start(request)

    def _start(self,request):
        self._active_request=request
        self.inventory_status.setText('부족제품 및 전체 규격 조회 중…');self.export_button.setEnabled(False)
        self.future=self.executor.submit(self.inventory_service.build,request);self.poll.start()

    def finish(self):
        if self.future is None or not self.future.done():return
        future=self.future;self.future=None;self.poll.stop()
        if self.pending is not None:
            request=self.pending;self.pending=None
            try:future.result()
            except Exception:pass
            self._start(request);return
        try:result=future.result()
        except Exception as exc:
            same=self.result and self.inventory_service._filter_key(self.result.get('filters'))==self.inventory_service._filter_key(self._active_request)
            self.inventory_status.setText(('기존 결과 유지 · ' if same else '조회 대기 · ')+str(exc));self.inventory_status.show()
            if not same:self.result=None;self.model.load([])
            self.export_button.setEnabled(bool(same));self._show_cycle_status();return
        self.inventory_status.hide()
        self.model.hydration_meta=result.get('hydration',{})
        self.result=result;prior=self.tabs.tabText(self.tabs.currentIndex());self.tabs.blockSignals(True)
        self._show_cycle_status()
        while self.tabs.count():self.tabs.removeTab(0)
        for s in result['sheets']:self.tabs.addTab(s['name'])
        idx=next((i for i,s in enumerate(result['sheets']) if s['name']==prior),0);self.tabs.setCurrentIndex(idx);self.tabs.blockSignals(False);self.display_tab(idx)
        self.inventory_status.setText(f"{result['products']:,}제품 · {result['rows']:,}규격 · 완제품 부족 {result['total80']:,.0f} · 연결 미확인 {result['unmapped']}행\n부족 기준 {result['cycle'].get('current_captured_at','-')} / 재고 {' ~ '.join(result['inventory_times'])}")
        self.export_button.setEnabled(bool(result['rows']));self.detail_page.table.hide();self.detail_page.pagination_bar.show()
        if result.get('_retained'):
            self.inventory_status.setText('갱신 중 · 마지막 정상 재고 결과 표시');self.inventory_status.show()
        if not getattr(self,'_stock_warm_started',False):
            self._stock_warm_started=True;self.executor.submit(self.inventory_service.warm_source_cache)
        state=self.inventory_service.cache/'refresh_status.json'
        if state.exists():
            report=json.loads(state.read_text('utf-8'));failed=[k for k,v in report.get('sources',{}).items() if v.get('status')!='success']
            if report.get('live',{}).get('status')!='success':failed.append('실시간 계산(이전값 유지)')
            if report.get('result',{}).get('status')!='success':failed.append('재고 집계')
            hydration=report.get('hydration',{});hydration_text=(f"수화 지시 {hydration.get('rows',0):,}건 · 수화 부족 산식 반영" if hydration.get('status')=='success' else '수화 지시 미연결(임시 0으로 산식 계산)')
            self.notice.setText('갱신 '+str(report.get('completed_at',''))+' · '+hydration_text+(' · 재고 실패: '+', '.join(failed) if failed else ' · 4개 표시 창고 스냅샷 저장'))
            self.notice.setToolTip(json.dumps(report,ensure_ascii=False,indent=2))

    def display_tab(self,index):
        if not self.result:self.model.load([]);return
        result=self.visible_result()
        products=[(s['name'],p) for s in result['sheets'] for p in s['products']]
        if self.detail_page.name_basis.currentData()=='due':products.sort(key=lambda x:(x[1].get('due') or '9999-12-31',x[0],x[1].get('base','')))
        self.visible_rows=[r for _name,p in products for r in p['rows']]
        self.inventory_page_index=0;self.render_inventory_page()

    def change_inventory_page(self,delta):
        pages=max(1,(len(self.visible_rows)+self.inventory_page_size-1)//self.inventory_page_size)
        self.inventory_page_index=max(0,min(pages-1,self.inventory_page_index+delta));self.render_inventory_page()

    def render_inventory_page(self):
        start=self.inventory_page_index*self.inventory_page_size
        self.model.load(self.visible_rows[start:start+self.inventory_page_size]);self.inventory_table.scrollToTop()
        pages=max(1,(len(self.visible_rows)+self.inventory_page_size-1)//self.inventory_page_size);d=self.detail_page
        d.page_status_label.setText(f'{self.inventory_page_index+1} / {pages} 페이지')
        d.page_status_label.setToolTip(f'전체 {len(self.visible_rows):,}행 · 페이지당 {self.inventory_page_size:,}행 · 엑셀은 전체 페이지 저장')
        d.previous_page_button.setEnabled(self.inventory_page_index>0);d.next_page_button.setEnabled(self.inventory_page_index+1<pages);d.pagination_bar.show()

    def visible_result(self):
        if not self.result:return None
        selected=self.detail_page._selected_classifications()
        source_sheets=self.result['sheets'] if '전체' in selected else [s for s in self.result['sheets'] if s['name'] in selected]
        mode=self.detail_page.summary_mode.currentIndex();col={1:11,2:12}.get(mode)
        result=dict(self.result);sheets=[]
        for sheet in source_sheets:
            products=[]
            for product in sheet['products']:
                rows=product['rows'] if col is None else [r for r in product['rows'] if isinstance(r[col],(int,float)) and r[col]>0]
                if rows:products.append({**product,'rows':rows})
            if products:sheets.append({**sheet,'products':products})
        rows=[r for s in sheets for p in s['products'] for r in p['rows']]
        result.update(sheets=sheets,rows=len(rows),products=sum(len(s['products']) for s in sheets),total45=sum(r[11] for r in rows),total80=sum(r[12] for r in rows),filters={**self.result['filters'],'분류':next(iter(selected),'전체'),'간략히 보기':self.detail_page.summary_mode.currentText()})
        return result

    def compact_view(self,*_):
        if not hasattr(self,'model'):return
        self.display_tab(0)

    def sort_changed(self,*_):
        if hasattr(self,'model'):self.display_tab(0)

    def _export_excel(self):
        open_path=getattr(self,'_recent_export_path',None)
        if open_path is not None:
            os.startfile(str(open_path.parent));self._restore_export_button();return
        if not getattr(self,'result',None):return
        export_filters=self.filters()
        export_filters.update(search='',detail_search='',classes=['전체'],process='전체')
        selected=self._selected_export_classification()
        try:
            source=self.inventory_service.build(export_filters)
            result=self._export_result_for_classification(source,selected)
            path=self._next_desktop_export_path()
            export_inventory(result,path)
        except Exception as exc:
            self._show_export_feedback(False,None,str(exc))
            return
        self._show_export_feedback(True,path,'')

    def _selected_export_classification(self):
        for name,button in self.detail_page.classification_buttons.items():
            if button.isChecked():return str(name)
        return '전체'

    def _export_result_for_classification(self,source,classification):
        result=dict(source)
        sheets=source['sheets'] if classification=='전체' else [s for s in source['sheets'] if s['name']==classification]
        rows=[r for s in sheets for p in s['products'] for r in p['rows']]
        markets=[str(b.property('market')) for b in self.market_buttons if b.isChecked()]
        due=self.detail_page._due_limit()
        result.update(
            sheets=sheets,rows=len(rows),products=sum(len(s['products']) for s in sheets),
            total45=sum(float(r[11] or 0) for r in rows),total80=sum(float(r[12] or 0) for r in rows),
            filters={'진행현황':', '.join(markets) or '전체','납기':due.isoformat() if due else '해제','신규분류요약':classification},
        )
        return result

    @staticmethod
    def _next_desktop_export_path(now=None,desktop=None):
        now=now or datetime.now();folder=Path(desktop or (Path.home()/'Desktop'));folder.mkdir(parents=True,exist_ok=True)
        stem='생산3팀_재고현황_'+now.strftime('%Y%m%d_%H%M%S');path=folder/(stem+'.xlsx');suffix=1
        while path.exists():
            path=folder/(f'{stem} ({suffix}).xlsx');suffix+=1
        return path

    def _show_export_feedback(self,success,path,error):
        self._recent_export_path=path if success else None
        self.export_button.setText('저장 완료 ↗' if success else '저장 실패')
        self.export_button.setToolTip((f'{path.name}\n{path.parent}\n클릭하면 폴더를 엽니다.' if success else error))
        self.export_button.setProperty('exportState','success' if success else 'error')
        self.export_button.style().unpolish(self.export_button);self.export_button.style().polish(self.export_button)
        token=object();self._export_feedback_token=token
        QTimer.singleShot(5000,lambda:self._restore_export_button(token))

    def _restore_export_button(self,token=None):
        if token is not None and token is not getattr(self,'_export_feedback_token',None):return
        self._recent_export_path=None;self.export_button.setText('엑셀 내보내기')
        self.export_button.setToolTip('진행현황·납기·신규분류요약 조건의 전체 페이지를 바탕화면에 저장합니다.')
        self.export_button.setProperty('exportState','');self.export_button.style().unpolish(self.export_button);self.export_button.style().polish(self.export_button)
