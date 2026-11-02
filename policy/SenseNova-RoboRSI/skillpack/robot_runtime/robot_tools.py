"""Three public tools over the original RoboDojo episode's robot bridge.

The bridge retains full proposals, permissions, IK, native state and budgets.
The public tool interface uses grasp-centre TCP poses; native bridge/IK poses
remain link6. The adapter also samples trajectories for display only.
"""

from __future__ import annotations

import base64
from copy import deepcopy
import json
from pathlib import Path
import socket
import threading
from typing import Any, Callable

from tcp_pose import (
    POSE_CONTRACT,
    link6_to_public_pose,
    link6_to_tcp_target,
    tcp_to_link6_target,
)


class ToolInputError(ValueError):
    """A recoverable public tool argument error."""


class RobotBridge:
    """Send one request to an existing episode; never retry uncertain calls."""

    def __init__(self, socket_path: str, timeout: float = 1200):
        self.socket_path = socket_path
        self.timeout = timeout

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(self.timeout)
            connection.connect(self.socket_path)
            connection.sendall((json.dumps(request, allow_nan=False) + "\n").encode())
            with connection.makefile("rb") as stream:
                line = stream.readline(16 * 1024 * 1024 + 1)
        if not line or len(line) > 16 * 1024 * 1024:
            raise RuntimeError("Incomplete robot reply; observe before any further motion")
        result = json.loads(line)
        if "error" in result:
            # This is a complete, explicit rejection from the original bridge.
            raise ToolInputError(result["error"])
        return result


def _object(properties: dict, required: list[str]) -> dict:
    return dict(type="object", properties=properties, required=required,
                additionalProperties=False)


TEXT = dict(type="string", minLength=1)
REVIEW = _object({key: TEXT for key in (
    "observed_state", "demo_comparison", "why_not_done", "next_plan", "ending_check"
)}, ["observed_state", "demo_comparison", "why_not_done", "next_plan", "ending_check"])
ARM = _object({
    "position": dict(type="array", items=dict(type="number"), minItems=3, maxItems=3,
                     description="Absolute gripping-face-centre TCP XYZ in environment-origin metres."),
    "pitch_deg": dict(type="number", minimum=-180, maximum=180,
                      description="Degrees about environment +X, relative to the downward reference."),
    "roll_deg": dict(type="number", minimum=-90, maximum=90,
                     description="Degrees about environment +Y, applied after pitch."),
    "yaw_deg": dict(type="number", minimum=-180, maximum=180,
                    description="Degrees about environment +Z, applied after roll."),
    "gripper_opening": dict(type="number", minimum=0, maximum=1,
                            description="Continuous normalized opening: 0 fully closed, 1 fully open."),
}, ["position", "pitch_deg", "roll_deg", "yaw_deg", "gripper_opening"])
DUAL = _object({"left": ARM, "right": ARM}, ["left", "right"])
# The private bridge retains native quaternion poses and continuous openings.
NATIVE_ARM = _object({
    "position": dict(type="array", items=dict(type="number"), minItems=3, maxItems=3),
    "quaternion_wxyz": dict(type="array", items=dict(type="number"), minItems=4, maxItems=4),
    "gripper_opening": dict(type="number", minimum=0, maximum=1),
}, ["position", "quaternion_wxyz", "gripper_opening"])
NATIVE_DUAL = _object({"left": NATIVE_ARM, "right": NATIVE_ARM}, ["left", "right"])


def tool_specs() -> list[dict[str, Any]]:
    """Return three model-facing schemas using the grasp-centre TCP contract."""
    return [
        dict(name="observe", description="Read current RGB, measured TCP positions, pitch/roll/yaw degrees relative to the downward reference, permissions and budgets without advancing physics. Display floats use at most three decimal places.", input_schema=_object({}, [])),
        dict(name="vla_propose", description="Predict a trajectory without moving. Give one short English sentence describing an immediate action, the observed object and a destination when needed; use task/demo wording and omit reasoning, code and multi-step plans. Example: Move the left gripper above the blue 8. Return proposal_id and TCP samples at steps 5,10,...,50. A new request replaces an unexecuted old proposal.", input_schema=_object({"subgoal": {**TEXT, "maxLength": 2000}}, ["subgoal"])),
        dict(name="act", description="Accept stored original VLA joint steps, or track model-supplied TCP targets with internal measured-state feedback and IK. TCP targets use continuous gripper_opening (0 closed, 1 open). Supplying targets declines the proposal. Return reached/stalled/budget status, actual native steps, residual errors, RGB and measured TCP state. Pose arrival does not verify grasp. Keep reason/assessment concise.", input_schema=_object({
            "proposal_id": {**TEXT, "description": "Use only when accepting the current VLA proposal; omit when supplying targets."},
            "steps": dict(type="integer", minimum=1, maximum=50,
                          description="Consecutive prefix length; VLA allows 5..50, default 5."),
            "targets": dict(type="array", items=DUAL, minItems=1, maxItems=50,
                            description="Model-chosen dual-arm TCP waypoints; no proposal_id or reject flag. Harness tracks each to 3mm/3deg before advancing. A waypoint may consume multiple native steps; model-side interpolation is unnecessary."),
            "max_native_steps": dict(type="integer", minimum=1, maximum=160,
                                     description="Shared native-step budget for TCP tracking, default 80. Omit for original VLA acceptance."),
            "reason": TEXT,
            "assessment": _object({"current_subgoal": TEXT}, ["current_subgoal"]),
            "review": {**REVIEW, "description": "Evidence at a required checkpoint. Without proposal_id/steps/targets, records the due review without motion."},
        }, ["reason", "assessment"])),
    ]


