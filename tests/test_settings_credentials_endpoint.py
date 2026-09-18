"""Settings surface backend: the /api/settings/credentials endpoint sets/lists/deletes the user's
own cloud API key (BYOK). The secret value is sealed at rest and is NEVER returned by any GET. Only
the UI-managed credential name is writable; the request shape is validated like the other POSTs."""
from __future__ import annotations

import pytest

from core import runtime_paths
from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post

_OPENROUTER = "llm.cloud.openrouter"
_SECRET = "sk-or-v1-do-not-leak-this-value"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    # Own VOOL_HOME so the credential file is a throwaway, isolated from the real store.
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _rt() -> RuntimeServices:
    return RuntimeServices(display_name="VOOL")


def _post(body, headers=None):
    return dispatch_post(
        path="/api/settings/credentials",
        body=body,
        headers=headers if headers is not None else {"content-type": "application/json"},
        runtime=_rt(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
    )


def _get():
    return dispatch_get(path="/api/settings/credentials", query={}, runtime=_rt(), model_name="vool")


def _body(response):
    import json

    return json.loads(response.body.decode("utf-8"))


def test_set_list_delete_roundtrip_without_leaking_the_value() -> None:
    # Initially empty.
    assert _body(_get())["credentials"] == []
    # Set the key.
    set_res = _post({"name": _OPENROUTER, "value": _SECRET, "label": "OpenRouter"})
    assert set_res.status == 200
    assert _body(set_res)["connected"] is True
    # It shows as connected in the list, with its label -- but never the secret value.
    listed = _body(_get())["credentials"]
    assert [c["name"] for c in listed] == [_OPENROUTER]
    assert listed[0]["label"] == "OpenRouter"
    assert _SECRET not in _get().body.decode("utf-8")
    # Delete it.
    del_res = _post({"name": _OPENROUTER, "delete": True})
    assert del_res.status == 200 and _body(del_res)["connected"] is False
    assert _body(_get())["credentials"] == []


def test_setting_the_key_enables_the_cloud_lane_presence_check() -> None:
    # The name matches what the runtime reads, so a connected key is visible to the cloud-key probe.
    from core import credential_store

    _post({"name": _OPENROUTER, "value": _SECRET})
    assert credential_store.has_credential(_OPENROUTER) is True
    assert credential_store.get_credential(_OPENROUTER) == _SECRET


def test_rejects_unsupported_name_and_missing_value() -> None:
    assert _post({"name": "some.other.secret", "value": "x"}).status == 400
    assert _post({"name": _OPENROUTER}).status == 400  # no value, no delete
    assert _post({"name": _OPENROUTER, "value": "   "}).status == 400  # blank
    assert _post({"name": _OPENROUTER, "value": "x", "junk": 1}).status == 400  # unknown field


def test_rejects_non_json_content_type() -> None:
    res = _post({"name": _OPENROUTER, "value": _SECRET}, headers={"content-type": "text/plain"})
    assert res.status == 415


def test_cloud_keys_maps_stored_slots_to_providers(monkeypatch) -> None:
    # /api/cloud/keys lists which providers are keyed (for the Settings keys panel), never a secret.
    import json as _json

    from core import credential_store, media_tools

    monkeypatch.setattr(credential_store, "list_credentials",
                        lambda: [{"name": "llm.cloud.openrouter", "label": "OpenRouter"}])
    monkeypatch.setattr(media_tools, "has_image_service", lambda *a, **k: True)
    res = dispatch_get(path="/api/cloud/keys", query={}, runtime=_rt(), model_name="vool")
    keys = _json.loads(res.body.decode("utf-8"))["keys"]
    provs = {k["provider"] for k in keys}
    assert "openrouter" in provs and "fal" in provs
    assert next(k for k in keys if k["provider"] == "fal")["kind"] == "fal"
    assert _SECRET not in res.body.decode("utf-8")
