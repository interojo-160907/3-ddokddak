"""Central ERP request queue; absence of a valid grant never permits ERP calls."""
from contextlib import contextmanager
from datetime import datetime
import json
import os
import sys
import threading
import time
import uuid
import requests


def _progress(result):
    from services.data_location import resolve_data_root
    path = resolve_data_root() / 'backward-v1/settings' / f'erp_turn_{os.getpid()}.json'
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps({**result, 'checked_at':datetime.now().astimezone().isoformat()},
                                  ensure_ascii=False), encoding='utf-8')
        temp.replace(path)
    except OSError:
        pass


@contextmanager
def request_turn(endpoint=''):
    # Source test/preview runs and the isolated packaged simulation use fake ERP.
    if ('--package-smoke-test' in sys.argv or
        (not getattr(sys, 'frozen', False) and os.getenv('DDOKDDAK_PROD3_COORDINATION') != '1')):
        yield
        return
    from config import APP_VERSION
    from services.program_gate import ProgramGate
    gate = ProgramGate(APP_VERSION)
    token = uuid.uuid4().hex
    stop = threading.Event()
    lost = threading.Event()
    worker = None
    granted = queued = False
    failed = True

    def send(action, **fields):
        if not gate.endpoint:
            raise RuntimeError('API 호출 차례 관리 주소가 없습니다. 수집을 보류합니다.')
        response = requests.post(gate.endpoint,
            json={**gate.identity(), 'action':action, 'request_token':token, **fields}, timeout=(5,20))
        response.raise_for_status()
        body = response.json()
        result = body.get('result', {}) if isinstance(body, dict) else {}
        if not isinstance(body, dict) or not isinstance(result, dict) or not body.get('ok') or result.get('coordination_version') != 2:
            raise RuntimeError('관리 API 호출 차례 기능이 준비되지 않아 수집을 보류합니다.')
        return result

    def heartbeat():
        while not stop.wait(45):
            try:
                if not send('erp_renew').get('granted'):
                    lost.set(); return
            except (requests.RequestException, ValueError, RuntimeError):
                lost.set(); return

    try:
        deadline = time.monotonic() + 1200
        while True:
            result = send('erp_acquire')
            queued = True
            _progress({**result, 'endpoint':endpoint})
            if result.get('granted'):
                granted = True
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('수집 차례 대기가 20분을 초과했습니다. 다음 주기에 다시 요청합니다.')
            delay = max(5, min(30, float(result.get('retry_after',20))))
            time.sleep(delay)
        worker = threading.Thread(target=heartbeat, name='erp-turn-heartbeat', daemon=True)
        worker.start()
        yield
        if lost.is_set():
            raise RuntimeError('수집 진행 신호를 확인하지 못했습니다. 추가 API 호출을 중단합니다.')
        failed = False
    finally:
        stop.set()
        if worker is not None:
            worker.join(30)
        if queued:
            try:
                send('erp_release' if granted else 'erp_cancel', failed=failed)
            except (requests.RequestException, ValueError, RuntimeError):
                pass  # Lost completion keeps server ownership until lease expiry.
        _progress({'status':'finished', 'endpoint':endpoint})
