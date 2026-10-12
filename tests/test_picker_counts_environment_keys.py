"""A provider keyed through the environment is a connected provider in the model picker.

The server already routes, registers and tests a key set in the environment (OPENROUTER_API_KEY and
the other aliases in ``core.cloud_providers``), but the chat page decided "connected" from the
stored credential slots alone, so the picker said "Connect a provider key in Settings" and offered
no cloud model for a lane that was working. The provider catalogue now says, by presence only,
which providers hold an environment key, and the page counts them.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from core.vool_chat_page import render_vool_chat_html
from tests import served_browser

HTML = render_vool_chat_html()
ENV_KEY = "sk-or-v1-" + "e" * 64


def _providers(monkeypatch, env: dict[str, str]) -> dict[str, dict]:
    from core.web.api.service import dispatch_get

    for name in ("OPENROUTER_API_KEY", "VOOL_OPENROUTER_API_KEY", "VOOL_CUSTOM_API_KEY",
                 "VOOL_CUSTOM_BASE_URL", "VOOL_REMOTE_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    response = dispatch_get(path="/api/cloud/providers", query={}, runtime=SimpleNamespace(runtime_version_stamp={}), model_name="vool")
    body = response.body if isinstance(response.body, (bytes, str)) else json.dumps(response.body)
    text = body.decode() if isinstance(body, bytes) else body
    assert ENV_KEY not in text, "the catalogue echoed key material"
    return {row["id"]: row for row in json.loads(text)["providers"]}


def test_catalogue_reports_environment_keys_by_presence_only(monkeypatch):
    rows = _providers(monkeypatch, {"OPENROUTER_API_KEY": ENV_KEY})
    assert rows["openrouter"]["key_from_environment"] is True
    assert rows["openai"]["key_from_environment"] is False


def test_custom_needs_its_base_url_in_the_environment_too(monkeypatch):
    rows = _providers(monkeypatch, {"VOOL_CUSTOM_API_KEY": ENV_KEY})
    assert rows["custom"]["key_from_environment"] is False
    rows = _providers(monkeypatch, {"VOOL_CUSTOM_API_KEY": ENV_KEY, "VOOL_CUSTOM_BASE_URL": "https://llm.example/v1"})
    assert rows["custom"]["key_from_environment"] is True


def _route(route):
    request = route.request
    path = urlsplit(request.url).path
    if request.resource_type == "document":
        route.fulfill(status=200, content_type="text/html", body=HTML)
    elif path == "/api/cloud/providers":
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"providers": [
            {"id": "openrouter", "label": "OpenRouter", "key_from_environment": True},
            {"id": "openai", "label": "OpenAI (GPT)", "key_from_environment": False}]}))
    elif path == "/api/settings/credentials":
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"credentials": []}))
    elif path == "/api/cloud/models":
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "provider": "openrouter", "label": "OpenRouter", "auto_free_model": "auto",
            "models": [{"id": "vendor/env-model", "name": "Env Model", "free": True}]}))
    else:
        route.fulfill(status=200, content_type="application/json", body="{}")


def test_picker_offers_cloud_models_for_an_environment_key():
    ctx, browser = served_browser.launch_chromium()
    try:
        page = browser.new_page()
        page.route("**/*", _route)
        page.goto("http://vool.test/chat")
        page.wait_for_timeout(600)
        page.evaluate("() => document.getElementById('modelBtn').click()")
        page.wait_for_timeout(400)
        state = page.evaluate("""() => ({
            connected: cloudKeyConnected,
            note: (document.querySelector('#modelPop .pop-note') || {}).textContent || '',
            rows: [...document.querySelectorAll('#modelPop .cloud-dyn.pop-item')].map((n) => n.getAttribute('data-model')),
        })""")
    finally:
        browser.close()
        ctx.stop()
    assert state["connected"] is True, state
    assert "Connect a provider key" not in state["note"], state
    assert "vendor/env-model" in state["rows"], state
