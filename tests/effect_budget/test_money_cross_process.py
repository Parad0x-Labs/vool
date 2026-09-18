"""THE MONETARY LAW ACROSS INDEPENDENT PROCESSES.

Every worker here is its own `python -B` process (`money_race_probe.race`),
started, parked at a file barrier after its imports, then released at once
against one shared sqlite store — the deployment shape of several agents or a
daemon plus a CLI. No thread-in-one-process stands in for a process: the
atomicity under test is the store's BEGIN IMMEDIATE serialization across
processes, and the recovery under test is what a SIGKILLed process leaves
behind.

Assets, accounts and providers are SYNTHETIC identities.
"""
from __future__ import annotations

import json
import signal

import pytest

from core import effect_budget as eb
from core import effect_budget_money as ebm
from tests.effect_budget import money_race_probe as probe
from tests.effect_budget.conftest import *  # noqa: F403 — fixtures

pytestmark = [pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason="needs POSIX process signals")]

NETWORK = probe.MONEY_NETWORK
USDC = probe.MONEY_USDC
SOL = probe.MONEY_SOL
PROVIDER = probe.MONEY_PROVIDER
ACCOUNT = probe.MONEY_ACCOUNT
X402_ACCOUNT = probe.MONEY_X402_ACCOUNT
PAYER = probe.MONEY_PAYER


@pytest.fixture()
def shared_home(tmp_path):
    """One store for this test process AND every worker it spawns."""
    home = tmp_path / "shared-home"
    probe.prepare_shared_store(home)
    eb.reset_effect_budget_process_state()
    try:
        yield home
    finally:
        from core import runtime_paths
        from core.runtime_continuity import configure_runtime_continuity_db_path

        configure_runtime_continuity_db_path(None)
        runtime_paths.configure_runtime_home(None)


def _granted(results):
    return [item for item in results if item.get("granted")]


def _codes(results):
    return sorted(str(item.get("code")) for item in results if not item.get("granted"))


def _liabilities():
    from storage.db import get_connection

    conn = get_connection()
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM effect_budget_money_liabilities ORDER BY created_epoch").fetchall()]
    finally:
        conn.close()


def _held_expense(task_id: str) -> int:
    from storage.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT l.max_atomic, l.actual_atomic, l.line_state, m.state FROM effect_budget_money_lines l "
            "JOIN effect_budget_money_liabilities m ON m.liability_id = l.liability_id "
            "WHERE l.flow='inference_expense' AND m.task_id=? AND m.state NOT IN ('released','unsent')",
            (task_id,),
        ).fetchall()
    finally:
        conn.close()
    return sum(int(row["actual_atomic"] if row["line_state"] == "exact" else row["max_atomic"]) for row in rows)


def test_four_processes_each_needing_three_against_a_total_of_four_one_wins_no_overspend(shared_home):
    token = eb.grant_operator_budget_authority("cross-process race")
    for round_index in range(3):
        task = f"race-task-{round_index}"
        grant = probe.mint_prepaid_grant(token, task_id=task, max_total_atomic=4_000_000, per_operation_max_atomic=3_000_000)
        probe.observe_verified_credit(100_000_000)
        requests = [probe.prepaid_request(f"{task}-op-{i}", grant.grant_id, 3_000_000, task_id=task) for i in range(4)]
        results = probe.race(shared_home, "money_reserve", [{"request": request, "claim": True} for request in requests])
        assert all(item["returncode"] == 0 for item in results), results
        assert len(_granted(results)) == 1, results
        assert _codes(results) == [ebm.MONEY_AUTHORITY_EXHAUSTED] * 3, results
        assert _held_expense(task) == 3_000_000 <= 4_000_000
    pids = {item["pid"] for item in results}
    assert len(pids) == 4, "four distinct processes"


def test_the_same_race_bound_by_an_operator_rule_instead_of_the_grant(shared_home):
    token = eb.grant_operator_budget_authority("cross-process rule race")
    ebm.apply_operator_money_adjustment(
        token,
        [ebm.MoneyRuleAdjustment(flow=ebm.FLOW_INFERENCE_EXPENSE, asset=USDC, scope=eb.SCOPE_TASK, new_limit_atomic=4_000_000)],
    )
    grant = probe.mint_prepaid_grant(token, task_id="rule-task", max_total_atomic=100_000_000, per_operation_max_atomic=3_000_000)
    probe.observe_verified_credit(100_000_000)
    requests = [probe.prepaid_request(f"rule-op-{i}", grant.grant_id, 3_000_000, task_id="rule-task") for i in range(4)]
    results = probe.race(shared_home, "money_reserve", [{"request": request, "claim": True} for request in requests])
    assert len(_granted(results)) == 1, results
    assert _codes(results) == [ebm.MONEY_BUDGET_EXCEEDED] * 3
    assert _held_expense("rule-task") == 3_000_000


