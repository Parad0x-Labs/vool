"""UsePod registration seams: the Settings door, the connection test, credential verification, lane
registration, the owner-action guard suite, the model catalog, the pricing owner and routing events.

Everything runs against the SYNTHETIC strict local service or touches no network at all. Tokens are
generated per test; model names are invented.
"""
from __future__ import annotations

import json
import pickle
import uuid
from types import SimpleNamespace

import pytest

from core.usepod import descriptor, pricing, routing
from tests.usepod._usepod_doors import get, post
from tests.usepod.strict_usepod_service import Listing

MODEL = "cantilever-22b-synth"
MARKET_ID = "0b9a8c7d-6e5f-4a3b-8c2d-1e0f2a3b4c5d"
MARKET = (380_000, 1_140_000)
CENTRAL = ("together", 560_000, 1_700_000)


@pytest.fixture
def service_and_token(usepod_home, usepod_service):
    token = str(uuid.uuid4())
    service = usepod_service(
        tokens={token: 9_000_000},
        models={MODEL: [Listing("marketplace", MARKET_ID, *MARKET), Listing("centralized", *CENTRAL)]},
    )
    return service, token


def _store(service, token: str) -> tuple[int, dict]:
    return post("/api/settings/credentials", {"provider": "usepod", "value": token, "base_url": service.origin})


def _approve() -> dict:
    status, payload = post("/api/cloud/usepod/route-policy", {"mode": "marketplace-only"})
    assert status == 200, payload
    status, payload = post("/api/cloud/usepod/approve-route", {"model_id": MODEL})
    assert status == 200, payload
    return payload["approved_route"]


# --- the Settings door ------------------------------------------------------------------------------------


def test_a_non_default_origin_is_bound_only_when_chosen_and_a_paste_cannot_steer_the_token(service_and_token, usepod_service) -> None:
    from core import credential_store

    service, token = service_and_token
    elsewhere = usepod_service(tokens={token: 1})
    status, payload = post("/api/settings/credentials", {"provider": "usepod", "value": f"{service.origin}/proxy/{token}/v1"})
    assert (status, payload.get("code")) == (400, "non_default_origin_must_be_chosen_explicitly")
    status, payload = post(
        "/api/settings/credentials", {"provider": "usepod", "value": f"{elsewhere.origin}/proxy/{token}/v1", "base_url": service.origin}
    )
    assert (status, payload.get("code")) == (400, "pasted_origin_differs_from_chosen_origin")
    assert not credential_store.get_credential("llm.cloud.usepod")

    status, payload = post("/api/settings/credentials", {"provider": "usepod", "value": f"{service.origin}/proxy/{token}/v1", "base_url": service.origin})
    assert status == 200, payload
    assert payload["credential_fingerprint"] == descriptor.credential_fingerprint(service.origin, token)
    assert (payload["origin"], payload["origin_is_default"], payload["token_shape"]) == (service.origin, False, "uuid")
    assert credential_store.get_credential("llm.cloud.usepod") == token
    assert credential_store.get_credential("llm.cloud.usepod_origin") == service.origin
    assert token not in json.dumps(payload) and token not in json.dumps(get("/api/settings/credentials"))
    assert service.requests == [] and elsewhere.requests == []


def test_removing_the_credential_removes_the_origin_chosen_with_it(service_and_token) -> None:
    from core import credential_store

    service, token = service_and_token
    assert _store(service, token)[0] == 200
    status, payload = post("/api/settings/credentials", {"provider": "usepod", "delete": True})
    assert status == 200 and payload["removed"] is True, payload
    assert not credential_store.get_credential("llm.cloud.usepod")
    assert not credential_store.get_credential("llm.cloud.usepod_origin")


def test_the_connection_test_reads_the_balance_on_the_token_path_and_never_sends_a_bearer_header(service_and_token, monkeypatch) -> None:
    from core import cloud_connection_state

    service, token = service_and_token
    assert _store(service, token)[0] == 200
    monkeypatch.setattr(cloud_connection_state, "_last_probe_ts", {})
    status, payload = post("/api/cloud/test", {"provider": "usepod"})
    assert status == 200 and (payload["state"], payload["http_status"]) == ("ok", 200), payload
    [probe] = service.requests
    assert (probe["method"], probe["path"]) == ("GET", "/proxy/{token}/balance")
    assert "authorization" not in probe["headers"]

    stranger = str(uuid.uuid4())
    assert _store(service, stranger)[0] == 200
    monkeypatch.setattr(cloud_connection_state, "_last_probe_ts", {})
    status, payload = post("/api/cloud/test", {"provider": "usepod"})
    assert (payload["state"], payload["detail"], payload["http_status"]) == ("failed", "unauthorized", 401)
    assert token not in json.dumps(payload) and stranger not in json.dumps(payload)


