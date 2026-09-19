"""Provider capability discovery — provenance, freshness, invalidation, both documented shapes.

THE LAWS UNDER TEST
-------------------
* An assertion carries its evidence class (observed), source, observation time, expiry and an
  endpoint FINGERPRINT that carries no path or query — and it survives a restart (plain file).
* Refresh verifies the key first: a public model catalogue never validates a key, and an
  inconclusive verification (throttle, outage) keeps the previous catalogue with the refusal's
  own name instead of erasing it.
* A model that disappeared from a fresh listing is retained as ``missing``, never deleted.
* A key or base-URL change invalidates capability evidence (rows degrade to ``unknown``); a
  refresh that raced a settings change is discarded, never written over the newer settings.
* The OpenAI models-list shape and the Anthropic models-list shape both parse; unpublished
  fields stay unknown; a body that is not a models list is named as itself.
* No key material is ever persisted: digests and origins only.
"""
from __future__ import annotations

import json

import pytest

from tests._credential_intelligence_support import (
    FakeProviderServer,
    isolated_home,
)

PROVIDER_KEY = "sk-or-v1-" + "d" * 56
OTHER_KEY = "sk-or-v1-" + "e" * 56

OPENAI_LIST = {
    "object": "list",
    "data": [
        {
            "id": "lab/chat-large",
            "name": "Lab Chat Large",
            "context_length": 131072,
            "top_provider": {"max_completion_tokens": 16384},
            "supported_parameters": ["tools", "max_tokens"],
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        },
        {
            "id": "lab/chat-small",
            "name": "Lab Chat Small",
            "context_length": 32768,
        },
    ],
}

ANTHROPIC_LIST = {
    "data": [
        {
            "type": "model",
            "id": "claude-harness-4",
            "display_name": "Claude Harness 4",
            "max_input_tokens": 200000,
            "max_tokens": 32000,
            "capabilities": {"thinking": {"supported": True}, "vision": {"supported": False}},
        }
    ],
    "has_more": False,
}


@pytest.fixture
def home(isolated_home):
    return isolated_home


def _descriptor(server: FakeProviderServer):
    """An openrouter-shaped descriptor whose /key verifies and whose /models lists."""
    from core.credential_intelligence.provider_registry import default_registry

    base = default_registry().get("openrouter")
    return base.__class__(**{**base.__dict__, "verify_endpoint": f"{server.url}/api/v1/key"})


def _registry(monkeypatch, descriptor):
    import core.credential_intelligence.provider_registry as pr

    table = {d.provider_id: d for d in pr.default_registry().providers()}
    table[descriptor.provider_id] = descriptor
    registry = pr.ProviderRegistry(table)
    monkeypatch.setattr(pr, "default_registry", lambda: registry)
    return registry


def _by_path(server: FakeProviderServer, key_body=None, models_body=None, models_status=200):
    key_body = key_body or {"data": {"label": "harness", "limit_remaining": None}}

    def respond(record):
        if record["path"].endswith("/key"):
            authed = record["auth_present"]
            if not authed:
                return (401, {"error": {"code": 401, "message": "Missing Authentication header"}})
            return (200, key_body)
        if record["path"].endswith("/models"):
            if not record["auth_present"]:
                return (401, {"error": {"code": 401, "message": "Missing Authentication header"}})
            return (models_status, models_body if models_body is not None else OPENAI_LIST)
        return (404, {"error": {"code": 404, "message": "Not Found"}})

    server.responses = respond


def _refresh(monkeypatch, server, provider_id="openrouter", **kw):
    from core.credential_intelligence.discovery import refresh_discovery

    return refresh_discovery(provider_id, **kw)


def _stored_key(monkeypatch, value=PROVIDER_KEY):
    monkeypatch.setattr(
        "core.credential_store.get_credential",
        lambda name: value if name == "llm.cloud.openrouter" else None,
    )


# ------------------------------------------------------------------ the observed assertion