def test_distinct_nested_scopes_race_task_inside_session_inside_provider(shared_home):
    token = eb.grant_operator_budget_authority("nested scope race")
    ebm.apply_operator_money_adjustment(
        token,
        [
            ebm.MoneyRuleAdjustment(flow=ebm.FLOW_INFERENCE_EXPENSE, asset=USDC, scope=eb.SCOPE_TASK, new_limit_atomic=4_000_000),
            ebm.MoneyRuleAdjustment(flow=ebm.FLOW_INFERENCE_EXPENSE, asset=USDC, scope=eb.SCOPE_SESSION, new_limit_atomic=6_000_000),
            ebm.MoneyRuleAdjustment(flow=ebm.FLOW_INFERENCE_EXPENSE, asset=USDC, scope=eb.SCOPE_PROVIDER, new_limit_atomic=9_000_000),
        ],
    )
    grant = probe.mint_prepaid_grant(token, task_id="", session_id="sess-nest", max_total_atomic=100_000_000, per_operation_max_atomic=3_000_000)
    probe.observe_verified_credit(100_000_000)
    requests = []
    for task in ("nest-a", "nest-b"):
        requests += [probe.prepaid_request(f"{task}-{i}", grant.grant_id, 3_000_000, task_id=task, session_id="sess-nest") for i in range(4)]
    results = probe.race(shared_home, "money_reserve", [{"request": request, "claim": True} for request in requests])
    winners = _granted(results)
    assert len(winners) == 2, results
    assert {item["operation_id"].split("-")[1] for item in winners} == {"a", "b"}, "one per task: the task ceiling binds, the session admits both"
    third = [probe.prepaid_request(f"nest-c-{i}", grant.grant_id, 1_000_000, task_id="nest-c", session_id="sess-nest") for i in range(3)]
    results = probe.race(shared_home, "money_reserve", [{"request": request, "claim": True} for request in third])
    assert _granted(results) == [] and _codes(results) == [ebm.MONEY_BUDGET_EXCEEDED] * 3, "the session is full at 6"
    other_session = probe.mint_prepaid_grant(token, task_id="", session_id="sess-other", max_total_atomic=100_000_000, per_operation_max_atomic=3_000_000)
    fourth = [probe.prepaid_request(f"other-{i}", other_session.grant_id, 3_000_000, task_id=f"other-task-{i}", session_id="sess-other") for i in range(3)]
    results = probe.race(shared_home, "money_reserve", [{"request": request, "claim": True} for request in fourth])
    assert len(_granted(results)) == 1, "the provider ceiling (9) admits exactly one more 3 across a different session"


def test_a_novel_race_where_the_fee_asset_and_verified_credit_bind_not_the_budget(shared_home):
    """Different data and a different answer from the original race: six x402
    payments of 1 USDC with a 5,000-lamport fee against a wallet holding plenty
    of USDC but 12,000 lamports (two fees fit), and four prepaid calls of 2 USDC
    against 5 USDC of verified provider credit (two fit) — all at once."""
    token = eb.grant_operator_budget_authority("novel race")
    x402_grant = probe.mint_x402_grant(token, max_total_atomic=100_000_000, per_operation_max_atomic=1_000_000, max_fee_total_atomic=1_000_000)
    prepaid_grant = probe.mint_prepaid_grant(token, task_id="novel-task", max_total_atomic=100_000_000, per_operation_max_atomic=2_000_000)
    probe.observe_wallet(usdc=100_000_000, sol=12_000)
    probe.observe_verified_credit(5_000_000)
    x402_requests = [probe.x402_request(f"novel-x402-{i}", x402_grant.grant_id, 1_000_000, fee=5_000) for i in range(6)]
    prepaid_requests = [probe.prepaid_request(f"novel-prepaid-{i}", prepaid_grant.grant_id, 2_000_000, task_id="novel-task") for i in range(4)]
    results = probe.race(shared_home, "money_reserve", [{"request": request, "claim": True} for request in x402_requests + prepaid_requests])
    x402_results = [item for item in results if item.get("operation_id", "").startswith("novel-x402")]
    prepaid_results = [item for item in results if item.get("operation_id", "").startswith("novel-prepaid")]
    assert len(x402_results) == 6 and len(prepaid_results) == 4, results
    assert len(_granted(x402_results)) == 2 and _codes(x402_results) == [ebm.MONEY_LIQUIDITY_INSUFFICIENT] * 4
    assert len(_granted(prepaid_results)) == 2 and _codes(prepaid_results) == [ebm.MONEY_LIQUIDITY_INSUFFICIENT] * 2


