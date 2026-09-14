import tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch,Mock
import requests
from services import safe_mode
class ModeDiagnosticsTests(unittest.TestCase):
 def test_only_transport_errors_are_transient(self):
  self.assertTrue(safe_mode.transient_control_error(requests.ReadTimeout()))
  self.assertFalse(safe_mode.transient_control_error(RuntimeError('사용 권한 거부')))
  self.assertFalse(safe_mode.transient_control_error(ValueError('잘못된 모드')))
  for code, expected in [(403,False),(503,True)]:
   response=requests.Response(); response.status_code=code
   self.assertEqual(safe_mode.transient_control_error(requests.HTTPError(response=response)),expected)
 def test_success_and_timeout_diagnostics(self):
  with tempfile.TemporaryDirectory() as tmp, patch.object(safe_mode,'DATA_CENTER_DIR',Path(tmp)), patch.object(safe_mode,'ProgramGate') as gate, patch.object(safe_mode.requests,'post') as post:
   gate.return_value.endpoint='https://example.invalid'; gate.return_value.identity.return_value={}
   post.return_value.json.return_value={'ok':True,'result':{'mode':'자동모드'}}
   self.assertEqual(safe_mode.fetch_control()['mode'],'자동모드')
   self.assertEqual(post.call_args.kwargs['timeout'],(5,30))
   target=Path(tmp)/'settings/mode_check_status.json'
   self.assertEqual(json.loads(target.read_text(encoding='utf-8'))['status'],'success')
   post.side_effect=requests.ReadTimeout('private endpoint')
   with self.assertRaises(requests.ReadTimeout): safe_mode.fetch_control()
   record=target.read_text(encoding='utf-8')
   self.assertEqual(json.loads(record)['error_type'],'ReadTimeout')
   self.assertNotIn('private endpoint',record)
if __name__=='__main__': unittest.main()
