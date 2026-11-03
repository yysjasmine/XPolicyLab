"""Run one configured native episode with lossless RGB IPC and JPEG-only archives.

This launcher creates no PNG observation files. Existing frozen experiments
continue to use their original launchers and capture identities.
"""
from pathlib import Path
import argparse
import importlib
import json
import os
import sys
import time
import traceback
import uuid


ROOT = WORKSPACE = POLICY = None


def configure(argv=None):
    global ROOT, WORKSPACE, POLICY
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="Owned batch directory containing cases.json")
    parser.add_argument("--workspace", type=Path, required=True, help="Parent directory containing the frozen XPolicyLab checkout")
    parser.add_argument("--native-python", type=Path, required=True, help="Native simulator Python or its existing wrapper")
    parser.add_argument("--native-root", type=Path, required=True, help="Existing native RoboDojo installation")
    parser.add_argument("--label", required=True)
    args = parser.parse_args(argv)
    ROOT, WORKSPACE = args.root.resolve(strict=True), args.workspace.resolve(strict=True)
    POLICY = WORKSPACE/"XPolicyLab/policy/SenseNova-RoboRSI"
    if not (ROOT/"cases.json").is_file() or not (POLICY/"native_rgb_adapter.py").is_file():
        parser.error("Missing cases.json or the frozen raw-RGB policy source")
    if not args.native_python.is_file() or not args.native_root.is_dir():
        parser.error("Native Python/root must already exist")
    sys.path[:0] = [str(WORKSPACE), str(POLICY/"runtime/RoboRSI")]
    os.environ["ROBORSI_ROBODOJO_NATIVE_ROOT"] = str(args.native_root.resolve())
    return args


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".new")
    temp.write_text(json.dumps(data, indent=2)+"\n")
    temp.replace(path)


