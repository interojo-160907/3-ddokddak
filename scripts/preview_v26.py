"""Open a second, isolated review window. Installed app keeps collecting."""
from pathlib import Path
import os
import sys
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'outputs' / 'v2.6-preview'
OUT.mkdir(parents=True, exist_ok=True)
os.environ['DDOKDDAK_PROD3_ALLOW_PARALLEL'] = '1'
os.environ['DDOKDDAK_PROD3_PREVIEW'] = '1'
os.environ['DDOKDDAK_SAFE_ROOT'] = str(OUT / '안전모드')
os.environ['DDOKDDAK_PREVIEW_CONTROL'] = str(OUT / '시험모드.json')
control = {'mode':'안전모드','safe_asset':{'name':'260907_오전.xlsx','local_path':str(ROOT/'안전모드_APS자료'/'260907_오전.xlsx')}}
(OUT/'시험모드.json').write_text(json.dumps(control,ensure_ascii=False),encoding='utf-8')

import gui_app_pyside6 as entry
from PySide6.QtCore import QTimer
from services import safe_mode

Base = entry.MainWindow
class ReviewWindow(Base):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle('똑딱이 - 생산3팀 전용 v2.6 미리보기')
        self._last_review_signature = None
        self._review_timer = QTimer(self)
        self._review_timer.setInterval(3000)
        self._review_timer.timeout.connect(self.capture_review)
        self._review_timer.start()
        if os.getenv('DDOKDDAK_PREVIEW_FOCUS'):
            QTimer.singleShot(6000, self.focus_review)
    def focus_review(self):
        self.show_page('process_overview')
        self.process_overview_page.search_from_risk(os.environ['DDOKDDAK_PREVIEW_FOCUS'])
    def capture_review(self):
        mode = 'safe' if safe_mode.active() else 'auto'
        signature = (mode, self.header_meta.text(), self._mode_error, self._current_page, self.width(), self.height())
        if signature == self._last_review_signature:
            return
        self._last_review_signature = signature
        self.grab().save(str(OUT/f'{mode}.png'))
        report = {'mode':mode,'header':self.header_meta.text(),'error':self._mode_error,
                  'disabled':[k for k,b in self.nav_buttons.items() if not b.isEnabled()],
                  'aps_totals':self.dashboard_data.get('shortage'),
                  'window_title':self.windowTitle()}
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes
            send = ctypes.windll.user32.SendMessageW
            send.argtypes = [wintypes.HWND,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
            send.restype = wintypes.LPARAM
            report['native_small_icon'] = bool(send(int(self.winId()),0x7F,0,0))
            report['native_big_icon'] = bool(send(int(self.winId()),0x7F,1,0))
        (OUT/'ui-state.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
entry.MainWindow = ReviewWindow
raise SystemExit(entry.main())
