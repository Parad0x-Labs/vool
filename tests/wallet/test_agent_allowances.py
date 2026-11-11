"""Agent allowances: each rule decides the way its attack requires, on the real wallet store and custody."""
from __future__ import annotations

import time

import pytest

from core.wallet import allowances as alw
from core.wallet.security import WalletFault
from tests.wallet._rig import DESTINATION, DEVNET, OTHER_DESTINATION, wallet_env

PIN = "730461"
SERVICE = "https://api.example.test"
OTHER_SERVICE = "https://other.example.test"
NOW = 1_800_000_000.0


@pytest.fixture
def wallet(wallet_env):
    from core.wallet import custody

    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def _spec(wallet, **over):
    base = dict(
        agent_id="researcher", wallet_id=wallet.wallet_id, network=DEVNET, asset="SOL", per_payment_minor=1_000, window_seconds=alw.DAY, window_minor=3_000,
        total_minor=5_000, max_payments=10, min_interval_seconds=0, expires_at=NOW + 7 * alw.DAY,
        services=(alw.ServiceRule(SERVICE, 4_000, DESTINATION),),
    )
    base.update(over)
    return alw.AllowanceSpec(**base)


def _ask(wallet, *, agent="researcher", proposal="pay-1", amount=500, origin=SERVICE, pay_to=DESTINATION, digest="req-1", now=NOW, asset="SOL"):
    from core.wallet import limits
    from core.wallet.store import connection

    with connection() as conn:
        limits._begin_immediate(conn)
        return alw.authorize(conn, agent_id=agent, proposal_id=proposal, wallet_id=wallet.wallet_id, network=DEVNET, asset=asset, amount_minor=amount, origin=origin,
                             pay_to=pay_to, request_digest=digest, now=now)


def _paid_and_delivered(wallet, proposal, **kw):
    decision = _ask(wallet, proposal=proposal, **kw)
    assert decision.pay, decision
    assert alw.settle(proposal, amount_moved=True)
    alw.record_delivery(proposal, delivered=True)
    return decision


def _reason(excinfo) -> str:
    return str(excinfo.value.context.get("reason"))


# --- only the owner creates or widens -------------------------------------------------------------------------

def test_create_needs_the_wallet_credential_and_a_wrong_one_creates_nothing(wallet):
    with pytest.raises(WalletFault):
        alw.create_allowance(_spec(wallet), credential="", now=NOW)
    with pytest.raises(WalletFault):
        alw.create_allowance(_spec(wallet), credential="000000", now=NOW)
    assert alw.list_allowances() == []
    assert _ask(wallet).reason == alw.REASON_NO_ALLOWANCE


@pytest.mark.parametrize("over", [
    {"per_payment_minor": 0}, {"total_minor": -1}, {"max_payments": 0}, {"window_seconds": 60}, {"per_payment_minor": 4_000},
    {"window_minor": 6_000}, {"expires_at": NOW - 1}, {"expires_at": NOW + 91 * alw.DAY}, {"expires_at": float("inf")}, {"expires_at": float("nan")},
    {"services": ()}, {"services": (alw.ServiceRule("http://api.example.test", 100),)}, {"services": (alw.ServiceRule(SERVICE, 9_000),)},
    {"services": (alw.ServiceRule(SERVICE, 100, "not-an-address"),)}, {"per_payment_minor": 1.5}, {"per_payment_minor": True}, {"agent_id": "Bad Agent!"},
    {"total_minor": 2**63},
])
def test_an_unbounded_or_inconsistent_allowance_is_refused(wallet, over):
    with pytest.raises(WalletFault) as excinfo:
        alw.create_allowance(_spec(wallet, **over), credential=PIN, now=NOW)
    assert _reason(excinfo) == "invalid_allowance"
    assert alw.list_allowances() == []


def test_a_second_live_allowance_for_the_same_agent_is_refused(wallet):
    alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    with pytest.raises(WalletFault) as excinfo:
        alw.create_allowance(_spec(wallet, total_minor=5_000), credential=PIN, now=NOW)
    assert _reason(excinfo) == "allowance_exists"


