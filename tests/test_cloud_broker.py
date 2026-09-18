from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from adapters.cloud_provider_common import CloudProviderRequestError, classify_error
from core.cloud_broker import CloudModelBroker
from core.cloud_privacy_policy import CloudPrivacyGrant
from core.cloud_provider_contract import (
    CloudAccountLimits,
    CloudModelMetadata,
    CloudModelRequest,
    CloudModelResponse,
    CloudTaskRequirements,
    PricingState,
    PrivacyClass,
)
from core.cloud_route_receipt import list_cloud_route_receipts
from core.cloud_routing import CloudRouteMode


def _model(model_id="model", *, state=PricingState.FREE, price=0.0, provider_id="provider"):
    return CloudModelMetadata(
        provider_id=provider_id,
        model_id=model_id,
        display_name=model_id,
        pricing_state=state,
        input_usd_per_token=price,
        output_usd_per_token=price,
        request_usd=price,
        context_window=8192,
        capabilities=("text",),
        discovered_at="2026-07-14T00:00:00+00:00",
        expires_at="2099-01-01T00:00:00+00:00",
        health_state="healthy",
        quota_remaining=10,
    )


class _Provider:
    def __init__(self, snapshots, responses=None, provider_id="provider", limits=None):
        self.provider_id = provider_id
        self.snapshots = list(snapshots)
        self.responses = list(responses or [CloudModelResponse("ok", {"cost": 0.0})])
        self.send_count = 0
        self.sent_model_ids = []
        self.limits = limits or CloudAccountLimits()

    def discover_models(self, _transport):
        return self.snapshots.pop(0) if len(self.snapshots) > 1 else self.snapshots[0]

    def send_request(self, _transport, _request):
        self.send_count += 1
        self.sent_model_ids.append(_request.model_id)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def classify_provider_error(self, error):
        return classify_error(error)

    def validate_credentials(self, _transport):
        return True, "ok"

    def normalize_model_metadata(self, payload, *, discovered_at):
        return None

    def get_account_limits(self, _transport):
        return self.limits

    def estimate_request_cost(self, model, *, input_tokens, output_tokens):
        return 0.0

    def check_model_health(self, _transport, model):
        return {"ok": True}

    def parse_usage(self, payload):
        return {}

    def revoke_or_clear_session_credentials(self):
        return None


class _Registry:
    def __init__(self, providers):
        self.providers = {provider.provider_id: provider for provider in providers}

    def list(self):
        return tuple(self.providers.values())

    def get(self, provider_id):
        return self.providers.get(provider_id)


REQ = CloudTaskRequirements(
    min_context_tokens=100,
    expected_output_tokens=20,
    required_capabilities=("text",),
    privacy_class=PrivacyClass.PUBLIC,
)
REQUEST = CloudModelRequest(
    task_id="task",
    turn_id="turn",
    subtask_id="sub",
    model_call_id="call",
    model_id="",
    messages=({"role": "user", "content": "hello"},),
    max_output_tokens=20,
    metadata={"session_id": "broker-session"},
)


def _broker(provider, *, sleeper=lambda _seconds: None):
    return CloudModelBroker(
        registry=_Registry([provider]),
        transports={provider.provider_id: object()},
        sleeper=sleeper,
    )


