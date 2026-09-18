"""The cloud lane runs the operator's pinned free model and walks its chain past a gateway rejection.

Live failure 2026-09-08 (build 0.5.0, operator profile): the pinned ``openrouter-byok`` ``:free``
model was refused by the turn-routing fence as ``paid_not_permitted`` (the ranker exempted it via the
catalog, the fence carried its own copy of the rule and did not), the pin fell ``pin_unresolved``,
the free-cloud boost auto-picked a different free model, the gateway answered 40x, and the broker
ended the turn after that single attempt because provider fallback was not granted. The user saw
"I couldn't get a live model response" with two untried candidates in the signed chain.

Held-out shapes only: no test names the live models.
"""

from __future__ import annotations

import pytest

from adapters.cloud_provider_common import CloudProviderRequestError, classify_error
from core.cloud_broker import CloudModelBroker
from core.cloud_provider_contract import (
    CloudAccountLimits,
    CloudModelMetadata,
    CloudModelRequest,
    CloudModelResponse,
    CloudTaskRequirements,
    PricingState,
    PrivacyClass,
    ProviderErrorKind,
)
from core.cloud_route_receipt import list_cloud_route_receipts
from core.cloud_routing import CloudRouteMode
from core.openrouter_catalog import OpenRouterModel
from storage.model_provider_manifest import ModelProviderManifest

PINNED = "z-ai/glm-5-air:free"


# --- turn routing: one paid-fence authority -------------------------------------------------


def _catalog_row(model_id: str, *, prompt: float | None = 0.0, completion: float | None = 0.0) -> OpenRouterModel:
    return OpenRouterModel(
        model_id=model_id,
        name=model_id,
        context_length=131072,
        prompt_usd_per_token=prompt,
        completion_usd_per_token=completion,
        request_usd=None,
        supported_parameters=("tools",),
        input_modalities=("text",),
        output_modalities=("text",),
        fetched_at="2026-09-08T00:00:00+00:00",
    )


def _manifest(provider_name: str, model_name: str, *, base_url: str, cost_class: str) -> ModelProviderManifest:
    return ModelProviderManifest(
        provider_name=provider_name,
        model_name=model_name,
        source_type="http",
        adapter_type="openai_compatible",
        license_name="Apache-2.0",
        license_reference="https://example.invalid/license",
        weight_location="external",
        runtime_dependency="stub",
        capabilities=["summarize", "classify", "format", "extract", "structured_json"],
        runtime_config={"base_url": base_url, "timeout_seconds": 30},
        metadata={
            "runtime_family": "openai_compatible",
            "model_digest": "sha256:stub",
            "chat_template_hash": "tmpl",
            "quantization": "none",
            "parameter_billions": 100.0,
            "cost_class": cost_class,
        },
        enabled=True,
    )


def _byok(model_name: str) -> ModelProviderManifest:
    return _manifest("openrouter-byok", model_name, base_url="https://openrouter.ai/api/v1", cost_class="paid_cloud")


def _local() -> ModelProviderManifest:
    return _manifest("ollama", "small:2b", base_url="http://127.0.0.1:11434", cost_class="free_local")


def _plan(monkeypatch, catalog: tuple[OpenRouterModel, ...], *, pinned: str = PINNED, allow_paid: bool = False):
    import core.openrouter_catalog as catalog_module
    from core.turn_routing import mint_turn_routing_plan

    monkeypatch.setattr(catalog_module, "safe_all_models", lambda **_kw: (catalog, 0.0))
    return mint_turn_routing_plan(
        turn_id="turn-held-out",
        session_id="session-held-out",
        manifests=[_local(), _byok(pinned)],
        requested_model=pinned,
        allow_paid=allow_paid,
    )


def _byok_row(plan, model_name: str):
    return next(row for row in plan.eligibility if row.model_id == model_name)


@pytest.mark.parametrize(
    "catalog",
    [
        pytest.param((_catalog_row(PINNED),), id="priced-at-zero"),
        # OpenRouter's ``:free`` tag is the provider's own guarantee; the catalog leaves many of
        # those variants' per-token prices unpublished and they are still free (model_is_free).
        pytest.param((_catalog_row(PINNED, prompt=None, completion=None),), id="free-tag-unpublished-price"),
    ],
)
def test_a_catalog_listed_free_byok_pin_resolves_under_a_zero_cost_ceiling(monkeypatch, catalog) -> None:
    plan = _plan(monkeypatch, catalog)
    row = _byok_row(plan, PINNED)
    assert row.cost_class == "paid_cloud"  # the manifest's explicit class is unchanged; the price exempts it
    assert row.allowed and row.reason == "eligible"
    assert plan.reason == "pinned"
    assert plan.selected_model == PINNED
    assert not plan.paid_allowed


@pytest.mark.parametrize(
    "catalog",
    [
        pytest.param((_catalog_row("other-vendor/unrelated:free"),), id="absent-from-catalog"),
        pytest.param((), id="no-catalog-at-all"),
    ],
)
def test_a_free_suffix_the_catalog_does_not_list_stays_paid(monkeypatch, catalog) -> None:
    plan = _plan(monkeypatch, catalog)
    row = _byok_row(plan, PINNED)
    assert not row.allowed and row.reason == "paid_not_permitted"
    assert plan.reason == "pin_unresolved"
    assert plan.selected_provider == ""


