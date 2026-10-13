"""A custom OpenAI-compatible endpoint is usable whatever its auth: Bearer, a named header, or none.

THE LAWS UNDER TEST
-------------------
* **The operator names where the key goes, and only for an endpoint they entered.** A placement is
  Bearer, ``header:<Name>`` (a valid, non-reserved header) or ``none``; a fixed provider keeps the
  placement its API documents.
* **The key travels exactly there, and nowhere else.** Verification sends the bare key in the named
  header and no ``Authorization``; an open endpoint receives no key at all.
* **The placement belongs to its pair.** It is committed in the binding row with the endpoint and
  key, and a row left by another endpoint never decides where this endpoint's key goes.
* **An endpoint that cannot confirm a key is usable only by explicit choice.** ``asserted`` stores
  an ``unverified`` pair only after this session's verification ended ``public_endpoint`` or
  ``endpoint_not_found``; a rejected key is never stored that way.
* **A key in a custom header never follows a redirect to another origin.**
"""
from __future__ import annotations

import pytest

from tests._credential_intelligence_support import FakeProviderServer

MODELS = {"object": "list", "data": [{"id": "house/model-7b", "object": "model"}]}
KEY = "nv1_custom-endpoint-test-key-0042"


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    for name in ("VOOL_CUSTOM_API_KEY", "VOOL_CUSTOM_BASE_URL", "VOOL_REMOTE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    import core.runtime_paths as runtime_paths

    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield tmp_path
    runtime_paths.configure_runtime_home(None)


def _custom_at(url: str, auth: str):
    from core.credential_intelligence.provider_registry import default_registry

    return default_registry().get("custom").with_base_url(f"{url}/v1").with_custom_auth(auth)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("", "bearer"),
        ("bearer", "bearer"),
        ("none", "none"),
        ("header:api-key", "header:api-key"),
        ("header:X-Api-Key", "header:X-Api-Key"),
        ("header:Authorization", "bearer"),
        ("header:Host", ""),
        ("header:Cookie", ""),
        ("header:api key", ""),
        ("header:a\r\nX-Injected: 1", ""),
        ("header:", ""),
        ("query:key", ""),
    ],
)
def test_placement_is_validated(given, expected):
    from core.cloud_providers import normalize_custom_auth

    assert normalize_custom_auth(given) == expected


def test_fixed_provider_placement_is_never_overridden():
    from core.credential_intelligence.provider_registry import default_registry

    anthropic = default_registry().get("anthropic")
    assert anthropic.with_custom_auth("none").auth_name == "X-Api-Key"


def test_header_placement_sends_the_key_only_in_that_header(home):
    from core.credential_intelligence.verification import verify_provider_credential

    def answer(record):
        return (200, MODELS) if "api-key" in record["header_names"] else (401, {"error": {"code": "invalid_api_key"}})

    with FakeProviderServer(responses=answer) as server:
        outcome = verify_provider_credential(KEY, _custom_at(server.url, "header:api-key"))
    assert outcome.status == "verified", outcome
    keyed = [r for r in server.requests if "api-key" in r["header_names"]]
    assert keyed and all(not r["auth_present"] for r in server.requests)


def test_open_endpoint_is_verified_without_any_key(home):
    from core.credential_intelligence.verification import verify_provider_credential

    with FakeProviderServer(responses=[(200, MODELS)]) as server:
        outcome = verify_provider_credential("no-key", _custom_at(server.url, "none"))
    assert outcome.status == "verified"
    assert len(server.requests) == 1 and not server.requests[0]["auth_present"]


def test_open_placement_on_a_key_demanding_endpoint_is_named_not_called_a_bad_key(home):
    from core.credential_intelligence.verification import verify_provider_credential

    with FakeProviderServer(responses=[(401, {"error": {"code": "invalid_api_key"}})]) as server:
        outcome = verify_provider_credential("no-key", _custom_at(server.url, "none"))
    assert outcome.status == "unauthorized"
    assert "asks for a key" in outcome.detail


def _intake(provider_id: str, base_url: str, auth: str, value: str = KEY):
    from core.command_registry.groups import first_run as fr

    class In:
        pass

    sid = fr._intake_begin(None, None).data["session_id"]
    classify = In(); classify.session_id, classify.value = sid, value
    fr._intake_classify(classify, None)
    verify = In(); verify.session_id, verify.provider_id, verify.base_url, verify.auth = sid, provider_id, base_url, auth
    return fr, sid, fr._intake_verify(verify, None)


def _complete(fr, sid, persist):
    class In:
        pass

    done = In(); done.session_id, done.persist = sid, persist
    return fr._intake_complete(done, None)


