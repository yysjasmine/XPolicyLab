"""Task isolation, recovery and RGB geometry-to-memory integration, offline."""
from importlib import import_module, util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

execution = import_module("XPolicyLab.policy.SenseNova-RoboRSI.execution_profile")
POLICY = Path(__file__).resolve().parents[1]
SKILL = POLICY / "skillpack/common/skills/robodojo-spatial-localize"
spec = util.spec_from_file_location("spatial_memory_helper", SKILL / "scripts/space_memory.py")
memory_helper = util.module_from_spec(spec)
spec.loader.exec_module(memory_helper)


def test_staging_preserves_memory_on_recovery_and_isolates_new_runtime(tmp_path):
    first = tmp_path / "first"
    first.mkdir()
    context = execution.prepare_skillpack_context(POLICY, "classify_objects", first)
    memory = context.workspace / "space.json"
    state = memory_helper.load(memory)
    state["objects"] = {"doll_a": {"center": {"position_m": [0.1, 0.2, 0.8], "status": "stale"}}}
    memory_helper.save(memory, state)
    profile = execution.load_skillpack_profile(POLICY, "classify_objects", first)
    profile.stage_live_workspace(context.workspace, owner=first)
    assert memory_helper.load(memory) == state
    # The recovery path copies the SAME episode workspace before staging again.
    resumed = first / "resumed"
    shutil.copytree(context.workspace, resumed)
    profile.stage_live_workspace(resumed, owner=first)
    assert memory_helper.load(resumed / "space.json") == state
    second = tmp_path / "second"
    second.mkdir()
    fresh = execution.prepare_skillpack_context(POLICY, "classify_objects", second)
    assert memory_helper.load(fresh.workspace / "space.json")["objects"] == {}
    text = (fresh.workspace / "preparation/agent_system.md").read_text()
    assert "/workspace/space.json" in text and "read, write and modify" in text
    assert "robodojo-spatial-localize" in profile.skills_instruction
    assert (fresh.workspace / ".agents/skills/robodojo-spatial-localize/scripts/localize_rgb.py").is_file()
    assert not list(fresh.workspace.rglob("*.pyc"))
    wire, _ = profile.transform({"input": [
        {"role": "system", "content": [{"type": "input_text", "text": profile.core}]},
        {"type": "additional_tools", "tools": profile.deferred}]})
    prompt = json.dumps(wire["input"])
    assert "/workspace/space.json" in prompt and "robodojo-spatial-localize" in prompt


def test_refreshing_restored_skills_preserves_spatial_memory(tmp_path):
    context = execution.prepare_skillpack_context(POLICY, "classify_objects", tmp_path)
    memory = context.workspace / "space.json"
    state = memory_helper.load(memory)
    state["objects"] = {"block": {"center": {"position_m": [0.1, 0.2, 0.8], "status": "stale"}}}
    memory_helper.save(memory, state)
    general = context.workspace / ".agents/skills/robo-general/SKILL.md"
    general.write_text("Old skill copied from the previous attempt.\n")

    profile = execution.load_skillpack_profile(POLICY, "classify_objects", tmp_path)
    receipt = profile.stage_live_workspace(context.workspace, owner=tmp_path)

    assert memory_helper.load(memory) == state
    assert receipt["spatial_memory"] == "space.json"
    assert ".agents/skills/robo-general/SKILL.md" in receipt["refreshed_assets"]
    assert general.read_bytes() == (POLICY / "skillpack/common/skills/robo-general/SKILL.md").read_bytes()
    refresh = profile.skill_refresh_prompt()
    assert "Episode memory and finishing" in refresh
    assert "end your response only after native done" in refresh
    assert (context.workspace / ".agents/skills/robodojo-spatial-localize/scripts/space_memory.py").is_file()


def test_reject_cross_task_memory(tmp_path):
    context = execution.prepare_skillpack_context(POLICY, "classify_objects", tmp_path)
    memory = context.workspace / "space.json"
    state = memory_helper.load(memory)
    state["task"] = "push_T"
    memory_helper.save(memory, state)
    profile = execution.load_skillpack_profile(POLICY, "classify_objects", tmp_path)
    with pytest.raises(ValueError, match="another task"):
        profile.stage_live_workspace(context.workspace, owner=tmp_path)


def test_geometry_rgb_cli_import_and_invalidate(tmp_path):
    from PIL import Image
    Image.new("RGB", (100, 100), (100, 20, 30)).save(tmp_path / "rgb.png")
    case = {"frame": "environment_origin", "units": "meters", "cameras": {
        "head": {"image": "rgb.png", "K": [[100, 0, 50], [0, 100, 50], [0, 0, 1]],
                 "T_base_camera_cv": [[1, 0, 0, 0], [0, -1, 0, 0], [0, 0, -1, 1], [0, 0, 0, 1]]}},
        "support_planes": {"table": {"frame": "environment_origin", "normal": [0, 0, 1],
            "offset_m": 0, "height_sigma_m": 0, "source": "synthetic known plane"}}}
    targets = {"targets": [{"id": "contact", "object_id": "red_block", "description": "visible table contact",
        "kind": "support_plane", "support_plane": "table", "height_above_plane_m": 0,
        "observations": {"head": {"points_px": [[50, 50]]}},
        "plane_options": {"max_position_sigma_m": 0.02}}]}
    for name, data in [("case.json", case), ("targets.json", targets)]:
        (tmp_path / name).write_text(json.dumps(data))
    subprocess.run([sys.executable, "-B", str(SKILL / "scripts/localize_rgb.py"), "--case", str(tmp_path / "case.json"),
                    "--targets", str(tmp_path / "targets.json"), "--output", str(tmp_path / "geometry")], check=True)
    result = json.loads((tmp_path / "geometry/points3d.json").read_text())
    assert result["points"][0]["estimate"]["accepted"] is True
    state = {"schema_version": 1, "task": "classify_objects", "frame": "environment_origin",
             "units": "meters", "objects": {"unrelated": {}}}
    memory_helper.import_result(state, result, evidence="geometry/points3d.json", step=42)
    point = state["objects"]["red_block"]["contact"]
    assert point["position_m"] == pytest.approx([0, 0, 0])
    assert point["status"] == "localized" and point["control_step"] == 42
    assert "unrelated" in state["objects"]
    memory = tmp_path / "space.json"
    memory_helper.save(memory, state)
    subprocess.run([sys.executable, str(SKILL / "scripts/space_memory.py"), "stale", "--memory", str(memory),
                    "--object", "red_block"], check=True)
    assert memory_helper.load(memory)["objects"]["red_block"]["contact"]["status"] == "stale"
    result["frame"] = "other_robot_base"
    with pytest.raises(ValueError, match="frame/units"):
        memory_helper.import_result(state, result, evidence="wrong.json", step=43)


def test_rejected_estimate_never_promotes_a_position():
    state = {"frame": "environment_origin", "units": "meters", "objects": {
        "block": {"top": {"position_m": [1, 2, 3], "status": "localized"}}}}
    result = {"frame": "environment_origin", "units": "meters", "points": [{
        "object_id": "block", "id": "top", "estimate": {"accepted": False, "position_base_m": [9, 9, 9]}}]}
    memory_helper.import_result(state, result, evidence="rejected.json", step=4)
    assert state["objects"]["block"]["top"]["position_m"] == [1, 2, 3]
    assert state["objects"]["block"]["top"]["status"] == "stale"
