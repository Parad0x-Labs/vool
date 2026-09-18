"""Work 2/3 of the provider-answer recovery: one coherent UsePod price review.

The owner asked for editable maximum INPUT and OUTPUT prices beside the observed prices in the
yellow confirmation, in human units (USDC per 1M tokens), wired to the limits dispatch actually
enforces. Before this repair the confirmation showed only the observed rates; the saved route
bound derived its ceilings from the price at approval time, and when the price later rose above
that derived ceiling the dispatch refused (`route_price_above_approved_bound`) while the recovery
text pointed generically at Settings with no axis, no values, and no review path.

This pins the server side of the repair (the client dialog is exercised served):
  * an owner-reviewed TWO-AXIS maximum on the bound itself (basis ``explicit_owner_review``),
    saved through the existing authority ``authorize_dispatch`` re-checks;
  * one axis alone refused; exact decimal conversion; invalid values refused typed;
  * a rise above the SAVED maximum still refuses — a review never silently raises anything;
  * the refusal evidence carries the violating axes in exact human units;
  * the identity receipt for a pre-send refusal says NO RESPONSE, not a provider claim;
  * the recovery message names the axis, the observed price and the saved maximum.
"""
from __future__ import annotations

import pytest

from core.usepod import pricing, routing

ORIGIN = "https://api.usepod.ai"


def _row(model_id: str, *, market=(640_000, 1_200_000), centralized=(900_000, 2_000_000)):
    return {
        "model_id": model_id,
        "pricing_mode": "per_token",
        "cheapest_input_per_1m": market[0],
        "cheapest_output_per_1m": market[1],
        "centralized_input_per_1m": centralized[0],
        "centralized_output_per_1m": centralized[1],
        "centralized_providers": [{"provider": "together", "input_per_1m": centralized[0], "output_per_1m": centralized[1]}],
        "marketplace_provider_count": 3,
        "marketplace_total_tps": 0.0,
        "uncensored": False,
    }


def _snap(*rows, fetched_at: float = 1_000.0):
    return pricing.parse_marketplace_feed(
        {"models": list(rows)}, source="synthetic", origin=ORIGIN, fetched_at=fetched_at, ttl_seconds=120,
        evidence=pricing.EVIDENCE_FIXTURE, fixture_label="SYNTHETIC price-review snapshot",
    )


# --- the reviewed two-axis bound ----------------------------------------------------------------


def test_a_reviewed_two_axis_maximum_is_bound_exactly_and_enforced_at_dispatch() -> None:
    snapshot = _snap(_row("astra-8b"))
    bound = routing.approve_route_bound(
        snapshot, model_id="astra-8b", policy=routing.RoutePolicy(), now=1_010.0,
        reviewed_max_input_microunits=1_250_000, reviewed_max_output_microunits=4_000_000,
    )
    assert bound.max_input_microunits_per_million == 1_250_000
    assert bound.max_output_microunits_per_million == 4_000_000
    assert bound.input_basis == "explicit_owner_review"
    assert bound.output_basis == "explicit_owner_review"
    # Dispatch under the SAME prices is authorized against the reviewed maxima.
    approval = routing.authorize_dispatch(bound, _snap(_row("astra-8b"), fetched_at=1_020.0), model_id="astra-8b", policy=routing.RoutePolicy(), now=1_020.0)
    assert approval.max_input_microunits_per_million == 1_250_000


def test_a_review_needs_both_axes() -> None:
    with pytest.raises(routing.RoutePolicyError) as raised:
        routing.approve_route_bound(
            _snap(_row("astra-8b")), model_id="astra-8b", policy=routing.RoutePolicy(), now=1_010.0,
            reviewed_max_input_microunits=1_250_000,
        )
    assert raised.value.code == "owner_review_requires_both_axes"
    with pytest.raises(routing.RoutePolicyError):
        routing.approve_route_bound(
            _snap(_row("astra-8b")), model_id="astra-8b", policy=routing.RoutePolicy(), now=1_010.0,
            reviewed_max_output_microunits=4_000_000,
        )


def test_an_invalid_reviewed_maximum_is_refused_typed() -> None:
    with pytest.raises(routing.RoutePolicyError) as raised:
        routing.approve_route_bound(
            _snap(_row("astra-8b")), model_id="astra-8b", policy=routing.RoutePolicy(), now=1_010.0,
            reviewed_max_input_microunits=0, reviewed_max_output_microunits=4_000_000,
        )
    assert raised.value.code == "ceiling_invalid"


