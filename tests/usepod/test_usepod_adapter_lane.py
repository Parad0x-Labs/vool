"""UsePod on the adapter hierarchy, end to end in process.

The real Settings door stores the credential; discovery reads the SYNTHETIC strict local service's
marketplace feed; the owner approves a route through the owner-action endpoints; the lane is registered
through the manifest registry and built by ``ModelRegistry``; dispatch goes through the provider HTTP
worker; every call leaves a receipt.

The monetary and payment authorities are TEST DOUBLES labelled ``test_double:`` and every receipt they
touch carries that label. The service's reply is a deterministic function of the request that ARRIVED
(its last user message, or the first tool it offered), so no answer here is keyed to a prompt. Tokens are
generated per test; model names are invented.
"""
from __future__ import annotations

import hashlib
import json
import traceback
import uuid
from types import SimpleNamespace

import pytest

from adapters.base_adapter import ModelRequest
from adapters.usepod_adapter import UsePodDispatchRefusedError, UsePodRouteNotCompliantError
from core.anthropic_messages_protocol import AnthropicStreamError, IncompleteStreamError
from core.cloud_provider_contract import CloudToolDefinition
from core.normalized_provider_result import MalformedProviderResponseError
from core.usepod import pricing, routing
from core.usepod.monetary import install_monetary_authority
from core.usepod.transport import UsePodTransportError, install_payment_authority
from tests.usepod._usepod_doors import get as _get
from tests.usepod._usepod_doors import post as _post
from tests.usepod._usepod_doubles import RecordingMonetaryTestDouble, SyntheticChainPaymentTestDouble
from tests.usepod.strict_usepod_service import Listing, ScriptedReply, _last_user_text, default_reply

MODEL = "orrery-13b-synth"
MARKET_ID = "7e1d2c3b-4a59-4687-9a0b-1c2d3e4f5a6b"
MARKET = (420_000, 1_260_000)
CENTRAL = ("groq", 610_000, 1_830_000)
BALANCE = 50_000_000
OPENAI = "/proxy/{token}/v1/chat/completions"
ANTHROPIC = "/proxy/{token}/v1/messages"
READ_FILE = CloudToolDefinition(
    intent="workspace.read_file",
    name="workspace__read_file",
    description="Read one text file from the workspace.",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False},
)


# --- the rig: the real doors, the registry, the strict service -----------------------------------------


@pytest.fixture
def rig(usepod_home, usepod_service):
    token = str(uuid.uuid4())
    service = usepod_service(
        tokens={token: BALANCE},
        models={MODEL: [Listing("marketplace", MARKET_ID, *MARKET), Listing("centralized", *CENTRAL)]},
    )
    status, saved = _post("/api/settings/credentials", {"provider": "usepod", "value": token, "base_url": service.origin})
    assert status == 200, saved
    assert (saved["origin"], saved["origin_is_default"]) == (service.origin, False)
    return SimpleNamespace(service=service, token=token, home=usepod_home, fingerprint=saved["credential_fingerprint"], make=usepod_service, other=None)


def _approve(policy: dict | None = None, model: str = MODEL) -> dict:
    status, payload = _post("/api/cloud/usepod/route-policy", policy or {"mode": "marketplace-only"})
    assert status == 200, payload
    status, payload = _post("/api/cloud/usepod/approve-route", {"model_id": model})
    assert status == 200, payload
    return payload["approved_route"]


def _lane(*, protocol: str = "openai", transport: str = "prepaid_token", model: str = MODEL, runtime_overrides: dict | None = None):
    from core.model_registry import ModelRegistry
    from core.runtime_provider_defaults import register_chat_cloud_model

    status, payload = _post("/api/cloud/usepod/lane", {"protocol": protocol, "transport_mode": transport})
    assert status == 200, payload
    assert register_chat_cloud_model("usepod", model) == f"usepod-byok:{model}"
    registry = ModelRegistry()
    manifest = registry.get_manifest("usepod-byok", model)
    assert manifest is not None and (manifest.runtime_config["protocol"], manifest.runtime_config["transport_mode"]) == (protocol, transport)
    if runtime_overrides:
        manifest = manifest.model_copy(update={"runtime_config": {**manifest.runtime_config, **runtime_overrides}})
    return registry.build_adapter(manifest), manifest


def _monetary(**options) -> RecordingMonetaryTestDouble:
    double = RecordingMonetaryTestDouble(**options)
    install_monetary_authority(double, label=double.label)
    return double


def _request(prompt: str, *, max_output_tokens: int | None = 256, output_mode: str = "plain_text", tools=(), metadata=None, messages=None) -> ModelRequest:
    return ModelRequest(
        task_kind="chat",
        prompt=prompt,
        messages=list(messages) if messages is not None else [{"role": "user", "content": prompt}],
        max_output_tokens=max_output_tokens,
        output_mode=output_mode,
        tools=tuple(tools),
        metadata=dict(metadata or {}),
    )