def test_credential_verification_places_only_a_path_safe_token_and_sends_no_authorization_header(service_and_token) -> None:
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.verification import STATUS_REFUSED, STATUS_VERIFIED, verify_provider_credential

    service, token = service_and_token
    assert _store(service, token)[0] == 200
    usepod = default_registry().get("usepod")
    assert (usepod.auth_style, usepod.verify_endpoint) == ("url_path_token", f"{service.origin}/proxy/{{credential}}/balance")
    outcome = verify_provider_credential(token, usepod)
    assert (outcome.status, outcome.http_status) == (STATUS_VERIFIED, 200)
    [request] = service.requests
    assert request["path"] == "/proxy/{token}/balance" and "authorization" not in request["headers"]
    for hostile in ("../../v1/marketplace/models", f"{token}/../balance", f"{token}?admin=1", f"{token}%2F..", "short-token"):
        assert verify_provider_credential(hostile, usepod).status == STATUS_REFUSED
    assert len(service.requests) == 1
    assert token not in json.dumps(outcome.to_dict())


def test_a_proxy_target_never_prints_or_pickles_its_token() -> None:
    token = str(uuid.uuid4())
    target = descriptor.prepaid_target(origin="https://api.usepod.ai", token=token, surface_path="/v1/models")
    assert token not in repr(target) and token not in str(target) and token not in target.redacted_url
    assert target.wire_url() == f"https://api.usepod.ai/proxy/{token}/v1/models"
    with pytest.raises(TypeError):
        pickle.dumps(target)


# --- lanes and owner actions ------------------------------------------------------------------------------


def test_a_registered_lane_carries_the_origin_and_slot_never_the_token_and_follows_the_lane_preference(service_and_token) -> None:
    from adapters.usepod_adapter import UsePodAdapter
    from core.model_registry import ModelRegistry
    from core.runtime_provider_defaults import register_chat_cloud_model
    from storage.model_provider_manifest import list_provider_manifests

    service, token = service_and_token
    assert _store(service, token)[0] == 200
    approved = _approve()
    assert register_chat_cloud_model("usepod", MODEL) == f"usepod-byok:{MODEL}"
    manifest = ModelRegistry().get_manifest("usepod-byok", MODEL)
    assert manifest.adapter_type == "usepod"
    assert {key: manifest.runtime_config[key] for key in ("base_url", "api_path", "protocol", "transport_mode", "credential_key")} == {
        "base_url": service.origin,
        "api_path": "/v1/chat/completions",
        "protocol": "openai",
        "transport_mode": "prepaid_token",
        "credential_key": "llm.cloud.usepod",
    }
    assert (manifest.metadata["cost_class"], manifest.metadata["context_window"]) == ("paid_cloud", 0)
    assert isinstance(ModelRegistry().build_adapter(manifest), UsePodAdapter)

    status, changed = post("/api/cloud/usepod/lane", {"protocol": "anthropic", "transport_mode": "x402"})
    assert status == 200 and changed["refreshed_lanes"] == [f"usepod-byok:{MODEL}"], changed
    manifest = ModelRegistry().get_manifest("usepod-byok", MODEL)
    assert (manifest.runtime_config["protocol"], manifest.runtime_config["api_path"], manifest.runtime_config["transport_mode"]) == (
        "anthropic",
        "/v1/messages",
        "x402",
    )
    assert routing.load_route_state().bounds[MODEL].approval_id == approved["approval_id"]
    for body in ({"protocol": "grpc"}, {"protocol": "openai", "wire": "json"}):
        assert post("/api/cloud/usepod/lane", body) == (400, {"error": "lane refused", "code": "lane_invalid"})
    rows = json.dumps([row.model_dump() for row in list_provider_manifests()], default=str)
    assert token not in rows


