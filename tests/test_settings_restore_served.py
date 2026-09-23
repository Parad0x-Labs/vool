"""Settings surfaces the redesign dropped, restored in /settings and driven in real Chromium.

The legacy in-chat settings modal (`#settingsOverlay`) is unreachable -- nothing calls its opener --
and the controls it carried existed nowhere else: memory Pause/Resume and Export, per-item
Forget / Restore previous / Move scope on stored profile items, the session-bundle export and
import, the memory browser and privacy disclosure, the /web0 and /trace links, the custom
endpoint's base URL, and the Auto-fallback free-model choice. Each test here drives the restored
control on the served page against a real daemon on a fresh home and reads the outcome back from
the owning authority, never from the page's own words.

Nothing here contacts a provider: the daemon runs on a scripted loopback provider and the repo's
network seal refuses everything else.
"""
from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import urllib.error
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


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    home = tmp_path_factory.mktemp("settings-restore") / "home"
    (home / "data").mkdir(parents=True)
    (home / "data" / "openrouter_models_cache.json").write_text(json.dumps({
        "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "payload": _catalogue_payload()}))
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


def _get(base: str, path: str) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(base + path, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _in_daemon_home(daemon, script: str) -> str:
    """Run a production writer inside the daemon's own home (its env, its VOOL_HOME), the way the rig
    registers its provider. Seeds go through the real writers, never hand-built rows."""
    completed = subprocess.run([sys.executable, "-c", script], cwd=str(rig.REPO_ROOT), env=daemon.env(), capture_output=True, text=True, timeout=180)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout.strip()


def _profile_item(base: str, item_id: str) -> dict | None:
    _status, body = _get(base, "/api/profile")
    return next((i for i in body.get("items") or [] if i.get("item_id") == item_id), None)


# ---------------------------------------------------------------------------------------------
# Memory & Personalisation: Pause / Resume, Export, and the per-item controls on STORED items.
# ---------------------------------------------------------------------------------------------

def test_memory_pause_export_edit_restore_scope_and_forget_act_on_the_owning_authority(served):
    daemon, browser = served
    base = daemon.base_url
    status, body = _post(base, "/api/profile/remember", {"category": "preferred_name", "value": "Test Operator", "scope": "global", "replace": True})
    assert status == 200 and body.get("ok"), (status, body)
    item_id = body["item"]["item_id"]

    page = browser.new_page()
    page.goto(f"{base}/settings#memory", wait_until="networkidle")
    row = page.wait_for_selector(f"[data-profile-item='{item_id}']", timeout=15000)
    assert "Test Operator" in row.inner_text()

    # Pause, read back from the authority, resume.
    page.click("#profilePauseBtn")
    page.wait_for_function("() => /Resume memory/.test((document.querySelector('#profilePauseBtn') || {}).textContent || '')", timeout=15000)
    assert _get(base, "/api/profile")[1].get("paused") is True
    page.click("#profilePauseBtn")
    page.wait_for_function("() => /Pause memory/.test((document.querySelector('#profilePauseBtn') || {}).textContent || '')", timeout=15000)
    assert _get(base, "/api/profile")[1].get("paused") is False

    # Export renders the authority's export document on the page (the clipboard is a bonus, not the proof).
    page.click("#profileExportBtn")
    page.wait_for_function("() => /vool\\.operator_profile\\.v1/.test((document.querySelector('#profileExportOut') || {}).textContent || '')", timeout=15000)
    assert "Test Operator" in page.inner_text("#profileExportOut")

    # Edit -> the stored value changes; Restore previous -> it comes back.
    page.click(f"[data-profile-item='{item_id}'] .profile-edit")
    page.fill(f"[data-profile-item='{item_id}'] .profile-edit-input", "Test Operator Two")
    page.click(f"[data-profile-item='{item_id}'] .profile-edit-save")
    page.wait_for_function(f"() => (document.querySelector(\"[data-profile-item='{item_id}']\") || {{}}).textContent?.includes('Test Operator Two')", timeout=15000)
    assert _profile_item(base, item_id)["value_text"] == "Test Operator Two"
    page.click(f"[data-profile-item='{item_id}'] .profile-restore")
    page.wait_for_function(f"() => {{ const r = document.querySelector(\"[data-profile-item='{item_id}']\"); return r && !r.textContent.includes('Test Operator Two'); }}", timeout=15000)
    assert _profile_item(base, item_id)["value_text"] == "Test Operator"

    # Move scope -> the authority reports the new scope.
    page.select_option(f"[data-profile-item='{item_id}'] .profile-scope-select", "work")
    page.click(f"[data-profile-item='{item_id}'] .profile-scope-move")
    page.wait_for_function(f"() => {{ const r = document.querySelector(\"[data-profile-item='{item_id}'] .profile-scope-pill\"); return r && r.textContent.trim() === 'work'; }}", timeout=15000)
    assert _profile_item(base, item_id)["scope"] == "work"

    # Forget -> the item leaves the listing.
    page.click(f"[data-profile-item='{item_id}'] .profile-forget")
    page.wait_for_selector(f"[data-profile-item='{item_id}']", state="detached", timeout=15000)
    assert _profile_item(base, item_id) is None
    page.close()


# ---------------------------------------------------------------------------------------------
# Advanced: the chat page's comment says /web0 and /trace are reachable from Settings -> Advanced.
# ---------------------------------------------------------------------------------------------

def test_advanced_group_links_to_web0_and_trace_and_both_surfaces_resolve(served):
    daemon, browser = served
    base = daemon.base_url
    page = browser.new_page()
    page.goto(f"{base}/settings#advanced", wait_until="networkidle")
    page.wait_for_selector("a.advanced-link[href='/trace']", timeout=15000)
    hrefs = page.evaluate("() => Array.from(document.querySelectorAll('a.advanced-link')).map(a => a.getAttribute('href'))")
    assert set(hrefs) == {"/web0", "/trace"}, hrefs
    # Both targets answer from this daemon with their own surface stamp.
    for path, surface in (("/web0", "web0-browser"), ("/trace", "trace-rail")):
        with urllib.request.urlopen(base + path, timeout=30) as r:
            assert r.status == 200 and r.headers.get("X-Vool-Workstation-Surface") == surface, (path, r.status, dict(r.headers))
    # Clicking opens the surface without leaving Settings.
    with page.context.expect_page() as opened:
        page.click("a.advanced-link[href='/trace']")
    trace = opened.value
    trace.wait_for_load_state()
    assert trace.url == f"{base}/trace", trace.url
    assert page.url.startswith(f"{base}/settings"), page.url
    trace.close()
    page.close()


# ---------------------------------------------------------------------------------------------
# Models & Providers: the Auto-fallback free model, next to the pin picker.
# ---------------------------------------------------------------------------------------------

def test_auto_fallback_free_model_is_chosen_from_settings_and_lands_on_the_policy(served):
    daemon, browser = served
    base = daemon.base_url
    status, body = _post(base, "/api/settings/credentials", {
        "provider": "openrouter", "value": "sk-or-v1-" + "0123456789abcdef" * 4,
    })
    assert status == 200 and not body.get("error"), (status, body)
    page = browser.new_page()
    page.goto(f"{base}/settings#models", wait_until="networkidle")
    page.wait_for_selector(f"select.auto-fallback option[value='{FREE_ID}']", state="attached", timeout=15000)
    # VOOL Auto never pays: the paid model is not offered as a fallback.
    assert page.evaluate(f"() => !!document.querySelector(\"select.auto-fallback option[value='{PAID_ID}']\")") is False
    page.select_option("select.auto-fallback", FREE_ID)
    page.wait_for_function("() => /falls back to test\\/free-one:free/.test((document.querySelector('.auto-fallback-state') || {}).textContent || '')", timeout=15000)
    assert _get(base, "/api/cloud/models?provider=openrouter")[1].get("auto_free_model") == FREE_ID
    page.select_option("select.auto-fallback", "auto")
    page.wait_for_function("() => /best verified free/.test((document.querySelector('.auto-fallback-state') || {}).textContent || '')", timeout=15000)
    assert _get(base, "/api/cloud/models?provider=openrouter")[1].get("auto_free_model") == "auto"
    # Local Only is named for what it is: a composer mode for one conversation, not a global pin.
    text = page.inner_text("[data-row='model_pin']")
    assert "Local Only" in text and "composer" in text.lower() and "not a global pin" in text.lower(), text
    page.close()


# ---------------------------------------------------------------------------------------------
# API Keys: the custom OpenAI-compatible endpoint's base URL, and Show/Hide on the key field.
# ---------------------------------------------------------------------------------------------

def _credential_names(base: str) -> set[str]:
    _status, body = _get(base, "/api/settings/credentials")
    return {c.get("name") for c in body.get("credentials") or []}


def test_custom_endpoint_base_url_round_trips_and_the_key_field_can_be_revealed(served):
    daemon, browser = served
    base = daemon.base_url
    page = browser.new_page()
    page.goto(f"{base}/settings#keys", wait_until="networkidle")
    page.wait_for_selector("select[aria-label='Provider'] option[value='custom']", state="attached", timeout=15000)
    # The base URL field exists only for the custom endpoint.
    assert page.is_hidden("input.key-base-url")
    page.select_option("select[aria-label='Provider']", "custom")
    page.wait_for_selector("input.key-base-url", state="visible", timeout=15000)
    # Show/Hide flips the key field between password and text, and back.
    assert page.get_attribute("input[aria-label='API key']", "type") == "password"
    page.click("button.key-reveal")
    assert page.get_attribute("input[aria-label='API key']", "type") == "text"
    assert page.get_attribute("button.key-reveal", "aria-pressed") == "true"
    page.click("button.key-reveal")
    assert page.get_attribute("input[aria-label='API key']", "type") == "password"
    # Without a base URL nothing is sent: the page asks for an https:// address instead of claiming a save.
    page.fill("input[aria-label='API key']", "custom-key-0123456789abcdef")
    page.click("button.key-save")
    page.wait_for_function("() => /Not stored/.test((document.querySelector('.key-save-state') || {}).textContent || '')", timeout=15000)
    assert "https://" in page.inner_text(".key-save-state")
    assert "llm.cloud.custom" not in _credential_names(base)
    # Saving now verifies the key with the endpoint before storing it (2026-09-14). A loopback address
    # with nothing listening cannot verify, so nothing is stored and the page says the key was not judged.
    page.fill("input.key-base-url", "http://127.0.0.1:9/v1")
    page.fill("input[aria-label='API key']", "custom-key-0123456789abcdef")
    page.click("button.key-save")
    page.wait_for_function("() => /Nothing was stored/.test((document.querySelector('.key-save-state') || {}).textContent || '')", timeout=30000)
    assert "llm.cloud.custom" not in _credential_names(base)
    # A loopback endpoint that answers as an OpenAI-compatible API and refuses a request without a key is
    # asked once without the key, then once with it; the key lands and the base URL is persisted in its own
    # slot. (An entered endpoint that answers without a key cannot confirm one and never receives it.)
    import hashlib

    from tests._credential_intelligence_support import FakeProviderServer

    typed_key = "custom-key-0123456789abcdef"
    want = hashlib.sha256(f"Bearer {typed_key}".encode()).hexdigest()

    def respond(record):
        if record["auth_sha256"] != want:
            return (401, {"error": {"message": "Incorrect API key provided.", "type": "invalid_request_error", "code": "invalid_api_key"}})
        return (200, {"object": "list", "data": [{"id": "served-model", "object": "model"}]})

    with FakeProviderServer(respond) as endpoint:
        page.fill("input.key-base-url", f"{endpoint.url}/v1")
        page.fill("input[aria-label='API key']", "custom-key-0123456789abcdef")
        page.click("button.key-save")
        page.wait_for_function("() => /Stored, sealed/.test((document.querySelector('.key-save-state') || {}).textContent || '')", timeout=30000)
        assert [(request["path"], request["auth_present"]) for request in endpoint.requests] == [
            ("/v1/models", False),
            ("/v1/models", True),
        ]
    names = _credential_names(base)
    assert {"llm.cloud.custom", "llm.cloud.custom_base_url"} <= names, names
    # The base-URL slot is listed as the endpoint it is: no Test button on it, Test on the key itself.
    page.wait_for_selector("button.key-test[data-provider='custom']", timeout=15000)
    assert page.evaluate("() => !!document.querySelector(\"button.key-test[data-provider='custom_base_url']\")") is False
    assert "Custom endpoint base URL" in page.inner_text("[data-row='cloud_keys']")
    page.close()


# ---------------------------------------------------------------------------------------------
# The extras fragment (learned facts, privacy disclosure, Toolbelt), mounted into the new page.
# ---------------------------------------------------------------------------------------------

def test_extras_fragment_renders_learned_facts_privacy_and_toolbelt_in_the_new_page(served):
    daemon, browser = served
    base = daemon.base_url
    sid = rig.canonical_session("settings-restore-learned-facts")
    # A served chat creates its namespace at its first turn; the seed does the same, then writes the
    # fact through the production writer under that chat's own policy.
    _in_daemon_home(daemon, "from core.context_namespace import ensure_chat_namespace\n"
                            "from core.memory.entries import add_memory_fact\n"
                            f"ensure_chat_namespace({sid!r})\n"
                            f"assert add_memory_fact('the launch rehearsal is on Thursday morning', session_id={sid!r}, scope='chat')\nprint('ok')")
    status, body = _get(base, "/api/memory/entries?limit=50")
    assert status == 200, body
    entry = next((e for e in body.get("entries") or [] if "launch rehearsal" in e.get("fact", "")), None)
    assert entry and entry.get("record_id"), body

    page = browser.new_page()
    page.goto(f"{base}/settings#memory", wait_until="networkidle")
    row = page.wait_for_selector(f"#vsMemorySec [data-vs-record='{entry['record_id']}']", timeout=15000)
    assert "launch rehearsal" in row.inner_text()
    page.click(f"#vsMemorySec [data-vs-record='{entry['record_id']}'] .vs-forget")
    page.wait_for_selector(f"[data-vs-record='{entry['record_id']}']", state="detached", timeout=15000)
    remaining = _get(base, "/api/memory/entries?limit=50")[1].get("entries") or []
    assert all(e.get("record_id") != entry["record_id"] for e in remaining), remaining

    page.evaluate("window.__voolSettings.go('permissions')")
    page.wait_for_selector("#vsPrivacySec", timeout=15000)
    privacy = page.inner_text("#vsPrivacySec")
    assert "Keys are sealed" in privacy and "Spend is gated" in privacy, privacy

    page.evaluate("window.__voolSettings.go('about')")
    page.wait_for_selector("#vsToolbeltBtn", timeout=15000)
    page.click("#vsToolbeltBtn")
    page.wait_for_selector("#vsToolbeltOverlay:not([hidden]) .vs-belt-row", timeout=20000)
    belt = page.inner_text("#vsToolbeltModal")
    assert "Local models" in belt and "Plugins" in belt, belt
    page.close()


# ---------------------------------------------------------------------------------------------
# Backup & restore: export one chat as a signed .voolsession bundle, and import one back.
# ---------------------------------------------------------------------------------------------

def test_a_chat_is_exported_as_a_bundle_and_imported_back_from_settings(served):
    import pathlib

    daemon, browser = served
    base = daemon.base_url
    sid = rig.canonical_session("settings-restore-bundle")
    _in_daemon_home(daemon, "from core.persistent_memory import append_conversation_event\n"
                            f"append_conversation_event(session_id={sid!r}, user_input='What did we decide about the launch window?', assistant_output='Thursday 09:00 UTC.', source_context={{'surface': 'test', 'platform': 'pytest'}})\n"
                            f"append_conversation_event(session_id={sid!r}, user_input='And the fallback?', assistant_output='Friday 09:00 UTC, same pad.', source_context={{'surface': 'test', 'platform': 'pytest'}})\nprint('ok')")
    assert any(s.get("session_id") == sid for s in _get(base, "/api/chat/sessions")[1].get("sessions") or [])

    page = browser.new_page(accept_downloads=True)
    page.goto(f"{base}/settings#backup", wait_until="networkidle")
    page.wait_for_selector(f"select#sbChat option[value='{sid}']", state="attached", timeout=15000)
    page.select_option("select#sbChat", sid)
    page.wait_for_function("() => /2 turns/.test((document.querySelector('#sbExportPreview') || {}).textContent || '')", timeout=15000)

    # Encrypted export: the download route answers with the bundle's bytes.
    page.fill("#sbPass", "correct horse battery")
    page.fill("#sbPass2", "correct horse battery")
    with page.expect_download(timeout=30000) as dl:
        page.click("#sbExportBtn")
    download = dl.value
    assert download.suggested_filename.endswith(".voolsession"), download.suggested_filename
    bundle_path = str(download.path())
    assert pathlib.Path(bundle_path).stat().st_size > 0
    page.wait_for_function("() => /encrypted bundle/.test((document.querySelector('#sbExportStatus') || {}).textContent || '')", timeout=15000)

    # A plaintext export is refused by the page until the warning is acknowledged, as the legacy did.
    page.uncheck("#sbEncrypt")
    page.click("#sbExportBtn")
    page.wait_for_function("() => /acknowledgement/.test((document.querySelector('#sbExportStatus') || {}).textContent || '')", timeout=15000)

    # Import the encrypted bundle back: preview first, then import; the copy lands under a new id.
    page.set_input_files("#sbImportFile", bundle_path)
    page.fill("#sbImportPass", "correct horse battery")
    page.click("#sbPreviewBtn")
    page.wait_for_function("() => /Preview ready/.test((document.querySelector('#sbImportStatus') || {}).textContent || '')", timeout=20000)
    assert "2 / " in page.inner_text("#sbPreviewBox")
    page.click("#sbImportBtn")
    page.wait_for_function("() => /Imported 2 turns/.test((document.querySelector('#sbImportStatus') || {}).textContent || '')", timeout=20000)
    imported_id = page.get_attribute("#sbOpenRestored", "data-session")
    assert imported_id and imported_id != sid, imported_id
    listed = {s.get("session_id"): s for s in _get(base, "/api/chat/sessions")[1].get("sessions") or []}
    assert imported_id in listed and int(listed[imported_id].get("turn_count") or 0) >= 2, listed.get(imported_id)
    page.close()
