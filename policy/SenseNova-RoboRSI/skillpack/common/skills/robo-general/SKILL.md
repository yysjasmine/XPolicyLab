---
name: robo-general
description: Operate RoboDojo with observe, vla_propose and act. Use for VLA-assisted or enabled direct TCP control.
---

# Robot operation

Follow the current episode instruction, permissions and budgets. Use the declared tool parameters; keep reasons and observed-state assessments concise.

Unless needed to diagnose a concrete issue, do not read the implementation source of existing skills or scripts; use their SKILL.md instructions and documented interfaces.

Call robot tools directly. Their results include state text and RGB image blocks automatically; no JavaScript forwarding, `text()` or `image()` is needed.

1. Call `observe` first. Inspect current head and both wrist RGB views, measured TCP state and remaining budget.
2. When VLA is enabled, give `vla_propose` one short English sentence: an immediate action, the observed object and a destination when needed. Use task/demo wording; omit reasoning, code and multi-step plans. Example: `Move the left gripper above the blue 8.` It predicts without moving and shows every fifth target, steps 5,10,...,50, rounded to three decimal places for display.
3. Inspect the proposal and choose one action:
   - Accept: copy the current short `proposal_id` (e.g. `p1`) into `act` with a consecutive `steps` count (5..50, default 5). Old IDs expire when replaced or consumed. The harness executes the stored original joint sequence with its native timing; FK is for preview only.
   - Choose your own motion: give `act` full dual-arm TCP `targets`. No proposal_id or reject flag is needed; these targets replace the VLA suggestion. The harness tracks each target using measured-state feedback and IK; do not generate interpolation points yourself.
   - TCP tracking uses a shared `max_native_steps` budget (default 80, maximum 160), with 3 mm / 3 degree pose tolerance. Target count is not native-step count. Check reached/stalled/budget status, residuals and actual executed steps; pose arrival does not prove grasp.
4. Inspect the returned RGB and measured state after execution. Use short spans near contact, placement or uncertainty. After a timeout or uncertain execution, observe before another motion.

Preview samples are for inspection; do not interpolate them into an execution trajectory. All poses/targets use gripping-face-centre TCP XYZ in metres and `pitch_deg`, `roll_deg`, `yaw_deg` relative to the vertical-down reference. Zero angles point down; yaw=30 means rotate that reference 30 degrees about +Z. The fixed-axis order is pitch about +X, roll about +Y, then yaw about +Z. Use TCP targets directly. TCP is not necessarily the held object centre; judge grasp offset and progress from current RGB. A command or closed gripper does not prove possession or completion.

Targets use continuous `gripper_opening` from 0 (fully closed) to 1 (fully open). Intermediate values are allowed. Proposal samples show the stored continuous openings; accepting a proposal preserves them. Measured openings are in `left_ee_joint_state` and `right_ee_joint_state`.

Both forms of `act` monitor measured gripper openings after every native step. After an opening/closing command, monitoring arms once three consecutive readings span at most 0.003. With the command unchanged within 0.005, a change of at least 0.02 from the settled opening in either arm immediately cancels the rest of the act and returns `status=gripper_change_reobserve`, `gripper_event`, current RGB and measured state. The reference persists across act calls, so gradual drift is detected too. This signals changed contact, not confirmed object loss. Inspect the returned images before choosing a new action; an interrupted VLA proposal cannot be reused. This monitor is built in; no script invocation or source reading is needed.

When VLA is disabled and direct control is enabled, use `observe` then `act` with one dual-arm TCP target and no proposal_id. Hold an inactive arm at its measured pose and intended opening.

Check the instruction's object arrangement, support/release and final arm state. If a checkpoint requests evidence, provide the requested fields through `act.review`. Continue meaningful corrections while the task remains active. The harness controls termination and enforced limits; a tool-local success does not establish task completion.

## Offline helper usage

Case helpers are calculation utilities, separate from robot motion. Use the case's documented command directly; `--help` resolves syntax questions without loading source. Do not routinely `cat` scripts, import implementation-only helpers, or print calibration matrices. Read a small relevant source excerpt only to diagnose an actual unresolved error. CLI results are compact JSON with numeric values rounded to at most three decimal places; internal calculations and calibration retain full precision. Forward helper stdout rather than the full command metadata envelope. Save larger working data in the episode directory and print only needed fields or paths. After compaction, retain/reuse successful invocations and their input/output contracts; do not reread implementation to recover usage.
