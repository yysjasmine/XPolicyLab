"""Check skillpack ownership, CLI transport, recovery and native ACK feedback."""

from __future__ import annotations

from importlib import import_module

import copy
import hashlib
import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from roborsi.agents.config import AgentConfig
from roborsi.embodied_agent import EmbodiedAgent
from roborsi.planner.codex_cli import CodexCliPlanner
from roborsi.planner.codex_cli_config import CodexCliConfig

hybrid_agent = import_module("XPolicyLab.policy.SenseNova-RoboRSI.hybrid_agent")
skillpack_agent = import_module("XPolicyLab.policy.SenseNova-RoboRSI.skillpack_agent")
execution_profile = import_module("XPolicyLab.policy.SenseNova-RoboRSI.execution_profile")
HybridBackend = import_module("XPolicyLab.policy.SenseNova-RoboRSI.hybrid_backend").HybridBackend
hybrid_launcher = import_module("XPolicyLab.policy.SenseNova-RoboRSI.hybrid_launcher")
BackendClosed = import_module("XPolicyLab.policy.SenseNova-RoboRSI.hybrid_backend").BackendClosed
load_reference_source = import_module("XPolicyLab.policy.SenseNova-RoboRSI.hybrid_backend").load_reference_source

P = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def reference():
    pins = json.loads((P / "submission.json").read_text())["hybrid"]
    return load_reference_source(
        P / pins["source_path"], P / pins["manifest_path"], pins["manifest_sha256"]
    )


@pytest.fixture
def cfg():
    config = json.loads(
        (
            P / "vendor/robodojo_adaptive/resources/fresh-configs/align_blocks-s0.json"
        ).read_text()
    )
    config["model"] = "fixture-model"
    config["reasoning_effort"] = "xhigh"
    config["episode_seconds"] = 14400
    config["options"].update(codex_compact_tokens=200000, approved_steps_min=5)
    return config


def cli_config(executable):
    return CodexCliConfig.from_dict(
        {
            "executable": str(executable),
            "expected_version": "codex-cli 0.160.1",
            "expected_sha256": "f34a4d2301892ae96c90097786bfe5dc269f187b6f69faf42a7b357b8c081e35",
            "tools": "external",
            "inherit_env": False,
            "env_vars": ["CODEX_API_KEY", "NO_PROXY", "no_proxy", "HTTPS_PROXY"],
            "config_overrides": {
                "model_provider": "runtime",
                "model_providers.runtime.base_url": "https://provider.example.invalid/v1",
                "model_providers.runtime.wire_api": "responses",
                "model_providers.runtime.env_key": "CODEX_API_KEY",
                "model_auto_compact_token_limit": 200000,
            },
        }
    )






def test_agent_config_custom_factory_reaches_embodied_agent(tmp_path):
    raw = yaml.safe_load((P / "agent.yaml").read_text())
    raw["source_root"] = str(P)
    raw["code"] = [str(P/name) for name in raw["code"]]
    raw["agent"]["model"] = "fixture-model"
    raw["agent"]["codex_cli"]["executable"] = "/not-launched"
    # AgentConfig seals the preset together with its actual factory source.
    # Keep this short-lived preset inside source_root, outside runtime installs.
    with tempfile.TemporaryDirectory(prefix=".agent-preset-", dir=P/"tests") as temporary:
        preset = Path(temporary)/"agent.yaml"
        preset.write_text(yaml.safe_dump(raw))
        case = tmp_path / "episode"
        case.mkdir()
        agent = AgentConfig.from_file(preset).create_agent(case)
    assert isinstance(agent, skillpack_agent.SkillpackEmbodiedAgent)
    assert isinstance(agent, EmbodiedAgent)
    assert agent.planner == "codex_cli" and agent.codex_cli.tools == "external"
    assert agent.reasoning_effort == "xhigh"
    assert (case / "agent-config.json").is_file()
    agent.close()



