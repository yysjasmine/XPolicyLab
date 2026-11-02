"""Compaction keeps the model's summary and the unsummarized suffix unchanged."""
import asyncio
from copy import deepcopy
from importlib import import_module
from pathlib import Path

import pytest

module = import_module("XPolicyLab.policy.SenseNova-RoboRSI.skillpack.context_windows")
ROOT = Path(__file__).resolve().parents[1]/"skillpack"


def history(images):
    return [{"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "fixed core"}]},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "current episode"}]},
            {"type": "function_call_output", "call_id": "observed", "output":
             [{"type": "input_image", "image_url": "image:"+str(i)} for i in range(images)]}]


@pytest.mark.parametrize("trigger", ["images", "rounds", "tokens"])
def test_real_trigger_replaces_only_covered_history(trigger):
    async def run():
        window = module.ContextWindows(ROOT)
        previous = history(48 if trigger == "images" else 3)
        window.completed(previous, previous, {"input_tokens": 199999 if trigger == "tokens" else 10000})
        if trigger == "rounds": window.calls = 120
        suffix = [{"type": "function_call_output", "call_id": "latest", "output":
                   [{"type": "input_image", "image_url": "new:"+str(i)} for i in range(3)]}]
        raw = previous+suffix
        captured = []
        async def summarize(request, supplied, reasons):
            captured.append((deepcopy(supplied), reasons))
            return "Exact public continuation summary from the model."
        request = {"model": "fixture", "input": raw}
        result, evidence, original = await window.prepare(request, summarize)
        assert captured[0][0] == previous
        assert original == raw and request["input"] == raw
        assert result["input"][-1:] == suffix
        assert result["input"][2]["content"][0]["text"] == window.prefix+"Exact public continuation summary from the model."
        assert module.image_count(result["input"]) == 3
        assert evidence["triggers"]
        # A following call appends to the exact same new prefix.
        window.completed(raw, result["input"], {"input_tokens": 4000})
        added = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "next"}]}
        following, evidence2, _ = await window.prepare({"model": "fixture", "input": raw+[added]}, summarize)
        assert following["input"] == result["input"]+[added]
        assert not evidence2["triggers"] and len(captured) == 1
    asyncio.run(run())


def test_no_history_is_not_invented_for_an_oversized_initial_request():
    async def run():
        window = module.ContextWindows(ROOT)
        async def forbidden(*args): raise AssertionError("No past history exists")
        with pytest.raises(ValueError, match="no past window"):
            await window.prepare({"input": history(51)}, forbidden)
    asyncio.run(run())
