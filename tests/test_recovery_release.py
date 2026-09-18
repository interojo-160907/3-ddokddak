import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from config import APP_VERSION
from services.program_gate import ProgramGate


class RecoveryReleaseTests(unittest.TestCase):
    def test_every_existing_version_offers_recovery_and_recovery_does_not_loop(self):
        prior = ['2.6.4', *[f'2.7.{i}' for i in range(1, 10)]]
        self.assertEqual(APP_VERSION, '2.7.10')
        with tempfile.TemporaryDirectory() as tmp:
            for version in [*prior, APP_VERSION]:
                with self.subTest(version=version):
                    gate = ProgramGate(version)
                    gate.endpoint = 'https://example.invalid/manage'
                    gate.cache_path = Path(tmp) / 'gate.json'
                    gate.notice_cache_path = Path(tmp) / 'notices.json'
                    response = Mock(status_code=200)
                    response.json.return_value = {'ok': True, 'result': {
                        'allowed': True, 'latest_version': 'v2.7.10',
                        'update_required': True,
                    }}
                    with patch('services.program_gate.requests.post', return_value=response):
                        result = gate.check(allow_cache_fallback=False)
                    self.assertTrue(result.allowed)
                    self.assertEqual(result.update_required, version != APP_VERSION)

    def test_restored_runtime_with_newer_data_and_real_page(self):
        from services.package_smoke_test import run
        import json
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'report.json'
            self.assertEqual(run(str(path)), 0)
            report = json.loads(path.read_text(encoding='utf-8'))
            self.assertTrue(report['ok'])
            self.assertEqual(report['app_version'], APP_VERSION)
            self.assertEqual(report['restored_from'], '2.6.4')
            self.assertEqual(len(report['rollback_checks']), 6)
