"""The cloud broker resolves the answer budget against the SERVING model, not the prompt table.

Root cause this pins (2026-09-06, private profile, real provider): a grounded VW Passat/Golf
comparison reached ``nvidia/nemotron-3.5-lightning:free`` with the chat table's ``max_tokens: 520``.
The model spent all 520 tokens reasoning, the provider ended the choice ``finish_reason: "length"``
and returned the half-written monologue as ``content``; the runtime treated it as the answer and
the grounding gate refused the turn. The same request at 2048 returned a sourced answer.

The repair wires the ONE output-budget authority (``core.output_budget_policy``) into the broker at
the point where the model's catalog row is known, refuses ``length`` completions in the router's
validator instead of validating their bytes as prose, and carries the typed reason to the Activity
trail. No retry is added: a truncated call is fixed by a higher ceiling, never by a second call.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from core.cloud_broker import lane_output_budget
from core.cloud_provider_contract import CloudModelResponse, PricingState
from core.cloud_routing import CloudRouteMode
from core.memory_first_router import MemoryFirstRouter
from tests.test_cloud_broker import REQ, REQUEST, _broker, _model, _Provider

_WINDOW = 1_000_000
_CAP = 65_536


def _reasoning_model(**overrides):
    base = _model()
    return replace(
        base,
        context_window=overrides.pop("context_window", _WINDOW),
        max_output_tokens=overrides.pop("max_output_tokens", _CAP),
        capabilities=overrides.pop("capabilities", ("reasoning", "text")),
        **overrides,
    )


def _events(sink_calls, event_type):
    return [details for name, details in sink_calls if name == event_type]


def test_a_reasoning_capable_free_model_is_handed_the_free_target_plus_the_thinking_reserve(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    model = _reasoning_model()
    provider = _Provider([(model,)], [CloudModelResponse("answer", {"completion_tokens": 900}, finish_reason="stop")])
    sink_calls: list[tuple[str, dict]] = []
    sent: list = []
    original_send = provider.send_request

    def _capture(transport, request):
        sent.append(request)
        return original_send(transport, request)

    provider.send_request = _capture
    result = _broker(provider).execute(
        replace(REQUEST, max_output_tokens=520),
        requirements=REQ,
        mode=CloudRouteMode.LOCAL_FREE,
        event_sink=lambda name, details: sink_calls.append((name, details)),
    )
    assert result.used_cloud
    # 520 asked -> free-cloud target 1800 -> +2048 reasoning reserve; both hard caps have room.
    assert sent[0].max_output_tokens == 1800 + 2048
    started = _events(sink_calls, "model.call_started")[0]
    assert started["max_output_tokens"] == 3848
    assert started["output_budget"] == {
        "intent_base": 520,
        "tokens": 3848,
        "source": "free_cloud_target",
        "capped_by": "none",
        "reserve_applied": 2048,
        "thinking_capable": True,
    }
    completed = _events(sink_calls, "model.call_completed")[0]
    assert completed["finish_reason"] == "stop"
    assert completed["max_output_tokens"] == 3848
    assert completed["completion_tokens"] == 900


def test_a_model_the_catalog_does_not_flag_as_reasoning_gets_no_reserve() -> None:
    budget = lane_output_budget(replace(REQUEST, max_output_tokens=520), _reasoning_model(capabilities=("text",)))
    assert budget.tokens == 1800 and budget.reserve_applied == 0


def test_the_published_completion_cap_binds_the_lift_and_names_itself() -> None:
    budget = lane_output_budget(replace(REQUEST, max_output_tokens=520), _reasoning_model(max_output_tokens=1000))
    assert budget.tokens == 1000
    assert budget.capped_by == "capability_max_output_tokens"


def test_a_paid_model_is_lifted_to_the_paid_target_and_its_estimate_is_rechecked_on_that_number(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    monkeypatch.setattr("core.cloud_broker.settle_spend", lambda model_call_id, **kwargs: None)
    paid = _reasoning_model(pricing_state=PricingState.PAID, input_usd_per_token=0.001, output_usd_per_token=0.001, request_usd=0.0)
    assert lane_output_budget(replace(REQUEST, max_output_tokens=20), paid).tokens == 760 + 2048
    provider = _Provider([(paid,)], [CloudModelResponse("paid", {"input_tokens": 2, "output_tokens": 3})])
    authorized = SimpleNamespace(
        model_call_id="paid-call",
        escalation=SimpleNamespace(model_id="model", capsule=SimpleNamespace(task_id="task")),
        reservation=SimpleNamespace(status="reserved", model_call_id="paid-call"),
    )
    # The reservation covers the prompt table's 20 tokens but not the 2808 the lane will really
    # send: the recheck must see the real number and refuse, never spend past the reservation.
    result = _broker(provider).execute(
        replace(REQUEST, model_call_id="paid-call", max_output_tokens=20),
        requirements=REQ,
        mode=CloudRouteMode.LOCAL_FREE_PAID,
        authorized_paid_call=authorized,
        paid_budget_remaining_usd=0.5,
    )
    assert not result.used_cloud
    assert "paid_budget_exceeded" in result.errors
    assert provider.send_count == 0


def test_the_router_validator_refuses_a_length_completion_instead_of_validating_its_bytes(monkeypatch) -> None:
    captured: dict = {}

    class _Broker:
        def execute(self, request, **kwargs):
            captured["validator"] = kwargs["response_validator"]
            from core.cloud_broker import CloudBrokerResult

            return CloudBrokerResult(False, None, fallback_reason="captured")

    from adapters.base_adapter import ModelRequest
    from core.cloud_privacy_policy import CloudPrivacyGrant
    from core.cloud_provider_contract import CloudTaskRequirements, PrivacyClass

    router = MemoryFirstRouter(cloud_broker=_Broker())
    monkeypatch.setattr(
        "core.memory_first_router.cloud_escalation_policy.load_policy",
        lambda: SimpleNamespace(free_cloud_enabled=True, auto_free_model="auto"),
    )
    context = {
        "surface": "cli",
        "session_id": "session",
        "turn_id": "turn",
        "cloud_task_requirements": CloudTaskRequirements(
            min_context_tokens=10, expected_output_tokens=0, required_capabilities=("text",), privacy_class=PrivacyClass.PUBLIC
        ),
        "cloud_privacy_grant": CloudPrivacyGrant(),
    }
    router._try_free_cloud_boost(
        request=ModelRequest(task_kind="reasoning", prompt="compare", messages=[{"role": "user", "content": "compare"}], output_mode="plain_text", max_output_tokens=520),
        task=SimpleNamespace(task_id="task"),
        task_hash="hash",
        output_mode="plain_text",
        source_context=context,
    )
    validate = captured["validator"]
    truncated = CloudModelResponse(
        "Here's a thinking process: the user wants a comparison, so I should first",
        {"completion_tokens": 520},
        finish_reason="length",
    )
    ok, reason = validate(truncated)
    assert ok is False
    # The reason carries the completion's own termination facts: the reasoning-token count and
    # the ceiling the request went out with, each "unreported"/"unknown" when the provider or
    # the adapter did not say (2026-09-16: an empty or truncated paid reply must be classifiable
    # from its record, never from a guess).
    assert reason == (
        "output_truncated:finish_reason=length:completion_tokens=520"
        ":reasoning_tokens=unreported:max_output_tokens=unknown"
    )
    finished = CloudModelResponse("Passat and Golf differ in size and price.", {"completion_tokens": 40}, finish_reason="stop")
    assert validate(finished)[0] is True
    # An adapter that never read a finish reason (the empty default) is validated on its bytes.
    assert validate(CloudModelResponse("Passat and Golf differ in size and price.", {}))[0] is True


def test_the_broker_carries_the_validator_reason_beside_the_error_kind(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    model = _reasoning_model()
    provider = _Provider([(model,)], [CloudModelResponse("half a thought", {"completion_tokens": 3848}, finish_reason="length")])

    def _refuse(response):
        return False, "output_truncated:finish_reason=length:completion_tokens=3848"

    result = _broker(provider).execute(
        replace(REQUEST, max_output_tokens=520),
        requirements=REQ,
        mode=CloudRouteMode.LOCAL_FREE,
        response_validator=_refuse,
    )
    assert not result.used_cloud
    assert result.errors[0] == "malformed_response"
    assert result.error_details[0] == "validator:output_truncated:finish_reason=length:completion_tokens=3848"
    # One bounded same-provider retry of a malformed turn is the existing contract; both attempts
    # are recorded, and no third call is made.
    assert provider.send_count == 2
    assert len(result.error_details) == 2
