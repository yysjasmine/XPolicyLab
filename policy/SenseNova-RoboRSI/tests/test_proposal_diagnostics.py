"""Proposal diagnostics cannot change prediction, timing or execution inputs."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from importlib import import_module
import json
from pathlib import Path
import random

import pytest

P = Path(__file__).resolve().parents[1]
runtime = import_module("XPolicyLab.policy.SenseNova-RoboRSI.execution_profile").runtime_modules(P)
diagnostics = import_module("proposal_diagnostics")
DIMENSIONS = dict(arm_dim=[6, 6], ee_dim=[1, 1])


@pytest.fixture(autouse=True)
def offline_dimensions(monkeypatch):
    monkeypatch.setattr(runtime.execution, "_action_dimensions", lambda: deepcopy(DIMENSIONS))


def fixture():
    state = {}
    for arm in ("left", "right"):
        state.update({arm+"_arm_joint_state": [0.]*6, arm+"_ee_joint_state": [1.],
                      arm+"_ee_pose": [0., 0., 0., 1., 0., 0., 0.]})
    actions = [[0.]*6+[1.]+[0.]*6+[max(0., 1.-i*.04)] for i in range(1, 51)]
    targets = [{arm: dict(position=[0., 0., i*.01 if arm == "right" else 0.],
                         quaternion_wxyz=[-1., 0., 0., 0.],
                         gripper_opening=actions[i-1][6 if arm == "left" else 13])
                for arm in ("left", "right")} for i in range(1, 51)]
    proposal = dict(proposal_id="fixture", control_step=0, horizon=50, subgoal="Move the left gripper.",
                    targets=targets, out_of_bounds=[[] for _ in targets])
    return proposal, actions, dict(control_step=0, state=state)


def test_full_path_prefixes_first_step_and_no_mutation():
    proposal, actions, observation = fixture()
    before = deepcopy((proposal, actions, observation))
    rng = random.getstate()
    aid = diagnostics.build_review_aid(proposal, actions, observation, dimensions=DIMENSIONS)
    assert (proposal, actions, observation) == before
    assert random.getstate() == rng
    assert aid["arms"]["left"]["max_displacement_cm"] == 0
    assert aid["arms"]["right"]["max_displacement_cm"] == pytest.approx(50.)
    assert aid["arms"]["right"]["path_length_cm"] == pytest.approx(50.)
    assert diagnostics.PREVIEW_STEPS == tuple(range(5, 51, 5))
    assert list(aid["prefixes"]) == [str(n) for n in range(5, 51, 5)]
    for n in range(5, 51, 5):
        assert aid["prefixes"][str(n)]["right"]["delta_cm"] == pytest.approx([0., 0., n])
        assert aid["prefixes"][str(n)]["right"]["path_cm"] == pytest.approx(n)
    assert aid["prefixes"]["5"]["right"]["delta_cm"] == pytest.approx([0., 0., 5.])
    assert aid["arms"]["right"]["end_rotation_deg"] == 0  # q and -q are equivalent
    actions[0][0] = .2
    aid = diagnostics.build_review_aid(proposal, actions, observation, dimensions=DIMENSIONS)
    assert aid["arms"]["left"]["max_joint_step_at"] == 1
    assert aid["arms"]["left"]["max_joint_step_rad"] == .2


def wrapped(tmp_path, *, cached=True):
    proposal, actions, observation = fixture()

    class Base:
        def __init__(self):
            self.record_dir = tmp_path
            self.inferences = self.observations = 0

        def _transition(self):
            self.observations += 1
            return dict(observation=deepcopy(observation))

        def vla_propose(self, subgoal):
            self.inferences += 1
            record = dict(proposal_id=proposal["proposal_id"], raw_joint_actions=actions,
                          clipped_joint_actions=actions)
            with (tmp_path/"vla_proposals_private.jsonl").open("a") as stream:
                stream.write(json.dumps(record)+"\n")
            return deepcopy(proposal)

    adapter = runtime.execution.adapter_class(Base)()
    if cached:
        adapter._transition()
    return adapter, proposal, actions


def test_wrapper_and_public_return_preserve_original_buffer(tmp_path):
    adapter, original, actions = wrapped(tmp_path)
    proposal = adapter.vla_propose(original["subgoal"])
    assert adapter.inferences == adapter.observations == 1
    assert adapter._joint_proposal["actions"] == actions
    assert proposal["targets"] == original["targets"]
    public = runtime.robot_tools.sparse_proposal(proposal)
    assert public["proposal_id"] == original["proposal_id"]
    assert [row["step"] for row in public["preview"]] == list(diagnostics.PREVIEW_STEPS)
    assert public["preview"][-1]["right"]["path_cm"] == 50
    assert "targets" not in public and "review_aid" not in public


def test_unavailable_diagnostics_do_not_reject_proposal(tmp_path):
    adapter, original, actions = wrapped(tmp_path, cached=False)
    proposal = adapter.vla_propose(original["subgoal"])
    assert proposal["review_aid"]["status"] == "unavailable"
    assert adapter._joint_proposal["actions"] == actions
    assert proposal["targets"] == original["targets"]


def test_actual_public_adapter_caches_reset_and_ack_state(reference, robot, tmp_path, vla, monkeypatch):
    fixtures = import_module("XPolicyLab.policy.SenseNova-RoboRSI.tests.test_hybrid_backend")
    directory = tmp_path/"public-episode"
    directory.mkdir()
    wrapped_reference = replace(reference, HybridAdapter=runtime.execution.adapter_class(reference.HybridAdapter))
    backend = fixtures.HybridBackend(wrapped_reference, task_name="align_blocks", episode_dir=directory,
        initial_observation=fixtures.observation(), step_limit=200, metadata_path=robot.metadata,
        expected_metadata_sha256=fixtures.sha(robot.metadata), resource_root=robot.root, vla_port=19802)
    try:
        reset = backend.request(op="reset", episode=dict(task="align_blocks", robot="arx_x5"),
                                record_dir=directory/"records", observation_dir=directory/"observations")
        assert backend._adapter._review_aid_observation["state"] == reset["observation"]["state"]
        original_get_obs = backend._adapter.env.get_obs
        reads = []

        def get_obs():
            reads.append(backend.control_step)
            return original_get_obs()

        monkeypatch.setattr(backend._adapter.env, "get_obs", get_obs)
        proposal = backend.request(op="vla_propose", subgoal="Move the left gripper.")
        assert proposal["review_aid"]["status"] == "available"
        assert proposal["review_aid"]["control_step"] == backend.control_step == 0
        assert reads == [0] and len(vla.inputs) == 1
        assert len(backend._adapter._joint_proposal["actions"]) == 50
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(backend.request, op="step", action=dict(mode="stored_vla_joint",
                proposal_id=proposal["proposal_id"], index=0, reason="Synthetic review",
                assessment=dict(current_subgoal="Synthetic test")))
            pending = backend.next_action(timeout=5)
            acknowledged = fixtures.observation()
            acknowledged["state"]["left_ee_joint_state"][:] = 0.
            backend.acknowledge(pending.request_id, acknowledged)
            result = future.result(timeout=5)
        assert result["observation"]["control_step"] == 1
        assert backend._adapter._review_aid_observation["control_step"] == 1
        reads.clear()
        next_proposal = backend.request(op="vla_propose", subgoal="Lift the left gripper.")
        assert next_proposal["review_aid"]["status"] == "available"
        assert next_proposal["review_aid"]["control_step"] == 1
        assert next_proposal["review_aid"]["arms"]["left"]["gripper"]["start"] == 0.
        assert reads == [1] and len(vla.inputs) == 2
        assert backend.control_step == 1
    finally:
        backend.close()


def test_public_tool_only_exposes_ten_merged_points_without_files(tmp_path):
    adapter, original, actions = wrapped(tmp_path)
    proposal = adapter.vla_propose(original["subgoal"])

    def call(request):
        if request["op"] == "observe":
            return dict(done=False, observation=dict(review_due=False))
        assert request == dict(op="vla_propose", subgoal=original["subgoal"])
        return deepcopy(proposal)

    before = deepcopy(proposal)
    tools = runtime.robot_tools.RobotTools(call, setting="simate_vla")
    public = tools.dispatch("vla_propose", dict(subgoal=original["subgoal"]))
    assert set(public) == {"proposal_id", "control_step", "preview"}
    assert public["proposal_id"] == "p1"
    assert [row["step"] for row in public["preview"]] == list(diagnostics.PREVIEW_STEPS)
    for row in public["preview"]:
        for arm in ("left", "right"):
            assert set(row[arm]) == {"pos_m", "angles_deg", "grip", *diagnostics.PREFIX_FIELDS}
            assert len(row[arm]["pos_m"]) == len(row[arm]["angles_deg"]) == 3
    assert not (tmp_path/"workspace/vla-review-aids").exists()
    assert not hasattr(tools, "_save_review_aid")
    assert proposal == before
    assert proposal["review_aid"]["proposal_id"] == original["proposal_id"]
    assert proposal["targets"] == original["targets"]
    assert adapter._joint_proposal["actions"] == actions


def test_prefix_statistics_include_skipped_excursion_and_returned_gripper():
    proposal, actions, observation = fixture()
    # Step 6 is never a public target. Include its excursion in the step-10 summary.
    proposal["targets"][5]["left"]["position"][0] = .2
    actions[5][6] = .2
    proposal["targets"][5]["left"]["gripper_opening"] = .2
    proposal["out_of_bounds"][5] = ["left: displacement exceeds 5cm"]
    proposal["out_of_bounds"][6] = ["left: displacement exceeds 5cm"]
    proposal["review_aid"] = diagnostics.build_review_aid(
        proposal, actions, observation, dimensions=DIMENSIONS)
    before = deepcopy((proposal, actions, observation))
    public = runtime.robot_tools.sparse_proposal(proposal)
    five, ten = public["preview"][:2]
    assert five["bounds"] == []
    assert ten["bounds"] == ["left: displacement exceeds 5cm"]
    assert ten["left"]["delta_cm"] == [0., 0., 0.]
    assert ten["left"]["path_cm"] == 40.
    assert ten["left"]["max_step_cm"] == 20.
    assert ten["left"]["grip_delta"] == 0.
    assert ten["left"]["grip_range"] == [.2, 1.]
    assert ten["left"]["max_joint_step_rad"] == 0.
    assert (proposal, actions, observation) == before


def test_missing_diagnostics_remain_explicit_without_fabricating_zero_metrics(tmp_path):
    adapter, original, actions = wrapped(tmp_path, cached=False)
    proposal = adapter.vla_propose(original["subgoal"])
    public = runtime.robot_tools.sparse_proposal(proposal)
    assert public["diagnostics"] == "unavailable"
    assert len(public["preview"]) == 10
    assert all(set(row[arm]) == {"pos_m", "angles_deg", "grip"}
               for row in public["preview"] for arm in ("left", "right"))
    assert adapter._joint_proposal["actions"] == actions
