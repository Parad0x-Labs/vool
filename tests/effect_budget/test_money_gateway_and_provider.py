"""THE MONEY LAW AT THE DOORS — the effect gateway and the provider seal.

Real turn scopes (`open_effect_receipt_scope`), the real
`EffectLedger.open_effect` / `EffectLifecycle` transitions, and the real
`seal_direct_provider_invocation` -> `ProviderInvocationPermit.consume()`.
No provider is contacted anywhere: where a paid call's transport would run,
the test takes that step itself between `consume()` and the money record, and
says so. Identities, mints and accounts are SYNTHETIC.
"""
from __future__ import annotations

import time
from dataclasses import replace

import pytest

from core import effect_budget as eb
from core import effect_budget_money as ebm
from core.effect_budget_money import (
    AssetIdentity,
    LiabilityRequest,
    MoneyGrantSpec,
    MoneyIdentity,
    MoneyLine,
    SettlementEvidence,
)
from core.effect_gateway import (
    DECISION_ALLOWED,
    LIFECYCLE_AUTHORIZED,
    EffectReceipt,
    close_effect_receipt_scope,
    effect_receipts,
    open_effect_receipt_scope,
)
from tests.effect_budget.conftest import *  # noqa: F403 — fixtures

USDC = AssetIdentity(network="solana:synthetic-cluster", asset="SyntheticUsdcMint11111111111111111111111111", decimals=6, symbol="USDC")
PROVIDER = "usepod-synthetic"
ACCOUNT = "usepod-synthetic:acct-1"


def _turn(session="sess-door", turn="turn-door", task="task-door"):
    from core.turn_contract import TURN_REQUEST_KEY

    class _Request:
        pass

    request = _Request()
    request.turn_id = turn
    request.request_id = f"req-{turn}"
    request.session_id = session
    request.extras = {}
    return {
        TURN_REQUEST_KEY: request,
        "turn_id": turn,
        "request_id": f"req-{turn}",
        "session_id": session,
        "task_id": task,
        "workspace_root": "/tmp/money-door-project",
    }


def _grant(**overrides):
    token = eb.grant_operator_budget_authority("money door test")
    spec = MoneyGrantSpec(
        kind=ebm.GRANT_TASK_ENVELOPE,
        operation_kinds=(ebm.OP_INFERENCE_PREPAID,),
        provider_id=PROVIDER,
        provider_account=ACCOUNT,
        models=("model-x",),
        routes=("marketplace-only",),
        asset=USDC,
        max_total_atomic=4_000_000,
        per_operation_max_atomic=3_000_000,
        expires_epoch=time.time() + 3600,
        task_id="task-door",
    )
    grant = ebm.grant_money_authority(token, replace(spec, **overrides))
    ebm.record_liquidity_observation(account=ACCOUNT, asset=USDC, balance_atomic=50_000_000, source="provider:x-balance-remaining", verified=True)
    return token, grant


def _money(operation_id, grant, maximum=1_000_000, **identity):
    return LiabilityRequest(
        operation_id=operation_id,
        operation_kind=ebm.OP_INFERENCE_PREPAID,
        grant_id=grant.grant_id,
        lines=(
            MoneyLine(ebm.FLOW_INFERENCE_EXPENSE, USDC, maximum, ACCOUNT),
            MoneyLine(ebm.FLOW_PROVIDER_CREDIT_DEBIT, USDC, maximum, ACCOUNT),
        ),
        identity=MoneyIdentity(**identity),
        provider_account=ACCOUNT,
        model_id="model-x",
        route="marketplace-only",
    )


def _paid_receipt():
    return EffectReceipt(
        effect_class="provider_call",
        decision=DECISION_ALLOWED,
        lifecycle=LIFECYCLE_AUTHORIZED,
        reason="paid model call",
        provider_id=PROVIDER,
    )


def _refusal(call, *args, **kwargs):
    with pytest.raises(eb.EffectBudgetRefusedError) as caught:
        call(*args, **kwargs)
    return caught.value


# ---------------------------------------------------------------------------
# the effect gateway
# ---------------------------------------------------------------------------


