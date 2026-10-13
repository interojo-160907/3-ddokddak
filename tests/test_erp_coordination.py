import unittest
from unittest.mock import patch, Mock
import requests
from services import erp_coordination as coordination
from services import erp_api_client as client


def response(result):
    r=Mock();r.json.return_value={'ok':True,'result':{'coordination_version':2,**result}}
    return r


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict('os.environ',{'DDOKDDAK_PROD3_COORDINATION':'1'})
        self.env.start();self.addCleanup(self.env.stop)
        self.progress=patch.object(coordination,'_progress');self.progress.start();self.addCleanup(self.progress.stop)
        self.gate=patch('services.program_gate.ProgramGate');gate=self.gate.start().return_value
        gate.endpoint='https://manager.example.test';gate.identity.return_value={'pc_id':'PC1','program':'production3'}
        self.addCleanup(self.gate.stop)

    def test_waiting_turn_does_not_start_work_before_grant(self):
        replies=[response({'granted':False,'retry_after':5}),response({'granted':True}),response({'released':True})]
        with patch.object(coordination.requests,'post',side_effect=replies) as send,patch.object(coordination.time,'sleep') as sleep:
            with coordination.request_turn('/api/aps-backward-plan'):
                self.assertEqual(send.call_count,2)
        self.assertEqual(send.call_count,3);sleep.assert_called_once_with(5)
        self.assertEqual(send.call_args.kwargs['json']['action'],'erp_release')
        self.assertFalse(send.call_args.kwargs['json']['failed'])

    def test_http_524_releases_as_failure_and_does_not_retry(self):
        from tests.test_erp_api_client import reply
        with patch.object(coordination.requests,'post',side_effect=[response({'granted':True}),response({'released':True})]) as send, \
             patch.object(client.requests.Session,'get',return_value=reply(524)) as get:
            with self.assertRaises(client.ApiRequestError):client.request_json('/api/aps-backward-plan')
        self.assertEqual(get.call_count,1)
        self.assertTrue(send.call_args.kwargs['json']['failed'])

    def test_unavailable_coordinator_never_calls_erp(self):
        with patch.object(coordination.requests,'post',side_effect=requests.Timeout('private error')), \
             patch.object(client.requests.Session,'get') as get:
            with self.assertRaises(client.ApiRequestError) as caught:client.request_json('/api/aps-backward-plan')
        get.assert_not_called();self.assertNotIn('private error',str(caught.exception))

    def test_invalid_management_body_is_rejected(self):
        r=Mock();r.json.return_value=[]
        with patch.object(coordination.requests,'post',return_value=r):
            with self.assertRaises(RuntimeError):
                with coordination.request_turn():self.fail('ERP work must be blocked')
