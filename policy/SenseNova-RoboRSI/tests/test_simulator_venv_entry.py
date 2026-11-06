"""Exercise the explicit simulator venv path through the official launcher hook."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_simulator_venv_keeps_official_entry_and_native_wrapper(tmp_path):
    workspace = tmp_path / "native"
    framework = workspace / "XPolicyLab"
    policy = framework / "policy/SenseNova-RoboRSI"
    policy.mkdir(parents=True)
    source = Path(__file__).resolve().parents[1]
    shutil.copy2(source / "setup_eval_env_client.sh", policy)
    (framework / "utils").mkdir()
    (framework / "utils/resolve_eval_env_type.sh").write_text(
        "resolve_eval_env_type() { echo sim; }\n"
    )
    (workspace / "scripts").mkdir()
    (workspace / "scripts/eval_policy.sh").write_text(
        'printf "%s\\n" "$@" > "$TEST_NATIVE_ARGS"\n'
        'python -u src/eval_client/main.py --seed 2\n'
    )
    interpreter = tmp_path / "sim/bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text(
        f"#!{sys.executable}\n"
        "import json,os,sys\n"
        "if sys.argv[1:3] == ['-S','-c']:\n"
        "    print(os.path.dirname(os.path.dirname(sys.argv[0])))\n"
        "else:\n"
        "    with open(os.environ['TEST_PYTHON_CALLS'],'a') as f:\n"
        "        f.write(json.dumps({'args':sys.argv[1:], 'pythonpath':os.environ.get('PYTHONPATH')})+'\\n')\n"
    )
    interpreter.chmod(0o755)
    calls, native_args = tmp_path / "calls.jsonl", tmp_path / "native-args.txt"
    subprocess.run(
        ["bash", str(policy / "setup_eval_env_client.sh"), "RoboDojo", "swap_blocks",
         "roborsi-codex-v1", "arx_x5", "joint", "2", "3", "unused-conda-name",
         "ckpt_name=roborsi-codex-v1,action_type=joint", "12345"],
        env={**os.environ, "EVAL_ENV_TYPE": "sim", "ROBORSI_SIM_PYTHON": str(interpreter),
             "ROBORSI_SIM_EXTRA_PYTHONPATH": "/verified/sdk", "TEST_PYTHON_CALLS": str(calls),
             "TEST_NATIVE_ARGS": str(native_args)}, check=True,
    )
    rows = [json.loads(line) for line in calls.read_text().splitlines()]
    assert rows[0]["args"] == [str(policy / "sim_bootstrap/probe.py"), "--workspace", str(workspace)]
    assert rows[1]["args"] == ["-u", str(policy / "native_entry.py"), "--official-entry",
                               "src/eval_client/main.py", "--", "--seed", "2"]
    assert "/verified/sdk" in rows[1]["pythonpath"].split(":")
    args = native_args.read_text().splitlines()
    assert args[args.index("--task_name") + 1] == "swap_blocks"
    assert args[args.index("--seed") + 1] == "2"
    assert args[args.index("--device_id") + 1] == "3"