def test_open_effect_reserves_money_under_the_ledgers_own_identity():
    _token, grant = _grant()
    ledger = open_effect_receipt_scope(_turn())
    try:
        lifecycle = ledger.open_effect(_paid_receipt(), money=_money("gate-1", grant))
        found = ebm.liability(lifecycle.money_liability_id)
        assert found["state"] == ebm.LIABILITY_RESERVED
        assert (found["session_id"], found["task_id"], found["provider_id"]) == ("sess-door", "task-door", PROVIDER)
        assert found["effect_id"] == lifecycle.effect_id
        from storage.db import get_connection

        conn = get_connection()
        try:
            row = conn.execute("SELECT owner_ref, request_id, turn_id FROM effect_budget_money_liabilities WHERE liability_id=?", (lifecycle.money_liability_id,)).fetchone()
        finally:
            conn.close()
        assert row["owner_ref"] == ledger.ledger_id and row["request_id"] == "req-turn-door" and row["turn_id"] == "turn-door"
    finally:
        close_effect_receipt_scope()


def test_a_stated_identity_that_conflicts_with_the_turn_is_refused_and_its_unit_returns(set_budget):
    _token, grant = _grant()
    set_budget(("provider_call", eb.SCOPE_SESSION, 1))
    ledger = open_effect_receipt_scope(_turn())
    try:
        refusal = _refusal(ledger.open_effect, _paid_receipt(), money=_money("gate-conflict", grant, session_id="someone-elses-session"))
        assert refusal.code == ebm.MONEY_IDENTITY_CONFLICT
        denials = [entry for entry in effect_receipts() if entry.get("lifecycle") == "denied"]
        assert denials and denials[-1]["reason"].startswith(ebm.MONEY_IDENTITY_CONFLICT)
        assert [row["state"] for row in eb.reservation_rows()] == [eb.RESERVATION_RELEASED], "the unit came back"
        # control: the unit is spendable by a legitimate effect in the same session
        lifecycle = ledger.open_effect(_paid_receipt(), money=_money("gate-ok", grant))
        assert ebm.liability(lifecycle.money_liability_id)["state"] == ebm.LIABILITY_RESERVED
    finally:
        close_effect_receipt_scope()


def test_the_claim_precedes_the_attempt_and_a_failed_attempt_is_unknown_not_released():
    _token, grant = _grant()
    ledger = open_effect_receipt_scope(_turn())
    try:
        lifecycle = ledger.open_effect(_paid_receipt(), money=_money("gate-fail", grant, maximum=3_000_000))
        lifecycle.begin_attempt()
        assert ebm.liability(lifecycle.money_liability_id)["state"] == ebm.LIABILITY_DISPATCHING
        lifecycle.fail(reason="timeout_after_request_sent")
        assert ebm.liability(lifecycle.money_liability_id)["state"] == ebm.LIABILITY_UNKNOWN
        retry = _refusal(ledger.open_effect, _paid_receipt(), retry_of=lifecycle.effect_id, money=_money("gate-fail", grant, maximum=3_000_000))
        assert retry.code == ebm.MONEY_CLAIM_CONFLICT, "a blind retry of a payment that may have gone out is refused"
        assert _refusal(ledger.open_effect, _paid_receipt(), money=_money("gate-other", grant, maximum=2_000_000)).code == ebm.MONEY_AUTHORITY_EXHAUSTED
    finally:
        close_effect_receipt_scope()
    assert ebm.liability(lifecycle.money_liability_id)["state"] == ebm.LIABILITY_UNKNOWN, "closing the turn never releases claimed money"


def test_proven_unsent_is_retried_through_the_same_lifecycle_and_then_settles():
    _token, grant = _grant()
    ledger = open_effect_receipt_scope(_turn())
    try:
        lifecycle = ledger.open_effect(_paid_receipt(), money=_money("gate-unsent", grant, maximum=2_000_000))
        lifecycle.begin_attempt()
        assert lifecycle.money_unsent(proof_kind=ebm.UNSENT_CONNECTION_NOT_ESTABLISHED, evidence_id="ECONNREFUSED") == ebm.LIABILITY_UNSENT
        lifecycle.fail(reason="connection_refused")
        assert ebm.liability(lifecycle.money_liability_id)["state"] == ebm.LIABILITY_UNSENT, "a proven-unsent attempt does not turn unknown on fail"
        second = lifecycle.begin_attempt()
        found = ebm.liability(lifecycle.money_liability_id)
        assert second == 2 and found["state"] == ebm.LIABILITY_DISPATCHING and found["attempt"] == 2
        lifecycle.succeed(status=200)
        assert ebm.liability(lifecycle.money_liability_id)["state"] == ebm.LIABILITY_PENDING
        result = lifecycle.settle_money(
            SettlementEvidence(evidence_kind=ebm.EVIDENCE_PROVIDER_USAGE_RECEIPT, evidence_id="door-bill-1", source="provider", actuals={ebm.FLOW_INFERENCE_EXPENSE: 12_345, ebm.FLOW_PROVIDER_CREDIT_DEBIT: 12_345})
        )
        assert result["state"] == ebm.LIABILITY_SETTLED
    finally:
        close_effect_receipt_scope()


