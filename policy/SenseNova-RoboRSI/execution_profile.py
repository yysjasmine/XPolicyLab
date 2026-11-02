"""Stage the frozen three-tool interface and exactly one current case skill."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sys
import threading
import time
from types import SimpleNamespace


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runtime_modules(policy_dir):
    root = Path(policy_dir).resolve()/"skillpack"
    frozen = json.loads((root/"frozen_common.json").read_text())
    for relative, digest in frozen.items():
        path = (root/relative).resolve(strict=True)
        if not path.is_relative_to(root) or _hash(path) != digest:
            raise ValueError("Frozen shared interface differs: "+relative)
    runtime = root/"robot_runtime"
    for name in ("robot_tools", "tcp_pose", "execution", "native_request", "prompt_profile"):
        loaded = sys.modules.get(name)
        if loaded is not None and Path(loaded.__file__).resolve() != runtime/(name+".py"):
            raise ValueError("Robot runtime already imported from another source: "+name)
    if str(runtime) not in sys.path:
        sys.path.insert(0, str(runtime))
    return SimpleNamespace(**{name: importlib.import_module(name) for name in (
        "robot_tools", "execution", "native_request", "prompt_profile")})


def load_skillpack_profile(policy_dir, task, owner):
    """Derive case registration only; core, general and tool bytes stay pinned."""
    policy_dir, owner = Path(policy_dir).resolve(), Path(owner).resolve(strict=True)
    runtime = runtime_modules(policy_dir)
    library_root = policy_dir/"skillpack/cases"
    index = json.loads((library_root/"catalog.json").read_text())
    if task not in index["tasks"]:
        raise ValueError("Unknown public RoboDojo task: "+str(task))
    selected = index["tasks"][task]
    source = policy_dir/"skillpack/common"
    destination = owner/"execution-profile"
    if destination.exists():
        profile = runtime.prompt_profile.RobotPromptProfile(destination)
        if profile.task != task:
            raise ValueError("Owned profile belongs to another task")
        return profile
    destination.mkdir()
    for path in source.rglob("*"):
        if not path.is_file() or path.name == "profile.json":
            continue
        target = destination/path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    case_id = selected["case_skill"]
    manifest = json.loads((source/"profile.json").read_text())
    names = ["robo-general"]
    if case_id is not None:
        package = index["packs"][case_id]
        base = (library_root/package["directory"]).resolve(strict=True)
        for relative, digest in package["sha256"].items():
            if _hash(base/relative) != digest:
                raise ValueError("Case resource differs: "+relative)
        target = destination/"skills"/task
        shutil.copytree(base, target)
        if task != case_id:
            main = target/"SKILL.md"
            main.write_text(main.read_text().replace("name: "+case_id+"\n", "name: "+task+"\n", 1))
        names.append(task)
    instructions = (source/"skills_instruction.md").read_text()
    if task != "arrange_largest_number":
        lines = instructions.splitlines()
        lines = [line for line in lines if not line.startswith("- arrange_largest_number:")]
        if case_id is not None:
            lines.append(f"- {task}: Case strategy and public demonstration evidence. (file: /workspace/.agents/skills/{task}/SKILL.md)")
        instructions = "\n".join(lines)+"\n"
    (destination/"skills_instruction.md").write_text(instructions)
    manifest.update(task=task, skills=names, single_markdown_skills=names,
                    profile_id="robodojo_execution_skillpack_v1:"+task)
    manifest["sha256"] = {
        path.relative_to(destination).as_posix(): _hash(path)
        for path in destination.rglob("*") if path.is_file()
    }
    (destination/"profile.json").write_text(json.dumps(manifest, indent=2)+"\n")
    profile = runtime.prompt_profile.RobotPromptProfile(destination)
    profile.case_selection = deepcopy(selected)
    return profile


def prepare_skillpack_context(policy_dir, task, episode_dir):
    """Prepare a lean, immutable source workspace; raw ICL is never installed."""
    root = Path(episode_dir).resolve(strict=True)
    profile = load_skillpack_profile(policy_dir, task, root)
    workspace = root/"prepared-skills"
    workspace.mkdir(exist_ok=True)
    receipt = profile.stage_live_workspace(workspace, owner=root)
    index = json.loads((Path(policy_dir)/"skillpack/cases/catalog.json").read_text())
    selected = index["tasks"][task]
    return SimpleNamespace(workspace=workspace, source_identity={
        "format": "roborsi_current_case_skill_v1", "mapping": selected,
        "raw_icl_materialized": False, "runtime_subagents_enabled": False,
        "profile_id": profile.profile_id, "assets": receipt["assets"],
        "task": task,
    })


def extend_reference(reference):
    """Keep original metadata and ACK protocol, extending only this adapter class."""
    runtime = runtime_modules(Path(__file__).parent)
    return replace(reference, HybridAdapter=runtime.execution.adapter_class(reference.HybridAdapter))


def start_toolkit(experiment, run, owner, model_cfg):
    """Keep recording and optional operator stop outside the model tool schema."""
    runtime = runtime_modules(Path(__file__).parent)
    RobotTools, ToolInputError = runtime.robot_tools.RobotTools, runtime.robot_tools.ToolInputError
    run, owner = Path(run), Path(owner)
    limit = model_cfg.get("smoke_act_limit")
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("smoke_act_limit must be a positive operator-only act count")

    class RecordedTools(RobotTools):
        def __init__(self):
            self.operation_thread = None
            self.serial = threading.RLock()
            self.executed_act_calls = self.sequence = 0
            self.closed = False
            super().__init__(experiment.request, setting="simate_vla", direct_act_enabled=True,
                             image_root=run/"observations")

        def dispatch(self, name, arguments):
            if self.closed:
                raise ToolInputError("Operator-authorized execution has ended")
            return super().dispatch(name, arguments)

        def execute_tool(self, name, arguments):
            with self.serial:
                self.operation_thread = threading.get_ident()
                before = experiment.latest["observation"]["control_step"]
                self.sequence += 1
                record = {"sequence": self.sequence, "tool": name, "arguments": deepcopy(arguments),
                          "at": time.time(), "native_step_before": before}
                try:
                    result = super().execute_tool(name, arguments)
                    record["result"] = deepcopy(result.result)
                    return result
                except BaseException as error:
                    record.update(error_type=type(error).__name__, error=str(error))
                    raise
                finally:
                    try:
                        after = experiment.latest["observation"]["control_step"]
                        if name == "act" and after > before:
                            self.executed_act_calls += 1
                        record.update(native_step_after=after, executed_act_calls=self.executed_act_calls)
                        with (run/"tool_calls.jsonl").open("a") as stream:
                            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False)+"\n")
                        if limit is not None and self.executed_act_calls >= limit and not self.closed:
                            self.closed = True
                            status = {"reason": "user_smoke_act_limit", "executed_act_calls": self.executed_act_calls,
                                      "native_step": after, "score": None, "full_episode_evaluation": False,
                                      "native_reset": False, "at": time.time()}
                            for target in (run/"smoke_status.json", owner/"user_smoke_complete.json"):
                                target.write_text(json.dumps(status, indent=2)+"\n")
                            with experiment.lock:
                                try:
                                    experiment.end_episode("user_smoke_act_limit")
                                finally:
                                    experiment.segment_done = True
                    finally:
                        self.operation_thread = None
    return RecordedTools()


class CciPromptProfile:
    """Normalize a null wire content field without altering the frozen profile."""
    def __init__(self, profile):
        self.profile = profile

    def transform(self, request):
        normalized = deepcopy(request)
        nulls = []
        for index, item in enumerate(normalized.get("input", [])):
            if "content" in item and item["content"] is None:
                item["content"] = []
                nulls.append(index)
        result, evidence = self.profile.transform(normalized)
        evidence["cci_null_content_as_empty_array"] = nulls
        return result, evidence


def start_capture(profile, run, model_cfg):
    """Capture and normalize actual wire input using the same upstream and key."""
    from roborsi.planner.provider_capture import ProviderCaptureConfig
    from .skillpack.capture_guard import GuardedCapture
    from .runtime_config import resolve_base_url, resolve_model
    owner = Path(run).parent
    upstream = resolve_base_url()
    cci = None
    provider_kind = model_cfg.get("provider_kind", "responses")
    if provider_kind == "cci":
        from .cci_protocol import CciAdapter
        # The guard owns the cumulative episode deadline. Each upstream attempt
        # must retain the adapter's shorter timeout so bounded retries can run.
        cci = CciAdapter(upstream, resolve_model(), owner/"cci-wire")
        cci.start()
        upstream = cci.base_url
        profile = CciPromptProfile(profile)
    elif provider_kind != "responses":
        raise ValueError("provider_kind must be responses or cci")
    prior_rows = []
    for usage_file in model_cfg.get("prior_usage_files", []):
        prior_rows.extend(json.loads(line) for line in Path(usage_file).read_text().splitlines() if line)
    capture = GuardedCapture(
        ProviderCaptureConfig(upstream, "runtime", request_encrypted_content=True),
        owner/"api", case_dir=owner, model=resolve_model(), model_label=owner.name,
        cohort_root=owner, prompt_profile=profile, enforce_paid_cache=provider_kind != "cci",
        case_deadline=model_cfg.get("case_wall_deadline"),
        prior_rows=prior_rows,
    )
    try:
        capture.start()
    except BaseException:
        if cci is not None:
            cci.stop()
        raise
    if cci is not None:
        original_stop = capture.stop
        def stop():
            try:
                original_stop()
            finally:
                cci.stop()
        capture.stop = stop
    return capture


def stage_cci_catalog(workspace, model):
    """Declare the custom model's exec/wait transport, without extra instructions."""
    template = Path(__file__).parent/"cci_model_template.json"
    value = json.loads(template.read_text())
    value["models"][0].update(slug=model, display_name=model)
    path = Path(workspace)/"preparation/cci-model-catalog.json"
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n")
    return path
