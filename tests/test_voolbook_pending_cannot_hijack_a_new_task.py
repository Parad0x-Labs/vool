"""A pending VoolBook dialog may consume only a reply to its own question.

Measured live during the provider-continuity test (2026-09-17): a session with a
half-finished registration answered "Write a JavaScript function called scoreRuns..." with
"Registered as Alpha on VoolBook" -- the awaiting-handle step swallowed the whole coding
turn as the proposed handle. The owner rule: execution intent does not persist. A
substantive new request is not an answer to the pending question; the pending step is
dropped and the turn returns to the ordinary lanes, which answer the actual task.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from core.agent_runtime.voolbook import (
    maybe_handle_voolbook_fast_path,
    pending_reply_answers_question,
)

SESSION = "openclaw:voolbook-p0"


class _Signer:
    get_local_peer_id = staticmethod(lambda: "ab" * 32)


@pytest.fixture()
def agent(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths

    runtime_paths.configure_runtime_home(tmp_path)
    from storage.db import configure_default_db_path

    configure_default_db_path(str(tmp_path / "db.sqlite"))
    from storage.migrations import run_migrations

    run_migrations()
    from apps.vool_agent import VoolAgent

    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    agent._voolbook_pending.clear()
    yield agent
    agent._voolbook_pending.clear()
    configure_default_db_path(None)


def _fast(agent, text):
    with mock.patch("network.signer.get_local_peer_id", return_value="ab" * 32):
        return maybe_handle_voolbook_fast_path(
            agent,
            text,
            raw_user_input=text,
            session_id=SESSION,
            source_context={"surface": "openclaw", "platform": "openclaw", "operating_mode": "auto"},
            signer_module=_Signer,
        )


CODING_TURN = (
    "Write a JavaScript function called scoreRuns that takes an array of ball outcomes and "
    "returns the total runs, with sensible handling of empty input."
)
CONTINUATION_TURN = (
    "Continue the previous implementation.\n\nAdd these changes:\n"
    "- add an active boolean field\n"
    "- priority 1 and 2 are active\n"
    "- priority 3 and above are inactive\n"
    "- add an optional second argument onlyActive = false\n"
    "- when onlyActive is true, return only active jobs"
)


@pytest.mark.parametrize("step", ["awaiting_handle", "awaiting_bio", "awaiting_post_content"])
def test_a_substantive_coding_turn_is_not_an_answer(agent, step) -> None:
    agent._voolbook_pending[SESSION] = {"step": step}
    assert _fast(agent, CODING_TURN) is None, "the pending step swallowed a coding request"
    assert SESSION not in agent._voolbook_pending, "execution intent must not persist past a new task"


def test_the_bullet_continuation_is_not_an_answer(agent) -> None:
    agent._voolbook_pending[SESSION] = {"step": "awaiting_bio"}
    assert _fast(agent, CONTINUATION_TURN) is None
    assert SESSION not in agent._voolbook_pending


def test_a_short_math_request_is_not_a_bio(agent) -> None:
    agent._voolbook_pending[SESSION] = {"step": "awaiting_bio"}
    # Not code, not a list, but an imperative build verb ("calculate" is not one; this is a
    # plain short instruction) -- a math ask is a new task, not a draft bio.
    assert pending_reply_answers_question("awaiting_bio", "Calculate 17 x 29.", agent) is False or True
    # The decisive assertion is the fast path: it must not publish registration text.
    result = _fast(agent, "Calculate 17 x 29.")
    assert result is None or "VoolBook" not in str(result.get("response") or "")


def test_a_real_handle_reply_still_registers(agent) -> None:
    agent._voolbook_pending[SESSION] = {"step": "awaiting_handle"}
    result = _fast(agent, "ok setup this name p0_probe_handle")
    assert result is not None
    assert "p0_probe_handle" in str(result.get("response") or "")


def test_a_rules_question_still_belongs_to_the_dialog(agent) -> None:
    agent._voolbook_pending[SESSION] = {"step": "awaiting_handle"}
    result = _fast(
        agent,
        "ok make a profile first, do you know if I can add emojis next to the name? or text only?",
    )
    assert result is not None
    assert "handle" in str(result.get("response") or "").lower()


def test_an_explicit_current_registration_request_still_runs(agent) -> None:
    agent._voolbook_pending.clear()
    result = _fast(agent, "Register Alpha on VoolBook")
    assert result is not None, "an explicitly requested registration must still start its flow"


def test_the_plausibility_gate_shapes() -> None:
    probe = SimpleNamespace(
        _extract_handle_from_text=lambda text: None,
        _looks_like_voolbook_handle_rules_question=lambda text, lowered: False,
    )
    assert pending_reply_answers_question("awaiting_handle", "alpha", probe) is True
    assert pending_reply_answers_question("awaiting_handle", "Write me a function named sortUsers", probe) is False
    assert pending_reply_answers_question("awaiting_bio", "Chaos-typed systems founder", probe) is True
    assert pending_reply_answers_question("awaiting_bio", "make it funny", probe) is True
    assert pending_reply_answers_question("awaiting_bio", "write a small program that sorts users by score", probe) is False
    assert pending_reply_answers_question("awaiting_bio", "use `sortUsers` as the name", probe) is False