def test_a_paid_byok_model_is_still_refused_and_admitted_only_with_the_paid_grant(monkeypatch) -> None:
    paid = "z-ai/glm-5-air"
    refused = _plan(monkeypatch, (_catalog_row(paid, prompt=1e-6, completion=4e-6),), pinned=paid)
    assert _byok_row(refused, paid).reason == "paid_not_permitted"
    assert refused.reason == "pin_unresolved"
    granted = _plan(monkeypatch, (_catalog_row(paid, prompt=1e-6, completion=4e-6),), pinned=paid, allow_paid=True)
    assert _byok_row(granted, paid).allowed and granted.reason == "pinned"


def test_the_ranker_and_the_plan_read_the_same_authority(monkeypatch) -> None:
    import core.openrouter_catalog as catalog_module
    from core.model_selection_policy import charges_the_user
    from core.turn_routing import _charges_the_user

    monkeypatch.setattr(catalog_module, "safe_all_models", lambda **_kw: ((_catalog_row(PINNED),), 0.0))
    manifest = _byok(PINNED)
    assert charges_the_user(manifest, cost_class="paid_cloud") is False
    assert _charges_the_user(manifest, "paid_cloud") is False
    monkeypatch.setattr(catalog_module, "safe_all_models", lambda **_kw: ((), None))
    assert charges_the_user(manifest, cost_class="paid_cloud") is True
    assert _charges_the_user(manifest, "paid_cloud") is True


# --- broker: a gateway rejection does not spend the disclosure budget --------------------------


def _model(model_id: str, *, provider_id: str = "gateway") -> CloudModelMetadata:
    return CloudModelMetadata(
        provider_id=provider_id,
        model_id=model_id,
        display_name=model_id,
        pricing_state=PricingState.FREE,
        input_usd_per_token=0.0,
        output_usd_per_token=0.0,
        request_usd=0.0,
        context_window=8192,
        capabilities=("text",),
        discovered_at="2026-09-08T00:00:00+00:00",
        expires_at="2099-01-01T00:00:00+00:00",
        health_state="healthy",
        quota_remaining=10,
    )


class _Provider:
    def __init__(self, models, responses, *, provider_id="gateway"):
        self.provider_id = provider_id
        self.models = tuple(models)
        self.responses = list(responses)
        self.sent_model_ids: list[str] = []
        self.send_count = 0

    def discover_models(self, _transport):
        return self.models

    def send_request(self, _transport, request):
        self.send_count += 1
        self.sent_model_ids.append(request.model_id)
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
        return CloudAccountLimits()

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
    messages=({"role": "user", "content": "how much do four winter tyres cost for a 2011 estate?"},),
    max_output_tokens=20,
    metadata={"session_id": "held-out-broker-session"},
)
OK = CloudModelResponse("ok", {"cost": 0.0})


def _broker(*providers):
    return CloudModelBroker(
        registry=_Registry(list(providers)),
        transports={provider.provider_id: object() for provider in providers},
        sleeper=lambda _seconds: None,
    )


@pytest.mark.parametrize("status", [401, 403, 404, 429], ids=["key-rejected", "forbidden", "model-gone", "rate-wall"])
def test_a_gateway_rejection_before_inference_walks_to_the_next_model_on_the_same_gateway(monkeypatch, tmp_path, status) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    first, second = _model("alpha/first:free"), _model("omega/second:free")
    provider = _Provider((first, second), [CloudProviderRequestError(status, f"provider_http_{status}:"), OK])
    result = _broker(provider).execute(REQUEST, requirements=REQ, mode=CloudRouteMode.LOCAL_FREE, max_attempts=3)
    assert result.used_cloud and result.response and result.response.output_text == "ok"
    assert provider.sent_model_ids == ["alpha/first:free", "omega/second:free"]
    receipts = list_cloud_route_receipts("held-out-broker-session")
    assert [item["phase"] for item in receipts] == ["started", "completed", "started", "completed"]
    assert receipts[1]["success"] is False and receipts[3]["success"] is True
    assert receipts[2]["fallback_from"].endswith("alpha/first:free")


def test_a_failure_that_may_have_reached_a_model_still_needs_the_fanout_grant(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    provider = _Provider((_model("alpha/first:free"), _model("omega/second:free")), [CloudProviderRequestError(503, "temporary"), OK])
    result = _broker(provider).execute(REQUEST, requirements=REQ, mode=CloudRouteMode.LOCAL_FREE, max_attempts=3)
    assert not result.used_cloud
    assert provider.sent_model_ids == ["alpha/first:free"]
    assert result.fallback_reason == "cloud_attempts_exhausted"


def test_a_different_gateway_is_a_new_recipient_and_still_needs_the_grant(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("core.cloud_route_receipt._receipt_root", lambda: tmp_path)
    one = _Provider((_model("alpha/first:free", provider_id="gateway-one"),), [CloudProviderRequestError(403, "provider_http_403:")], provider_id="gateway-one")
    two = _Provider((_model("omega/second:free", provider_id="gateway-two"),), [OK], provider_id="gateway-two")
    result = _broker(one, two).execute(REQUEST, requirements=REQ, mode=CloudRouteMode.LOCAL_FREE, max_attempts=3)
    assert not result.used_cloud
    assert one.send_count == 1 and two.send_count == 0


def test_a_rejected_key_names_the_status_instead_of_a_generic_authentication_failure() -> None:
    rejected = classify_error(CloudProviderRequestError(401, "provider_http_401:"))
    refused = classify_error(CloudProviderRequestError(403, "provider_http_403:"))
    assert rejected.kind is ProviderErrorKind.AUTH and "401" in rejected.safe_message and "key" in rejected.safe_message
    assert refused.kind is ProviderErrorKind.AUTH and "403" in refused.safe_message and "this model" in refused.safe_message
    assert "authentication failed" not in rejected.safe_message + refused.safe_message