def test_one_dispatch_claim_wins_across_processes(shared_home):
    token = eb.grant_operator_budget_authority("claim race")
    grant = probe.mint_prepaid_grant(token, task_id="claim-task")
    probe.observe_verified_credit(10_000_000)
    receipt = ebm.reserve_liability(probe.request_from_dict(probe.prepaid_request("claim-op", grant.grant_id, 1_000_000, task_id="claim-task")))
    results = probe.race(shared_home, "money_claim", [{"liability_id": receipt.liability_id} for _ in range(6)])
    winners = [item for item in results if item.get("claimed")]
    assert len(winners) == 1, results
    assert sorted(item["code"] for item in results if not item.get("claimed")) == [ebm.MONEY_CLAIM_CONFLICT] * 5
    row = _liabilities()[0]
    assert row["state"] == ebm.LIABILITY_DISPATCHING and row["attempt"] == 1


def test_process_death_at_each_boundary_then_restart_never_reopens_unknown_money(shared_home):
    token = eb.grant_operator_budget_authority("kill boundaries")
    grant = probe.mint_prepaid_grant(token, task_id="kill-task", max_total_atomic=25_000_000, per_operation_max_atomic=10_000_000)
    probe.observe_verified_credit(100_000_000)
    evidence = {"kind": ebm.EVIDENCE_PROVIDER_USAGE_RECEIPT, "source": "provider", "actuals": {ebm.FLOW_INFERENCE_EXPENSE: 100_000, ebm.FLOW_PROVIDER_CREDIT_DEBIT: 100_000}}
    boundaries = ["after_reserve", "after_claim", "after_dispatched", "inside_settle_commit", "never"]
    arguments = [
        {
            "request": probe.prepaid_request(f"kill-{boundary}", grant.grant_id, 5_000_000 if boundary != "never" else 1_000_000, task_id="kill-task"),
            "die_at": boundary,
            "evidence": {**evidence, "id": f"receipt-{boundary}"},
        }
        for boundary in boundaries
    ]
    results = {item["operation_id"]: item for item in probe.race(shared_home, "money_sequence", arguments)}
    for boundary in boundaries[:-1]:
        assert results[f"kill-{boundary}"]["returncode"] == -signal.SIGKILL, results[f"kill-{boundary}"]
    assert results["kill-never"]["returncode"] == 0 and results["kill-never"]["state"] == ebm.LIABILITY_SETTLED
    # restart: a fresh process state reconciles against the store the dead left
    eb.reset_effect_budget_process_state()
    changes = {change["liability_id"]: change["to"] for change in ebm.reconcile_money_liabilities(force=True)}
    by_operation = {row["operation_id"]: row for row in _liabilities()}
    assert by_operation["kill-after_reserve"]["state"] == ebm.LIABILITY_RELEASED
    assert by_operation["kill-after_claim"]["state"] == ebm.LIABILITY_UNKNOWN
    assert by_operation["kill-after_dispatched"]["state"] == ebm.LIABILITY_UNKNOWN
    assert by_operation["kill-inside_settle_commit"]["state"] == ebm.LIABILITY_UNKNOWN, "a settlement killed before commit left no settlement"
    # the surviving worker may already have reconciled some of these on its own first use
    assert set(changes.values()) <= {ebm.LIABILITY_RELEASED, ebm.LIABILITY_UNKNOWN}
    from storage.db import get_connection

    conn = get_connection()
    try:
        evidence_rows = conn.execute(
            "SELECT e.evidence_id FROM effect_budget_money_evidence e JOIN effect_budget_money_liabilities m ON m.liability_id=e.liability_id WHERE m.operation_id='kill-inside_settle_commit'"
        ).fetchall()
    finally:
        conn.close()
    assert evidence_rows == []
    # unknown is not free: three unknown maxima (15) + the settled 0.1 of a 25 envelope leaves 9.9
    reopened = probe.race(shared_home, "money_reserve", [{"request": probe.prepaid_request("after-restart-10", grant.grant_id, 10_000_000, task_id="kill-task")}])
    assert reopened[0]["granted"] is False and reopened[0]["code"] == ebm.MONEY_AUTHORITY_EXHAUSTED
    fits = probe.race(shared_home, "money_reserve", [{"request": probe.prepaid_request("after-restart-9.9", grant.grant_id, 9_900_000, task_id="kill-task")}])
    assert fits[0]["granted"] is True
    # a fresh process sees the same held truth
    seen = probe.race(shared_home, "money_projection", [{"task_id": "kill-task"}])[0]
    assert seen["projection"]["liability_states"].get(ebm.LIABILITY_UNKNOWN) == 3
    # evidence arrives later; replaying it from several processes settles each exactly once
    unknown_ids = [by_operation[f"kill-{b}"]["liability_id"] for b in ("after_claim", "after_dispatched", "inside_settle_commit")]
    settle_args = [{"liability_id": liability_id, "evidence": {**evidence, "id": f"late-{liability_id}"}} for liability_id in unknown_ids for _ in range(2)]
    settled = probe.race(shared_home, "money_settle", settle_args)
    assert sum(1 for item in settled if item.get("applied") and not item.get("idempotent")) == 3, settled
    assert sum(1 for item in settled if item.get("idempotent")) == 3, settled
    assert all(row["state"] in (ebm.LIABILITY_SETTLED, ebm.LIABILITY_RELEASED, ebm.LIABILITY_RESERVED) for row in _liabilities())


