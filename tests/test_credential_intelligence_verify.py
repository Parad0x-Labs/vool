"""P0 credential intelligence — selected-provider verification against fake local providers.

THE LAW UNDER TEST
------------------
* The key is verified against EXACTLY ONE provider — the one the operator selected. Live
  loopback fakes for the OTHER shortlist candidates must see zero requests.
* ``invalid`` / ``exhausted`` (quota) / ``rate_limited`` / ``unauthorized`` /
  ``network_unavailable`` are five DISTINCT typed outcomes — none may collapse into another.
* No environment variable can redirect the keyed request to an arbitrary host: provider base
  URL env overrides and HTTP(S)_PROXY are ignored (the transport goes to the pinned registry
  endpoint, proxy-free).
* The verification receipt carries the host and outcome — never the key.
"""
from __future__ import annotations

import json

import pytest

from tests._credential_intelligence_support import (
    ODD_KEY,
    FakeProviderServer,
    descriptor_for,
)


@pytest.fixture
def fake_server():
    with FakeProviderServer() as server:
        yield server


def _verify(server, secret=ODD_KEY, **desc_overrides):
    from core.credential_intelligence.verification import verify_provider_credential

    return verify_provider_credential(secret, descriptor_for(server, **desc_overrides), timeout_s=5.0)


# ------------------------------------------------------------------ distinct outcomes

def test_200_is_verified(fake_server):

    fake_server.responses = [(200, {"data": [{"id": "m-1"}]})]
    outcome = _verify(fake_server)
    assert outcome.status == "verified"
    assert outcome.http_status == 200
    assert fake_server.saw_bearer(ODD_KEY)


def test_401_is_invalid_not_unauthorized(fake_server):
    fake_server.responses = [(401, {"error": {"message": "bad key"}})]
    assert _verify(fake_server).status == "invalid"


def test_403_is_unauthorized_not_invalid(fake_server):
    fake_server.responses = [(403, {"error": {"message": "no scope"}})]
    assert _verify(fake_server).status == "unauthorized"


def test_402_is_exhausted(fake_server):
    fake_server.responses = [(402, {"error": {"message": "quota"}})]
    assert _verify(fake_server).status == "exhausted"


def test_429_is_rate_limited(fake_server):
    fake_server.responses = [(429, {"error": {"message": "slow down"}})]
    assert _verify(fake_server).status == "rate_limited"


def test_unreachable_port_is_network_unavailable():
    from core.credential_intelligence.verification import verify_provider_credential

    # Bind then immediately close a server so the port is real but nothing listens.
    ghost = FakeProviderServer()
    ghost._thread.start()
    ghost._server.shutdown()
    ghost._server.server_close()
    outcome = verify_provider_credential(ODD_KEY, descriptor_for(ghost), timeout_s=5.0)
    assert outcome.status == "network_unavailable"
    assert outcome.http_status is None


def test_outcome_dict_is_receipt_safe(fake_server):
    fake_server.responses = [(200, {"data": []})]
    payload = _verify(fake_server).to_dict()
    as_text = json.dumps(payload)
    assert ODD_KEY not in as_text
    assert "Bearer" not in as_text
    assert payload["endpoint_host"] == fake_server.host


# ------------------------------------------------------------------ one provider only

def test_verification_never_touches_the_unselected_candidate():
    """Two live fakes, both plausible; the operator selected A. A sees exactly one request,
    B — same registry, same key format — sees zero. Trying the key against several providers
    is the forbidden behavior and this pins it with real sockets."""
    from core.credential_intelligence.verification import verify_provider_credential

    with FakeProviderServer() as prov_a, FakeProviderServer() as prov_b:
        desc_a = descriptor_for(prov_a, "alpha", key_prefixes=("zk9-",))
        descriptor_for(prov_b, "beta", key_prefixes=("zk9-",))  # plausible, NOT selected
        outcome = verify_provider_credential("zk9-" + "a" * 40, desc_a, timeout_s=5.0)
        assert outcome.provider_id == "alpha"
        assert prov_a.request_count == 1
        assert prov_b.request_count == 0, "verification probed an unselected provider"


