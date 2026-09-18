"""OPERATOR AUTHORITY — who may change a budget, proven mechanically.

Skills, plugins and models run INSIDE effect scopes (turn ledgers, named
background scopes); the operator surface does not. That fact is the
authority check: no token is minted and no adjustment applies from inside a
scope, and a forged token has no power at all.
"""
from __future__ import annotations

import pytest

from core import effect_budget as eb
from core.effect_gateway import named_background_effect_scope, open_effect_receipt_scope
from tests.effect_budget.conftest import *  # noqa: F401,F403 — fixtures


def test_operator_can_set_and_remove_budgets(operator_token):
    applied = eb.apply_operator_adjustment(
        operator_token,
        [eb.BudgetAdjustment(eb.BUDGET_CLASS_PROVIDER_CALL, eb.SCOPE_SESSION, 5, note="ops")],
    )
    assert [r.rule_id for r in applied] == ["provider_call/session"]
    assert [r.limit for r in eb.active_budgets()] == [5]
    # removal is the same operator shape (unbudget, not zero)
    eb.apply_operator_adjustment(
        operator_token,
        [eb.BudgetAdjustment(eb.BUDGET_CLASS_PROVIDER_CALL, eb.SCOPE_SESSION, None)],
    )
    assert eb.active_budgets() == []


def test_no_token_no_adjustment():
    with pytest.raises(eb.EffectBudgetRefusedError) as refusal:
        eb.apply_operator_adjustment(
            None, [eb.BudgetAdjustment(eb.BUDGET_CLASS_COMMAND, eb.SCOPE_SESSION, 99)]
        )
    assert refusal.value.code == eb.REFUSAL_AUTHORITY
    assert eb.active_budgets() == [], "nothing was written"


def test_forged_token_is_powerless(operator_token):
    """The dataclass is constructible by anyone — a synthetic token with no
    durable grant record adjusts nothing, and the attempt is journaled."""
    forged = eb.OperatorBudgetToken(token_id="obt:forged-by-a-plugin", note="raised by model")
    with pytest.raises(eb.EffectBudgetRefusedError) as refusal:
        eb.apply_operator_adjustment(
            forged, [eb.BudgetAdjustment(eb.BUDGET_CLASS_COMMAND, eb.SCOPE_SESSION, 999)]
        )
    assert refusal.value.code == eb.REFUSAL_AUTHORITY
    assert "no durable grant record" in refusal.value.detail
    assert eb.active_budgets() == []
    refused_events = eb.budget_events("authority_refused")
    assert len(refused_events) == 1, "the refused attempt is itself a durable fact"


def test_model_inside_turn_cannot_mint_or_adjust(operator_token):
    """A model, skill or plugin executes inside a turn's effect scope — the
    exact position this test runs from. Neither minting nor adjusting works
    here: this is the mechanical 'cannot raise their own budgets' proof."""
    set_outside = eb.apply_operator_adjustment(
        operator_token, [eb.BudgetAdjustment(eb.BUDGET_CLASS_COMMAND, eb.SCOPE_SESSION, 1)]
    )
    assert set_outside, "the operator configured the budget from OUTSIDE the turn"
    open_effect_receipt_scope()
    try:
        with pytest.raises(eb.EffectBudgetRefusedError) as mint_refusal:
            eb.grant_operator_budget_authority("raised mid-turn by participant")
        assert mint_refusal.value.code == eb.REFUSAL_AUTHORITY
        with pytest.raises(eb.EffectBudgetRefusedError) as adjust_refusal:
            eb.apply_operator_adjustment(
                operator_token,
                [eb.BudgetAdjustment(eb.BUDGET_CLASS_COMMAND, eb.SCOPE_SESSION, 999)],
            )
        assert adjust_refusal.value.code == eb.REFUSAL_AUTHORITY
        assert "inside an active effect scope" in adjust_refusal.value.detail
    finally:
        from core.effect_gateway import close_effect_receipt_scope

        close_effect_receipt_scope()
    # and the budget was NOT widened from inside
    assert eb.active_budgets()[0].limit == 1
    # every refusal left a durable trace
    assert len(eb.budget_events("authority_refused")) == 2


def test_background_scope_cannot_mint_either():
    with named_background_effect_scope("relay.discord"):
        with pytest.raises(eb.EffectBudgetRefusedError) as refusal:
            eb.grant_operator_budget_authority("daemon self-raise")
        assert refusal.value.code == eb.REFUSAL_AUTHORITY


def test_adjustment_validates_class_scope_and_window(operator_token):
    with pytest.raises(eb.EffectBudgetRefusedError) as unknown_class:
        eb.apply_operator_adjustment(
            operator_token,
            [eb.BudgetAdjustment("secret_exfiltration", eb.SCOPE_SESSION, 10)],
        )
    assert unknown_class.value.code == eb.REFUSAL_UNKNOWN_CLASS
    with pytest.raises(eb.EffectBudgetRefusedError) as bad_scope:
        eb.apply_operator_adjustment(
            operator_token,
            [eb.BudgetAdjustment(eb.BUDGET_CLASS_COMMAND, "universe", 10)],
        )
    assert bad_scope.value.code == eb.REFUSAL_STATE
    with pytest.raises(eb.EffectBudgetRefusedError) as bad_window:
        eb.apply_operator_adjustment(
            operator_token,
            [eb.BudgetAdjustment(eb.BUDGET_CLASS_COMMAND, eb.SCOPE_WINDOW, 10, 0.0)],
        )
    assert bad_window.value.code == eb.REFUSAL_STATE
    with pytest.raises(eb.EffectBudgetRefusedError) as negative:
        eb.apply_operator_adjustment(
            operator_token,
            [eb.BudgetAdjustment(eb.BUDGET_CLASS_COMMAND, eb.SCOPE_SESSION, -1)],
        )
    assert negative.value.code == eb.REFUSAL_STATE
    assert eb.active_budgets() == []


def test_wallet_classes_are_declared_for_the_future_lane():
    """The wallet lane's vocabulary exists NOW — proposals and broadcasts are
    budget classes the integrating lane reserves under, not invents."""
    assert eb.BUDGET_CLASS_WALLET_PROPOSAL in eb.BUDGET_CLASSES
    assert eb.BUDGET_CLASS_WALLET_BROADCAST in eb.BUDGET_CLASSES
    assert eb.BUDGET_CLASS_FILE_WRITE in eb.BUDGET_CLASSES
    assert eb.BUDGET_CLASS_PUBLIC_WRITE in eb.BUDGET_CLASSES
    assert eb.BUDGET_CLASS_PROVIDER_CALL in eb.BUDGET_CLASSES


def test_every_adjustment_is_a_durable_attributed_event(operator_token):
    eb.apply_operator_adjustment(
        operator_token,
        [eb.BudgetAdjustment(eb.BUDGET_CLASS_NETWORK_FETCH, eb.SCOPE_TURN, 3, note="why")],
    )
    events = eb.budget_events("adjustment")
    assert len(events) == 1
    import json

    detail = json.loads(events[0]["detail_json"])
    assert detail["token_id"] == operator_token.token_id, "attribution by token id"
    assert detail["note"] == "why"
    grants = eb.budget_events("authority_grant")
    assert len(grants) == 1, "the grant itself was journaled"
