"""A cancelled authorization returns its unit; a consumed one never does.

``release_effect_reservations`` existed for this from the start and the gateway
never called it. The wallet's external-signer lanes open a ``wallet_transaction``
effect at the claim, cancel it while the operator's wallet is signing, then open
a fresh one at submit — so every such payment reserved TWO units and stranded one
in ``reserved`` forever (live reservations are never pruned). The same shape
appears anywhere logic authorizes and then declines to run.

The law proved here is one line: **cancel returns the unit if and only if no
attempt consumed it.** That keeps "reserve before authorization, consume before
execution, terminal exactly once" true across a cancel-and-reopen, without ever
refunding a spend.
"""
from __future__ import annotations

import contextlib

import pytest

from tests.effect_budget.conftest import *  # noqa: F403 — budget fixtures


def _open(effect_class: str = "network_fetch"):
    from core.effect_gateway import (
        DECISION_ALLOWED,
        EffectReceipt,
        current_effect_ledger,
    )

    ledger = current_effect_ledger()
    assert ledger is not None, "no effect ledger in scope"
    return ledger.open_effect(
        EffectReceipt(
            effect_class=effect_class,
            decision=DECISION_ALLOWED,
            reason="test",
            host="example.invalid",
            decided_by="tests.effect_budget",
            provider_id="test",
            keyed_or_keyless="keyless",
        )
    )


@contextlib.contextmanager
def effect_scope():
    """Open the turn's effect scope. Entered AFTER set_budget: the authority
    refuses a budget adjustment made inside an active scope, by design."""
    from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

    open_effect_receipt_scope({"session_id": "cancel-sess", "workspace_root": "/tmp"})
    try:
        yield
    finally:
        close_effect_receipt_scope()


def _rows(budget_class: str = "network_fetch"):
    from core import effect_budget as eb

    return [r for r in eb.reservation_rows() if r["budget_class"] == budget_class]


def test_cancel_before_any_attempt_returns_the_unit(set_budget) -> None:
    from core import effect_budget as eb

    set_budget(("network_fetch", eb.SCOPE_SESSION, 1))

    with effect_scope():
        first = _open()
        assert len(_rows()) == 1, _rows()
        first.cancel(reason="awaiting external signature; re-opened at submit")

        states = [r["state"] for r in _rows()]
        assert states == [eb.RESERVATION_RELEASED], states

        # The unit is back in the pool: the re-opened effect is affordable, which is
        # exactly the cancel-and-reopen shape. Before this law it was refused, or --
        # worse, on a larger budget -- silently charged twice.
        second = _open()
        assert second is not None
        live = [r for r in _rows() if r["state"] != eb.RESERVATION_RELEASED]
        assert len(live) == 1, _rows()


def test_cancel_after_an_attempt_began_never_refunds_the_spend(set_budget) -> None:
    """The other half: a consumed unit is spent. Cancel must not launder it back."""
    from core import effect_budget as eb

    set_budget(("network_fetch", eb.SCOPE_SESSION, 2))

    with effect_scope():
        effect = _open()
        effect.begin_attempt()
        consumed = [r for r in _rows() if r["state"] == eb.RESERVATION_CONSUMED]
        assert len(consumed) == 1, _rows()

        effect.cancel(reason="cancelled mid-flight")

        after = _rows()
        assert [r["state"] for r in after] == [eb.RESERVATION_CONSUMED], after
        assert not any(r["state"] == eb.RESERVATION_RELEASED for r in after), after


def test_a_cancel_and_reopen_pair_charges_exactly_one_unit(set_budget) -> None:
    """The wallet's external-signer shape, end to end, on a budget of one.

    Claim -> cancel while the wallet signs -> re-open at submit -> execute. That
    is ONE logical payment and must cost ONE unit. With cancel stranding its
    reservation, the re-open was refused on a budget of one and double-charged
    on any larger budget.
    """
    from core import effect_budget as eb

    set_budget(("network_fetch", eb.SCOPE_SESSION, 1))

    with effect_scope():
        claim = _open()
        claim.cancel(reason="awaiting external signature; re-opened at submit")

        submit = _open()
        submit.begin_attempt()
        submit.succeed(reason="broadcast")

        rows = _rows()
        consumed = [r for r in rows if r["state"] == eb.RESERVATION_CONSUMED]
        released = [r for r in rows if r["state"] == eb.RESERVATION_RELEASED]
        assert len(consumed) == 1, rows
        assert len(released) == 1, rows
        assert len(rows) == 2, rows


def test_sabotage_a_cancel_that_keeps_the_unit_strands_it(
    set_budget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neutralize the release and the stranded reservation comes straight back.

    A guard whose removal changes nothing is not load-bearing.
    """
    from core import effect_budget as eb

    monkeypatch.setattr(eb, "release_effect_reservations", lambda *a, **k: 0)
    set_budget(("network_fetch", eb.SCOPE_SESSION, 2))

    with effect_scope():
        claim = _open()
        claim.cancel(reason="awaiting external signature; re-opened at submit")

        stranded = [r for r in _rows() if r["state"] == eb.RESERVATION_RESERVED]
        assert len(stranded) == 1, (
            "with the release neutralized the cancelled authorization must strand "
            f"its unit; rows={_rows()}"
        )
