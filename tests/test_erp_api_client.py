from __future__ import annotations

import json
import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import requests

from services import api_credentials as credentials
from services import erp_api_client as client


def reply(status=200, payload=None, headers=None):
    response = Mock(status_code=status, headers=headers or {})
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(side_effect=lambda *_: response.close())
    response.json.return_value = {"rows": [], "total_count": 0} if payload is None else payload
    return response


class ErpApiClientTests(unittest.TestCase):
    def setUp(self):
        self.key = patch.object(credentials, "credential_value", return_value="saved-test-key")
        self.env = patch.dict(os.environ, {}, clear=True)
        self.key.start();self.env.start()
        self.addCleanup(self.key.stop);self.addCleanup(self.env.stop)

    def test_environment_overrides_saved_alias_and_blank_alias_falls_through(self):
        with patch.dict(os.environ, {"DDOKDDAK_PROD3_API_KEY":"", "PLAN_API_KEY":"environment-key"}):
            self.assertEqual(credentials.resolve_api_key(), "environment-key")
            self.assertEqual(credentials.resolve_api_key("explicit"), "explicit")
        with patch.object(credentials, "credential_value", side_effect=lambda name: "saved-plan" if name == "PLAN_API_KEY" else ""):
            self.assertEqual(credentials.resolve_api_key(), "saved-plan")

    def test_read_timeout_is_not_immediately_repeated_and_secrets_are_not_logged(self):
        with patch.object(client.requests.Session, "get", side_effect=requests.ReadTimeout("secret-key")) as get:
            with self.assertRaises(client.ApiRequestError) as caught:
                client.request_json('/api/hydration-job-list', attempts=3)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(caught.exception.kind, "ReadTimeout")
        self.assertNotIn("secret-key", str(caught.exception))
        self.assertEqual(get.call_args.kwargs['timeout'], (5, 30))

    def test_connect_failure_retries_once_then_uses_same_request_and_releases_response(self):
        response = reply()
        with patch.object(client.requests.Session, "get", side_effect=[requests.ConnectTimeout(),response]) as get, patch.object(client.time, "sleep"):
            self.assertEqual(client.request_json('/api/example'), {"rows": [],"total_count":0})
        self.assertEqual(get.call_count,2)
        self.assertEqual(get.call_args_list[0],get.call_args_list[1])
        response.close.assert_called_once()

    def test_body_read_timeout_wrapped_as_connection_error_is_not_repeated(self):
        from urllib3.exceptions import ReadTimeoutError
        error=requests.ConnectionError(ReadTimeoutError(None, '/api/example', 'slow body'))
        with patch.object(client.requests.Session,'get',side_effect=error) as get:
            with self.assertRaises(client.ApiRequestError) as caught:client.request_json('/api/example')
        self.assertEqual(caught.exception.kind,'ReadTimeout')
        self.assertEqual(get.call_count,1)

    def test_http_success_with_api_failure_is_not_a_valid_snapshot(self):
        with patch.object(client.requests.Session,'get',return_value=reply(payload={'ok':False,'rows':[]})):
            with self.assertRaises(client.ApiRequestError):client.request_json('/api/example')

    def test_daily_chunk_collection_stops_on_first_failure(self):
        from services.collection_parallel import bounded_map
        from collectors import production_performance_collector as production
        from datetime import timedelta
        start=date(2026,9,1);end=date(2026,9,30)
        error=client.ApiRequestError('/api/production-performance','ReadTimeout',30,1)
        def fetch(a,b,*args):
            if a!=b:raise RuntimeError('일부만 반환')
            raise error
        with patch.object(production,'_fetch',side_effect=fetch) as call:
            with self.assertRaises(client.ApiRequestError):production._fetch_complete_range(start,end,'',45)
        self.assertLessEqual(call.call_count,3)  # One monthly attempt, two daily calls.
        self.assertEqual(bounded_map(lambda x:x*2,range(7)),list(range(0,14,2)))

    def test_item_batch_stops_network_requests_and_retains_cached_products(self):
        from services.item_code_service import ItemCodeService
        service=ItemCodeService.__new__(ItemCodeService)
        service.cached=lambda _:([{'gd_cd':'P1000-01.00'}],None)
        error=client.ApiRequestError('/api/item-list-bulk','ReadTimeout',30,1)
        with patch.object(service,'_fetch_one',side_effect=error) as fetch:
            result=service.load_many(['P'+str(n) for n in range(1000,1100)],force=True)
        self.assertLessEqual(fetch.call_count,2)
        self.assertEqual(len(result['errors']),100)
        self.assertEqual(len(result['rows_by_code']),100)
        self.assertEqual(set(result['sources'].values()),{'stale-cache'})

    def test_auth_and_validation_errors_are_not_retried(self):
        for status in (401,403,422,302):
            response=reply(status)
            with self.subTest(status=status), patch.object(client.requests.Session,'get',return_value=response) as get:
                with self.assertRaises(client.ApiRequestError) as caught:client.request_json('/api/example')
                self.assertEqual(caught.exception.http_status,status)
                self.assertEqual(get.call_count,1)
                self.assertFalse(get.call_args.kwargs['allow_redirects'])
                response.close.assert_called_once()

    def test_long_retry_after_defers_to_next_cycle(self):
        with patch.object(client.requests.Session,'get',return_value=reply(429,headers={'Retry-After':'120'})) as get:
            with self.assertRaises(client.ApiRequestError):client.request_json('/api/example')
        self.assertEqual(get.call_count,1)

    def test_short_retry_after_is_respected_once(self):
        responses=[reply(503,headers={'Retry-After':'2'}),reply()]
        with patch.object(client.requests.Session,'get',side_effect=responses) as get, patch.object(client.time,'sleep') as sleep:
            client.request_json('/api/example')
        sleep.assert_called_once_with(2)
        self.assertEqual(get.call_count,2)
        for response in responses:response.close.assert_called_once()

    def test_non_json_or_array_payload_is_rejected_without_repeating(self):
        for payload in ([],None):
            response=reply(payload=[])
            if payload is None:response.json.side_effect=ValueError('not json')
            with patch.object(client.requests.Session,'get',return_value=response) as get:
                with self.assertRaises(client.ApiRequestError) as caught:client.request_json('/api/example')
                self.assertEqual(caught.exception.kind,'InvalidJSON')
                self.assertEqual(get.call_count,1)

    def test_nested_collectors_never_exceed_two_network_requests(self):
        lock=threading.Lock();counts={'active':0,'maximum':0}
        def get(*args,**kwargs):
            with lock:
                counts['active']+=1
                counts['maximum']=max(counts['maximum'],counts['active'])
            threading.Event().wait(.015)
            with lock:counts['active']-=1
            return reply()
        with patch.object(client.requests.Session,'get',side_effect=get), ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _:client.request_json('/api/example'), range(16)))
        self.assertLessEqual(counts['maximum'],2)
        self.assertEqual(counts['active'],0)

    def test_every_collector_and_health_check_use_saved_credentials(self):
        from collectors import bom_snapshot_collector as bom, process_status_collector as aps
        from collectors import production_performance_collector as production, live_production_need_collector as live
        from services import api_health
        actions=[lambda:bom._fetch('/api/product-names','',240),
                 lambda:aps._request('/api/aps-plan/meta',{},'',300),
                 lambda:production._fetch_once(date(2026,9,19),date(2026,9,19),'',240),
                 lambda:live._request('/api/wip',{},'',240)]
        with patch.object(credentials,'credential_value',side_effect=lambda n:'saved-plan' if n=='PLAN_API_KEY' else ''):
            with patch.object(client.requests.Session,'get',side_effect=lambda *a,**kw:reply(payload={'rows':[{}],'total_count':1})) as get:
                for action in actions:action()
            self.assertEqual(get.call_count,4)
            for call in get.call_args_list:
                self.assertEqual(call.kwargs['headers']['X-API-Key'],'saved-plan')
                self.assertLessEqual(call.kwargs['timeout'][1],60)
            self.assertEqual(api_health._headers()['X-API-Key'],'saved-plan')


class RealHttpTransportTest(unittest.TestCase):
    def test_persistent_connection_and_full_json_body(self):
        connections=[];auth=[]
        class Handler(BaseHTTPRequestHandler):
            protocol_version='HTTP/1.1'
            def do_GET(self):
                connections.append(self.connection)
                auth.append(self.headers.get('X-API-Key'))
                body=json.dumps({'rows':[],'total_count':0}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(body)))
                self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            for _ in range(2):
                self.assertEqual(client.request_json('/test',base_url=f'http://127.0.0.1:{server.server_port}',api_key='local-test')['total_count'],0)
            self.assertIs(connections[0],connections[1])
            self.assertEqual(auth,['local-test','local-test'])
        finally:
            client._session().close()
            server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__':unittest.main()
