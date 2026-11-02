---
name: robo-general
description: Operate RoboDojo with observe, vla_propose and act. Use for VLA-assisted or enabled direct TCP control.
---

# Robot operation

Follow the current episode instruction, permissions and budgets. Use the declared tool parameters; keep reasons and observed-state assessments concise.

1. Call `observe` first. Inspect current head and both wrist RGB views, measured TCP state and remaining budget.
2. When VLA is enabled, give `vla_propose` one short English sentence: an immediate action, the observed object and a destination when needed. Use task/demo wording; omit reasoning, code and multi-step plans. Example: `Move the left gripper above the blue 8.` It predicts without moving and shows every fifth target, steps 5,10,...,50, rounded to three decimal places for display.
3. Inspect the proposal and choose one action:
   - Accept: give `act` the current `proposal_id` and a consecutive `steps` count (5..50, default 5). The harness executes the stored original joint sequence with its native timing; FK is for preview only.
   - Choose your own motion: give `act` full dual-arm TCP `targets`. No proposal_id or reject flag is needed; these targets replace the VLA suggestion. The harness tracks each target using measured-state feedback and IK; do not generate interpolation points yourself.
   - TCP tracking uses a shared `max_native_steps` budget (default 80, maximum 160), with 3 mm / 3 degree pose tolerance. Target count is not native-step count. Check reached/stalled/budget status, residuals and actual executed steps; pose arrival does not prove grasp.
4. Inspect the returned RGB and measured state after execution. Use short spans near contact, placement or uncertainty. After a timeout or uncertain execution, observe before another motion.

Preview samples are for inspection; do not interpolate them into an execution trajectory. All poses/targets use gripping-face-centre TCP XYZ in metres and `pitch_deg`, `roll_deg`, `yaw_deg` relative to the vertical-down reference. Zero angles point down; yaw=30 means rotate that reference 30 degrees about +Z. The fixed-axis order is pitch about +X, roll about +Y, then yaw about +Z. Use TCP targets directly. TCP is not necessarily the held object centre; judge grasp offset and progress from current RGB. A command or closed gripper does not prove possession or completion.

Targets use continuous `gripper_opening` from 0 (fully closed) to 1 (fully open). Intermediate values are allowed. Proposal samples show the stored continuous openings; accepting a proposal preserves them. Measured openings are in `left_ee_joint_state` and `right_ee_joint_state`.

When VLA is disabled and direct control is enabled, use `observe` then `act` with one dual-arm TCP target and no proposal_id. Hold an inactive arm at its measured pose and intended opening.

Check the instruction's object arrangement, support/release and final arm state. If a checkpoint requests evidence, provide the requested fields through `act.review`. Continue meaningful corrections while the task remains active. The harness controls termination and enforced limits; a tool-local success does not establish task completion.