def test_local_only_never_discovers_or_sends(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    provider = _Provider([(_model(),)])
    result = _broker(provider).execute(REQUEST, requirements=REQ, mode=CloudRouteMode.LOCAL_ONLY)
    assert not result.used_cloud and provider.send_count == 0
    assert len(provider.snapshots) == 1


def test_free_price_turns_paid_before_execution_and_call_stops(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    free = _model()
    paid = replace(free, pricing_state=PricingState.PAID, input_usd_per_token=0.001)
    provider = _Provider([(free,), (paid,)])
    result = _broker(provider).execute(REQUEST, requirements=REQ, mode=CloudRouteMode.LOCAL_FREE)
    assert not result.used_cloud and provider.send_count == 0
    assert "not_verified_free" in result.errors


def test_model_disappears_on_execution_refresh(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    provider = _Provider([(_model(),), ()])
    result = _broker(provider).execute(REQUEST, requirements=REQ, mode=CloudRouteMode.LOCAL_FREE)
    assert not result.used_cloud and provider.send_count == 0
    assert "model_removed" in result.errors


def test_account_quota_is_applied_before_route_selection(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    provider = _Provider([(_model(),)], limits=CloudAccountLimits(quota_remaining=0))
    result = _broker(provider).execute(REQUEST, requirements=REQ, mode=CloudRouteMode.LOCAL_FREE)
    assert not result.used_cloud and provider.send_count == 0
    assert result.fallback_reason == "no_eligible_cloud_route"


def test_success_writes_signed_preflight_and_terminal_receipts(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    provider = _Provider([(_model(),)])
    result = _broker(provider).execute(REQUEST, requirements=REQ, mode=CloudRouteMode.LOCAL_FREE)
    assert result.used_cloud and result.response and result.response.output_text == "ok"
    receipts = list_cloud_route_receipts("broker-session")
    assert [item["phase"] for item in receipts] == ["started", "completed"]
    assert all(item["signature"] for item in receipts)
    assert receipts[-1]["route_reason"] == "verified_zero_cost_capability_and_health_fit"


def test_retry_is_bounded_and_requires_fallback_permission(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    transient = CloudProviderRequestError(503, "temporary")
    provider = _Provider([(_model(),)], [transient, transient, CloudModelResponse("late", {"cost": 0.0})])
    result = _broker(provider).execute(
        REQUEST,
        requirements=REQ,
        mode=CloudRouteMode.LOCAL_FREE,
        privacy_grant=CloudPrivacyGrant(allow_provider_fallback=True),
        max_attempts=5,
    )
    assert not result.used_cloud
    assert provider.send_count == 2
    assert result.attempts == 2


def test_429_retry_after_is_honored_once(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    sleeps = []
    provider = _Provider(
        [(_model(),)],
        [
            CloudProviderRequestError(429, "rate limited", retry_after_seconds=1.25),
            CloudModelResponse("recovered", {"cost": 0.0}),
        ],
    )
    result = _broker(provider, sleeper=sleeps.append).execute(
        REQUEST,
        requirements=REQ,
        mode=CloudRouteMode.LOCAL_FREE,
        privacy_grant=CloudPrivacyGrant(allow_provider_fallback=True),
        max_attempts=5,
    )
    assert result.used_cloud and provider.send_count == 2
    assert sleeps == [1.25]


def test_malformed_model_turn_retries_once_without_provider_fanout_permission(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    provider = _Provider(
        [(_model(),)],
        [
            CloudModelResponse('{"tool":"bash","args":{"command":"pwd"}}', {"cost": 0.0}),
            CloudModelResponse('{"intent":"respond.direct","arguments":{"message":"safe"}}', {"cost": 0.0}),
        ],
    )

    def validator(response):
        return (
            '"intent"' in response.output_text,
            "foreign_tool_syntax" if '"intent"' not in response.output_text else "",
        )

    result = _broker(provider).execute(
        REQUEST,
        requirements=REQ,
        mode=CloudRouteMode.LOCAL_FREE,
        max_attempts=3,
        response_validator=validator,
    )
    assert result.used_cloud
    assert provider.send_count == 2
    assert result.attempts == 2
    assert result.response is not None
    assert result.response.output_text.startswith('{"intent"')
    assert result.errors == ("malformed_response",)


def test_repeated_malformed_model_turn_is_never_returned(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    leaked = CloudModelResponse('{"tool":"bash","args":{"command":"pwd"}}', {"cost": 0.0})
    provider = _Provider([(_model(),)], [leaked, leaked])
    result = _broker(provider).execute(
        REQUEST,
        requirements=REQ,
        mode=CloudRouteMode.LOCAL_FREE,
        max_attempts=3,
        response_validator=lambda _response: (False, "foreign_tool_syntax"),
    )
    assert not result.used_cloud
    assert result.response is None
    assert provider.send_count == 2
    assert result.errors == ("malformed_response", "malformed_response")


def test_no_private_payload_fanout_without_permission(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    first = _model("first")
    second = _model("second")
    provider = _Provider([(first, second)], [CloudProviderRequestError(503, "temporary"), CloudModelResponse("second")])
    result = _broker(provider).execute(REQUEST, requirements=REQ, mode=CloudRouteMode.LOCAL_FREE)
    assert not result.used_cloud and provider.send_count == 1


def test_reported_charge_on_verified_free_route_fails_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    provider = _Provider([(_model(),)], [CloudModelResponse("charged", {"cost": 0.01})])
    result = _broker(provider).execute(REQUEST, requirements=REQ, mode=CloudRouteMode.LOCAL_FREE)
    assert not result.used_cloud
    assert result.fallback_reason == "free_route_reported_charge"
    assert result.actual_usd == 0.01


def test_paid_route_requires_call_bound_reservation_and_settles(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    settled = {}
    monkeypatch.setattr(
        "core.cloud_broker.settle_spend",
        lambda model_call_id, *, actual_usd, **_kwargs: settled.update(call=model_call_id, actual=actual_usd),
    )
    paid = _model(state=PricingState.PAID, price=0.000001)
    provider = _Provider([(paid,)], [CloudModelResponse("paid", {"cost": 0.002})])
    mismatch = SimpleNamespace(
        model_call_id="paid-call",
        escalation=SimpleNamespace(model_id="wrong", capsule=SimpleNamespace(task_id="task")),
        reservation=SimpleNamespace(status="reserved", model_call_id="paid-call"),
    )
    blocked = _broker(provider).execute(
        REQUEST,
        requirements=REQ,
        mode=CloudRouteMode.LOCAL_FREE_PAID,
        authorized_paid_call=mismatch,
        paid_budget_remaining_usd=1.0,
    )
    assert not blocked.used_cloud and provider.send_count == 0
    assert "paid_authorization_mismatch" in blocked.errors

    authorized = SimpleNamespace(
        model_call_id="paid-call",
        escalation=SimpleNamespace(model_id="model", capsule=SimpleNamespace(task_id="task")),
        reservation=SimpleNamespace(status="reserved", model_call_id="paid-call"),
    )
    completed = _broker(provider).execute(
        replace(REQUEST, model_call_id="paid-call"),
        requirements=REQ,
        mode=CloudRouteMode.LOCAL_FREE_PAID,
        authorized_paid_call=authorized,
        paid_budget_remaining_usd=1.0,
    )
    assert completed.used_cloud and completed.actual_usd == 0.002
    assert completed.pricing_state == "paid"
    assert settled == {"call": "paid-call", "actual": 0.002}


def test_paid_settlement_failure_records_one_failed_terminal(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    monkeypatch.setattr(
        "core.cloud_broker.settle_spend",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("settlement failed")),
    )
    paid = _model(state=PricingState.PAID, price=0.000001)
    provider = _Provider([(paid,)], [CloudModelResponse("paid", {"cost": 0.002})])
    authorized = SimpleNamespace(
        model_call_id="paid-call",
        escalation=SimpleNamespace(model_id="model", capsule=SimpleNamespace(task_id="task")),
        reservation=SimpleNamespace(status="reserved", model_call_id="paid-call"),
    )
    result = _broker(provider).execute(
        replace(REQUEST, model_call_id="paid-call"),
        requirements=REQ,
        mode=CloudRouteMode.LOCAL_FREE_PAID,
        authorized_paid_call=authorized,
        paid_budget_remaining_usd=1.0,
    )
    assert not result.used_cloud
    receipts = list_cloud_route_receipts("broker-session")
    assert [item["phase"] for item in receipts] == ["started", "completed"]
    assert receipts[-1]["success"] is False
    assert receipts[-1]["actual_usd"] == 0.002


def test_paid_route_executes_only_the_exact_authorized_model(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    monkeypatch.setattr("core.cloud_broker.settle_spend", lambda *_args, **_kwargs: None)
    first = _model("a", state=PricingState.PAID, price=0.000001)
    authorized_model = _model("b", state=PricingState.PAID, price=0.000001)
    provider = _Provider([(first, authorized_model)], [CloudModelResponse("paid", {"cost": 0.001})])
    authorized = SimpleNamespace(
        model_call_id="paid-call",
        escalation=SimpleNamespace(model_id="b", capsule=SimpleNamespace(task_id="task")),
        reservation=SimpleNamespace(status="reserved", model_call_id="paid-call"),
    )
    result = _broker(provider).execute(
        replace(REQUEST, model_call_id="paid-call"),
        requirements=REQ,
        mode=CloudRouteMode.LOCAL_FREE_PAID,
        authorized_paid_call=authorized,
        paid_budget_remaining_usd=1.0,
    )
    assert result.used_cloud
    assert provider.sent_model_ids == ["b"]


def test_paid_route_calculates_cost_from_token_usage(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    settled = {}
    monkeypatch.setattr(
        "core.cloud_broker.settle_spend",
        lambda model_call_id, **kwargs: settled.update(call=model_call_id, **kwargs),
    )
    paid = _model(state=PricingState.PAID, price=0.001)
    provider = _Provider([(paid,)], [CloudModelResponse("paid", {"input_tokens": 2, "output_tokens": 3})])
    authorized = SimpleNamespace(
        model_call_id="paid-call",
        escalation=SimpleNamespace(model_id="model", capsule=SimpleNamespace(task_id="task")),
        reservation=SimpleNamespace(status="reserved", model_call_id="paid-call"),
    )
    result = _broker(provider).execute(
        replace(REQUEST, model_call_id="paid-call"),
        requirements=REQ,
        mode=CloudRouteMode.LOCAL_FREE_PAID,
        authorized_paid_call=authorized,
        paid_budget_remaining_usd=1.0,
    )
    assert result.used_cloud and result.actual_usd == 0.006
    assert settled["actual_usd"] == 0.006


def test_paid_route_without_cost_or_usage_fails_billing_ambiguous(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    settled = {}
    monkeypatch.setattr(
        "core.cloud_broker.settle_spend",
        lambda model_call_id, **kwargs: settled.update(call=model_call_id, **kwargs),
    )
    paid = _model(state=PricingState.PAID, price=0.001)
    provider = _Provider([(paid,)], [CloudModelResponse("paid", {})])
    authorized = SimpleNamespace(
        model_call_id="paid-call",
        escalation=SimpleNamespace(model_id="model", capsule=SimpleNamespace(task_id="task")),
        reservation=SimpleNamespace(status="reserved", model_call_id="paid-call"),
    )
    result = _broker(provider).execute(
        replace(REQUEST, model_call_id="paid-call"),
        requirements=REQ,
        mode=CloudRouteMode.LOCAL_FREE_PAID,
        authorized_paid_call=authorized,
        paid_budget_remaining_usd=1.0,
    )
    assert not result.used_cloud and result.fallback_reason == "paid_cost_billing_ambiguous"
    assert settled["billing_ambiguous"] is True


def test_paid_route_over_reservation_fails_closed(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    monkeypatch.setattr(
        "core.cloud_broker.settle_spend",
        lambda *_args, **_kwargs: SimpleNamespace(status="cap_breached"),
    )
    paid = _model(state=PricingState.PAID, price=0.001)
    provider = _Provider([(paid,)], [CloudModelResponse("paid", {"cost": 2.0})])
    authorized = SimpleNamespace(
        model_call_id="paid-call",
        escalation=SimpleNamespace(model_id="model", capsule=SimpleNamespace(task_id="task")),
        reservation=SimpleNamespace(status="reserved", model_call_id="paid-call"),
    )
    result = _broker(provider).execute(
        replace(REQUEST, model_call_id="paid-call"),
        requirements=REQ,
        mode=CloudRouteMode.LOCAL_FREE_PAID,
        authorized_paid_call=authorized,
        paid_budget_remaining_usd=1.0,
    )
    assert not result.used_cloud and result.fallback_reason == "paid_cap_breached"