@pytest.mark.skipif(not hasattr(os, "memfd_create"), reason="Verified memfd transport requires Linux")
def test_launcher_uses_original_mounts_and_hides_secrets_in_memfd(
    reference, cfg, tmp_path, monkeypatch
):
    binary, bwrap = tmp_path / "codex", tmp_path / "bwrap"
    binary.write_bytes(b"\x7fELFfixture")
    bwrap.write_bytes(b"fixture-bubblewrap")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    python = tmp_path / "agent-env/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("fixture")
    run = tmp_path / "run"
    for name in [
        "bridge",
        "tools",
        "observations/evaluation-0001",
        "workspace",
        "segment",
    ]:
        (run / name).mkdir(parents=True)
    training = tmp_path / "training"
    training.mkdir()
    cfg.update(
        agent_python=str(python),
        training_set=str(training),
        _observation_episode="evaluation-0001",
    )
    planner = hybrid_launcher.HybridCodexCliPlanner(
        reference=reference,
        bubblewrap=bwrap,
        bubblewrap_sha256=hashlib.sha256(bwrap.read_bytes()).hexdigest(),
        output_dir=run,
        repo_root=workspace,
        model=cfg["model"],
        timeout_s=None,
        dashboard_events=None,
        reasoning_effort="xhigh",
        config=cli_config(binary),
    )
    assert isinstance(planner, CodexCliPlanner)
    identity = {
        "executable": str(binary),
        "native_binary": {"path": str(binary)},
        "version": "codex-cli 0.160.1",
    }
    monkeypatch.setattr(planner, "preflight", lambda: identity)
    monkeypatch.setenv("CODEX_API_KEY", "fixture-api-secret")
    monkeypatch.setenv("NO_PROXY", "localhost,provider.example.invalid")
    monkeypatch.setenv("no_proxy", "lowercase.example.invalid")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.invalid:8080")
    seen = {}

    class Input(io.BytesIO):
        def close(self):
            seen["prompt"] = self.getvalue()
            super().close()

    process = SimpleNamespace(stdin=Input(), pid=999999)

    def spawn(argv, **kwargs):
        seen["argv"], seen["env"] = argv, kwargs["env"]
        seen["options"] = os.pread(kwargs["pass_fds"][-1], 1024 * 1024, 0).split(b"\0")
        return process

    planner._spawn = spawn
    personal_home = os.environ.get("HOME")
    # A stale template must never override the resolved model on the planner.
    cfg["model"] = "stale-template-model"
    returned = planner.launch_segment(
        cfg,
        run,
        tmp_path / "private-home",
        run / "segment",
        "original prompt",
        "same-session",
    )
    assert returned is process
    assert seen["argv"][:2] == [str(bwrap), "--args"]
    assert "fixture-api-secret" not in repr(seen["argv"])
    assert "fixture-api-secret" not in repr(seen["env"])
    assert b"fixture-api-secret" in seen["options"]
    for name, value in ((b"NO_PROXY", b"localhost,provider.example.invalid"),
                        (b"no_proxy", b"lowercase.example.invalid"),
                        (b"HTTPS_PROXY", b"http://proxy.example.invalid:8080")):
        index = seen["options"].index(name)
        assert seen["options"][index + 1] == value
    assert not any(b"auth.json" in part for part in seen["options"])
    for mount in [
        b"/training",
        b"/workspace",
        b"/tools",
        b"/bridge",
        b"/observations/evaluation-0001",
        b"--unshare-pid",
        b"--unshare-user",
        b"--clearenv",
        b"/codex-resources",
        b"/codex-code-mode-host",
        b"/proc/self/exe",
        b"--symlink",
    ]:
        assert mount in seen["options"]
    boundary = seen["argv"].index("--")
    command = seen["argv"][boundary + 1 :]
    assert command[:4] == ["/codex", "exec", "resume", "same-session"]
    assert 'model_providers.runtime.env_key="CODEX_API_KEY"' in command
    assert command[command.index("--model") + 1] == "fixture-model"
    assert 'model_providers.runtime.base_url="https://provider.example.invalid/v1"' in command
    assert "stale-template-model" not in command
    assert seen["prompt"] == b"original prompt"
    assert not (tmp_path / "private-home/auth.json").exists()
    assert os.environ.get("HOME") == personal_home
    assert "fixture-api-secret" not in (run / "segment/cli-identity.json").read_text()


