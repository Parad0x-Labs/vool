"""R2 — PER-TURN EFFECT TRUTH: one turn, one bounded receipt ledger.

WHAT THIS FILE PINS
-------------------
The effect-receipt channel was process-global mutable state
(`core.effect_gateway._EFFECT_RECEIPTS`, a module-level list). Everything below
follows from that one fact and each test names the consequence it proves:

* two turns running at the same time appended to the SAME list, so each read the
  other's effects and either could have reported the other's network calls;
* either turn's scope exit set the shared list back to `None`, so a turn that
  ended first drained a turn still running;
* a nested scope's `finally` closed the channel its parent was still writing to;
* the receipts a scope did collect were returned by `close_effect_receipt_scope`
  and then dropped on the floor by the only production caller, so nothing
  downstream could read what the turn had actually done;
* `TurnResult` — the turn's terminal typed truth — carried an empty receipt slot
  on every turn, including turns that were denied at the network door;
* the turn's policy was rebuilt at every effect door, so two doors in one turn
  could decide under two different policies, and a mode change made mid-turn
  retroactively changed decisions inside the turn that was already running;
* the network consult was wrapped in `except Exception -> ALLOWED`, so any
  failure to construct or consult the policy let the effect through.

The receipt ledger is per-turn, bounded, and reports truncation; the turn's
policy is frozen once and consumed by the network, command and filesystem
gates; and a policy failure denies with a receipt instead of allowing.
"""
from __future__ import annotations

import threading

import pytest

from core.effect_gateway import (
    DECISION_ALLOWED,
    DECISION_DENIED,
    EFFECT_NETWORK_FETCH,
    EffectReceipt,
    close_effect_receipt_scope,
    effect_receipts,
    open_effect_receipt_scope,
    record_effect_receipt,
)
from core.remote_fetch_policy import (
    RemoteFetchRefusedError,
    remote_fetch_policy_scope,
)

TURN_A = {"surface": "openclaw", "platform": "openclaw", "session_id": "turn-a"}
TURN_B = {"surface": "openclaw", "platform": "openclaw", "session_id": "turn-b"}


def _receipt(reason: str) -> EffectReceipt:
    return EffectReceipt(
        effect_class=EFFECT_NETWORK_FETCH,
        decision=DECISION_DENIED,
        reason=reason,
        recorded_at="t",
    )


def _open(url: str) -> None:
    from core.remote_fetch_policy import open_remote_url

    open_remote_url(url, timeout=0.001)


# ------------------------------------------------------------------ isolation