def sparse_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    """Expose every fifth target without changing the original proposal."""
    targets = proposal.get("targets")
    if not isinstance(targets, list) or len(targets) != 50:
        raise RuntimeError("Expected the configured full 50-target proposal")
    indices = list(range(4, 50, 5))
    fields = ("status", "proposal_id", "control_step", "subgoal", "horizon",
              "robot_moved", "approved_steps_min", "approved_steps_max",
              "approved_steps_default", "gripper_conversion", "gripper_clipped_values",
              "inference_latency_s", "fk_validation", "execution_mode")
    result = {key: deepcopy(proposal[key]) for key in fields if key in proposal}
    violations = proposal.get("out_of_bounds", [[] for _ in range(50)])
    if not isinstance(violations, list) or len(violations) != 50:
        raise RuntimeError("Expected full-trajectory bound checks")
    result.update(
        targets=[{arm: link6_to_tcp_target(targets[i][arm]) for arm in ("left", "right")}
                 for i in indices],
        selected_waypoint_indices=indices,
        sample_step_numbers=[i + 1 for i in indices],
        display_stride=5, horizon=50, display_only=True,
        out_of_bounds=[deepcopy(violations[i]) for i in indices],
        full_trajectory_out_of_bounds_steps=[i for i, value in enumerate(violations) if value],
        execution="act executes the full stored consecutive prefix, not these samples",
        pose_contract=deepcopy(POSE_CONTRACT),
        display_precision_decimals=3,
    )
    return _round_display(result)


def _round_display(value: Any) -> Any:
    """Round model-facing float outputs; preserve IDs, integers and booleans."""
    if isinstance(value, float):
        rounded = round(value, 3)
        return 0.0 if rounded == 0 else rounded
    if isinstance(value, list):
        return [_round_display(item) for item in value]
    if isinstance(value, dict):
        return {key: _round_display(item) for key, item in value.items()}
    return value