def test_lowering_needs_no_credential_and_raising_does(wallet):
    allowance_id = alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    alw.change_allowance(allowance_id, _spec(wallet, per_payment_minor=400), now=NOW)
    assert _ask(wallet, amount=500).reason == alw.REASON_OVER_PER_PAYMENT
    for wider in ({"per_payment_minor": 900}, {"expires_at": NOW + 8 * alw.DAY}, {"max_payments": 11}, {"min_interval_seconds": 0, "total_minor": 5_001},
                  {"services": (alw.ServiceRule(SERVICE, 4_000), alw.ServiceRule(OTHER_SERVICE, 10))}, {"services": (alw.ServiceRule(SERVICE, 4_000, OTHER_DESTINATION),)}):
        with pytest.raises(WalletFault):
            alw.change_allowance(allowance_id, _spec(wallet, **{"per_payment_minor": 400, **wider}), now=NOW)
    assert alw.allowance_view(allowance_id, now=NOW)["per_payment_minor"] == 400
    alw.change_allowance(allowance_id, _spec(wallet, per_payment_minor=900), credential=PIN, now=NOW)
    assert _ask(wallet, amount=500).pay


def test_a_change_keeps_the_pinned_recipient(wallet):
    allowance_id = alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    alw.change_allowance(allowance_id, _spec(wallet, services=(alw.ServiceRule(SERVICE, 3_000),)), now=NOW)
    assert alw.allowance_view(allowance_id, now=NOW)["services"][0]["pay_to"] == DESTINATION
    assert _ask(wallet, pay_to=OTHER_DESTINATION).reason == alw.REASON_RECIPIENT_CHANGED


# --- the decision ---------------------------------------------------------------------------------------------

def test_isolated_another_agent_cannot_ride_this_agents_allowance(wallet):
    alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    assert _ask(wallet, agent="writer").reason == alw.REASON_NO_ALLOWANCE
    assert _ask(wallet, agent="").reason == alw.REASON_INVALID_REQUEST
    assert _ask(wallet, agent="researcher").pay


def test_the_server_does_not_choose_asset_service_or_recipient(wallet):
    alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    assert _ask(wallet, asset="USDC").reason == alw.REASON_NO_ALLOWANCE
    assert _ask(wallet, origin=OTHER_SERVICE).reason == alw.REASON_NEW_SERVICE
    assert _ask(wallet, origin="https://api.example.test.evil.test").reason == alw.REASON_NEW_SERVICE
    assert _ask(wallet, origin="http://api.example.test").reason == alw.REASON_INVALID_REQUEST
    assert _ask(wallet, pay_to=OTHER_DESTINATION).reason == alw.REASON_RECIPIENT_CHANGED
    assert alw.allowance_view(alw.list_allowances()[0]["allowance_id"], now=NOW)["total_used_minor"] == 0


def test_an_unpinned_service_asks_until_an_owner_payment_pins_it_and_a_pin_is_never_replaced(wallet):
    allowance_id = alw.create_allowance(_spec(wallet, services=(alw.ServiceRule(SERVICE, 4_000),)), credential=PIN, now=NOW)
    assert _ask(wallet).reason == alw.REASON_FIRST_PAYMENT
    assert alw.pin_service(allowance_id, OTHER_SERVICE, DESTINATION) is False  # not a listed service
    assert alw.pin_service(allowance_id, SERVICE, DESTINATION) is True
    assert alw.pin_service(allowance_id, SERVICE, OTHER_DESTINATION) is False
    assert _ask(wallet).pay
    assert _ask(wallet, proposal="pay-2", digest="req-2", pay_to=OTHER_DESTINATION).reason == alw.REASON_RECIPIENT_CHANGED


def test_per_payment_window_total_count_and_service_ceilings(wallet):
    alw.create_allowance(_spec(wallet, max_payments=6), credential=PIN, now=NOW)
    assert _ask(wallet, amount=1_001).reason == alw.REASON_OVER_PER_PAYMENT
    for index in range(3):
        _paid_and_delivered(wallet, f"pay-{index}", amount=1_000, digest=f"req-{index}", now=NOW + index)
    assert _ask(wallet, proposal="pay-w", amount=1, digest="req-w", now=NOW + 10).reason == alw.REASON_WINDOW_USED
    later = NOW + alw.DAY + 10  # the window rolled; the service total (4000) is what binds next
    _paid_and_delivered(wallet, "pay-3", amount=1_000, digest="req-3", now=later)
    assert _ask(wallet, proposal="pay-s", amount=1, digest="req-s", now=later + 1).reason == alw.REASON_SERVICE_TOTAL_USED


