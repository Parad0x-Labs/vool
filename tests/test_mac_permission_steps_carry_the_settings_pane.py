"""Every macOS permission step that sends the person to the Mac's System Settings carries the pane to open.

Owner's cut-list item 55 (2026-11-27): Calendar had "Open Mac settings"; Contacts, Notes (Automation),
Speech Recognition and the plugin folder (Files and Folders) only had the words. Each of those answers now
carries ``mac_settings_pane`` beside its words, set where the typed reason is decided and only on a Mac, and
every surface renders the one shared button (core.mac_settings_fragment) from that field.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import types
import uuid
from pathlib import Path

import pytest

from core import mac_settings
from core.i18n.catalog import catalog_for, clear_catalog_cache


@pytest.fixture
def on_a_mac(monkeypatch):
    monkeypatch.setattr(mac_settings, "on_this_mac", lambda: True)


@pytest.fixture
def off_a_mac(monkeypatch):
    monkeypatch.setattr(mac_settings, "on_this_mac", lambda: False)


def test_a_pane_is_carried_only_when_listed_and_only_on_a_mac(monkeypatch) -> None:
    monkeypatch.setattr(mac_settings, "on_this_mac", lambda: True)
    assert [mac_settings.settings_pane(p) for p in ("contacts", "automation", "speech", "files")] == ["contacts", "automation", "speech", "files"]
    assert mac_settings.settings_pane("") == "" and mac_settings.settings_pane("x-apple.systempreferences:other") == ""
    monkeypatch.setattr(mac_settings, "on_this_mac", lambda: False)
    assert mac_settings.settings_pane("contacts") == ""


# --- Contacts --------------------------------------------------------------------------------------------------------

@pytest.fixture
def contacts_env():
    from tests.contacts import protected_changes

    protected_changes.reset()
    yield
    protected_changes.reset()


def test_a_declined_contacts_import_carries_the_contacts_pane_on_every_surface(on_a_mac, contacts_env, monkeypatch) -> None:
    from core.contacts import importing
    from core.contacts.importers.apple import AppleContactsAdapter
    from core.contacts.importers.base import REASON_PERMISSION_DENIED, REASON_PERMISSION_RESTRICTED, ImportSourceError
    from tests.contacts.test_contacts_imports import _apple_people, _framework

    assert ImportSourceError(REASON_PERMISSION_DENIED).as_dict()["mac_settings_pane"] == "contacts"
    assert "mac_settings_pane" not in ImportSourceError(REASON_PERMISSION_RESTRICTED).as_dict(), "a device policy is not a switch to turn on"

    denied = AppleContactsAdapter(binding=_framework(_apple_people(), status=2))
    # the import form's line about this Mac's permission
    assert denied.authorization(None)["mac_settings_pane"] == "contacts"
    assert "mac_settings_pane" not in AppleContactsAdapter(binding=_framework(_apple_people(), status=3)).authorization(None)
    source = importing.connect_source("apple_contacts", consent=True, adapter=denied)
    # the failed preview
    run = importing.preview(source["source_id"], adapter=denied)
    assert run["error"]["reason"] == "permission_denied" and run["error"]["mac_settings_pane"] == "contacts"
    # the source card, read again later
    assert importing.get_source(source["source_id"])["mac_settings_pane"] == "contacts"

    # the route answer the page reads after Preview
    from core.web.api import contacts_api, registry_authorities

    monkeypatch.setattr(registry_authorities, "operator_json_guard", lambda *a, **k: None)
    monkeypatch.setattr(importing, "adapter_for", lambda provider: denied)
    from core.web.api.runtime import RuntimeServices

    response = contacts_api.contacts_write_authority("import_preview", {"source_id": source["source_id"]}, {}, RuntimeServices(display_name="VOOL"))
    body = json.loads(bytes(response.body).decode("utf-8"))
    assert response.status == 409 and body["mac_settings_pane"] == "contacts", body


def test_off_a_mac_a_contacts_refusal_carries_no_pane(off_a_mac) -> None:
    from core.contacts.importers.base import REASON_PERMISSION_DENIED, ImportSourceError

    assert "mac_settings_pane" not in ImportSourceError(REASON_PERMISSION_DENIED).as_dict()


# --- Apple Notes (Automation) ----------------------------------------------------------------------------------------

def _denied_runner(*args, **kwargs):
    return types.SimpleNamespace(returncode=1, stdout="", stderr="execution error: Not authorized to send Apple events to Notes. (-1743)")


def test_a_refused_notes_bridge_carries_the_automation_pane(on_a_mac) -> None:
    from core.operator import apple_notes

    created = apple_notes.create_apple_note(title="t", body="b", runner=_denied_runner)
    assert created["reason"] == "os_permission_denied" and created["mac_settings_pane"] == "automation"
    listed = apple_notes.run_notes_bridge("tell application \"Notes\" to count notes", runner=_denied_runner)
    assert listed["mac_settings_pane"] == "automation"
    unknown = apple_notes.create_apple_note(title="t", body="b", runner=lambda *a, **k: types.SimpleNamespace(returncode=1, stdout="", stderr="(-1712)"))
    assert "mac_settings_pane" not in unknown, "a timeout is not a permission"


@pytest.fixture
def notes_env(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("VOOL_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    from storage.migrations import run_migrations

    run_migrations()


def test_a_note_saved_aside_because_macos_refused_notes_carries_the_pane_to_its_receipt(on_a_mac, notes_env, monkeypatch) -> None:
    from core.action_receipts import receipt_for, receipts_of_events
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent
    from core.operator import apple_notes

    monkeypatch.setattr(apple_notes, "create_apple_note", lambda **kwargs: {"ok": False, "reason": "os_permission_denied",
                                                                            "detail": "macOS refused Automation access to Notes (test double)."})
    intent = parse_operator_action_intent("save a note to Apple Notes with agenda: venue walkthrough")
    saved = dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id="mac-pane-notes")
    assert not saved.ok and saved.details["mac_settings_pane"] == "automation"

    receipt = receipt_for("operator.save_note", ok=saved.ok, status=saved.status, response_text=saved.response_text, details=saved.details)
    assert receipt["action_state"] == "failed" and receipt["action_mac_settings_pane"] == "automation"
    [row] = receipts_of_events([{"event_type": "action_receipt", **receipt}])
    assert row["mac_settings_pane"] == "automation"


def test_a_receipt_carries_only_a_listed_pane_and_only_for_a_failure() -> None:
    from core.action_receipts import receipt_for

    # a read that failed on a Mac permission still gets a receipt: that is where the button is
    read = receipt_for("operator.apple_note_list", ok=False, status="failed", response_text="macOS refused.", details={"mac_settings_pane": "automation"})
    assert read is not None and read["action_mac_settings_pane"] == "automation"
    assert receipt_for("operator.apple_note_list", ok=False, status="failed", response_text="No.", details={}) is None
    odd = receipt_for("operator.save_note", ok=False, status="failed", response_text="No.", details={"mac_settings_pane": "https://example.invalid"})
    assert odd["action_mac_settings_pane"] == ""
    done = receipt_for("operator.save_note", ok=True, status="executed", response_text="Saved.", details={"mac_settings_pane": "automation"})
    assert done["action_mac_settings_pane"] == ""


# --- Speech Recognition (dictation) ----------------------------------------------------------------------------------

def test_dictation_without_speech_permission_carries_the_speech_pane(on_a_mac, monkeypatch) -> None:
    from core import dictation
    from core.artifact_readers import speech_tool

    monkeypatch.setattr(speech_tool, "toolchain_available", lambda: True)
    monkeypatch.setattr(speech_tool, "probe", lambda locale=None: {"authorization": "denied"})
    unavailable = speech_tool.transcribe_unavailable(locale="en-US")
    assert unavailable.code == "speech_recognizer_unauthorized" and unavailable.to_dict()["mac_settings_pane"] == "speech"
    # the composer's probe, and a recording sent anyway
    assert dictation.availability(locale="en-US")["mac_settings_pane"] == "speech"
    with pytest.raises(dictation.DictationUnavailable) as refused:
        dictation.transcribe(b"\0" * 64, locale="en-US")
    assert refused.value.to_dict()["mac_settings_pane"] == "speech"
    monkeypatch.setattr(speech_tool, "probe", lambda locale=None: {"authorization": "authorized", "recognizer_available": False})
    assert "mac_settings_pane" not in dictation.availability(locale="en-US"), "a missing language is not a Mac permission"


# --- Plugin folder (Files and Folders) -------------------------------------------------------------------------------

def test_a_plugin_folder_macos_refused_carries_the_files_pane(on_a_mac, tmp_path, monkeypatch) -> None:
    from core import plugin_catalog

    (tmp_path / "plugins").mkdir()
    monkeypatch.setattr(plugin_catalog, "plugins_root", lambda: tmp_path)
    monkeypatch.setattr(plugin_catalog, "probe_plugin_storage", lambda path, budget_s: {
        "state": plugin_catalog.STORAGE_DENIED, "entries": (), "detail": "Operation not permitted", "elapsed_ms": 1, "probe_pid": None, "lingering": False})
    state = plugin_catalog._probe_and_publish(budget_s=1.0, register=False, reason="test")
    assert state["state"] == "denied" and state["mac_settings_pane"] == "files"
    monkeypatch.setattr(plugin_catalog, "probe_plugin_storage", lambda path, budget_s: {
        "state": plugin_catalog.STORAGE_ACCESSIBLE, "entries": (), "packs": (), "detail": "", "elapsed_ms": 1, "probe_pid": None, "lingering": False})
    assert plugin_catalog._probe_and_publish(budget_s=1.0, register=False, reason="test")["mac_settings_pane"] == ""


# --- the one shared button -------------------------------------------------------------------------------------------

def _run_fragment(script: str, reply: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the fragment")
    from core.mac_settings_fragment import _JS
    from tests.test_calendar_settings_remove import _DOM_STUB

    clear_catalog_cache()
    program = (
        _DOM_STUB
        + "const M = " + json.dumps(dict(catalog_for("en")._messages), ensure_ascii=False) + ";\n"
        + "globalThis.VOOLT = function (key) { var t = M[key]; return (t === undefined || t === '') ? key : t; };\n"
        + "var calls = [];\n"
        + "globalThis.fetch = function (url, options) { calls.push((options.method || 'GET') + ' ' + url + ' ' + (options.body || ''));\n"
        + "  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(" + json.dumps(reply) + "); } }); };\n"
        + _JS + "\n" + script
    )
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(program)
        path = handle.name
    try:
        result = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, result.stderr[:1200]
    return json.loads(result.stdout.strip().splitlines()[-1])


_PRESS = """
var out = { none: VoolMacSettings.control('') === null };
var c = VoolMacSettings.control(PANE);
var btn = c.children[0], status = c.children[1];
out.label = btn.textContent; out.testid = btn.attrs['data-testid'];
btn.__click(); await new Promise(function (r) { setTimeout(r, 30); });
out.calls = calls; out.status = status.textContent; out.bad = status.className.indexOf('bad') !== -1;
console.log(JSON.stringify(out));
"""


@pytest.mark.parametrize("pane, opened", [
    ("contacts", "System Settings is open on Contacts. Turn VOOL on there, then try again."),
    ("automation", "System Settings is open on Automation. Turn on Notes under VOOL there, then try again."),
    ("speech", "System Settings is open on Speech Recognition. Turn VOOL on there, then try again."),
    ("files", "System Settings is open on Files and Folders. Turn VOOL on there, then choose Rescan."),
])
def test_the_shared_button_opens_the_pane_it_was_given_and_says_what_to_do(pane: str, opened: str) -> None:
    shown = _run_fragment("var PANE = " + json.dumps(pane) + ";" + _PRESS, {"ok": True, "pane": pane})
    assert shown["none"] is True
    assert shown["label"] == "Open Mac settings" and shown["testid"] == f"mac-settings-{pane}"
    assert shown["calls"] == [f'POST /api/system/open-mac-settings {{"pane":"{pane}"}}']
    assert shown["status"] == opened and shown["bad"] is False


def test_the_shared_button_says_plainly_when_it_did_not_work() -> None:
    shown = _run_fragment("var PANE = 'contacts';" + _PRESS, {"ok": False, "error": "not_a_mac"})
    assert shown["status"] == "That did not work." and shown["bad"] is True


def test_every_surface_asks_the_shared_button_for_the_field_and_never_reads_the_words() -> None:
    from core.contacts_fragment import _JS as CONTACTS_JS
    from core.vool_chat_page import _VOOL_CHAT_HTML, _page_fragments

    assert sum("window.VoolMacSettings = Object.freeze" in f for f in _page_fragments()) == 1
    for surface in ("receipts[i] && receipts[i].mac_settings_pane", "if (typed) offerDictationMacSettings(data.mac_settings_pane)", "if (j.message) offerDictationMacSettings(j.mac_settings_pane)",
                    "st.mac_settings_pane && window.VoolMacSettings"):
        assert surface in _VOOL_CHAT_HTML, surface
    for surface in ("macSettings(authMac, a.mac_settings_pane)", "macSettings(card, s.mac_settings_pane)", "res.body && res.body.mac_settings_pane"):
        assert surface in CONTACTS_JS, surface
    assert "System Settings" not in CONTACTS_JS.split("function macSettings")[1].split("function why")[0]


@pytest.mark.parametrize("locale", ["de", "ja", "ar", "zh-Hans"])
def test_the_button_words_are_translated(locale: str) -> None:
    clear_catalog_cache()
    messages = catalog_for(locale)._messages
    english = catalog_for("en")._messages
    for key in ("mac_settings.button", "mac_settings.opened.contacts", "mac_settings.opened.automation", "mac_settings.opened.speech",
                "mac_settings.opened.files", "mac_settings.failed"):
        assert messages.get(key) and messages[key] != english[key], (locale, key)
