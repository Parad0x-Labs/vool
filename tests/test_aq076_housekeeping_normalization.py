"""Housekeeping only declines when normalization GAINS a search the original bytes lack.

Review finding 5: the lane returned None whenever the accepted request's original bytes did not
parse, even when normalization had merely stripped a fence and the message was plain housekeeping
-- the turn then fell to a model that has no housekeeping tool. The lane's front door is driven
here directly with a real TurnRequest in the source context.
"""
from __future__ import annotations

import pytest

from apps.vool_agent import VoolAgent
from core import runtime_paths
from core.agent_runtime.housekeeping_lane import maybe_handle_housekeeping_request
from core.turn_contract import TURN_REQUEST_KEY, TurnRequest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


def _context(raw_bytes: str) -> dict:
    request = TurnRequest(
        request_id="req-aq076-1",
        turn_id="turn-aq076-1",
        session_id="openclaw:4234567890abcdef8801",
        user_text=raw_bytes,
    )
    return {"surface": "api", "_owner_local": True, TURN_REQUEST_KEY: request}


def _agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="aq076-housekeeping")


def test_a_fenced_housekeeping_command_is_not_run():
    agent = _agent()
    handled = maybe_handle_housekeeping_request(
        agent,
        raw_user_input="```\npin this chat\n```",
        effective_input="pin this chat",
        session_id="openclaw:4234567890abcdef8801",
        source_context=_context("```\npin this chat\n```"),
    )
    assert handled is None, "a command that only appears once a fence is stripped is pasted text, not a request"


def test_taken_back_clause_housekeeping_is_owned_by_the_lane():
    agent = _agent()
    raw = "rename this chat to Weeklies, actually no, pin it"
    handled = maybe_handle_housekeeping_request(
        agent,
        raw_user_input=raw,
        effective_input="pin it",
        session_id="openclaw:4234567890abcdef8801",
        source_context=_context(raw),
    )
    assert handled is not None, "plain housekeeping the normalizer rescued is still this lane's turn"


def test_a_search_that_only_normalization_adds_is_still_refused():
    agent = _agent()
    raw = "```\nfind my chats about the Andersen audit\n```"
    handled = maybe_handle_housekeeping_request(
        agent,
        raw_user_input=raw,
        effective_input="find my chats about the Andersen audit",
        session_id="openclaw:4234567890abcdef8801",
        source_context=_context(raw),
    )
    assert handled is None, "fenced text must not gain search authority through normalization"


def test_a_search_the_original_bytes_carried_is_handled():
    agent = _agent()
    raw = "find my chats about the Andersen audit"
    handled = maybe_handle_housekeeping_request(
        agent,
        raw_user_input=raw,
        effective_input=raw,
        session_id="openclaw:4234567890abcdef8801",
        source_context=_context(raw),
    )
    assert handled is not None, "an ordinary search in the original bytes runs in the lane"