@pytest.mark.parametrize(
    ("path", "body", "options", "status", "expected"),
    [
        pytest.param("/api/cloud/usepod/refresh", {}, {"client_host": "203.0.113.9"}, 403, {"error": "owner_local_required"}, id="not-owner-local"),
        pytest.param(
            "/api/cloud/usepod/route-policy",
            {"mode": "marketplace-only"},
            {"headers": {"content-type": "application/json", "origin": "https://pages.example.test"}},
            403,
            {"error": "cross-origin request not allowed"},
            id="cross-origin",
        ),
        pytest.param("/api/cloud/usepod/route-policy", {"mode": "marketplace-only"}, {"headers": {"content-type": "text/plain"}}, 415, {}, id="not-json"),
        pytest.param("/api/cloud/usepod/route-policy", {"mode": "auto"}, {}, 400, {"code": "auto_requires_explicit_fallback_permission"}, id="auto-without-fallback-permission"),
        pytest.param("/api/cloud/usepod/route-policy", {"mode": "marketplace-only", "surge_pricing": True}, {}, 400, {"code": "unknown_policy_fields"}, id="unknown-policy-field"),
        pytest.param("/api/cloud/usepod/approve-route", {}, {}, 400, {"code": "model_id_required"}, id="approve-without-model"),
        pytest.param("/api/cloud/usepod/approve-route", {"model_id": MODEL, "force": True}, {}, 400, {"code": "unknown_fields"}, id="approve-with-extra-field"),
        pytest.param("/api/cloud/usepod/refresh", {"everything": True}, {}, 400, {"code": "unknown_fields"}, id="refresh-with-fields"),
        pytest.param("/api/cloud/usepod/withdraw-funds", {}, {}, 404, {"code": "unknown_action"}, id="unknown-action"),
    ],
)
def test_owner_actions_hold_the_post_guard_suite(usepod_home, path, body, options, status, expected) -> None:
    got_status, payload = post(path, body, **options)
    assert got_status == status, payload
    assert {key: payload.get(key) for key in expected} == expected


def test_approving_a_route_the_feed_cannot_support_is_a_typed_conflict(service_and_token) -> None:
    service, token = service_and_token
    assert _store(service, token)[0] == 200
    assert post("/api/cloud/usepod/route-policy", {"mode": "marketplace-only"})[0] == 200
    status, payload = post("/api/cloud/usepod/approve-route", {"model_id": "not-listed-anywhere-synth"})
    assert (status, payload["code"]) == (409, "model_not_listed")
    service.feed_status = 503
    pricing._cache_path().unlink(missing_ok=True)
    status, payload = post("/api/cloud/usepod/approve-route", {"model_id": MODEL})
    assert (status, payload["code"]) == (409, "price_feed_unavailable")
    assert routing.load_route_state().bounds == {}


def test_the_model_catalog_lists_exact_route_prices_with_their_provenance(service_and_token) -> None:
    from core.cloud_providers import AUTH_BEARER, AUTH_URL_PATH_TOKEN

    service, token = service_and_token
    assert _store(service, token)[0] == 200
    empty = get("/api/cloud/models", {"provider": ["usepod"]})
    assert (empty["models"], empty["price_source"]) == ([], {"state": "no_cached_snapshot"})

    listed = get("/api/cloud/models", {"provider": ["usepod"], "refresh": ["1"]})
    assert listed["refresh"] == {"state": "fetched"}
    [row] = listed["models"]
    assert (row["id"], row["usable_for_spend"], row["prompt_usd_per_m"], row["completion_usd_per_m"]) == (MODEL, True, 0.38, 1.14)
    market = row["route_prices"]["marketplace"]
    assert (market["input_microunits_per_million"], market["output_microunits_per_million"]) == MARKET
    assert [(price["provider"], price["input_microunits_per_million"]) for price in row["route_prices"]["centralized"]] == [(CENTRAL[0], CENTRAL[1])]
    source = listed["price_source"]
    assert (source["origin"], source["evidence"], source["stale"], len(source["body_sha256"])) == (service.origin, "cache", False, 64)

    service.feed_status = 500
    degraded = get("/api/cloud/models", {"provider": ["usepod"], "refresh": ["1"]})
    assert degraded["refresh"] == {"state": "unavailable", "error_code": "feed_http_error"}
    assert [item["id"] for item in degraded["models"]] == [MODEL]

    providers = {item["id"]: item for item in get("/api/cloud/providers")["providers"]}
    assert providers["usepod"]["auth_placement"] == AUTH_URL_PATH_TOKEN
    assert {item["auth_placement"] for pid, item in providers.items() if pid != "usepod"} == {AUTH_BEARER}
    assert token not in json.dumps(listed) + json.dumps(degraded)