def test_placement_commits_with_its_pair_and_only_applies_to_that_endpoint(home, monkeypatch):
    from core.cloud_providers import committed_custom_auth

    monkeypatch.setattr("core.web.api.registry_authorities.bind_saved_cloud_key", lambda _pid: None)
    with FakeProviderServer(responses=lambda r: (200, MODELS) if "api-key" in r["header_names"] else (401, {})) as server:
        fr, sid, verified = _intake("custom", f"{server.url}/v1", "header:api-key")
        assert verified.data["outcome"] == "verified"
        assert _complete(fr, sid, "store").data["binding"]["status"] == "verified"
        assert committed_custom_auth(f"{server.url}/v1") == "header:api-key"
        assert committed_custom_auth("https://another.example/v1") == "bearer"
        from core.credential_intelligence.provider_registry import default_registry

        live = default_registry().get("custom")
        assert (live.auth_style, live.auth_name) == ("header", "api-key")


def test_auth_is_refused_for_a_fixed_provider(home):
    _fr, _sid, refused = _intake("openai", "", "header:api-key")
    assert refused.detail["error"] == "auth_not_configurable"


def test_unconfirmable_endpoint_is_stored_unverified_only_by_explicit_choice(home, monkeypatch):
    monkeypatch.setattr("core.web.api.registry_authorities.bind_saved_cloud_key", lambda _pid: None)
    with FakeProviderServer(responses=[(200, MODELS)]) as server:
        fr, sid, verified = _intake("custom", f"{server.url}/v1", "bearer")
        assert verified.data["outcome"] == "public_endpoint"
        assert verified.data["can_use_unconfirmed"] is True
        refused = _complete(fr, sid, "store")
        assert getattr(refused, "detail", {}).get("error") == "not_verified"
        fr, sid, _ = _intake("custom", f"{server.url}/v1", "bearer")
        stored = _complete(fr, sid, "asserted")
        assert stored.data["asserted"] is True
        assert stored.data["binding"]["status"] == "unverified"
    assert not any(r["auth_present"] for r in server.requests), "the key was sent to a public endpoint"


def test_rejected_key_can_never_be_stored_as_asserted(home):
    with FakeProviderServer(responses=[(401, {}), (401, {"error": {"code": "invalid_api_key"}})]) as server:
        fr, sid, verified = _intake("custom", f"{server.url}/v1", "bearer")
        assert verified.data["outcome"] == "invalid"
        assert verified.data["can_use_unconfirmed"] is False
        refused = _complete(fr, sid, "asserted")
    assert refused.detail["error"] == "not_assertable"


def test_custom_key_header_does_not_follow_a_cross_origin_redirect():
    """A custom endpoint's key header (``x-api-key``) never rides a redirect to another origin.

    A loopback provider follows no redirect at all, so end to end the key reaches only the origin
    it was addressed to. A cloud endpoint's redirect is followed, and the hop requests builds for it
    goes through this session's ``rebuild_auth``: the key header is dropped for another origin and
    kept for the same one."""
    import requests

    from core import provider_http

    with FakeProviderServer(responses=[(200, MODELS)]) as target:
        with FakeProviderServer(responses=[(307, b"", {"Location": f"{target.url}/v1/models"})]) as origin:
            with pytest.raises(requests.exceptions.TooManyRedirects):
                provider_http.get(f"{origin.url}/v1/models", headers={"x-api-key": KEY}, timeout=10)
        assert "x-api-key" in origin.requests[0]["header_names"]
        assert target.requests == []

    def hop(to: str) -> requests.PreparedRequest:
        first = requests.Request("GET", "https://models.example.test/v1/models",
                                 headers={"x-api-key": KEY, "Accept": "application/json"}).prepare()
        answer = requests.Response()
        answer.status_code, answer.request = 307, first
        nxt = requests.Request("GET", to, headers={"x-api-key": KEY, "Accept": "application/json"}).prepare()
        with provider_http._KeySafeSession() as session:
            session.trust_env = False
            session.rebuild_auth(nxt, answer)
        return nxt

    elsewhere = hop("https://elsewhere.example.test/v1/models")
    assert "x-api-key" not in elsewhere.headers and elsewhere.headers["Accept"] == "application/json"
    assert hop("https://models.example.test/v2/models").headers["x-api-key"] == KEY


@pytest.mark.parametrize(
    ("model_id", "accepted"),
    [
        ("accounts/fireworks/models/llama-v3p1-70b-instruct", True),
        ("@cf/meta/llama-3.1-8b-instruct", True),
        ("Qwen/Qwen2.5-7B-Instruct@main", True),
        ("my-finetune:latest", True),
        ("openai/gpt-4.1-mini", True),
        ("../etc/passwd", False),
        ("a/../b", False),
        ("a/b/c/d/e/f", False),
        ("not a model", False),
    ],
)
def test_model_id_rule_accepts_provider_paths_and_refuses_traversal(model_id, accepted):
    from core.cloud_model_control import CLOUD_MODEL_ID_RE

    assert bool(CLOUD_MODEL_ID_RE.match(model_id)) is accepted


