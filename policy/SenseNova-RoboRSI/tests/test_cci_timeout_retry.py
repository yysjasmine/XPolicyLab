"""Exercise the CCI timeout through the real adapter and buffered capture."""
from importlib import import_module
import json
from pathlib import Path
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

cci = import_module("XPolicyLab.policy.SenseNova-RoboRSI.cci_protocol")
guard = import_module("XPolicyLab.policy.SenseNova-RoboRSI.skillpack.capture_guard")
config = import_module("roborsi.planner.provider_capture").ProviderCaptureConfig


@pytest.mark.parametrize("outcome", ["recover", "exhaust", "authentication"])
def test_bounded_timeout_retains_request_and_original_budget(tmp_path, monkeypatch, outcome):
    original_open = urllib.request.urlopen
    payloads, timeouts = [], []

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            return json.dumps({"id": "ok", "status": "completed", "output": [{
                "type": "message", "role": "assistant", "content": [{
                    "type": "output_text", "text": "third attempt only"}]}],
                "usage": {"input_tokens": 10, "output_tokens": 1,
                          "input_tokens_details": {"cached_tokens": 0}}}).encode()

    def upstream(request, *args, **kwargs):
        if not request.full_url.startswith("http://provider.invalid/"):
            return original_open(request, *args, **kwargs)
        payloads.append(request.data)
        timeouts.append(kwargs["timeout"])
        if outcome == "authentication":
            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)
        if outcome == "exhaust" or len(payloads) < 3:
            raise TimeoutError("timed out")
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", upstream)
    sampling = {"temperature": 1.0, "top_k": 20, "top_p": 0.95}
    adapter = cci.CciAdapter("http://provider.invalid/v1", "fixture", tmp_path/"cci", sampling=sampling)
    assert adapter.request_timeout == 300
    adapter.start()
    deadline = time.time()+60
    capture = guard.GuardedCapture(config(adapter.base_url, "runtime"), tmp_path/"api",
        case_dir=tmp_path, model="fixture", model_label="fixture", cohort_root=tmp_path,
        prompt_profile=SimpleNamespace(transform=lambda value: (value, {})),
        case_deadline=deadline, enforce_paid_cache=False, expected_effort="medium")
    capture.start()
    request = {"model": "fixture", "reasoning": {"effort": "medium"}, "stream": False,
               "input": [{"role": "user", "content": [{"type": "input_text", "text": "one request"}]}]}
    try:
        call = urllib.request.Request(capture.base_url+"/responses", data=json.dumps(request).encode(),
                                      headers={"Content-Type": "application/json"})
        if outcome == "recover":
            response = json.load(urllib.request.urlopen(call, timeout=30))
            assert response["status"] == "completed"
            assert response["output"][0]["content"][0]["text"] == "third attempt only"
            assert [row["completed"] for row in capture.rows] == [False, False, True]
            assert not (tmp_path/"dispatch_hold.json").exists()
        else:
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(call, timeout=30)
            assert error.value.code == (401 if outcome == "authentication" else 502)
            hold = json.loads((tmp_path/"dispatch_hold.json").read_text())
            assert hold["kind"] == ("provider_terminal_or_protocol_error" if outcome == "authentication"
                                     else "transient_retry_exhausted")
        expected = 1 if outcome == "authentication" else 3
        assert len(payloads) == capture._attempts == expected
        assert len(set(payloads)) == 1
        actual = json.loads(payloads[0])
        assert {k: actual[k] for k in sampling} == sampling
        assert actual["reasoning"]["effort"] == actual["chat_template_kwargs"]["reasoning_effort"] == "medium"
        assert timeouts == [300]*expected
        assert capture.case_deadline == deadline
        assert len(list((tmp_path/"cci").glob("*.request.json"))) == expected
        if outcome != "authentication":
            error_body = json.loads((tmp_path/"cci/000001.adapter_error.json").read_text())
            assert error_body["error"]["type"] == "cci_request_timeout"
            assert error_body["error"]["timeout_seconds"] == 300
    finally:
        capture.stop()
        adapter.stop()


def test_completed_requests_continue_past_former_total_call_limit(tmp_path, monkeypatch):
    """Keep real buffered transport/accounting while crossing both old caps."""
    original_open = urllib.request.urlopen

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            return json.dumps({"id": "after-old-cap", "status": "completed", "output": [],
                "usage": {"input_tokens": 10, "output_tokens": 1,
                          "input_tokens_details": {"cached_tokens": 0}}}).encode()

    def upstream(request, *args, **kwargs):
        return Response() if request.full_url.startswith("http://provider.invalid/") else original_open(request, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", upstream)
    adapter = cci.CciAdapter("http://provider.invalid/v1", "fixture", tmp_path/"cci")
    adapter.start()
    prior = [{"request_id": i+1, "completed": True, "usage": {"input_tokens": 10},
              "request_kind": "turn"} for i in range(350)]
    capture = guard.GuardedCapture(config(adapter.base_url, "runtime"), tmp_path/"api",
        case_dir=tmp_path, model="fixture", model_label="fixture", cohort_root=tmp_path,
        prior_rows=prior, prompt_profile=SimpleNamespace(transform=lambda value: (value, {})),
        enforce_paid_cache=False, expected_effort="medium")
    capture.start()
    request = {"model": "fixture", "reasoning": {"effort": "medium"}, "stream": False,
               "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}]}
    try:
        call = urllib.request.Request(capture.base_url+"/responses", data=json.dumps(request).encode(),
                                      headers={"Content-Type": "application/json"})
        response = json.load(urllib.request.urlopen(call, timeout=30))
        assert response["status"] == "completed"
        assert capture._attempts == 351 and sum(x["completed"] for x in capture.rows) == 351
        assert not (tmp_path/"dispatch_hold.json").exists()
        assert capture.case_deadline is None
    finally:
        capture.stop()
        adapter.stop()
