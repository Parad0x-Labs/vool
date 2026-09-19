"""THE ONE REAL GATE — budgets enforced where effects are authorized.

`EffectLedger.open_effect` is the convergence point every effect class flows
through; the budget reserve happens THERE (not in prompt prose, not in a
lane's private count), consumption happens at `begin_attempt`, and the
scope's close rolls back authorized-but-never-executed effects.
"""
from __future__ import annotations

import pytest

from core import effect_budget as eb
from core.effect_gateway import (
    DECISION_ALLOWED,
    DECISION_DENIED,
    EFFECT_NETWORK_FETCH,
    LIFECYCLE_AUTHORIZED,
    LIFECYCLE_SUCCEEDED,
    EffectReceipt,
    close_effect_receipt_scope,
    current_effect_ledger,
    named_background_effect_scope,
    open_effect_receipt_scope,
)
from tests.effect_budget.conftest import *  # noqa: F403 — fixtures


def _authorized_receipt(effect_class: str = EFFECT_NETWORK_FETCH) -> EffectReceipt:
    return EffectReceipt(
        effect_class=effect_class,
        decision=DECISION_ALLOWED,
        lifecycle=LIFECYCLE_AUTHORIZED,
        reason="test",
    )


def _turn_context(turn_id: str = "turn-1", session_id: str = "sess-1"):
    from core.turn_contract import TURN_REQUEST_KEY

    class _Request:
        pass

    request = _Request()
    request.turn_id = turn_id
    request.request_id = f"req-{turn_id}"
    request.session_id = session_id
    return {
        TURN_REQUEST_KEY: request,
        "turn_id": turn_id,
        "request_id": f"req-{turn_id}",
        "session_id": session_id,
        "workspace_root": "/tmp/project-x",
    }


def test_authorized_effect_reserves_at_the_gate(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 3))
    ledger = open_effect_receipt_scope(_turn_context())
    try:
        lifecycle = ledger.open_effect(_authorized_receipt())
        lifecycle.begin_attempt()
        lifecycle.succeed()
        rows = eb.reservation_rows()
        assert len(rows) == 1
        assert rows[0]["state"] == eb.RESERVATION_CONSUMED
        assert rows[0]["session_id"] == "sess-1"
        assert rows[0]["project_key"] == "/tmp/project-x"
        assert rows[0]["effect_id"] == lifecycle.effect_id
    finally:
        close_effect_receipt_scope()


def test_exhausted_budget_denies_at_the_gate_with_typed_code(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 1))
    ledger = open_effect_receipt_scope(_turn_context())
    try:
        first = ledger.open_effect(_authorized_receipt())
        first.begin_attempt()
        first.succeed()
        from core.effect_budget import EffectBudgetRefusedError

        with pytest.raises(EffectBudgetRefusedError) as refusal:
            ledger.open_effect(_authorized_receipt())
        assert refusal.value.code == eb.REFUSAL_BUDGET_EXCEEDED
        assert refusal.value.rule == "network_fetch/session"
        # the denial is a FIRST-CLASS FACT on the turn's own account
        denials = [
            entry
            for entry in ledger.entries()
            if entry.get("decision") == DECISION_DENIED
            and entry.get("decided_by") == "core.effect_budget"
        ]
        assert len(denials) == 1
        assert denials[0]["reason"].startswith("EFFECT_BUDGET_EXCEEDED")
        assert "refused" in eb.budget_events("refused")[0]["event_kind"]
    finally:
        close_effect_receipt_scope()


def test_denied_effect_never_touches_the_budget(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 1))
    ledger = open_effect_receipt_scope(_turn_context())
    try:
        ledger.open_effect(
            EffectReceipt(
                effect_class=EFFECT_NETWORK_FETCH,
                decision=DECISION_DENIED,
                lifecycle="denied",
                reason="mode matrix refused",
            )
        )
        assert eb.reservation_rows() == [], "a denial reserves nothing"
    finally:
        close_effect_receipt_scope()


def test_scope_close_rolls_back_authorized_never_executed(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 2))
    ledger = open_effect_receipt_scope(_turn_context())
    executed = ledger.open_effect(_authorized_receipt())
    executed.begin_attempt()
    executed.succeed()
    never_ran = ledger.open_effect(_authorized_receipt())  # authorized, no attempt
    close_effect_receipt_scope()
    states = {row["effect_id"]: row["state"] for row in eb.reservation_rows()}
    assert states[executed.effect_id] == eb.RESERVATION_CONSUMED
    assert states[never_ran.effect_id] == eb.RESERVATION_RELEASED
    assert eb.budget_status("network_fetch", session_id="sess-1")[0].used == 1


