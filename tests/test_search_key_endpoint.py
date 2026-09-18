"""The paste-a-search-key surface, driven through the real HTTP handlers.

Same contract as the cloud-key endpoint it shares a path with: the secret is sealed at rest, no GET
ever returns it, and only a slot from the closed provider table is writable. What is additionally
pinned here is that adding search keys did not weaken the cloud path or open the whitelist to
arbitrary names -- the two lanes share one endpoint and must not have become one lane.
"""
from __future__ import annotations

import json

import pytest

from core import runtime_paths
from core.search_providers import SearchProviderConfig
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post

_FAKE = SearchProviderConfig(
    provider_id="fakesearch",
    label="Fake Search",
    search_url="https://api.fake.test/v1/web/search",
    auth_style="header",
    auth_name="X-Test-Token",
    results_path=("results",),
    key_prefixes=("fks-",),
    signup_url="https://fake.test/signup",
    free_tier="1k/month",
)
_SLOT = "search.web.fakesearch"
_SECRET = "fks-do-not-leak-this-value"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    monkeypatch.setattr("core.search_providers.SEARCH_PROVIDERS", {"fakesearch": _FAKE})
    yield
    runtime_paths.configure_runtime_home(None)


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _post(path, body, headers=None):
    return dispatch_post(
        path=path,
        body=body,
        headers=headers if headers is not None else {"content-type": "application/json"},
        runtime=_rt(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
    )


def _get(path, query=None):
    return dispatch_get(path=path, query=query or {}, runtime=_rt(), model_name="vool")


def _body(response):
    return json.loads(response.body.decode("utf-8"))


def test_paste_save_list_delete_roundtrip_without_leaking_the_value():
    assert _body(_get("/api/settings/credentials"))["credentials"] == []

    saved = _post("/api/settings/credentials", {"provider": "fakesearch", "value": _SECRET})
    assert saved.status == 200
    assert _body(saved)["connected"] is True
    assert _body(saved)["provider"] == "fakesearch"

    listed = _body(_get("/api/settings/credentials"))["credentials"]
    assert [row["name"] for row in listed] == [_SLOT]
    assert _SECRET not in json.dumps(listed)          # the value is never returned by a GET

    catalog = _body(_get("/api/search/providers"))["providers"]
    assert [(p["provider"], p["connected"]) for p in catalog] == [("fakesearch", True)]
    assert _SECRET not in json.dumps(catalog)

    removed = _post("/api/settings/credentials", {"provider": "fakesearch", "delete": True})
    assert _body(removed)["removed"] is True
    assert _body(_get("/api/settings/credentials"))["credentials"] == []


def test_a_decorated_paste_is_stored_as_the_bare_key():
    """The user pasted the key with quotes; what gets sent to the provider must be the key."""
    from core.credential_store import get_credential

    _post("/api/settings/credentials", {"provider": "fakesearch", "value": f'"Bearer {_SECRET}"'})
    assert get_credential(_SLOT) == _SECRET


def test_an_unknown_search_provider_is_refused():
    res = _post("/api/settings/credentials", {"provider": "not-a-provider", "value": "x"})
    assert res.status == 400


def test_an_arbitrary_credential_name_is_still_refused():
    """The closed whitelist must not have been loosened by adding a second table to it."""
    res = _post("/api/settings/credentials", {"name": "search.web.../etc/passwd", "value": "x"})
    assert res.status == 400
    res = _post("/api/settings/credentials", {"name": "anything.at.all", "value": "x"})
    assert res.status == 400


def test_name_and_provider_must_agree():
    res = _post("/api/settings/credentials", {"provider": "fakesearch", "name": "search.web.other", "value": "x"})
    assert res.status == 400


@pytest.mark.parametrize("bad", ["", "   ", None, 42, {"nested": 1}])
def test_a_missing_or_non_string_value_is_refused(bad):
    res = _post("/api/settings/credentials", {"provider": "fakesearch", "value": bad})
    assert res.status == 400


def test_an_oversized_value_is_refused():
    res = _post("/api/settings/credentials", {"provider": "fakesearch", "value": "f" * 9000})
    assert res.status == 413


def test_a_value_that_is_only_decoration_is_refused():
    """`"Bearer "` normalizes to nothing; storing it would create a permanently broken provider."""
    res = _post("/api/settings/credentials", {"provider": "fakesearch", "value": '"Bearer "'})
    assert res.status == 400


def test_the_cloud_lane_still_works_through_the_same_endpoint():
    """The search branch returns early; the cloud path behind it must be untouched."""
    res = _post("/api/settings/credentials", {"name": "llm.cloud.openrouter", "value": "sk-or-v1-x", "label": "OpenRouter"})
    assert res.status == 200
    assert _body(res)["provider"] == "openrouter"
    names = [row["name"] for row in _body(_get("/api/settings/credentials"))["credentials"]]
    assert "llm.cloud.openrouter" in names


def test_detect_endpoint_labels_a_key_without_storing_it():
    res = _post("/api/search/detect", {"value": f'"{_SECRET}"'})
    assert _body(res) == {"provider": "fakesearch", "confidence": "high", "candidates": []}
    # nothing was stored by asking
    assert _body(_get("/api/settings/credentials"))["credentials"] == []


def test_detect_never_echoes_the_key_back():
    res = _post("/api/search/detect", {"value": _SECRET})
    assert _SECRET not in res.body.decode("utf-8")


def test_the_test_endpoint_requires_a_provider():
    assert _post("/api/search/test", {}).status == 400


def test_the_test_endpoint_reports_no_key_rather_than_calling_out(monkeypatch):
    def _explode(req, timeout):
        raise AssertionError("probed a provider with no key stored")

    monkeypatch.setattr("tools.web.search_api_client.open_remote", _explode)
    res = _post("/api/search/test", {"provider": "fakesearch"})
    assert _body(res)["state"] == "no_key"


def test_unknown_fields_and_wrong_content_type_are_refused():
    assert _post("/api/settings/credentials", {"provider": "fakesearch", "value": "x", "bogus": 1}).status == 400
    assert _post("/api/search/detect", {"value": "x"}, headers={"content-type": "text/plain"}).status == 415
    assert _post("/api/search/test", {"provider": "fakesearch", "extra": 1}).status == 400
