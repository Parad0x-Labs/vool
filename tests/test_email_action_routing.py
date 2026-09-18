"""Routing law for email-account action demands: the tools-less lane must release them.

Covers the `_email_account_action_demand` arm added to core.execution_requirements (mirroring
the wallet-action law) and the extended typed email rules in core.tool_demand_signals:

  * ORIGINAL wording — "Check unread emails from the orchard supplier, open the delivery
    thread and draft a reply choosing Wednesday at 10:00. Do not send it." — with the email
    lane enabled is tools_required, so no tools-less chat lane may claim the turn.
  * NOVEL wording — different vocabulary, same class: a date-window search request and a
    summarize-my-inbox request.
  * NEGATIVE / PRESERVATION controls — with the email policy OFF (the shipped default) the
    arm does not fire (nothing changes for existing users); a QUESTION about email as a
    topic ("how do I stop emails going to spam?") is not an account action and keeps its
    ordinary classification; unrelated sentences are untouched.
"""
from __future__ import annotations

import pytest

from core.execution_requirements import requirements_for
from core.tool_demand_signals import resolve_demand_signals


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Hermetic policy state: an empty VOOL_HOME and a restored policy cache.

    Without this the fixture's `load()` caches whatever ambient policy the previous
    test file left in the process, and later suites (the gauntlet's live catalog
    snapshot) then see a different tool surface -- measured as demo.plan/web.fetch
    disappearing purely from test ordering."""
    from core import policy_engine, runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    previous_cache = getattr(policy_engine, "_POLICY_CACHE", None)
    policy_engine._POLICY_CACHE = None  # force a reload from the empty home
    yield
    policy_engine._POLICY_CACHE = previous_cache
    runtime_paths.configure_runtime_home(None)


@pytest.fixture
def email_lane_enabled(monkeypatch):
    from core import policy_engine

    base = dict(policy_engine.load())
    base["email"] = {**(base.get("email") or {}), "read_enabled": True, "send_enabled": True}
    monkeypatch.setattr(policy_engine, "_POLICY_CACHE", base)
    yield  # monkeypatch restores the original cache object on teardown


ORIGINAL = (
    "Check unread emails from the orchard supplier, open the delivery thread and draft a "
    "reply choosing Wednesday at 10:00. Do not send it."
)
NOVEL_SEARCH = (
    "Look at my inbox for anything the repair workshop sent since September 5th and open "
    "that estimate thread so I can answer it."
)
NOVEL_SUMMARY = "Summarize my unread mail from this week and show me the orchard thread."


def test_original_fixture_wording_requires_tools(email_lane_enabled) -> None:
    req = requirements_for(ORIGINAL, task_class="chat_conversation")
    assert req.tools_required, req.reason_codes
    assert req.forbids_toolless_lane()
    assert "email" in req.allowed_toolsets
    assert "email_account_action_request" in req.reason_codes


def test_novel_wordings_require_tools(email_lane_enabled) -> None:
    for text in (NOVEL_SEARCH, NOVEL_SUMMARY):
        req = requirements_for(text, task_class="chat_conversation")
        assert req.tools_required, (text, req.reason_codes)
        assert "email" in req.allowed_toolsets


def test_email_policy_off_leaves_the_turn_unchanged() -> None:
    # Shipped default: both email toggles off -> the arm must not fire; the sentence keeps
    # whatever classification it had before this change (no forced tool requirement).
    req = requirements_for(ORIGINAL, task_class="chat_conversation")
    assert "email_account_action_request" not in req.reason_codes


def test_email_topic_question_is_not_an_account_action(email_lane_enabled) -> None:
    # A question about email as a TOPIC names no account operation; it must not be pulled
    # into the tool lane by the email nouns alone.
    for text in (
        "How do I stop emails from going to spam?",
        "What is the difference between an email header and an envelope recipient?",
    ):
        signals = resolve_demand_signals(text)
        assert "email" not in signals.required_families, text
        req = requirements_for(text, task_class="chat_conversation")
        assert "email_account_action_request" not in req.reason_codes, text


def test_email_text_authoring_is_not_an_account_action(email_lane_enabled) -> None:
    # "Write me a cold-outreach email template" authors TEXT: no account anchor. The typed
    # recognizer may still SEAT the email family (pre-existing base seating); the routing
    # ARM must not fire, so the turn keeps its ordinary tool-less classification.
    text = "Write me a sample cold outreach email template for freelance clients."
    req = requirements_for(text, task_class="chat_conversation")
    assert "email_account_action_request" not in req.reason_codes
    assert not req.tools_required
    # ...while the same verbs WITH an account anchor stay an action:
    anchored = requirements_for(
        "Check my unread emails and draft a reply to the delivery thread.", task_class="chat_conversation"
    )
    assert anchored.tools_required and "email_account_action_request" in anchored.reason_codes


def test_demand_rules_fire_on_account_operations(email_lane_enabled) -> None:
    for text in (ORIGINAL, NOVEL_SEARCH, NOVEL_SUMMARY,
                 "Reply to the delivery email and accept Wednesday",
                 "Forward that invoice email to accounting"):
        assert "email" in resolve_demand_signals(text).required_families, text


def test_unrelated_sentences_are_untouched(email_lane_enabled) -> None:
    for text in (
        "Tell me about stoicism in two sentences.",
        "fix the failing tests in the parser module",
        "what is the capital of France",
    ):
        req = requirements_for(text, task_class="chat_conversation")
        assert "email_account_action_request" not in req.reason_codes