def test_verifier_reads_no_environment():
    """Structural: the verifier module has no os.environ access at all — the endpoint comes
    from the descriptor, so there is nothing for an env var to redirect."""
    import inspect

    import core.credential_intelligence.verification as verification_module

    source = inspect.getsource(verification_module)
    assert "environ" not in source


# ------------------------------------------------------------------ env-redirect immunity

def test_provider_base_url_env_cannot_redirect_verification(monkeypatch, fake_server):
    """Every known base-URL override env is pointed at an attacker server; verification must
    still go to the descriptor's pinned endpoint."""
    with FakeProviderServer() as attacker:
        for name in (
            "OPENROUTER_BASE_URL", "VOOL_OPENROUTER_BASE_URL", "OPENAI_BASE_URL",
            "VOOL_REMOTE_BASE_URL", "VOOL_CUSTOM_BASE_URL", "TETHER_BASE_URL",
        ):
            monkeypatch.setenv(name, attacker.url)
        fake_server.responses = [(200, {"data": []})]
        outcome = _verify(fake_server, provider_id="openrouter")
        assert outcome.status == "verified"
        assert fake_server.request_count == 1
        assert attacker.request_count == 0, "an env var redirected credential verification"


def test_proxy_env_cannot_intercept_the_keyed_request(monkeypatch, fake_server):
    """HTTP_PROXY is pointed at a live attacker server. urllib would route the loopback GET
    through it by default; the verifier must send the key proxy-free to the pinned host."""
    with FakeProviderServer() as attacker:
        monkeypatch.setenv("HTTP_PROXY", attacker.url)
        monkeypatch.setenv("HTTPS_PROXY", attacker.url)
        monkeypatch.setenv("ALL_PROXY", attacker.url)
        monkeypatch.delenv("NO_PROXY", raising=False)
        fake_server.responses = [(200, {"data": []})]
        outcome = _verify(fake_server)
        assert outcome.status == "verified"
        assert fake_server.request_count == 1
        assert fake_server.saw_bearer(ODD_KEY)
        assert attacker.request_count == 0, "a proxy env var intercepted the credential"


def test_insecure_non_loopback_endpoint_is_refused_before_any_socket():
    """The transport gate stays load-bearing: an http:// endpoint that is not loopback never
    receives the key, and the refusal is typed — distinct from network_unavailable."""
    from core.credential_intelligence.verification import verify_provider_credential

    desc = descriptor_for(FakeProviderServer(), verify_endpoint="http://definitely-not-loopback.invalid/v1/models")
    outcome = verify_provider_credential(ODD_KEY, desc, timeout_s=5.0)
    assert outcome.status == "refused"
    assert outcome.http_status is None


# ------------------------------------------------------------------ verification goes through the door

def test_verification_runs_inside_the_named_effect_scope(monkeypatch, fake_server):
    """The keyed request must ride the ONE outbound door (veto + accounting), self-authorized
    under its own named background scope — never a raw urlopen of its own."""
    import core.remote_fetch_policy as rfp

    calls: list[dict] = []
    real = rfp.open_remote_url

    def recording(url, **kwargs):
        calls.append({"url": url, **{k: v for k, v in kwargs.items() if k != "headers"}})
        fake_server.responses = [(200, {"data": []})]
        return real(url, headers=kwargs.get("headers"), timeout=kwargs.get("timeout", 5.0))

    monkeypatch.setattr(rfp, "open_remote_url", recording)
    from core.credential_intelligence import verification

    # The verifier resolves the door at call time, so the rfp patch above IS the seam; there is
    # no module-level binding to replace.
    outcome = verification.verify_provider_credential(ODD_KEY, descriptor_for(fake_server), timeout_s=5.0)
    assert outcome.status == "verified"
    assert calls and calls[0]["url"].startswith(fake_server.url)
    assert calls[0].get("no_proxy") is True, "the keyed request must bypass proxy env routing"
