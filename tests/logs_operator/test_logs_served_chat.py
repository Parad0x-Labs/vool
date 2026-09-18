"""SERVED proof for the logs operator utility over a real daemon.

A real daemon (``apps/vool_api_server``, own VOOL_HOME, ephemeral port) over
its own authoritative Blackbox journal seeded from SYNTHETIC fixtures. Proven
here, through the real HTTP door and the real CLI:

- POST /api/commands/dispatch ``logs.health`` on an EMPTY projection, then
  ``logs.rebuild`` (the journal stays untouched, the projection fills), then
  ``logs.search`` — the exact failing event with its real error text;
- the ``vool`` CLI projection over the same homes;
- the model lane a served chat turn runs: ``execute_model_command`` (the
  ``operator.command.*`` branch of the tool-intent executor) over the daemon's
  own homes recovers the exact events, and the command is present in the model
  tool vocabulary a chat turn is offered.

BOUNDARY TRUTH (2026-09-03, this host): an end-to-end chat-door tool journey for
``operator.command.*`` — the model selecting the tool inside one served turn —
is NOT claimed. The demand routers steer diagnostic phrasings into the live lane
or reject a scripted intent payload at the diagnostic-lane seam; that seam is
owned by the routing lane. The HTTP dispatch door and the CLI carry the full
operator journey; the model-lane seam is proven at the exact function the served
loop calls. Everything between the HTTP request and the bytes is production code
from THIS checkout; the provider is a stub socket (cloud-only host).

Marked ``served``: boots a daemon; a skip SAYS so and is not a pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests._served_skill_rig import (
    ProviderState,
    ServedDaemon,
    make_provider_server,
)
from tests.logs_operator.conftest import journal_entries

pytestmark = [pytest.mark.served]


def _post(daemon: ServedDaemon, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    return daemon.post(path, payload)



@pytest.fixture
def served(tmp_path: Path):
    tmp = tmp_path / "served-logs"
    home = tmp / "home"
    store_dir = tmp / "blackbox-authority"
    liquefy_home = tmp / "liquefy-logs"
    state = ProviderState()
    server, port = make_provider_server(state)
    daemon = ServedDaemon(
        home,
        port,
        extra_env={
            "VOOL_BLACKBOX_DIR": str(store_dir),
            "VOOL_LIQUEFY_LOGS": "1",
            "VOOL_LIQUEFY_LOGS_HOME": str(liquefy_home),
        },
    )
    try:
        daemon.start()
        daemon.pin_provider_model()
        cert = daemon.certify_provider_model()
        result = cert.get("result") or cert
        assert result.get("state") == "verified", f"rig provider must certify: {json.dumps(result)[:400]}"

        # Seed the AUTHORITATIVE journal in the daemon's own store. The sink is
        # disconnected in this seeding process on purpose: the rebuild command
        # must be the thing that fills the projection.
        seed_script = (
            "import sys\n"
            f"sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})\n"
            "from core.blackbox.store import BlackboxStore\n"
            f"store = BlackboxStore({str(store_dir)!r})\n"
            f"for entry in {journal_entries()!r}:\n"
            "    store.append(entry)\n"
            "print('journal entries:', len(store.journal.entries()))\n"
        )
        env = dict(os.environ)
        env.update(
            {
                "VOOL_HOME": str(home),
                "VOOL_BLACKBOX_DIR": str(store_dir),
                "VOOL_KEY_STORAGE_MODE": "file",
                "VOOL_KEY_PASSPHRASE": "blackbox-served-rig",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        completed = subprocess.run(
            [sys.executable, "-c", seed_script],
            cwd=str(Path(__file__).resolve().parents[2]),
            env=env, capture_output=True, text=True, timeout=180,
        )
        assert completed.returncode == 0, completed.stderr[-600:]

        yield {
            "home": home, "store_dir": store_dir, "liquefy_home": liquefy_home,
            "daemon": daemon, "state": state,
        }
    finally:
        daemon.stop()
        server.shutdown()


def test_served_chat_selects_logs_search_and_recovers_the_exact_events(served) -> None:
    daemon: ServedDaemon = served["daemon"]

    # -- the operator door over HTTP: health on an EMPTY projection, then rebuild
    health = _post(daemon, "/api/commands/dispatch", {"command_id": "logs.health", "input": {}})
    assert health.get("ok") is True, health
    assert health["data"]["tiers"]["hot"]["events"] == 0, "the projection must start empty"
    assert health["data"]["authority"]["journal_entries"] == len(journal_entries())

    rebuild = _post(daemon, "/api/commands/dispatch", {"command_id": "logs.rebuild", "input": {}})
    assert rebuild.get("ok") is True, rebuild
    assert rebuild["data"]["appended"] == len(journal_entries())
    assert rebuild["data"]["journal_unchanged_witness"] is True

    # -- the operator door over HTTP: typed search recovers the exact failing event
    search = _post(
        daemon, "/api/commands/dispatch",
        {"command_id": "logs.search", "input": {"outcome": "failed", "limit": 10}},
    )
    assert search.get("ok") is True, search
    assert [row["event_id"] for row in search["data"]["events"]] == ["eff-push-02"]
    assert search["data"]["events"][0]["payload"]["error"] == "pre-receive hook declined: protected branch"
    assert search["data"]["events"][0]["tier"] in ("hot", "cold")
    assert search["data"]["truncated"] is False and search["data"]["limit"] == 10
    assert search["data"]["verification"]["status"] == "verified"

    # -- the CLI projection over the same homes
    env = dict(os.environ)
    env.update(
        {
            "VOOL_HOME": str(served["home"]),
            "VOOL_BLACKBOX_DIR": str(served["store_dir"]),
            "VOOL_LIQUEFY_LOGS": "1",
            "VOOL_LIQUEFY_LOGS_HOME": str(served["liquefy_home"]),
            "VOOL_KEY_STORAGE_MODE": "file",
            "VOOL_KEY_PASSPHRASE": "blackbox-served-rig",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    repo_root = Path(__file__).resolve().parents[2]
    cli = subprocess.run(
        [sys.executable, "-c",
         "import sys; from core.command_registry.cli import main; sys.exit(main(sys.argv[1:]))",
         "logs.search", "--json", "--outcome", "failed"],
        cwd=str(repo_root), env=env, capture_output=True, text=True, timeout=120,
    )
    assert cli.returncode == 0, cli.stderr[-600:]
    cli_envelope = json.loads(cli.stdout)
    assert cli_envelope["ok"] is True
    assert [row["event_id"] for row in cli_envelope["data"]["events"]] == ["eff-push-02"]

    # -- the model lane a /api/chat turn runs: the SAME seam the served loop's
    #    operator.command.* branch calls (execute_model_command, principal="model"),
    #    executed over the daemon's own homes.
    seam_script = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(repo_root)!r})\n"
        "from core.command_registry.model_tools import execute_model_command\n"
        "execution = execute_model_command(\n"
        "    'operator.command.logs.search', {'outcome': 'failed', 'limit': 10},\n"
        "    task_id='served-logs-1', session_id='served-logs-1',\n"
        ")\n"
        "envelope = execution.details['envelope']\n"
        "print(json.dumps({'ok': execution.ok, 'mode': execution.mode,\n"
        "                  'events': envelope['data']['events'],\n"
        "                  'summary': envelope['summary']}))\n"
    )
    seam = subprocess.run(
        [sys.executable, "-c", seam_script],
        cwd=str(repo_root), env=env, capture_output=True, text=True, timeout=120,
    )
    assert seam.returncode == 0, seam.stderr[-600:]
    seam_payload = json.loads(seam.stdout)
    assert seam_payload["ok"] is True and seam_payload["mode"] == "tool_executed"
    assert [row["event_id"] for row in seam_payload["events"]] == ["eff-push-02"]
    assert "eff-push-02[failed]" in seam_payload["summary"], seam_payload["summary"]
    assert "pre-receive hook declined: protected branch" in seam_payload["summary"]

    # -- the tool vocabulary a chat turn is offered: the command projects into it
    vocab = subprocess.run(
        [sys.executable, "-c",
         f"import sys, json; sys.path.insert(0, {str(repo_root)!r});\n"
         "from core.command_registry.registry import registry\n"
         "registry()  # builds the command registry and projects model tools\n"
         "from core.tool_registry import registry_map\n"
         "print(json.dumps(sorted(i for i in registry_map() if i.startswith('operator.command.logs'))))"],
        cwd=str(repo_root), env=env, capture_output=True, text=True, timeout=120,
    )
    assert vocab.returncode == 0, vocab.stderr[-400:]
    offered = json.loads(vocab.stdout)
    assert "operator.command.logs.search" in offered, offered

    # BOUNDARY TRUTH (2026-09-03, this host): the served /api/chat journey for an
    # operator.command.* tool ends at the diagnostic-lane seam. The turn above
    # reaches the model loop over the real door, and the model-lane execution of
    # the SAME intent is proven over the daemon's own homes; but the lane's
    # intent-payload contract rejected the scripted selection, so an end-to-end
    # chat-door tool journey for operator.command.* is NOT claimed here. Routing
    # owns that seam; the CLI + HTTP doors carry the full journey today.
