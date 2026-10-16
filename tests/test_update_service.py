import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
import requests
from services import update_service as service

class UpdateTests(unittest.TestCase):
    def test_aps_basis_separate_from_latest(self):
        from services.aps_display import aps_basis_note
        self.assertIn('이전 회차', aps_basis_note('2026-10-16 15:07:20','2026-10-16 07:38:52'))
        self.assertNotIn('이전 회차', aps_basis_note('2026-10-16 15:07:20','2026-10-16 15:07:20'))
        self.assertIn('미확인', aps_basis_note('2026-10-16 15:07:20',None))

    def test_real_buttons(self):
        from services.update_smoke_test import run
        self.assertEqual(run(),['download','update','retry','standalone'])

    def test_destination_fallback_and_uniqueness(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(service.Path,'home',return_value=Path(directory)/'missing'), patch.dict('os.environ',{'LOCALAPPDATA':directory}):
            first=service.installer_destination('https://example.com/setup.exe','3.2.5',save_only=True)
            second=service.installer_destination('https://example.com/setup.exe','3.2.5',save_only=True)
            self.assertNotEqual(first,second)
        with patch.object(service.tempfile,'mkdtemp',side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(OSError,'저장 가능한'): service.installer_destination('https://example.com/setup.exe','3.2.5',save_only=True)

    def response(self,data):
        response=MagicMock();response.__enter__.return_value=response
        response.headers={'Content-Length':str(len(data))};response.iter_content.return_value=[data];response.history=[];response.url='http://localhost/setup.exe'
        return response

    def test_retry_and_preserve_old_file(self):
        data=bytearray(128);data[:2]=b'MZ';data[60:64]=(64).to_bytes(4,'little');data[64:68]=b'PE\0\0'
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'setup.exe';target.write_bytes(b'old')
            with patch.object(service.requests,'get',return_value=self.response(b'<html>blocked</html>')):
                with self.assertRaises(ValueError):service.download_installer('https://example.com/setup.exe',target,lambda x:None)
            self.assertEqual(target.read_bytes(),b'old')
            self.assertFalse(list(Path(directory).glob('*.part')))
            with patch.object(service.requests,'get',side_effect=[requests.Timeout(),self.response(data)]) as get, patch.object(service.time,'sleep'):
                service.download_installer('https://example.com/setup.exe',target,lambda x:None)
                self.assertEqual(get.call_count,2)
            self.assertEqual(target.read_bytes(),data)

    def test_checksum_mismatch_preserves_file(self):
        data=bytearray(128);data[:2]=b'MZ';data[60:64]=(64).to_bytes(4,'little');data[64:68]=b'PE\0\0'
        response=self.response(data);response.url='https://github.com/interojo-160907/3-ddokddak/releases/download/v3.2.5/setup.exe'
        checksum=self.response(b'');checksum.text='0'*64
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'setup.exe';target.write_bytes(b'old')
            with patch.object(service.requests,'get',side_effect=[response,checksum]):
                with self.assertRaisesRegex(ValueError,'SHA256'):service.download_installer(response.url,target,lambda x:None)
            self.assertEqual(target.read_bytes(),b'old')

if __name__=='__main__':unittest.main()