def test_refresh_records_observed_assertion_with_provenance_and_fingerprint(home, monkeypatch):
    with FakeProviderServer() as server:
        _by_path(server)
        _stored_key(monkeypatch)
        _registry(monkeypatch, _descriptor(server))
        assertion = _refresh(monkeypatch, server)
        assert assertion.evidence == "observed"
        assert assertion.source == "model_list_endpoint"
        assert assertion.protocol == "openai_models_list"
        assert assertion.last_refresh_status == "verified"
        # fingerprint is an origin only — no path, no query, and the file holds no key material
        assert assertion.endpoint_fingerprint == server.url
        assert assertion.fresh is True
        ids = {m.model_id for m in assertion.models}
        assert ids == {"lab/chat-large", "lab/chat-small"}
        large = next(m for m in assertion.models if m.model_id == "lab/chat-large")
        assert large.display_name == "Lab Chat Large"
        assert large.context_window == 131072
        assert large.max_output_tokens == 16384
        assert large.capabilities == ("tools", "max_tokens")
        small = next(m for m in assertion.models if m.model_id == "lab/chat-small")
        assert small.max_output_tokens == 0, "unpublished output cap stays unknown, never invented"
        raw = json.loads((home / "data" / "discovery_assertions.json").read_text())
        assert PROVIDER_KEY not in json.dumps(raw)
        assert PROVIDER_KEY[-8:] not in json.dumps(raw)


def test_assertion_survives_a_restart(home, monkeypatch):
    with FakeProviderServer() as server:
        _by_path(server)
        _stored_key(monkeypatch)
        _registry(monkeypatch, _descriptor(server))
        _refresh(monkeypatch, server)
    from core.credential_intelligence.discovery import discovery_snapshot, load_assertions

    assert "openrouter" in load_assertions()          # a fresh process reads the same file
    snapshot = discovery_snapshot(["openrouter"])
    assert snapshot["openrouter"]["fresh"] is True
    assert {m["model_id"] for m in snapshot["openrouter"]["models"]} == {"lab/chat-large", "lab/chat-small"}


# ------------------------------------------------------------------ verification gates discovery

def test_public_catalogue_never_validates_a_key(home, monkeypatch):
    """/models answers 200 for ANY key (a public catalogue) while /key rejects this one: the
    refresh must record the refusal, not adopt the public list as capability evidence."""
    with FakeProviderServer() as server:
        def respond(record):
            if record["path"].endswith("/key"):
                return (401, {"error": {"code": 401, "message": "Missing Authentication header"}})
            return (200, OPENAI_LIST)

        server.responses = respond
        _stored_key(monkeypatch)
        _registry(monkeypatch, _descriptor(server))
        assertion = _refresh(monkeypatch, server)
        assert assertion.last_refresh_status == "invalid"
        assert assertion.models == (), "a public listing became evidence without a verified key"
        assert assertion.evidence == "unknown"


def test_outage_during_refresh_keeps_the_catalogue(home, monkeypatch):
    with FakeProviderServer() as server:
        _by_path(server)
        _stored_key(monkeypatch)
        _registry(monkeypatch, _descriptor(server))
        first = _refresh(monkeypatch, server)
        assert first.last_refresh_status == "verified"
        _by_path(server, models_status=503, models_body={"error": {"code": 503, "message": "outage"}})
        second = _refresh(monkeypatch, server)
        assert second.last_refresh_status == "provider_unavailable"
        assert {m.model_id for m in second.models} == {m.model_id for m in first.models}, "an outage erased the catalogue"
        assert all(m.evidence == "observed" for m in second.models)


def test_no_key_means_no_network(home, monkeypatch):
    with FakeProviderServer() as server:
        _by_path(server)
        monkeypatch.setattr("core.credential_store.get_credential", lambda name: None)
        _registry(monkeypatch, _descriptor(server))
        assertion = _refresh(monkeypatch, server)
        assert assertion.last_refresh_status == "no_key"
        assert server.request_count == 0, "a keyless discovery refresh sent a request"


