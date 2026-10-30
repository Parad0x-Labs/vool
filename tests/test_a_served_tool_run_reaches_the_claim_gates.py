"""A tool the model ran on a served turn is evidence for BOTH claim gates.

Review FU-C01 (2026-10-30, qwen3:8b, tool cards on): "what's Priya's phone number in my
contacts?" ran `contacts.search`, and the published reply was "I haven't looked in your contacts.
Ask me to look them up and I'll check." The served runtime opened the turn's ledger on its own
copy of the transport's context (`core.web.api.runtime._run_agent_locked`), the tool run was
recorded in that ledger, and the transport then finalized with the context it held, which carried
no ledger stamp: `gate_action_claims` and `gate_execution_claims` read no execution and corrected
a reply a real tool run backed.

Driven here over a real socket: `apps.vool_api_server` in its own process and VOOL_HOME, a
contact seeded in that home's store, and an Ollama-dialect stub that calls `contacts__search`
and then answers. Everything between the request and the reply is production code. The guard
both ways: the same claims with no tool run are still corrected.

Each test boots a daemon (~15-30 s).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests._blackbox_served_rig import REPO_ROOT, SEED_MANIFEST, ScriptedProvider, ServedDaemon, run_in_home

MODEL = "qwen3-stub:2b"
ASK = "what's Priya's phone number in my contacts?"
NUMBER = "+44 7700 900123"
# A read claim the action gate knows ("I checked your contacts") and a run block the execution
# gate knows ("Tool: contacts.search"): with the tool run both stay, without it both go.
REPLY = f"I checked your contacts: Priya's phone number is {NUMBER}.\n\nTool: contacts.search\nquery: Priya"
NOT_LOOKED = "I haven't looked in your contacts"
SEARCH_CALL = {"id": "call-1", "type": "function", "function": {"name": "contacts__search", "arguments": {"query": "Priya"}}}

_SEED = SEED_MANIFEST + '''
from tests._authorship_certification import certify_for_authorship
certify_for_authorship(manifest({model!r}))
from core.contacts import store
store.create_contact(display_name="Priya Shah", actor="owner", endpoints=[{{"kind": "phone", "value": "{number}"}}])
'''


def _reply_text(payload: dict[str, Any]) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    return str(message.get("content") or payload.get("response") or payload.get("content") or "")


def _committed_text(payload: dict[str, Any]) -> str:
    """The reply the transport committed: `vool_response_commit` on both lanes (the streamed lane's
    last frame carries it; its text arrived in the earlier frames)."""
    commit = payload.get("vool_response_commit") if isinstance(payload.get("vool_response_commit"), dict) else {}
    return str(commit.get("canonical_content") or "") or _reply_text(payload)


def _executed(home: Path) -> list[str]:
    """Tools this daemon's runtime recorded as executed (one home per test, one turn per home)."""
    conn = sqlite3.connect(str(home / "data" / "vool_web0_v2.db"))
    try:
        rows = conn.execute(
            "SELECT details_json FROM runtime_session_events WHERE event_type = 'tool_executed' ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()
    return [str(json.loads(row[0] or "{}").get("tool_name") or "") for row in rows]


def _served(tmp_path: Path, *, calls_the_tool: bool):
    """A daemon whose model calls `contacts__search` on its first tool round (or never) and
    otherwise says REPLY; the ambiguity preflight is told the question is not ambiguous."""

    from core.entity_ambiguity import AMBIGUITY_SYSTEM_PROMPT

    tool_rounds: list[int] = []

    def reply_fn(body: dict[str, Any]) -> Any:
        prompt = " ".join(str(m.get("content") or "") for m in body.get("messages") or [] if isinstance(m, dict))
        if AMBIGUITY_SYSTEM_PROMPT in prompt:
            return json.dumps({"ambiguous": False, "referents": [], "clarification": ""})
        if body.get("tools"):
            tool_rounds.append(1)
            if calls_the_tool and len(tool_rounds) == 1:
                return SEARCH_CALL
        return REPLY

    home = tmp_path / "home"
    provider = ScriptedProvider({MODEL: REPLY}, reply_fn=reply_fn)
    daemon = ServedDaemon(
        home,
        env_extra={
            # Every default local endpoint points at the stub, so no lane reaches a real model.
            "OLLAMA_HOST": provider.base_url,
            "VOOL_OLLAMA_URL": provider.base_url,
            "VOOL_OLLAMA_CHAT_URL": f"{provider.base_url}/api/chat",
            "HOME": str(tmp_path / "owner-home"),
        },
    )
    return home, provider, daemon


@pytest.fixture
def served_turn(tmp_path: Path):
    started: list[tuple[ScriptedProvider, ServedDaemon]] = []

    def start(*, calls_the_tool: bool) -> tuple[Path, ScriptedProvider, ServedDaemon]:
        home, provider, daemon = _served(tmp_path / ("tool" if calls_the_tool else "no-tool"), calls_the_tool=calls_the_tool)
        provider.__enter__()
        started.append((provider, daemon))
        try:
            daemon.start(timeout=240)
        except Exception as exc:  # pragma: no cover - environment, not the gates
            pytest.skip(f"served daemon could not boot here: {exc}")
        run_in_home(home, _SEED.format(root=REPO_ROOT, base_url=provider.base_url, registered=[MODEL], model=MODEL, number=NUMBER))
        provider.reset()
        return home, provider, daemon

    yield start
    for provider, daemon in started:
        daemon.stop()
        provider.__exit__(None, None, None)


@pytest.mark.parametrize("lane", ["buffered", "streamed"])
def test_a_contacts_lookup_that_ran_keeps_its_answer(served_turn, lane: str) -> None:
    home, provider, daemon = served_turn(calls_the_tool=True)
    session = f"served-c01-{lane}"
    chat = daemon.chat if lane == "buffered" else daemon.chat_stream
    text = _committed_text(chat(ASK, session_id=session, model=MODEL, mode="auto"))

    assert _executed(home) == ["contacts.search"], (text, provider.calls[-4:])
    assert NOT_LOOKED not in text, text
    assert "Removed from this reply" not in text, text
    assert f"Priya's phone number is {NUMBER}" in text, text
    assert "Tool: contacts.search" in text, text


@pytest.mark.parametrize("lane", ["buffered", "streamed"])
def test_a_contacts_claim_with_no_tool_run_is_still_corrected(served_turn, lane: str) -> None:
    home, provider, daemon = served_turn(calls_the_tool=False)
    session = f"served-c01-none-{lane}"
    chat = daemon.chat if lane == "buffered" else daemon.chat_stream
    text = _committed_text(chat(ASK, session_id=session, model=MODEL, mode="auto"))

    assert _executed(home) == [], text
    assert any(call["tools"] for call in provider.calls), "the model was never offered a tool"
    assert "I checked your contacts" not in text, text
    assert NOT_LOOKED in text, text