def test_total_and_count_bind_across_windows(wallet):
    allowance_id = alw.create_allowance(_spec(wallet, total_minor=3_500, window_minor=3_000, max_payments=4, services=(alw.ServiceRule(SERVICE, 3_500, DESTINATION),)),
                                        credential=PIN, now=NOW)
    for index in range(3):
        _paid_and_delivered(wallet, f"pay-{index}", amount=1_000, digest=f"req-{index}", now=NOW + index)
    later = NOW + alw.DAY + 10
    assert _ask(wallet, proposal="pay-t", amount=600, digest="req-t", now=later).reason == alw.REASON_TOTAL_USED
    _paid_and_delivered(wallet, "pay-3", amount=500, digest="req-3", now=later)
    assert _ask(wallet, proposal="pay-c", amount=0 + 1, digest="req-c", now=later + 1).reason == alw.REASON_COUNT_USED
    view = alw.allowance_view(allowance_id, now=later + 1)
    assert (view["total_used_minor"], view["payments"], view["window_used_minor"]) == (3_500, 4, 500)


def test_ask_above_threshold_asks_inside_every_other_limit(wallet):
    alw.create_allowance(_spec(wallet, ask_above_minor=300), credential=PIN, now=NOW)
    assert _ask(wallet, amount=301).reason == alw.REASON_OVER_ASK_THRESHOLD
    assert _ask(wallet, amount=300).pay


def test_expired_paused_and_revoked_pay_nothing(wallet):
    allowance_id = alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    assert _ask(wallet, now=NOW + 7 * alw.DAY).reason == alw.REASON_EXPIRED
    alw.pause_allowance(allowance_id)
    assert _ask(wallet).reason == alw.REASON_PAUSED
    with pytest.raises(WalletFault):
        alw.resume_allowance(allowance_id, credential="")
    assert _ask(wallet).reason == alw.REASON_PAUSED
    alw.resume_allowance(allowance_id, credential=PIN)
    assert _ask(wallet).pay
    alw.revoke_allowance(allowance_id)
    assert _ask(wallet, proposal="pay-2", digest="req-2").reason == alw.REASON_NO_ALLOWANCE
    with pytest.raises(WalletFault):
        alw.change_allowance(allowance_id, _spec(wallet), credential=PIN, now=NOW)


def test_the_panic_freeze_stops_every_allowance(wallet):
    from core.wallet import limits

    alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    limits.set_frozen(True)
    decision = _ask(wallet)
    assert (decision.decision, decision.reason) == (alw.DECISION_REFUSE, alw.REASON_FROZEN)
    limits.set_frozen(False)
    assert _ask(wallet).pay


def test_no_second_payment_for_the_same_request_until_the_first_delivered(wallet):
    alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    assert _ask(wallet, proposal="pay-1").pay
    assert _ask(wallet, proposal="pay-1").pay  # the same proposal again: its hold stands, counted once
    assert alw.list_allowances()[0]["total_used_minor"] == 500
    assert _ask(wallet, proposal="pay-2").reason == alw.REASON_REPEAT_UNCONFIRMED  # in flight
    alw.settle("pay-1", amount_moved=True)
    assert _ask(wallet, proposal="pay-2").reason == alw.REASON_REPEAT_UNCONFIRMED  # paid, delivery unknown
    alw.record_delivery("pay-1", delivered=True)
    assert _ask(wallet, proposal="pay-2").pay


def test_price_jump_on_the_same_request_asks(wallet):
    alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    _paid_and_delivered(wallet, "pay-1", amount=100)
    assert _ask(wallet, proposal="pay-2", amount=301).reason == alw.REASON_PRICE_JUMP
    assert _ask(wallet, proposal="pay-3", amount=300).pay


