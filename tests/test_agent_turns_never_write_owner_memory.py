"""An agent's words never become the owner's memory.

Measured on the live agent-team run (2026-10-07): each agent of a VOOL team runs its own chat session through
/api/chat, and its brief arrives as that session's user message. The post-turn memory extractor read the brief below
as the owner speaking and stored "For code review tasks, user wants responses as a one-line result beginning with a
RESULT JSON status line, no file changes." in the owner's preferences block. A turn whose request declares
``turn_author: "agent"`` no longer reaches the extractor that writes the owner's profile blocks (the agent's own
session history is unchanged). The default (no declaration) stays the owner, and a declaration can only remove
capture, never grant it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import core.web.api.runtime as runtime_api
from core.request_trust import TURN_AUTHOR_KEY, turn_author_from_request, turn_is_owner_authored

# The agent brief and the agent's reply, verbatim from the run's ledger (the extraction call's conversation).
AGENT_BRIEF = (
    'Read stock.py only, find the single most important bug, and report it as `stock.py:line — what is wrong — the '
    'fix` in one line, beginning your reply with one line `RESULT: {"status": "done|partial|needs_decision|failed", '
    '"summary": "at most 3 sentences", "changed": [paths], "question": "only if needs_decision"}`, changing no file, '
    'as the agent "Review stock.py bug · normal" in a VOOL team.'
)
AGENT_REPLY = (
    "**Status: Unverified** — `stock.py`. Opened 1 of 3 source files in this pass.\n\n"
    "**NOT PROVEN** — no current correctness bug was confirmed in this pass."
)


@pytest.fixture()
def owner_chat(tmp_path, monkeypatch):
    from core.context_namespace import ensure_chat_namespace
    from core.runtime_paths import configure_runtime_home
    from storage.migrations import run_migrations

    home = tmp_path / "home"; home.mkdir()
    monkeypatch.setenv("VOOL_HOME", str(home))
    configure_runtime_home(home)
    run_migrations()
    chat = "openclaw:" + "a1" * 10
    ensure_chat_namespace(chat, grant_current_receipts=False)
    triggered: list[list[dict]] = []
    monkeypatch.setattr("core.fact_extractor.FactExtractor.trigger_async", lambda self, messages: triggered.append(messages))
    yield SimpleNamespace(home=str(home), chat=chat, triggered=triggered)
    configure_runtime_home(None)


def _context(chat, home, **extra):
    return {"chat_id": chat, "session_id": chat, "runtime_session_id": chat, "surface": "openclaw", "platform": "api",
            "_owner_local": True, "runtime_home": home,
            "conversation_history": [{"role": "user", "content": AGENT_BRIEF}], **extra}


def _schedule(owner_chat, context):
    runtime_api.schedule_memory_extraction(
        SimpleNamespace(runtime_home=owner_chat.home), user_text=AGENT_BRIEF, assistant_output=AGENT_REPLY,
        session_id=owner_chat.chat, source_context=context,
    )


def test_an_agent_turn_never_reaches_the_owner_profile_extractor(owner_chat):
    _schedule(owner_chat, _context(owner_chat.chat, owner_chat.home, **{TURN_AUTHOR_KEY: "agent"}))
    assert owner_chat.triggered == []


def test_the_same_turn_from_the_owner_still_reaches_it(owner_chat):
    _schedule(owner_chat, _context(owner_chat.chat, owner_chat.home))
    assert len(owner_chat.triggered) == 1
    assert owner_chat.triggered[0][-2] == {"role": "user", "content": AGENT_BRIEF}


@pytest.mark.parametrize("body,inbound,expected", [
    ({}, {}, "owner"),
    ({TURN_AUTHOR_KEY: "agent"}, {}, "agent"),
    ({}, {TURN_AUTHOR_KEY: "agent"}, "agent"),
    ({TURN_AUTHOR_KEY: "owner"}, {TURN_AUTHOR_KEY: "agent"}, "agent"),
    ({TURN_AUTHOR_KEY: "Owner"}, {}, "owner"),
], ids=["absent", "body-agent", "context-agent", "owner-cannot-override-agent", "case-folded-owner"])
def test_a_declared_author_can_only_remove_owner_authorship(body, inbound, expected):
    assert turn_author_from_request(body, inbound) == expected
    assert turn_is_owner_authored({TURN_AUTHOR_KEY: expected}) is (expected == "owner")
