"""One provider-aware verification policy for Save, Test and refresh — the 2026-09-15 completion
of the setup repair's open contract gaps.

THE LAWS UNDER TEST
-------------------
* **The Anthropic Models API key rides ``X-Api-Key``, not Bearer.** Anthropic's ordinary
  ``GET /v1/models`` documents ``X-Api-Key`` + ``anthropic-version`` and no Bearer option
  (platform.claude.com, read 2026-09-15); the OpenAI-compatibility CHAT endpoint accepting
  Bearer does not change the ordinary API's contract. The registry descriptor — which Save,
  Test and the refresh all share — must place the key where that API documents it, or every
  real ``sk-ant-`` key is answered 401 and reported "rejected" (the reported failure shape).
* **The stored-key Test probe runs the SAME policy as Save.** The probe builds no request of
  its own anymore: it resolves the provider registry descriptor and calls
  ``verify_provider_credential`` under its own effect scope. So the placement, the unredirected
  key header, the documented-shape body check and the keyless-first rule for an
  operator-entered endpoint hold for Test exactly as for Save.
* **A public custom endpoint cannot make Test certify an arbitrary key.** An endpoint that
  answers the keyless ask in the documented shape is ``public_endpoint`` — a refusal detail,
  never a green connection — and the key is never sent.
"""
from __future__ import annotations

import hashlib

import pytest

from tests._credential_intelligence_support import FakeProviderServer

ANTHROPIC_KEY = "sk-ant-api03-" + "b" * 40
ANTHROPIC_MODELS_BODY = {
    "data": [
        {
            "type": "model",
            "id": "claude-harness-4",
            "display_name": "Claude Harness 4",
            "created_at": "2026-01-01T00:00:00Z",
            "max_input_tokens": 200000,
            "max_tokens": 32000,
        }
    ],
    "has_more": False,
    "first_id": "claude-harness-4",
    "last_id": "claude-harness-4",
}


def _saw_header(server: FakeProviderServer, name: str, value: str) -> bool:
    want = hashlib.sha256(value.encode()).hexdigest()
    with server._lock:
        return any((r.get("header_sha256") or {}).get(name) == want for r in server.requests)


def _no_bearer_was_sent(server: FakeProviderServer) -> bool:
    with server._lock:
        return all(not r["auth_present"] for r in server.requests)


def _anthropic_descriptor_at(url: str):
    from core.credential_intelligence.provider_registry import default_registry

    descriptor = default_registry().get("anthropic")
    assert descriptor is not None
    return descriptor.__class__(**{**descriptor.__dict__, "verify_endpoint": f"{url}/v1/models"})


# ------------------------------------------------------------------ registry placement

def test_anthropic_descriptor_places_the_key_in_x_api_key():
    from core.credential_intelligence.provider_registry import default_registry

    descriptor = default_registry().get("anthropic")
    assert descriptor is not None
    assert descriptor.auth_style == "header"
    assert descriptor.auth_name == "X-Api-Key"
    assert descriptor.extra_headers.get("anthropic-version") == "2023-06-01"


def test_bearer_providers_keep_bearer_placement():
    from core.credential_intelligence.provider_registry import default_registry

    registry = default_registry()
    for pid in ("openrouter", "openai", "groq", "custom"):
        assert registry.get(pid).auth_style == "bearer", pid


# ------------------------------------------------------------------ Save verification (original failure)

def test_anthropic_key_verifies_through_x_api_key_and_never_bearer():
    """The reported failure shape: a real sk-ant- key against the documented Models API. A
    service that demands X-Api-Key (as Anthropic documents) must see the key there and NOT as
    Bearer, and the outcome must be verified."""
    with FakeProviderServer() as server:
        server.responses = [(200, ANTHROPIC_MODELS_BODY)]
        from core.credential_intelligence.verification import verify_provider_credential

        outcome = verify_provider_credential(
            ANTHROPIC_KEY, _anthropic_descriptor_at(server.url), timeout_s=5.0
        )
        assert outcome.status == "verified", outcome.to_dict()
        assert _saw_header(server, "x-api-key", ANTHROPIC_KEY)
        assert _no_bearer_was_sent(server), "Bearer was sent to the Anthropic Models API"


def test_anthropic_bearer_only_service_rejects_without_key_judgement():
    """Novel case, same class, different data: a service that answers the documented shape ONLY
    for Bearer (a compatibility gateway). The unified policy does not bend: it sends X-Api-Key
    as documented, the 401 is `invalid`, and no Bearer fallback is tried anywhere."""
    with FakeProviderServer() as server:
        server.responses = [
            (401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}),
        ]
        from core.credential_intelligence.verification import verify_provider_credential

        outcome = verify_provider_credential(
            ANTHROPIC_KEY, _anthropic_descriptor_at(server.url), timeout_s=5.0
        )
        assert outcome.status == "invalid"
        assert server.request_count == 1, "a second, differently-authed attempt was made"


# ------------------------------------------------------------------ the Test probe uses the same policy

