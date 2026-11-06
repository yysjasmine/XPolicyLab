# SenseNova-RoboRSI

**Contributor:** RoboRSI integration | **Original code:** [RoboRSI](https://github.com/SenseTime-FVG/RoboRSI)

RoboDojo policy with a single execution entry: `Model` → `SkillpackController` →
`SkillpackEmbodiedAgent` → external Codex CLI. Simate beta predicts joint
trajectories; the agent uses three robot tools to inspect and execute them.
XPolicyLab owns the simulator, physical layout, native step feedback and scoring.
Vendored RoboRSI, kinematics and OpenPI wire-codec sources remain pinned.

See [CHANGELOG.md](CHANGELOG.md) for iteration history, deployment scope and evidence.

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

For an operator-owned CLI build, set `ROBORSI_CUSTOM_CODEX_MANIFEST` to an
absolute JSON identity manifest before installation and evaluation. It must
declare `schema_version: 1`, `selection: operator_custom_build`, the native ELF
`executable`, exact `version`, `expected_version`, `target`, executable `sha256`,
and `companion_sha256` for `bin/codex-code-mode-host` and
`codex-resources/bwrap`. Select the wrapper's actual native payload. Installation
verifies the selected files, links `runtime/codex` to their package, and records
the selected identity. The default release pins remain unchanged. A custom build
requires a separate real CLI/provider and native-tool smoke before evaluation.

`ROBORSI_DEPLOY_CONFIG` selects an explicit per-experiment deployment YAML for
both policy startup/preflight and the client. `ROBORSI_SIM_PYTHON` can select an
absolute existing simulator virtualenv interpreter in place of Conda activation;
its environment must already provide the official simulator and dependencies.
This path still invokes the official evaluation script and native identity/layout
wrapper. The namespace launcher accepts a checkout with an `XPolicyLab.py` package
entry even when its physical directory has a different name.

## Spatial localization and task memory

Every task workspace includes `robodojo-spatial-localize` alongside `robo-general`
and the current case skill. Its bundled RGB helpers use matching camera
calibration and declared support planes or multi-view correspondences. The system
prompt explains both the skill and the writable `/workspace/space.json` memory.
The memory records current named object points in environment-origin metres,
with control-step and evidence references. Initialize empty per task runtime;
preserve on same-episode recovery; invalidate affected points after manipulation
and re-localize before reuse. The skill's `space_memory.py` CLI can import accepted
`points3d.json` results or mark an object's points stale; direct JSON edits are
also allowed. Images and detailed geometry reports remain separate.

The model-tool environment adds pinned `opencv-python-headless==4.10.0.84` to its
existing NumPy/SciPy dependencies. No simulator object-state or depth access is
added. The native observation wrapper now captures paired RGB and optical calibration
through existing Isaac camera APIs, without depth or changes to RoboDojo source.
The observation transport preserves this metadata, and the policy bridge writes
an immutable `*_spatial_case.json` plus image hashes beside the current images.
`observe` exposes `spatial_localization.case_path`; the model only annotates
pixels and invokes the bundled `run_localize.py`, which runs geometry and merges
accepted estimates into `space.json`. Calibration capture renders without
advancing physics. `ROBORSI_SPATIAL_CALIBRATION=0` retains the legacy uncalibrated
capture path for host diagnostics; normal spatial deployments leave it enabled.
Already-running frozen experiments require a new deployment to use this code.

## Execution materials

`skillpack/common/` contains the frozen short core instructions, `robo-general`
and tool declarations. `skillpack/frozen_common.json` verifies their bytes.
`skillpack/cases/catalog.json` maps 54 public task names to 26 case definitions,
12 explicit generalization aliases and 16 open tasks. Only the current task's
case is staged. Open tasks receive `robo-general` alone.

Every skill has one `SKILL.md`. Main documents are bounded at 16,384 UTF-8 bytes
to retain complete public completion criteria and current-evidence checks in the
same file. Case packages contain concise demonstration references, at most
three selected reference images and offline numerical helpers. They contain no
raw ICL corpus or nested Markdown. Source hashes and authoring receipts stay
outside the model's workspace under `skillpack/provenance/`. Runtime subagents
remain disabled. Supplied reference images are ordinary RGB JPEGs; they are not
serialized XPolicyLab trajectory buffers.

The 26 case main documents retain the original public completion goal/evidence
checks and task-specific uncertainty cautions. Assess each applicable item as
met, unmet or uncertain from current RGB/native-step and measured-state evidence;
a request or demonstration ending alone does not establish completion. Historical
review/stop-window mechanics are replaced by the current harness protocol.
Generalization aliases inherit the base case; open tasks use their current
instruction with the same general evidence discipline. Original ICL archives and
additional checklist Markdown are not installed in the agent workspace.
Task-specific acceptance thresholds and final poses belong in the corresponding
case's completion checklist. `robo-general` provides shared checking principles;
one case's verified numerical contract is not inherited by other tasks.

Helpers are used through their documented CLI or explicitly documented Python
API. Normal use needs no source inspection: consult the case skill and `--help`
for syntax, and inspect only a small relevant excerpt for an actual unresolved
error. All 26 helper CLIs emit one compact JSON object with numeric outputs
rounded to at most three decimal places. The documented batch projection API
uses the same output precision; calibration and internal calculations retain
their original precision. Continuation notes retain invocations and input/output
contracts rather than implementation source.

## Robot tools

The external CLI exposes `functions.exec` / `functions.wait` with the registered
MCP robot tools `observe`, `vla_propose` and `act`:

- `observe` returns current RGB and measured state, with displayed floats rounded
  to three decimals. Native measurements retain their precision.
- `vla_propose` takes a short immediate-action instruction. Its 50-step joint
  trajectory remains private; TCP previews show steps 5,10,…,50. Proposals use
  short IDs such as `p1`, `p2`, …; copy the current ID into `act.proposal_id`.
  IDs continue across same-episode recovery, and superseded or consumed IDs
  cannot execute a different proposal. Native UUIDs remain in private records.
- `act` accepts an unchanged consecutive VLA joint prefix (5–50 steps, no IK),
  or tracks supplied dual-arm TCP targets using measured feedback and IK.
  Targets use metres, pitch/roll/yaw degrees relative to vertical down, and
  continuous `gripper_opening` in [0,1]. Tracking completes a waypoint before
  advancing, and reports stalls, residuals and consumed native steps.

Each physical joint step is returned by `Model.get_action()` and acknowledged
with the actual executed observation. FK preview, stored joints and manual IK
remain separate execution paths. The model has no stop tool.

Both forms of `act` check measured gripper openings after each acknowledged
native control step. An intentional command change greater than 0.005 starts a
settling phase: three readings must span at most 0.003 before monitoring arms.
A subsequent opening change of at least 0.02 from that fixed reference in
either arm cancels the remaining motion and returns
`status=gripper_change_reobserve`, a `gripper_event` with the arm, command,
reference, measurement and delta, plus current RGB and state. References persist
across act calls; the accepted VLA tail is discarded on interruption. This is
an indication to inspect contact and possession, not proof of a dropped object.
Thresholds use full-precision normalized feedback internally. The initial and
command-change settling phases do not classify intentional opening/closing as
slippage. These defaults require native-physics validation.

## Evaluation

Model-call totals are uncapped. `episode_seconds: null` (the default) disables
the harness episode wall-time limit; a positive integer opts into a limit.
Native task step limits remain authoritative. Individual CCI requests still
time out after 300 seconds and use bounded retries with the same request,
model, credentials and route. API failures retain their raw evidence and do
not become native zero scores. Recovery can explicitly remove a previous wall
limit with `hybrid_resume --unlimited-wall-time` without renewing native steps.

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

The agent defaults to xhigh; `model_cfg.reasoning_effort` also accepts `medium`.
It uses a 200K input context, all images within each append-only
window, and default same-model text compaction before the next request exceeds
50 images or after 120 completed model turns. General Responses calls have no
additional output-token cap. There is no injected current-state summary or
additional cumulative model-call cap. Episode wall time is unlimited by default;
an evaluation wrapper must not silently add a smoke act limit or wall-time cap.
Authentication, policy and quota failures retain evidence and stop dispatch.
The harness never changes a key or model route to bypass such failures.

Fresh manual TCP actions use `bounded_curobo`: each solver step bounds the
translation vector to 2 cm, rotation to 0.1 rad and joint target to measured
position ±0.05 rad, with joint-target EMA alpha 0.5. The real native owner runs
the official solver and joint executor, and returns a verified one-step ACK.
Stored VLA joint prefixes retain their original execution path. Set
`eef_execution_backend: legacy_dls` for the existing DLS comparison, including
its open-gripper fast approach behavior. Resume keeps the recorded backend;
older checkpoints without a backend field retain DLS. See
[cuRobo execution](CUROBO_EXECUTION.md) for bounds, fallback and verification.

For an existing unauthenticated self-hosted CCI service, set `provider_kind: cci`
and select its exact model/base URL. The same entry uses the CCI v4 tool transport,
temperature zero by default and a compact model catalog containing no extra instructions.
`model_cfg.sampling` can explicitly set `temperature`, `top_k` and `top_p`;
the exact upstream request records their effective values. CCI requests wait at
most 300 seconds, with up to three attempts for transient failures, and cap each
output at 65,536 tokens (or retain a smaller caller-supplied cap).
It retains upstream JSON and translated CLI JSON separately. Cache usage is
measured. After cache warmup, a three-request weighted hit rate below 50%
produces an alert and continues dispatch; cache hit rate never stops a run.
Paid-provider cache alerts are not applied to self-hosted CCI.
A single output-limit response may retry the same request once, before any tool
result is delivered. Every attempt and retry retains usage and error evidence.

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

Model-facing RGB observations are JPEG files (quality 95, 4:4:4, no resizing)
with `image/jpeg` tool content. Replay reuses the existing head-camera JPEG;
this controller creates no extra PNG checkpoints. Old sealed PNG checkpoints
remain supported. The pose and decoded RGB error thresholds are unchanged;
validation receipts record the actual source and replay image paths.

The standalone native evaluation entry is `native_rgb_episode.py`. It uses
`native_rgb_adapter.py` for the original simulator owner and `native_rgb.py`
for private lossless RGB IPC. Live uint8 camera bytes go directly to Model/VLA;
JPEG is never decoded to construct live input. Only JPEG95/4:4:4 files are
written to a temporary native replay archive. Physics, native score calculation
and joint stepping retain the pinned original behavior.
Video publication is opt-in with `save_video: true` in the batch's case spec;
the default is `false`.

```bash
python policy/SenseNova-RoboRSI/native_rgb_episode.py \
  --root /your/owned/batch \
  --workspace /your/owned/workspace \
  --native-python /your/native_python.sh \
  --native-root /your/RoboDojo --label 819
```

The workspace contains the frozen `XPolicyLab` checkout. The batch contains
`cases.json` with the existing model/endpoint/task/native-step-limit settings;
self-hosted CCI needs no credential input. Paid-provider credentials retain the
existing one-line stdin JSON contract and are never saved in captures. The
launcher keeps seed0/layout0, native per-step ACKs, source identity and the
unlimited default wall budget. Existing frozen runs retain their old launchers.
The ordinary XPolicyLab websocket evaluation path already transports raw arrays.

Native `rgb_frames.jsonl` records each camera's live `rgb_sha256` separately
from its archival JPEG `archive.sha256`. Cloud-to-M migration copies the
existing JPEG bytes and verifies **archive** hashes; it must not decode,
resize, re-encode or change colour channels. JPEG encoding introduces one
lossy archival step; IPC and subsequent byte-preserving copies are lossless.

In `storage_mode: raw`, the native replay archive is retained. After copying it
to M, verify the original manifest against copied files:

```bash
python policy/SenseNova-RoboRSI/native_rgb.py \
  --records /your/copied/native/episode/rgb_frames.jsonl \
  --images /your/copied/native/observations
```

## Compact training-source storage (default)

`storage_mode: training` is the default deployment and standalone case setting.
It saves pre-decode source data, not a rendered Swift dataset. It does not decode
reasoning, run semantic quality checks, create teacher answers or call a model.
Use `storage_mode: raw` explicitly for full diagnostic captures.

Each runtime publishes `agent/training/`:

- `actor.jsonl`: one row per continuous model context. Real invocation, system,
  tool-schema or history changes start another row; there is no fixed-turn split.
- `compact.jsonl`: one row per actual completed compactor response, including
  its full generation input and complete output. A compact produces the three
  distinct samples: preceding actor, compact generation, following actor.
- `images/*.jpg`: content-addressed model-visible pictures, including the final
  native tool-result tail. Existing JPEGs are marked without recompression;
  PNG and other conforming files are converted once at quality 95, 4:4:4.
- `manifest.json`, `native-evidence.json`, `cipher_variants.jsonl`: identity,
  image hashes, target/consumption evidence, and alternate ciphertext versions.

Assistant `reasoning_content` is empty. Original reasoning IDs and complete
`encrypted_content` remain available. Messages retain tool names, arguments,
call IDs, multi-item response order and real tool replies. The original tool
schema is retained alongside the nested function projection. Native session
records supply the final tool reply when no following HTTP request exists.

Rows carry `score: null` until an external, completed native result is attached.
The standalone launcher attaches the native 0–1 score to every actor/compact row
after the controller closes; outcome is never injected into the model prompt.
Other supervisors can run the same offline operation:

```bash
python -m XPolicyLab.policy.SenseNova-RoboRSI.training_store \
  --case /your/owned/case --native-result /your/owned/case/native-terminal.json
```

Completed HTTP attempts are reduced to compact source rows plus small usage,
retry/error and hash receipts. Full API bodies are temporary, not an accumulating
request/response archive. Live workspaces, native sessions and ACK/action journals
remain available during interrupted runs. Following an accepted native terminal,
successful score attachment and source export, duplicate workspace/checkpoint,
CLI-session and per-native-step replay pictures are removed with a storage
receipt. Export failures retain their source evidence for repair. Temporary
native video streams are removed after their writer exits when `save_video`
is false; explicit `save_video: true` retains the native video. Existing frozen
evaluations are unchanged.

Capture shutdown retains ownership after a timeout so controller cleanup can
retry the same writer. Training rows close only after that writer has stopped;
persistence errors remain explicit evidence gaps. Before removing duplicate
controller pictures from either the runtime or its shared trajectory directory,
cleanup verifies each closed continuation's training rows, image references and
image hashes. An incomplete continuation keeps the recovery material available.
Failed manifest/evidence publication leaves segments available for retry;
cleanup also requires the native-evidence receipt to exist without gaps.
