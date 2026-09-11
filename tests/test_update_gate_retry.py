import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import requests
from services.program_gate import ProgramGate


class UpdateGateRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.gate = ProgramGate('2.6.1')
        self.gate.endpoint = 'https://example.test/manage'
        self.gate.cache_path = Path(self.tmp.name) / 'gate.json'
        self.gate.notice_cache_path = Path(self.tmp.name) / 'notices.json'
        self.gate.identity = lambda: {'program':'생산3공장 똑딱이','version':'2.6.1','pc_id':'TEST'}

    @patch('services.program_gate.requests.post')
    def test_google_timeout_retries_then_offers_in_app_update(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {'result':{'allowed':True,'latest_version':'2.6.2'}}
        post.side_effect = [requests.ReadTimeout('slow redirect'), response]
        result = self.gate.check(allow_cache_fallback=False)
        self.assertTrue(result.allowed)
        self.assertTrue(result.update_required)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.kwargs['timeout'], (5, 30))

    @patch('services.program_gate.requests.post')
    def test_exhausted_timeout_preserves_network_error_and_does_not_authorize(self, post):
        post.side_effect = requests.ReadTimeout('offline')
        result = self.gate.check(allow_cache_fallback=False)
        self.assertEqual(post.call_count, 2)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, 'network')

    @patch('services.program_gate.requests.post')
    def test_explicit_denial_is_not_retried_or_replaced_by_update(self, post):
        response = Mock(status_code=200)
        response.json.return_value = {'result':{'allowed':False,'latest_version':'2.6.2'}}
        post.return_value = response
        self.assertEqual(self.gate.check(allow_cache_fallback=False).reason, 'denied')
        post.assert_called_once()