def test_a_placement_changed_after_verification_is_probed_again_before_storing(home, monkeypatch):
    """Verify with Bearer, then preview the SAME endpoint with a header placement, then complete: the
    Bearer outcome must not be reused for the header placement (audit T-0045 P1)."""
    from core.command_registry.groups import first_run as fr
    from core.credential_intelligence.binding import load_index

    monkeypatch.setattr("core.web.api.registry_authorities.bind_saved_cloud_key", lambda _pid: None)

    class In:
        pass

    with FakeProviderServer(responses=lambda r: (200, MODELS) if r["auth_present"] else (401, {})) as server:
        _fr, sid, verified = _intake("custom", f"{server.url}/v1", "bearer")
        assert verified.data["outcome"] == "verified"
        before = len(server.requests)
        preview = In(); preview.session_id, preview.provider_id, preview.base_url, preview.auth = (
            sid, "custom", f"{server.url}/v1", "header:x-api-key")
        fr._intake_preview(preview, None)
        stored = _complete(fr, sid, "store")
        assert len(server.requests) > before, "the header placement was stored without being probed"
    assert getattr(stored, "detail", {}).get("error") == "not_verified"
    row = load_index().get("custom") if load_index() else None
    assert not (isinstance(row, dict) and row.get("status") == "verified")


@pytest.mark.parametrize("placement", ["header:x-api-key", "none"])
def test_save_for_later_keeps_the_placement_through_retry(home, monkeypatch, placement):
    """Preview with a placement, save for later, retry: the retry verifies with THAT placement and
    promotes, and the promoted binding keeps it (audit T-0045 P2)."""
    from core.cloud_providers import committed_custom_auth
    from core.command_registry.groups import first_run as fr

    monkeypatch.setattr("core.web.api.registry_authorities.bind_saved_cloud_key", lambda _pid: None)

    class In:
        pass

    def answer(record):
        if placement == "none":
            return 200, MODELS
        return (200, MODELS) if "x-api-key" in record["header_names"] else (401, {})

    with FakeProviderServer(responses=answer) as server:
        sid = fr._intake_begin(None, None).data["session_id"]
        classify = In(); classify.session_id, classify.value = sid, "no-key" if placement == "none" else KEY
        fr._intake_classify(classify, None)
        preview = In(); preview.session_id, preview.provider_id, preview.base_url, preview.auth = (
            sid, "custom", f"{server.url}/v1", placement)
        fr._intake_preview(preview, None)
        saved = _complete(fr, sid, "later")
        assert saved.data["quarantined"] is True
        retry = In(); retry.provider_id = "custom"
        result = fr._intake_quarantine_retry(retry, None)
        assert result.data["promoted"] is True, result.data
        assert not any(r["auth_present"] for r in server.requests), "a Bearer header was sent"
        assert committed_custom_auth(f"{server.url}/v1") == placement


# ── a verification answers only for the key and destination it was sent with (re-audit T-0045 P1) ──

GOOD = "nv1_good-disposable-0123456789"
BAD = "nv1_bad-disposable-0123456789"


def _bearer(value: str) -> str:
    import hashlib

    return hashlib.sha256(f"Bearer {value}".encode()).hexdigest()


class _Held:
    """Answers the first KEYED request only once released, so a test can change the session mid-flight.
    (A custom endpoint is first asked without a key, to tell a public model list from a keyed one.)"""

    def __init__(self, first, rest):
        import threading

        self.entered, self.release = threading.Event(), threading.Event()
        self.first, self.rest = first, rest

    def __call__(self, record):
        if record["auth_present"] and not self.entered.is_set():
            self.entered.set()
            assert self.release.wait(12)
            return self.first(record)
        return self.rest(record)


def _verify_in_background(fr, sid, base_url, auth="bearer"):
    import threading

    class In:
        pass

    rows: list = []
    verify = In(); verify.session_id, verify.provider_id, verify.base_url, verify.auth = sid, "custom", base_url, auth
    thread = threading.Thread(target=lambda: rows.append(fr._intake_verify(verify, None)))
    thread.start()
    return thread, rows


def _paste(fr, sid, value):
    class In:
        pass

    classify = In(); classify.session_id, classify.value = sid, value
    fr._intake_classify(classify, None)


def _answer_by_key(record):
    return (200, MODELS) if record["auth_sha256"] == _bearer(GOOD) else (401, {"error": {"code": "invalid_api_key"}})


def _keys_sent(server) -> list[str]:
    return [r["auth_sha256"] for r in server.requests if r["auth_present"]]