def test_duplicate_and_conflicting_receipts_from_several_processes(shared_home):
    token = eb.grant_operator_budget_authority("receipt race")
    grant = probe.mint_prepaid_grant(token, task_id="receipt-task")
    probe.observe_verified_credit(10_000_000)
    receipt = ebm.reserve_liability(probe.request_from_dict(probe.prepaid_request("receipt-op", grant.grant_id, 1_000_000, task_id="receipt-task")))
    claim = ebm.claim_dispatch(receipt.liability_id, executor="test")
    ebm.record_dispatched(receipt.liability_id, claim.claim_token, evidence_id="req-receipt")
    same = {"kind": ebm.EVIDENCE_PROVIDER_USAGE_RECEIPT, "id": "bill-receipt-op", "source": "provider", "actuals": {ebm.FLOW_INFERENCE_EXPENSE: 42_000, ebm.FLOW_PROVIDER_CREDIT_DEBIT: 42_000}}
    results = probe.race(shared_home, "money_settle", [{"liability_id": receipt.liability_id, "evidence": same} for _ in range(5)])
    assert sum(1 for item in results if item.get("applied") and not item.get("idempotent")) == 1, results
    assert sum(1 for item in results if item.get("idempotent")) == 4, results
    conflicting = [
        {"liability_id": receipt.liability_id, "evidence": {**same, "actuals": {ebm.FLOW_INFERENCE_EXPENSE: 43_000, ebm.FLOW_PROVIDER_CREDIT_DEBIT: 43_000}}},
        {"liability_id": receipt.liability_id, "evidence": {**same, "id": "bill-other", "actuals": {ebm.FLOW_INFERENCE_EXPENSE: 1, ebm.FLOW_PROVIDER_CREDIT_DEBIT: 1}}},
        {"liability_id": receipt.liability_id, "evidence": {**same, "id": "bill-delta", "kind": ebm.EVIDENCE_BALANCE_DELTA}},
    ]
    refused = probe.race(shared_home, "money_settle", conflicting)
    assert [item.get("code") for item in refused] == [ebm.MONEY_SETTLEMENT_CONFLICT, ebm.MONEY_SETTLEMENT_CONFLICT, ebm.MONEY_EVIDENCE_INSUFFICIENT], refused
    from storage.db import get_connection

    conn = get_connection()
    try:
        lines = {row["flow"]: row["actual_atomic"] for row in conn.execute("SELECT flow, actual_atomic FROM effect_budget_money_lines WHERE liability_id=?", (receipt.liability_id,)).fetchall()}
        evidence = conn.execute("SELECT COUNT(*) AS n FROM effect_budget_money_evidence WHERE liability_id=?", (receipt.liability_id,)).fetchone()["n"]
    finally:
        conn.close()
    assert lines == {"inference_expense": "42000", "provider_credit_debit": "42000"} and evidence == 1


