from datetime import date, timedelta
from html import escape
import json
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import Signal, QTimer, QDate, Qt, QUrl, QEvent
from PySide6.QtGui import QColor, QImage, QPainter, QFont, QTextDocument, QPen
from PySide6.QtWidgets import QApplication, QWidget, QTableWidgetItem, QHeaderView, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTabWidget, QTextBrowser, QAbstractItemView
from ui.process_overview_page import KpiCard
from services.lidding_data import cache_path, build_model, select_rows, STOCK_WAREHOUSES

_loader = ThreadPoolExecutor(max_workers=1, thread_name_prefix='lidding-cache')

def _read_model(path):
    before = path.stat()
    bundle = json.loads(path.read_text('utf-8'))
    model = build_model(bundle)
    after = path.stat()
    signature = (before.st_size, before.st_mtime_ns)
    if signature != (after.st_size, after.st_mtime_ns):
        raise OSError('리드지 자료 교체 중')
    return signature, bundle, model

def make_live_page(Base):
    class LivePage(Base):
        status_changed=Signal()
        def __init__(self):
            self.bundle=None;self.model={};self.visible_rows=[]
            self.receive_status='생산 현황 패키지 수신 대기'
            self._cache_signature=None
            self._load_future=None
            self._outside_filter_active=False
            self._render_pending=False
            super().__init__()
            self.progress.buttonClicked.disconnect(self.filter_rows)
            self.progress.setExclusive(False)
            self.progress.buttonClicked.connect(self.choose_markets)
            self.table.setHorizontalHeaderLabels(['품목코드','품목명','규격','재고 (EA)',
                'APS 생산필요','1.5배 목표','목표 후 잔여','사용속도 / 일','가용일수','APS 납기','관련 수주','비고'])
            header=self.table.horizontalHeader()
            header.setSectionResizeMode(QHeaderView.Fixed)
            header.setMinimumSectionSize(1)
            self.table.horizontalHeaderItem(3).setToolTip('L관·A관·C관·S관 재고 합계 · 셀 위에 마우스를 올리면 창고별 수량')
            self.table.horizontalHeaderItem(7).setToolTip('S관 완료된 최근 7일 자재투입 불출량 ÷ 7')
            self.table.horizontalHeaderItem(8).setToolTip('전체 재고 ÷ S관 최근 7일 일평균 사용속도')
            header.setStretchLastSection(False)
            self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.detail.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.table.setTextElideMode(Qt.ElideRight)
            self.table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.table.setMinimumHeight(145)
            self.search.setPlaceholderText('리드지 · P코드 · 품명 · 수주번호 · 이니셜')
            self.local_search.setPlaceholderText('현재 결과 내 검색')
            self.kpis[0].detail.setText('S관 · 연결 리드지 전체')
            self.kpis[1].detail.setText('L·A·C·S관 재고 · 검사접착(55) × 1.5')
            self.kpis[2].detail.setText('완료된 최근 7일 자재투입 불출 기준')
            self.kpis[2].title_label.setText('S관 가용 3일 이하')
            self.kpis[3].detail.setText('목표 부족·가용 3일 이하 제외')
            # Keep the existing exclusive filter group as state, but remove its UI row.
            risk_layout=self.risk.button(0).parentWidget().layout().takeAt(1).layout()
            while risk_layout.count():
                item=risk_layout.takeAt(0)
                if item.widget():item.widget().hide()
            extra=KpiCard('점검 필요','#8B5CF6')
            extra.detail.setText('안전재고·가용일수·자료 확인')
            self.layout().itemAt(1).layout().insertWidget(1,extra)
            self.kpis.insert(1,extra)
            self.kpis[0].title_label.setText('전체 리드지')
            self.kpis[2].title_label.setText('안전재고 미달')
            self.kpis[2].detail.setText('4개 창고 합계 < APS 필요 × 1.5')
            self.kpis[3].title_label.setText('가용일수 부족')
            self.kpis[3].detail.setText('전체 재고 기준 3일 이하')
            self.kpis[4].hide()
            for index,card in enumerate(self.kpis):
                card.set_clickable(True,'클릭하여 해당 품목 조회')
                card.clicked.connect(lambda i=index:self.choose_risk(i))
            self.layout().removeWidget(self.detail)
            self.detail_panel=QFrame();self.detail_panel.setObjectName('Card')
            panel_layout=QVBoxLayout(self.detail_panel);panel_layout.setContentsMargins(14,10,14,10)
            heading=QHBoxLayout();self.detail_title=QLabel();self.detail_title.setObjectName('CardTitle')
            self.detail_title.setMinimumWidth(0);heading.addWidget(self.detail_title,1)
            close=QPushButton('상세 닫기 ×');close.setObjectName('SecondaryButton');close.clicked.connect(self.close_detail);heading.addWidget(close)
            panel_layout.addLayout(heading)
            self.detail_tabs=QTabWidget()
            self.detail_tabs.setObjectName('bomInnerTabs')
            self.detail_tabs.tabBar().setObjectName('dashboardTabs')
            self.detail_tabs.tabBar().setExpanding(False)
            panel_layout.addWidget(self.detail_tabs)
            self.detail_views=[self.detail]+[QTextBrowser() for _ in range(3)]
            for label,view in zip(['불출 추이','관련 수주','사용 P코드','수불 내역'],self.detail_views):
                view.setMinimumHeight(0);view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
                view.setStyleSheet('QTextBrowser {background:white; border:0; padding:6px; color:#173655;}')
                self.detail_tabs.addTab(view,label)
            self.detail_panel.setFixedHeight(self.detail_height())
            self.layout().addWidget(self.detail_panel,0)
            self.detail_panel.hide()
            self.due_end.installEventFilter(self)
            self.due_end.setDate(QDate.currentDate())
            self.detail.hide()
            self.timer=QTimer(self);self.timer.setInterval(3000);self.timer.timeout.connect(self.reload_data)
            self.load_poll=QTimer(self);self.load_poll.setInterval(30);self.load_poll.timeout.connect(self._finish_load)
            QTimer.singleShot(0,self.fit_columns)

        def _set_outside_filter(self, enabled):
            if enabled == self._outside_filter_active:return
            self._outside_filter_active=enabled
            app=QApplication.instance()
            if enabled:app.installEventFilter(self)
            else:app.removeEventFilter(self)

        def showEvent(self,event):
            super().showEvent(event)
            if hasattr(self,'timer'):
                self.timer.start()
                if self._render_pending:
                    self.filter_rows();self._render_pending=False
                self.reload_data()

        def hideEvent(self,event):
            self._set_outside_filter(False)
            if hasattr(self,'timer'):self.timer.stop()
            if hasattr(self,'detail_panel'):self.detail_panel.hide()
            super().hideEvent(event)

        def resizeEvent(self,event):
            super().resizeEvent(event)
            if hasattr(self,'detail_panel'):
                self.detail_panel.setFixedHeight(self.detail_height())
            if hasattr(self,'table'):QTimer.singleShot(0,self.fit_columns)
            if hasattr(self,'chart_row'):QTimer.singleShot(0,self.render_usage_chart)

        def detail_height(self):
            return max(210,min(600,int(self.height()*0.58),self.height()-460))

        def choose_risk(self,index):
            self.risk.button(index).setChecked(True)
            self.filter_rows()

        def selected_markets(self):
            return {b.text() for b in self.progress.buttons() if b.isChecked()}

        def choose_markets(self,button):
            if self.progress.id(button)==0:
                for b in self.progress.buttons():b.setChecked(b is button)
            else:
                self.progress.button(0).setChecked(False)
                selected=[b for b in self.progress.buttons()[1:] if b.isChecked()]
                if not selected or len(selected)==4:
                    for b in self.progress.buttons():b.setChecked(self.progress.id(b)==0)
            self.filter_rows()

        def close_detail(self):
            self._set_outside_filter(False)
            self.detail_panel.hide()
            self.table.clearSelection()
            QTimer.singleShot(0,self.fit_columns)

        def eventFilter(self,watched,event):
            if (watched is getattr(self,'due_end',None)
                    and event.type() in (QEvent.MouseButtonPress,QEvent.KeyPress)):
                self.due.button(1).setChecked(True)
                self.filter_rows()
            if (self.isVisible() and hasattr(self,'detail_panel')
                    and self.detail_panel.isVisible()):
                if event.type()==QEvent.MouseButtonPress and isinstance(watched,QWidget):
                    # Global position also handles presses propagated from child labels.
                    # Defer closing so the clicked control can finish its normal action.
                    point=event.globalPosition().toPoint()
                    inside_detail=self.detail_panel.rect().contains(self.detail_panel.mapFromGlobal(point))
                    viewport=self.table.viewport()
                    pos=viewport.mapFromGlobal(point)
                    inside_row=viewport.rect().contains(pos) and self.table.indexAt(pos).isValid()
                    scrollbar=self.table.verticalScrollBar()
                    inside_scroll=scrollbar.isVisible() and scrollbar.rect().contains(scrollbar.mapFromGlobal(point))
                    if not (inside_detail or inside_row or inside_scroll):
                        QTimer.singleShot(0,self.close_detail)
                elif event.type()==QEvent.KeyPress and event.key()==Qt.Key_Escape:
                    self.close_detail()
                    return True
            return super().eventFilter(watched,event)

        def fit_columns(self):
            weights=[75,280,100,70,80,75,80,65,65,90,150,130]
            available=self.table.viewport().width()
            widths=[int(available*w/sum(weights)) for w in weights]
            widths[-1]+=available-sum(widths)
            # Reserve enough room for the actual numbers, giving the saved space to names.
            metrics=self.table.fontMetrics()
            for col in range(3,8):
                needed=max((metrics.horizontalAdvance(self.table.item(row,col).text())
                    for row in range(self.table.rowCount()) if self.table.item(row,col)),default=0)+16
                extra=max(0,needed-widths[col])
                extra=min(extra,max(0,widths[1]-120))
                widths[col]+=extra
                widths[1]-=extra
            for i,width in enumerate(widths):self.table.setColumnWidth(i,width)

        def reload_data(self):
            if not self.isVisible() or self._load_future is not None:return
            path=cache_path()
            try:
                stat=path.stat();signature=(stat.st_size,stat.st_mtime_ns)
                if signature==self._cache_signature:return
                self._load_future=_loader.submit(_read_model,path)
                self.load_poll.start()
                return
            except FileNotFoundError:
                self.receive_status='생산 현황 패키지 최초 수신 대기'
                self.result.setText('생산 현황 패키지 수신 대기')
            except (OSError,ValueError,KeyError,TypeError):
                self.receive_status='리드지 자료 확인 필요 · 이전 자료 유지' if self.bundle else '리드지 자료 확인 필요'
            self.status_changed.emit()

        def _finish_load(self):
            future=self._load_future
            if future is None or not future.done():return
            self.load_poll.stop();self._load_future=None
            try:
                signature,bundle,model=future.result()
                self.bundle=bundle;self.model=model;self._cache_signature=signature
                if self.isVisible():self.filter_rows()
                else:self._render_pending=True
                self.receive_status='양호'
            except (OSError,ValueError,KeyError,TypeError):
                self.receive_status='리드지 자료 확인 필요 · 이전 자료 유지' if self.bundle else '리드지 자료 확인 필요'
            self.status_changed.emit()

        def accept_bundle(self,bundle):
            if bundle:
                model=build_model(bundle)
                self.bundle=bundle;self.model=model
                self.filter_rows()
            self.receive_status='양호';self.status_changed.emit()

        def reset_filters(self):
            for b in self.progress.buttons():b.setChecked(False)
            for group in (self.progress,self.due,self.risk):group.button(0).setChecked(True)
            self.due_end.setDate(QDate.fromString(self.bundle['day'],'yyyy-MM-dd') if self.bundle else QDate.currentDate())
            self.search.clear();self.local_search.clear();self.filter_rows()

        def filter_rows(self,*_):
            mode=self.due.checkedButton().text();self.due_end.setEnabled(True)
            base=QDate.currentDate()
            end={'당월':QDate(base.year(),base.month(),base.daysInMonth()),'+7일':base.addDays(7),'+14일':base.addDays(14),'직접':self.due_end.date()}.get(mode)
            if mode in ('당월','+7일','+14일'):
                previous=self.due_end.blockSignals(True)
                self.due_end.setDate(end)
                self.due_end.blockSignals(previous)
            rows,scoped=select_rows(self.model,self.selected_markets(),end.toString('yyyy-MM-dd') if end else None,
                self.search.text(),self.local_search.text(),self.risk.checkedButton().text())
            self.visible_rows=rows;self.table.setRowCount(len(rows))
            counts=[len(scoped),sum(r['shortage'] for r in scoped),sum(r['lowdays'] for r in scoped),sum(r['normal'] for r in scoped)]
            if len(self.kpis)==5:counts.insert(1,sum(r['shortage'] or r['lowdays'] or r['unknown'] for r in scoped))
            for i,n in enumerate(counts):
                self.kpis[i].value.setText(f'{n:,}종')
                self.kpis[i].set_selected_process(i==self.risk.checkedId())
            for row,r in enumerate(rows):
                products=sorted(r['products'].items())
                orders=sorted({(o['initial'],o['order']) for o in r['selected'] if o['qty']>0})
                related=(f'{orders[0][0]} {orders[0][1]}' + (f' 외 {len(orders)-1}건' if len(orders)>1 else '')) if orders else '—'
                specs=sorted(r['specs'])
                label=specs[0]+(f' 외 {len(specs)-1}개' if len(specs)>1 else '')
                fmt=lambda n:'—' if n is None else f'{n:,.0f}'
                reasons=[]
                if r['shortage']:reasons.append('1.5배 목표 부족')
                if r['lowdays']:reasons.append('가용일수 3일 이하')
                if r['unknown']:reasons.append('자료 확인')
                values=[r['code'],r['name'],label,fmt(r['stock']),fmt(r['need']),fmt(r['goal']),fmt(r['remaining']),
                    fmt(r['rate']),f"{r['days']:.1f}일" if r['days'] is not None else ('사용 없음' if r['rate']==0 else '—'),
                    min((o['due'] for o in r['selected'] if o['qty']>0),default='—'),related,' · '.join(reasons) or '정상']
                for col,value in enumerate(values):
                    cell=QTableWidgetItem(value);cell.setToolTip(value)
                    if 3<=col<=8:cell.setTextAlignment(Qt.AlignRight|Qt.AlignVCenter)
                    if (col==6 and r['shortage']) or (col==8 and r['lowdays']):cell.setForeground(QColor('#DC3545'))
                    self.table.setItem(row,col,cell)
                self.table.item(row,0).setToolTip('<b>'+escape(r['code'])+' · 사용 P코드 '+str(len(products))+'개</b><hr>'+'<br>'.join(escape(f'{p} · {name}') for p,name in products))
                self.table.item(row,2).setToolTip('\n'.join(specs))
                warehouse_lines=[f"{name}: {r['warehouses'][code]['qty']:,.0f} EA"
                    for code,name in STOCK_WAREHOUSES.items()
                    if code in r['warehouses'] and r['warehouses'][code]['qty'] != 0]
                if len(warehouse_lines)>1:
                    warehouse_lines.append('합계: '+fmt(r['stock'])+' EA')
                self.table.item(row,3).setToolTip('\n'.join(warehouse_lines))
                self.table.item(row,10).setToolTip('\n'.join(f'{a} · {b}' for a,b in orders) or '연결된 APS 생산필요 없음')
            self.result.setText(f'{len(rows):,}종 · 검사접착(55)' if self.bundle else '패키지 준비 중')
            self.detail.setHtml('')
            self.detail.hide()
            if hasattr(self,'detail_panel'):
                self._set_outside_filter(False)
                self.detail_panel.hide()
            QTimer.singleShot(0,self.fit_columns)

        def show_detail(self,row=0,*_):
            if not 0<=row<len(self.visible_rows):return
            self.table.selectRow(row)
            self.detail_panel.show()
            self._set_outside_filter(True)
            self.detail_tabs.currentWidget().show()
            r=self.visible_rows[row];today=date.fromisoformat(self.bundle['day'])
            title=f"{r['code']} · {r['name']}"
            self.detail_title.setText(title);self.detail_title.setToolTip(title)
            orders={}
            for o in r['selected']:
                if o['qty']<=0:continue
                key=(o['initial'],o['order'],o['pcode'],o['product'],o['due'])
                orders[key]=orders.get(key,0)+o['material_qty']
            order_html=''.join('<tr>'+''.join(f'<td>{escape(str(v))}</td>' for v in (*key,f'{qty:,.0f}'))+'</tr>' for key,qty in sorted(orders.items(),key=lambda it:(it[0][4],it[0][1])))
            products='<br>'.join(escape(f'{p} · {n}') for p,n in sorted(r['products'].items()))
            transactions=''.join('<tr>'+''.join(f'<td>{escape(str(t.get(k) or ""))}</td>' for k in ('dt1','cd2_nm','io_no','lot_no','in_qty','out_qty'))+'</tr>' for t in sorted(r['transactions'],key=lambda t:str(t['dt1']),reverse=True)[:30])
            self.chart_row=r
            self.detail_views[1].setHtml(f'<table width="100%" cellspacing="8"><tr><th>이니셜</th><th>수주번호</th><th>P코드</th><th>품명</th><th>납기</th><th>자재 필요량</th></tr>{order_html or "<tr><td>연결된 APS 생산필요 없음</td></tr>"}</table>')
            self.detail_views[2].setHtml(f'<p>{products}</p>')
            self.detail_views[3].setHtml(f'<table width="100%" cellspacing="8"><tr><th>일자</th><th>구분</th><th>전표</th><th>LOT</th><th>입고</th><th>출고</th></tr>{transactions}</table>')
            QTimer.singleShot(0,self.render_usage_chart)

        def render_usage_chart(self):
            if not hasattr(self,'chart_row') or not self.detail_panel.isVisible():return
            r=self.chart_row;today=date.fromisoformat(self.bundle['day'])
            width=max(300,self.detail.viewport().width()-24)
            height=max(180,self.detail_panel.height()-150)
            image=QImage(width,height,QImage.Format_ARGB32);image.fill(QColor('white'))
            painter=QPainter(image);painter.setRenderHint(QPainter.Antialiasing)
            painter.setFont(QFont('Malgun Gothic',9))
            values=[r['daily'].get((today-timedelta(days=29-n)).isoformat(),0) for n in range(30)]
            stock=r['stock'];s_stock=stock
            scale=max(stock or 0,r['need'],r['goal'],1)
            bar_left=150;bar_width=max(100,width-310)
            for i,(label,value,color) in enumerate([
                    ('전체 재고',stock,'#1677FF'),('APS 생산필요',r['need'],'#6B87AA'),
                    ('안전재고 목표 ×1.5',r['goal'],'#8B5CF6')]):
                y=10+i*25
                painter.setPen(QColor('#173655'));painter.drawText(0,y,145,20,Qt.AlignVCenter,label)
                painter.fillRect(bar_left,y+3,bar_width,14,QColor('#F0F4F9'))
                if value is not None:
                    painter.fillRect(bar_left,y+3,int(bar_width*max(0,value)/scale),14,QColor(color))
                painter.drawText(bar_left+bar_width+10,y,width-bar_left-bar_width-10,20,Qt.AlignVCenter,'미확인' if value is None else f'{value:,.0f} EA')
            painter.setPen(QColor('#52677E'))
            painter.drawText(0,100,'S관 일별 불출량 · 점선은 현재 전체 재고 비교 기준 (과거 재고 아님)')
            peak=max(max(values,default=0),1)
            if s_stock is not None and 0<s_stock<=peak*1.25:peak=max(peak,s_stock)*1.12
            left=80;right=18;top=123;bottom=height-34
            plot_height=bottom-top;step=(width-left-right)/30
            for tick in range(5):
                y=bottom-int(plot_height*tick/4)
                painter.setPen(QColor('#E5EBF3'));painter.drawLine(left,y,width-right,y)
                painter.setPen(QColor('#52677E'))
                painter.drawText(0,y-10,left-12,20,Qt.AlignRight|Qt.AlignVCenter,f'{peak*tick/4:,.0f}')
            painter.drawText(8,top-8,'EA')
            for n,value in enumerate(values):
                bar_height=max(0,int(value/peak*plot_height));x=int(left+n*step+step*.18)
                painter.fillRect(x,bottom-bar_height,max(2,int(step*.64)),bar_height,QColor('#1677FF' if n>=23 else '#9DC9FF'))
                if n%5==0 or n==29:
                    painter.setPen(QColor('#52677E'));painter.drawText(x-8,bottom+23,(today-timedelta(days=29-n)).strftime('%m/%d'))
            if s_stock is not None:
                y=bottom-int(plot_height*min(max(s_stock,0),peak)/peak)
                painter.setPen(QPen(QColor('#D97706'),2,Qt.DashLine))
                painter.drawLine(left,y,width-right,y)
                days=f" · 약 {r['days']:.1f}일분" if r['days'] is not None else ''
                label=f"현재 전체 재고 {s_stock:,.0f} EA{days}"+(' · 축 범위 초과' if s_stock>peak else '')
                label_width=painter.fontMetrics().horizontalAdvance(label)+14
                label_y=max(top,y-23)
                painter.fillRect(width-right-label_width,label_y,label_width,21,QColor('#FFFFFF'))
                painter.setPen(QColor('#B45309'));painter.drawText(width-right-label_width+6,label_y+15,label)
            painter.end()
            self.detail.document().addResource(QTextDocument.ImageResource,QUrl('usage-chart'),image)
            self.detail.setHtml(f'<p style="margin:0 0 6px 0">S관 최근 30일 자재투입 불출 <b>{sum(r["daily"].values()):,.0f} EA</b> · <span style="color:#1677FF">진한 파랑: 최근 7일</span></p><img src="usage-chart" width="{width}" height="{height}">')
    return LivePage


from ui.lidding_layout import LiddingLayout
LiddingInventoryPage = make_live_page(LiddingLayout)
