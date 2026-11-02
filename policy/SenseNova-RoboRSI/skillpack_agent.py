"""Frozen three-tool skillpack on this checkout's native XPolicyLab ACK loop."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import threading

from roborsi.planner.codex_cli_config import CodexCliConfig

from .hybrid_agent import HybridController, HybridEmbodiedAgent
from .hybrid_launcher import HybridCodexCliPlanner
from .runtime_config import POLICY_DIR


class SkillpackCodexCliPlanner(HybridCodexCliPlanner):
    """Own MCP/capture per planner instance; no process-global launch patches."""

    def __init__(self, *, profile, context, **kwargs):
        super().__init__(**kwargs)
        self.profile, self.context = profile, context
        self.toolkit = None
        self._robot_server = self._capture = None
        self._resource_lock = threading.RLock()

    def launch_segment(self, cfg, run, private_home, segment, prompt, session=None):
        from .execution_profile import start_capture

        with self._resource_lock:
            if self._cancelled.is_set():
                raise RuntimeError("Skillpack planner was cancelled")
            if self.toolkit is None:
                raise RuntimeError("Bind the episode's registered MCP tools before launch")
            if self._robot_server is None:
                self._robot_server = self.toolkit.mcp_server()
                self._robot_server.start()
            if self._capture is None:
                self._capture = start_capture(self.profile, Path(run), self.context.model_cfg or {})
            overrides = dict(self.config.config_overrides)
            overrides.update({
                "model_providers.runtime.base_url": self._capture.base_url,
                "model_instructions_file": "/workspace/preparation/agent_system.md",
                "model_auto_compact_token_limit": 200000,
                "model_context_window": 200000,
                "mcp_servers.robot.url": self._robot_server.url,
                "mcp_servers.robot.enabled": True,
                "mcp_servers.robot.startup_timeout_sec": 30,
                "mcp_servers.robot.tool_timeout_sec": 1200,
            })
            if (self.context.model_cfg or {}).get("provider_kind") == "cci":
                overrides["model_catalog_json"] = "/workspace/preparation/cci-model-catalog.json"
            environment = dict(self.config.env)
            for name in ("NO_PROXY", "no_proxy"):
                environment[name] = ",".join(filter(None, [environment.get(name), os.environ.get(name), "127.0.0.1", "localhost"]))
            previous = self.config
            forwarded = tuple(name for name in previous.env_vars if name not in {"NO_PROXY", "no_proxy"})
            self.config = replace(previous, config_overrides=overrides, env=environment, env_vars=forwarded)
            short = self.profile.episode_prompt(cfg)
            Path(segment).mkdir(parents=True, exist_ok=True)
            (Path(segment)/"prompt-assets.json").write_text(json.dumps({
                "profile_id": self.profile.profile_id,
                "task": self.profile.task,
                "registered_robot_tools": [s["name"] for s in self.toolkit.get_tools_spec()],
                "model_prompt_bytes": len(short.encode()),
                "operator_limits_in_prompt": False,
                "planner": "SkillpackCodexCliPlanner",
                "source": str(POLICY_DIR),
            }, indent=2)+"\n")
            try:
                return super().launch_segment(cfg, run, private_home, segment, short, session)
            finally:
                self.config = previous

    def cancel(self):
        super().cancel()
        with self._resource_lock:
            for name in ("_robot_server", "_capture"):
                resource = getattr(self, name)
                setattr(self, name, None)
                if resource is not None:
                    try:
                        resource.stop()
                    except Exception as error:
                        # Keep terminal acknowledgement independent of proxy cleanup.
                        path = Path(self.context.episode_dir)/"skillpack-cleanup.jsonl"
                        with path.open("a") as stream:
                            stream.write(json.dumps({"resource": name, "error_type": type(error).__name__})+"\n")


class SkillpackEmbodiedAgent(HybridEmbodiedAgent):
    """Keep the EmbodiedAgent owner and native controller, replacing its context."""

    def stage_workspace(self, context, workspace, run):
        if context.execution_profile is None:
            raise ValueError("The current task's frozen skillpack profile is required")
        # The legacy socket remains private infrastructure; no Python robot module
        # is published. Experiment.request also verifies the MCP operation thread.
        (Path(run)/"tools/robot.py").unlink(missing_ok=True)
        context.execution_profile.stage_live_workspace(workspace, owner=context.episode_dir)
        if (context.model_cfg or {}).get("provider_kind") == "cci":
            from .execution_profile import stage_cci_catalog
            stage_cci_catalog(workspace, self.model, reasoning_effort=self.reasoning_effort)

    def evaluation_prompt(self, context, cfg):
        return context.execution_profile.episode_prompt(cfg)

    def make_experiment(self, context, cfg, run):
        from .execution_profile import runtime_modules

        runtime = runtime_modules(POLICY_DIR)
        base = runtime.native_request.make_experiment_class(context.reference, allow_direct_targets=True)
        owner = self

        class RegisteredExperiment(base):
            def request(self, req):
                toolkit = getattr(owner, "_skillpack_toolkit", None)
                if toolkit is None or toolkit.operation_thread != threading.get_ident():
                    raise context.reference.runner.InvalidAction("Use the registered robot MCP tools")
                return super().request(req)

        return RegisteredExperiment(cfg, run, context.backend)

    def make_planner(self, context, run, workspace):
        return SkillpackCodexCliPlanner(
            profile=context.execution_profile, context=context,
            reference=context.reference, bubblewrap=context.bubblewrap,
            bubblewrap_sha256=context.bubblewrap_sha256, output_dir=run,
            repo_root=workspace, model=self.model, timeout_s=None,
            dashboard_events=None, reasoning_effort=self.reasoning_effort, config=self.codex_cli,
        )

    def bind_experiment(self, context, planner, experiment, run):
        from .execution_profile import start_toolkit

        self._skillpack_toolkit = start_toolkit(experiment, run, context.episode_dir, context.model_cfg or {})
        planner.toolkit = self._skillpack_toolkit


class SkillpackController(HybridController):
    def cancel(self, timeout=15.0):
        return super().cancel(timeout)

    close = cancel


def make_agent(case_dir, *, config):
    options = dict(config.document.get("agent", {}))
    if (options.get("planner") != "codex_cli" or options.get("reasoning_effort") not in {"medium", "xhigh"}
            or options.get("max_turns") is not None or options.get("max_tokens") is not None):
        raise ValueError("Skillpack requires the external Codex CLI, medium/xhigh and no output-token cap")
    if config.document.get("prompt_file") or config.document.get("skills") or config.document.get("runtime"):
        raise ValueError("The frozen execution profile owns prompts and skills")
    options["codex_cli"] = CodexCliConfig.from_dict(options.get("codex_cli", {}))
    if options["codex_cli"].tools != "external":
        raise ValueError("Skillpack requires its three registered robot MCP tools")
    return SkillpackEmbodiedAgent(mcp_servers=(), output_dir=case_dir, local_tools=(), **options)
