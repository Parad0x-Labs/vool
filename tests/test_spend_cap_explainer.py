"""The spend-cap explainer: VOOL answering "can you guarantee my $5 limit?" honestly.

The local model improvises on this question, and an improvised money guarantee is the worst
thing this product can say. So the answer is deterministic, it comes from the REAL policy and
ledger, and it refuses rather than guesses when it cannot read them.

These tests pin the honesty, not the prose: the answer must concede that the cap is a brake and
not a fence, must give the concrete reasons an exact cap is impossible, and must point at the
provider as the only hard ceiling.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import core.usage_meter as um
from core.agent_runtime.fast_command_surface import (
    _SPEND_CAP_DOC,
    maybe_handle_spend_cap_explainer_intent,
)
from core.usage_meter import COST_PAID_CLOUD, record_usage

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    from core import runtime_paths
    from storage.db import (
        active_default_db_path,
        configure_default_db_path,
        get_connection,
        reset_default_connection,
    )

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    previous_db_path = active_default_db_path()
    configure_default_db_path(tmp_path / "usage-meter.db")
    um._SCHEMA_READY_PATHS.clear()

    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS token_usage")
        conn.commit()
    finally:
        conn.close()
    yield
    reset_default_connection()
    configure_default_db_path(previous_db_path)
    runtime_paths.configure_runtime_home(None)


# --------------------------------------------------------------------------------------------
# What gets captured, and what must never be.
# --------------------------------------------------------------------------------------------
CAPTURED = [
    "how does the spend cap work",
    "can you guarantee my $5 limit?",
    "what happens if I go over",
    "what happens if i go over the cap?",
    "explain the spending limit",
    "is my daily cap a guarantee?",
    "can you promise you won't overspend?",
    "what does the cost ceiling actually do",
    "will you exceed my budget?",
    "what is the per-call limit",
    "how do the spend guardrails work?",
    "does the monthly cap actually stop you",
]

# Ordinary chat, mutations, and other people's money. Every one of these must reach the
# assistant untouched — a deterministic surface that hijacks normal conversation is a
# regression, not a feature.
NOT_CAPTURED = [
    "what is a budget",
    "there is no limit to what you can do",
    "let's go over the plan",
    "go over the notes with me",
    "the spend cap is fine",
    "budget",
    "cap",
    "how do I limit the number of files in a folder?",
    "what's my grocery budget limit this month?",
    # Mutations belong to `cloud cap N` / the settings surface, never to the explainer.
    "cloud cap 25",
    "set the spend cap to $10",
    "raise my daily cap",
    "change the daily limit to 50",
    # Somebody else's ceiling is not VOOL's.
    "what happens if I go over my credit card limit",
]

# The most dangerous confusion class: other kinds of "limit" that live in the same product.
# Answering "what is the rate limit?" with a lecture about money would be a wrong answer
# delivered with full deterministic confidence — worse than falling through to the model.
OTHER_LIMITS = [
    "what is the rate limit?",
    "am I hitting a rate limit?",
    "what is the token limit for this model?",
    "what is the context limit?",
    "is there a limit on file size?",
    "what is the character limit?",
]


@pytest.mark.parametrize("prompt", CAPTURED)
def test_cap_questions_are_answered_deterministically(prompt):
    assert maybe_handle_spend_cap_explainer_intent(prompt, owner_local=True) is not None


@pytest.mark.parametrize("prompt", NOT_CAPTURED)
def test_ordinary_chat_and_mutations_fall_through(prompt):
    """For BOTH callers: the owner gate must never be what decides whether chat is hijacked."""
    assert maybe_handle_spend_cap_explainer_intent(prompt, owner_local=True) is None
    assert maybe_handle_spend_cap_explainer_intent(prompt, owner_local=False) is None


@pytest.mark.parametrize("prompt", OTHER_LIMITS)
def test_non_money_limits_are_not_answered_with_a_spend_lecture(prompt):
    assert maybe_handle_spend_cap_explainer_intent(prompt, owner_local=True) is None


def test_over_long_message_falls_through():
    assert maybe_handle_spend_cap_explainer_intent("spend cap? " + "x" * 300, owner_local=True) is None


# --------------------------------------------------------------------------------------------
# The honesty itself.
# --------------------------------------------------------------------------------------------
def test_answer_refuses_to_promise_an_exact_cap():
    out = maybe_handle_spend_cap_explainer_intent("can you guarantee my $5 limit?", owner_local=True)
    lowered = out.lower()
    # It concedes the shape of the promise.
    assert "brake, not a fence" in lowered
    assert "cannot promise" in lowered
    assert "several in-flight calls" in lowered
    # It must NOT claim a guarantee.
    assert "i guarantee" not in lowered
    assert "guaranteed" not in lowered
    assert "never go over" not in lowered


def test_answer_gives_the_concrete_reasons_not_a_hedge():
    out = maybe_handle_spend_cap_explainer_intent("how does the spend cap work", owner_local=True).lower()
    assert "not knowable before it runs" in out          # output length is unknown up front
    assert "estimate of the provider's bill" in out      # token counts x published price
    assert "price table can be out of date" in out       # prices drift
    assert "in flight cannot be recalled" in out         # no recall


def test_answer_points_at_the_provider_as_the_only_hard_ceiling():
    out = maybe_handle_spend_cap_explainer_intent("what happens if I go over", owner_local=True).lower()
    assert "set it at the provider" in out
    assert "check its billing terms" in out
    assert "alert is not a cap" in out


def test_answer_describes_atomic_caps_and_unknown_billing_truthfully():
    out = maybe_handle_spend_cap_explainer_intent("how does the spend cap work", owner_local=True).lower()
    assert "checks are atomic" in out
    assert "unknown cost is not zero" in out
    assert "stays held until reconciliation" in out
    assert "not a spending limit" in out
    assert "measured not to bind" not in out


def test_answer_distinguishes_byok_from_wallet_provider_payments():
    out = maybe_handle_spend_cap_explainer_intent("how does the spend cap work", owner_local=True).lower()
    assert "your provider using your key" in out
    assert "usepod wallet payments" in out
    assert "these usd ceilings do not govern that lane" in out


def test_answer_links_the_written_doc_and_the_doc_exists():
    out = maybe_handle_spend_cap_explainer_intent("how does the spend cap work", owner_local=True)
    assert _SPEND_CAP_DOC in out
    assert (REPO_ROOT / _SPEND_CAP_DOC).is_file()


# --------------------------------------------------------------------------------------------
# Real state, not invented numbers.
# --------------------------------------------------------------------------------------------
def test_numbers_come_from_the_real_policy_and_ledger():
    """Change the configured cap and the ledger; the answer must move with them."""
    from dataclasses import replace

    from core import cloud_escalation_policy as cep

    cep.save_policy(replace(cep.load_policy(), mode=cep.MODE_AUTO, daily_cap=7))
    assert record_usage(provider_id="anthropic", model_id="claude-x", cost_class=COST_PAID_CLOUD,
                        prompt_tokens=100, output_tokens=100, usd_actual=0.42)

    out = maybe_handle_spend_cap_explainer_intent("how does the spend cap work", owner_local=True)
    assert "no call-count limit" in out  # the legacy count no longer authorizes spend
    assert "mode: auto" in out
    assert "$0.4200" in out                 # the ledger's real figure, not a placeholder
    assert "$5.00/day" in out               # the real USD ceiling from spend_limits()


def test_a_provider_priced_response_is_not_labelled_an_estimate():
    assert record_usage(provider_id="openrouter", model_id="or/x", cost_class=COST_PAID_CLOUD,
                        prompt_tokens=10, output_tokens=10, usd_actual=0.05)
    out = maybe_handle_spend_cap_explainer_intent("how does the spend cap work", owner_local=True)
    assert "provider-reported cost" in out
    assert "~$" not in out


def test_a_response_with_no_provider_cost_is_labelled_an_estimate():
    """Settlement is blind for every provider that returns token counts only. The number VOOL
    shows for those is its own estimate and must say so rather than pose as the bill."""
    assert record_usage(provider_id="anthropic", model_id="claude-x", cost_class=COST_PAID_CLOUD,
                        prompt_tokens=1000, output_tokens=1000, usd_actual=None)
    out = maybe_handle_spend_cap_explainer_intent("how does the spend cap work", owner_local=True)
    assert "~$" in out
    assert "my own estimate from token counts" in out


def test_unreadable_state_refuses_instead_of_guessing(monkeypatch):
    """A spend answer that invents its own numbers is worse than one that admits it could not
    look. The general explanation still stands; only the numbers are withheld."""
    import core.agent_runtime.fast_command_surface as fcs
    from core import cloud_escalation_policy as cep

    def _boom(*_a, **_k):
        raise OSError("data directory unreadable")

    monkeypatch.setattr(cep, "load_policy", _boom)
    assert fcs._spend_cap_state_lines() is None

    out = maybe_handle_spend_cap_explainer_intent("how does the spend cap work", owner_local=True)
    assert "could not read this machine's cap settings" in out
    assert "On this machine right now" not in out
    assert "brake, not a fence" in out.lower()


# --------------------------------------------------------------------------------------------
# Owner gating: the explanation is public, this machine's numbers are not.
# --------------------------------------------------------------------------------------------
def test_remote_caller_gets_the_truth_but_not_the_machine_state():
    from dataclasses import replace

    from core import cloud_escalation_policy as cep

    cep.save_policy(replace(cep.load_policy(), mode=cep.MODE_AUTO, daily_cap=7))
    assert record_usage(provider_id="anthropic", model_id="claude-x", cost_class=COST_PAID_CLOUD,
                        prompt_tokens=100, output_tokens=100, usd_actual=0.42)

    out = maybe_handle_spend_cap_explainer_intent("can you guarantee my $5 limit?", owner_local=False)
    # The honesty is not a secret — how the product works is published.
    assert "brake, not a fence" in out.lower()
    assert "set it at the provider" in out
    # This machine's configuration and spend are.
    assert "owner-local" in out
    assert "On this machine right now" not in out
    assert "0.42" not in out
    assert "of 7 (UTC day)" not in out


def test_answer_shows_unpriced_liability_separately_from_reported_spend():
    from core.model_spend_ledger import SpendLimits, reserve_spend, settle_spend

    reserve_spend(model_call_id="missing-usage", task_id="t", subtask_id="s", model_id="m",
                  maximum_usd=0.25, limits=SpendLimits(0.25, 1, 5, 25))
    settle_spend("missing-usage", actual_usd=None)
    out = maybe_handle_spend_cap_explainer_intent("how does the spend cap work", owner_local=True)
    assert "unresolved budget held today: $0.2500 (not a confirmed charge)" in out