def test_a_key_pasted_while_another_is_verified_is_never_stored_as_verified(home, monkeypatch):
    from core.command_registry.groups import first_run as fr
    from core.credential_intelligence.binding import load_index

    monkeypatch.setattr("core.web.api.registry_authorities.bind_saved_cloud_key", lambda _pid: None)
    held = _Held(_answer_by_key, _answer_by_key)
    with FakeProviderServer(responses=held) as server:
        sid = fr._intake_begin(None, None).data["session_id"]
        _paste(fr, sid, GOOD)
        thread, rows = _verify_in_background(fr, sid, f"{server.url}/v1")
        try:
            assert held.entered.wait(12)
            _paste(fr, sid, BAD)  # the same session takes a new key while GOOD is still being checked
        finally:
            held.release.set()
            thread.join(15)
        assert not thread.is_alive()
        assert getattr(rows[0], "detail", {}).get("error") == "intake_changed", rows
        # GOOD's answer recorded nothing: completing now stores nothing
        assert getattr(_complete(fr, sid, "store"), "detail", {}).get("error") == "provider_mismatch"
        # checked again, BAD itself is asked, and refused
        sid = fr._intake_begin(None, None).data["session_id"]
        _paste(fr, sid, BAD)
        thread, rows = _verify_in_background(fr, sid, f"{server.url}/v1")
        thread.join(15)
        assert rows[0].data["outcome"] != "verified"
        stored = _complete(fr, sid, "store")
        assert _keys_sent(server)[0] == _bearer(GOOD) and set(_keys_sent(server)[1:]) == {_bearer(BAD)}
    assert getattr(stored, "detail", {}).get("error") == "not_verified"
    row = (load_index() or {}).get("custom")
    assert not (isinstance(row, dict) and row.get("status") == "verified")


def test_an_old_refusal_does_not_judge_the_key_pasted_after_it(home, monkeypatch):
    from core.command_registry.groups import first_run as fr

    monkeypatch.setattr("core.web.api.registry_authorities.bind_saved_cloud_key", lambda _pid: None)
    held = _Held(_answer_by_key, _answer_by_key)
    with FakeProviderServer(responses=held) as server:
        sid = fr._intake_begin(None, None).data["session_id"]
        _paste(fr, sid, BAD)
        thread, rows = _verify_in_background(fr, sid, f"{server.url}/v1")
        try:
            assert held.entered.wait(12)
            _paste(fr, sid, GOOD)
        finally:
            held.release.set()
            thread.join(15)
        assert getattr(rows[0], "detail", {}).get("error") == "intake_changed", rows
        # BAD's refusal arrived after GOOD was pasted: it judged nothing; GOOD verifies on its own check
        thread, rows = _verify_in_background(fr, sid, f"{server.url}/v1")
        thread.join(15)
        assert rows[0].data["outcome"] == "verified"
        stored = _complete(fr, sid, "store")
    assert stored.data["binding"]["status"] == "verified"


def test_a_destination_changed_while_verifying_is_verified_where_it_now_points(home, monkeypatch):
    from core.command_registry.groups import first_run as fr

    class In:
        pass

    monkeypatch.setattr("core.web.api.registry_authorities.bind_saved_cloud_key", lambda _pid: None)
    keyed_only = lambda r: (200, MODELS) if r["auth_present"] else (401, {})  # noqa: E731
    held = _Held(keyed_only, keyed_only)
    with FakeProviderServer(responses=held) as first, \
            FakeProviderServer(responses=lambda _r: (401, {"error": {"code": "invalid_api_key"}})) as second:
        sid = fr._intake_begin(None, None).data["session_id"]
        _paste(fr, sid, GOOD)
        thread, rows = _verify_in_background(fr, sid, f"{first.url}/v1")
        try:
            assert held.entered.wait(12)
            preview = In(); preview.session_id, preview.provider_id, preview.base_url, preview.auth = (
                sid, "custom", f"{second.url}/v1", "bearer")
            fr._intake_preview(preview, None)
        finally:
            held.release.set()
            thread.join(15)
        assert getattr(rows[0], "detail", {}).get("error") == "intake_changed", rows
        stored = _complete(fr, sid, "store")
        assert len(_keys_sent(second)) == 1, "the new destination was stored without being asked"
    assert getattr(stored, "detail", {}).get("error") == "not_verified"


def test_an_unchanged_session_still_reuses_its_one_verification(home, monkeypatch):
    from core.command_registry.groups import first_run as fr

    monkeypatch.setattr("core.web.api.registry_authorities.bind_saved_cloud_key", lambda _pid: None)
    with FakeProviderServer(responses=_answer_by_key) as server:
        fr_, sid, verified = _intake("custom", f"{server.url}/v1", "bearer", value=GOOD)
        assert verified.data["outcome"] == "verified"
        stored = _complete(fr_, sid, "store")
        assert _keys_sent(server) == [_bearer(GOOD)]
    assert stored.data["binding"]["status"] == "verified"
