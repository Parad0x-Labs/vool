"""A free-cloud answer that actually served is the turn's author at publication.

Before: the free-cloud boost metered its answer but never put it on the turn's ledger, so when the
local model had been refused before its call, that refusal stayed the turn's only authorship
record and the publication gate withheld the cloud answer that did serve. Now the boost records
its served usage like every other lane, with the broker's own dispatch identity for the author
check; a failed call, a supporting role or an uncertified route still does not publish.

Drives the real ``MemoryFirstRouter._try_free_cloud_boost`` against a stub OpenRouter transport
(no network) and reads the real publication gate.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.final_answer_authorship import (
    REASON_BLOCKED_BEFORE_GENERATION,
    AuthorshipDecision,
    gate_authored_content,
    record_authorship_decision,
)
from tests.test_usage_presence_flags_repair import DUMMY_KEY, _AsymmetricUsageTransport, _requirements

BLOCKED_LOCAL = "ollama-local:uncertified"


class _FailingTransport(_AsymmetricUsageTransport):
    def request_json(self, *, method, url, **kwargs):
        if url.endswith("/chat/completions"):
            return 401, {}, {"error": {"message": "bad key"}}
        return super().request_json(method=method, url=url, **kwargs)


def _boost(monkeypatch, turn_id, *, transport, role="", base_url=""):
    monkeypatch.setenv("OPENROUTER_API_KEY", DUMMY_KEY)
    from adapters.base_adapter import ModelRequest
    from adapters.openrouter_cloud_provider import OpenRouterCloudProvider
    from core.cloud_broker import CloudModelBroker
    from core.cloud_privacy_policy import CloudPrivacyGrant
    from core.memory_first_router import MemoryFirstRouter
    from core.turn_model_call_ledger import begin_turn, turn_served_usage

    provider = OpenRouterCloudProvider()
    if base_url:
        monkeypatch.setattr(provider, "base_url", base_url, raising=False)
    registry = SimpleNamespace(get=lambda pid: provider if pid == "openrouter" else None, list=lambda: [provider])
    router = MemoryFirstRouter(cloud_broker=CloudModelBroker(registry=registry, transports={"openrouter": transport}))
    monkeypatch.setattr("core.memory_first_router.cloud_escalation_policy.load_policy",
                        lambda: SimpleNamespace(free_cloud_enabled=True))
    context = {
        "surface": "cli", "session_id": f"s-{turn_id}", "cancel_turn_id": turn_id, "request_id": f"req-{turn_id}",
        "turn_request": "What is the capital of France?",
        "cloud_task_requirements": _requirements(), "cloud_privacy_grant": CloudPrivacyGrant(),
    }
    if role:
        context["model_call_role"] = role
    begin_turn(context)
    # The local model was refused before its call: the turn's authorship record says so.
    record_authorship_decision(
        context,
        AuthorshipDecision(eligible=False, author_role="answer_generation", task_class="direct",
                           requested_model=BLOCKED_LOCAL, reason=REASON_BLOCKED_BEFORE_GENERATION),
        blocked_model=BLOCKED_LOCAL,
        request_text="What is the capital of France?",
    )
    decision = router._try_free_cloud_boost(
        request=ModelRequest(task_kind="chat", prompt="What is the capital of France?",
                             messages=[{"role": "user", "content": "What is the capital of France?"}],
                             output_mode="plain_text", max_output_tokens=200),
        task=SimpleNamespace(task_id=f"task-{turn_id}"), task_hash=f"hash-{turn_id}",
        output_mode="plain_text", source_context=context,
    )
    gated, payload = gate_authored_content("free lane answer", turn_id=turn_id)
    return decision, gated, payload, turn_served_usage(context)


def test_a_served_free_cloud_answer_publishes_over_a_precall_local_block(monkeypatch):
    transport = _AsymmetricUsageTransport({"prompt_tokens": 9, "completion_tokens": 3})
    decision, gated, payload, served = _boost(monkeypatch, "boost-ok", transport=transport)
    assert decision is not None and decision.output_text == "free lane answer"
    assert gated == "free lane answer" and payload.get("publication") == "published"
    assert payload["selected_model"] == "openrouter:vendor/free-chat:free"
    assert served["provider_id"] == "openrouter:vendor/free-chat:free" and served["output_tokens"] == 3


@pytest.mark.parametrize("case", ["failed_call", "supporting_role", "loopback_route"])
def test_nothing_that_did_not_serve_as_a_certified_author_publishes(monkeypatch, case):
    transport = (_FailingTransport if case == "failed_call" else _AsymmetricUsageTransport)(
        {"prompt_tokens": 9, "completion_tokens": 3})
    decision, gated, payload, served = _boost(
        monkeypatch, f"boost-{case}", transport=transport,
        role="role_classification" if case == "supporting_role" else "",
        base_url="http://127.0.0.1:1/api/v1" if case == "loopback_route" else "",
    )
    if case == "failed_call":
        assert decision is None and served is None  # no bytes, nothing recorded as served
    assert gated != "free lane answer" and payload.get("publication") == "refused"


def test_the_broker_names_only_routes_it_can_dispatch():
    from core.cloud_broker import CloudModelBroker

    provider = SimpleNamespace(provider_id="openrouter", base_url="https://openrouter.ai/api/v1")
    registry = SimpleNamespace(get=lambda pid: provider if pid == "openrouter" else None, list=lambda: [provider])
    assert CloudModelBroker(registry=registry, transports={}).author_manifest("openrouter", "m") is None
    broker = CloudModelBroker(registry=registry, transports={"openrouter": object()})
    assert broker.author_manifest("elsewhere", "m") is None and broker.author_manifest("openrouter", "") is None
    manifest = broker.author_manifest("openrouter", "m")
    assert manifest.provider_id == "openrouter:m" and manifest.runtime_config["base_url"] == provider.base_url
