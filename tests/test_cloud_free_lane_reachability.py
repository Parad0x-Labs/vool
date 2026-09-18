"""The FREE OpenRouter lane, end to end, against the live catalog SHAPE — no live call.

Two defects are locked down here:

* The per-request-fee defect fixed for the chat catalog in d9381a6 also lived in the broker path.
  OpenRouter publishes ``pricing.request`` for NO model (measured 0 of 338), and both
  ``pricing_state_for_prices`` and ``verified_zero_price`` demanded it, so every model normalized
  to UNKNOWN and the free router rejected the entire catalog with ``pricing_unknown``. The free
  lane could not select anything.
* ``cloud model auto`` stood in the paid ``_DEFAULT_OPENROUTER_MODEL`` when no free pick was
  available, at both the command surface and boot-time provider registration.

The transport here is a stub that answers from a recorded payload; nothing leaves the process and
the key is a dummy.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.cloud_provider_contract import (
    CloudModelMetadata,
    CloudTaskRequirements,
    PricingState,
    PrivacyClass,
    pricing_state_for_prices,
)
from core.cloud_routing import CloudRouteMode, select_cloud_route

DUMMY_KEY = "sk-or-v1-DUMMY-NOT-A-REAL-KEY"

# The live payload shape: prompt/completion published for every model, `request` for none.
LIVE_SHAPE_PAYLOAD = {
    "data": [
        {
            "id": "vendor/free-chat:free",
            "name": "Free Chat",
            "context_length": 262144,
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "supported_parameters": ["tools"],
        },
        {
            "id": "novel/zephyr-audit:free",
            "name": "Zephyr Audit Free",
            "context_length": 131072,
            "pricing": {"prompt": "0", "completion": "0"},
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "supported_parameters": ["tools"],
        },
        {
            "id": "vendor/priced-chat",
            "name": "Priced Chat",
            "context_length": 262144,
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "supported_parameters": ["tools"],
        },
        {
            "id": "vendor/no-pricing-block",
            "name": "Unpublished",
            "context_length": 262144,
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        },
    ]
}


def _requirements() -> CloudTaskRequirements:
    return CloudTaskRequirements(
        min_context_tokens=1000,
        expected_output_tokens=200,
        required_capabilities=("text",),
        privacy_class=PrivacyClass.PUBLIC,
    )


class _StubTransport:
    """Answers OpenRouter's endpoints from the recorded payload. Makes no network call."""

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.bodies: list[dict] = []

    def request_json(self, *, method, url, headers=None, body=None, credential_name="",
                     credential_env="", credential_scheme="bearer", timeout_seconds=30.0):
        self.urls.append(f"{method} {url}")
        if url.endswith("/models"):
            return 200, {}, LIVE_SHAPE_PAYLOAD
        if url.endswith("/auth/key"):
            return 200, {}, {"data": {"limit_remaining": None, "is_free_tier": True}}
        if url.endswith("/chat/completions"):
            self.bodies.append(dict(body or {}))
            if body and body.get("tools"):
                sandbox = next(
                    tool
                    for tool in body["tools"]
                    if tool["function"]["name"] == "sandbox__run_command"
                )
                return 200, {}, {
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "native-e2e-1",
                                        "type": "function",
                                        "function": {
                                            "name": sandbox["function"]["name"],
                                            "arguments": '{"command":"pwd","cwd":null}',
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0},
                }
            return 200, {}, {
                "choices": [{"message": {"content": "free lane answer"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0},
            }
        raise AssertionError(f"unexpected url {url}")


def _metadata(**kw) -> CloudModelMetadata:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    defaults = dict(
        provider_id="openrouter",
        model_id="vendor/model",
        display_name="Model",
        context_window=262144,
        capabilities=("text",),
        discovered_at=now.isoformat(),
        expires_at=(now + timedelta(hours=1)).isoformat(),
        health_state="ready",
    )
    defaults.update(kw)
    return CloudModelMetadata(**defaults)


# --- pricing normalization -------------------------------------------------------------------


def test_a_missing_per_request_fee_does_not_class_every_model_unknown() -> None:
    state = pricing_state_for_prices(0.0, 0.0, None)
    assert state == PricingState.FREE
    assert _metadata(pricing_state=state, input_usd_per_token=0.0, output_usd_per_token=0.0).verified_zero_price


def test_an_unpublished_per_token_rate_is_still_unknown_not_free() -> None:
    """The per-token pair is the load-bearing evidence and must stay required."""
    assert pricing_state_for_prices(None, 0.0, 0.0) == PricingState.UNKNOWN
    assert pricing_state_for_prices(0.0, None, 0.0) == PricingState.UNKNOWN
    assert not _metadata(pricing_state=PricingState.FREE, input_usd_per_token=None,
                         output_usd_per_token=0.0).verified_zero_price


def test_a_published_per_request_fee_still_makes_a_model_paid() -> None:
    assert pricing_state_for_prices(0.0, 0.0, 0.01) == PricingState.PAID
    assert not _metadata(pricing_state=PricingState.FREE, input_usd_per_token=0.0,
                         output_usd_per_token=0.0, request_usd=0.01).verified_zero_price


def test_a_priced_model_is_paid_not_free() -> None:
    assert pricing_state_for_prices(0.000001, 0.000002, None) == PricingState.PAID


# --- routing ---------------------------------------------------------------------------------


def _route(models, *, mode, paid_approved=False, budget=0.0):
    return select_cloud_route(
        models,
        requirements=_requirements(),
        mode=mode,
        enabled_providers=("openrouter",),
        network_allowed_providers=("openrouter",),
        privacy_allowed=True,
        paid_approved=paid_approved,
        paid_budget_remaining_usd=budget,
    )


def test_a_verified_free_model_is_selectable_on_the_free_route() -> None:
    free = _metadata(
        model_id="vendor/free-chat:free",
        pricing_state=PricingState.FREE,
        input_usd_per_token=0.0,
        output_usd_per_token=0.0,
    )
    plan = _route((free,), mode=CloudRouteMode.LOCAL_FREE)
    assert plan.primary is not None and plan.primary.model_id == "vendor/free-chat:free"
    assert plan.route_reason == "verified_zero_cost_capability_and_health_fit"


def test_a_paid_model_is_still_refused_on_the_free_route() -> None:
    paid = _metadata(
        model_id="vendor/priced-chat",
        pricing_state=PricingState.PAID,
        input_usd_per_token=0.000001,
        output_usd_per_token=0.000002,
    )
    plan = _route((paid,), mode=CloudRouteMode.LOCAL_FREE)
    assert plan.primary is None
    assert [row["reason"] for row in plan.rejected] == ["not_verified_free"]


def test_a_paid_model_without_a_published_request_fee_stays_cost_unknown_when_approved() -> None:
    """The paid brake must not move: an unpublished per-request fee keeps a paid route unpriceable.

    Reading the free side of an absent surcharge is safe because a free route that is charged
    anything aborts at settlement. A paid route has no such backstop, so it still requires a fully
    published price before any spend, even with an approval and budget in hand.
    """
    paid = _metadata(
        model_id="vendor/priced-chat",
        pricing_state=PricingState.PAID,
        input_usd_per_token=0.000001,
        output_usd_per_token=0.000002,
    )
    plan = _route((paid,), mode=CloudRouteMode.LOCAL_FREE_PAID, paid_approved=True, budget=100.0)
    assert plan.primary is None
    assert [row["reason"] for row in plan.rejected] == ["paid_cost_unknown"]


def test_unpublished_pricing_is_still_rejected_outright() -> None:
    unknown = _metadata(model_id="vendor/no-pricing-block", pricing_state=PricingState.UNKNOWN)
    plan = _route((unknown,), mode=CloudRouteMode.LOCAL_FREE)
    assert plan.primary is None
    assert [row["reason"] for row in plan.rejected] == ["pricing_unknown"]


# --- end to end: catalog -> selection -> a request bound for openrouter.ai ----------------------


def test_free_lane_reaches_the_completions_endpoint_with_a_verified_free_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", DUMMY_KEY)
    from adapters.base_adapter import ModelRequest
    from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
    from core.cloud_broker import CloudModelBroker
    from core.cloud_privacy_policy import CloudPrivacyGrant
    from core.memory_first_router import MemoryFirstRouter

    provider = OpenRouterCloudProvider()
    transport = _StubTransport()
    registry = SimpleNamespace(
        get=lambda pid: provider if pid == "openrouter" else None,
        list=lambda: [provider],
    )
    router = MemoryFirstRouter(cloud_broker=CloudModelBroker(registry=registry, transports={"openrouter": transport}))
    monkeypatch.setattr(
        "core.memory_first_router.cloud_escalation_policy.load_policy",
        lambda: SimpleNamespace(free_cloud_enabled=True),
    )
    decision = router._try_free_cloud_boost(
        request=ModelRequest(
            task_kind="reasoning",
            prompt="hello",
            messages=[{"role": "user", "content": "hello"}],
            output_mode="plain_text",
            max_output_tokens=200,
        ),
        task=SimpleNamespace(task_id="task"),
        task_hash="hash",
        output_mode="plain_text",
        source_context={
            "surface": "cli",
            "session_id": "session",
            "turn_id": "turn",
            "cloud_task_requirements": _requirements(),
            "cloud_privacy_grant": CloudPrivacyGrant(),
        },
    )
    assert decision is not None, "a verified-free model must be reachable end to end"
    assert decision.model_name == "vendor/free-chat:free", "only the FREE model may be selected"
    assert decision.output_text == "free lane answer"
    assert decision.details["pricing_state"] == PricingState.FREE.value
    assert decision.details["actual_usd"] == 0.0
    assert "POST https://openrouter.ai/api/v1/chat/completions" in transport.urls


def test_free_lane_boost_records_usage_in_the_meter(monkeypatch) -> None:
    """This lane used to build its ModelExecutionDecision without ever calling
    usage_meter.record_usage -- every free-cloud-boost response was invisible to the Activity
    panel's token/cost totals, since _record_response_usage was wired only into the OTHER
    (System A / _decision_from_response) path. Fixed by routing this lane's usage recording
    through the same normalized fields (core/normalized_provider_result.py) the decision itself
    now uses."""
    monkeypatch.setenv("OPENROUTER_API_KEY", DUMMY_KEY)
    import core.usage_meter as um
    from adapters.base_adapter import ModelRequest
    from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
    from core.cloud_broker import CloudModelBroker
    from core.cloud_privacy_policy import CloudPrivacyGrant
    from core.memory_first_router import MemoryFirstRouter
    from core.usage_meter import usage_summary

    um._SCHEMA_READY_PATHS.clear()
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute("DROP TABLE IF EXISTS token_usage")
        conn.commit()
    finally:
        conn.close()

    provider = OpenRouterCloudProvider()
    transport = _StubTransport()
    registry = SimpleNamespace(
        get=lambda pid: provider if pid == "openrouter" else None,
        list=lambda: [provider],
    )
    router = MemoryFirstRouter(cloud_broker=CloudModelBroker(registry=registry, transports={"openrouter": transport}))
    monkeypatch.setattr(
        "core.memory_first_router.cloud_escalation_policy.load_policy",
        lambda: SimpleNamespace(free_cloud_enabled=True),
    )
    decision = router._try_free_cloud_boost(
        request=ModelRequest(
            task_kind="reasoning", prompt="hello", messages=[{"role": "user", "content": "hello"}],
            output_mode="plain_text", max_output_tokens=200,
        ),
        task=SimpleNamespace(task_id="usage-task"),
        task_hash="usage-hash",
        output_mode="plain_text",
        source_context={
            "surface": "cli", "session_id": "usage-session", "turn_id": "usage-turn",
            "cloud_task_requirements": _requirements(), "cloud_privacy_grant": CloudPrivacyGrant(),
        },
    )
    assert decision is not None

    summary = usage_summary()
    assert summary["free_cloud"]["responses"] == 1
    assert summary["free_cloud"]["prompt_tokens"] == 10
    assert summary["free_cloud"]["output_tokens"] == 5
    assert summary["calls_missing_usage"] == 0


def test_auto_free_model_preference_is_strict_and_reaches_that_exact_model(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", DUMMY_KEY)
    from adapters.base_adapter import ModelRequest
    from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
    from core.cloud_broker import CloudModelBroker
    from core.cloud_privacy_policy import CloudPrivacyGrant
    from core.memory_first_router import MemoryFirstRouter

    provider = OpenRouterCloudProvider()
    transport = _StubTransport()
    registry = SimpleNamespace(
        get=lambda pid: provider if pid == "openrouter" else None,
        list=lambda: [provider],
    )
    router = MemoryFirstRouter(cloud_broker=CloudModelBroker(registry=registry, transports={"openrouter": transport}))
    monkeypatch.setattr(
        "core.memory_first_router.cloud_escalation_policy.load_policy",
        lambda: SimpleNamespace(free_cloud_enabled=True, auto_free_model="novel/zephyr-audit:free"),
    )
    decision = router._try_free_cloud_boost(
        request=ModelRequest(
            task_kind="reasoning",
            prompt="novel randomized audit request",
            messages=[{"role": "user", "content": "novel randomized audit request"}],
            output_mode="plain_text",
            max_output_tokens=200,
        ),
        task=SimpleNamespace(task_id="strict-free-task"),
        task_hash="strict-free-hash",
        output_mode="plain_text",
        source_context={
            "surface": "cli",
            "session_id": "strict-free-session",
            "turn_id": "strict-free-turn",
            "cloud_task_requirements": _requirements(),
            "cloud_privacy_grant": CloudPrivacyGrant(),
        },
    )

    assert decision is not None
    assert decision.model_name == "novel/zephyr-audit:free"
    assert transport.bodies[-1]["model"] == "novel/zephyr-audit:free"


def test_free_lane_native_tool_call_reaches_validated_internal_intent(monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", DUMMY_KEY)
    from adapters.base_adapter import ModelRequest
    from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
    from core.cloud_broker import CloudModelBroker
    from core.cloud_privacy_policy import CloudPrivacyGrant
    from core.memory_first_router import MemoryFirstRouter

    provider = OpenRouterCloudProvider()
    transport = _StubTransport()
    registry = SimpleNamespace(
        get=lambda pid: provider if pid == "openrouter" else None,
        list=lambda: [provider],
    )
    router = MemoryFirstRouter(cloud_broker=CloudModelBroker(registry=registry, transports={"openrouter": transport}))
    monkeypatch.setattr(
        "core.memory_first_router.cloud_escalation_policy.load_policy",
        lambda: SimpleNamespace(free_cloud_enabled=True),
    )
    decision = router._try_free_cloud_boost(
        request=ModelRequest(
            task_kind="tool_intent",
            prompt="show the current directory",
            messages=[{"role": "user", "content": "show the current directory"}],
            output_mode="tool_intent",
            max_output_tokens=200,
        ),
        task=SimpleNamespace(task_id="tool-task"),
        task_hash="tool-hash",
        output_mode="tool_intent",
        source_context={
            "surface": "cli",
            "session_id": "tool-session",
            "turn_id": "tool-turn",
            "cloud_task_requirements": _requirements(),
            "cloud_privacy_grant": CloudPrivacyGrant(),
        },
    )
    assert decision is not None
    assert decision.structured_output == {
        "_native_tool_call_id": "native-e2e-1",
        "arguments": {"command": "pwd", "cwd": None},
        "intent": "sandbox.run_command",
    }
    request_body = transport.bodies[-1]
    assert request_body["tool_choice"] == "required"
    assert request_body["tools"]
    names = [tool["function"]["name"] for tool in request_body["tools"]]
    assert len(names) == len(set(names))
    assert "sandbox__run_command" in names
    # This recorded model only advertises tools, not structured outputs; the broker therefore
    # uses native functions but does not ask the provider for an unsupported strict transform.
    assert all(tool["function"]["strict"] is False for tool in request_body["tools"])


class _TwoCallStubTransport:
    """Same shape as `_StubTransport`, but the completion answers with a TWO-call native batch.

    Regression guard for the exact defect the comment above `tool_calls=tuple(getattr(...))` in
    `_try_free_cloud_boost` documents: the free-cloud lane builds its `ModelExecutionDecision` by
    hand instead of through `_decision_from_response`, and once dropped `tool_calls` entirely --
    "the free-cloud lane keeps dropping call #2". `structured_output` only ever carries the FIRST
    call (the step loop executes one observed step at a time by design), so a test that only
    offers a single-call batch cannot catch a regression here -- it needs two.
    """

    def __init__(self) -> None:
        self.urls: list[str] = []
        self.bodies: list[dict] = []

    def request_json(self, *, method, url, headers=None, body=None, credential_name="",
                     credential_env="", credential_scheme="bearer", timeout_seconds=30.0):
        self.urls.append(f"{method} {url}")
        if url.endswith("/models"):
            return 200, {}, LIVE_SHAPE_PAYLOAD
        if url.endswith("/auth/key"):
            return 200, {}, {"data": {"limit_remaining": None, "is_free_tier": True}}
        if url.endswith("/chat/completions"):
            self.bodies.append(dict(body or {}))
            names = {tool["function"]["name"] for tool in (body or {}).get("tools") or []}
            assert "sandbox__run_command" in names and "web__search" in names
            return 200, {}, {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "native-batch-1",
                                    "type": "function",
                                    "function": {"name": "sandbox__run_command", "arguments": '{"command":"pwd","cwd":null}'},
                                },
                                {
                                    "id": "native-batch-2",
                                    "type": "function",
                                    "function": {"name": "web__search", "arguments": '{"query":"vool","limit":null}'},
                                },
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0},
            }
        raise AssertionError(f"unexpected url {url}")


def test_free_lane_preserves_every_call_in_a_multi_call_native_batch(monkeypatch) -> None:
    """The second (and any later) call in a batch must survive to `decision.tool_calls`.

    Only the first is executed this round (`structured_output`) -- the step loop re-asks with
    that result in hand rather than draining a queue (see `ModelExecutionDecision.tool_calls`'s
    own docstring) -- but the REST of the batch must still be visible on the decision, not
    silently dropped, so the turn can say those calls were seen and not run.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", DUMMY_KEY)
    from adapters.base_adapter import ModelRequest
    from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
    from core.cloud_broker import CloudModelBroker
    from core.cloud_privacy_policy import CloudPrivacyGrant
    from core.memory_first_router import MemoryFirstRouter

    provider = OpenRouterCloudProvider()
    transport = _TwoCallStubTransport()
    registry = SimpleNamespace(
        get=lambda pid: provider if pid == "openrouter" else None,
        list=lambda: [provider],
    )
    router = MemoryFirstRouter(cloud_broker=CloudModelBroker(registry=registry, transports={"openrouter": transport}))
    monkeypatch.setattr(
        "core.memory_first_router.cloud_escalation_policy.load_policy",
        lambda: SimpleNamespace(free_cloud_enabled=True),
    )
    decision = router._try_free_cloud_boost(
        request=ModelRequest(
            task_kind="tool_intent",
            prompt="run pwd and search for vool",
            messages=[{"role": "user", "content": "run pwd and search for vool"}],
            output_mode="tool_intent",
            max_output_tokens=200,
        ),
        task=SimpleNamespace(task_id="batch-task"),
        task_hash="batch-hash",
        output_mode="tool_intent",
        source_context={
            "surface": "cli",
            "session_id": "batch-session",
            "turn_id": "batch-turn",
            "cloud_task_requirements": _requirements(),
            "cloud_privacy_grant": CloudPrivacyGrant(),
        },
    )
    assert decision is not None
    assert decision.structured_output["intent"] == "sandbox.run_command"  # only call #1 executes this round
    assert len(decision.tool_calls) == 2, "call #2 must not be silently dropped from the decision"
    assert {call.intent for call in decision.tool_calls} == {"sandbox.run_command", "web.search"}


# --- `cloud model auto` must never stand in a paid model ---------------------------------------


@pytest.fixture
def no_free_catalog(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    import core.openrouter_catalog as cat

    monkeypatch.setattr(cat, "_cache_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(cat, "refresh_openrouter_catalog", lambda **kw: (_ for _ in ()).throw(RuntimeError("offline")))
    return tmp_path


def test_cloud_model_auto_refuses_rather_than_standing_in_the_paid_default(no_free_catalog) -> None:
    from core.cloud_escalation_policy import load_policy
    from core.cloud_model_control import set_cloud_model
    from core.runtime_provider_defaults import _DEFAULT_OPENROUTER_MODEL

    ok, message, chosen = set_cloud_model("auto", provider="openrouter", owner_local=True)
    assert ok is False, "auto with no verified-free candidate must refuse, not silently pick a paid model"
    assert chosen == ""
    assert _DEFAULT_OPENROUTER_MODEL not in message
    assert "free" in message.lower() and "paid" in message.lower(), "the refusal must say why"
    assert load_policy().model == "", "a refused switch must leave the persisted choice untouched"


def test_boot_registration_under_auto_registers_no_paid_lane(no_free_catalog, monkeypatch) -> None:
    """Boot reads the catalog cache-only, so a cold cache used to register the paid default."""
    from dataclasses import replace

    from core import cloud_escalation_policy as cep
    from core.model_registry import ModelRegistry
    from core.runtime_provider_defaults import _DEFAULT_OPENROUTER_MODEL, _ensure_openrouter_byok_provider

    monkeypatch.setenv("OPENROUTER_API_KEY", DUMMY_KEY)
    cep.save_policy(replace(cep.load_policy(), model="auto"))
    provider_id = _ensure_openrouter_byok_provider(ModelRegistry(), env={"OPENROUTER_API_KEY": DUMMY_KEY})
    assert provider_id == "", "no free pick means no lane, not the paid default"

    from storage.model_provider_manifest import list_provider_manifests

    live = [m.model_name for m in list_provider_manifests(enabled_only=True) if m.provider_name == "openrouter-byok"]
    assert _DEFAULT_OPENROUTER_MODEL not in live


def test_openrouter_byok_manifest_declares_tool_intent_capability(monkeypatch) -> None:
    """Finding, 2026-08-04: a capable instruction-following OpenRouter model can genuinely
    select a tool from a schema catalogue, exactly like every local Ollama manifest already
    declares (see `manifest_profile["capabilities"]` in `_ensure_local_ollama_provider`).
    Omitting `tool_intent` here is what made `capability_score()` rank a free, capable cloud
    model 1.2 points below qwen3:8b for every tool_intent/output_mode=="tool_intent" turn,
    regardless of how good the underlying model actually is at the task -- structurally
    blocking the free-cloud ranking boost from ever mattering for tool_intent routing."""
    from core.model_registry import ModelRegistry
    from core.runtime_provider_defaults import _ensure_openrouter_byok_provider
    from storage.model_provider_manifest import list_provider_manifests

    monkeypatch.setenv("OPENROUTER_API_KEY", DUMMY_KEY)
    monkeypatch.setenv("OPENROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
    provider_id = _ensure_openrouter_byok_provider(
        ModelRegistry(),
        env={"OPENROUTER_API_KEY": DUMMY_KEY, "OPENROUTER_MODEL": "nvidia/nemotron-3-ultra-550b-a55b:free"},
    )
    assert provider_id, "the explicit-model lane must register"

    manifests = [m for m in list_provider_manifests(enabled_only=True) if m.provider_name == "openrouter-byok"]
    assert manifests, "the openrouter-byok manifest must be registered"
    assert "tool_intent" in manifests[0].capabilities, (
        "the OpenRouter BYOK manifest must declare tool_intent, or capability_score() ranks it "
        "below every local Ollama manifest for tool_intent routing regardless of model quality"
    )
