import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import requests
from services.api_health import _probe, record_health, connection_label
from services.collection_health import header_status

class ApiHealthTests(unittest.TestCase):
    def statuses(self):
        return {k: {'status':'success'} for k in ('bom_status','aps_status','production_status','live_status')}

    def details(self):
        return {k: {'status':'success','checked_at':'2026-09-15T16:00:00+09:00','elapsed_seconds':1} for k in ('bom','aps','production','live')}

    def test_timeout_is_waiting_and_does_not_change_collection_success(self):
        with patch('services.api_health.requests.get',side_effect=requests.ReadTimeout('secret')):
            result=_probe('/api/aps-plan/meta',{},30)
        self.assertEqual(result['status'],'delayed')
        self.assertEqual(result['error_type'],'ReadTimeout')
        self.assertNotIn('secret',json.dumps(result))
        details=self.details(); details['aps']=result
        text,tip,ready=header_status(self.statuses(),{},details)
        self.assertEqual(text,'수집 정상 · APS 응답 대기')
        self.assertFalse(ready)
        self.assertIn('ReadTimeout',tip)

    def test_repeated_failures_escalate_and_recovery_resets(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'health.json'; previous={}
            results=self.details();results['aps']['status']='delayed'
            for count in range(1,4):
                previous=record_health(results,previous,path)
                self.assertEqual(previous['aps']['consecutive_failures'],count)
            self.assertEqual(connection_label(previous['aps']),'접속 확인 필요')
            self.assertEqual(header_status(self.statuses(),{},previous)[0],'수집 정상 · APS 접속 확인 필요')
            previous=record_health(self.details(),previous,path)
            self.assertEqual(previous['aps']['consecutive_failures'],0)
            self.assertEqual(header_status(self.statuses(),{},previous)[0],'수집 전체 양호')
            self.assertEqual(json.loads(path.read_text())['aps']['status'],'success')

    def test_auth_errors_are_immediate_and_take_priority_over_timeout(self):
        response=Mock();response.status_code=403
        response.__enter__=Mock(return_value=response); response.__exit__=Mock(return_value=False)
        with patch('services.api_health.requests.get',return_value=response): result=_probe('/api/aps-plan/meta',{},30)
        self.assertEqual(connection_label(result),'접속 확인 필요')
        details=self.details();details['bom']['status']='delayed';details['aps']=result
        self.assertEqual(header_status(self.statuses(),{},details)[0],'수집 정상 · APS 외 1건 접속 확인 필요')

    def test_real_collection_error_is_not_hidden_by_healthy_probes(self):
        statuses=self.statuses(); statuses['live_status']['status']='retained'
        self.assertEqual(header_status(statuses,{},self.details())[0],'수집 상태 확인 필요')
        self.assertEqual(header_status(self.statuses(),{'aps':{'message':'failed'}},self.details())[0],'수집 상태 확인 필요')

    def test_not_checked_is_not_reported_as_healthy(self):
        self.assertEqual(header_status(self.statuses(),{},{} )[0],'수집 정상 · 접속 확인 중')

    def test_diagnostic_write_failure_does_not_change_results(self):
        with patch('pathlib.Path.mkdir',side_effect=OSError('disk')):
            results=record_health(self.details(),{},Path('unwritable/health.json'))
        self.assertEqual(results['aps']['status'],'success')

    def test_header_ui_blue_for_timeout_orange_for_collection_failure(self):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
        from PySide6.QtWidgets import QApplication,QLabel,QPushButton
        from types import SimpleNamespace
        from ui.main_window import MainWindow
        app=QApplication.instance() or QApplication([])
        details=self.details();details['aps']['status']='delayed'
        w=SimpleNamespace(dashboard_data=self.statuses(),_current_page='settings',
            LIVE_PROCESS_NAV={},LOT_PROCESS_NAV={},header_meta=QLabel(),data_status=QPushButton(),
            _mode_error='',_api_health_details=details,_read_collection_errors=lambda:{})
        with patch('ui.main_window.safe_mode.active',return_value={}):
            MainWindow._refresh_header_status(w)
            self.assertIn('APS 응답 대기',w.data_status.text())
            self.assertIn('#EFF6FF',w.data_status.styleSheet())
            w.dashboard_data['aps_status']['status']='retained'
            MainWindow._refresh_header_status(w)
            self.assertIn('수집 상태 확인 필요',w.data_status.text())
            self.assertIn('#FFF7ED',w.data_status.styleSheet())

if __name__=='__main__': unittest.main()