def test_a_price_above_the_reviewed_maximum_still_refuses_with_the_axes_named() -> None:
    bound = routing.approve_route_bound(
        _snap(_row("astra-8b")), model_id="astra-8b", policy=routing.RoutePolicy(), now=1_010.0,
        reviewed_max_input_microunits=1_250_000, reviewed_max_output_microunits=4_000_000,
    )
    risen = _snap(_row("astra-8b", market=(1_400_000, 4_500_000), centralized=(1_500_000, 5_000_000)), fetched_at=1_020.0)
    with pytest.raises(routing.RouteUnavailableError) as raised:
        routing.authorize_dispatch(bound, risen, model_id="astra-8b", policy=routing.RoutePolicy(), now=1_020.0)
    assert raised.value.code == "route_price_above_approved_bound"
    axes = raised.value.evidence.get("refusal_axes")
    assert isinstance(axes, list) and axes, "the refusal evidence must carry the violating axes"
    by_axis = {axis["axis"]: axis for axis in axes}
    # Exact human units, decimal strings, never binary float artifacts.
    assert by_axis["input"]["observed_usdc_per_million"] == "1.4"
    assert by_axis["input"]["saved_max_usdc_per_million"] == "1.25"
    assert by_axis["output"]["observed_usdc_per_million"] == "4.5"
    assert by_axis["output"]["saved_max_usdc_per_million"] == "4"


def test_a_fall_below_the_reviewed_maximum_does_not_refuse() -> None:
    bound = routing.approve_route_bound(
        _snap(_row("astra-8b")), model_id="astra-8b", policy=routing.RoutePolicy(), now=1_010.0,
        reviewed_max_input_microunits=1_250_000, reviewed_max_output_microunits=4_000_000,
    )
    fallen = _snap(_row("astra-8b", market=(700_000, 1_300_000)), fetched_at=1_020.0)
    approval = routing.authorize_dispatch(bound, fallen, model_id="astra-8b", policy=routing.RoutePolicy(), now=1_020.0)
    assert approval.eligible_prices


def test_price_refusal_axes_reads_only_real_evidence() -> None:
    assert routing.price_refusal_axes(None) == []
    assert routing.price_refusal_axes({}) == []
    assert routing.price_refusal_axes({"approved": {"max_input_microunits_per_million": 1}}) == []


# --- the discovery door: exact decimal conversion -------------------------------------------------


def test_approve_model_route_converts_exact_decimals(monkeypatch, tmp_path) -> None:
    from core.usepod import discovery

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", tmp_path, raising=False)
    captured: dict[str, object] = {}

    real_approve = routing.approve_route_bound

    def _fake_approve(snapshot, *, model_id, policy, now, reviewed_max_input_microunits=None, reviewed_max_output_microunits=None):
        captured["in"] = reviewed_max_input_microunits
        captured["out"] = reviewed_max_output_microunits
        return real_approve(
            snapshot, model_id=model_id, policy=policy, now=now,
            reviewed_max_input_microunits=reviewed_max_input_microunits,
            reviewed_max_output_microunits=reviewed_max_output_microunits,
        )

    monkeypatch.setattr(discovery.routing, "approve_route_bound", _fake_approve)
    monkeypatch.setattr(discovery, "resolve_credential", lambda: None)
    monkeypatch.setattr(discovery, "configured_origin", lambda: ORIGIN)
    monkeypatch.setattr(
        discovery.pricing, "current_snapshot",
        lambda origin, allow_network, now=None: type("R", (), {"snapshot": _snap(_row("astra-8b")), "state": pricing.SNAPSHOT_FRESH, "error_code": ""})(),
    )
    monkeypatch.setattr(discovery.routing, "save_approved_bound", lambda bound: None)
    monkeypatch.setattr(
        discovery.routing, "load_route_state",
        lambda: routing.RouteState(policy=routing.RoutePolicy(), bounds={}, error=""),
    )
    discovery.approve_model_route(
        "astra-8b", max_input_usdc_per_million="1.25", max_output_usdc_per_million="4.000001",
        now=1_010.0,
    )
    # 1.25 USDC = 1_250_000 microunits; 4.000001 = 4_000_001 exactly — no float rounding.
    assert captured["in"] == 1_250_000
    assert captured["out"] == 4_000_001


