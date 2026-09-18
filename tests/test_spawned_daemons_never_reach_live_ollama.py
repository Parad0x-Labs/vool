"""A served daemon spawned by a test never sees the machine's live Ollama.

The in-process socket policy in `conftest.block_live_local_ollama_under_pytest` cannot reach a
child process. On 2026-09-07 daemons booted by the pack and the shard run listed, inspected and
certified against 127.0.0.1:11434 and loaded two local models (12.7 GB) into the operator's Ollama
-- on a machine whose rule is no local model launches. The fence now rides `subprocess.Popen`:
a child whose command names the api server inherits dead endpoints unless the launcher chose its own.
"""

from __future__ import annotations

import json
import subprocess
import sys

_PRINT_ENV = (
    "import json, os, sys; print(json.dumps({k: os.environ.get(k) for k in "
    "('OLLAMA_HOST', 'VOOL_OLLAMA_URL', 'VOOL_OLLAMA_CHAT_URL', 'VOOL_REGISTER_INSTALLED_OLLAMA_MODELS')}))"
)


def _child_env(extra_argv: list[str], env: dict[str, str] | None = None) -> dict[str, str | None]:
    completed = subprocess.run(
        [sys.executable, "-c", _PRINT_ENV, *extra_argv], capture_output=True, text=True, timeout=60, env=env
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_a_child_that_runs_the_api_server_gets_dead_ollama_endpoints() -> None:
    seen = _child_env(["apps.vool_api_server", "--port", "0"])
    assert seen["OLLAMA_HOST"] == "http://127.0.0.1:9"
    assert seen["VOOL_OLLAMA_URL"] == "http://127.0.0.1:9"
    assert seen["VOOL_OLLAMA_CHAT_URL"] == "http://127.0.0.1:9/api/chat"
    assert seen["VOOL_REGISTER_INSTALLED_OLLAMA_MODELS"] == "0"


def test_a_launcher_that_chose_its_own_endpoint_keeps_it() -> None:
    import os

    env = dict(os.environ)
    env["OLLAMA_HOST"] = "http://127.0.0.1:54321"
    seen = _child_env(["apps.vool_api_server"], env=env)
    assert seen["OLLAMA_HOST"] == "http://127.0.0.1:54321"
    assert seen["VOOL_OLLAMA_URL"] == "http://127.0.0.1:9"


def test_an_unrelated_child_is_left_alone() -> None:
    import os

    env = {k: v for k, v in os.environ.items() if not k.startswith(("OLLAMA", "VOOL_OLLAMA"))}
    seen = _child_env([], env=env)
    assert seen["OLLAMA_HOST"] is None