def main():
    args = configure()
    label = args.label
    spec = json.loads((ROOT/"cases.json").read_text())[label]
    route = spec["model"]
    task = spec["task"]
    step_limit = spec["native_step_limit"]
    # Only a single JSON stdin line carries the credential; never persist it.
    credential = json.loads(sys.stdin.readline())["key"] if spec["provider_kind"] == "responses" else "local-cci-noauth"
    os.environ.update(CODEX_API_KEY=credential, OPENAI_API_KEY=credential,
                      ROBORSI_CODEX_MODEL=route, ROBORSI_CODEX_BASE_URL=spec["endpoint"],
                      ROBORSI_SHARED_VLA_PORT="19806", ROBORSI_NATIVE_LAYOUT_ID="0")
    del credential
    case = ROOT/"cases"/label
    case.mkdir(parents=True, exist_ok=False)
    run_id = f"xpolicy-{label}-{task}-s0-layout0-"+uuid.uuid4().hex[:12]
    os.environ["ROBODOJO_RUN_ID"] = run_id
    os.environ["ROBORSI_RUNTIME_STATE_DIR"] = str(case/"state")
    model_module = importlib.import_module("XPolicyLab.policy.SenseNova-RoboRSI.model")
    rgb_module = importlib.import_module("XPolicyLab.policy.SenseNova-RoboRSI.native_rgb")
    cfg = json.loads((POLICY/"vendor/robodojo_adaptive/resources/fresh-configs"/(task+"-s0.json")).read_text())
    source = POLICY/"vendor/robodojo_adaptive/source"
    adapter = source/"code/configurations/robodojo/20261005_standard_1x_10x/adapters/adapter.py"
    cfg.update(adapter=str(adapter)+":Adapter", benchmark_python=str(args.native_python.resolve()),
               environment_dir=os.environ["ROBORSI_ROBODOJO_NATIVE_ROOT"], request_seconds=180,
               episode_seconds=None, explore_seconds=0, training_set=str(case/"empty-training"))
    cfg["options"].update(action_mode="joint", review_window_steps=step_limit, review_max_windows=1,
                           stop_min_steps=step_limit, max_repeat=1, gpu=0, physical_gpu=0, reset_request_seconds=180)
    native = case/"native"
    native.mkdir()
    (case/"empty-training").mkdir()
    observations = native/"observations"
    observations.mkdir()
    records = native/"episode"
    records.mkdir()
    model_cfg = dict(bench_name="RoboDojo", task_name=task, env_cfg_type="arx_x5",
                     action_type="joint", eval_batch=False, seed=0, vla_backend="simate_beta",
                     ckpt_name="roborsi-codex-v1", trajectory_dir=str(case/"trajectories"),
                     provider_kind=spec["provider_kind"], reasoning_effort=spec["reasoning_effort"], episode_seconds=None)
    if "sampling" in spec:model_cfg["sampling"] = spec["sampling"]
    if "enable_thinking" in spec:model_cfg["enable_thinking"] = spec["enable_thinking"]
    prior=ROOT/"prior_usage_files.json"
    if prior.exists():model_cfg["prior_usage_files"]=json.loads(prior.read_text())
    model_cfg["episode_seconds"] = cfg["episode_seconds"] = None
    model_cfg["native_step_limit"] = step_limit
    write(case/"status.json", {"phase": "preflight", "at": time.time(), "model": route,
          "policy_source": str(POLICY), "native_step_limit": step_limit, "score": None,
          "native_rgb_transport": rgb_module.TRANSPORT, "archive_image_format": "JPEG95_444"})
    backend = model = None
    started = time.time()
    control_steps = 0
    try:
        model = model_module.Model(model_cfg)
        model.reset()
        backend = rgb_module.create_backend(cfg, native)
        backend.request(op="initialize")
        episode = cfg["test_episodes"][0]
        reply = backend.request(op="reset", episode=episode, record_dir=str(records), observation_dir=str(observations))
        native_identity = json.loads((records/"native_episode.json").read_text())
        scene_sha = native_identity["layout_sha256"]
        expected_sha = "8d45b23010c950c35d5618c8701c6b8203036abaaf20a228db067ec3b1e30617"
        if task == "arrange_largest_number" and scene_sha != expected_sha:
            raise ValueError("Native layout differs from the authorized seed0/layout0")
        write(case/"native-identity.json", native_identity)

        def current_observation(result):
            obs = result["observation"]
            value = backend.model_observation(result)
            value["roborsi_native_identity"] = {"schema": 1, "task": task,
                "eval_seed": 0, "layout": 0, "run_id": run_id, "scene_sha256": scene_sha,
                "control_step": obs["control_step"], "step_limit": step_limit}
            return value

        model.update_obs(current_observation(reply))
        write(case/"status.json", {"phase": "agent_running", "model": route, "native_steps": 0, "at": time.time(), "score": None})
        while not reply["done"]:
            actions = model.get_action()
            if actions == []:
                continue
            if len(actions) != 1:
                raise ValueError("Expected exactly one native action per Model chunk")
            action = actions[0]
            if "_roborsi_control" in action:
                if action != {"_roborsi_control": "finish"}:
                    raise ValueError("Unknown native finish envelope")
                reply = backend.request(op="finish")
                break
            command = {key: value.tolist() if hasattr(value, "tolist") else value for key, value in action.items()}
            reply = backend.request(op="step", action=command)
            control_steps += 1
            if reply["observation"]["control_step"] != control_steps or control_steps > step_limit:
                raise ValueError("Native step ACK/budget mismatch")
            with (case/"joint_actions.jsonl").open("a") as stream:
                stream.write(json.dumps({"step": control_steps, "action": command, "done": reply["done"]})+"\n")
            write(case/"status.json", {"phase": "running", "model": route, "native_steps": control_steps,
                  "at": time.time(), "policy_source": str(POLICY), "score": None})
            if not reply["done"]:
                model.update_obs(current_observation(reply))
        # Persist the native terminal reply before controller/capture cleanup.
        write(case/"native-terminal.json", reply)
        receipt = model.end_episode(current_observation(reply))
        write(case/"status.json", {"phase": "completed", "model": route, "native_steps": control_steps,
              "at": time.time(), "wall_seconds": time.time()-started, "cleanup_receipt": receipt,
              "policy_source": str(POLICY), "score": reply.get("metrics", {}).get("score"), "native_score_at_stop": reply.get("metrics", {}).get("score"), "full_episode_evaluation": True, "episode_seconds": None, "smoke_act_limit": None,
              "native_metrics": reply.get("metrics"), "native_step_limit": step_limit})
    except BaseException as error:
        write(case/"status.json", {"phase": "technical_error", "model": route, "native_steps": control_steps,
              "at": time.time(), "wall_seconds": time.time()-started, "error_type": type(error).__name__,
              "error": str(error), "traceback": traceback.format_exc(), "score": None})
        raise
    finally:
        if model is not None:
            model.shutdown()
        if backend is not None:
            backend.close()


if __name__ == "__main__":
    main()
