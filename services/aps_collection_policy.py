"""Persist run success and failure delays across collector/app restarts."""
import json
import time
from pathlib import Path
from services.data_location import resolve_data_root


class ApsPolicy:
    def __init__(self, path=None, clock=time.time):
        self.path = Path(path or resolve_data_root() / 'backward-v1/settings/aps_request_policy.json')
        self.clock = clock
        try:
            self.state = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.state = {}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.state, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.path)

    def blocked(self):
        return self.clock() < float(self.state.get('next_attempt_at', 0))

    def succeeded(self, run_key):
        self.state = {'successful_run_key':run_key, 'failures':0, 'next_attempt_at':0}
        self.save()

    def failed(self):
        count = min(10, int(self.state.get('failures', 0)) + 1)
        self.state.update(failures=count, next_attempt_at=self.clock()+min(3600,600*2**(count-1)))
        self.save()

    def begin(self):
        # Crash/cancellation also leaves a delay rather than immediate repetition.
        self.state['next_attempt_at'] = self.clock() + 600
        self.save()
