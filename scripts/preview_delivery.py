"""Launch the production3 delivery candidate with its isolated review data."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['DDOKDDAK_PROD3_PREVIEW'] = '1'
os.environ['DDOKDDAK_PROD3_ALLOW_PARALLEL'] = '1'
os.environ['DDOKDDAK_PROD3_DMZ_REVIEW'] = '1'
os.environ['DDOKDDAK_PROD3_PREVIEW_DATA_DIR'] = str(ROOT / 'qa/delivery-preview-20261016')

import gui_app_pyside6 as entry
from PySide6.QtCore import QTimer

class DeliveryPreview(entry.MainWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setWindowTitle('똑딱이 생산3팀 · 3.2.3 검토용 (별도 데이터)')
        self.collection_expand_button.setChecked(True)
        self._toggle_collection_details(True)
        QTimer.singleShot(500, lambda: self.show_page('settings'))
        QTimer.singleShot(15000, lambda: self.grab().save(str(ROOT / 'qa/delivery-preview.png')))

entry.MainWindow = DeliveryPreview
if __name__ == '__main__':
    raise SystemExit(entry.main())
