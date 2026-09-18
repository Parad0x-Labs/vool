"""Route policy, owner-approved price bounds, pre-dispatch revalidation and the verdict on the route
that answered. Every snapshot here is SYNTHETIC (built in the test) except where the RECORDED fixture
is named. Model names are invented for these tests and appear nowhere in production code.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.usepod import pricing, routing

ORIGIN = "https://api.usepod.ai"
MARKET_UUID = "1f0e2d3c-4b5a-4968-8776-5a4b3c2d1e0f"
RELAY_UUID = "9a8b7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d"


def _row(model_id: str, *, market=(640_000, 1_200_000), count=3, centralized=(("together", 900_000, 2_000_000), ("openai", 1_100_000, 2_400_000)), mode="per_token"):
    providers = [{"provider": name, "input_per_1m": i, "output_per_1m": o} for name, i, o in centralized]
    best_in = market[0] if count and market else (min(p[1] for p in centralized) if centralized else None)
    best_out = market[1] if count and market else (min(p[2] for p in centralized) if centralized else None)
    return {
        "model_id": model_id,
        "pricing_mode": mode,
        "cheapest_input_per_1m": best_in,
        "cheapest_output_per_1m": best_out,
        "centralized_input_per_1m": min((p[1] for p in centralized), default=None),
        "centralized_output_per_1m": min((p[2] for p in centralized), default=None),
        "centralized_providers": providers,
        "marketplace_provider_count": count,
        "marketplace_total_tps": 0.0,
        "uncensored": False,
    }


def _snap(*rows, fetched_at: float = 1_000.0):
    return pricing.parse_marketplace_feed(
        {"models": list(rows)}, source="synthetic", origin=ORIGIN, fetched_at=fetched_at, ttl_seconds=120,
        evidence=pricing.EVIDENCE_FIXTURE, fixture_label="SYNTHETIC routing snapshot",
    )


@pytest.mark.parametrize(
    ("policy", "code"),
    [
        (dict(mode="marketplace-only", pinned_providers=("openai",)), "pin_conflicts_with_marketplace_only"),
        (dict(mode="auto", pinned_providers=("openai",), allow_centralized_fallback=True, max_input_microunits_per_million=1, max_output_microunits_per_million=1), "pin_requires_centralized_only"),
        (dict(mode="auto"), "auto_requires_explicit_fallback_permission"),
        (dict(mode="auto", allow_centralized_fallback=True, max_input_microunits_per_million=5), "auto_fallback_requires_both_ceilings"),
        (dict(mode="marketplace-only", allow_centralized_fallback=True), "fallback_flag_only_applies_to_auto"),
        (dict(mode="centralized-only", pinned_providers=("Open AI",)), "pinned_provider_name_invalid"),
        (dict(mode="centralized-only", pinned_providers=tuple(f"p{i}" for i in range(9))), "too_many_pinned_providers"),
        (dict(max_input_microunits_per_million=0), "ceiling_invalid"),
        (dict(max_output_microunits_per_million=0.5), "ceiling_invalid"),
        (dict(max_output_microunits_per_million=True), "ceiling_invalid"),
        (dict(mode="fastest"), "routing_mode_unknown"),
    ],
)
def test_a_policy_upstream_would_refuse_is_refused_locally(policy, code) -> None:
    with pytest.raises(routing.RoutePolicyError) as caught:
        routing.RoutePolicy(**policy).validated()
    assert caught.value.code == code


def test_policy_from_settings_accepts_exact_decimal_strings_and_nothing_ambiguous() -> None:
    policy = routing.RoutePolicy.from_dict(
        {"mode": "auto", "allow_centralized_fallback": True, "max_input_usdc_per_million": "0.40", "max_output_usdc_per_million": "0.60"}
    )
    assert (policy.max_input_microunits_per_million, policy.max_output_microunits_per_million) == (400_000, 600_000)
    for bad, code in (
        ({"max_input_usdc_per_million": "0.4", "max_input_microunits_per_million": 400_000}, "ceiling_given_twice"),
        ({"max_input_usdc_per_million": 0.4}, "ceiling_invalid"),
        ({"max_input_usdc_per_million": "0.0000004"}, "ceiling_invalid"),
        ({"surge_pricing": True}, "unknown_policy_fields"),
    ):
        with pytest.raises(routing.RoutePolicyError) as caught:
            routing.RoutePolicy.from_dict(bad)
        assert caught.value.code == code


def test_marketplace_only_binds_the_price_the_owner_sees_exactly_and_sends_it_downstream() -> None:
    snapshot = _snap(_row("lyra-7b-synth"))
    bound = routing.approve_route_bound(snapshot, model_id="lyra-7b-synth", policy=routing.RoutePolicy(), now=1_010.0)
    assert (bound.max_input_microunits_per_million, bound.max_output_microunits_per_million) == (640_000, 1_200_000)
    assert bound.input_basis == bound.output_basis == "discovered_marketplace_price"
    assert bound.allowed_route_classes == ("marketplace", "key_relay")
    dispatch = routing.authorize_dispatch(bound, snapshot, model_id="lyra-7b-synth", policy=routing.RoutePolicy(), now=1_020.0)
    assert dispatch.request_headers() == (
        ("X-Pod-Routing-Mode", "marketplace-only"),
        ("X-Pod-Max-Price-Input", "640000"),
        ("X-Pod-Max-Price-Output", "1200000"),
    )


def test_explicit_ceilings_are_independent_per_axis() -> None:
    snapshot = _snap(_row("orbit-coder-synth"))
    policy = routing.RoutePolicy(max_input_microunits_per_million=700_000)
    bound = routing.approve_route_bound(snapshot, model_id="orbit-coder-synth", policy=policy, now=1_010.0)
    assert (bound.max_input_microunits_per_million, bound.input_basis) == (700_000, "explicit_policy_ceiling")
    assert (bound.max_output_microunits_per_million, bound.output_basis) == (1_200_000, "discovered_marketplace_price")


def test_centralized_only_binds_the_cheapest_provider_and_a_pin_list_binds_its_dearest_member() -> None:
    snapshot = _snap(_row("quill-9b-synth"))
    cheapest = routing.approve_route_bound(snapshot, model_id="quill-9b-synth", policy=routing.RoutePolicy(mode="centralized-only"), now=1_010.0)
    assert (cheapest.max_input_microunits_per_million, cheapest.max_output_microunits_per_million) == (900_000, 2_000_000)
    assert cheapest.input_basis == "discovered_centralized_price:together"
    pinned = routing.approve_route_bound(
        snapshot, model_id="quill-9b-synth", policy=routing.RoutePolicy(mode="centralized-only", pinned_providers=("openai", "venice", "together")), now=1_010.0
    )
    assert (pinned.max_input_microunits_per_million, pinned.max_output_microunits_per_million) == (1_100_000, 2_400_000)
    assert pinned.unlisted_pins == ("venice",) and pinned.header_providers == "openai,venice,together"
    with pytest.raises(routing.RouteUnavailableError) as caught:
        routing.approve_route_bound(snapshot, model_id="quill-9b-synth", policy=routing.RoutePolicy(mode="centralized-only", pinned_providers=("groq",)), now=1_010.0)
    assert caught.value.code == "pinned_providers_not_listed_for_model"


@pytest.mark.parametrize(
    ("snapshot", "model_id", "now", "policy", "code"),
    [
        (_snap(_row("lyra-7b-synth")), "nova-synth", 1_010.0, routing.RoutePolicy(), "model_not_listed"),
        (_snap(_row("lyra-7b-synth")), "lyra-7b-synth", 1_500.0, routing.RoutePolicy(), "price_stale"),
        (None, "lyra-7b-synth", 1_010.0, routing.RoutePolicy(), "price_feed_unavailable"),
        (_snap(_row("lyra-7b-synth", market=(640_000.5, 1)), ), "lyra-7b-synth", 1_010.0, routing.RoutePolicy(), "price_row_malformed"),
        (_snap(_row("lyra-7b-synth", count=0)), "lyra-7b-synth", 1_010.0, routing.RoutePolicy(), "marketplace_route_unavailable"),
        (_snap(_row("lyra-7b-synth", mode="per_request")), "lyra-7b-synth", 1_010.0, routing.RoutePolicy(), "pricing_mode_per_request_unsupported"),
        (_snap(_row("lyra-7b-synth", market=(0, 0))), "lyra-7b-synth", 1_010.0, routing.RoutePolicy(), "zero_price_cannot_bound_a_ceiling"),
        (_snap(_row("lyra-7b-synth")), "lyra-7b-synth", 1_010.0, routing.RoutePolicy(max_input_microunits_per_million=100), "approved_ceiling_below_every_current_price"),
    ],
    ids=["unlisted", "stale", "no-feed", "malformed", "no-marketplace", "per-request", "zero-price", "ceiling-too-low"],
)
def test_an_approval_is_refused_with_the_reason_named(snapshot, model_id, now, policy, code) -> None:
    with pytest.raises(routing.RouteUnavailableError) as caught:
        routing.approve_route_bound(snapshot, model_id=model_id, policy=policy, now=now)
    assert caught.value.code == code


def test_a_disappeared_cheap_route_is_refused_not_silently_replaced_by_an_expensive_one() -> None:
    approved_on = _snap(_row("lyra-7b-synth", market=(640_000, 1_200_000), centralized=(("anthropic", 50_000_000, 150_000_000),)))
    bound = routing.approve_route_bound(approved_on, model_id="lyra-7b-synth", policy=routing.RoutePolicy(), now=1_010.0)
    tomorrow = _snap(
        _row("lyra-7b-synth", market=None, count=0, centralized=(("anthropic", 50_000_000, 150_000_000),)), fetched_at=90_000.0
    )
    with pytest.raises(routing.RouteUnavailableError) as caught:
        routing.authorize_dispatch(bound, tomorrow, model_id="lyra-7b-synth", policy=routing.RoutePolicy(), now=90_010.0)
    assert caught.value.code == "marketplace_route_disappeared"


def test_a_price_rise_above_the_approved_bound_is_refused_and_a_fall_is_not() -> None:
    bound = routing.approve_route_bound(_snap(_row("ember-13b-synth")), model_id="ember-13b-synth", policy=routing.RoutePolicy(), now=1_010.0)
    risen = _snap(_row("ember-13b-synth", market=(640_001, 1_200_000)), fetched_at=2_000.0)
    with pytest.raises(routing.RouteUnavailableError) as caught:
        routing.authorize_dispatch(bound, risen, model_id="ember-13b-synth", policy=routing.RoutePolicy(), now=2_010.0)
    assert caught.value.code == "route_price_above_approved_bound"
    fallen = _snap(_row("ember-13b-synth", market=(300_000, 900_000)), fetched_at=2_000.0)
    dispatch = routing.authorize_dispatch(bound, fallen, model_id="ember-13b-synth", policy=routing.RoutePolicy(), now=2_010.0)
    assert dispatch.max_input_microunits_per_million == 640_000  # the bound does not ratchet down or up


def test_auto_with_an_explicit_fallback_says_before_dispatch_that_fallback_will_happen() -> None:
    policy = routing.RoutePolicy(mode="auto", allow_centralized_fallback=True, max_input_microunits_per_million=1_000_000, max_output_microunits_per_million=2_500_000)
    bound = routing.approve_route_bound(_snap(_row("tide-4b-synth")), model_id="tide-4b-synth", policy=policy, now=1_010.0)
    assert bound.allowed_route_classes == ("marketplace", "key_relay", "centralized")
    no_market = _snap(_row("tide-4b-synth", market=None, count=0), fetched_at=2_000.0)
    dispatch = routing.authorize_dispatch(bound, no_market, model_id="tide-4b-synth", policy=policy, now=2_010.0)
    assert dispatch.fallback_expected and dispatch.notes == ("no_marketplace_listing_within_bound_centralized_fallback_expected",)
    assert ("X-Pod-Routing-Mode", "auto") in dispatch.request_headers()


@pytest.mark.parametrize(
    ("bound_model", "dispatch_model", "policy_after", "code"),
    [
        ("lyra-7b-synth", "lyra-7b-synth", routing.RoutePolicy(mode="centralized-only"), "route_policy_changed_since_approval"),
        ("lyra-7b-synth", "orbit-coder-synth", routing.RoutePolicy(), "route_approval_is_for_another_model"),
        (None, "lyra-7b-synth", routing.RoutePolicy(), "route_not_approved"),
    ],
)
def test_dispatch_refuses_an_approval_that_does_not_cover_this_call(bound_model, dispatch_model, policy_after, code) -> None:
    snapshot = _snap(_row("lyra-7b-synth"), _row("orbit-coder-synth"))
    bound = routing.approve_route_bound(snapshot, model_id=bound_model, policy=routing.RoutePolicy(), now=1_010.0) if bound_model else None
    with pytest.raises(routing.RouteUnavailableError) as caught:
        routing.authorize_dispatch(bound, snapshot, model_id=dispatch_model, policy=policy_after, now=1_020.0)
    assert caught.value.code == code


def test_a_delisted_model_is_refused_at_dispatch() -> None:
    bound = routing.approve_route_bound(_snap(_row("lyra-7b-synth")), model_id="lyra-7b-synth", policy=routing.RoutePolicy(), now=1_010.0)
    with pytest.raises(routing.RouteUnavailableError) as caught:
        routing.authorize_dispatch(bound, _snap(_row("orbit-coder-synth"), fetched_at=1_500.0), model_id="lyra-7b-synth", policy=routing.RoutePolicy(), now=1_510.0)
    assert caught.value.code == "model_disappeared"


def _dispatch(policy: routing.RoutePolicy):
    snapshot = _snap(_row("lyra-7b-synth"))
    bound = routing.approve_route_bound(snapshot, model_id="lyra-7b-synth", policy=policy, now=1_010.0)
    return routing.authorize_dispatch(bound, snapshot, model_id="lyra-7b-synth", policy=policy, now=1_020.0)


@pytest.mark.parametrize(
    ("policy", "headers", "compliance", "reason"),
    [
        (routing.RoutePolicy(), {"X-Pod-Route": "marketplace", "X-Pod-Provider-Id": MARKET_UUID}, "compliant", None),
        (routing.RoutePolicy(), {"x-pod-route": "key relay", "x-pod-provider-id": RELAY_UUID}, "compliant", None),
        (routing.RoutePolicy(), {"X-Pod-Route": "centralized", "X-Pod-Provider-Id": "openai"}, "violated", "route_class_not_permitted:centralized"),
        (routing.RoutePolicy(), {"X-Pod-Provider-Id": MARKET_UUID}, "unverified", "route_header_missing"),
        (routing.RoutePolicy(), {"X-Pod-Route": "priority lane"}, "unverified", "route_header_unrecognized"),
        (routing.RoutePolicy(mode="centralized-only", pinned_providers=("openai",)), {"X-Pod-Route": "centralized", "X-Pod-Provider-Id": "venice"}, "violated", "provider_not_in_pin:venice"),
        (routing.RoutePolicy(mode="centralized-only", pinned_providers=("openai",)), {"X-Pod-Route": "centralized"}, "unverified", "pinned_provider_not_confirmed_by_response"),
        (routing.RoutePolicy(mode="centralized-only"), {"X-Pod-Route": "centralized", "X-Pod-Provider-Id": MARKET_UUID}, "unverified", "provider_id_shape_inconsistent_with_route"),
    ],
    ids=["marketplace", "key-relay", "centralized-on-marketplace-only", "no-route", "unknown-route", "outside-pin", "pin-unconfirmed", "shape-mismatch"],
)
def test_the_route_that_answered_is_judged_from_the_documented_headers_only(policy, headers, compliance, reason) -> None:
    evidence = routing.evaluate_route_evidence(_dispatch(policy), headers)
    assert evidence.compliance == compliance
    if reason:
        assert reason in evidence.reasons
    assert evidence.as_dict()["route_verification"] == "header_reported_only_no_cryptographic_proof"


def test_route_metadata_is_kept_raw_and_bounded_and_missing_balance_is_not_zero() -> None:
    dispatch = _dispatch(routing.RoutePolicy())
    evidence = routing.evaluate_route_evidence(dispatch, {"X-Pod-Route": "market\x07place", "X-Pod-Provider-Id": MARKET_UUID, "X-Balance-Remaining": "4.812300"})
    assert evidence.route_raw == "marketplace" and evidence.balance_remaining_decimal == "4.812300"
    no_balance = routing.evaluate_route_evidence(dispatch, {"X-Pod-Route": "marketplace", "X-Pod-Provider-Id": MARKET_UUID})
    assert no_balance.balance_remaining_raw is None and no_balance.balance_remaining_decimal is None
    garbage = routing.evaluate_route_evidence(dispatch, {"X-Pod-Route": "marketplace", "X-Pod-Provider-Id": MARKET_UUID, "X-Balance-Remaining": "lots"})
    assert "balance_header_unparseable" in garbage.reasons and garbage.compliance == "compliant"


def test_auto_records_a_fallback_when_centralized_served() -> None:
    policy = routing.RoutePolicy(mode="auto", allow_centralized_fallback=True, max_input_microunits_per_million=2_000_000, max_output_microunits_per_million=3_000_000)
    evidence = routing.evaluate_route_evidence(_dispatch(policy), {"X-Pod-Route": "centralized fallback", "X-Pod-Provider-Id": "together"})
    assert evidence.compliance == "compliant" and evidence.fallback_used


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield tmp_path
    runtime_paths.configure_runtime_home(None)


def test_the_store_keeps_approvals_only_under_the_policy_they_were_made_for(isolated_home) -> None:
    snapshot = _snap(_row("lyra-7b-synth"))
    bound = routing.approve_route_bound(snapshot, model_id="lyra-7b-synth", policy=routing.RoutePolicy(), now=1_010.0)
    state = routing.save_approved_bound(bound)
    assert set(state.bounds) == {"lyra-7b-synth"} and state.error == ""
    reloaded = routing.load_route_state()
    assert reloaded.bounds["lyra-7b-synth"].to_dict() == bound.to_dict()
    changed = routing.save_route_policy(routing.RoutePolicy(mode="centralized-only"))
    assert changed.bounds == {}


def test_a_tampered_or_corrupt_store_honours_no_approval(isolated_home) -> None:
    snapshot = _snap(_row("lyra-7b-synth"))
    routing.save_approved_bound(routing.approve_route_bound(snapshot, model_id="lyra-7b-synth", policy=routing.RoutePolicy(), now=1_010.0))
    path = next(Path(isolated_home).rglob("route_state.json"))
    record = json.loads(path.read_text())
    record["bounds"]["lyra-7b-synth"]["policy"]["mode"] = "auto"
    path.write_text(json.dumps(record))
    state = routing.load_route_state()
    assert state.error == "route_store_unreadable" and state.bounds == {}
    path.write_text("{not json")
    assert routing.load_route_state().error == "route_store_unreadable"


def test_an_approval_never_authorizes_dispatch_against_another_origin() -> None:
    policy = routing.RoutePolicy(mode=routing.RoutingMode.MARKETPLACE_ONLY)
    bound = routing.approve_route_bound(_snap(_row("lyra-7b-synth")), model_id="lyra-7b-synth", policy=policy, now=1_000.0)
    assert bound.origin == ORIGIN and bound.to_dict()["origin"] == ORIGIN
    elsewhere = pricing.parse_marketplace_feed(
        {"models": [_row("lyra-7b-synth")]}, source="synthetic", origin="https://gateway.example.test", fetched_at=1_000.0,
        ttl_seconds=120, evidence=pricing.EVIDENCE_FIXTURE, fixture_label="SYNTHETIC second origin",
    )
    with pytest.raises(routing.RouteUnavailableError) as caught:
        routing.authorize_dispatch(bound, elsewhere, model_id="lyra-7b-synth", policy=policy, now=1_001.0)
    assert caught.value.code == "route_approval_is_for_another_origin"
    record = bound.to_dict()
    record.pop("origin")
    with pytest.raises(ValueError, match="origin"):
        routing.ApprovedRouteBound.from_dict(record)