def test_minimum_gap_refuses_with_a_retry_time_and_does_not_ask(wallet):
    alw.create_allowance(_spec(wallet, min_interval_seconds=60), credential=PIN, now=NOW)
    _paid_and_delivered(wallet, "pay-1")
    decision = _ask(wallet, proposal="pay-2", digest="req-2", now=NOW + 20)
    assert (decision.decision, decision.reason, decision.retry_after_seconds) == (alw.DECISION_REFUSE, alw.REASON_TOO_SOON, 40.0)
    assert _ask(wallet, proposal="pay-2", digest="req-2", now=NOW + 60).pay


def test_three_anomalous_requests_in_a_row_pause_the_allowance_until_the_owner_resumes(wallet):
    allowance_id = alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    assert _ask(wallet, pay_to=OTHER_DESTINATION).paused == ""
    assert _ask(wallet, amount=5_000).paused == ""
    tripped = _ask(wallet, pay_to=OTHER_DESTINATION)
    assert tripped.paused == alw.PAUSE_BREAKER_REFUSALS
    assert _ask(wallet).reason == alw.REASON_PAUSED
    assert alw.allowance_view(allowance_id, now=NOW)["pause_reason"] == alw.PAUSE_BREAKER_REFUSALS
    alw.resume_allowance(allowance_id, credential=PIN)
    assert _ask(wallet).pay


def test_a_good_payment_clears_the_strikes_and_a_spent_budget_is_not_a_strike(wallet):
    alw.create_allowance(_spec(wallet, window_minor=1_000), credential=PIN, now=NOW)
    _ask(wallet, pay_to=OTHER_DESTINATION)
    _ask(wallet, pay_to=OTHER_DESTINATION)
    _paid_and_delivered(wallet, "pay-1", amount=1_000)
    _ask(wallet, proposal="p2", digest="r2", pay_to=OTHER_DESTINATION)
    _ask(wallet, proposal="p3", digest="r3", pay_to=OTHER_DESTINATION)
    for index in range(5):
        assert _ask(wallet, proposal=f"w-{index}", digest=f"w-{index}", amount=1).reason == alw.REASON_WINDOW_USED
    assert alw.list_allowances()[0]["state"] == alw.STATE_ACTIVE


def test_two_paid_requests_in_a_row_that_deliver_nothing_pause_the_allowance(wallet):
    alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    for index in range(2):
        assert _ask(wallet, proposal=f"pay-{index}", digest=f"req-{index}").pay
        alw.settle(f"pay-{index}", amount_moved=True)
    assert alw.record_delivery("pay-0", delivered=False) == ""
    assert alw.record_delivery("pay-0", delivered=True) == ""  # answered once: a later claim does not undo it
    assert alw.record_delivery("pay-1", delivered=False) == alw.PAUSE_BREAKER_UNDELIVERED
    assert _ask(wallet, proposal="pay-9", digest="req-9").reason == alw.REASON_PAUSED


def test_release_gives_the_hold_back_a_failed_payment_counts_nothing_and_unknown_stays_held(wallet):
    allowance_id = alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    for name in ("pay-a", "pay-b", "pay-c"):
        assert _ask(wallet, proposal=name, digest=name, amount=1_000).pay
    assert alw.release("pay-a") is True and alw.release("pay-a") is False
    assert alw.settle("pay-b", amount_moved=False) is True
    assert alw.settle("pay-a", amount_moved=True) is False  # a released hold never settles
    view = alw.allowance_view(allowance_id, now=NOW)
    assert view["total_used_minor"] == 1_000  # pay-c: nobody knows, so it is still held
    assert _ask(wallet, proposal="pay-a", digest="pay-a").reason == alw.REASON_REPEAT_UNCONFIRMED  # a released proposal is not revived


def test_a_decision_that_does_not_commit_holds_nothing(wallet):
    from core.wallet import limits
    from core.wallet.store import connection

    allowance_id = alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    with pytest.raises(RuntimeError), connection() as conn:
        limits._begin_immediate(conn)
        assert alw.authorize(conn, agent_id="researcher", proposal_id="pay-1", wallet_id=wallet.wallet_id, network=DEVNET, asset="SOL", amount_minor=500,
                             origin=SERVICE, pay_to=DESTINATION, request_digest="req-1", now=NOW).pay
        raise RuntimeError("the wallet's own hold failed")
    assert alw.allowance_view(allowance_id, now=NOW)["total_used_minor"] == 0
    assert alw.decisions() == []


