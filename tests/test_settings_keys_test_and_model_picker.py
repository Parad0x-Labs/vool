"""Settings → API Keys can TEST a stored key, and Settings → Models & Providers can PICK and PIN a model.

Both controls existed in the old in-chat settings modal and were lost in the Settings redesign; the operator found
the gap on 2026-09-07 ("api key added but models not listed? … don't see the TEST button"). The served proof runs a
real daemon on a fresh home with a two-model OpenRouter catalogue seeded into its cache and a fake key stored the way
the page stores it; nothing here contacts a provider (the network seal refuses it), which is exactly the "could not
reach" branch the Test button must name instead of pretending.
"""
from __future__ import annotations

import datetime
import json
import os
import urllib.request

import pytest

import tests._reader_served_rig as rig
from tests.served_browser import launch_chromium

FREE_ID = "test/free-one:free"
PAID_ID = "test/paid-one"


def _catalogue_payload() -> dict:
    return {"data": [
        {"id": FREE_ID, "name": "Test Free One", "context_length": 8192, "pricing": {"prompt": "0", "completion": "0"},
         "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}, "top_provider": {"max_completion_tokens": 4096}},
        {"id": PAID_ID, "name": "Test Paid One", "context_length": 32768, "pricing": {"prompt": "0.000001", "completion": "0.000002"},
         "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}, "top_provider": {"max_completion_tokens": 8192}},
    ]}


def test_the_settings_page_carries_the_test_control_and_the_model_picker():
    from core.vool_settings_page import render_vool_settings_html

    html = render_vool_settings_html()
    for token in ("/api/cloud/test", "/api/search/test", "key-test", "/api/cloud/models?provider=", "model-picker", "model-pin", "confirm_paid"):
        assert token in html, token


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    home = tmp_path_factory.mktemp("settings-keys") / "home"
    (home / "data").mkdir(parents=True)
    (home / "data" / "openrouter_models_cache.json").write_text(json.dumps({"fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "payload": _catalogue_payload()}))
    with rig.CapturingProvider(default="ok") as provider:
        daemon = rig.ServedDaemon(home, provider=provider, env_extra={"PLAYWRIGHT_BROWSERS_PATH": os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")})
        try:
            daemon.start(timeout=180)
        except RuntimeError as exc:
            daemon.stop()
            pytest.skip(f"served daemon could not boot here: {exc}")
        manager, browser = launch_chromium()
        try:
            yield daemon, browser
        finally:
            browser.close()
            manager.stop()
            daemon.stop()


def _post(base: str, path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "Origin": base}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base + path, timeout=30) as r:
        return json.loads(r.read())


def test_a_stored_key_has_a_test_button_that_names_what_the_provider_answered(served):
    daemon, browser = served
    status, body = _post(daemon.base_url, "/api/settings/credentials", {"provider": "openrouter", "value": "sk-or-v1-" + "0123456789abcdef" * 4})
    assert status == 200 and not body.get("error"), (status, body)
    page = browser.new_page()
    page.goto(f"{daemon.base_url}/settings#keys", wait_until="networkidle")
    button = page.wait_for_selector("button.key-test[data-provider='openrouter']", timeout=20000)
    button.click()
    page.wait_for_function("() => { const s = document.querySelector('.key-test-state[data-provider=\"openrouter\"]'); return s && /verified|rejected the key|Could not reach/.test(s.textContent); }", timeout=30000)
    text = page.inner_text(".key-test-state[data-provider='openrouter']")
    assert "Test failed" not in text and "openrouter" in text.lower(), text
    assert any(phrase in text for phrase in ("Connection verified", "rejected the key", "Could not reach")), text
    page.close()


def test_models_can_be_picked_and_pinned_from_settings_including_the_paid_handshake(served):
    daemon, browser = served
    page = browser.new_page()
    page.goto(f"{daemon.base_url}/settings#models", wait_until="networkidle")
    page.wait_for_selector(".model-picker button[value='" + FREE_ID + "']", state="attached", timeout=20000)
    labels = page.evaluate("() => Array.from(document.querySelectorAll('.model-picker button')).map(o => o.textContent)")
    assert any("Test Free One" in l and "free" in l for l in labels) and any("Test Paid One" in l and "paid" in l for l in labels), labels
    page.click(".model-picker button[value=\"" + FREE_ID + "\"]")
    page.click("button.model-pin")
    page.wait_for_function("() => /Pinned/.test((document.querySelector('.model-pin-state') || {}).textContent || '')", timeout=20000)
    assert _get(daemon.base_url, "/api/cloud/model").get("model") == FREE_ID, page.inner_text(".model-pin-state")
    # the paid one goes through the server's 409 handshake; the page asks, the person confirms
    page.wait_for_selector(".model-picker button[value='" + PAID_ID + "']", state="attached", timeout=20000)
    page.once("dialog", lambda d: d.accept())
    page.click(".model-picker button[value=\"" + PAID_ID + "\"]")
    page.click("button.model-pin")
    page.wait_for_function("() => /Pinned|Not pinned/.test((document.querySelector('.model-pin-state') || {}).textContent || '')", timeout=20000)
    current = _get(daemon.base_url, "/api/cloud/model")
    assert current.get("model") == PAID_ID, (current, page.inner_text(".model-pin-state"))
    page.click(".model-picker button[value=auto]")
    page.click("button.model-pin")
    page.wait_for_function("() => /cleared/.test((document.querySelector('.model-pin-state') || {}).textContent || '')", timeout=20000)
    assert _get(daemon.base_url, "/api/cloud/model").get("model") in ("", None, "auto"), _get(daemon.base_url, "/api/cloud/model")
    page.close()
