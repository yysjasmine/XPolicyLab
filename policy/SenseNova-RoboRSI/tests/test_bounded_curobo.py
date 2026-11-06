"""Keep synthetic simulator modules isolated from other adapter tests."""
from pathlib import Path
import os
import subprocess
import sys


def test_bounded_curobo_execution_and_vla_passthrough_contracts():
    policy = Path(__file__).resolve().parents[1]
    runtime = policy / "skillpack/robot_runtime"
    result = subprocess.run(
        [sys.executable, str(policy / "tests/fixtures/bounded_curobo_contract.py")],
        env=dict(os.environ, PYTHONPATH=str(runtime)), capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
