"""An operator stop prevents another provider call while native cleanup finishes."""
import asyncio
from importlib import import_module
import json


def test_completed_smoke_does_not_dispatch_or_consume_model_budget(tmp_path):
    module = import_module("XPolicyLab.policy.SenseNova-RoboRSI.skillpack.capture_guard")
    config = import_module("roborsi.planner.provider_capture").ProviderCaptureConfig
    (tmp_path/"user_smoke_complete.json").write_text('{"executed_act_calls":3}')
    capture = module.GuardedCapture(config("http://127.0.0.1:1/v1", "runtime"), tmp_path/"api",
                                    case_dir=tmp_path, model="fixture", model_label="fixture",
                                    cohort_root=tmp_path)
    messages = []
    async def send(message): messages.append(message)
    asyncio.run(capture._forward({"path": "/v1/responses"}, json.dumps({"model": "fixture"}).encode(), send))
    assert messages[0]["status"] == 409
    assert capture._attempts == 0
    assert capture.rows == []
    assert (tmp_path/"post_smoke.original_cli_request.json").exists()
