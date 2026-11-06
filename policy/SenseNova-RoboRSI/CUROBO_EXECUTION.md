# Bounded cuRobo execution for manual EEF actions

## Native execution bridge on xpolicy-wsl9

This branch is based on repaired `xpolicy-wsl4` (`bf8c39e9`). It selects the
conservative cuRobo bounds described below and wires manual TCP actions through
`Model`, the public queue and the native RGB or standard deployment owner. The
native owner runs the official solver and original joint executor. A matching
request ID, target digest, native-step increment, actual joint command and
positive physics-step receipt must return before the pending action continues.

The prior optional modules' larger bounds (4.5 cm / 0.3 rad / 0.4 rad and fast
approach EMA bypass) do not apply to this cuRobo backend. The existing outer TCP
tracker may choose approach waypoints, but each cuRobo solver step is bounded to
2 cm / 0.1 rad / 0.05 rad with EMA 0.5. Explicit `legacy_dls` selection preserves
the prior DLS path for comparisons. Existing storage, spatial calibration and
memory, case guidance, three robot tools and ten preview samples remain intact.

Validation for this port is offline and uses synthetic robot geometry and solver
responses. It does not claim a new native GPU smoke or model-task score.

Fresh episodes use `eef_execution_backend: bounded_curobo` by default. Set
`eef_execution_backend: legacy_dls` in the model/deployment configuration to use
the original DLS implementation. Resume preserves the recorded backend; older
checkpoints without this field retain their original DLS behavior. A resume
cannot silently switch its solver.

Only model-authored EEF actions, including the existing closed-loop TCP tracking
path, enter this backend. The existing TCP-to-link6 conversion and outer
5 cm / 0.35 rad action validation remain in place. For each arm and native step:

1. Read measured joints and FK, then bound the translation error vector to
   0.02 m and the rotation error vector to 0.1 rad.
2. Use the official environment's `robot_manager.solve_ik` cuRobo solver, with
   verified link frames and joint ordering. A zero-error arm skips the solver.
3. Select the nearest equivalent revolute-joint solution within soft limits.
4. Limit each joint target to measured position ±0.05 rad, apply target EMA with
   alpha 0.5 using the actual previous control, and apply the same bounds again.
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
path. It is not converted back to EEF, solved with cuRobo, clipped to the new
0.05-rad target delta, or EMA-filtered. Existing proposal freshness, order,
finite-value, native joint-range and pose-bound validation remain unchanged.

`bounded_curobo_execution.jsonl` records manual-EEF solver status, raw and
canonical solutions, effective pose target, measured joints, previous command,
bounds, EMA target, executed joints, physics counters and pose residuals. The
native action/transition logs remain the authority for actual execution.

Offline tests cover numerical bounds, periodic equivalents, zero-error holds,
IK fallback, one native action per update, errors after physics, continuous
grippers and exact VLA joint passthrough with the cuRobo executor forbidden.
These tests do not claim a new native GPU task success rate.
