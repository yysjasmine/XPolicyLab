"""Cache warnings never block real buffered dispatch; auth guards still do."""
from importlib import import_module
import json
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

cci = import_module('XPolicyLab.policy.SenseNova-RoboRSI.cci_protocol')
guard = import_module('XPolicyLab.policy.SenseNova-RoboRSI.skillpack.capture_guard')
Config = import_module('roborsi.planner.provider_capture').ProviderCaptureConfig


@pytest.mark.parametrize('cached,expected_alert', [(499, True), (500, False), (601, False)])
def test_cache_threshold_warns_and_next_request_continues(tmp_path, monkeypatch, cached, expected_alert):
    original_open = urllib.request.urlopen
    upstream_calls = []

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            return json.dumps({'id': 'fixture', 'status': 'completed', 'output': [],
                'usage': {'input_tokens': 1000, 'output_tokens': 1,
                          'input_tokens_details': {'cached_tokens': cached}}}).encode()

    def upstream(request, *args, **kwargs):
        if request.full_url.startswith('http://provider.invalid/'):
            upstream_calls.append(request.data)
            if len(upstream_calls) == 10:
                raise urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {}, None)
            return Response()
        return original_open(request, *args, **kwargs)

    monkeypatch.setattr(urllib.request, 'urlopen', upstream)
    adapter = cci.CciAdapter('http://provider.invalid/v1', 'fixture', tmp_path / 'cci')
    adapter.start()
    capture = guard.GuardedCapture(Config(adapter.base_url, 'runtime'), tmp_path / 'api',
        case_dir=tmp_path, model='fixture', model_label='fixture', cohort_root=tmp_path,
        prompt_profile=SimpleNamespace(transform=lambda value: (value, {})),
        enforce_paid_cache=True, expected_effort='medium')
    capture.start()
    request = {'model': 'fixture', 'reasoning': {'effort': 'medium'}, 'stream': False,
               'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': 'continue'}]}]}
    try:
        for _ in range(9):
            call = urllib.request.Request(capture.base_url + '/responses',
                data=json.dumps(request).encode(), headers={'Content-Type': 'application/json'})
            assert json.load(urllib.request.urlopen(call, timeout=10))['status'] == 'completed'
        assert len(upstream_calls) == 9
        assert not (tmp_path / 'dispatch_hold.json').exists()
        alerts = tmp_path / 'cache_alerts.jsonl'
        assert alerts.exists() == expected_alert
        if expected_alert:
            rows = [json.loads(line) for line in alerts.read_text().splitlines()]
            assert rows[-1]['threshold'] == .5
            assert rows[-1]['action'] == 'alert_only'
            assert rows[-1]['dispatch_paused'] is False
            assert rows[-1]['evidence']['weighted_ratio'] == .499
        call = urllib.request.Request(capture.base_url + '/responses',
            data=json.dumps(request).encode(), headers={'Content-Type': 'application/json'})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(call, timeout=10)
        assert error.value.code == 401
        hold = json.loads((tmp_path / 'dispatch_hold.json').read_text())
        assert hold['kind'] == 'provider_terminal_or_protocol_error'
    finally:
        capture.stop()
        adapter.stop()