# ------------------------------------------------------------------ disappeared models

def test_disappeared_model_is_kept_as_missing(home, monkeypatch):
    with FakeProviderServer() as server:
        _by_path(server)
        _stored_key(monkeypatch)
        _registry(monkeypatch, _descriptor(server))
        _refresh(monkeypatch, server)
        shrunk = {**OPENAI_LIST, "data": OPENAI_LIST["data"][:1]}   # chat-small disappears
        _by_path(server, models_body=shrunk)
        assertion = _refresh(monkeypatch, server)
        rows = {m.model_id: m for m in assertion.models}
        assert rows["lab/chat-large"].status == "available"
        assert rows["lab/chat-small"].status == "missing", "a disappeared model was silently deleted"


# ------------------------------------------------------------------ generation invalidation

def test_key_change_degrades_capabilities_to_unknown(home, monkeypatch):
    with FakeProviderServer() as server:
        _by_path(server)
        _stored_key(monkeypatch)
        _registry(monkeypatch, _descriptor(server))
        _refresh(monkeypatch, server)
        _stored_key(monkeypatch, OTHER_KEY)
        from core.credential_intelligence.discovery import load_assertions

        assertion = load_assertions()["openrouter"]
        assert assertion.refreshed_generations_match is True  # no refresh ran yet; the file still holds old evidence
        from core.credential_intelligence.discovery import _generations, invalidate_stale_capabilities

        descriptor = _descriptor(server)
        key_gen, endpoint_gen = _generations("openrouter", descriptor, OTHER_KEY)
        degraded = invalidate_stale_capabilities("openrouter", key_generation=key_gen, endpoint_generation=endpoint_gen)
        assert degraded.evidence == "unknown"
        assert all(m.evidence == "unknown" for m in degraded.models), "capabilities were silently retained across a key change"


def test_refresh_after_key_change_observates_under_the_new_generation(home, monkeypatch):
    with FakeProviderServer() as server:
        _by_path(server)
        _stored_key(monkeypatch)
        _registry(monkeypatch, _descriptor(server))
        _refresh(monkeypatch, server)
        _stored_key(monkeypatch, OTHER_KEY)
        _by_path(server, models_body=OPENAI_LIST)
        assertion = _refresh(monkeypatch, server)
        assert assertion.evidence == "observed"
        assert assertion.protocol == "openai_models_list"
        assert assertion.last_refresh_status == "verified"


def test_refresh_that_races_a_settings_change_is_discarded(home, monkeypatch):
    """A first refresh observes under key A. A second refresh starts, and the key changes to B
    while its requests are in flight: that observation belongs to a combination nobody
    configured anymore and must NOT overwrite the settled evidence (nor the newer settings)."""
    with FakeProviderServer() as server:
        _by_path(server, models_body=OPENAI_LIST)
        _stored_key(monkeypatch)
        _registry(monkeypatch, _descriptor(server))
        first = _refresh(monkeypatch, server)
        assert first.last_refresh_status == "verified"

        import core.credential_intelligence.discovery as disc
        import core.credential_intelligence.verification as verification

        real = verification.verify_provider_credential
        flipped = {"done": False}

        def verify_and_flip(secret, descriptor, **kw):
            outcome = real(secret, descriptor, **kw)
            if not flipped["done"]:
                flipped["done"] = True
                _stored_key(monkeypatch, OTHER_KEY)     # the settings change lands mid-flight
            return outcome

        # refresh_discovery resolves the verifier at call time, so the verification-module
        # attribute is the seam.
        monkeypatch.setattr(verification, "verify_provider_credential", verify_and_flip)
        second = disc.refresh_discovery("openrouter")
        assert second is not None, "refresh crashed instead of discarding the raced observation"
        assert second.key_generation == disc._digest(PROVIDER_KEY), "a raced observation overwrote the settled assertion"