@pytest.mark.parametrize(
    "failure,new_session",
    [("maximum number of images exceeded", True), ("HTTP 429 capacity", False)],
)
def test_original_live_context_recovery_keeps_the_same_physical_episode(
    reference, cfg, tmp_path, monkeypatch, failure, new_session
):
    class Clock:
        now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(reference.runner, "time", clock)
    monkeypatch.setattr(reference.runner, "stop_process", lambda _process: None)
    experiment = SimpleNamespace(
        lock=threading.RLock(),
        phase="evaluation",
        latest={"done": False, "observation": {"control_step": 7}},
        segment_done=False,
        fault=None,
        review_status=lambda _reply: {},
        finalize=lambda _reason: None,
        checkpoint=lambda _name: "checkpoint",
    )
    calls = []

    def launch(_cfg, _run, _home, segment, prompt, session):
        calls.append((session, prompt))
        rows = [{"type": "thread.started", "thread_id": "source-session"}]
        if len(calls) == 1:
            rows.append({"type": "turn.failed", "error": {"message": failure}})
        else:
            experiment.segment_done = True
            experiment.latest["done"] = True
        (segment / "model.jsonl").write_text("\n".join(json.dumps(row) for row in rows))
        return SimpleNamespace(poll=lambda: 0, returncode=0)

    result = reference.runner.run_segment(
        experiment,
        cfg,
        tmp_path,
        tmp_path / "home",
        "evaluation",
        "original",
        120,
        "initial-session",
        launch=launch,
    )
    assert len(calls) == 2
    assert calls[1][0] == (None if new_session else "source-session")
    assert "SAME live episode" in calls[1][1]
    assert result["live_context_recoveries"][0]["native_reset"] is False
    assert result["live_context_recoveries"][0]["control_step"] == 7
    assert result["status"] == "finished"


class OfflineToolProcess:
    """Thread-backed CLI transport fake; invokes the registered MCP handlers."""
    def __init__(self, callback, toolkit, cfg, run, segment, prompt):
        self.returncode = None
        def execute():
            try:
                callback(toolkit, cfg, run, segment, prompt)
                (segment/"model.jsonl").write_text(json.dumps({"type": "thread.started", "thread_id": "offline-session"})+"\n")
                self.returncode = 0
            except BaseException:
                import traceback
                (segment/"model.stderr").write_text(traceback.format_exc())
                self.returncode = 1
        self.thread = threading.Thread(target=execute, daemon=True)
        self.thread.start()
    def poll(self):
        return self.returncode
    def wait(self, timeout=None):
        self.thread.join(timeout)
        if self.thread.is_alive():
            raise TimeoutError("Offline tool operation did not finish")
        return self.returncode


def make_offline_tool_planner(callback):
    class OfflinePlanner:
        def __init__(self, **kwargs):
            self.process = None
        def launch_segment(self, cfg, run, home, segment, prompt, session=None):
            segment.mkdir(exist_ok=True)
            (segment/"prompt.txt").write_text(prompt)
            self.process = OfflineToolProcess(callback, self.toolkit, cfg, run, segment, prompt)
            return self.process
        def cancel(self):
            if self.process is not None:
                self.process.wait(timeout=5)
    return OfflinePlanner



