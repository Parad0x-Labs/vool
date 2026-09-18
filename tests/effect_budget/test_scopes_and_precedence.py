"""SCOPES AND PRECEDENCE — turn, session, project, window; intersection law.

Precedence is not override: every configured rule is a ceiling and an effect
must satisfy ALL of them. The tightest budget always wins because nothing
can pass a rule it exceeds, whichever order they are checked in.
"""
from __future__ import annotations

from core import effect_budget as eb
from tests.effect_budget.conftest import *  # noqa: F401,F403 — fixtures


def _res(budget_class: str, **identity) -> None:
    eb.reserve_effect_units(budget_class, **identity)


def test_turn_budget_is_per_turn_and_scopes_do_not_leak(set_budget):
    set_budget(("network_fetch", eb.SCOPE_TURN, 1))
    # turn 1 spends its single unit
    _res("network_fetch", turn_id="turn-1", session_id="sess", project_key="proj")
    # turn 2 under the SAME session and project has its OWN unit
    _res("network_fetch", turn_id="turn-2", session_id="sess", project_key="proj")
    # but turn 1's unit is gone
    try:
        _res("network_fetch", turn_id="turn-1", session_id="sess", project_key="proj")
        raise AssertionError("turn 1's budget must be exhausted")
    except eb.EffectBudgetRefusedError as refusal:
        assert refusal.rule == "network_fetch/turn"


def test_session_budget_spans_turns(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 2))
    _res("network_fetch", turn_id="t1", session_id="sess", project_key="proj")
    _res("network_fetch", turn_id="t2", session_id="sess", project_key="proj")
    try:
        _res("network_fetch", turn_id="t3", session_id="sess", project_key="proj")
        raise AssertionError("session budget must be exhausted across turns")
    except eb.EffectBudgetRefusedError as refusal:
        assert refusal.rule == "network_fetch/session"


def test_project_budget_spans_sessions(set_budget):
    set_budget(("command", eb.SCOPE_PROJECT, 1))
    _res("command", turn_id="t1", session_id="sess-A", project_key="proj")
    try:
        _res("command", turn_id="t2", session_id="sess-B", project_key="proj")
        raise AssertionError("project budget must bind across sessions")
    except eb.EffectBudgetRefusedError as refusal:
        assert refusal.rule == "network_fetch/project".replace("network_fetch", "command")
    # a different project is untouched — scope isolation in the other direction
    _res("command", turn_id="t3", session_id="sess-A", project_key="other-proj")


def test_all_rules_apply_intersection_not_override(set_budget):
    """A wide session budget does NOT widen a tight turn budget, and the
    refusal names the tightest (most specific) rule."""
    set_budget(
        ("network_fetch", eb.SCOPE_TURN, 1),
        ("network_fetch", eb.SCOPE_SESSION, 100),
        ("network_fetch", eb.SCOPE_PROJECT, 100),
    )
    _res("network_fetch", turn_id="t1", session_id="s", project_key="p")
    try:
        _res("network_fetch", turn_id="t1", session_id="s", project_key="p")
        raise AssertionError("the turn rule must refuse the second effect")
    except eb.EffectBudgetRefusedError as refusal:
        assert refusal.rule == "network_fetch/turn", (
            "the most specific refusing rule is named first"
        )


def test_identity_absent_rule_does_not_bind_and_says_so(set_budget):
    """A session rule does not bind an effect with no session identity — but
    the turn rule beside it still does. Honest non-binding, not silent skip."""
    set_budget(
        ("network_fetch", eb.SCOPE_SESSION, 1),
        ("network_fetch", eb.SCOPE_TURN, 1),
    )
    _res("network_fetch", turn_id="t1", session_id="", project_key="p")  # no session
    _res("network_fetch", turn_id="t2", session_id="", project_key="p")  # own turn unit
    try:
        _res("network_fetch", turn_id="t2", session_id="", project_key="p")
        raise AssertionError("turn rule still binds without a session")
    except eb.EffectBudgetRefusedError:
        pass
    statuses = eb.budget_status("network_fetch", turn_id="t1")
    session_status = next(s for s in statuses if s.rule.scope == "session")
    assert session_status.applies is False and session_status.remaining == 1


def test_window_budget_is_rolling_and_expires(set_budget, monkeypatch):
    """A time-window budget counts units inside the window; a unit whose
    reservation is older than the window stops counting."""
    set_budget(("network_fetch", eb.SCOPE_WINDOW, 1, 3600.0))
    _res("network_fetch", turn_id="t1", session_id="s", project_key="p")
    try:
        _res("network_fetch", turn_id="t2", session_id="s", project_key="p")
        raise AssertionError("window budget must be full")
    except eb.EffectBudgetRefusedError:
        pass
    # two hours pass: the window has rolled, the unit no longer counts
    import datetime as dt

    real_now = eb._utcnow_epoch

    def _shifted_now() -> float:
        return real_now() + 7200.0

    monkeypatch.setattr(eb, "_utcnow_epoch", _shifted_now)
    _res("network_fetch", turn_id="t3", session_id="s", project_key="p")
    status = eb.budget_status("network_fetch", turn_id="t3")[0]
    assert status.used == 1, "only the in-window unit counts"


def test_window_binds_across_identities(set_budget):
    """The window dimension is the identity: it binds effects from different
    turns/sessions/projects alike — the rate-shape it exists for."""
    set_budget(("command", eb.SCOPE_WINDOW, 2, 600.0))
    _res("command", turn_id="t1", session_id="sA", project_key="pA")
    _res("command", turn_id="t2", session_id="sB", project_key="pB")
    try:
        _res("command", turn_id="t3", session_id="sC", project_key="pC")
        raise AssertionError("the window is global across identities")
    except eb.EffectBudgetRefusedError as refusal:
        assert refusal.rule == "command/window:w600"


def test_preview_is_advisory_and_reserves_nothing(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 2))
    identity = {"turn_id": "", "session_id": "s-prev", "project_key": "p"}
    preview = eb.preview_effect("network_fetch", **identity)
    assert preview.allowed is True
    statuses = eb.budget_status("network_fetch", session_id="s-prev")
    assert statuses[0].used == 0, "preview reserved nothing"
    assert eb.reservation_rows() == []
    _res("network_fetch", **identity)
    _res("network_fetch", **identity)
    preview = eb.preview_effect("network_fetch", **identity)
    assert preview.allowed is False
    assert preview.code == eb.REFUSAL_BUDGET_EXCEEDED
    assert preview.rule == "network_fetch/session"
    assert "2 of 2" in preview.detail


def test_remaining_budget_status_is_exact(set_budget):
    set_budget(
        ("network_fetch", eb.SCOPE_SESSION, 5),
        ("network_fetch", eb.SCOPE_TURN, 2),
    )
    identity = {"turn_id": "t1", "session_id": "s1", "project_key": "p1"}
    first = eb.reserve_effect_units("network_fetch", **identity)
    eb.consume_reservation(first.reservation_id)
    eb.reserve_effect_units("network_fetch", **identity)  # still reserved (not consumed)
    by_scope = {s.rule.scope: s for s in eb.budget_status("network_fetch", **identity)}
    assert by_scope["session"].used == 2 and by_scope["session"].remaining == 3
    assert by_scope["turn"].used == 2 and by_scope["turn"].remaining == 0