@pytest.fixture
def probe_env(monkeypatch, tmp_path):
    """Isolated home + the probe's state file pinned to the test dir."""
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    import core.runtime_paths as runtime_paths

    runtime_paths.configure_runtime_home(tmp_path / "home")
    from core import cloud_connection_state as ccs

    monkeypatch.setattr(ccs, "_state_path", lambda: tmp_path / "state.json")
    ccs.reset_probe_rate_limit_for_tests()
    yield monkeypatch
    runtime_paths.configure_runtime_home(None)


def _repoint_registry(monkeypatch, provider_id: str, url: str):
    """Pin one registry provider's verify endpoint at a loopback stand-in, keeping every other
    descriptor exactly as the production registry builds it."""
    import core.credential_intelligence.provider_registry as pr

    real = pr.default_registry

    def replacement():
        registry = real()
        table = {d.provider_id: d for d in registry.providers()}
        descriptor = table[provider_id]
        table[provider_id] = descriptor.__class__(
            **{**descriptor.__dict__, "verify_endpoint": f"{url.rstrip('/')}{_verify_path(descriptor)}"}
        )
        return pr.ProviderRegistry(table)

    def _verify_path(descriptor) -> str:
        from urllib.parse import urlsplit

        return urlsplit(descriptor.verify_endpoint).path or "/v1/models"

    monkeypatch.setattr(pr, "default_registry", replacement)


def _stored_key(monkeypatch, slot: str, value: str):
    monkeypatch.setattr(
        "core.credential_store.get_credential", lambda name: value if name == slot else None
    )


def test_probe_sends_x_api_key_to_the_anthropic_models_api(probe_env):
    """The Test button, through the one policy, places an Anthropic key exactly as Save does."""
    with FakeProviderServer() as server:
        server.responses = [(200, ANTHROPIC_MODELS_BODY)]
        _repoint_registry(probe_env, "anthropic", server.url)
        _stored_key(probe_env, "llm.cloud.anthropic", ANTHROPIC_KEY)
        from core import cloud_connection_state as ccs

        result = ccs.run_auth_probe(provider="anthropic", now=2000.0)
        assert result["state"] == ccs.STATE_OK, result
        assert result["http_status"] == 200
        assert _saw_header(server, "x-api-key", ANTHROPIC_KEY)
        assert _no_bearer_was_sent(server)


def test_probe_reports_a_rejected_anthropic_key_as_unauthorized(probe_env):
    """Negative control: the documented API refusing the key is a judgement about the key; the
    probe must say 'unauthorized', not 'could not reach'."""
    with FakeProviderServer() as server:
        server.responses = [
            (401, {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}),
        ]
        _repoint_registry(probe_env, "anthropic", server.url)
        _stored_key(probe_env, "llm.cloud.anthropic", ANTHROPIC_KEY)
        from core import cloud_connection_state as ccs

        result = ccs.run_auth_probe(provider="anthropic", now=2000.0)
        assert result["state"] == ccs.STATE_FAILED
        assert result["detail"] == "unauthorized"


def test_probe_distinguishes_throttling_from_a_rejected_key(probe_env):
    """A 429 is not a verdict about the key: failed, but never 'unauthorized'."""
    with FakeProviderServer() as server:
        server.responses = [
            (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}),
        ]
        _repoint_registry(probe_env, "anthropic", server.url)
        _stored_key(probe_env, "llm.cloud.anthropic", ANTHROPIC_KEY)
        from core import cloud_connection_state as ccs

        result = ccs.run_auth_probe(provider="anthropic", now=2000.0)
        assert result["state"] == ccs.STATE_FAILED
        assert result["detail"] == "rate_limited"


def test_probe_separates_account_exhaustion_from_key_validity(probe_env):
    """402 from the provider's own error body: the key is valid, the account is out of credit —
    failed with its own name, not 'unauthorized'."""
    with FakeProviderServer() as server:
        server.responses = [
            (402, {"type": "error", "error": {"type": "billing_error", "message": "no credit"}}),
        ]
        _repoint_registry(probe_env, "anthropic", server.url)
        _stored_key(probe_env, "llm.cloud.anthropic", ANTHROPIC_KEY)
        from core import cloud_connection_state as ccs

        result = ccs.run_auth_probe(provider="anthropic", now=2000.0)
        assert result["state"] == ccs.STATE_FAILED
        assert result["detail"] == "exhausted"


def test_public_custom_endpoint_cannot_green_the_probe(probe_env, tmp_path):
    """The unified policy's keyless-first rule reaches the Test button: a custom endpoint whose
    /models answers without any key cannot confirm one, so the probe refuses — and never sends
    the stored key there."""
    with FakeProviderServer(
        responses=lambda record: (200, {"object": "list", "data": [{"id": "pub/model", "object": "model"}]})
    ) as server:
        probe_env.setenv("VOOL_CUSTOM_API_KEY", "nv1_arbitrary-unverified-key-000111")
        probe_env.setattr(
            "core.cloud_providers.custom_base_url", lambda: f"{server.url}/v1"
        )
        from core import cloud_connection_state as ccs

        result = ccs.run_auth_probe(provider="custom", now=2000.0)
        assert result["state"] == ccs.STATE_FAILED, result
        assert result["detail"] == "public_endpoint"
        assert _no_bearer_was_sent(server), "the stored key was sent to a public endpoint"