def test_approve_model_route_refuses_one_axis_and_bad_decimals(monkeypatch, tmp_path) -> None:
    from core.usepod import discovery

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", tmp_path, raising=False)
    with pytest.raises(routing.RoutePolicyError) as raised:
        discovery.approve_model_route("astra-8b", max_input_usdc_per_million="1.25")
    assert raised.value.code == "owner_review_requires_both_axes"
    with pytest.raises(routing.RoutePolicyError) as raised:
        discovery.approve_model_route("astra-8b", max_input_usdc_per_million="1.25", max_output_usdc_per_million="-4")
    assert raised.value.code == "ceiling_invalid"
    with pytest.raises(routing.RoutePolicyError) as raised:
        discovery.approve_model_route("astra-8b", max_input_usdc_per_million="1.25", max_output_usdc_per_million="abc")
    assert raised.value.code == "ceiling_invalid"


# --- the identity receipt: no response is not a claim ---------------------------------------------


def test_a_pre_send_refusal_receipt_says_no_response_not_claimed() -> None:
    from core.provider_verification import build_verification_receipt

    class _RefusedError(RuntimeError):
        provider_evidence = {
            "usepod": {
                "schema": "vool.usepod.refusal.v1",
                "status": "refused_before_send",
                "code": "route_price_above_approved_bound",
            },
            "receipt": {},
        }

    receipt = build_verification_receipt(
        call_id="provider-call-1", request_id="req-1",
        requested_model="usepod:gpt-6-astra", selected_model="gpt-6-astra",
        provider_id="usepod-byok:gpt-6-astra", response=_RefusedError("usepod_dispatch_refused:route_price_above_approved_bound"), outcome="failed",
    )
    verification = receipt["verification"]
    assert verification["status"] == "NO_RESPONSE"
    assert "No provider response exists" in verification["explanation"]
    assert receipt["returned_model"] is None


def test_a_real_response_still_reads_claimed() -> None:
    from core.provider_verification import build_verification_receipt

    class _Answered:
        provider_attested_model = "gpt-6-astra"
        raw_response = {"id": "resp-1", "model": "gpt-6-astra"}
        provider_metadata = {}
        usage = {}

    receipt = build_verification_receipt(
        call_id="provider-call-2", request_id="req-2",
        requested_model="usepod:gpt-6-astra", selected_model="gpt-6-astra",
        provider_id="usepod-byok:gpt-6-astra", response=_Answered(), outcome="completed",
    )
    assert receipt["verification"]["status"] == "CLAIMED"
    assert receipt["returned_model"] == "gpt-6-astra"


# --- the recovery message names the axes -----------------------------------------------------------


def test_the_refusal_message_names_axis_observed_and_saved() -> None:
    from types import SimpleNamespace

    from core.agent_runtime import memory_runtime

    execution = SimpleNamespace(
        source="selected_model_blocked",
        details={
            "requested_model": "usepod:gpt-6-astra",
            "block_reason": "usepod_dispatch_refused:route_price_above_approved_bound",
            "block_route_evidence": {
                "refusal_axes": [
                    {"axis": "input", "observed_usdc_per_million": "1.4", "saved_max_usdc_per_million": "1.25"},
                    {"axis": "output", "observed_usdc_per_million": "4.5", "saved_max_usdc_per_million": "4"},
                ],
            },
        },
    )
    message = memory_runtime._usepod_prepaid_refusal_hint(execution)
    assert "input price 1.4 USDC per 1M is above your saved maximum 1.25" in message
    assert "output price 4.5 USDC per 1M is above your saved maximum 4" in message
    assert "nothing was sent and nothing was charged" in message
    assert "Review price limits" in message


def test_the_refusal_message_without_evidence_names_no_invented_values() -> None:
    from types import SimpleNamespace

    from core.agent_runtime import memory_runtime

    execution = SimpleNamespace(
        source="selected_model_blocked",
        details={
            "requested_model": "usepod:gpt-6-astra",
            "block_reason": "usepod_dispatch_refused:route_price_above_approved_bound",
        },
    )
    message = memory_runtime._usepod_prepaid_refusal_hint(execution)
    assert "above the maximum you approved" in message
    assert "1." not in message.replace("1 hour", "")  # no invented price figures
    assert "nothing was sent and nothing was charged" in message
