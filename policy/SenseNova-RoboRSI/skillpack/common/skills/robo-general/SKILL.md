---
name: robo-general
description: Operate RoboDojo with observe, vla_propose and act. Use for VLA-assisted or enabled direct TCP control.
---

# Robot operation

Follow the current episode instruction, permissions and budgets. When a case SKILL.md is supplied, read its strategy and acceptance criteria. Use declared tool parameters; keep reasons and observed-state assessments concise.

Unless needed to diagnose a concrete issue, do not read the implementation source of existing skills or scripts; use their SKILL.md instructions and documented interfaces.

Call robot tools directly. Their results include state text and RGB image blocks automatically; no JavaScript forwarding, `text()` or `image()` is needed.

## Observe, propose and execute

1. Call `observe` first. Inspect head and both wrist RGB, measured TCP state and remaining budget. Observe again after a timeout, uncertain execution or session recovery.
2. When the target geometry is not sufficiently known and VLA is enabled, give `vla_propose` one short English sentence: an immediate action, the observed object and a destination when needed. Use task/demo wording; omit reasoning, code and multi-step plans. Example: `Move the left gripper above the blue 8.` It predicts without moving and returns `proposal_id` plus one `preview` at steps 5,10,15,20,25,30,35,40,45,50 only.
3. Read each preview row with current RGB. Both arms show `pos_m` (TCP XYZ metres), `angles_deg` ([pitch,roll,yaw] degrees), `grip` (opening 0..1), `delta_cm` (XYZ from measured start), `path_cm`, maximum consecutive-step changes (`max_step_cm`, `max_rotation_step_deg`, `max_joint_step_rad`), `grip_delta` and `grip_range` (including the measured start). Row `bounds` are cumulative existing warnings through that step. Prefix statistics include every original step, including unsampled excursions; all displayed floats have at most three decimals. If `diagnostics=unavailable`, inspect the poses and RGB without assuming zero changes. No full 50-point file or inspection command is exposed. Choose one action:
   - Accept: copy the current short `proposal_id` (e.g. `p1`) into `act` with a consecutive `steps` count (5..50, default 5). Old IDs expire when replaced or consumed. The harness executes the stored original joint sequence with its native timing; FK is for preview only.
   - Choose your own motion: give `act` full dual-arm TCP `targets`. No proposal_id or reject flag is needed; these targets replace the VLA suggestion. The harness tracks each target using measured-state feedback and IK; compute only meaningful approach waypoints; let the controller generate low-level interpolation.
   - To calculate a target from the latest measured state, call `hold_current({})`, `close_in_place({"arm":"left","opening":0})`, or `move_relative({"arm":"left","delta_m":[0,0,0.03]})`. They preserve both orientations and unmodified arm/gripper fields. Relative offsets use environment XYZ metres. All three are read-only: they return `control_step`, `computed_only=true` and one full dual-arm `targets` list, without motion, VLA inference or proposal replacement. Inspect the result, then pass its `targets` to `act` with `reason` and `assessment.current_subgoal`. Recalculate after any intervening action; a returned target does not execute itself.
   - TCP tracking uses a shared `max_native_steps` budget (default 80, maximum 160), with 3 mm / 3 degree pose tolerance. Target count is not native-step count. Check reached/stalled/budget status, residuals and actual executed steps; pose arrival does not prove grasp.
4. Inspect the returned RGB and measured state after execution. Use short spans near contact, placement or uncertainty. If interrupted, inspect the current result before choosing another action; an interrupted VLA proposal cannot be reused.

Only if observe reports `review_due`, use the legacy checkpoint fields declared by `act.review` before continuing. This is harness bookkeeping, not a native scoring condition.

Use RGB, measured state and numerical evidence to choose the needed review depth:

- Target identity: confirm the instructed object on first selection, after occlusion or when the scene differs from expectation. Change view or crop when useful.
- Arm and subgoal: compare both arms' motion and gripper changes with the intended object and action. Briefly explain consequential changes in the next action reason/current_subgoal.
- Grasp: after closing or a short test lift, before transport, or when slipping/empty fingers are suspected, check whether the intended object is held and moves with the gripper.
- Path: inspect visible clearance along the path and at the endpoint, especially near surfaces, clutter or the other arm. Use a shorter span, re-observe or revise the motion when needed.
- Completion: compare actual arrangement, support, release and arm state with the current case's acceptance criteria.

Keep consequential findings and the next action concise in `act.reason` and `assessment.current_subgoal`.

Preview samples are for inspection; do not interpolate them into an execution trajectory. All poses/targets use gripping-face-centre TCP XYZ in metres and `pitch_deg`, `roll_deg`, `yaw_deg` relative to the vertical-down reference. Zero angles point down; yaw=30 means rotate that reference 30 degrees about +Z. The fixed-axis order is pitch about +X, roll about +Y, then yaw about +Z. Use TCP targets directly. TCP is not necessarily the held object centre; judge grasp offset and progress from current RGB. A command or closed gripper does not prove possession or completion.

Targets use continuous `gripper_opening` from 0 (fully closed) to 1 (fully open). Intermediate values are allowed. Proposal samples show the stored continuous openings; accepting a proposal preserves them. Measured openings are in `left_ee_joint_state` and `right_ee_joint_state`.

When VLA is disabled and direct control is enabled, use `observe` then `act` with one dual-arm TCP target and no proposal_id. Hold an inactive arm at its measured pose and intended opening.

## Direct approach to a known object

If direct control is enabled and a current accepted localization or non-stale `space.json` entry supplies the needed object point, skip `vla_propose` for approach and grasp. Confirm identity, frame and freshness in current RGB. Write a short calculation or a few approach waypoints, choosing the arm, grasp orientation, opening, object-to-TCP offset and clearance from object geometry, obstacles and uncertainty. An object center is not a grasp pose; avoid a universal offset.

Execute one full dual-arm target at a time through `act`, without `proposal_id`; hold the inactive arm at its measured pose and opening. Approach a clear nearby pose, inspect the result, then descend, close and test-lift in short stages. `targets` describes both arms at one pose, not a timed trajectory. The controller handles IK and interpolation; code generation alone executes nothing. Re-observe after uncertainty and refresh stale memory. Localize unknown target geometry first or use the available VLA path.

## Completion

Use the current task instruction and supplied case SKILL.md to assess applicable checks as met / unmet / uncertain, with current RGB, measured state and native-step evidence. Task-specific thresholds and final poses stay in that case's SKILL.md. Check actual position/orientation, coverage, support, release, stability after real native steps and applicable arm state. A command, planned target, demo ending or occluded view does not establish completion; repeated observe alone advances no physics. Continue useful inspection/corrections while requirements remain unmet or uncertain and the task permits it. Native done means termination; success and score come from the native evaluator. The harness owns enforced limits and termination.