class RobotTools:
    """Map three outer tools to one existing, original episode bridge."""

    def __init__(self, call: Callable[[dict], dict], *, setting: str,
                 direct_act_enabled: bool = False, image_root: Path | None = None):
        if setting not in ("simate_vla", "no_vla"):
            raise ValueError("Explicit simate_vla or no_vla setting required")
        self.call = call
        self.setting = setting
        self.direct_act_enabled = direct_act_enabled
        self.image_root = image_root.resolve(strict=True) if image_root else None
        self._lock = threading.Lock()
        self._specs = {spec["name"]: spec for spec in tool_specs()}

    def get_tools_spec(self) -> list[dict]:
        return deepcopy(list(self._specs.values()))

    def _observe(self) -> dict:
        return self.call({"op": "observe"})

    def _public(self, result: dict) -> dict:
        public = deepcopy(result)
        observation = public.get("observation", {})
        state = observation.get("state", {})
        for arm in ("left", "right"):
            key = arm + "_ee_pose"
            if key in state:
                state[key] = link6_to_public_pose(state[key])
        public["pose_contract"] = deepcopy(POSE_CONTRACT)
        if public.get("pending_vla_proposal") is not None:
            public["pending_vla_proposal"] = sparse_proposal(public["pending_vla_proposal"])
        # Convert from full-precision native poses first, then quantize only
        # the model-visible copy. Native measurements and execution stay raw.
        return _round_display(public)

    def _review(self, latest: dict, review: dict | None) -> dict:
        if latest.get("observation", {}).get("review_due") and review is None:
            raise ToolInputError("Include the checkpoint evidence in act.review")
        if review is not None:
            return self.call({"op": "review", "analysis": review})
        return latest

    def dispatch(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute once, preserving native rejections and uncertain failures."""
        from jsonschema import Draft202012Validator, ValidationError

        if name not in self._specs:
            raise ToolInputError(f"Unknown robot tool: {name}")
        try:
            json.dumps(arguments, allow_nan=False)
            Draft202012Validator(self._specs[name]["input_schema"]).validate(arguments)
        except (ValidationError, ValueError, TypeError) as error:
            raise ToolInputError(str(error)) from error
        with self._lock:
            if name == "observe":
                return self._public(self._observe())
            if name == "vla_propose":
                if self.setting != "simate_vla":
                    raise ToolInputError("VLA is disabled for this episode")
                latest = self._observe()
                if latest.get("done") or latest.get("observation", {}).get("review_due"):
                    raise ToolInputError("Episode ended or checkpoint review is due")
                pending = latest.get("pending_vla_proposal")
                if pending is not None:
                    self.call(dict(op="vla_review", proposal_id=pending["proposal_id"],
                                   decision="reject", targets=None,
                                   reason="Replace the unexecuted proposal with a new prediction",
                                   assessment={"current_subgoal": arguments["subgoal"]}))
                return sparse_proposal(self.call({"op": "vla_propose", **arguments}))
            return self._act(arguments)

    def _act(self, args: dict) -> dict:
        if "review" in args and not any(key in args for key in ("proposal_id", "targets", "steps")):
            latest = self._observe()
            if not latest.get("observation", {}).get("review_due"):
                raise ToolInputError("A review-only act requires a due checkpoint")
            return self._public(self._review(latest, args["review"]))
        targets = args.get("targets")
        if targets is not None and "proposal_id" in args:
            raise ToolInputError("Choose proposal_id with steps, or targets without proposal_id")
        if targets is None and "max_native_steps" in args:
            raise ToolInputError("max_native_steps applies only to model TCP targets")
        steps = args.get("steps", len(targets) if targets is not None else 5)
        if targets is not None and len(targets) != steps:
            raise ToolInputError("steps must match the replacement target count")
        # Convert only caller-supplied TCP targets. An accepted proposal is
        # already stored natively in link6 coordinates and is never shifted.
        native_targets = None
        if targets is not None:
            try:
                native_targets = [{arm: tcp_to_link6_target(dual[arm])
                                   for arm in ("left", "right")} for dual in targets]
            except ValueError as error:
                raise ToolInputError(str(error)) from error
        if self.setting == "simate_vla":
            if targets is None and "proposal_id" not in args:
                raise ToolInputError("Provide proposal_id with steps, or model-chosen TCP targets")
        elif not self.direct_act_enabled or targets is None or steps != 1 or "proposal_id" in args:
            raise ToolInputError("Direct EEF requires permission and exactly one dual-arm target")
        latest = self._review(self._observe(), args.get("review"))
        if latest.get("done"):
            return self._public(latest)
        if native_targets is not None and latest.get("manual_tcp_targets_enabled"):
            if not latest.get("manual_tcp_targets_enabled"):
                raise ToolInputError("This episode bridge has not enabled direct TCP fallback")
            request = dict(op="act_eef", targets=native_targets,
                           reason=args["reason"], assessment=args["assessment"])
            if "max_native_steps" in args:
                request["max_native_steps"] = args["max_native_steps"]
            result = self.call(request)
        elif self.setting == "simate_vla" and native_targets is not None:
            raise ToolInputError("This episode bridge has not enabled direct TCP fallback")
        elif self.setting == "simate_vla":
            result = self.call(dict(op="vla_review", proposal_id=args["proposal_id"],
                                    decision="accept", targets=None, approved_steps=steps,
                                    reason=args["reason"], assessment=args["assessment"]))
        else:
            result = self.call(dict(op="act", repeat=1, action=dict(
                mode="eef", target=native_targets[0], reason=args["reason"], assessment=args["assessment"])))
        return self._public(result)

    def execute_tool(self, name: str, arguments: dict) -> Any:
        """Supply RoboRSI ToolResult objects to its existing MCP transport."""
        from roborsi.tools import ToolResult

        try:
            value = self.dispatch(name, arguments)
        except ToolInputError as error:
            return ToolResult(name=name, result={"error": str(error)})
        result = ToolResult(name=name, result=value)
        cameras = value.get("observation", {}).get("cameras", {})
        if self.image_root is not None:
            for camera in ("cam_head", "cam_left_wrist", "cam_right_wrist"):
                ref = cameras.get(camera)
                if not isinstance(ref, str) or not ref.startswith("/observations/"):
                    continue
                path = (self.image_root / ref.removeprefix("/observations/")).resolve(strict=True)
                if not path.is_relative_to(self.image_root) or path.suffix.lower() != ".png":
                    raise RuntimeError("Invalid public RGB image path")
                result.content_blocks.extend([
                    {"type": "text", "text": camera + " RGB"},
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/png",
                        "data": base64.b64encode(path.read_bytes()).decode(),
                    }},
                ])
        return result

    def as_local_tools(self) -> tuple:
        """Register in EmbodiedAgent using the repository's LocalToolSpec."""
        from roborsi.embodied_agent import LocalToolSpec

        return tuple(LocalToolSpec(
            name=spec["name"], description=spec["description"],
            input_schema=spec["input_schema"],
            handler=lambda args, name=spec["name"]: self.execute_tool(name, args),
            propagate_errors=True,
        ) for spec in self.get_tools_spec())

    def mcp_server(self) -> Any:
        """Create the existing RoboRSI HTTP MCP transport; caller owns its lifetime."""
        from roborsi.planner.utils.http_mcp_server import HttpMcpServer

        return HttpMcpServer(self)