def test_cancel_before_the_attempt_and_scope_close_return_only_unclaimed_money():
    _token, grant = _grant()
    ledger = open_effect_receipt_scope(_turn())
    try:
        cancelled = ledger.open_effect(_paid_receipt(), money=_money("gate-cancel", grant))
        cancelled.cancel(reason="planner dropped the call")
        abandoned = ledger.open_effect(_paid_receipt(), money=_money("gate-abandoned", grant))
        claimed = ledger.open_effect(_paid_receipt(), money=_money("gate-claimed", grant))
        claimed.begin_attempt()
    finally:
        close_effect_receipt_scope()
    assert ebm.liability(cancelled.money_liability_id)["state"] == ebm.LIABILITY_RELEASED
    assert ebm.liability(abandoned.money_liability_id)["state"] == ebm.LIABILITY_RELEASED
    assert ebm.liability(claimed.money_liability_id)["state"] == ebm.LIABILITY_DISPATCHING


# ---------------------------------------------------------------------------
# the provider seal
# ---------------------------------------------------------------------------


@pytest.fixture()
def provider_home(tmp_path):
    from core.runtime_paths import configure_runtime_home
    from tests.effect_budget import money_race_probe as probe

    # Restore exactly what was pinned: configure_runtime_home(None) would CLEAR the session
    # home the root conftest installed for the run, not undo this fixture's pin.
    runtime_state = probe.runtime_state_snapshot()
    configure_runtime_home(tmp_path)
    try:
        yield tmp_path
    finally:
        probe.restore_runtime_state(runtime_state)


def _seal(monetary=None, *, require=False, model_id="model-x"):
    from core.context_manifest import build_context_manifest
    from core.provenance_store import store_manifest
    from core.provider_invocation_gateway import seal_direct_provider_invocation

    manifest = build_context_manifest(
        task_id="task-door",
        trace_id="request-money-door",
        evidence_items=[],
        source_metadata=[],
        chat_id="chat-money",
        project_id="project-money",
        capsule_version="none",
    )
    store_manifest(manifest)
    return seal_direct_provider_invocation(
        provider_id=PROVIDER,
        model_id=model_id,
        operation="chat",
        payload={"model": model_id, "messages": [], "max_tokens": 256},
        request_id="request-money-door",
        context_manifest={
            "context_manifest_id": manifest.manifest_id,
            "chat_id": "chat-money",
            "project_id": "project-money",
            "capsule_version": "none",
            "items_included": [],
            "items_excluded": [],
        },
        max_output_tokens=256,
        monetary=monetary,
        require_monetary_authority=require,
    )


def _manifest_count():
    from storage.db import get_connection

    conn = get_connection()
    try:
        present = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='provider_invocation_manifests'").fetchone()
        return 0 if present is None else int(conn.execute("SELECT COUNT(*) FROM provider_invocation_manifests").fetchone()[0])
    finally:
        conn.close()


def test_a_billing_surface_without_monetary_authority_is_refused_before_anything_is_sealed(provider_home):
    before = _manifest_count()
    refusal = _refusal(_seal, None, require=True)
    assert refusal.code == ebm.MONEY_AUTHORITY_REQUIRED
    assert _manifest_count() == before, "nothing was sealed for an uncapped paid call"


def test_the_permit_claims_money_before_the_payload_leaves_and_an_interrupted_stream_stays_unknown(provider_home, set_budget):
    _token, grant = _grant()
    set_budget(("provider_call", eb.SCOPE_PROJECT, 5))
    permit = _seal(_money("seal-1", grant, maximum=3_000_000, task_id="task-door", provider_id=PROVIDER))
    liability = ebm.liability(permit.money_liability_id)
    assert liability["state"] == ebm.LIABILITY_RESERVED and liability["model_id"] == "model-x"
    payload = permit.consume()
    assert payload["model"] == "model-x"
    assert ebm.liability(permit.money_liability_id)["state"] == ebm.LIABILITY_DISPATCHING
    # the test's own transport step: headers arrived, then the stream died mid-way
    permit.money_dispatched(evidence_id="provider-request-77")
    permit.money_unknown(reason="stream_interrupted_after_first_chunks")
    assert ebm.liability(permit.money_liability_id)["state"] == ebm.LIABILITY_UNKNOWN
    refused = _refusal(_seal, _money("seal-2", grant, maximum=2_000_000, task_id="task-door"))
    assert refused.code == ebm.MONEY_AUTHORITY_EXHAUSTED
    unit_states = sorted(row["state"] for row in eb.reservation_rows())
    assert unit_states == [eb.RESERVATION_CONSUMED, eb.RESERVATION_RELEASED], "the refused seal returned its unit"
    settled = permit.settle_money(
        SettlementEvidence(evidence_kind=ebm.EVIDENCE_PROVIDER_BILLING_STATEMENT, evidence_id="bill-seal-1", source="provider", actuals={ebm.FLOW_INFERENCE_EXPENSE: 800_000, ebm.FLOW_PROVIDER_CREDIT_DEBIT: 800_000})
    )
    assert settled["state"] == ebm.LIABILITY_SETTLED
    assert _seal(_money("seal-3", grant, maximum=3_000_000, task_id="task-door")).money_liability_id, "settlement returned the unused part"


