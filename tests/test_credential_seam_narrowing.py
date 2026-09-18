"""P0 credential intelligence — narrowing the two existing seams that violate the new laws.

1. INTAKE (``cloud key <value>`` fast command): an unrecognized key format used to be sealed
   under the active provider's slot (OpenRouter legacy default) — a GUESS. Storing a key in
   the wrong slot produces a provider that fails auth forever with a key the user knows is
   good. The narrowed law: classification decides — high-confidence prefix seals as before,
   ambiguous asks, and an UNRECOGNIZED format stores NOTHING and asks for the provider.

2. VERIFICATION (``cloud_connection_state`` probe): the probe used to honour
   ``OPENROUTER_BASE_URL`` / ``VOOL_OPENROUTER_BASE_URL`` env overrides, so an env var could
   silently send the Bearer key to an arbitrary HTTPS host. The narrowed law: probes of NAMED
   providers always go to the catalog-pinned base URL; only the explicitly user-configured
   ``custom`` endpoint resolves its base URL from env/store.
"""
from __future__ import annotations

# ruff: noqa: F811 (imported pytest fixtures are re-exposed as test parameters by design)
import pytest

from tests._credential_intelligence_support import isolated_home  # noqa: F401 (fixture)

FAKE_OR_KEY = "sk-or-v1-" + "d" * 56
UNRECOGNIZED_KEY = "qz7-" + "nobody-claims-this-shape-0123456789"


@pytest.fixture
def store(monkeypatch, isolated_home):
    saved: dict[str, str] = {}

    monkeypatch.setattr("core.credential_store.store_credential", lambda name, value, **k: saved.setdefault(name, value))
    monkeypatch.setattr("core.credential_store.has_credential", lambda name: name in saved)
    monkeypatch.setattr("core.credential_store.get_credential", lambda name: saved.get(name))
    monkeypatch.setattr("core.credential_store.delete_credential", lambda name: saved.pop(name, None) is not None)
    monkeypatch.setattr("core.runtime_provider_defaults.activate_openrouter_byok", lambda env=None: "openrouter-byok:test")
    monkeypatch.setattr("core.runtime_provider_defaults.deactivate_openrouter_byok", lambda: 1)
    return saved


def test_recognized_prefix_still_seals_directly(store):
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_key_command

    reply = maybe_handle_cloud_key_command(f"cloud key {FAKE_OR_KEY}", owner_local=True)
    assert store.get("llm.cloud.openrouter") == FAKE_OR_KEY
    assert FAKE_OR_KEY not in reply


def test_unrecognized_format_stores_nothing_and_asks(store):
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_key_command

    reply = maybe_handle_cloud_key_command(f"cloud key {UNRECOGNIZED_KEY}", owner_local=True)
    assert store == {}, "an unrecognized key format must never be sealed under a guessed slot"
    assert UNRECOGNIZED_KEY not in reply
    assert "provider" in reply.lower(), "the operator must be asked which provider it is"


def test_unrecognized_format_with_explicit_provider_seals_that_choice(store):
    from core.agent_runtime.fast_command_surface import maybe_handle_cloud_key_command

    reply = maybe_handle_cloud_key_command(f"cloud key {UNRECOGNIZED_KEY} openai", owner_local=True)
    assert store.get("llm.cloud.openai") == UNRECOGNIZED_KEY, "the operator's explicit choice is authoritative"
    assert UNRECOGNIZED_KEY not in reply


# ------------------------------------------------------------------ probe env-redirect narrowing

def _probe(monkeypatch, tmp_path, *, provider, responses):
    """Drive the REAL probe (real door, real HTTP) at a loopback fake and return (state, calls)."""
    from dataclasses import replace

    from tests._credential_intelligence_support import FakeProviderServer

    with FakeProviderServer(responses=responses) as server:
        monkeypatch.setenv("VOOL_HOME", str(tmp_path))
        for env_name in ("OPENROUTER_API_KEY", "VOOL_OPENROUTER_API_KEY"):
            monkeypatch.delenv(env_name, raising=False)
        import core.runtime_paths as rp

        rp.configure_runtime_home(tmp_path)
        try:
            monkeypatch.setattr("core.credential_store.get_credential", lambda name: "sk-or-v1-" + "e" * 40)
            import core.cloud_connection_state as ccs
            from core.cloud_providers import PROVIDERS

            ccs.reset_probe_rate_limit_for_tests()
            # pin the CATALOG base URL to the loopback fake — exactly what a pinned registry does
            monkeypatch.setitem(PROVIDERS, provider, replace(PROVIDERS[provider], base_url=server.url))
            monkeypatch.setattr(ccs, "_state_path", lambda: tmp_path / "conn_state.json")
            result = ccs.run_auth_probe(provider=provider)
            return result, server
        finally:
            rp.configure_runtime_home(None)


def test_named_provider_probe_ignores_base_url_env_redirect(monkeypatch, tmp_path):
    """OPENROUTER_BASE_URL points at an attacker server; the probe must still go to the
    catalog-pinned (here: loopback fake) URL and the attacker must see zero requests."""
    from tests._credential_intelligence_support import FakeProviderServer

    with FakeProviderServer() as attacker:
        monkeypatch.setenv("OPENROUTER_BASE_URL", attacker.url)
        monkeypatch.setenv("VOOL_OPENROUTER_BASE_URL", attacker.url)
        # OpenRouter's documented /key answer: the probe judges a 200 by that shape (2026-09-14).
        key_answer = {"data": {"label": "fixture", "limit_remaining": None}}
        result, server = _probe(monkeypatch, tmp_path, provider="openrouter", responses=[(200, key_answer)])
        assert result["state"] == "ok"
        assert server.request_count == 1
        assert attacker.request_count == 0, "an env var redirected the bearer-key probe"


def test_custom_provider_base_url_still_resolves_from_env(monkeypatch, tmp_path):
    """The narrowed seam keeps the ONE sanctioned override: the ``custom`` provider's
    endpoint is explicitly user-supplied, so its env resolution still applies."""
    from core.cloud_providers import custom_base_url

    monkeypatch.setenv("VOOL_CUSTOM_BASE_URL", "https://my-gateway.example/v1")
    assert custom_base_url() == "https://my-gateway.example/v1"
    monkeypatch.delenv("VOOL_CUSTOM_BASE_URL")
    monkeypatch.setenv("VOOL_REMOTE_BASE_URL", "https://other.example/v1")
    assert custom_base_url() == "https://other.example/v1"
