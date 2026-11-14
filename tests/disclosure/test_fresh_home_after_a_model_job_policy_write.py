"""A fresh home still refuses a cloud probe after a test that switched outbound work on.

test_provider_http_lifetime's background model-pull test turns ``network.outbound_enabled`` on
through the operator policy. When it wrote that into the session-wide test home, every later
fresh-home disclosure check in the same process read outbound as on: the synthetic remote
probe was no longer refused and its request reached the receiver (CI #697, shard 9 order).
The model-pull test now writes into its own home and leaves the policy as it found it.

This runs the two in one pytest process, in CI order, so the isolation stays pinned.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

POLICY_WRITER = (
    "tests/test_provider_http_lifetime.py::"
    "test_background_model_job_keeps_selected_endpoint_when_process_environment_changes"
)
FRESH_HOME_CHECKS = (
    "tests/disclosure/test_direct_helpers.py::test_fresh_home_cloud_off_refuses_synthetic_remote_probe",
    "tests/disclosure/test_direct_helpers.py::test_fresh_home_backend_probe_sends_no_inventory_or_generation_request",
)


def test_a_fresh_home_refuses_the_cloud_probe_after_the_model_job_test(tmp_path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(ROOT), env.get("PYTHONPATH", ""))))
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
         f"--basetemp={tmp_path / 'inner'}", POLICY_WRITER, *FRESH_HOME_CHECKS],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=600,
    )
    tail = (run.stdout + run.stderr)[-3000:]
    assert run.returncode == 0, tail
    assert "3 passed" in run.stdout, tail
