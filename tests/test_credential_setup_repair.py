
"""Settings key setup (2026-09-14): valid keys rejected by "Auto-detect".

Measured before this repair (validation-logs/autodetection-20260914, baseline replay):

* the /settings form on "Auto-detect from the key" posted ``{"value": key}`` and the save authority
  answered ``400 unsupported credential name`` for every key -- nothing recognised it;
* the credential-intelligence verifier sent ``Authorization: Bearer`` to Brave, whose documented
  auth is ``X-Subscription-Token``, so a valid Brave key came back ``unexpected 422``;
* a cross-origin redirect carried the key to the second origin and was reported verified; an HTML
  page or a wrong-shaped JSON 200 was "verified"; the cloud Test door reported a 429 as unreachable.

THE LAW UNDER TEST
------------------
* The one verification owner places the key where the provider documents it, judges a 2xx by the
  provider's documented response shape, never lets a redirect carry the key to another origin, and
  keeps throttling / outage / timeout / missing endpoint / quota apart from a rejected key.
* The Settings doors consume that owner: a recognised key is verified once, stored and bound without
  a second request; an unrecognised key gets a reason and a selection path, never "unsupported name";
  a refused verification stores nothing.

Real HTTP on loopback against fakes answering in the providers' recorded shapes (OpenRouter and Brave
API references, read 2026-09-14). Keys are synthetic. Isolated home, file vault -- no Keychain.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json

import pytest

from tests._credential_intelligence_support import (
    FakeProviderServer,
    isolated_home,
    sweep_home_for_secret,
    vault_home,
)
from tests.first_run_pact_rig import pact_rig


def _synthetic(label: str, prefix: str, length: int) -> str:
    return prefix + (hashlib.sha256(f"setup-repair:{label}".encode()).hexdigest() * 2)[:length]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


OPENROUTER_KEY = _synthetic("openrouter", "sk-or-v1-", 64)
BRAVE_KEY = _synthetic("brave", "BSAI", 27)
SERPER_KEY = _synthetic("serper", "", 40)
NOVEL_KEY = _synthetic("novel-compatible", "nv1_", 40)
OPAQUE_KEY = _synthetic("opaque", "zq7-", 40)


def openrouter_key_body(limit_remaining=None) -> dict:
    return {
        "data": {
            "label": "setup-repair",
            "limit": None if limit_remaining is None else 5,
            "limit_remaining": limit_remaining,
            "usage": 0,
            "is_free_tier": True,
        }
    }


def brave_results() -> dict:
    return {
        "type": "search",
        "query": {"original": "example"},
        "web": {"type": "search", "results": [{"type": "search_result", "title": "Example Domain", "url": "https://example.com/", "description": "Example."}]},
    }


def brave_error(status: int, code: str) -> dict:
    return {"type": "ErrorResponse", "error": {"id": "t", "status": status, "code": code, "detail": "synthetic"}, "time": 0}


def _brave(valid_key: str):
    want = _sha(valid_key)

    def respond(record):
        if record["header_sha256"].get("x-subscription-token") == want:
            return (200, brave_results())
        return (422, brave_error(422, "SUBSCRIPTION_TOKEN_INVALID"))

    return respond


def _openrouter(valid_key: str):
    want = _sha(f"Bearer {valid_key}")

    def respond(record):
        if record["auth_sha256"] != want:
            return (401, {"error": {"code": 401, "message": "Missing Authentication header"}})
        return (200, openrouter_key_body())

    return respond


def _descriptor(provider_id: str, endpoint: str):
    from core.credential_intelligence.provider_registry import default_registry

    return dataclasses.replace(default_registry().get(provider_id), verify_endpoint=endpoint)


def _verify(provider_id: str, endpoint: str, secret: str, *, timeout_s: float = 5.0):
    from core.credential_intelligence.verification import verify_provider_credential

    return verify_provider_credential(secret, _descriptor(provider_id, endpoint), timeout_s=timeout_s)


def _point_openrouter_at(monkeypatch, base_url: str) -> None:
    import core.cloud_providers as cloud_providers

    monkeypatch.setitem(cloud_providers.PROVIDERS, "openrouter", dataclasses.replace(cloud_providers.PROVIDERS["openrouter"], base_url=base_url))


def _point_brave_at(monkeypatch, search_url: str) -> None:
    import core.search_providers as search_providers

    monkeypatch.setitem(search_providers.SEARCH_PROVIDERS, "brave", dataclasses.replace(search_providers.SEARCH_PROVIDERS["brave"], search_url=search_url))


# ------------------------------------------------------------------ auth placement


def test_brave_key_rides_x_subscription_token_not_bearer_and_verifies(vault_home):
    with FakeProviderServer(_brave(BRAVE_KEY)) as brave:
        outcome = _verify("search.brave", f"{brave.url}/res/v1/web/search", BRAVE_KEY)
    assert outcome.status == "verified", outcome
    assert "x-subscription-token" in brave.requests[0]["header_names"]
    assert brave.requests[0]["auth_present"] is False, "a Bearer header was sent to Brave"


def test_a_post_search_provider_gets_its_own_key_header_and_a_json_query_body(vault_home):
    want = _sha(SERPER_KEY)

    def respond(record):
        placed = record["header_sha256"].get("x-api-key") == want and record["method"] == "POST" and "q" in record["json_keys"]
        if placed:
            return (200, {"organic": [{"title": "t", "link": "https://example.com/", "snippet": "s"}]})
        return (401, {"message": "Unauthorized."})

    with FakeProviderServer(respond) as serper:
        outcome = _verify("search.serper", f"{serper.url}/search", SERPER_KEY)
    assert outcome.status == "verified", outcome
    assert serper.requests[0]["auth_present"] is False


# ------------------------------------------------------------------ what a 422 means


def test_brave_token_invalid_error_code_is_an_invalid_key(vault_home):
    with FakeProviderServer([(422, brave_error(422, "SUBSCRIPTION_TOKEN_INVALID"))]) as brave:
        outcome = _verify("search.brave", f"{brave.url}/res/v1/web/search", BRAVE_KEY)
    assert outcome.status == "invalid"
    assert getattr(outcome, "provider_error_code", "") == "SUBSCRIPTION_TOKEN_INVALID"


def test_a_422_with_another_error_code_is_not_called_an_invalid_key(vault_home):
    with FakeProviderServer([(422, brave_error(422, "VALIDATION"))]) as brave:
        outcome = _verify("search.brave", f"{brave.url}/res/v1/web/search", BRAVE_KEY)
    assert outcome.status == "unexpected"
    assert getattr(outcome, "provider_error_code", "") == "VALIDATION"
    assert "422" in outcome.detail


# ------------------------------------------------------------------ a 2xx must look like the provider


def test_an_html_200_is_a_malformed_response_not_a_verified_key(vault_home):
    page = (200, b"<html><body>Sign in to this network</body></html>", {"Content-Type": "text/html"})
    with FakeProviderServer([page]) as portal:
        outcome = _verify("openrouter", f"{portal.url}/api/v1/key", OPENROUTER_KEY)
    assert outcome.status == "malformed_response"


def test_json_without_the_documented_key_shape_is_unexpected_schema(vault_home):
    with FakeProviderServer([(200, {"ok": True})]) as imposter:
        outcome = _verify("openrouter", f"{imposter.url}/api/v1/key", OPENROUTER_KEY)
    assert outcome.status == "unexpected_schema"


def test_a_search_answer_without_its_results_list_is_unexpected_schema(vault_home):
    with FakeProviderServer([(200, {"type": "search"})]) as brave:
        outcome = _verify("search.brave", f"{brave.url}/res/v1/web/search", BRAVE_KEY)
    assert outcome.status == "unexpected_schema"


def test_a_valid_openrouter_key_with_no_credit_left_verifies_and_reports_the_account(vault_home):
    with FakeProviderServer([(200, openrouter_key_body(limit_remaining=0))]) as router:
        outcome = _verify("openrouter", f"{router.url}/api/v1/key", OPENROUTER_KEY)
    assert outcome.status == "verified"
    assert getattr(outcome, "account_state", "") == "exhausted"


# ------------------------------------------------------------------ transient and quota are not a bad key


def test_429_is_throttling_with_the_retry_after_the_provider_named(vault_home):
    throttled = (429, {"error": {"code": 429, "message": "Rate limit exceeded"}}, {"Retry-After": "7"})
    with FakeProviderServer([throttled]) as router:
        outcome = _verify("openrouter", f"{router.url}/api/v1/key", OPENROUTER_KEY)
    assert outcome.status == "rate_limited"
    assert getattr(outcome, "retry_after_s", None) == 7.0


def test_a_5xx_is_a_provider_outage_not_an_unexpected_key_verdict(vault_home):
    with FakeProviderServer([(503, {"error": {"code": 503, "message": "No available provider"}})]) as router:
        outcome = _verify("openrouter", f"{router.url}/api/v1/key", OPENROUTER_KEY)
    assert outcome.status == "provider_unavailable"


def test_a_404_names_a_wrong_endpoint(vault_home):
    with FakeProviderServer([(404, b"<h1>Not Found</h1>", {"Content-Type": "text/html"})]) as wrong:
        outcome = _verify("openrouter", f"{wrong.url}/wrong/base/key", OPENROUTER_KEY)
    assert outcome.status == "endpoint_not_found"


def test_no_answer_within_the_bound_is_a_timeout_not_unreachable(vault_home):
    with FakeProviderServer([(200, openrouter_key_body(), {}, 2.0)]) as slow:
        outcome = _verify("openrouter", f"{slow.url}/api/v1/key", OPENROUTER_KEY, timeout_s=0.5)
    assert outcome.status == "timeout"


# ------------------------------------------------------------------ an entered endpoint is asked without the key first


def test_an_entered_endpoint_that_answers_without_a_key_cannot_confirm_one(vault_home):
    catalogue = (200, {"object": "list", "data": [{"id": "open-model", "object": "model"}]})
    with FakeProviderServer([catalogue]) as endpoint:
        outcome = _verify("custom", f"{endpoint.url}/v1/models", NOVEL_KEY)
    assert outcome.status == "public_endpoint", outcome
    assert endpoint.request_count == 1
    assert not _credential_arrived(endpoint), "the key was sent to an endpoint that never asked for one"


def test_an_entered_endpoint_gets_the_key_only_after_it_refuses_a_keyless_request(vault_home):
    want = _sha(f"Bearer {NOVEL_KEY}")

    def respond(record):
        if record["auth_sha256"] != want:
            return (401, {"error": {"message": "Incorrect API key provided.", "type": "invalid_request_error", "code": "invalid_api_key"}})
        return (200, {"object": "list", "data": [{"id": "novel-lab/chat-large", "object": "model"}]})

    with FakeProviderServer(respond) as endpoint:
        outcome = _verify("custom", f"{endpoint.url}/v1/models", NOVEL_KEY)
    assert outcome.status == "verified", outcome
    assert [r["auth_present"] for r in endpoint.requests] == [False, True]


def test_an_entered_endpoint_that_answers_a_web_page_never_receives_the_key(vault_home):
    with FakeProviderServer([(200, b"<html>sign in to the network</html>", {"Content-Type": "text/html"})]) as portal:
        outcome = _verify("custom", f"{portal.url}/v1/models", NOVEL_KEY)
    assert outcome.status == "malformed_response", outcome
    assert portal.request_count == 1
    assert not _credential_arrived(portal)


def test_a_named_provider_with_a_documented_auth_check_is_asked_with_the_key_directly(vault_home):
    with FakeProviderServer(_openrouter(OPENROUTER_KEY)) as router:
        outcome = _verify("openrouter", f"{router.url}/api/v1/key", OPENROUTER_KEY)
    assert outcome.status == "verified", outcome
    assert [r["auth_present"] for r in router.requests] == [True]


# ------------------------------------------------------------------ redirects never carry the key away


def _credential_arrived(server) -> bool:
    """Whether any request at `server` carried a credential header. The fakes record digests and
    header names, never a key."""
    return any(
        r["auth_present"] or {"x-subscription-token", "x-api-key"} & set(r["header_names"])
        for r in server.requests
    )


def test_the_door_keeps_urlopen_as_its_transport_and_attaches_the_key_unredirected(monkeypatch):
    """The key header rides unredirected (urllib never copies those onto a redirect hop) and the
    exchange still goes through ``urllib.request.urlopen`` -- the one call test transports and the
    scratch-daemon fixture transport replace. A transport that reports some other URL but never
    redirected is not mistaken for a redirect."""
    import urllib.request

    from core.effect_gateway import named_background_effect_scope
    from core.remote_fetch_policy import open_remote_url

    sent: list = []

    class _Answer:
        status = 200

        def read(self, *_a):
            return b"{}"

        def geturl(self):
            return "https://elsewhere.invalid/"

    def _transport(request, timeout=None, **_kwargs):
        sent.append(request)
        return _Answer()

    monkeypatch.setattr(urllib.request, "urlopen", _transport)
    with named_background_effect_scope("test.settings_setup_repair"):
        answer = open_remote_url(
            "https://api.example.invalid/v1/key",
            headers={"Authorization": f"Bearer {OPENROUTER_KEY}", "Accept": "application/json"},
            credential_headers=("Authorization",),
        )
    assert isinstance(answer, _Answer)
    (request,) = sent
    assert request.unredirected_hdrs.get("Authorization") == f"Bearer {OPENROUTER_KEY}"
    assert "Authorization" not in request.headers
    assert request.headers.get("Accept") == "application/json"


def test_a_cross_origin_redirect_is_refused_and_the_key_never_arrives_there(vault_home):
    with FakeProviderServer([(200, openrouter_key_body())]) as elsewhere:
        with FakeProviderServer([(302, b"", {"Location": f"{elsewhere.url}/api/v1/key"})]) as pinned:
            outcome = _verify("openrouter", f"{pinned.url}/api/v1/key", OPENROUTER_KEY)
        assert outcome.status == "redirected"
        assert outcome.redirect_origin == elsewhere.url
        # urllib may follow the hop itself; the key never crosses it and that answer is never judged.
        assert not _credential_arrived(elsewhere), "the key followed a redirect to another origin"
        assert elsewhere.request_count <= 1


def test_a_same_origin_redirect_is_asked_again_with_the_key_and_verifies(vault_home):
    want = _sha(f"Bearer {OPENROUTER_KEY}")

    def respond(record):
        if record["path"] == "/api/v1/old-key":
            return (302, b"", {"Location": "/api/v1/key"})
        if record["auth_sha256"] != want:
            return (401, {"error": {"code": 401, "message": "Missing Authentication header"}})
        return (200, openrouter_key_body())

    with FakeProviderServer(respond) as router:
        outcome = _verify("openrouter", f"{router.url}/api/v1/old-key", OPENROUTER_KEY)
    assert outcome.status == "verified", outcome
    # urllib followed the hop WITHOUT the key (its 401 was never judged); the door then asked the
    # final URL once more, with the key.
    assert [(r["path"], r["auth_present"]) for r in router.requests] == [
        ("/api/v1/old-key", True),
        ("/api/v1/key", False),
        ("/api/v1/key", True),
    ]


def test_a_provider_that_keeps_redirecting_gets_the_key_at_most_twice(vault_home):
    def respond(record):
        return (302, b"", {"Location": "/api/v1/next" if record["path"] == "/api/v1/key" else "/api/v1/key"})

    with FakeProviderServer(respond) as router:
        outcome = _verify("openrouter", f"{router.url}/api/v1/key", OPENROUTER_KEY)
    assert outcome.status == "redirected"
    assert sum(1 for r in router.requests if r["auth_present"]) == 2


# ------------------------------------------------------------------ the cloud Test door on a stored key


@pytest.fixture
def stored_openrouter(vault_home, monkeypatch):
    for name in ("OPENROUTER_API_KEY", "VOOL_OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    from core import cloud_connection_state, credential_store

    credential_store.store_credential("llm.cloud.openrouter", OPENROUTER_KEY, label="OpenRouter")
    cloud_connection_state.reset_probe_rate_limit_for_tests()
    return cloud_connection_state


def test_cloud_test_calls_a_rejected_key_unauthorized_not_unreachable(stored_openrouter, monkeypatch):
    with FakeProviderServer([(401, {"error": {"code": 401, "message": "User not found."}})]) as router:
        _point_openrouter_at(monkeypatch, f"{router.url}/api/v1")
        result = stored_openrouter.run_auth_probe(provider="openrouter", now=1000.0)
    assert (result["state"], result["detail"], result["http_status"]) == ("failed", "unauthorized", 401)


def test_cloud_test_calls_throttling_rate_limited_not_unreachable(stored_openrouter, monkeypatch):
    with FakeProviderServer([(429, {"error": {"code": 429, "message": "Rate limit exceeded"}}, {"Retry-After": "7"})]) as router:
        _point_openrouter_at(monkeypatch, f"{router.url}/api/v1")
        result = stored_openrouter.run_auth_probe(provider="openrouter", now=1000.0)
    assert (result["state"], result["detail"], result["http_status"]) == ("failed", "rate_limited", 429)


def test_cloud_test_never_forwards_the_stored_key_to_a_redirect_origin(stored_openrouter, monkeypatch):
    with FakeProviderServer([(200, openrouter_key_body())]) as elsewhere:
        with FakeProviderServer([(302, b"", {"Location": f"{elsewhere.url}/api/v1/key"})]) as pinned:
            _point_openrouter_at(monkeypatch, f"{pinned.url}/api/v1")
            result = stored_openrouter.run_auth_probe(provider="openrouter", now=1000.0)
        assert (result["state"], result["detail"]) == ("failed", "redirected")
        assert not _credential_arrived(elsewhere), "the stored key followed a redirect to another origin"


def test_cloud_test_does_not_turn_an_html_200_green(stored_openrouter, monkeypatch):
    with FakeProviderServer([(200, b"<html>captive portal</html>", {"Content-Type": "text/html"})]) as portal:
        _point_openrouter_at(monkeypatch, f"{portal.url}/api/v1")
        result = stored_openrouter.run_auth_probe(provider="openrouter", now=1000.0)
    assert (result["state"], result["detail"]) == ("failed", "malformed_response")


def test_cloud_test_goes_green_on_the_documented_key_answer(stored_openrouter, monkeypatch):
    with FakeProviderServer(_openrouter(OPENROUTER_KEY)) as router:
        _point_openrouter_at(monkeypatch, f"{router.url}/api/v1")
        result = stored_openrouter.run_auth_probe(provider="openrouter", now=1000.0)
    assert (result["state"], result["http_status"]) == ("ok", 200)


# ------------------------------------------------------------------ the search client (Test door + live use)


def _search_brave(search_url: str, key: str):
    from core.effect_gateway import named_background_effect_scope
    from core.search_providers import SEARCH_PROVIDERS
    from tools.web.search_api_client import search

    cfg = dataclasses.replace(SEARCH_PROVIDERS["brave"], search_url=search_url)
    with named_background_effect_scope("test.settings_setup_repair"):
        return search(cfg, "example", key, max_hits=3, timeout_s=5.0)


def test_search_client_refuses_a_redirect_to_another_origin_without_forwarding_the_key(vault_home):
    from tools.web.search_api_client import SearchApiError

    with FakeProviderServer(_brave(BRAVE_KEY)) as elsewhere:
        with FakeProviderServer([(302, b"", {"Location": f"{elsewhere.url}/res/v1/web/search"})]) as pinned:
            with pytest.raises(SearchApiError) as raised:
                _search_brave(f"{pinned.url}/res/v1/web/search", BRAVE_KEY)
        assert raised.value.reason == "redirect_refused"
        assert not _credential_arrived(elsewhere), "the search key followed a redirect to another origin"


def test_search_client_asks_a_same_origin_redirect_target_again_with_the_key(vault_home):
    want = _sha(BRAVE_KEY)

    def respond(record):
        if record["path"] == "/res/v1/web/old-search":
            return (302, b"", {"Location": "/res/v1/web/search?q=example&count=3"})
        if record["header_sha256"].get("x-subscription-token") != want:
            return (422, brave_error(422, "SUBSCRIPTION_TOKEN_INVALID"))
        return (200, brave_results())

    with FakeProviderServer(respond) as brave:
        hits = _search_brave(f"{brave.url}/res/v1/web/old-search", BRAVE_KEY)
    assert [hit.url for hit in hits] == ["https://example.com/"]
    # urllib followed the hop without the token (its 422 was never judged); the door asked again with it.
    assert ["x-subscription-token" in r["header_names"] for r in brave.requests] == [True, False, True]


def test_search_client_reads_the_token_invalid_code_as_a_rejected_key(vault_home):
    from tools.web.search_api_client import SearchApiError

    with FakeProviderServer([(422, brave_error(422, "SUBSCRIPTION_TOKEN_INVALID"))]) as brave:
        with pytest.raises(SearchApiError) as raised:
            _search_brave(f"{brave.url}/res/v1/web/search", BRAVE_KEY)
    assert raised.value.reason == "unauthorized"


def test_search_client_keeps_any_other_422_an_http_error(vault_home):
    from tools.web.search_api_client import SearchApiError

    with FakeProviderServer([(422, brave_error(422, "VALIDATION"))]) as brave:
        with pytest.raises(SearchApiError) as raised:
            _search_brave(f"{brave.url}/res/v1/web/search", BRAVE_KEY)
    assert raised.value.reason == "http_error"


# ------------------------------------------------------------------ the Settings doors


def _stored(slot: str):
    from core import credential_store

    return credential_store.get_credential(slot)


def _begin_and_classify(rig, value: str):
    status, begun = rig.post("/api/intake/begin", {})
    assert status == 200, begun
    session_id = begun["session_id"]
    status, classified = rig.post("/api/intake/classify", {"session_id": session_id, "value": value})
    assert status == 200, classified
    return session_id, classified


def test_auto_detect_save_answers_with_a_selection_not_unsupported_credential_name(pact_rig):
    status, payload = pact_rig.post("/api/settings/credentials", {"value": OPENROUTER_KEY})
    assert status == 400, payload
    assert payload.get("error") != "unsupported credential name"
    assert payload.get("code") == "provider_selection_required"
    assert payload.get("suggestion") == "openrouter"
    assert _stored("llm.cloud.openrouter") is None


def test_auto_detect_save_with_an_opaque_key_explains_and_stores_nothing(pact_rig):
    status, payload = pact_rig.post("/api/settings/credentials", {"value": OPAQUE_KEY})
    assert status == 400, payload
    assert payload.get("code") == "provider_selection_required"
    assert payload.get("suggestion") == ""
    assert payload.get("reason")
    from core import credential_store

    assert credential_store.list_credentials() == []


def test_brave_key_on_auto_detect_is_recognised_verified_once_stored_and_bound(pact_rig, monkeypatch):
    with FakeProviderServer(_brave(BRAVE_KEY)) as brave:
        _point_brave_at(monkeypatch, f"{brave.url}/res/v1/web/search")
        session_id, classified = _begin_and_classify(pact_rig, BRAVE_KEY)
        assert classified["suggestion"] == "search.brave", classified
        assert brave.request_count == 0, "classification reached the network"
        status, verified = pact_rig.post("/api/intake/verify", {"session_id": session_id, "provider_id": "search.brave"})
        assert status == 200 and verified["outcome"] == "verified", verified
        status, done = pact_rig.post("/api/intake/complete", {"session_id": session_id})
        assert status == 200, done
        assert brave.request_count == 1, "completing re-sent the key instead of using the verified outcome"
    assert _stored("search.web.brave") == BRAVE_KEY
    from core.credential_intelligence.provider_registry import default_registry
    from core.credential_intelligence.store import CredentialStore
    from core.search_providers import keyed_providers

    assert CredentialStore(default_registry()).find("search.brave").status == "verified"
    assert "brave" in keyed_providers()
    assert BRAVE_KEY not in json.dumps([classified, verified, done])
    assert sweep_home_for_secret(pact_rig.home, BRAVE_KEY) == []


def test_openrouter_key_on_auto_detect_is_verified_stored_and_made_the_active_cloud_lane(pact_rig, monkeypatch):
    with FakeProviderServer(_openrouter(OPENROUTER_KEY)) as router:
        _point_openrouter_at(monkeypatch, f"{router.url}/api/v1")
        session_id, classified = _begin_and_classify(pact_rig, OPENROUTER_KEY)
        assert classified["suggestion"] == "openrouter", classified
        status, verified = pact_rig.post("/api/intake/verify", {"session_id": session_id, "provider_id": "openrouter"})
        assert status == 200 and verified["outcome"] == "verified", verified
        status, done = pact_rig.post("/api/intake/complete", {"session_id": session_id})
        assert status == 200, done
        assert router.request_count == 1
    assert _stored("llm.cloud.openrouter") == OPENROUTER_KEY
    from core import cloud_escalation_policy

    assert cloud_escalation_policy.load_policy().provider == "openrouter"


def test_a_novel_compatible_endpoint_with_an_opaque_key_verifies_at_the_entered_base_url(pact_rig, monkeypatch):
    for name in ("VOOL_CUSTOM_BASE_URL", "VOOL_REMOTE_BASE_URL", "VOOL_CUSTOM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    want = _sha(f"Bearer {NOVEL_KEY}")

    def respond(record):
        if record["path"] != "/v1/models":
            return (404, {"error": {"message": "Unknown request URL", "type": "invalid_request_error"}})
        if record["auth_sha256"] != want:
            return (401, {"error": {"message": "Incorrect API key provided.", "type": "invalid_request_error", "code": "invalid_api_key"}})
        return (200, {"object": "list", "data": [{"id": "novel-lab/chat-large", "object": "model", "created": 0, "owned_by": "novel-lab"}]})

    with FakeProviderServer(respond) as novel:
        session_id, classified = _begin_and_classify(pact_rig, NOVEL_KEY)
        assert classified["suggestion"] == "" and classified.get("reason"), classified
        status, verified = pact_rig.post("/api/intake/verify", {"session_id": session_id, "provider_id": "custom", "base_url": f"{novel.url}/v1"})
        assert status == 200 and verified["outcome"] == "verified", verified
        status, done = pact_rig.post("/api/intake/complete", {"session_id": session_id})
        assert status == 200, done
        # An entered endpoint is asked without the key first; it refused, so it was asked once with the key.
        assert [r["auth_present"] for r in novel.requests] == [False, True]
        base_url = f"{novel.url}/v1"
    assert _stored("llm.cloud.custom") == NOVEL_KEY
    from core.cloud_providers import custom_base_url

    assert custom_base_url() == base_url


def test_throttling_during_setup_stores_nothing_and_complete_does_not_retry(pact_rig, monkeypatch):
    throttled = (429, {"error": {"code": 429, "message": "Rate limit exceeded"}}, {"Retry-After": "9"})
    with FakeProviderServer([throttled]) as router:
        _point_openrouter_at(monkeypatch, f"{router.url}/api/v1")
        session_id, _ = _begin_and_classify(pact_rig, OPENROUTER_KEY)
        status, verified = pact_rig.post("/api/intake/verify", {"session_id": session_id, "provider_id": "openrouter"})
        assert status == 200 and verified["outcome"] == "rate_limited", verified
        assert verified.get("retry_after_s") == 9.0
        status, done = pact_rig.post("/api/intake/complete", {"session_id": session_id})
        assert status == 409 and done.get("error") == "not_verified", done
        assert router.request_count == 1
    assert _stored("llm.cloud.openrouter") is None


def test_a_rejected_key_during_setup_stores_nothing(pact_rig, monkeypatch):
    with FakeProviderServer(_brave("a-different-key-entirely-0123456789")) as brave:
        _point_brave_at(monkeypatch, f"{brave.url}/res/v1/web/search")
        session_id, _ = _begin_and_classify(pact_rig, BRAVE_KEY)
        status, verified = pact_rig.post("/api/intake/verify", {"session_id": session_id, "provider_id": "search.brave"})
        assert status == 200 and verified["outcome"] == "invalid", verified
        status, done = pact_rig.post("/api/intake/complete", {"session_id": session_id})
        assert status == 409, done
    assert _stored("search.web.brave") is None


def test_a_custom_endpoint_needs_a_safe_base_url_before_any_request(pact_rig):
    session_id, _ = _begin_and_classify(pact_rig, NOVEL_KEY)
    status, missing = pact_rig.post("/api/intake/verify", {"session_id": session_id, "provider_id": "custom"})
    assert status == 400 and missing.get("error") == "base_url_required", missing
    status, insecure = pact_rig.post("/api/intake/verify", {"session_id": session_id, "provider_id": "custom", "base_url": "http://example.invalid/v1"})
    assert status == 400 and insecure.get("error") == "insecure_base_url", insecure


def test_classifying_any_key_opens_no_socket(pact_rig, monkeypatch):
    import socket

    calls: list = []
    real_connect = socket.socket.connect

    def counted(self, address):
        calls.append(address)
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", counted)
    for value in (BRAVE_KEY, OPAQUE_KEY, NOVEL_KEY, OPENROUTER_KEY):
        _begin_and_classify(pact_rig, value)
    assert calls == [], f"classification made outbound connections: {calls}"


def test_school_edition_keeps_key_setup_a_school_admin_action(pact_rig, monkeypatch):
    """The intake doors now carry the Settings key form; in the school edition they refuse exactly as
    /api/settings/credentials does, so the move cannot become a way around the school admin rule."""
    from core.product_edition import reset_edition_cache

    monkeypatch.setenv("VOOL_EDITION", "school")
    reset_edition_cache()
    try:
        for path, body in (
            ("/api/intake/begin", {}),
            ("/api/intake/classify", {"session_id": "intake-x", "value": OPENROUTER_KEY}),
            ("/api/intake/verify", {"session_id": "intake-x", "provider_id": "openrouter"}),
            ("/api/intake/complete", {"session_id": "intake-x"}),
        ):
            status, payload = pact_rig.post(path, body)
            assert status == 403 and payload.get("error") == "school_admin_only", (path, status, payload)
    finally:
        monkeypatch.delenv("VOOL_EDITION", raising=False)
        reset_edition_cache()
    assert _stored("llm.cloud.openrouter") is None
