"""Gauntlet — category 4: tool-result honesty (deterministic).

The "tool-result honesty = 100%" target is almost entirely a property of the
SURFACED contract, not of model quality — so it is deterministic. These pin:

  - sell.quote is read-only: it returns a quote and says so, and never reaches a
    payment path;
  - pay.x402 is a retired money surface: its answer is a typed, receipt-backed
    refusal with a user-safe text, never a fake "paid" and never a leaked internal;
  - a malformed / unknown tool intent produces an honest failure status and never
    leaks an internal error string to the user;
  - respond.direct is a non-tool sentinel, not a fake tool execution;
  - the loop only falls back to research after a *parse* miss on a fresh-info
    class — a real tool that ran, or a real tool that was merely disabled, does
    not silently reroute.

What is NOT here (live lane): whether the model emits a *valid* tool_intent JSON
payload for a prompt, and tool-selection appropriateness on a live turn.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.execution.payment_tools import execute_payment_tool
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.tool_intent_executor import execute_tool_intent
from core.wallet.authority import LEGACY_RETIRED
from tests.conftest import FORBIDDEN_CHAT_LEAKS

pytestmark = [pytest.mark.gauntlet]

_CTX = {"surface": "openclaw", "platform": "openclaw"}


def _tracker():
    return HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))


# ---------------------------------------------------------------------------
# sell.quote is read-only and never pays
# ---------------------------------------------------------------------------

def test_sell_quote_is_read_only_and_states_no_payment():
    pay_calls: list = []
    ex = execute_payment_tool(
        "sell.quote",
        {"resource": "null://task/quote"},
        source_context={},
        dna_pay_and_unlock_fn=lambda *a, **k: pay_calls.append(1),
    )
    assert ex.ok is True
    assert ex.status == "quoted"
    assert "no payment was made" in ex.response_text.lower()
    assert pay_calls == []  # a quote never touches the payment path


def test_pay_x402_refusal_is_typed_receipt_backed_and_leak_free():
    from core.faults.recorder import fault_by_id

    pay_calls: list = []
    ex = execute_payment_tool(
        "pay.x402",
        {"resource": "https://compute.example.test/job", "allow_spend": True, "approve": True, "max_spend_usdc": 0.01},
        source_context={"vool_wallet": object(), "_owner_local": True},
        dna_pay_and_unlock_fn=lambda *a, **k: pay_calls.append(1),
        dna_get_quote_fn=lambda *a, **k: {"error": True, "message": "offline"},
    )
    assert ex.ok is False
    assert ex.status == LEGACY_RETIRED
    assert ex.mode == "tool_failed"
    assert ex.details["executed"] is False
    assert ex.details["observation"]["status"] == LEGACY_RETIRED
    fault_id = ex.details["fault"]["fault_id"]
    assert fault_id.startswith("fault-")
    assert fault_by_id(fault_id) is not None  # the refusal is on file, not just in the reply
    safe = (ex.user_safe_response_text or "").lower()
    assert safe and "paid" not in safe.split()
    for leak in FORBIDDEN_CHAT_LEAKS:
        assert leak not in safe
    assert pay_calls == []  # a retired surface never touches the payment path


# ---------------------------------------------------------------------------
# Malformed / unknown tool intents fail honestly, with no leaked internals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload,expected_status", [
    ({"arguments": {}}, "missing_intent"),
    ({"intent": "", "arguments": {}}, "missing_intent"),
    ({"intent": "   ", "arguments": {}}, "missing_intent"),
    ({"intent": "foo.bar", "arguments": {}}, "unsupported"),
    ({"intent": "nope.nothing"}, "unsupported"),
])
def test_bad_tool_intent_fails_honestly_without_leaks(payload, expected_status):
    ex = execute_tool_intent(
        payload, task_id="t", session_id="s", source_context=_CTX, hive_activity_tracker=_tracker()
    )
    assert ex.ok is False
    assert ex.status == expected_status
    safe = (ex.user_safe_response_text or "").lower()
    for leak in FORBIDDEN_CHAT_LEAKS:
        assert leak not in safe


def test_respond_direct_is_a_sentinel_not_a_fake_tool():
    ex = execute_tool_intent(
        {"intent": "respond.direct", "arguments": {"message": "hi"}},
        task_id="t", session_id="s", source_context=_CTX, hive_activity_tracker=_tracker(),
    )
    assert ex.handled is False
    assert ex.status == "direct_response"


# ---------------------------------------------------------------------------
# _should_fallback_after_tool_failure truth table
# ---------------------------------------------------------------------------

def _exec(ok, mode, status, tool_name=""):
    return SimpleNamespace(ok=ok, mode=mode, status=status, tool_name=tool_name)


@pytest.mark.parametrize("execution,executed_steps,task_class,expected", [
    # success never falls back
    (_exec(True, "tool_executed", "executed"), [], "research", False),
    # a preview / non-failed mode does not fall back
    (_exec(False, "tool_preview", "user_action_required"), [], "research", False),
    # a real tool step already ran -> no silent reroute
    (_exec(False, "tool_failed", "missing_intent"), [{"tool_name": "workspace.search_text"}], "research", False),
    # a real tool that was merely disabled is NOT a parse miss -> no fallback
    (_exec(False, "tool_failed", "disabled", "workspace.write_file"), [], "research", False),
    # a parse miss on a fresh-info class -> fall back to research
    (_exec(False, "tool_failed", "missing_intent", ""), [], "research", True),
    (_exec(False, "tool_failed", "invalid_payload", "unknown"), [], "system_design", True),
])
def test_should_fallback_after_tool_failure_truth_table(
    make_agent, execution, executed_steps, task_class, expected
):
    agent = make_agent()
    got = agent._should_fallback_after_tool_failure(
        execution=execution,
        effective_input="do the requested thing",
        classification={"task_class": task_class},
        interpretation=SimpleNamespace(),
        executed_steps=executed_steps,
    )
    assert got is expected


def test_malformed_tool_payload_falls_back_for_normal_conversation(make_agent):
    agent = make_agent()
    got = agent._should_fallback_after_tool_failure(
        execution=_exec(False, "tool_failed", "missing_intent", "unknown"),
        effective_input="For this chat, the fictional project signal is LANTERN-742. Please remember it.",
        classification={"task_class": "chat"},
        interpretation=SimpleNamespace(),
        executed_steps=[],
    )
    assert got is True


def test_malformed_tool_payload_remains_terminal_for_explicit_action(make_agent):
    agent = make_agent()
    got = agent._should_fallback_after_tool_failure(
        execution=_exec(False, "tool_failed", "missing_intent", "unknown"),
        effective_input="Create a release note file in the workspace.",
        classification={"task_class": "chat"},
        interpretation=SimpleNamespace(),
        executed_steps=[],
    )
    assert got is False