def test_two_concurrent_turns_never_observe_each_others_receipts():
    """RED at eab9e8b6: both turns append to the one process-global list, so each
    reads two receipts — including the other turn's — instead of its own one."""
    ready = threading.Barrier(2)
    recorded = threading.Barrier(2)
    seen: dict[str, list[dict]] = {}
    failures: list[BaseException] = []

    def _turn(name: str, context: dict) -> None:
        try:
            with remote_fetch_policy_scope(context):
                ready.wait(timeout=10)
                record_effect_receipt(_receipt(name))
                recorded.wait(timeout=10)
                seen[name] = list(effect_receipts())
        except BaseException as exc:  # reported, never swallowed
            failures.append(exc)

    threads = [
        threading.Thread(target=_turn, args=("turn-a", TURN_A)),
        threading.Thread(target=_turn, args=("turn-b", TURN_B)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not failures, failures
    assert [r["reason"] for r in seen["turn-a"]] == ["turn-a"], seen["turn-a"]
    assert [r["reason"] for r in seen["turn-b"]] == ["turn-b"], seen["turn-b"]


def test_a_turn_that_ends_cannot_drain_a_turn_still_running():
    """RED at eab9e8b6: turn B's scope exit sets the shared channel to None, so
    turn A — still open — loses the receipt it had already recorded."""
    a_recorded = threading.Barrier(2)
    b_closed = threading.Barrier(2)
    survived: dict[str, list[dict]] = {}
    failures: list[BaseException] = []

    def _long_turn() -> None:
        try:
            with remote_fetch_policy_scope(TURN_A):
                record_effect_receipt(_receipt("long-turn"))
                a_recorded.wait(timeout=10)
                b_closed.wait(timeout=10)
                survived["after"] = list(effect_receipts())
                record_effect_receipt(_receipt("after-b-closed"))
                survived["still_open"] = list(effect_receipts())
        except BaseException as exc:  # reported, never swallowed
            failures.append(exc)

    def _short_turn() -> None:
        try:
            a_recorded.wait(timeout=10)
            with remote_fetch_policy_scope(TURN_B):
                record_effect_receipt(_receipt("short-turn"))
            b_closed.wait(timeout=10)
        except BaseException as exc:  # reported, never swallowed
            failures.append(exc)

    threads = [threading.Thread(target=_long_turn), threading.Thread(target=_short_turn)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert not failures, failures
    assert [r["reason"] for r in survived["after"]] == ["long-turn"]
    assert [r["reason"] for r in survived["still_open"]] == ["long-turn", "after-b-closed"]


def test_a_nested_scope_never_closes_or_drains_its_parent():
    """RED at eab9e8b6: the inner scope's finally drains the shared channel and
    leaves it closed, so the parent loses its receipt AND stops recording."""
    with remote_fetch_policy_scope(TURN_A):
        record_effect_receipt(_receipt("parent-before"))
        with remote_fetch_policy_scope(TURN_B):
            record_effect_receipt(_receipt("child"))
            child_view = [r["reason"] for r in effect_receipts()]
        parent_after = [r["reason"] for r in effect_receipts()]
        record_effect_receipt(_receipt("parent-after"))
        parent_final = [r["reason"] for r in effect_receipts()]

    assert child_view == ["child"], child_view
    assert parent_after == ["parent-before"], parent_after
    assert parent_final == ["parent-before", "parent-after"], parent_final


def test_scope_exit_hands_the_turn_its_receipts_instead_of_dropping_them():
    """RED at eab9e8b6: `remote_fetch_policy_scope` calls
    `close_effect_receipt_scope()` and discards the return value, so a turn's
    effects are unreadable the moment the scope closes."""
    from core.effect_gateway import EFFECT_RECEIPTS_CONTEXT_KEY

    context = dict(TURN_A)
    with remote_fetch_policy_scope(context):
        record_effect_receipt(_receipt("kept"))
    published = context.get(EFFECT_RECEIPTS_CONTEXT_KEY)
    assert isinstance(published, list), context
    assert [r["reason"] for r in published] == ["kept"]


def test_a_worker_task_records_into_the_turn_that_owns_it():
    """A pool worker running the turn's copied context reports into the turn's
    ledger, not into a ledger of its own that dies with the task."""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    with remote_fetch_policy_scope(TURN_A):
        carrier = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(
                pool.map(
                    lambda index: carrier.run(record_effect_receipt, _receipt(f"worker-{index}")),
                    range(4),
                )
            )
        reasons = sorted(r["reason"] for r in effect_receipts())
    assert reasons == ["worker-0", "worker-1", "worker-2", "worker-3"], reasons


# ------------------------------------------------------------------ the receipt itself


def test_every_receipt_names_its_turn_its_effect_and_its_lifecycle():
    """RED at eab9e8b6: a receipt carries no request id, no turn id, no effect id
    and no lifecycle state, so it cannot be attributed to the turn that made it."""
    from core.turn_contract import TURN_REQUEST_KEY, TurnRequest

    request = TurnRequest.from_ingress(
        user_text="fetch the page",
        source_context={"surface": "openclaw"},
        request_id="req-r2-1",
        turn_id="turn-r2-1",
        session_id="sess-r2-1",
    )
    context = {**TURN_A, TURN_REQUEST_KEY: request}
    with remote_fetch_policy_scope(context):
        record_effect_receipt(_receipt("identified"))
        record_effect_receipt(_receipt("identified-again"))
        receipts = list(effect_receipts())

    for receipt in receipts:
        for field in (
            "request_id",
            "turn_id",
            "effect_id",
            "effect_class",
            "lifecycle",
            "reason",
        ):
            assert field in receipt, (field, sorted(receipt))
        assert receipt["request_id"] == "req-r2-1"
        assert receipt["turn_id"] == "turn-r2-1"
        assert receipt["effect_class"] == EFFECT_NETWORK_FETCH
        assert receipt["lifecycle"] == "denied"
    assert receipts[0]["effect_id"] != receipts[1]["effect_id"], receipts


def test_the_lifecycle_is_typed_and_an_unknown_state_is_refused():
    """RED at eab9e8b6: there is no lifecycle field at all, so nothing
    distinguishes started from succeeded, failed, cancelled or unknown."""
    from core.effect_gateway import (
        LIFECYCLE_AUTHORIZED,
        LIFECYCLE_CANCELLED,
        LIFECYCLE_DENIED,
        LIFECYCLE_FAILED,
        LIFECYCLE_SIMULATED,
        LIFECYCLE_STARTED,
        LIFECYCLE_STATES,
        LIFECYCLE_SUCCEEDED,
        LIFECYCLE_UNKNOWN,
    )

    assert frozenset(
        {
            LIFECYCLE_DENIED,
            LIFECYCLE_AUTHORIZED,
            LIFECYCLE_STARTED,
            LIFECYCLE_SUCCEEDED,
            LIFECYCLE_FAILED,
            LIFECYCLE_CANCELLED,
            LIFECYCLE_UNKNOWN,
            LIFECYCLE_SIMULATED,
        }
    ) == LIFECYCLE_STATES
    with pytest.raises(ValueError):
        EffectReceipt(
            effect_class=EFFECT_NETWORK_FETCH,
            decision=DECISION_DENIED,
            lifecycle="probably_fine",
        )


def test_the_ledger_is_bounded_and_says_when_it_truncated():
    """RED at eab9e8b6: the channel is an unbounded list, so a retry storm grows
    it without limit and a reader cannot tell a full account from a partial one."""
    from core.effect_gateway import (
        MAX_RECEIPTS_PER_TURN,
        effect_receipts_truncated,
    )

    context = dict(TURN_A)
    with remote_fetch_policy_scope(context):
        for index in range(MAX_RECEIPTS_PER_TURN + 25):
            record_effect_receipt(_receipt(f"storm-{index}"))
        assert len(effect_receipts()) == MAX_RECEIPTS_PER_TURN
        assert effect_receipts_truncated() is True

    from core.effect_gateway import EFFECT_RECEIPTS_CONTEXT_KEY

    assert len(context[EFFECT_RECEIPTS_CONTEXT_KEY]) == MAX_RECEIPTS_PER_TURN


# ------------------------------------------------------------------ the frozen policy


def test_the_turn_derives_exactly_one_policy_for_every_gate(monkeypatch):
    """RED at eab9e8b6: `decide_network_fetch` builds a TurnPolicy at every fetch,
    so one turn with two effect doors decides under two different policies."""
    from core import effect_gateway

    derivations: list[str] = []
    original = effect_gateway.TurnPolicy.from_source_context

    def _counted(source_context):
        derivations.append("derived")
        return original(source_context)

    monkeypatch.setattr(
        effect_gateway.TurnPolicy, "from_source_context", staticmethod(_counted)
    )

    from core.execution_gate import ExecutionGate

    with remote_fetch_policy_scope(TURN_A):
        for host in ("one.invalid", "two.invalid"):
            with pytest.raises((RemoteFetchRefusedError, OSError)):
                _open(f"https://{host}/x")
        ExecutionGate.evaluate_command("ls -la")
        ExecutionGate.evaluate_machine_effect(
            effect_type="machine.write_file", resolved_path="/tmp/r2.txt"
        )
        receipts = list(effect_receipts())
        policy = effect_gateway.current_turn_policy()

    assert len(derivations) == 1, derivations
    assert policy is not None and policy.policy_id
    stamped = {r.get("policy_id") for r in receipts}
    assert stamped == {policy.policy_id}, stamped


def test_a_mode_change_made_mid_turn_binds_the_next_turn_not_this_one():
    """RED at eab9e8b6: the consult reads the live mode at every door, so raising
    the mode mid-turn retroactively widened the turn already in flight (and the
    consult itself failed open, so the first fetch was never denied at all)."""
    from core import mode_permission_policy as mpp
    from core.effect_gateway import decide_network_fetch

    session = "r2-mode-session"
    mpp.reset_mode_permission_state()
    try:
        mpp.set_active_mode(session_id=session, mode="plan")
        context = {"surface": "openclaw", "session_id": session}
        with remote_fetch_policy_scope(context):
            first, first_reason = decide_network_fetch(context)
            assert first == DECISION_DENIED, (first, first_reason)
            mpp.set_active_mode(session_id=session, mode="auto")
            second, second_reason = decide_network_fetch(context)
            assert second == DECISION_DENIED, (second, second_reason)
        # The NEXT turn sees the new mode.
        with remote_fetch_policy_scope(context):
            after, _ = decide_network_fetch(context)
        assert after == DECISION_ALLOWED, after
    finally:
        mpp.reset_mode_permission_state()


def test_a_policy_failure_denies_the_effect_and_leaves_a_receipt(monkeypatch):
    """RED at eab9e8b6: `except Exception -> DECISION_ALLOWED` let every policy
    construction or consult failure through the network door."""
    from core import mode_permission_policy as mpp

    def _explode(**kwargs):
        raise RuntimeError("policy store unavailable")

    monkeypatch.setattr(mpp, "decide_tool_call", _explode)

    session = "r2-failure-session"
    mpp.set_active_mode(session_id=session, mode="auto")
    context = {"surface": "openclaw", "session_id": session}
    try:
        with pytest.raises(RemoteFetchRefusedError):
            with remote_fetch_policy_scope(context):
                try:
                    _open("https://policy-failure.invalid/x")
                finally:
                    captured = list(effect_receipts())
    finally:
        mpp.reset_mode_permission_state()

    assert captured, "the fail-closed denial left no receipt"
    denial = captured[-1]
    assert denial["decision"] == DECISION_DENIED
    assert denial["lifecycle"] == "denied"
    assert "failed closed" in denial["reason"], denial["reason"]


def test_a_command_gate_policy_failure_blocks_instead_of_running(monkeypatch):
    """The same fail-closed law at the command door: if the turn's policy cannot
    be consulted the command is blocked, with the denial recorded."""
    from core import effect_gateway
    from core.execution_gate import ExecutionGate

    with remote_fetch_policy_scope(TURN_A):
        monkeypatch.setattr(
            effect_gateway,
            "current_turn_policy",
            lambda: (_ for _ in ()).throw(RuntimeError("policy unreadable")),
        )
        result = ExecutionGate.evaluate_command("ls -la")
        monkeypatch.undo()
        receipts = list(effect_receipts())

    assert result["decision"] == "blocked", result
    assert receipts[-1]["decision"] == DECISION_DENIED
    assert "failed closed" in receipts[-1]["reason"], receipts[-1]["reason"]


# ------------------------------------------------------------------ the terminal truth


@pytest.fixture()
def bound_ledger(tmp_path):
    """A fresh obligation ledger with one satisfied prose obligation, so
    `finalize_answer` can reach its terminal truth."""
    import storage.db as sdb
    from core.conductor import obligation_ledger as ol
    from storage.migrations import run_migrations

    sdb.configure_default_db_path(tmp_path / "r2-effect-truth.db")
    run_migrations()
    opened = ol.open_obligation_set(
        request_text="fetch the page",
        obligations=[
            {"obligation_id": "ob:r2:answer", "text": "fetch the page", "kind": "prose"}
        ],
    )
    ol.record_disposition(
        opened["set_id"], opened["version"], "ob:r2:answer", "satisfied",
        evidence_source="served_bytes",
    )
    ol.bind_active_set(opened["set_id"], opened["version"])
    yield {**ol.closure_verdict(opened["set_id"], opened["version"]), "set_id": opened["set_id"]}
    ol.clear_active_set()
    sdb.configure_default_db_path(None)


def test_the_turn_result_carries_the_turns_effect_receipts(bound_ledger):
    """RED at eab9e8b6: TurnResult.effect_receipts was empty on every turn, so
    the terminal record of a turn denied at the network door said nothing about
    the denial."""
    import json

    from core.finalization import finalize_answer

    context = dict(TURN_A)
    with remote_fetch_policy_scope({**context, "allow_remote_fetch": False}):
        with pytest.raises(RemoteFetchRefusedError):
            _open("https://denied-at-the-door.invalid/x")
        commit = finalize_answer(
            turn_id="turn-r2-final",
            canonical_content="I did not reach the network.",
            closure=bound_ledger,
            status="ANSWER_PRESENT",
        )

    carried = commit["turn_result"]["effect_receipts"]
    assert carried, commit["turn_result"]
    assert [r["decision"] for r in carried] == [DECISION_DENIED]
    assert carried[0]["effect_class"] == EFFECT_NETWORK_FETCH
    # JSON-safe: the commit envelope is serialized on the serve path.
    assert json.loads(json.dumps(commit["turn_result"]))["effect_receipts"] == carried
    assert commit["turn_result"]["effect_receipts_truncated"] is False


def test_a_turn_result_reports_a_truncated_effect_account(bound_ledger):
    """A bounded ledger that dropped detail must say so in the terminal record,
    or a short list reads as the whole story."""
    from core.effect_gateway import MAX_RECEIPTS_PER_TURN
    from core.finalization import finalize_answer

    with remote_fetch_policy_scope(dict(TURN_A)):
        for index in range(MAX_RECEIPTS_PER_TURN + 5):
            record_effect_receipt(_receipt(f"storm-{index}"))
        commit = finalize_answer(
            turn_id="turn-r2-truncated",
            canonical_content="I did a great many things.",
            closure=bound_ledger,
            status="ANSWER_PRESENT",
        )

    assert commit["turn_result"]["effect_receipts_truncated"] is True
    assert len(commit["turn_result"]["effect_receipts"]) == MAX_RECEIPTS_PER_TURN


def test_the_scope_still_drains_only_its_own_turn():
    """Invariant 7 at the raw channel: what a scope returns is that scope's
    receipts, and closing it restores the caller's own channel."""
    outer = open_effect_receipt_scope(dict(TURN_A))
    record_effect_receipt(_receipt("outer"))
    inner = open_effect_receipt_scope(dict(TURN_B))
    record_effect_receipt(_receipt("inner"))
    inner_drained = close_effect_receipt_scope()
    outer_drained = close_effect_receipt_scope()

    assert outer is not inner
    assert [r["reason"] for r in inner_drained] == ["inner"]
    assert [r["reason"] for r in outer_drained] == ["outer"]
    assert effect_receipts() == ()
    # Outside every scope a receipt is dropped, never invented onto a foreign turn.
    record_effect_receipt(_receipt("orphan"))
    assert effect_receipts() == ()


def test_an_allowed_fetch_is_receipted_with_the_turns_frozen_mode():
    """The mode a receipt names is the turn's frozen mode — it used to be empty on
    every receipt, because the label was read through a call that always raised.

    R2b2b: the account no longer STOPS at `authorized`. The probe host does not
    resolve, so the same logical effect continues started → failed under one
    effect_id — where before the failure left no record and `authorized` was
    the account's last (and only) word."""
    from core import mode_permission_policy as mpp

    session = "r2-mode-label"
    mpp.reset_mode_permission_state()
    try:
        mpp.set_active_mode(session_id=session, mode="auto")
        context = {"surface": "openclaw", "session_id": session}
        with pytest.raises((RemoteFetchRefusedError, OSError)):
            with remote_fetch_policy_scope(context):
                try:
                    _open("https://allowed-probe.invalid/x")
                finally:
                    captured = list(effect_receipts())
    finally:
        mpp.reset_mode_permission_state()

    allowed = [r for r in captured if r["decision"] == DECISION_ALLOWED]
    assert allowed, captured
    assert all(r["mode"] == "auto" for r in allowed), allowed
    assert [r["lifecycle"] for r in captured] == ["authorized", "started", "failed"]
    assert len({r["effect_id"] for r in captured}) == 1
