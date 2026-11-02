# SenseNova-RoboRSI

**Contributor:** RoboRSI integration | **Original code:** [RoboRSI](https://github.com/SenseTime-FVG/RoboRSI)

RoboDojo policy with a single execution entry: `Model` → `SkillpackController` →
`SkillpackEmbodiedAgent` → external Codex CLI. Simate beta predicts joint
trajectories; the agent uses three robot tools to inspect and execute them.
XPolicyLab owns the simulator, physical layout, native step feedback and scoring.
Vendored RoboRSI, kinematics and OpenPI wire-codec sources remain pinned.

## Installation

Place `XPolicyLab/` beside the official RoboDojo `env_cfg/` directory. Run from
`policy/SenseNova-RoboRSI/`:

```bash
bash install.sh
```

The installer builds/reuses the pinned policy and tool environments, verifies
Codex CLI 0.160.1 and bubblewrap, and installs the selected VLA. The default
`deploy.yml` selects Simate beta. It does not call a model API. See
[INSTALL.md](INSTALL.md) for dependencies, checkpoint downloads and cache reuse.

Simate weights resolve through XPolicyLab's checkpoint resolver. The default is
`checkpoints/simate-beta-robodojo-eval/`; an explicit `checkpoint_path` or a
checkpoint run directory uses the shared resolver's precedence. Weights and
installed environments are not part of this source checkout.

## Execution materials

`skillpack/common/` contains the frozen short core instructions, `robo-general`
and tool declarations. `skillpack/frozen_common.json` verifies their bytes.
`skillpack/cases/catalog.json` maps 54 public task names to 26 case definitions,
12 explicit generalization aliases and 16 open tasks. Only the current task's
case is staged. Open tasks receive `robo-general` alone.

Every skill has one `SKILL.md`, below 8192 UTF-8 bytes (a conservative bound on
8192 tokens). Case packages contain concise demonstration references, at most
three selected reference images and offline numerical helpers. They contain no
raw ICL corpus or nested Markdown. Source hashes and authoring receipts stay
outside the model's workspace under `skillpack/provenance/`. Runtime subagents
remain disabled. Supplied reference images are ordinary RGB JPEGs; they are not
serialized XPolicyLab trajectory buffers.

## Robot tools

The external CLI exposes `functions.exec` / `functions.wait` with the registered
MCP robot tools `observe`, `vla_propose` and `act`:

- `observe` returns current RGB and measured state, with displayed floats rounded
  to three decimals. Native measurements retain their precision.
- `vla_propose` takes a short immediate-action instruction. Its 50-step joint
  trajectory remains private; TCP previews show steps 5,10,…,50.
- `act` accepts an unchanged consecutive VLA joint prefix (5–50 steps, no IK),
  or tracks supplied dual-arm TCP targets using measured feedback and IK.
  Targets use metres, pitch/roll/yaw degrees relative to vertical down, and
  continuous `gripper_opening` in [0,1]. Tracking completes a waypoint before
  advancing, and reports stalls, residuals and consumed native steps.

Each physical joint step is returned by `Model.get_action()` and acknowledged
with the actual executed observation. FK preview, stored joints and manual IK
remain separate execution paths. The model has no stop tool.

## Evaluation

```bash
export ROBORSI_CODEX_BASE_URL='https://your-service.example/v1'
export ROBORSI_CODEX_MODEL='your-model-route'
read -rsp 'CODEX_API_KEY: ' CODEX_API_KEY; export CODEX_API_KEY
EVAL_ENV_TYPE=sim EVAL_NUM=1 bash eval.sh \
  RoboDojo arrange_largest_number roborsi-codex-v1 arx_x5 joint 0 0 0 uv RoboDojo
```

The ten arguments follow the shared XPolicyLab convention; `uv` selects the
policy environment and `RoboDojo` the simulator Conda environment. Supported:
`RoboDojo`, `arx_x5`, `joint`, one environment. RGB arrives decoded at the Model
boundary; action dimensions come from the shared robot metadata helper.

The agent uses xhigh, a 200K input context, all images within each append-only
window, and default same-model text compaction before the next request exceeds
50 images or after 120 completed model turns. There is no additional output-token
cap, cumulative ledger or injected current-state summary. Provider attempt and
completion budgets are 330 and 300; the episode wall budget is four hours.
Authentication, policy and quota failures retain evidence and stop dispatch.
The harness never changes a key or model route to bypass such failures.

For an existing unauthenticated self-hosted CCI service, set `provider_kind: cci`
and select its exact model/base URL. The same entry uses the CCI v4 tool transport,
temperature zero and a compact model catalog containing no extra instructions.
It retains upstream JSON and translated CLI JSON separately. Cache usage is
measured; the paid-provider cache fuse is not applied to self-hosted CCI.
A single output-limit response may retry the same request once, before any tool
result is delivered. Retries consume the original call and wall budgets.

For a node-local persistent Simate service, set `ROBORSI_SHARED_VLA_PORT`.
Episodes have separate session UUIDs; closing an episode releases its history
without stopping the shared service. `smoke_act_limit` is an optional operator
configuration used to stop after a small number of motion-producing act calls;
it is not included in the task prompt or tool schema. Smoke runs are not scored
as complete task evaluations.

## Trajectories and validation

`trajectory_dir` selects persistent episode storage (default
`outputs/trajectories/`). It retains native RGB, joint requests and ACKs,
controller messages, tool calls, actual wire requests, raw responses and opaque
`encrypted_content`. Credentials are not written to these records. `close()`
preserves the source directory and writes `archive.pending.json`; large copying
and publication run after the native terminal ACK.

Offline checks cover skill visibility, scoped MCP dispatch, prompt selection,
tracking and joint-prefix execution. Synthetic checks do not establish native
physics success. Actual native smoke results and full-evaluation scores must be
reported separately. This is an eval-only adapter; training/data conversion is
not provided here. Training release timing is not specified.
