"""A granted paid reservation settles at what the provider will actually bill, retries included.

The measured defect: settlement read ``usage.cost``, which only OpenRouter returns, so 30 owner-
picked Claude Sonnet turns settled $0.00 against a $5.00 daily maximum while the provider billed
for every one of them. These tests drive the real spend ledger.

NO REAL CALL IS MADE ANYWHERE HERE — token counts are replayed into the settlement path; no
adapter, transport or key is involved.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import memory_first_router as mfr
from core import paid_call_reservation as pcr
from core.cloud_providers import config_for
from core.model_spend_ledger import SpendLimits, reserve_spend
from storage.model_provider_manifest import ModelProviderManifest

IN, OUT = 12_000, 900
# Anthropic's published Claude Sonnet 4.5 rate: $3 / 1M input, $15 / 1M output.
SONNET_CALL_USD = round(IN * 3.00 / 1e6 + OUT * 15.00 / 1e6, 8)
LIMITS = SpendLimits(per_call_usd=0.25, per_task_usd=1.00, daily_usd=5.00, monthly_usd=25.00)


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    from core import runtime_paths
    from storage.db import configure_default_db_path

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "test.db")
    yield
    configure_default_db_path(None)
    runtime_paths.configure_runtime_home(None)


@pytest.fixture(autouse=True)
def _clean_attempt_tally():
    pcr._ATTEMPTS.clear()
    yield
    pcr._ATTEMPTS.clear()


def _sonnet_manifest() -> ModelProviderManifest:
    cfg = config_for("anthropic")
    return ModelProviderManifest(
        provider_name="anthropic-byok",
        model_name="claude-sonnet-4-5",
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Provider",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        capabilities=["summarize", "classify", "format", "extract", "structured_json"],
        runtime_config={
            "base_url": cfg.base_url,
            "api_path": "/chat/completions",
            "credential_key": cfg.credential_slot,
        },
        metadata={"cost_class": "paid_cloud"},
    )


def _authorization(model_call_id: str, *, task_id: str = "task-1"):
    """The shape ``core.model_orchestration.AuthorizedPaidCall`` presents to settlement."""
    capsule = SimpleNamespace(task_id=task_id)
    escalation = SimpleNamespace(model_id="claude-sonnet-4-5", capsule=capsule)
    return SimpleNamespace(model_call_id=model_call_id, escalation=escalation)


def _reserve(model_call_id: str, *, task_id: str = "task-1", usd: float = 0.25):
    reserve_spend(
        model_call_id=model_call_id,
        task_id=task_id,
        subtask_id="owner_explicit_model_pick",
        model_id="claude-sonnet-4-5",
        maximum_usd=usd,
        limits=LIMITS,
    )
    return _authorization(model_call_id, task_id=task_id)


def _ledger_actual_total() -> float:
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute("SELECT COALESCE(SUM(actual_usd), 0) FROM model_spend_reservations").fetchone()
        return float(row[0] or 0.0)
    finally:
        conn.close()


def _ledger_status(model_call_id: str) -> str:
    from storage.db import get_connection

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM model_spend_reservations WHERE model_call_id = ?", (model_call_id,)
        ).fetchone()
        return str(row[0]) if row else ""
    finally:
        conn.close()


# --- the headline defect -------------------------------------------------------------------------


def test_thirty_anthropic_calls_move_the_usd_ledger():
    manifest = _sonnet_manifest()
    response = SimpleNamespace(usage={"input_tokens": IN, "output_tokens": OUT})
    settled = 0
    for index in range(30):
        call_id = f"call-{index}"
        try:
            auth = _reserve(call_id, task_id=f"task-{index}")  # per-task cap must not mask the day cap
        except PermissionError:
            break
        pcr.settle_owner_pick_paid_call(auth, actual_usd=mfr._response_actual_usd(manifest, response))
        settled += 1

    total = _ledger_actual_total()
    assert settled == 30
    assert total > 0.0, "the ledger must move; $0.00 is the defect"
    # What the ledger now holds is what Anthropic will bill for those same 30 turns.
    assert total == pytest.approx(30 * SONNET_CALL_USD, abs=1e-6)
    assert total == pytest.approx(1.485, abs=1e-3)


def test_the_daily_usd_maximum_now_refuses_a_call():
    """With settlement stuck at $0.00 the day total never grew, so the cap could not be reached.
    Priced settlement makes it bind."""
    refused = ""
    for index in range(200):
        try:
            auth = _reserve(f"day-{index}", task_id=f"day-task-{index}")
        except PermissionError as exc:
            refused = str(exc)
            break
        pcr.settle_owner_pick_paid_call(auth, actual_usd=SONNET_CALL_USD)
    assert refused == "daily_spend_cap_exceeded"
    # The refusal projects the NEXT call at its per-call ceiling, so the day stops within one
    # reservation of the maximum rather than exactly on it.
    spent = _ledger_actual_total()
    assert LIMITS.daily_usd - LIMITS.per_call_usd <= spent <= LIMITS.daily_usd


def test_a_settled_call_records_the_published_rate_not_zero():
    manifest = _sonnet_manifest()
    auth = _reserve("call-single")
    pcr.settle_owner_pick_paid_call(
        auth, actual_usd=mfr._response_actual_usd(manifest, SimpleNamespace(usage={"input_tokens": IN, "output_tokens": OUT}))
    )
    assert _ledger_actual_total() == pytest.approx(SONNET_CALL_USD, abs=1e-9)
    assert _ledger_status("call-single") == "settled"


def test_an_unpriced_model_still_moves_the_ledger():
    """The whole point of the ceiling: an unknown price must not read as a free call."""
    cfg = config_for("anthropic")
    unknown = ModelProviderManifest(
        provider_name="anthropic-byok",
        model_name="claude-model-not-in-the-table",
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Provider",
        license_reference="user-managed",
        license_url_or_reference="user-managed",
        capabilities=["summarize"],
        runtime_config={"base_url": cfg.base_url, "credential_key": cfg.credential_slot},
        metadata={"cost_class": "paid_cloud"},
    )
    auth = _reserve("call-unknown")
    usd = mfr._response_actual_usd(unknown, SimpleNamespace(usage={"input_tokens": 1_000, "output_tokens": 100}))
    assert usd > 0.0
    pcr.settle_owner_pick_paid_call(auth, actual_usd=usd)
    assert _ledger_actual_total() == pytest.approx(usd, abs=1e-9)


# --- retries -------------------------------------------------------------------------------------


def test_a_failed_attempt_then_a_retry_keeps_uncertain_bill_held():
    auth = _reserve("call-retry")
    pcr.release_owner_pick_paid_call(auth, reason="call_failed")  # attempt 1 reached the provider
    pcr.settle_owner_pick_paid_call(auth, actual_usd=SONNET_CALL_USD)  # attempt 2 came back
    assert _ledger_actual_total() == pytest.approx(SONNET_CALL_USD, abs=1e-9)
    _assert_held("call-retry")


def test_a_priced_retry_does_not_drop_its_charge_against_an_ambiguous_reservation():
    """Known retry cost counts alongside the prior unresolved liability."""
    auth = _reserve("call-closed")
    pcr.release_owner_pick_paid_call(auth, reason="call_failed")
    assert _ledger_status("call-closed") == "billing_ambiguous"
    pcr.settle_owner_pick_paid_call(auth, actual_usd=SONNET_CALL_USD)
    assert _ledger_actual_total() > 0.0


def test_retry_usage_does_not_fabricate_prices_for_failed_attempts():
    auth = _reserve("call-retry-twice")
    pcr.release_owner_pick_paid_call(auth, reason="call_failed")
    pcr.release_owner_pick_paid_call(auth, reason="call_failed")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=SONNET_CALL_USD)
    assert _ledger_actual_total() == pytest.approx(SONNET_CALL_USD, abs=1e-9)
    _assert_held("call-retry-twice")


@pytest.mark.parametrize("reason", ["circuit_open", "provider_unhealthy", "prompt_budget_exceeded"])
def test_a_call_that_never_reached_the_provider_bills_nothing(reason):
    """These reasons mean no request was sent, so there is nothing to owe — charging for them is
    the other half of the defect (paying for work that never happened)."""
    auth = _reserve(f"call-{reason}")
    pcr.release_owner_pick_paid_call(auth, reason=reason)
    pcr.settle_owner_pick_paid_call(auth, actual_usd=0.0)
    assert _ledger_actual_total() == pytest.approx(0.0, abs=1e-9)
    assert _ledger_status(f"call-{reason}") == reason


def test_an_unsent_release_cannot_erase_a_prior_uncertain_liability():
    auth = _reserve("call-mixed")
    pcr.release_owner_pick_paid_call(auth, reason="call_failed")
    pcr.release_owner_pick_paid_call(auth, reason="circuit_open")
    pcr.settle_owner_pick_paid_call(auth, actual_usd=0.0)
    assert _ledger_actual_total() == pytest.approx(0.0, abs=1e-9)
    _assert_held("call-mixed")


def test_the_attempt_tally_is_dropped_once_the_reservation_closes():
    auth = _reserve("call-tally")
    pcr.release_owner_pick_paid_call(auth, reason="call_failed")
    assert "call-tally" in pcr._ATTEMPTS
    pcr.settle_owner_pick_paid_call(auth, actual_usd=SONNET_CALL_USD)
    assert "call-tally" not in pcr._ATTEMPTS


def test_attempt_charge_total_multiplies_only_the_unpriced_attempts():
    pcr.note_unbilled_paid_attempt(_authorization("acc"))
    pcr.note_unbilled_paid_attempt(_authorization("acc"))
    assert pcr.attempt_charge_total("acc", this_attempt_usd=0.01) == pytest.approx(0.03)
    pcr.forget_paid_attempts("acc")
    assert pcr.attempt_charge_total("acc", this_attempt_usd=0.01) == pytest.approx(0.01)


# --- fail-soft -------------------------------------------------------------------------------------


def test_settlement_never_raises_without_a_reservation_id():
    pcr.settle_owner_pick_paid_call(SimpleNamespace(model_call_id=""), actual_usd=1.0)
    pcr.release_owner_pick_paid_call(SimpleNamespace(model_call_id=""), reason="call_failed")


def test_settlement_never_raises_on_an_unknown_reservation():
    pcr.settle_owner_pick_paid_call(_authorization("never-reserved"), actual_usd=SONNET_CALL_USD)
    # The charge is real and is still recorded, rather than dropped because the row is missing.
    assert _ledger_actual_total() == pytest.approx(SONNET_CALL_USD, abs=1e-9)


def _assert_held(call_id, usd=0.25):
    from core.model_spend_ledger import get_spend_reservation

    row = get_spend_reservation(call_id)
    assert row.status == "billing_ambiguous"
    assert row.reserved_usd == pytest.approx(usd)


@pytest.mark.parametrize("usage", [{}, None])
def test_missing_usage_retains_budget_across_restart_and_blocks_next_call(usage):
    from core.model_spend_ledger import settle_spend
    from storage.db import reset_default_connection

    for i in range(4):
        auth = _reserve(f"unmetered-{i}")
        amount = mfr._response_actual_usd(_sonnet_manifest(), SimpleNamespace(usage=usage))
        assert amount is None
        pcr.settle_owner_pick_paid_call(auth, actual_usd=amount)
        _assert_held(f"unmetered-{i}")
    assert _ledger_actual_total() == 0  # unknown is held separately, never invented as a bill
    pcr._ATTEMPTS.clear()
    reset_default_connection()
    with pytest.raises(PermissionError, match="per_task_spend_cap_exceeded"):
        _reserve("blocked-unmetered")
    # Explicit reconciliation is the only operation that releases an ambiguous hold.
    settled = settle_spend("unmetered-0", actual_usd=0.0)
    assert settled.status == "settled" and settled.reserved_usd == 0
    _reserve("after-reconciliation")


def test_pricing_failure_is_unknown_not_zero(monkeypatch):
    monkeypatch.setattr(mfr, "_response_cost_estimate", lambda *_: None)
    assert mfr._response_actual_usd(_sonnet_manifest(), SimpleNamespace(usage={})) is None


def test_provider_explicit_zero_releases_budget():
    amount = mfr._response_actual_usd(_sonnet_manifest(), SimpleNamespace(usage={"cost": 0.0}))
    assert amount == 0.0
    pcr.settle_owner_pick_paid_call(_reserve("free-reported"), actual_usd=amount)
    assert _ledger_status("free-reported") == "settled"


def test_failed_dispatch_holds_budget_without_an_in_memory_attempt_tally():
    auth = _reserve("uncertain-failure")
    pcr.release_owner_pick_paid_call(auth, reason="call_failed")
    pcr._ATTEMPTS.clear()
    pcr.settle_owner_pick_paid_call(auth, actual_usd=SONNET_CALL_USD)
    _assert_held("uncertain-failure")
    assert _ledger_actual_total() == pytest.approx(SONNET_CALL_USD)
