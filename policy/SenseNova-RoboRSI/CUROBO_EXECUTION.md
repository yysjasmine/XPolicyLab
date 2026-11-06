# Optional bounded cuRobo numerical implementation

Integration note: xpolicy-wsl6 retains the wsl4 DLS routing. The following
describes the optional implementation and its numerical tests; it is not
a claim that this branch has selected cuRobo for the public RGB path.

# Bounded cuRobo execution for manual EEF actions

Fresh episodes use `eef_execution_backend: bounded_curobo` by default. Set
`eef_execution_backend: legacy_dls` in the model/deployment configuration to use
the original DLS implementation. Resume preserves the recorded backend; older
checkpoints without this field retain their original DLS behavior. A resume
cannot silently switch its solver.

Only model-authored EEF actions, including the existing closed-loop TCP tracking
path, enter this backend. The existing TCP-to-link6 conversion and outer
5 cm / 0.35 rad action validation remain in place. For each arm and native step:

The shared tracker sends an open-gripper waypoint more than 4 cm away through
the fast approach path: at most 4.5 cm and 0.3 rad per native step. Closer
waypoints and closed-gripper motion retain 2 cm and 0.1 rad increments. A
model can give the distant pregrasp pose in one `act` call; the tracker still
executes and observes each physical step.

1. Read measured joints and FK, then bound the translation error vector to
   0.045 m and the rotation error vector to 0.3 rad. The shared tracker may
   supply a smaller near-target increment.
2. Use the official environment's `robot_manager.solve_ik` cuRobo solver, with
   verified link frames and joint ordering. A zero-error arm skips the solver.
3. Select the nearest equivalent revolute-joint solution within soft limits.
4. Limit each joint target to measured position ±0.4 rad, apply target EMA with
   alpha 1.0 for open-gripper fast approach or 0.5 otherwise using the actual
   previous control, and apply the same bounds again.
5. Execute exactly one original `_step_joint` action. Gripper openings pass
   through without EMA. The official simulator and interpolation source are
   unchanged.

An IK `Fail` or rejected periodic solution uses the bounded previous command
(or measured hold when there is no previous command); the other arm can still
execute. A fault after physics has advanced is retained and raised, never
resent as an apparently unexecuted action.

## VLA accept is direct joint execution

`act(decision="accept", ...)` still selects the stored VLA joint prefix.
Each selected joint vector goes directly through the original joint execution
path. It is not converted back to EEF, solved with cuRobo, clipped to the manual
TCP joint delta, or EMA-filtered. Existing proposal freshness, order,
finite-value, native joint-range and pose-bound validation remain unchanged.

`bounded_curobo_execution.jsonl` records manual-EEF solver status, raw and
canonical solutions, effective pose target, measured joints, previous command,
bounds, EMA target, executed joints, physics counters and pose residuals. The
native action/transition logs remain the authority for actual execution.

Offline tests cover numerical bounds, periodic equivalents, zero-error holds,
IK fallback, one native action per update, errors after physics, continuous
grippers and exact VLA joint passthrough with the cuRobo executor forbidden.
These tests do not claim a new native GPU task success rate.