def _ceil_micro(numerator: int) -> int:
    return -(-numerator // 1_000_000)


def _rendered(error: BaseException) -> str:
    return "".join(traceback.format_exception(error))


def _call_first_offered_tool(body: dict, protocol: str) -> ScriptedReply:
    """Call the first tool the REQUEST offered, with arguments derived from its last user message."""
    first = next(iter(body.get("tools") or []))
    name, schema = (first["name"], first["input_schema"]) if protocol == "anthropic" else (first["function"]["name"], first["function"]["parameters"])
    digest = hashlib.sha256(_last_user_text(body).encode("utf-8")).hexdigest()[:10]
    return ScriptedReply(tool_name=name, tool_arguments={key: f"notes/{digest}.txt" for key in schema.get("required", [])})


def _serve_feed_row(rig, **changes) -> None:
    feed = rig.service.derived_feed()
    for row in feed["models"]:
        if row["model_id"] == MODEL:
            row.update(changes)
    rig.service.feed_payload = feed
    pricing._cache_path().unlink(missing_ok=True)


# --- successful turns, both dialects ----------------------------------------------------------------------


def test_a_prepaid_openai_turn_is_sent_once_to_the_token_path_and_leaves_a_settled_receipt(rig) -> None:
    approved = _approve()
    assert (approved["max_input_microunits_per_million"], approved["max_output_microunits_per_million"]) == MARKET
    assert (approved["input_basis"], approved["origin"]) == ("discovered_marketplace_price", rig.service.origin)
    adapter, manifest = _lane()
    monetary = _monetary()

    response = adapter.invoke(_request("Which weekday comes three days after Thursday? One word."))

    [arrived] = rig.service.requests_to(OPENAI)
    body = json.loads(arrived["body"])
    assert response.output_text == default_reply(body, "openai").text
    assert (body["model"], body["max_tokens"]) == (MODEL, 256)
    assert "authorization" not in arrived["headers"]
    assert arrived["headers"]["x-pod-routing-mode"] == "marketplace-only"
    assert (arrived["headers"]["x-pod-max-price-input"], arrived["headers"]["x-pod-max-price-output"]) == tuple(str(rate) for rate in MARKET)

    evidence = response.provider_metadata["usepod"]
    receipt = response.provider_metadata["receipt"]
    assert evidence["envelope"]["body_sha256"] == arrived["body_sha256"]
    liability = _ceil_micro((len(arrived["body"]) + pricing.PROMPT_OVERHEAD_TOKENS) * MARKET[0] + 256 * MARKET[1])
    assert monetary.names() == ["reserve", "mark_dispatched", "settle"]
    assert monetary.calls[0][1].max_amount_atomic == liability
    charged = _ceil_micro(21 * MARKET[0] + 13 * MARKET[1])
    assert receipt["cost"] == {
        "asset": "USDC",
        "unit": "usdc_microunit",
        "liability_bound_atomic": liability,
        "usage_upper_bound_atomic": charged,
        "exact_atomic": None,
        "exact_state": "not_supplied_by_provider",
    }
    assert {key: receipt["route"][key] for key in ("reported", "class", "provider_id", "compliance", "reasons", "fallback_used")} == {
        "reported": "marketplace",
        "class": "marketplace",
        "provider_id": MARKET_ID,
        "compliance": "compliant",
        "reasons": [],
        "fallback_used": False,
    }
    remaining = BALANCE - charged
    shown = f"{remaining // 1_000_000}.{remaining % 1_000_000:06d}"
    assert receipt["balance_remaining"] == {"state": "reported", "raw": shown, "decimal": shown, "unit": "unverified_header_unit"}
    assert receipt["settlement"] == {"outcome": "completed", "recording": "settled_with_evidence"}
    assert receipt["monetary_authority"] == RecordingMonetaryTestDouble.label
    assert receipt["credential_fingerprint"] == rig.fingerprint
    assert evidence["route_policy"]["bound_approval_id"] == approved["approval_id"]
    assert receipt["reservation_id"] == monetary.calls[1][1].reservation_id
    assert receipt["price_snapshot_sha256"] == evidence["route_policy"]["price_source"]["snapshot_sha256"] == approved["snapshot_sha256"]
    estimate = response.provider_metadata["cost_estimate"]
    assert (estimate["known"], estimate["basis"], estimate["upper_bound_atomic"]) == (False, "usepod_reported_usage_at_approved_ceiling", charged)
    rendered = json.dumps(response.provider_metadata) + repr(response) + json.dumps(manifest.model_dump(), default=str)
    assert rig.token not in rendered


def test_a_prepaid_anthropic_tool_turn_is_translated_on_the_wire_and_normalized_back(rig) -> None:
    _approve()
    adapter, _manifest = _lane(protocol="anthropic")
    monetary = _monetary()
    rig.service.reply = _call_first_offered_tool

    response = adapter.invoke(_request("Open the release notes the ticket mentions.", output_mode="tool_intent", tools=(READ_FILE,)))

    [arrived] = rig.service.requests_to(ANTHROPIC)
    body = json.loads(arrived["body"])
    assert arrived["headers"]["anthropic-version"] == "2023-06-01" and "authorization" not in arrived["headers"]
    assert isinstance(body["max_tokens"], int)
    assert body["tools"][0]["name"] == READ_FILE.name and "input_schema" in body["tools"][0]
    assert {message["role"] for message in body["messages"]} <= {"user", "assistant"}
    digest = hashlib.sha256(_last_user_text(body).encode("utf-8")).hexdigest()[:10]
    [call] = response.tool_calls
    assert (call.intent, call.name, call.arguments) == (READ_FILE.intent, READ_FILE.name, {"path": f"notes/{digest}.txt"})
    receipt = response.provider_metadata["receipt"]
    assert (receipt["protocol"], receipt["endpoint"]) == ("anthropic", ANTHROPIC)
    assert monetary.names() == ["reserve", "mark_dispatched", "settle"]


def test_an_openai_stream_yields_text_then_one_terminal_chunk_carrying_the_receipt(rig) -> None:
    _approve()
    adapter, _manifest = _lane()
    monetary = _monetary()

    chunks = list(adapter.stream_text_task(_request("Name the prime between 44 and 50.")))

    [arrived] = rig.service.requests_to(OPENAI)
    body = json.loads(arrived["body"])
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert "".join(chunk.delta_text for chunk in chunks) == default_reply(body, "openai").text
    *text_chunks, terminal = chunks
    assert terminal.done and terminal.delta_text == "" and not any(chunk.done for chunk in text_chunks)
    assert all(chunk.provider_metadata is None for chunk in text_chunks)
    assert (terminal.usage["prompt_tokens"], terminal.usage["completion_tokens"], terminal.finish_reason) == (21, 13, "stop")
    assert terminal.provider_metadata["receipt"]["settlement"] == {"outcome": "completed", "recording": "settled_with_evidence"}
    assert monetary.names() == ["reserve", "mark_dispatched", "settle"]


def test_an_anthropic_stream_is_assembled_and_settled_only_after_message_stop(rig) -> None:
    _approve()
    adapter, _manifest = _lane(protocol="anthropic")
    monetary = _monetary()

    chunks = list(adapter.stream_text_task(_request("Spell 'ocelot' backwards.")))

    [arrived] = rig.service.requests_to(ANTHROPIC)
    body = json.loads(arrived["body"])
    assert "".join(chunk.delta_text for chunk in chunks) == default_reply(body, "anthropic").text
    terminal = chunks[-1]
    assert terminal.done and (terminal.usage["prompt_tokens"], terminal.usage["completion_tokens"]) == (21, 13)
    assert terminal.provider_metadata["receipt"]["protocol"] == "anthropic"
    assert monetary.names() == ["reserve", "mark_dispatched", "settle"]


def test_auto_routing_with_permitted_fallback_says_before_dispatch_that_centralized_will_serve(rig) -> None:
    rig.service.models[MODEL] = [Listing("centralized", *CENTRAL)]
    _approve({"mode": "auto", "allow_centralized_fallback": True, "max_input_usdc_per_million": "0.70", "max_output_usdc_per_million": "2.00"})
    adapter, _manifest = _lane()
    _monetary()

    response = adapter.invoke(_request("Convert 5 km to miles, rounded to one decimal."))

    evidence = response.provider_metadata["usepod"]
    assert evidence["route_policy"]["fallback_expected"] is True
    assert "no_marketplace_listing_within_bound_centralized_fallback_expected" in evidence["route_policy"]["notes"]
    receipt = response.provider_metadata["receipt"]
    assert (receipt["route"]["class"], receipt["route"]["provider_id"], receipt["route"]["fallback_used"], receipt["route"]["compliance"]) == (
        "centralized",
        "groq",
        True,
        "compliant",
    )
    [arrived] = rig.service.requests_to(OPENAI)
    assert (arrived["headers"]["x-pod-routing-mode"], arrived["headers"]["x-pod-max-price-input"], arrived["headers"]["x-pod-max-price-output"]) == (
        "auto",
        "700000",
        "2000000",
    )
    assert receipt["cost"]["usage_upper_bound_atomic"] == _ceil_micro(21 * 700_000 + 13 * 2_000_000)


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_an_accountless_x402_turn_carries_no_token_and_records_the_wallet_outflow(rig, protocol: str) -> None:
    _approve()
    adapter, _manifest = _lane(protocol=protocol, transport="x402")
    monetary = _monetary()
    payment = SyntheticChainPaymentTestDouble(rig.service)
    install_payment_authority(payment, label=payment.label)

    response = adapter.invoke(_request("Name a color that rhymes with 'bed'."))

    path = "/proxy/x402/v1/chat/completions" if protocol == "openai" else "/proxy/x402/v1/messages"
    quote_request, paid = rig.service.requests_to(path)
    assert quote_request["body_sha256"] == paid["body_sha256"]
    assert not [item for item in rig.service.requests if item["path"].startswith("/proxy/{token}")]
    body = json.loads(paid["body"])
    assert response.output_text == default_reply(body, protocol).text
    receipt = response.provider_metadata["receipt"]
    cap = _ceil_micro(len(paid["body"]) * MARKET[0] + body["max_tokens"] * MARKET[1])
    assert receipt["x402"]["wallet_outflow_atomic"] == cap == payment.calls[0]["amount"]
    assert receipt["x402"]["payment_authority"] == SyntheticChainPaymentTestDouble.label
    assert (receipt["x402"]["payment_signature"], receipt["x402"]["network"]) == (payment.issued[0]["proof"].signature, payment.networks[0])
    assert receipt["x402"]["network_fee_state"] == "owned_by_wallet_authority_not_observed_by_provider_transport"
    assert receipt["x402"]["payment_response_state"] == "decoded_schema_unpublished"
    # the SYNTHETIC service names the unused cap as surplus in its PAYMENT-RESPONSE: carried as provider credit
    # (unverified, never a wallet refund), with the receipt's own fields kept as evidence
    surplus = receipt["x402"]["payment_response_fields"]["surplus_credited_microunits"]
    assert surplus > 0 and receipt["x402"]["payment_response_fields"]["quote_id"] == receipt["x402"]["quote_id"]
    assert receipt["x402"]["surplus_credit_state"] == "credited_per_provider_receipt_unverified"
    assert (receipt["provider_credit"]["state"], receipt["provider_credit"]["atomic"]) == ("credited", surplus)
    assert (receipt["credential_fingerprint"], receipt["transport_mode"]) == ("", "x402")
    assert monetary.names() == ["reserve", "mark_dispatched", "settle"]
    assert rig.token not in json.dumps(response.provider_metadata)


# --- refused before a byte leaves -----------------------------------------------------------------------------


def test_prepaid_dispatch_fails_unavailable_until_a_monetary_authority_is_integrated(rig) -> None:
    _approve()
    adapter, _manifest = _lane()
    with pytest.raises(UsePodDispatchRefusedError) as caught:
        adapter.invoke(_request("Is 221 prime?"))
    assert caught.value.code == "monetary_authority_unavailable"
    assert rig.service.requests_to(OPENAI) == []
    evidence = caught.value.provider_evidence["usepod"]
    receipt = caught.value.provider_evidence["receipt"]
    assert receipt["monetary_authority"] == "unavailable:monetary_authority_not_integrated"
    assert receipt["cost"]["liability_bound_atomic"] == evidence["liability"]["max_amount_atomic"] > 0
    assert evidence["liability"]["basis"]["max_input_microunits_per_million"] == MARKET[0]


def test_a_monetary_refusal_stops_the_call_before_it_is_sent(rig) -> None:
    _approve()
    adapter, _manifest = _lane()
    monetary = _monetary(refuse="usdc_daily_cap_reached")
    with pytest.raises(UsePodDispatchRefusedError) as caught:
        adapter.invoke(_request("Is 223 prime?"))
    assert caught.value.code == "usdc_daily_cap_reached"
    assert monetary.names() == ["refused"]
    assert rig.service.requests_to(OPENAI) == []


def _route_not_approved(rig):
    status, payload = _post("/api/cloud/usepod/route-policy", {"mode": "marketplace-only"})
    assert status == 200, payload
    return _lane()[0], _request("Name a river in Peru."), "route_not_approved"


def _store_policy_moved_after_approval(rig):
    _approve()
    adapter = _lane()[0]
    path = routing._store_path()
    record = json.loads(path.read_text(encoding="utf-8"))
    record["policy"] = routing.RoutePolicy(mode=routing.RoutingMode.CENTRALIZED_ONLY).to_dict()
    path.write_text(json.dumps(record), encoding="utf-8")
    return adapter, _request("Name a river in Chile."), "route_policy_changed_since_approval"


def _model_disappeared(rig):
    _approve()
    adapter = _lane()[0]
    rig.service.models.pop(MODEL)
    pricing._cache_path().unlink()
    return adapter, _request("Name a river in Bolivia."), "model_disappeared"


def _price_rose_above_bound(rig):
    _approve()
    adapter = _lane()[0]
    rig.service.models[MODEL] = [Listing("marketplace", MARKET_ID, MARKET[0] + 10_000, MARKET[1]), Listing("centralized", *CENTRAL)]
    pricing._cache_path().unlink()
    return adapter, _request("Name a river in Ecuador."), "route_price_above_approved_bound"


def _feed_unavailable(rig):
    _approve()
    adapter = _lane()[0]
    rig.service.feed_status = 500
    pricing._cache_path().unlink()
    return adapter, _request("Name a lake in Peru."), "price_feed_unavailable"


def _feed_stale(rig):
    _approve()
    adapter = _lane()[0]
    rig.service.feed_status = 500
    path = pricing._cache_path()
    record = json.loads(path.read_text(encoding="utf-8"))
    record["fetched_at"] = float(record["fetched_at"]) - 10_000
    path.write_text(json.dumps(record), encoding="utf-8")
    return adapter, _request("Name a lake in Chile."), "price_stale"


def _row_changed(changes: dict, code: str):
    def scenario(rig):
        _approve()
        adapter = _lane()[0]
        _serve_feed_row(rig, **changes)
        return adapter, _request("Name a glacier in Peru."), code

    return scenario


def _privacy_class(label: str, code: str):
    def scenario(rig):
        _approve()
        return _lane()[0], _request("Summarize the attached notes.", metadata={"privacy_class": label}), code

    return scenario


def _token_removed(rig):
    _approve()
    adapter = _lane()[0]
    status, payload = _post("/api/settings/credentials", {"provider": "usepod", "delete": True})
    assert status == 200 and payload["removed"] is True, payload
    return adapter, _request("Name a desert in Peru."), "usepod_token_not_configured"


def _repoint(rig):
    rig.other = rig.make(tokens={rig.token: BALANCE}, models={MODEL: list(rig.service.models[MODEL])})
    status, saved = _post("/api/settings/credentials", {"provider": "usepod", "value": rig.token, "base_url": rig.other.origin})
    assert status == 200, saved


def _origin_repointed_under_a_built_lane(rig):
    _approve()
    adapter = _lane()[0]
    _repoint(rig)
    return adapter, _request("Name a desert in Chile."), "lane_origin_differs_from_credential_origin"


def _origin_repointed_and_lane_rebuilt(rig):
    from core.model_registry import ModelRegistry

    _approve()
    _lane()
    _repoint(rig)
    manifest = ModelRegistry().get_manifest("usepod-byok", MODEL)
    assert manifest.runtime_config["base_url"] == rig.other.origin
    return ModelRegistry().build_adapter(manifest), _request("Name a desert in Bolivia."), "route_approval_is_for_another_origin"


def _no_output_ceiling(rig):
    _approve()
    return _lane()[0], _request("Name a volcano in Peru.", max_output_tokens=None), "max_output_tokens_required"


REFUSALS = [
    pytest.param(_route_not_approved, id="route-not-approved"),
    pytest.param(_store_policy_moved_after_approval, id="store-policy-moved-after-approval"),
    pytest.param(_model_disappeared, id="model-disappeared"),
    pytest.param(_price_rose_above_bound, id="price-rose-above-bound"),
    pytest.param(_feed_unavailable, id="feed-unavailable"),
    pytest.param(_feed_stale, id="feed-stale"),
    pytest.param(_row_changed({"cheapest_input_per_1m": -5}, "price_row_malformed"), id="negative-price"),
    pytest.param(_row_changed({"cheapest_input_per_1m": 0.42}, "price_row_malformed"), id="price-in-usdc-not-microunits"),
    pytest.param(_row_changed({"centralized_output_per_1m": 10**13}, "price_row_malformed"), id="implausibly-large-price"),
    pytest.param(_row_changed({"pricing_mode": "per_request"}, "pricing_mode_per_request_unsupported"), id="per-request-pricing"),
    pytest.param(_privacy_class("source-code", "route_trust:route_trust_grant_required:source-code"), id="sensitive-data-without-grant"),
    pytest.param(_privacy_class("secrets", "route_trust:privacy_class_never_remote:secrets"), id="secrets-never-remote"),
    pytest.param(_token_removed, id="token-removed"),
    pytest.param(_origin_repointed_under_a_built_lane, id="origin-repointed-under-built-lane"),
    pytest.param(_origin_repointed_and_lane_rebuilt, id="origin-repointed-lane-rebuilt"),
    pytest.param(_no_output_ceiling, id="no-output-ceiling"),
]


@pytest.mark.parametrize("scenario", REFUSALS)
def test_a_dispatch_that_cannot_be_authorized_is_refused_before_a_byte_is_sent(rig, scenario) -> None:
    monetary = _monetary()
    adapter, request, code = scenario(rig)
    with pytest.raises(UsePodDispatchRefusedError) as caught:
        adapter.invoke(request)
    assert caught.value.code == code
    for service in (rig.service, rig.other):
        if service is not None:
            assert service.requests_to(OPENAI) == [] and service.requests_to(ANTHROPIC) == []
    assert monetary.names() == []
    receipt = caught.value.provider_evidence["receipt"]
    assert (receipt["status"], receipt["code"]) == ("refused_before_send", code)
    assert rig.token not in _rendered(caught.value) + json.dumps(caught.value.provider_evidence, default=str)


# --- served, but not on terms the approval allowed ------------------------------------------------------


NON_COMPLIANT_ROUTES = [
    pytest.param(None, {"force_listing": Listing("centralized", *CENTRAL)}, "violated", ["route_class_not_permitted:centralized"], id="centralized-under-marketplace-only"),
    pytest.param(None, {"omit_route_headers": True}, "unverified", ["route_header_missing"], id="no-route-headers"),
    pytest.param(None, {"route_header_value": "mystery-lane"}, "unverified", ["route_header_unrecognized"], id="unrecognized-route"),
    pytest.param(None, {"provider_id_value": "together"}, "unverified", ["provider_id_shape_inconsistent_with_route"], id="name-where-uuid-expected"),
    pytest.param(
        {"mode": "centralized-only", "pinned_providers": ["groq"]}, {"provider_id_value": "together"}, "violated", ["provider_not_in_pin:together"], id="provider-outside-the-pin"
    ),
]


@pytest.mark.parametrize(("policy", "faults", "compliance", "reasons"), NON_COMPLIANT_ROUTES)
def test_an_answer_from_a_route_the_approval_did_not_permit_is_not_returned_and_its_cost_is_retained(rig, policy, faults, compliance, reasons) -> None:
    _approve(policy)
    adapter, _manifest = _lane()
    monetary = _monetary()
    rig.service.faults.update(faults)
    with pytest.raises(UsePodRouteNotCompliantError) as caught:
        adapter.invoke(_request("Translate 'harbor' into French."))
    assert (caught.value.compliance, list(caught.value.reasons)) == (compliance, reasons)
    assert monetary.names() == ["reserve", "mark_dispatched", "retain_unknown"]
    outcome = "completed_on_unpermitted_route" if compliance == "violated" else "completed_route_unverified"
    assert caught.value.provider_evidence["receipt"]["settlement"] == {"outcome": outcome, "recording": "retained_unknown"}
    assert "synthetic reply" not in json.dumps(caught.value.provider_evidence)
    assert len(rig.service.requests_to(OPENAI)) == 1


@pytest.mark.parametrize(
    ("faults", "expected"),
    [
        pytest.param({"omit_balance_header": True}, {"state": "not_reported", "raw": None, "decimal": None}, id="absent"),
        pytest.param({"balance_header_value": "12,5"}, {"state": "reported", "raw": "12,5", "decimal": None}, id="unparseable"),
        pytest.param({"balance_header_value": "-0.000150"}, {"state": "reported", "raw": "-0.000150", "decimal": "-0.000150"}, id="negative-kept-as-reported"),
    ],
)
def test_the_balance_header_is_recorded_as_reported_and_its_absence_is_never_zero(rig, faults, expected) -> None:
    _approve()
    adapter, _manifest = _lane()
    _monetary()
    rig.service.faults.update(faults)
    response = adapter.invoke(_request("Count the letters in 'albatross'."))
    receipt = response.provider_metadata["receipt"]
    assert {key: receipt["balance_remaining"][key] for key in ("state", "raw", "decimal")} == expected
    assert receipt["route"]["compliance"] == "compliant"
    assert ("balance_header_unparseable" in receipt["route"]["reasons"]) is (expected["raw"] is not None and expected["decimal"] is None)


# --- failures after the request left ---------------------------------------------------------------------


def test_an_unreadable_body_is_retained_as_unknown_not_settled(rig) -> None:
    _approve()
    adapter, _manifest = _lane()
    monetary = _monetary()
    rig.service.faults["malformed_body"] = True
    with pytest.raises(MalformedProviderResponseError) as caught:
        adapter.invoke(_request("Give one synonym for 'brisk'."))
    assert monetary.names() == ["reserve", "mark_dispatched", "retain_unknown"]
    receipt = caught.value.provider_evidence["receipt"]
    assert (receipt["status"], receipt["settlement"]) == ("response_unusable", {"outcome": "outcome_unknown", "recording": "retained_unknown"})


def test_a_token_without_balance_gets_a_typed_402_and_the_liability_is_retained(rig) -> None:
    _approve()
    adapter, _manifest = _lane()
    monetary = _monetary()
    rig.service.tokens[rig.token] = 0
    with pytest.raises(UsePodTransportError) as caught:
        adapter.invoke(_request("Summarize 'tidal lock' in six words."))
    assert (caught.value.code, caught.value.http_status, caught.value.dispatch_state, caught.value.detail) == (
        "payment_or_balance_required",
        402,
        "response_received",
        "insufficient_balance",
    )
    assert monetary.names() == ["reserve", "mark_dispatched", "retain_unknown"]
    receipt = caught.value.provider_evidence["receipt"]
    assert (receipt["status"], receipt["code"], receipt["settlement"]["recording"]) == ("failed_after_send", "payment_or_balance_required", "retained_unknown")
    assert rig.token not in _rendered(caught.value) + json.dumps(caught.value.provider_evidence, default=str)


def test_a_provider_price_that_moved_after_the_feed_answered_is_a_typed_503_not_an_overspend(rig) -> None:
    _approve()
    adapter, _manifest = _lane()
    monetary = _monetary()
    # The cached snapshot is still fresh and still shows the approved price; the provider's own listing moved.
    rig.service.models[MODEL] = [Listing("marketplace", MARKET_ID, 480_000, 1_300_000), Listing("centralized", *CENTRAL)]
    with pytest.raises(UsePodTransportError) as caught:
        adapter.invoke(_request("Pick the larger: 0.7 or 0.65?"))
    assert (caught.value.code, caught.value.http_status, caught.value.detail) == ("no_provider_available", 503, "no_provider_at_price")
    [arrived] = rig.service.requests_to(OPENAI)
    assert arrived["headers"]["x-pod-max-price-input"] == str(MARKET[0])
    assert monetary.names() == ["reserve", "mark_dispatched", "retain_unknown"]


@pytest.mark.parametrize(
    ("protocol", "faults", "error_type"),
    [
        pytest.param("openai", {"cut_stream_after_events": 2}, IncompleteStreamError, id="openai-cut-mid-stream"),
        pytest.param("openai", {"omit_stream_terminator": True}, IncompleteStreamError, id="openai-no-done-marker"),
        pytest.param("anthropic", {"cut_stream_after_events": 3}, IncompleteStreamError, id="anthropic-cut-mid-stream"),
        pytest.param("anthropic", {"anthropic_error_event": True}, AnthropicStreamError, id="anthropic-error-event"),
    ],
)
def test_a_stream_that_does_not_finish_is_a_typed_failure_with_its_cost_retained(rig, protocol, faults, error_type) -> None:
    _approve()
    adapter, _manifest = _lane(protocol=protocol)
    monetary = _monetary()
    rig.service.faults.update(faults)
    seen = []
    with pytest.raises(error_type) as caught:
        for chunk in adapter.stream_text_task(_request("Describe a lighthouse in ten words.")):
            seen.append(chunk)
    assert not any(chunk.done for chunk in seen)
    assert monetary.names() == ["reserve", "mark_dispatched", "retain_unknown"]
    assert caught.value.provider_evidence["receipt"]["settlement"] == {"outcome": "partial_stream", "recording": "retained_unknown"}


def test_a_consumer_that_abandons_a_stream_leaves_the_liability_retained(rig) -> None:
    _approve()
    adapter, _manifest = _lane()
    monetary = _monetary()
    stream = adapter.stream_text_task(_request("List four moons of Jupiter."))
    first = next(stream)
    assert first.delta_text and not first.done
    stream.close()
    assert monetary.names() == ["reserve", "mark_dispatched", "retain_unknown"]
    assert monetary.calls[-1][1].detail == "GeneratorExit"


def test_a_call_that_times_out_after_sending_is_retained_never_released(rig) -> None:
    _approve()
    adapter, _manifest = _lane(runtime_overrides={"timeout_seconds": 1.5})
    monetary = _monetary()
    rig.service.faults["delay_seconds"] = 4
    with pytest.raises(UsePodTransportError) as caught:
        adapter.invoke(_request("What is 17 squared?"))
    assert (caught.value.code, caught.value.dispatch_state) == ("transfer_ended_without_response", "sent_outcome_unknown")
    # mark_dispatched now records AFTER the request left, so a send that ends without a response
    # never claims a dispatch it cannot prove: the claim's dispatching state + retained unknown is
    # the honest terminal (the money law reconciles a dead claimant the same way).
    assert monetary.names() == ["reserve", "retain_unknown"]
    assert caught.value.provider_evidence["receipt"]["settlement"]["recording"] == "retained_unknown"


def test_a_redirect_from_the_proxy_is_refused_and_the_other_origin_sees_nothing(rig) -> None:
    _approve()
    adapter, _manifest = _lane()
    monetary = _monetary()
    elsewhere = rig.make(tokens={rig.token: BALANCE}, models=dict(rig.service.models))
    rig.service.faults["redirect_location"] = f"{elsewhere.origin}/proxy/{rig.token}/v1/chat/completions"
    with pytest.raises(UsePodTransportError) as caught:
        adapter.invoke(_request("Name the capital of Mongolia."))
    assert (caught.value.code, caught.value.http_status) == ("redirect_refused", 302)
    assert elsewhere.requests == []
    assert monetary.names() == ["reserve", "mark_dispatched", "retain_unknown"]
    assert rig.token not in _rendered(caught.value) + json.dumps(caught.value.provider_evidence, default=str)


def test_an_unreachable_proxy_is_recorded_as_outcome_unknown_and_retained(rig) -> None:
    _approve()
    adapter, _manifest = _lane()
    monetary = _monetary()
    rig.service.stop()
    with pytest.raises(UsePodTransportError) as caught:
        adapter.invoke(_request("Name the capital of Laos."))
    # The HTTP worker does not report whether a connection was ever established, so an unanswered
    # request is never assumed unsent.
    assert (caught.value.code, caught.value.dispatch_state) == ("transfer_ended_without_response", "sent_outcome_unknown")
    # mark_dispatched now records AFTER the request left, so a send that ends without a response
    # never claims a dispatch it cannot prove: the claim's dispatching state + retained unknown is
    # the honest terminal (the money law reconciles a dead claimant the same way).
    assert monetary.names() == ["reserve", "retain_unknown"]


# --- nothing inherited reaches the network, and identity follows the credential --------------------------


def test_no_inherited_path_puts_the_token_in_a_header_or_sends_outside_the_sealed_dispatch(rig) -> None:
    from core.model_registry import ModelRegistry

    _approve()
    _adapter, manifest = _lane()
    _monetary()
    rig.service.reply = _call_first_offered_tool
    # Without a configured dialect or declared tool support, the parent lane would PROBE a loopback
    # origin for tool support -- a real request with a bearer header built from the credential slot.
    stripped = manifest.model_copy(
        update={
            "runtime_config": {key: value for key, value in manifest.runtime_config.items() if key != "tool_dialect"},
            "metadata": {key: value for key, value in manifest.metadata.items() if key != "tool_support"},
        }
    )
    adapter = ModelRegistry().build_adapter(stripped)

    response = adapter.invoke(_request("Read the changelog the issue links to.", output_mode="tool_intent", tools=(READ_FILE,)))

    assert response.tool_calls[0].intent == READ_FILE.intent
    assert adapter.prewarm()["status"] == "skipped"
    assert "Authorization" not in adapter._headers()
    refusals = (
        adapter.tool_certification_backend_version,
        adapter.tool_certification_runtime_identity,
        lambda: adapter.tool_certification_exchange(messages=[], tools=(), tool_choice=None, max_output_tokens=1, timeout_seconds=1.0),
    )
    for attempt in refusals:
        with pytest.raises(ValueError, match="paid remote marketplace"):
            attempt()
    assert [item["path"] for item in rig.service.requests if item["method"] == "POST"] == [OPENAI]
    assert all("authorization" not in item["headers"] for item in rig.service.requests)


def test_rotating_the_token_changes_the_credential_identity_and_orphans_its_discovery(rig) -> None:
    status, refreshed = _post("/api/cloud/usepod/refresh", {})
    assert status == 200 and refreshed["credential"]["state"] == "observed", refreshed
    before = _get("/api/cloud/usepod/discovery")
    assert before["credential_discovery"]["models"]["model_ids"] == [MODEL]
    assert before["credential"]["fingerprint"] == rig.fingerprint
    rows = {(row["surface"], row["capability"]): row for row in before["capabilities"]}
    balance_row, feed_row = rows[("prepaid", "balance_read")], rows[("marketplace_feed", "per_model_route_prices")]
    assert (balance_row["evidence"], balance_row["endpoint"]) == ("live_observed", "GET /proxy/{token}/balance")
    assert balance_row["observed_at"] == before["credential_discovery"]["observed_at"]
    assert feed_row["expires_at"] == feed_row["observed_at"] + before["marketplace"]["ttl_seconds"]
    assert rows[("prepaid_openai", "native_tool_calls")]["observed_at"] is None

    rotated = str(uuid.uuid4())
    rig.service.tokens[rotated] = 1_000_000
    status, saved = _post("/api/settings/credentials", {"provider": "usepod", "value": rotated, "base_url": rig.service.origin})
    assert status == 200, saved

    after = _get("/api/cloud/usepod/discovery")
    assert after["credential"]["fingerprint"] == saved["credential_fingerprint"] != rig.fingerprint
    assert after["credential_discovery"] is None
    # The public price snapshot is keyed by origin, not by credential, so it survives the rotation.
    assert (after["marketplace"]["origin"], after["marketplace"]["evidence"]) == (rig.service.origin, "cache")
    rendered = json.dumps(before) + json.dumps(after) + json.dumps(saved)
    assert rig.token not in rendered and rotated not in rendered


def test_price_pause_keeps_request_and_reserves_only_after_resume(rig, monkeypatch):
    """Actual adapter + loopback provider; no reservation or HTTP call while paused."""
    import time
    from core import runtime_continuity as queue
    from core.usepod import price_wait
    _approve()
    adapter, _ = _lane()
    money = _monetary()
    price_wait.save_target(model_id=MODEL, max_input_usdc='0.42', max_output_usdc='1.26',
                          poll_minutes=1, active_price_tolerance_percent=10)
    facts = price_wait.queued_target(MODEL)
    row = queue.enqueue_message(session_id='pause:adapter', payload={'text':'task', 'price_wait':facts})
    queue.claim_next_message('pause:adapter')
    request = _request('Continue using the previous tool result: exactly 19 files were processed.')
    request.cancel_check = lambda: False
    events = []
    monkeypatch.setattr(price_wait, 'emit_runtime_event', lambda ctx, **ev: events.append(ev))
    ticks = [time.monotonic()]
    monkeypatch.setattr(price_wait.time, 'monotonic', lambda: ticks[0])
    monkeypatch.setattr('core.usepod.spend_approval.prepaid_spend_readiness', lambda model: {'available':True})
    with price_wait.running_task('pause:adapter', 'task', queue_item_id=row['queue_item_id']):
        first = adapter.run_text_task(_request('First model call.'))
        assert first.output_text
        # Supply parsed fresh snapshots at the same owning seam; wire inference remains real loopback.
        from dataclasses import replace
        from tests.usepod.test_usepod_routing_policy import _snap, _row
        high = replace(_snap(_row(MODEL, market=(900000, 1260000)), fetched_at=time.time()), origin=rig.service.origin)
        low = replace(_snap(_row(MODEL, market=(420000, 1260000)), fetched_at=time.time()), origin=rig.service.origin)
        monkeypatch.setattr(price_wait.pricing, 'current_snapshot', lambda **kw: SimpleNamespace(snapshot=high, state='fresh', error_code=''))
        monkeypatch.setattr(price_wait.pricing, 'fetch_marketplace_snapshot', lambda **kw: low)
        before = len(money.calls)
        def sleep(seconds):
            assert len(money.calls) == before
            ticks[0] += seconds
        monkeypatch.setattr(price_wait, '_sleep', sleep)
        response = adapter.run_text_task(request)
    arrived = rig.service.requests_to(OPENAI)
    assert len(arrived) == 2
    assert json.loads(arrived[-1]['body'])['messages'][-1]['content'] == request.prompt
    assert response.output_text == default_reply(json.loads(arrived[-1]['body']), 'openai').text
    assert [ev['event_type'] for ev in events] == ['usepod_price_paused','usepod_price_resumed']
