"""The ONE account-bound balance observation, from setup through preflight to the dispatch reservation.

Reproduces the live refusal of 2026-09-16 02:26 (a balance observed 70 minutes earlier, never
renewed, MONEY_LIQUIDITY_UNVERIFIED on send) against the REAL money law and the SYNTHETIC strict
local service, then proves the repair: the dispatch boundary obtains a fresh observation through the
same door Settings and the composer preflight use, the money law judges it, and a failed read stays a
named refusal -- never a cached success, never a renewed timestamp, never a zero read as funds.
No live provider, no paid inference; every fund here is synthetic and labelled.
"""
from __future__ import annotations

import json
import time
import uuid
from types import SimpleNamespace

import pytest

from adapters.base_adapter import ModelRequest
from adapters.usepod_adapter import UsePodDispatchRefusedError
from core.usepod.monetary import MonetaryAuthorityRefusedError
from tests.usepod._usepod_doors import post as _post
from tests.usepod.strict_usepod_service import Listing, default_reply
from tests.usepod.test_usepod_money_law import (
    GRANT_NOTE,
    MARKET,
    MARKET_ID,
    CENTRAL,
    MODEL,
    _fingerprint,
    _liability,
    _mint_grant,
    _observe_balance,
    law,
)

OPENAI = "/proxy/{token}/v1/chat/completions"
BALANCE = "/proxy/{token}/balance"


def _record_path():
    from core.usepod import discovery

    return discovery._discovery_path(_fingerprint())


def _age_the_record(seconds: float) -> None:
    """Move the recorded balance observation into the past WITHOUT touching the provider: the
    reproduction of a Settings refresh nobody repeated."""
    path = _record_path()
    record = json.loads(path.read_text(encoding="utf-8"))
    record["balance"]["observed_at"] = time.time() - seconds
    path.write_text(json.dumps(record), encoding="utf-8")


def _request(prompt: str) -> ModelRequest:
    return ModelRequest(task_kind="chat", prompt=prompt, messages=[{"role": "user", "content": prompt}], max_output_tokens=64, output_mode="plain_text")


def _runtime_schema() -> None:
    """The law fixture re-points the default store at an empty file for money isolation; the owner doors
    and the dispatch path also record their invocations there, so the store needs the runtime schema."""
    from storage.migrations import run_migrations

    run_migrations(force=True)


def _adapter(service, authority):
    """The real UsePod lane on the real money law: route approval and lane registration through the
    production owner doors, the adapter built by the registry. The suite's isolation installs an
    UNAVAILABLE authority per test, so the production law is installed here under its own label --
    no test double touches these dispatches."""
    from core.model_registry import ModelRegistry
    from core.runtime_provider_defaults import register_chat_cloud_model
    from core.usepod.monetary import install_monetary_authority

    install_monetary_authority(authority, label=authority.label)
    _runtime_schema()
    status, payload = _post("/api/cloud/usepod/route-policy", {"mode": "marketplace-only"})
    assert status == 200, payload
    status, payload = _post("/api/cloud/usepod/approve-route", {"model_id": MODEL})
    assert status == 200, payload
    # The grant binds the route identity the approval permits, exactly as the money law reads it.
    route = "+".join(sorted(str(item) for item in payload["approved_route"]["allowed_route_classes"]))
    status, payload = _post("/api/cloud/usepod/lane", {"protocol": "openai", "transport_mode": "prepaid_token"})
    assert status == 200, payload
    assert register_chat_cloud_model("usepod", MODEL) == f"usepod-byok:{MODEL}"
    registry = ModelRegistry()
    manifest = registry.get_manifest("usepod-byok", MODEL)
    assert manifest is not None
    return registry.build_adapter(manifest), route


# --- the reproduction: the money law itself never renews an old observation -------------------------


def test_the_money_law_refuses_an_observation_older_than_its_window_and_names_the_age(law) -> None:
    authority, service, token = law
    fingerprint = _observe_balance(service, token)
    _mint_grant(provider_account=fingerprint)
    _age_the_record(70 * 60)   # the live refusal: observed at 22:15 UTC, sent at 23:26 UTC
    with pytest.raises(MonetaryAuthorityRefusedError) as caught:
        authority.reserve(_liability(f"old-{uuid.uuid4().hex[:8]}"))
    assert caught.value.code == "MONEY_LIQUIDITY_UNVERIFIED"
    detail = str(caught.value)
    assert "balance observation is 4" in detail and "at most 900 s" in detail, detail
    assert "nothing was sent or charged" in detail
    assert detail.count("MONEY_LIQUIDITY_UNVERIFIED") == 1, detail
    # The law read the record; it never wrote a newer time into it.
    record = json.loads(_record_path().read_text(encoding="utf-8"))
    assert time.time() - record["balance"]["observed_at"] > 60 * 60


