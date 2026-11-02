"""Execution material selection, independent of simulator or paid provider calls."""
from importlib import import_module
import hashlib
import json
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace

import pytest

profile = import_module("XPolicyLab.policy.SenseNova-RoboRSI.execution_profile")
P = Path(__file__).resolve().parents[1]


def test_largest_workspace_has_only_two_single_markdown_skills(tmp_path):
    context = profile.prepare_skillpack_context(P, "arrange_largest_number", tmp_path)
    visible = sorted(p.name for p in (context.workspace/".agents/skills").iterdir())
    assert visible == ["arrange_largest_number", "robo-general"]
    assert not (context.workspace/"AGENTS.md").exists()
    assert not (context.workspace/"README.md").exists()
    assert not (context.workspace/"preparation/demonstrations.md").exists()
    assert len(list(context.workspace.rglob("SKILL.md"))) == 2
    assert all(p.stat().st_size <= 8192 for p in context.workspace.rglob("*.md"))
    assert context.source_identity["raw_icl_materialized"] is False
    assert context.source_identity["runtime_subagents_enabled"] is False


def test_open_task_has_no_case_skill(tmp_path):
    index = json.loads((P/"skillpack/cases/catalog.json").read_text())
    task = next(name for name, row in index["tasks"].items() if row["kind"] == "open")
    context = profile.prepare_skillpack_context(P, task, tmp_path)
    visible = sorted(p.name for p in (context.workspace/".agents/skills").iterdir())
    assert visible == ["robo-general"]
    derived = profile.load_skillpack_profile(P, task, tmp_path)
    assert "arrange_largest_number:" not in derived.skills_instruction


def test_shared_bytes_and_tools_are_unchanged(tmp_path):
    context = profile.prepare_skillpack_context(P, "arrange_largest_number", tmp_path)
    derived = profile.load_skillpack_profile(P, "arrange_largest_number", tmp_path)
    common = P/"skillpack/common"
    for name in ("agent_core.md", "native_tools.json", "robot_tools.json", "actual_robot_declarations.json", "skills_instruction.md"):
        assert (derived.directory/name).read_bytes() == (common/name).read_bytes()
    general = context.workspace/".agents/skills/robo-general/SKILL.md"
    assert general.read_bytes() == (common/"skills/robo-general/SKILL.md").read_bytes()
    cfg = {"options": {"review_window_steps": 1050, "review_max_windows": 1}, "episode_seconds": 14000}
    prompt = derived.episode_prompt(cfg)
    assert "smoke" not in prompt.lower() and "three" not in prompt.lower()
    assert derived.expected_tools == {"observe", "vla_propose", "act"}


def test_unknown_task_and_cross_task_profile_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="Unknown public"):
        profile.load_skillpack_profile(P, "made_up_task", tmp_path)
    profile.load_skillpack_profile(P, "arrange_largest_number", tmp_path)
    with pytest.raises(ValueError, match="another task"):
        profile.load_skillpack_profile(P, "stack_bowls", tmp_path)


def test_cci_reasoning_null_content_does_not_rewrite_visible_history():
    raw = {"input": [{"type": "reasoning", "content": None, "encrypted_content": "opaque"},
                     {"role": "assistant", "content": [{"type": "output_text", "text": "visible"}]}]}
    frozen = SimpleNamespace(transform=lambda value: (deepcopy(value), {}))
    result, evidence = profile.CciPromptProfile(frozen).transform(raw)
    assert raw["input"][0]["content"] is None
    assert result["input"][0]["content"] == []
    assert result["input"][0]["encrypted_content"] == "opaque"
    assert result["input"][1] == raw["input"][1]
    assert evidence["cci_null_content_as_empty_array"] == [0]