def _reconciliation_journal():
    from storage.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT event_kind, reservation_id, instance_id, detail_json FROM effect_budget_events "
            "WHERE event_kind IN ('money_reconciled_release', 'money_reconciled_unknown') ORDER BY seq"
        ).fetchall()
    finally:
        conn.close()
    return {row["reservation_id"]: {"event_kind": row["event_kind"], "instance_id": row["instance_id"], "detail": json.loads(row["detail_json"])} for row in rows}


def test_an_exited_unclaimed_reservation_returns_while_a_claimed_one_stays_held(shared_home):
    """Liveness without widening: a process that reserved and exited without
    ever claiming dispatch cannot have paid, so the next process's first use
    returns that capacity; a process that claimed and exited may have paid, so
    its maximum stays held as unknown. Races are not solved by refusing
    everything forever. Each automatic transition names the process that
    judged it and the proof it relied on: a pid the kernel reports gone."""
    token = eb.grant_operator_budget_authority("liveness")
    grant = probe.mint_prepaid_grant(token, task_id="live-task", max_total_atomic=4_000_000, per_operation_max_atomic=3_000_000)
    probe.observe_verified_credit(10_000_000)
    unclaimed = probe.race(shared_home, "money_reserve", [{"request": probe.prepaid_request("live-unclaimed", grant.grant_id, 3_000_000, task_id="live-task")}])[0]
    assert unclaimed["granted"] is True and unclaimed["returncode"] == 0
    claimed = probe.race(shared_home, "money_reserve", [{"request": probe.prepaid_request("live-claimed", grant.grant_id, 3_000_000, task_id="live-task"), "claim": True}])[0]
    assert claimed["granted"] is True, "the exited, unclaimed reservation was returned on this process's first use"
    assert ebm.liability(unclaimed["liability_id"])["state"] == ebm.LIABILITY_RELEASED
    third = probe.race(shared_home, "money_reserve", [{"request": probe.prepaid_request("live-third", grant.grant_id, 3_000_000, task_id="live-task")}])[0]
    assert third["granted"] is False and third["code"] == ebm.MONEY_AUTHORITY_EXHAUSTED
    assert ebm.liability(claimed["liability_id"])["state"] == ebm.LIABILITY_UNKNOWN, "claimed and gone: held, never returned"
    journal = _reconciliation_journal()
    assert set(journal) == {unclaimed["liability_id"], claimed["liability_id"]}, journal
    release, unknown = journal[unclaimed["liability_id"]], journal[claimed["liability_id"]]
    # the second process's first use judged the first; the third process's first use judged the second
    assert release["event_kind"] == "money_reconciled_release" and release["instance_id"] == claimed["instance_id"], release
    assert (release["detail"]["judged_instance_id"], release["detail"]["proof"], release["detail"]["reconciler_instance_id"]) == (
        unclaimed["instance_id"], eb.INSTANCE_DEATH_PROCESS_GONE, claimed["instance_id"]), release
    assert unknown["event_kind"] == "money_reconciled_unknown" and unknown["instance_id"] == third["instance_id"], unknown
    assert (unknown["detail"]["judged_instance_id"], unknown["detail"]["proof"], unknown["detail"]["reconciler_instance_id"]) == (
        claimed["instance_id"], eb.INSTANCE_DEATH_PROCESS_GONE, third["instance_id"]), unknown


def test_this_test_runs_on_its_own_isolated_store(request, tmp_path):
    """The package's store isolation and real-home guard hold for every money test whatever order the
    run collected files in (tests/effect_budget/conftest.py exports them for exactly this)."""
    from pathlib import Path

    from storage.db import active_default_db_path

    assert {"_isolated_budget_store", "_real_home_residue_guard"} <= set(request.fixturenames), request.fixturenames
    # Goal 2 stage 2 (2026-09-17): with the store in its own tmp subdir (see conftest), isolation means
    # this test's store lives inside THIS test's tmp tree and nowhere else.
    assert Path(active_default_db_path()).resolve().is_relative_to(Path(tmp_path).resolve())