def test_a_never_observed_and_a_failed_read_are_named_not_treated_as_zero(law) -> None:
    authority, service, token = law
    from core import credential_store
    from core.cloud_providers import USEPOD_ORIGIN_SLOT
    from core.usepod import discovery

    credential_store.store_credential("llm.cloud.usepod", token)
    credential_store.store_credential(USEPOD_ORIGIN_SLOT, service.origin)
    _mint_grant(provider_account=_fingerprint())
    with pytest.raises(MonetaryAuthorityRefusedError) as never:
        authority.reserve(_liability(f"never-{uuid.uuid4().hex[:8]}"))
    assert never.value.code == "MONEY_LIQUIDITY_UNVERIFIED" and "never been observed" in str(never.value)
    # A read that came back without the documented field is recorded as that failure.
    service.faults["balance_payload"] = {"credit_balance": 0}
    observation = discovery.observe_balance(max_age_seconds=None)
    assert (observation["state"], observation["evidence"], observation.get("usdc_balance_microunits")) == ("field_absent", "live_read", None)
    with pytest.raises(MonetaryAuthorityRefusedError) as absent:
        authority.reserve(_liability(f"absent-{uuid.uuid4().hex[:8]}"))
    assert absent.value.code == "MONEY_LIQUIDITY_UNVERIFIED" and "field_absent" in str(absent.value)


# --- the repair: the dispatch boundary obtains the observation through the one door ----------------


def test_a_stale_observation_is_refreshed_at_the_dispatch_boundary_and_the_call_is_sent_once(law) -> None:
    authority, service, token = law
    fingerprint = _observe_balance(service, token)
    adapter, route = _adapter(service, authority)
    _mint_grant(provider_account=fingerprint, routes=(route,))
    _age_the_record(70 * 60)
    reads_before = len(service.requests_to(BALANCE))

    response = adapter.invoke(_request("Which weekday comes three days after Thursday? One word."))

    [arrived] = service.requests_to(OPENAI)
    assert response.output_text == default_reply(json.loads(arrived["body"]), "openai").text
    assert len(service.requests_to(BALANCE)) == reads_before + 1, "exactly one read-only balance GET before the send"
    receipt = response.provider_metadata["receipt"]
    verified = receipt["balance_verified"]
    assert (verified["state"], verified["evidence"], verified["usdc_balance_microunits"]) == ("reported", "live_read", 80_000_000)
    assert verified["age_seconds"] == 0.0
    assert receipt["inference"]["state"] == "completed"
    assert receipt["monetary_authority"] == authority.label
    # The refreshed observation is the record every other surface reads.
    record = json.loads(_record_path().read_text(encoding="utf-8"))
    assert time.time() - record["balance"]["observed_at"] < 60