# --- the pricing owner and routing events ------------------------------------------------------------------


def test_a_paid_reservation_is_refused_until_the_route_is_approved_then_sized_at_the_approved_ceiling(service_and_token) -> None:
    from core.model_pricing import (
        BASIS_APPROVED_CEILING,
        estimate_call_usd,
        lookup_model_price,
        reservation_price_refusal,
    )
    from core.paid_call_reservation import _reservation_ceiling_usd

    service, token = service_and_token
    assert _store(service, token)[0] == 200
    limits = SimpleNamespace(per_call_usd=0.25)
    assert reservation_price_refusal("usepod", MODEL) == "usepod_route_not_approved"
    with pytest.raises(PermissionError, match="usepod_route_not_approved"):
        _reservation_ceiling_usd(provider_id="usepod", model_id=MODEL, prompt_tokens=100, completion_tokens=100, limits=limits)
    assert reservation_price_refusal("openai", "bearer-lane-synth") == ""

    _approve()
    assert reservation_price_refusal("usepod", MODEL) == ""
    price = lookup_model_price("usepod", MODEL)
    assert (price.basis, price.known) == (BASIS_APPROVED_CEILING, False)
    assert (price.prompt_usd_per_token, price.completion_usd_per_token) == pytest.approx((MARKET[0] / 1e12, MARKET[1] / 1e12))
    estimate = estimate_call_usd(provider_id="usepod", model_id=MODEL, prompt_tokens=1_000, completion_tokens=500)
    assert estimate.basis == BASIS_APPROVED_CEILING and "approved price ceiling" in estimate.describe()
    assert estimate.usd == pytest.approx((1_000 * MARKET[0] + 500 * MARKET[1]) / 1e12)
    assert _reservation_ceiling_usd(provider_id="usepod", model_id=MODEL, prompt_tokens=1_000, completion_tokens=500, limits=limits) > 0

    routing._store_path().write_text("{not json", encoding="utf-8")
    assert reservation_price_refusal("usepod", MODEL) == "route_store_unreadable"


def test_routing_events_carry_only_the_bounded_redacted_receipt() -> None:
    from core.memory_first_router import _provider_receipt

    token = str(uuid.uuid4())
    receipt = {"schema": "vool.usepod.receipt.v1", "endpoint": "/proxy/{token}/v1/chat/completions", "note": f"https://api.usepod.ai/proxy/{token}/v1"}
    rendered = _provider_receipt({"usepod": {"envelope": "x" * 100_000}, "receipt": receipt})
    assert rendered["endpoint"] == "/proxy/{token}/v1/chat/completions"
    assert token not in json.dumps(rendered)
    oversized = {"blob": "y" * 20_000}
    assert _provider_receipt({"receipt": oversized}) == {"state": "receipt_too_large_for_event", "bytes": len(json.dumps(oversized, sort_keys=True))}
    assert _provider_receipt({"usepod": {}}) is None and _provider_receipt(None) is None


def test_settlement_uses_an_adapter_cost_estimate_only_when_it_is_well_formed() -> None:
    from core.memory_first_router import _adapter_supplied_cost_estimate

    manifest = SimpleNamespace(model_name="cantilever-22b-synth")
    good = {
        "usd": 0.000026,
        "known": False,
        "basis": "usepod_reported_usage_at_approved_ceiling",
        "source": "upper bound",
        "provider_id": "usepod",
        "model_id": "cantilever-22b-synth",
        "prompt_tokens": 21,
        "completion_tokens": 13,
    }
    estimate = _adapter_supplied_cost_estimate(manifest, SimpleNamespace(provider_metadata={"cost_estimate": good}))
    assert (estimate.usd, estimate.known, estimate.basis, estimate.prompt_tokens) == (0.000026, False, "usepod_reported_usage_at_approved_ceiling", 21)
    for hostile in ({**good, "usd": -1}, {**good, "usd": float("nan")}, {**good, "usd": "lots"}, {"known": True}, "not-a-mapping", None):
        assert _adapter_supplied_cost_estimate(manifest, SimpleNamespace(provider_metadata={"cost_estimate": hostile})) is None
    assert _adapter_supplied_cost_estimate(manifest, SimpleNamespace()) is None
