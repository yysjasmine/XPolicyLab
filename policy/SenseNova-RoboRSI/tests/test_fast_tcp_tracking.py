"""A distant TCP goal gets larger native updates without bypassing host bounds."""
import importlib
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation


P = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(P / "skillpack/robot_runtime"))
sys.path.insert(0, str(P / "vendor/robodojo_adaptive/source/code/configurations/robodojo/20261005_standard_1x_10x/adapters"))
execution = importlib.import_module("execution")
kinematics = importlib.import_module("eef_kinematics")


def _goal(left_x, opening=1):
    return {arm: {"position": [left_x if arm == "left" else 0, 0, 0],
                  "quaternion_wxyz": [1, 0, 0, 0], "gripper_opening": opening}
            for arm in ("left", "right")}


def test_open_gripper_fast_approach_then_fine_tracking():
    state = {f"{arm}_ee_pose": [0, 0, 0, 1, 0, 0, 0] for arm in ("left", "right")}
    fast, errors = execution.tracking_geometry(state, _goal(.2))
    assert errors["left"]["position_error_m"] == pytest.approx(.2)
    assert .02 < fast["left"]["position"][0] <= .045 + 1e-9
    assert fast["right"]["position"][0] == pytest.approx(0)
    fine, _ = execution.tracking_geometry(state, _goal(.03))
    assert fine["left"]["position"][0] == pytest.approx(.02)
    carrying, _ = execution.tracking_geometry(state, _goal(.2, opening=0))
    assert carrying["left"]["position"][0] == pytest.approx(.02)

    # With ideal tracking, a 20 cm pregrasp needs five native updates rather
    # than ten updates at the former fixed 2 cm cap.
    updates = 0
    while True:
        targets, residual = execution.tracking_geometry(state, _goal(.2))
        if residual["left"]["position_error_m"] <= execution.POSITION_TOLERANCE_M:
            break
        state["left_ee_pose"] = [*targets["left"]["position"],
                                 *targets["left"]["quaternion_wxyz"]]
        updates += 1
        assert updates <= 6
    assert updates == 5


def test_small_orientation_error_does_not_halve_valid_fast_step():
    state = {f"{arm}_ee_pose": [0, 0, 0, 1, 0, 0, 0] for arm in ("left", "right")}
    q = Rotation.from_euler("z", .02).as_quat()[[3, 0, 1, 2]].tolist()
    goal = _goal(.2)
    goal["left"]["quaternion_wxyz"] = q
    target, _ = execution.tracking_geometry(state, goal)
    displacement = np.linalg.norm(target["left"]["position"])
    assert .044 < displacement <= .05 + 1e-9


def test_legacy_ik_accepts_useful_joint_step_above_previous_limit():
    arm = object.__new__(kinematics.ArmFK)

    def fk(q):
        pose = np.eye(4)
        pose[0, 3] = .05 * q[0]
        return pose

    arm.names = [f"joint{i}" for i in range(6)]
    arm.matrix = fk
    current = np.zeros(6)
    limits = np.tile([-2., 2.], (6, 1))
    joints, diagnostics = arm.bounded_target(
        current, limits, np.eye(4),
        {"position": [.045, 0, 0], "quaternion_wxyz": [1, 0, 0, 0]})
    assert .05 < joints[0] <= .4 + 1e-6
    assert np.max(np.abs(joints - current)) <= .4 + 1e-6
    assert diagnostics["line_search_scale"] > 0


def test_legacy_large_joint_step_preserves_predicted_pose_bound():
    arm = object.__new__(kinematics.ArmFK)
    arm.names = [f"joint{i}" for i in range(6)]

    def nonlinear_fk(q):
        pose = np.eye(4)
        pose[0, 3] = .05*q[0] + .5*q[0]**2
        return pose

    arm.matrix = nonlinear_fk
    joints, diagnostics = arm.bounded_target(
        np.zeros(6), np.tile([-2., 2.], (6, 1)), np.eye(4),
        {"position": [.045, 0, 0], "quaternion_wxyz": [1, 0, 0, 0]})
    assert 0 < nonlinear_fk(joints)[0, 3] <= .05 + 1e-9
    assert diagnostics["line_search_scale"] < 1
