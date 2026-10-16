"""Exercise real Qt buttons and streaming HTTP without installing on the test PC."""
import functools
import hashlib
import http.server
import threading
import tempfile
from pathlib import Path
from unittest.mock import patch


def run():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QPushButton, QLabel
    import ui.update_flow_dialog as flow
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        data = bytearray(1024); data[:2] = b'MZ'; data[60:64] = (64).to_bytes(4,'little'); data[64:68] = b'PE\0\0'
        (root/'setup.exe').write_bytes(data)
        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args): pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=directory))
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            results = []
            for action in ('download','update','retry','standalone'):
                target = root/(action+'.exe'); errors=[]; clicked=False; retried=False
                timer=QTimer(); ticks=0
                def interact():
                    nonlocal clicked,retried,ticks
                    ticks+=1
                    dialog=app.activeModalWidget()
                    if dialog is None: return
                    if ticks>300:
                        errors.append('dialog timeout');dialog.accept();return
                    button=dialog.findChild(QPushButton,'updateButton' if action=='update' else 'downloadButton')
                    if not clicked:
                        clicked=True;button.click();return
                    status=dialog.findChild(QLabel,'updateStatus').text()
                    if '실패' in status:
                        if action=='retry' and not retried and button.isEnabled():
                            retried=True;button.click()
                        else:
                            errors.append(status);dialog.accept()
                    confirm=dialog.findChild(QPushButton,'downloadConfirm')
                    if '다운로드 완료' in status and confirm.isVisible() and confirm.isEnabled(): confirm.click()
                timer.timeout.connect(interact);timer.start(30)
                destinations=[PermissionError('test denied'),target] if action=='retry' else None
                with patch.object(flow,'installer_destination',side_effect=destinations,return_value=target), patch.object(flow,'schedule_install_and_restart') as install:
                    selected=flow.show_required_update(None,'3.2.3','3.2.5','Test',f'http://127.0.0.1:{server.server_port}/setup.exe',download_only=action=='standalone')
                timer.stop()
                assert not errors,errors
                assert selected==('update' if action=='update' else 'download'),selected
                assert target.read_bytes()==data
                assert install.call_count==(1 if action=='update' else 0)
                if action=='retry': assert retried
                results.append(action)
            return results
        finally:
            server.shutdown();server.server_close();thread.join()