@pytest.mark.parametrize("episode_seconds", [14400, None])
def test_skillpack_tools_five_acknowledged_steps_close_without_self_scoring(
    reference, cfg, robot, vla, tmp_path, monkeypatch, episode_seconds
):
    cfg["episode_seconds"] = episode_seconds
    reference = execution_profile.extend_reference(reference)
    episode = tmp_path / "episode"
    episode.mkdir()
    prepared = execution_profile.prepare_skillpack_context(P, "align_blocks", episode)
    current_observation = import_module("XPolicyLab.policy.SenseNova-RoboRSI.tests.test_hybrid_backend").observation
    backend = HybridBackend(reference, task_name="align_blocks", episode_dir=episode,
        initial_observation=current_observation(), step_limit=200, metadata_path=robot.metadata,
        expected_metadata_sha256=hashlib.sha256(robot.metadata.read_bytes()).hexdigest(),
        resource_root=robot.root, vla_port=19802)
    original_request = reference.runner.Experiment.request
    completed = []
    def execute(toolkit, _cfg, _run, _segment, prompt):
        assert prompt.startswith("Carry out the current episode task.")
        assert ("wall_seconds_budget" in prompt) == (episode_seconds is not None)
        saved_cfg = json.loads((_run/"config.json").read_text())
        assert saved_cfg["episode_seconds"] == episode_seconds
        assert toolkit.execute_tool("observe", {}).result["observation"]["control_step"] == 0
        proposal = toolkit.execute_tool("vla_propose", {"subgoal": "Align the visible blocks."}).result
        result = toolkit.execute_tool("act", {"proposal_id": proposal["proposal_id"], "steps": 5,
            "reason": "Observed fixture prefix", "assessment": {"current_subgoal": "Align"}}).result
        assert result["executed_steps"] == 5 and result["done"]
        completed.append(result)
    monkeypatch.setattr(skillpack_agent, "SkillpackCodexCliPlanner", make_offline_tool_planner(execute))
    monkeypatch.setattr(reference.runner, "stop_process", lambda process: process.wait(timeout=5))
    agent = skillpack_agent.SkillpackEmbodiedAgent(mcp_servers=(), output_dir=episode,
        planner="codex_cli", model=cfg["model"], reasoning_effort="xhigh", max_turns=None,
        max_tokens=None, codex_cli=cli_config(Path("/not-used")))
    context = hybrid_agent.HybridRunContext(reference, backend, cfg, episode, prepared.workspace,
        prepared.workspace, P/"vendor/robodojo_adaptive/resources/interfaces/BENCHMARK.md",
        Path(sys.executable), Path("/not-used"), "0"*64,
        execution_profile=execution_profile.load_skillpack_profile(P, "align_blocks", episode), model_cfg={})
    controller = skillpack_agent.SkillpackController(agent, context).start()
    try:
        for step in range(5):
            pending = controller.next_action(timeout=10)
            assert pending.step_index == step and backend.control_step == step
            assert set(pending.action) == {"left_arm_joint_state", "left_ee_joint_state",
                                           "right_arm_joint_state", "right_ee_joint_state"}
            controller.acknowledge(pending.request_id, current_observation(), terminated=step == 4)
        controller._thread.join(10)
        assert controller.done and controller.error is None and completed
        assert controller.result.stats["native_success"] is None
        assert controller.result.finish_result is None
        result = json.loads((episode/"controller/episodes/evaluation-0001/result.json").read_text())
        assert result["native_done"] is True and result["success"] is None
        assert (episode/"workspace/.agents/skills/robo-general/SKILL.md").is_file()
        assert context.execution_profile.manifest["skills"] == ["robo-general", "robodojo-spatial-localize"]
        assert not (episode/"workspace/.agents/skills/align_blocks").exists()
        assert not (episode/"workspace/AGENTS.md").exists()
        assert not (episode/"controller/tools/robot.py").exists()
        assert reference.runner.Experiment.request is original_request
        assert not (episode/"controller/bridge").exists()
    finally:
        controller.cancel()



def test_cancel_releases_backend_before_agent_close_without_experiment_lock():
    calls = []
    backend = SimpleNamespace(cancel=lambda: calls.append("backend"))

    class Agent:
        def bind_reference(self, _context):
            return self

        def close(self):
            calls.append("agent")

    controller = hybrid_agent.HybridController(
        Agent(), SimpleNamespace(backend=backend)
    )
    controller.cancel()
    assert calls == ["backend", "agent"]
    assert controller.next_action() is None


def test_controller_cleanup_allows_3600_seconds_without_sleeping():
    waits = []
    backend = SimpleNamespace(cancel=lambda: None)

    class Agent:
        def bind_reference(self, _context):
            return self

        def close(self):
            return None

    controller = hybrid_agent.HybridController(Agent(), SimpleNamespace(backend=backend))
    controller._thread = SimpleNamespace(
        ident=1,
        join=lambda timeout: waits.append(timeout),
        is_alive=lambda: False,
    )
    controller.close()
    assert waits == [3600.0]


def test_terminal_rpc_timeout_includes_cleanup_and_ack_margin():
    import yaml

    config = yaml.safe_load((P / "deploy.yml").read_text())
    assert config["request_timeout_s"] == 3660.0
    assert config["request_timeout_s"] >= hybrid_agent.CONTROLLER_CLEANUP_TIMEOUT_SECONDS + 60
