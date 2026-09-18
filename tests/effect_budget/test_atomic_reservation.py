"""THE LAST UNIT GOES TO EXACTLY ONE EFFECT — the atomic reservation core.

Every proof here runs REAL threads against the REAL store (per-test sqlite
file, one connection per thread — the deployment shape): the check and the
write must be one transaction or two effects both take the last unit.
"""
from __future__ import annotations

import threading

import pytest

from core import effect_budget as eb
from tests.effect_budget.conftest import *  # noqa: F401,F403 — fixtures


def _reserve_all(budget_class: str, identity: dict, count: int, barrier: threading.Barrier):
    """`count` threads race for the same units through a barrier (maximum
    collision). Returns (success_ids, refusals)."""
    successes: list[str] = []
    refusals: list[eb.EffectBudgetRefusedError] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        barrier.wait()
        try:
            receipt = eb.reserve_effect_units(budget_class, **identity)
            with lock:
                successes.append(receipt.reservation_id)
        except eb.EffectBudgetRefusedError as refusal:
            with lock:
                refusals.append(refusal)

    threads = [
        threading.Thread(target=worker, args=(i,), name=f"reserve-{i}")
        for i in range(count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return successes, refusals


def test_two_calls_cannot_both_consume_the_last_unit(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 1))
    successes, refusals = _reserve_all(
        "network_fetch",
        {"turn_id": "", "session_id": "s-last", "project_key": "p"},
        count=2,
        barrier=threading.Barrier(2),
    )
    assert len(successes) == 1, "the last unit must go to exactly one effect"
    assert len(refusals) == 1
    assert refusals[0].code == eb.REFUSAL_BUDGET_EXCEEDED
    assert refusals[0].rule == "network_fetch/session"
    statuses = eb.budget_status("network_fetch", session_id="s-last")
    assert statuses[0].used == 1 and statuses[0].remaining == 0


def test_many_threads_limited_budget_never_overspends(set_budget):
    limit = 5
    set_budget(("command", eb.SCOPE_SESSION, limit))
    successes, refusals = _reserve_all(
        "command",
        {"turn_id": "", "session_id": "s-race", "project_key": "p"},
        count=24,
        barrier=threading.Barrier(24),
    )
    assert len(successes) == limit
    assert len(refusals) == 24 - limit
    assert all(r.code == eb.REFUSAL_BUDGET_EXCEEDED for r in refusals)
    statuses = eb.budget_status("command", session_id="s-race")
    assert statuses[0].used == limit, "reserved units must equal the limit exactly"
    rows = eb.reservation_rows()
    assert len(rows) == limit and all(r["state"] == "reserved" for r in rows)


def test_multi_rule_reservation_is_all_or_nothing(set_budget):
    """One effect under session AND project rules: when project is exhausted,
    the session rule must NOT be incremented either — the reservation either
    touches every rule or none."""
    set_budget(
        ("network_fetch", eb.SCOPE_SESSION, 10),
        ("network_fetch", eb.SCOPE_PROJECT, 2),
    )
    identity = {"turn_id": "", "session_id": "s-all", "project_key": "proj"}
    for _ in range(2):
        eb.reserve_effect_units("network_fetch", **identity)
    with pytest.raises(eb.EffectBudgetRefusedError) as refusal:
        eb.reserve_effect_units("network_fetch", **identity)
    assert refusal.value.rule == "network_fetch/project"  # the refusing rule is named
    session_status = next(
        s for s in eb.budget_status("network_fetch", **identity) if s.rule.scope == "session"
    )
    project_status = next(
        s for s in eb.budget_status("network_fetch", **identity) if s.rule.scope == "project"
    )
    assert project_status.used == 2 and project_status.remaining == 0
    assert session_status.used == 2 and session_status.remaining == 8, (
        "the third effect reserved NOTHING — not even under the rule that had room"
    )
    assert len(eb.reservation_rows()) == 2, "no partial reservation row may exist"


def test_concurrent_mixed_rules_never_break_the_pair(set_budget):
    """Threads reserving effects bound by two counters: after the dust
    settles, both counters agree with the reservation rows exactly."""
    set_budget(
        ("command", eb.SCOPE_SESSION, 6),
        ("command", eb.SCOPE_PROJECT, 8),
    )
    successes, refusals = _reserve_all(
        "command",
        {"turn_id": "", "session_id": "s-mixed", "project_key": "proj-mixed"},
        count=10,
        barrier=threading.Barrier(10),
    )
    assert len(successes) == 6
    session_used = next(
        s for s in eb.budget_status("command", session_id="s-mixed", project_key="proj-mixed")
        if s.rule.scope == "session"
    ).used
    project_used = next(
        s for s in eb.budget_status("command", session_id="s-mixed", project_key="proj-mixed")
        if s.rule.scope == "project"
    ).used
    assert session_used == 6 == project_used
    assert len(eb.reservation_rows()) == 6


def test_unbudgeted_class_passes_without_touching_the_store(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 0))  # only network_fetch is budgeted
    receipt = eb.reserve_effect_units(
        "command", turn_id="t", session_id="s", project_key="p"
    )
    assert receipt.unbudgeted is True
    assert receipt.rules == ()
    assert eb.reservation_rows() == []
    kinds = [event["event_kind"] for event in eb.budget_events()]
    assert "reserved" not in kinds and "refused" not in kinds, (
        "the unbudgeted reserve added no budget event of its own "
        f"(only the operator's config events may exist): {kinds}"
    )


def test_zero_limit_forbids_the_class_entirely(set_budget):
    set_budget(("public_write", eb.SCOPE_PROJECT, 0))
    with pytest.raises(eb.EffectBudgetRefusedError) as refusal:
        eb.reserve_effect_units("public_write", turn_id="t", session_id="s", project_key="p")
    assert refusal.value.code == eb.REFUSAL_BUDGET_EXCEEDED
    assert "0 of 0" in refusal.value.detail


def test_unknown_class_is_a_typed_refusal():
    with pytest.raises(eb.EffectBudgetRefusedError) as refusal:
        eb.reserve_effect_units("not_a_class", turn_id="t")
    assert refusal.value.code == eb.REFUSAL_UNKNOWN_CLASS


def test_units_must_be_positive(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 3))
    with pytest.raises(eb.EffectBudgetRefusedError) as refusal:
        eb.reserve_effect_units("network_fetch", turn_id="t", session_id="s", units=0)
    assert refusal.value.code == eb.REFUSAL_STATE


def test_weighted_units_reserve_and_refuse_correctly(set_budget):
    """The provider-spend shape: one effect may cost more than one unit."""
    set_budget(("provider_call", eb.SCOPE_SESSION, 10))
    identity = {"turn_id": "", "session_id": "s-spend", "project_key": "p"}
    eb.reserve_effect_units("provider_call", units=7, **identity)
    with pytest.raises(eb.EffectBudgetRefusedError):
        eb.reserve_effect_units("provider_call", units=4, **identity)  # 7+4 > 10
    ok = eb.reserve_effect_units("provider_call", units=3, **identity)  # 7+3 <= 10
    assert ok.units == 3
    status = eb.budget_status("provider_call", session_id="s-spend")[0]
    assert status.used == 10 and status.remaining == 0