def test_base_url_change_invalidates_evidence(home, monkeypatch):
    with FakeProviderServer() as server:
        _by_path(server)
        _stored_key(monkeypatch)
        _registry(monkeypatch, _descriptor(server))
        _refresh(monkeypatch, server)
        from core.credential_intelligence.discovery import _generations, invalidate_stale_capabilities, load_assertions

        descriptor = _descriptor(server)
        key_gen, _old = _generations("openrouter", descriptor, PROVIDER_KEY)
        degraded = invalidate_stale_capabilities(
            "openrouter", key_generation=key_gen, endpoint_generation="sha256:new-base"
        )
        assert degraded.refreshed_generations_match is False
        assert all(m.evidence == "unknown" for m in degraded.models)
        assert load_assertions()["openrouter"].endpoint_generation == "sha256:new-base"


# ------------------------------------------------------------------ shapes

def test_anthropic_shape_parses_with_capabilities(home, monkeypatch):
    import hashlib

    with FakeProviderServer() as server:
        key_digest = hashlib.sha256(PROVIDER_KEY.encode()).hexdigest()

        def respond(record):
            keyed = (record.get("header_sha256") or {}).get("x-api-key") == key_digest
            if record["path"].endswith("/models"):
                if not keyed:
                    return (401, {"type": "error", "error": {"type": "authentication_error", "message": "x-api-key required"}})
                return (200, ANTHROPIC_LIST)
            if keyed:
                return (200, {"data": {"label": "harness"}})
            return (401, {"type": "error", "error": {"type": "authentication_error", "message": "x-api-key required"}})

        server.responses = respond
        base = _descriptor(server)
        descriptor = base.__class__(**{
            **base.__dict__,
            "verify_endpoint": f"{server.url}/v1/key",
            "provider_id": "anthropic",
            "auth_style": "header",
            "auth_name": "X-Api-Key",
            "credential_slot": "llm.cloud.anthropic",
            "key_prefixes": ("sk-ant-",),
            "extra_headers": {"anthropic-version": "2023-06-01"},
            "models_protocol": "anthropic_models_list",
        })
        import core.credential_intelligence.provider_registry as pr

        table = {d.provider_id: d for d in pr.default_registry().providers()}
        table["anthropic"] = descriptor
        monkeypatch.setattr(pr, "default_registry", lambda: pr.ProviderRegistry(table))
        monkeypatch.setattr(
            "core.credential_store.get_credential",
            lambda name: PROVIDER_KEY if name == "llm.cloud.anthropic" else None,
        )
        from core.credential_intelligence.discovery import refresh_discovery

        assertion = refresh_discovery("anthropic")
        assert assertion.protocol == "anthropic_models_list"
        row = assertion.models[0]
        assert row.display_name == "Claude Harness 4"
        assert row.context_window == 200000
        assert row.max_output_tokens == 32000


def test_malformed_list_is_named_not_swallowed(home, monkeypatch):
    with FakeProviderServer() as server:
        _by_path(server, models_body={"data": "unavailable"})
        _stored_key(monkeypatch)
        _registry(monkeypatch, _descriptor(server))
        assertion = _refresh(monkeypatch, server)
        assert assertion.last_refresh_status == "protocol_mismatch"
        _by_path(server, models_body={"unexpected": True})
        assertion3 = _refresh(monkeypatch, server)
        assert assertion3.last_refresh_status == "protocol_mismatch"
        _by_path(server, models_body="<html>sign in</html>")
        assertion4 = _refresh(monkeypatch, server)
        assert assertion4.last_refresh_status == "malformed_response"


def test_search_provider_refuses_model_discovery(home, monkeypatch):
    import core.credential_intelligence.provider_registry as pr
    from core.credential_intelligence.discovery import refresh_discovery

    registry = pr.default_registry()
    monkeypatch.setattr(pr, "default_registry", lambda: registry)
    assert refresh_discovery("search.brave") is None or refresh_discovery("search.brave").models == ()