def test_the_sealed_model_and_provider_are_the_liabilitys(provider_home):
    _token, grant = _grant()
    assert _refusal(_seal, replace(_money("seal-model", grant, task_id="task-door"), model_id="model-y")).code == ebm.MONEY_IDENTITY_CONFLICT
    assert _refusal(_seal, _money("seal-provider", grant, task_id="task-door", provider_id="another-provider")).code == ebm.MONEY_IDENTITY_CONFLICT


def test_revocation_stops_consume_before_the_payload_leaves(provider_home):
    token, grant = _grant()
    permit = _seal(_money("seal-revoke", grant, task_id="task-door"))
    ebm.revoke_money_authority(token, grant.grant_id, reason="owner stopped the task")
    refusal = _refusal(permit.consume)
    assert refusal.code == ebm.MONEY_AUTHORITY_REVOKED
    assert ebm.liability(permit.money_liability_id)["state"] == ebm.LIABILITY_RELEASED
    assert _refusal(permit.consume).code == ebm.MONEY_STATE_ERROR, "no second chance to leak the payload"


def test_an_abandoned_permit_returns_its_never_claimed_money(provider_home):
    _token, grant = _grant()
    permit = _seal(_money("seal-abandon", grant, task_id="task-door"))
    assert permit.abandon_money() is True
    assert ebm.liability(permit.money_liability_id)["state"] == ebm.LIABILITY_RELEASED


# ---------------------------------------------------------------------------
# the unit law is unchanged for its existing callers
# ---------------------------------------------------------------------------


def test_existing_unit_callers_write_the_same_receipts_and_new_scopes_bind_only_when_named(set_budget):
    set_budget(("command", eb.SCOPE_SESSION, 3), ("command", eb.SCOPE_TASK, 1))
    legacy = eb.reserve_effect_units("command", turn_id="t", session_id="s", project_key="p")
    reserved = next(event for event in eb.budget_events("reserved") if event["reservation_id"] == legacy.reservation_id)
    import json

    detail = json.loads(reserved["detail_json"])
    assert set(detail) == {"rules", "effect_id", "owner_ref", "turn_id", "session_id", "project_key"}, "an old caller's receipt shape is unchanged"
    eb.reserve_effect_units("command", turn_id="t2", session_id="s", project_key="p")
    assert eb.reserve_effect_units("command", turn_id="t3", session_id="s", project_key="p", task_id="task-a").rules
    refusal = _refusal(eb.reserve_effect_units, "command", turn_id="t4", session_id="s-other", project_key="p", task_id="task-a")
    assert refusal.code == eb.REFUSAL_BUDGET_EXCEEDED and refusal.rule == "command/task"
    consumed = eb.reserve_effect_units("command", turn_id="t5", session_id="s-other", project_key="p")
    eb.consume_reservation(consumed.reservation_id)
    assert [row["state"] for row in eb.reservation_rows() if row["reservation_id"] == consumed.reservation_id] == [eb.RESERVATION_CONSUMED], "consumed stays consumed: never read as a settled payment"


def test_this_test_runs_on_its_own_isolated_store(request, tmp_path):
    """The package's store isolation and real-home guard hold for every money test whatever order the
    run collected files in (tests/effect_budget/conftest.py exports them for exactly this)."""
    from pathlib import Path

    from storage.db import active_default_db_path

    assert {"_isolated_budget_store", "_real_home_residue_guard"} <= set(request.fixturenames), request.fixturenames
    # Goal 2 stage 2 (2026-09-17): with the store in its own tmp subdir (see conftest), isolation means
    # this test's store lives inside THIS test's tmp tree and nowhere else.
    assert Path(active_default_db_path()).resolve().is_relative_to(Path(tmp_path).resolve())
