"""Adding a Google or Outlook calendar offers only that service's sign-ins, and on a Mac says the Calendar app path.

sls on the installed fe98e849 (Settings, Calendars, Add a calendar account, Source "Google Calendar", 2026-11-22): the
"Sign-in" list offered OpenRouter and Brave Search, keys that have nothing to do with Google, and nothing said that the
Google calendars already in the Mac's Calendar app show up under "Apple Calendar on this Mac". The server refused nothing
either: an account could name any verified key. The real fragment runs under node, as in test_calendar_settings_remove.
"""
from __future__ import annotations

import json
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from core.calendar_settings_fragment import _JS as CAL_JS
from core.i18n.catalog import catalog_for, clear_catalog_cache
from core.operator import calendar_accounts
from tests.test_calendar_settings_remove import _DOM_STUB

UNRELATED = [
    {"binding_id": "b-or", "provider_id": "openrouter", "provider_label": "OpenRouter", "account": "", "status": "verified"},
    {"binding_id": "b-brave", "provider_id": "brave_search", "provider_label": "Brave Search", "account": "", "status": "verified"},
]
# A Google sign-in's provider id varies by where it was made; the service it names is what counts.
GOOGLE = {"binding_id": "b-g", "provider_id": random.choice(["google", "google_calendar", "fixture_google", "work-gmail"]),
          "provider_label": "Google", "account": "me@example.com", "status": "verified"}


def _add_form(source: str, bindings: list[dict], *, platform: str) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the fragment")
    clear_catalog_cache()
    bundle = dict(catalog_for("en")._messages)
    payload = {"ok": True, "accounts": [], "bindings": bindings, "providers": calendar_accounts.PROVIDERS}
    program = (
        _DOM_STUB
        + "const M = " + json.dumps(bundle, ensure_ascii=False) + ";\n"
        + "globalThis.VOOLT = function (key) { var t = M[key]; return (t === undefined || t === '') ? key : t; };\n"
        + "globalThis.VOOLFMT = function (t, p) { return String(t).replace(/\\{(\\w+)\\}/g, function (m, n) {"
          " return (p && p[n] != null) ? String(p[n]) : m; }); };\n"
        + "globalThis.document.documentElement.lang = 'en';\n"
        + "Object.defineProperty(globalThis, 'navigator', {value: {platform: " + json.dumps(platform) + "}, configurable: true});\n"
        + "var payload = " + json.dumps(payload) + ";\n"
        + "globalThis.fetch = function () { return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(payload); } }); };\n"
        + CAL_JS
        + """
function walk(node, out) { if (!node) return out; out.push(node); (node.children || []).forEach(function (c) { walk(c, out); }); return out; }
var host = elem('div'); document.body.appendChild(host);
VoolCalendarSettings.mountInto(host);
await new Promise(function (r) { setTimeout(r, 30); });
var nodes = walk(host, []);
var source = nodes.filter(function (n) { return n.tag === 'select' && n.attrs['aria-label'] === 'Calendar source'; })[0];
source.value = """ + json.dumps(source) + """; source.__on.change({});
nodes = walk(host, []);
var signin = nodes.filter(function (n) { return n.tag === 'select' && n.attrs['aria-label'] === 'Sign-in for this account'; })[0];
var row = nodes.filter(function (n) { return n.children && n.children.indexOf(signin) >= 0; })[0];
var details = nodes.filter(function (n) { return n.className === 'vcs-detail' && !n.hidden; }).map(function (n) { return n.textContent; });
var submit = nodes.filter(function (n) { return n.tag === 'button' && n.textContent === 'Add account'; })[0];
console.log(JSON.stringify({options: signin.children.map(function (o) { return o.textContent; }), row_hidden: !!row.hidden,
  details: details, add_disabled: !!submit.disabled}));
"""
    )
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(program)
        path = handle.name
    try:
        result = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, f"the fragment failed under node:\n{result.stderr[:1200]}"
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("source", ["google", "graph"])
def test_a_model_or_search_key_is_never_offered_as_a_calendar_sign_in(source):
    form = _add_form(source, UNRELATED, platform="MacIntel")
    assert not any("OpenRouter" in o or "Brave" in o for o in form["options"]), form
    assert form["row_hidden"] and form["add_disabled"], form
    said = " ".join(form["details"])
    assert "Apple Calendar on this Mac" in said and "Calendar app" in said, form


def test_a_google_sign_in_is_offered_for_google_and_the_mac_path_is_said():
    form = _add_form("google", [*UNRELATED, GOOGLE], platform="MacIntel")
    assert form["options"][1:] == ["me@example.com — Google"], form
    assert not form["row_hidden"], form
    assert any("show up under" in d for d in form["details"]), form


def test_off_a_mac_the_calendar_app_path_is_not_offered():
    form = _add_form("google", UNRELATED, platform="Linux x86_64")
    said = " ".join(form["details"])
    assert "Apple Calendar on this Mac" not in said.replace("“", "").replace("”", "") or "only on a Mac" in said, form
    assert form["add_disabled"], form


@pytest.mark.parametrize(("provider", "binding_provider", "fits"), [
    ("google", "google", True), ("google", "google_calendar", True), ("google", "fixture_google", True),
    ("google", "openrouter", False), ("google", "brave_search", False), ("google", "googly", False),
    ("graph", "microsoft", True), ("graph", "microsoft_graph", True), ("graph", "google", False),
    ("caldav", "anything", True),
])
def test_the_server_takes_only_a_sign_in_for_that_calendar(provider, binding_provider, fits):
    assert calendar_accounts.binding_fits(provider, binding_provider) is fits