def test_a_corrupt_allowance_row_pays_nothing_and_says_so(wallet):
    from core.wallet.store import connection

    allowance_id = alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    with connection() as conn:
        conn.execute("UPDATE wallet_agent_allowances SET total_minor = 'lots' WHERE allowance_id = ?", (allowance_id,))
    assert _ask(wallet).reason == alw.REASON_UNREADABLE
    assert alw.allowance_view(allowance_id) == {"allowance_id": allowance_id, "unreadable": True}
    with connection() as conn:
        conn.execute("UPDATE wallet_agent_allowances SET total_minor = 5000, state = 'open' WHERE allowance_id = ?", (allowance_id,))
    assert _ask(wallet).reason == alw.REASON_UNREADABLE
    with connection() as conn:
        conn.execute("UPDATE wallet_agent_allowances SET state = 'active' WHERE allowance_id = ?", (allowance_id,))
        conn.execute("UPDATE wallet_agent_allowance_services SET total_minor = -5 WHERE allowance_id = ?", (allowance_id,))
    assert _ask(wallet).reason == alw.REASON_UNREADABLE


@pytest.mark.parametrize("amount", [0, -5, 2**63, 1.5, True, None, "500"])
def test_an_amount_that_is_not_a_positive_whole_number_is_never_paid(wallet, amount):
    alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    assert _ask(wallet, amount=amount).reason == alw.REASON_INVALID_REQUEST


def test_every_decision_is_logged_with_agent_service_and_reason(wallet):
    alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    _ask(wallet, agent="writer")
    _ask(wallet, pay_to=OTHER_DESTINATION)
    _ask(wallet)
    log = alw.decisions()
    assert [(row["agent_id"], row["decision"], row["reason"]) for row in log] == [
        ("researcher", alw.DECISION_PAY, alw.REASON_WITHIN), ("researcher", alw.DECISION_ASK_OWNER, alw.REASON_RECIPIENT_CHANGED),
        ("writer", alw.DECISION_ASK_OWNER, alw.REASON_NO_ALLOWANCE)]
    assert log[0]["origin"] == SERVICE and log[0]["amount_minor"] == 500
    assert [row["agent_id"] for row in alw.decisions(agent_id="writer")] == ["writer"]


def test_budget_notices_fire_once_as_each_threshold_is_crossed(wallet):
    alw.create_allowance(_spec(wallet, window_minor=2_000, total_minor=2_000, services=(alw.ServiceRule(SERVICE, 2_000, DESTINATION),)), credential=PIN, now=NOW)
    assert _paid_and_delivered(wallet, "p1", amount=900, digest="r1").notices == ()
    assert _paid_and_delivered(wallet, "p2", amount=100, digest="r2").notices == ("window:50", "total:50")
    assert _paid_and_delivered(wallet, "p3", amount=100, digest="r3").notices == ()
    assert _paid_and_delivered(wallet, "p4", amount=900, digest="r4").notices == ("window:80", "window:100", "total:80", "total:100")


# --- attenuation ----------------------------------------------------------------------------------------------

def _slice(wallet, **over):
    base = dict(agent_id="helper", per_payment_minor=500, window_minor=1_000, total_minor=1_000, max_payments=3, services=(alw.ServiceRule(SERVICE, 1_000),))
    base.update(over)
    return _spec(wallet, **base)