def test_a_fresh_observation_is_reused_and_held_debits_still_count_against_it(law) -> None:
    authority, service, token = law
    from core.usepod import discovery

    fingerprint = _observe_balance(service, token)
    adapter, route = _adapter(service, authority)
    _mint_grant(provider_account=fingerprint, routes=(route,))
    reads_before = len(service.requests_to(BALANCE))
    first = adapter.invoke(_request("Name a river in Peru."))
    assert first.provider_metadata["receipt"]["balance_verified"]["evidence"] == "cache"
    assert len(service.requests_to(BALANCE)) == reads_before, "a reported balance under the reuse window is not re-read"
    bound = int(first.provider_metadata["receipt"]["cost"]["liability_bound_atomic"])
    # An account that covers one such request but not two, observed fresh once. The observation is
    # evidence; the money law's own arithmetic over debits held since it stays the authority: the
    # second request reserves, the third is refused as insufficient -- never re-read into a green.
    service.tokens[token] = bound + bound // 2
    fresh = discovery.observe_balance(max_age_seconds=None)
    assert (fresh["evidence"], fresh["usdc_balance_microunits"]) == ("live_read", bound + bound // 2)
    _mint_grant(provider_account=fingerprint, routes=(route,))
    _mint_grant(provider_account=fingerprint, routes=(route,))
    second = adapter.invoke(_request("Name a river in Peru."))
    assert second.provider_metadata["receipt"]["balance_verified"]["evidence"] == "cache"
    with pytest.raises(UsePodDispatchRefusedError) as caught:
        adapter.invoke(_request("Name a river in Peru."))
    assert caught.value.code == "MONEY_LIQUIDITY_INSUFFICIENT"
    assert caught.value.provider_evidence["receipt"]["inference"]["state"] == "not_sent"
    assert len(service.requests_to(OPENAI)) == 2
    assert len(service.requests_to(BALANCE)) == reads_before + 1


def test_a_failed_live_read_at_dispatch_is_a_named_refusal_before_any_byte(law) -> None:
    authority, service, token = law
    fingerprint = _observe_balance(service, token)
    adapter, route = _adapter(service, authority)
    _mint_grant(provider_account=fingerprint, routes=(route,))
    _age_the_record(70 * 60)
    service.faults["balance_payload"] = {"usdc_balance": "12.5"}   # a shape the contract refuses
    with pytest.raises(UsePodDispatchRefusedError) as caught:
        adapter.invoke(_request("Is 223 prime?"))
    assert caught.value.code == "MONEY_LIQUIDITY_UNVERIFIED"
    assert str(caught.value) == "usepod_dispatch_refused:MONEY_LIQUIDITY_UNVERIFIED"
    receipt = caught.value.provider_evidence["receipt"]
    assert receipt["inference"]["state"] == "not_sent"
    assert receipt["balance_verified"]["state"] == "field_malformed"
    assert receipt["balance_verified"]["evidence"] == "live_read"
    assert "field_malformed" in receipt["monetary_refusal_detail"]
    assert "nothing was sent or charged" in receipt["monetary_refusal_detail"]
    assert service.requests_to(OPENAI) == []
    # The failed read replaced the stale success in the record: no cached success survives to force a green.
    record = json.loads(_record_path().read_text(encoding="utf-8"))
    assert record["balance"]["state"] == "field_malformed"


def test_an_empty_account_is_insufficient_not_unverified(law) -> None:
    authority, service, token = law
    fingerprint = _observe_balance(service, token)
    adapter, route = _adapter(service, authority)
    _mint_grant(provider_account=fingerprint, routes=(route,))
    service.tokens[token] = 0
    _age_the_record(70 * 60)
    with pytest.raises(UsePodDispatchRefusedError) as caught:
        adapter.invoke(_request("Is 227 prime?"))
    assert caught.value.code == "MONEY_LIQUIDITY_INSUFFICIENT"
    receipt = caught.value.provider_evidence["receipt"]
    assert (receipt["balance_verified"]["state"], receipt["balance_verified"]["usdc_balance_microunits"]) == ("reported", 0)
    assert receipt["inference"]["state"] == "not_sent"
    assert service.requests_to(OPENAI) == []


def test_another_credentials_observation_never_funds_this_one(law) -> None:
    authority, service, token = law
    fingerprint = _observe_balance(service, token)
    _mint_grant(provider_account=fingerprint)
    from core.usepod import discovery

    other = discovery.balance_observation("upc_" + "f" * 32)
    assert other["state"] == "not_observed"
    with pytest.raises(MonetaryAuthorityRefusedError) as caught:
        authority.reserve(_liability(f"other-{uuid.uuid4().hex[:8]}", account_ref="upc_" + "f" * 32))
    assert caught.value.code in {"MONEY_LIQUIDITY_UNVERIFIED", "MONEY_AUTHORITY_INVALID"}


# --- setup and preflight share the door ----------------------------------------------------------------


def test_the_settings_test_records_the_balance_it_reads(law) -> None:
    authority, service, token = law
    from core import credential_store
    from core.cloud_providers import USEPOD_ORIGIN_SLOT
    from core.usepod import discovery

    credential_store.store_credential("llm.cloud.usepod", token)
    credential_store.store_credential(USEPOD_ORIGIN_SLOT, service.origin)
    assert discovery.balance_observation(_fingerprint())["state"] == "not_observed"
    assert discovery.probe_credential() == ("ok", "authorized", 200)
    observed = discovery.balance_observation(_fingerprint())
    assert (observed["state"], observed["usdc_balance_microunits"]) == ("reported", 80_000_000)
    _mint_grant(provider_account=_fingerprint())
    reservation = authority.reserve(_liability(f"tested-{uuid.uuid4().hex[:8]}"))
    assert reservation.reservation_id.startswith("mli:")


def test_the_owner_balance_endpoint_reads_once_and_reuses_within_the_window(law) -> None:
    authority, service, token = law
    _runtime_schema()
    status, payload = _post("/api/cloud/usepod/balance", {})
    assert (status, payload["state"], payload["usdc_balance"]) == (200, "no_token", None)
    _observe_balance(service, token)
    reads = len(service.requests_to(BALANCE))
    status, cached = _post("/api/cloud/usepod/balance", {})
    assert status == 200 and cached["evidence"] == "cache" and len(service.requests_to(BALANCE)) == reads
    assert (cached["usdc_balance_microunits"], cached["usdc_balance"], cached["unit"]) == (80_000_000, "80.000000", "usdc_microunit")
    assert cached["fingerprint"] == _fingerprint() and token not in json.dumps(cached)
    status, fresh = _post("/api/cloud/usepod/balance", {"max_age_seconds": 0})
    assert status == 200 and fresh["evidence"] == "live_read" and len(service.requests_to(BALANCE)) == reads + 1
    status, refused = _post("/api/cloud/usepod/balance", {"max_age_seconds": 5000})
    assert (status, refused["code"]) == (400, "max_age_invalid")
    status, refused = _post("/api/cloud/usepod/balance", {"token": "x"})
    assert (status, refused["code"]) == (400, "unknown_fields")
    service.faults["balance_payload"] = {"usdc_balance": -40}
    status, failed = _post("/api/cloud/usepod/balance", {"max_age_seconds": 0})
    assert status == 200 and failed["state"] == "field_malformed" and failed["usdc_balance"] is None
    assert failed["previous"]["usdc_balance_microunits"] == 80_000_000


def test_display_money_is_decimal_usdc_never_the_raw_microunit_count(law) -> None:
    from core.web.api.service import _usepod_balance_public

    shown = _usepod_balance_public({"state": "reported", "usdc_balance_microunits": 3_000_000, "observed_at": 1.0, "age_seconds": 0.0, "evidence": "cache", "fingerprint": "upc_x"})
    assert shown["usdc_balance"] == "3.000000" and shown["usdc_balance_microunits"] == 3_000_000
    tiny = _usepod_balance_public({"state": "reported", "usdc_balance_microunits": 5_233, "observed_at": 1.0, "age_seconds": 0.0, "evidence": "cache", "fingerprint": "upc_x"})
    assert tiny["usdc_balance"] == "0.005233"


def test_the_footer_says_not_sent_for_a_refusal_before_send() -> None:
    from core.response_provenance import format_provenance_footer

    accounting = {"calls": 1, "completed_calls": 0, "failed_calls": 1, "pending_calls": 0, "failed_error_classes": [], "providers": ["usepod-byok"], "lanes": ["cloud"], "models": ["gpt-6-astra"], "tools": [], "served_usage": {}}
    refused = {"model_execution": {"source": "selected_model_blocked", "used_model": False, "details": {"requested_model": "usepod:gpt-6-astra", "block_reason": "usepod_dispatch_refused:MONEY_LIQUIDITY_UNVERIFIED", "attempted": ["usepod-byok:gpt-6-astra"]}}, "model_call_accounting": accounting}
    footer = format_provenance_footer(refused)
    assert "gpt-6-astra" in footer and "not sent" in footer and "refused before send: MONEY_LIQUIDITY_UNVERIFIED" in footer, footer
    assert "model attempted" not in footer and "no usable answer" not in footer
    assert "1 cloud model attempt failed" in footer
    # A call that DID go out and failed keeps the attempted wording: the control this reading must not widen.
    failed = {"model_execution": {"source": "selected_model_blocked", "used_model": False, "details": {"requested_model": "usepod:gpt-6-astra", "block_reason": "selected_model_call_failed", "attempted": ["usepod-byok:gpt-6-astra"]}}, "model_call_accounting": accounting}
    control = format_provenance_footer(failed)
    assert "model attempted" in control and "no usable answer" in control and "not sent" not in control
    # An x402 journal state can follow a payment: never read as "not sent".
    journal = {"model_execution": {"source": "selected_model_blocked", "used_model": False, "details": {"requested_model": "usepod:gpt-6-astra", "block_reason": "usepod_dispatch_refused:resend_bytes_differ_from_paid_request", "attempted": ["usepod-byok:gpt-6-astra"]}}, "model_call_accounting": accounting}
    assert "not sent" not in format_provenance_footer(journal)


def test_the_degraded_answer_says_not_sent_and_never_offers_auto_for_a_balance_defect() -> None:
    from core.agent_runtime.memory_runtime import chat_surface_honest_degraded_response

    agent = SimpleNamespace(_live_info_mode=lambda *a, **kw: "")
    for code, phrase in (
        ("MONEY_LIQUIDITY_UNVERIFIED", "could not verify your account balance"),
        ("MONEY_LIQUIDITY_INSUFFICIENT", "does not cover this request's maximum cost"),
        ("MONEY_AUTHORITY_EXPIRED", "spending approval expired"),
        ("price_stale", "price feed is stale"),
    ):
        execution = SimpleNamespace(
            source="selected_model_blocked",
            details={
                "requested_model": "usepod:gpt-6-astra",
                "block_reason": f"usepod_dispatch_refused:{code}",
                "reason": f"usepod_dispatch_refused:{code}",
                "model_was_attempted": True,
                "attempted": ["usepod-byok:gpt-6-astra"],
                "ranked_candidates": [],
            },
        )
        text = chat_surface_honest_degraded_response(agent, execution)
        assert phrase in text, (code, text)
        assert "nothing was sent and nothing was charged" in text
        assert "did not return a usable reply" not in text and "Auto" not in text, (code, text)
    # Controls: an after-send transport failure on a UsePod pin, and an x402 journal state that may
    # follow a payment, keep the lane's own wording -- "nothing was charged" is never claimed for them.
    for reason in ("usepod_transport:transfer_ended_without_response dispatch=sent_outcome_unknown", "usepod_dispatch_refused:resend_bytes_differ_from_paid_request"):
        execution = SimpleNamespace(source="selected_model_blocked", details={"requested_model": "usepod:gpt-6-astra", "block_reason": reason, "reason": reason, "model_was_attempted": True, "attempted": ["usepod-byok:gpt-6-astra"], "ranked_candidates": []})
        text = chat_surface_honest_degraded_response(agent, execution)
        assert "nothing was charged" not in text, (reason, text)


# --- the per-chat selection owner: choosing Auto releases the server's cloud pin -----------------------


def test_choosing_auto_for_a_chat_releases_its_server_pin_through_the_pin_door(law) -> None:
    """The page posts model=auto with the chat's session id when the operator picks Auto, Local Only
    or a local model, so the persisted per-chat selection can never keep painting a cloud pin the
    composer left behind. The door accepts it with or without a stored provider key."""
    authority, service, token = law
    from core.cloud_escalation_policy import chat_model_selection

    _runtime_schema()
    session = "openclaw:" + "a" * 20
    # Without any provider key stored: Auto is still a valid selection for a chat.
    status, payload = _post("/api/cloud/model", {"model": "auto", "session_id": session, "selection_revision": 5})
    assert status == 200 and payload["ok"] is True and payload["model"] == "", payload
    assert chat_model_selection(session) == {"model": "", "provider": ""}
    # With a pinned UsePod model on the server, the release clears exactly this chat's pin.
    _observe_balance(service, token)
    _adapter(service, authority)
    status, payload = _post("/api/cloud/model", {"model": MODEL, "provider": "usepod", "session_id": session, "selection_revision": 6, "confirm_paid": True})
    assert status == 200 and payload["provider"] == "usepod", payload
    assert chat_model_selection(session) == {"model": MODEL, "provider": "usepod"}
    other = "openclaw:" + "b" * 20
    status, payload = _post("/api/cloud/model", {"model": MODEL, "provider": "usepod", "session_id": other, "selection_revision": 7, "confirm_paid": True})
    assert status == 200, payload
    status, payload = _post("/api/cloud/model", {"model": "auto", "session_id": session, "selection_revision": 8})
    assert status == 200 and payload["model"] == "", payload
    assert chat_model_selection(session) == {"model": "", "provider": ""}
    assert chat_model_selection(other) == {"model": MODEL, "provider": "usepod"}, "another chat's pin is untouched"
    # A stale release (older revision) cannot undo a newer selection.
    status, payload = _post("/api/cloud/model", {"model": MODEL, "provider": "usepod", "session_id": session, "selection_revision": 9, "confirm_paid": True})
    assert status == 200, payload
    status, payload = _post("/api/cloud/model", {"model": "auto", "session_id": session, "selection_revision": 3})
    assert status == 503 or chat_model_selection(session)["model"] == MODEL, (status, payload, chat_model_selection(session))
