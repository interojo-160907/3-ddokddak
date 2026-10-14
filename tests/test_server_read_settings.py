import unittest
from types import SimpleNamespace
from unittest.mock import patch,Mock
from services import server_read_settings as settings


class ServerSettingsTests(unittest.TestCase):
    def setUp(self):
        self.old=(settings._dsn,settings._permission_until)
        settings._dsn='';settings._permission_until=0
        self.gate=Mock(endpoint='https://management.example/exec')
        self.gate.check.return_value=SimpleNamespace(allowed=True,source='remote')
        self.gate.identity.return_value={'pc_id':'AUTHORIZED','program':'생산3공장 똑딱이'}
    def tearDown(self):
        settings._dsn,settings._permission_until=self.old
    def test_denied_pc_cannot_read_or_provision_connection(self):
        self.gate.check.return_value=SimpleNamespace(allowed=False,source='remote')
        with patch.object(settings,'_gate',return_value=self.gate), \
             patch.object(settings.keyring,'get_password') as read, \
             patch.object(settings.requests,'post') as send:
            with self.assertRaisesRegex(RuntimeError,'사용 허가'): settings.resolve_connection()
            read.assert_not_called();send.assert_not_called()
    def test_existing_scm_connection_is_automatically_registered_locally(self):
        with patch.object(settings,'_gate',return_value=self.gate), \
             patch.dict('os.environ',{'DDOKDDAK_PROD3_DMZ_DSN':''}), \
             patch.object(settings.keyring,'get_password',side_effect=['','private-local-connection']), \
             patch.object(settings.keyring,'set_password') as save, \
             patch.object(settings.requests,'post') as send:
            self.assertEqual(settings.resolve_connection(),'private-local-connection')
            save.assert_called_once_with(settings.SERVICE,'DATABASE_URL','private-local-connection')
            send.assert_not_called()
    def test_new_pc_fetches_common_profile_without_manual_registration(self):
        reply=Mock();reply.json.return_value={'ok':True,'result':{'allowed':True,
                                       'server_read':{'dsn':'common-reader-connection'}}}
        with patch.object(settings,'_gate',return_value=self.gate), \
             patch.dict('os.environ',{'DDOKDDAK_PROD3_DMZ_DSN':''}), \
             patch.object(settings.keyring,'get_password',return_value=None), \
             patch.object(settings.keyring,'set_password') as save, \
             patch.object(settings.requests,'post',return_value=reply) as send, \
             patch.object(settings,'validate_shared_reader') as validate:
            self.assertEqual(settings.resolve_connection(),'common-reader-connection')
            self.assertEqual(send.call_args.kwargs['json']['action'],'server_read_config')
            validate.assert_called_once_with('common-reader-connection')
            save.assert_called_once()
    def test_missing_common_settings_preserve_local_data_and_do_not_save(self):
        reply=Mock();reply.json.return_value={'ok':False,'result':{'allowed':True}}
        with patch.object(settings,'_gate',return_value=self.gate), \
             patch.dict('os.environ',{'DDOKDDAK_PROD3_DMZ_DSN':''}), \
             patch.object(settings.keyring,'get_password',return_value=None), \
             patch.object(settings.keyring,'set_password') as save, \
             patch.object(settings.requests,'post',return_value=reply):
            with self.assertRaisesRegex(RuntimeError,'기존 로컬 자료'): settings.resolve_connection()
            save.assert_not_called()


if __name__=='__main__': unittest.main()