def test_a_slice_is_never_wider_than_what_the_parent_has_left(wallet):
    parent = alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    _paid_and_delivered(wallet, "pay-1", amount=1_000)
    _paid_and_delivered(wallet, "pay-2", amount=1_000, digest="req-2")
    for wider in ({"per_payment_minor": 1_001, "window_minor": 1_001, "total_minor": 1_001}, {"window_minor": 1_001, "total_minor": 1_001}, {"total_minor": 3_001, "window_minor": 1_000},
                  {"max_payments": 9}, {"expires_at": NOW + 8 * alw.DAY}, {"services": (alw.ServiceRule(OTHER_SERVICE, 10),)},
                  {"services": (alw.ServiceRule(SERVICE, 1_000, OTHER_DESTINATION),)}, {"window_seconds": alw.HOUR}, {"agent_id": "researcher"}, {"asset": "USDC"}):
        with pytest.raises(WalletFault):
            alw.delegate_slice(parent, _slice(wallet, **wider), now=NOW)
    assert alw.delegate_slice(parent, _slice(wallet), now=NOW)
    with pytest.raises(WalletFault) as excinfo:
        alw.delegate_slice(alw.list_allowances(agent_id="helper")[0]["allowance_id"], _slice(wallet, agent_id="helper2", per_payment_minor=10), now=NOW)
    assert _reason(excinfo) == "parent_cannot_delegate"


def test_a_slice_spends_against_itself_and_its_parent_and_pays_only_the_parents_pinned_recipient(wallet):
    parent = alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    slice_id = alw.delegate_slice(parent, _slice(wallet), now=NOW)
    assert _ask(wallet, agent="helper", pay_to=OTHER_DESTINATION).reason == alw.REASON_RECIPIENT_CHANGED
    _paid_and_delivered(wallet, "h-1", agent="helper", amount=500, digest="h-1")
    _paid_and_delivered(wallet, "h-2", agent="helper", amount=500, digest="h-2")
    assert _ask(wallet, agent="helper", proposal="h-3", amount=1, digest="h-3").reason == alw.REASON_WINDOW_USED
    assert alw.allowance_view(slice_id, now=NOW)["total_used_minor"] == 1_000
    assert alw.allowance_view(parent, now=NOW)["window_used_minor"] == 1_000
    _paid_and_delivered(wallet, "p-1", amount=1_000, digest="p-1")
    _paid_and_delivered(wallet, "p-2", amount=1_000, digest="p-2")
    assert _ask(wallet, proposal="p-3", amount=1, digest="p-3").reason == alw.REASON_WINDOW_USED  # the helper's 1000 counted


def test_a_parent_that_spends_first_leaves_the_slice_less_and_a_paused_or_revoked_parent_stops_it(wallet):
    parent = alw.create_allowance(_spec(wallet), credential=PIN, now=NOW)
    alw.delegate_slice(parent, _slice(wallet), now=NOW)
    for index in range(3):
        _paid_and_delivered(wallet, f"p-{index}", amount=900, digest=f"p-{index}")
    assert _ask(wallet, agent="helper", amount=400, digest="h-1", proposal="h-1").reason == alw.REASON_WINDOW_USED
    assert _ask(wallet, agent="helper", amount=300, digest="h-1", proposal="h-1").pay
    alw.pause_allowance(parent)
    assert _ask(wallet, agent="helper", amount=1, digest="h-2", proposal="h-2").reason == alw.REASON_PAUSED
    alw.resume_allowance(parent, credential=PIN)
    alw.revoke_allowance(parent)
    assert _ask(wallet, agent="helper", amount=1, digest="h-3", proposal="h-3").reason == alw.REASON_NO_ALLOWANCE


def test_normalize_origin():
    assert alw.normalize_origin("HTTPS://API.Example.test:443/paid?x=1") == SERVICE
    assert alw.normalize_origin("https://api.example.test:8443/") == "https://api.example.test:8443"
    assert alw.normalize_origin("http://127.0.0.1:8080/x") == "http://127.0.0.1:8080"
    for bad in ("", "api.example.test", "http://api.example.test", "https://user:pw@api.example.test", "ftp://api.example.test", "https://", "https://a:b:c"):
        assert alw.normalize_origin(bad) == ""


def test_wall_clock_default_is_used_when_no_time_is_given(wallet):
    alw.create_allowance(_spec(wallet, expires_at=time.time() + alw.DAY), credential=PIN)
    from core.wallet import limits
    from core.wallet.store import connection

    with connection() as conn:
        limits._begin_immediate(conn)
        assert alw.authorize(conn, agent_id="researcher", proposal_id="pay-1", wallet_id=wallet.wallet_id, network=DEVNET, asset="SOL", amount_minor=500,
                             origin=SERVICE, pay_to=DESTINATION, request_digest="req-1").pay