def test_retry_does_not_reserve_a_second_unit(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 1))
    ledger = open_effect_receipt_scope(_turn_context())
    try:
        first = ledger.open_effect(_authorized_receipt())
        first.begin_attempt()
        first.fail(reason="timeout")
        # the retry continues the SAME logical effect — no second unit
        retry = ledger.open_effect(_authorized_receipt(), retry_of=first.effect_id)
        assert retry.effect_id == first.effect_id
        retry.begin_attempt()
        retry.succeed()
        assert len(eb.reservation_rows()) == 1
        assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_CONSUMED
        assert eb.budget_status("network_fetch", session_id="sess-1")[0].used == 1
    finally:
        close_effect_receipt_scope()


def test_begin_attempt_fails_closed_without_a_reservation(set_budget, monkeypatch):
    """Sabotage shape: the reserve is skipped (as if never wired) and the
    attempt begins anyway — the gate refuses at consumption time."""
    set_budget(("network_fetch", eb.SCOPE_SESSION, 5))
    ledger = open_effect_receipt_scope(_turn_context())
    try:
        lifecycle = ledger.open_effect(_authorized_receipt())
        eb.release_reservation(
            eb.reservation_rows()[0]["reservation_id"], reason="simulated skipped reserve"
        )
        from core.effect_budget import EffectBudgetRefusedError

        with pytest.raises(EffectBudgetRefusedError) as refusal:
            lifecycle.begin_attempt()
        assert refusal.value.code == eb.REFUSAL_STATE
        assert "re-reserve" in refusal.value.detail or "released" in refusal.value.detail
    finally:
        close_effect_receipt_scope()


def test_network_fetch_door_surfaces_the_typed_refusal(set_budget):
    """End-to-end through the real transport door: the budget refusal is the
    door's typed refusal, and the turn's ledger holds the denial receipt."""
    from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote

    set_budget(("network_fetch", eb.SCOPE_TURN, 1))
    ledger = open_effect_receipt_scope(_turn_context(turn_id="door-turn"))
    try:
        import urllib.request

        first = open_remote(urllib.request.Request("data:text/plain,one"), timeout=5)
        first.close()
        from core.effect_budget import EffectBudgetRefusedError

        with pytest.raises((RemoteFetchRefusedError, EffectBudgetRefusedError)) as refusal:
            open_remote(urllib.request.Request("data:text/plain,two"), timeout=5)
        message = str(refusal.value)
        assert "EFFECT_BUDGET_EXCEEDED" in message, (
            f"the typed code must surface through the door: {message}"
        )
        assert any(
            entry.get("decided_by") == "core.effect_budget" for entry in ledger.entries()
        )
    finally:
        close_effect_receipt_scope()


def test_named_background_scope_reserves_and_consumes(set_budget):
    """Background work budgets under the window/project dimensions (it has no
    turn/session identity — those rules honestly do not bind it)."""
    set_budget(("network_fetch", eb.SCOPE_PROJECT, 2))
    with named_background_effect_scope("watch.poll"):
        ledger = current_effect_ledger()
        lifecycle = ledger.open_effect(_authorized_receipt())
        lifecycle.begin_attempt()
        lifecycle.succeed()
        rows = eb.reservation_rows()
        assert rows[0]["project_key"] == "default"
        assert rows[0]["turn_id"] == "" and rows[0]["session_id"] == ""
    assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_CONSUMED


def test_unbudgeted_turn_works_unchanged():
    """No budgets configured: the gate adds nothing — base behavior, zero
    rows, zero events (the honest nothing of law 3)."""
    ledger = open_effect_receipt_scope(_turn_context())
    try:
        lifecycle = ledger.open_effect(_authorized_receipt())
        lifecycle.begin_attempt()
        lifecycle.succeed()
        assert eb.reservation_rows() == []
        assert eb.budget_events() == []
        assert lifecycle.outcome()["lifecycle"] == LIFECYCLE_SUCCEEDED
    finally:
        close_effect_receipt_scope()


def test_preview_and_status_reach_the_gate_identity(set_budget):
    set_budget(("network_fetch", eb.SCOPE_TURN, 1))
    ledger = open_effect_receipt_scope(_turn_context("preview-turn"))
    try:
        identity = ledger._budget_identity()
        assert identity["turn_id"] == "preview-turn"
        assert identity["session_id"] == "sess-1"
        assert identity["project_key"] == "/tmp/project-x"
        preview = eb.preview_effect("network_fetch", **identity)
        assert preview.allowed is True and preview.per_rule[0].remaining == 1
        ledger.open_effect(_authorized_receipt())
        assert eb.preview_effect("network_fetch", **identity).allowed is False
    finally:
        close_effect_receipt_scope()
