"""SERVED selection proof for the project-archaeology skill (C21) — real
/api/chat, provider-bound.

A real daemon (``apps/vool_api_server.py``, own VOOL_HOME, ephemeral port)
with the repo's shipped native skill library answers real chat turns through
the scripted loopback provider. Proven here:

- an archaeology question SELECTS ``vool-project-archaeology`` on the served
  turn: the ``tool_offer_skills`` ledger event names it with its version, and
  the provider-bound system prompt carries the FULL SKILL.md body — the byte
  content an archaeology turn actually teaches the model;
- an unrelated turn selects NOTHING for this skill: no ledger row, no bytes
  on the wire (the no-override law: history questions must not hijack
  unrelated conversations);
- the selection survives a full daemon restart on the same home (the skill
  is disk-config, not process state);
- the typed archaeology commands are dispatchable over the REAL served
  command door (``POST /api/commands/dispatch``) — read-only effects on the
  daemon's own (empty) stores answer honestly with a typed envelope.

Marked ``served``: boots a daemon; a skip SAYS so and is not a pass.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._served_skill_rig import (
    ProviderState,
    ServedDaemon,
    make_provider_server,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_BODY = (
    REPO_ROOT / "skills" / "vool-project-archaeology" / "SKILL.md"
).read_text(encoding="utf-8").split("---", 2)[2].strip()
RELEASE_BODY = (
    REPO_ROOT / "skills" / "vool-release-gate" / "SKILL.md"
).read_text(encoding="utf-8").split("---", 2)[2].strip()

ARCHAEOLOGY_TURN = (
    "A unit test in my project fails after my change. Diagnose the root "
    "cause before proposing any fix. Use the project archaeology skill for "
    "the file history."
)
UNRELATED_TURN = "What is the capital of France? Answer in one sentence."

pytestmark = [pytest.mark.served]


@pytest.fixture(scope="module")
def served_rig(tmp_path_factory):
    """One provider + one certified daemon for the fast selection proofs."""
    tmp = tmp_path_factory.mktemp("arch-served")
    state = ProviderState()
    server, port = make_provider_server(state)
    home = tmp / "home"
    home.mkdir(parents=True)
    daemon = ServedDaemon(home, port).start()
    daemon.pin_provider_model()
    cert = daemon.certify_provider_model()
    result = cert.get("result") or cert
    assert result.get("state") == "verified", (
        f"rig provider must certify: {json.dumps(result)[:400]}"
    )
    yield type("Rig", (), {"state": state, "daemon": daemon, "home": home})
    daemon.stop()
    server.shutdown()


def _skill_names(daemon: ServedDaemon, session: str) -> dict[str, str]:
    event = daemon.skill_event(session)
    if event is None:
        return {}
    return {
        row["name"]: row.get("version")
        for row in event.get("skills") or []
    }


def test_served_archaeology_turn_SELECTS_the_skill_and_binds_its_full_body(served_rig) -> None:
    """The real chat turn is influenced by the archaeology skill: the ledger
    names it with its version, and the wire carries its FULL instructions."""
    rig = served_rig
    rig.state.requests.clear()
    rig.state.script = [
        {"final": "The trace shows which task changed the file, with provenance."}
    ]
    reply = rig.daemon.chat(ARCHAEOLOGY_TURN)
    assert reply.get("choices"), reply

    session = reply.get("vool_session_id")
    names = _skill_names(rig.daemon, session)
    assert "project-archaeology" in names, (
        f"the served turn did not select the archaeology skill: {names}"
    )
    assert names["project-archaeology"] == "1.0.0", names

    prompts = rig.state.system_prompts()
    assert prompts, "no provider-bound prompt was captured"
    bound = "\n".join(prompts)
    assert SKILL_BODY in bound, (
        "the archaeology skill's FULL body never reached the wire"
    )
    assert RELEASE_BODY not in bound, "an unrelated skill rode along"


def test_served_unrelated_turn_selects_NOTHING_for_archaeology(served_rig) -> None:
    """No override law: an unrelated conversation must not receive archaeology
    guidance — no ledger row and no bytes on the wire."""
    rig = served_rig
    rig.state.requests.clear()
    rig.state.script = [{"final": "Paris."}]
    reply = rig.daemon.chat(UNRELATED_TURN)
    assert reply.get("choices"), reply

    session = reply.get("vool_session_id")
    names = _skill_names(rig.daemon, session)
    assert "project-archaeology" not in names, (
        f"the archaeology skill hijacked an unrelated turn: {names}"
    )
    prompts = rig.state.system_prompts()
    bound = "\n".join(prompts)
    assert SKILL_BODY not in bound, (
        "the archaeology body reached the wire on an unrelated turn"
    )


def test_archaeology_skill_selection_SURVIVES_a_full_daemon_restart(tmp_path_factory) -> None:
    """A fresh daemon process on the same home must still select the skill:
    the library is disk truth, not process state."""
    tmp = tmp_path_factory.mktemp("arch-served-restart")
    state = ProviderState()
    server, port = make_provider_server(state)
    home = tmp / "home"
    home.mkdir(parents=True)

    def _boot_and_ask() -> dict[str, str]:
        daemon = ServedDaemon(home, port).start()
        try:
            daemon.pin_provider_model()
            cert = daemon.certify_provider_model()
            result = cert.get("result") or cert
            assert result.get("state") == "verified", json.dumps(result)[:300]
            state.requests.clear()
            state.script = [{"final": "Traced."}]
            reply = daemon.chat(ARCHAEOLOGY_TURN)
            assert reply.get("choices"), reply
            return _skill_names(daemon, reply.get("vool_session_id"))
        finally:
            daemon.stop()

    before = _boot_and_ask()
    assert "project-archaeology" in before, f"first boot did not select: {before}"

    after = _boot_and_ask()
    assert "project-archaeology" in after, (
        f"the restart lost the skill selection: {after}"
    )
    assert after["project-archaeology"] == "1.0.0"
    server.shutdown()


def test_served_command_door_dispatches_archaeology_search(served_rig) -> None:
    """The typed archaeology command answers over the REAL served HTTP door:
    ok-True with an honest empty envelope on the daemon's own fresh stores."""
    rig = served_rig
    dispatched = rig.daemon.post(
        "/api/commands/dispatch",
        {"command_id": "archaeology.search", "input": {"text": "pre-receive", "limit": 5}},
    )
    assert dispatched.get("ok") is True, json.dumps(dispatched)[:600]
    data = dispatched.get("data") or {}
    assert data.get("schema") == "vool.archaeology.v1", data
    assert data.get("operation") == "search"
    assert data.get("execution") == "none"
    assert data.get("untrusted_content") is True
    # The daemon's fresh home holds no such history: a typed empty, not a guess.
    assert data.get("returned") == 0
    assert data.get("truncated") is False
