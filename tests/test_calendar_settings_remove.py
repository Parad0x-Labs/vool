"""The Calendars panel's "Remove account" button and the card's recovery line (UX finding SB-07).

The real fragment runs under node with the real catalog bundle published as VOOLT, a recording fetch stub and a
scripted ``window.confirm``. A press asks first; a "no" sends nothing; a "yes" sends one remove request even when the
button is pressed twice, then reloads the list and says what happened in calm words. The recovery line is the
catalog's text for the account's state in the page's language.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from core.calendar_settings_fragment import _JS as CAL_JS
from core.i18n.catalog import catalog_for, clear_catalog_cache

_DOM_STUB = """
function elem(tag) {
  return { tag: tag, children: [], attrs: {}, _text: '', _html: '', className: '', disabled: false,
    appendChild(c) { this.children.push(c); return c; },
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(type, fn) { (this.__on = this.__on || {})[type] = fn; },
    __click() { if (this.__on && this.__on.click) this.__on.click({ preventDefault() {} }); },
    querySelector() { return null; }, querySelectorAll() { return []; },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); this.children = []; this._html = ''; },
    get innerHTML() { return this._html; }, set innerHTML(v) { this._html = String(v); } };
}
globalThis.document = { createElement: elem, body: elem('body'), getElementById() { return null; },
  addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
  readyState: 'complete', documentElement: { lang: '' } };
globalThis.document.body.contains = function () { return false; };
globalThis.window = globalThis;
"""

_PROVIDERS = {"caldav": {"label": "CalDAV", "connection": "a password", "events": "events", "links": "links"}}


def _account(status: str) -> dict:
    return {"account_id": "cal-a1", "provider": "caldav", "label": "Team server", "status": status,
            "recovery": "server text", "calendars": [], "capability": {"label": "CalDAV"}}


def _run(accounts: list[dict], *, locale: str = "en", confirm: bool = True, remove_reply: tuple[int, dict] = (200, {"ok": True}),
         clicks: int = 1, card_index: int = 0) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the fragment")
    clear_catalog_cache()
    bundle = dict(catalog_for(locale)._messages)
    status, reply = remove_reply
    program = (
        _DOM_STUB
        + "const M = " + json.dumps(bundle, ensure_ascii=False) + ";\n"
        + "globalThis.VOOLT = function (key) { var t = M[key]; return (t === undefined || t === '') ? key : t; };\n"
        + "globalThis.VOOLFMT = function (t, p) { return String(t).replace(/\\{(\\w+)\\}/g, function (m, n) {"
          " return (p && p[n] != null) ? String(p[n]) : m; }); };\n"
        + "globalThis.document.documentElement.lang = " + json.dumps(locale) + ";\n"
        + "var calls = []; var confirms = []; var listAfter = " + json.dumps([]) + ";\n"
        + "var accounts = " + json.dumps(accounts) + "; var removed = false;\n"
        + "globalThis.confirm = function (text) { confirms.push(text); return " + ("true" if confirm else "false") + "; };\n"
        + "globalThis.fetch = function (url, options) {\n"
        + "  var method = (options && options.method) || 'GET'; calls.push(method + ' ' + url + (options && options.body ? ' ' + options.body : ''));\n"
        + "  if (method === 'GET') {\n"
        + "    var payload = {ok: true, accounts: removed ? [] : accounts, bindings: [], providers: " + json.dumps(_PROVIDERS) + "};\n"
        + "    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(payload); } });\n"
        + "  }\n"
        + "  if (String(url) === '/api/calendar/accounts/remove') {\n"
        + "    removed = " + ("true" if status == 200 else "false") + ";\n"
        + "    return new Promise(function (r) { setTimeout(function () { r({ ok: " + ("true" if status == 200 else "false")
        + ", status: " + str(status) + ", json: function () { return Promise.resolve(" + json.dumps(reply) + "); } }); }, 20); });\n"
        + "  }\n"
        + "  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve({ok: true}); } });\n"
        + "};\n"
        + CAL_JS
        + """
function walk(node, out) { if (!node) return out; out.push(node); (node.children || []).forEach(function (c) { walk(c, out); }); return out; }
var host = elem('div'); document.body.appendChild(host);
VoolCalendarSettings.mountInto(host);
await new Promise(function (r) { setTimeout(r, 30); });
var root = host.children[0];
var card = root.children[2].children[""" + str(card_index) + """];
var nodes = walk(card, []);
var out = { buttons: nodes.filter(function (n) { return n.tag === 'button'; }).map(function (n) { return n.textContent; }),
            recovery: (nodes.filter(function (n) { return n.className === 'vcs-recovery'; })[0] || {}).textContent || '' };
var remove = nodes.filter(function (n) { return n.tag === 'button' && n.textContent === M['vcs.btn.remove']; })[0];
calls.length = 0;
for (var i = 0; i < """ + str(clicks) + """; i++) remove.__click();
await new Promise(function (r) { setTimeout(r, 120); });
out.calls = calls; out.confirms = confirms; out.status = root.children[1].textContent;
out.status_bad = root.children[1].className.indexOf('vcs-bad') !== -1;
out.cards_after = root.children[2].children.filter(function (n) { return n.className === 'vcs-card'; }).length;
console.log(JSON.stringify(out));
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


def test_every_account_card_offers_remove_and_a_disconnected_one_keeps_reconnect_first() -> None:
    connected = _run([_account("connected")], confirm=False)
    assert connected["buttons"][-1] == "Remove account" and "Disconnect" in connected["buttons"], connected
    broken = _run([_account("unsupported")], confirm=False)
    assert "Remove account" in broken["buttons"], broken
    off = _run([_account("disconnected")], confirm=False)
    assert off["buttons"] == ["Reconnect", "Remove account"], off
    assert off["recovery"] == "Disconnected. Reconnect to fetch events and schedule alerts again, or remove the account.", off
    assert "Settings" not in off["recovery"]


def test_saying_no_to_the_confirm_sends_nothing() -> None:
    result = _run([_account("disconnected")], confirm=False)
    assert result["confirms"] == ["Remove “Team server” from VOOL? VOOL forgets the calendars you chose and the events it copied, "
                                  "and cancels its pending alerts. Nothing changes in the calendar itself, and alerts already shown stay in the bell."]
    assert result["calls"] == [] and result["cards_after"] == 1, result


def test_a_double_press_removes_once_and_the_list_reloads_without_the_account() -> None:
    result = _run([_account("connected")], clicks=2)
    posts = [call for call in result["calls"] if call.startswith("POST")]
    assert posts == ['POST /api/calendar/accounts/remove {"account_id":"cal-a1"}'], result
    assert len(result["confirms"]) == 1, result
    assert result["calls"][-1] == "GET /api/calendar/accounts" and result["cards_after"] == 0, result
    assert result["status"] == "Removed “Team server”. Nothing changed in the calendar itself." and not result["status_bad"], result


def test_an_account_already_gone_or_busy_is_said_calmly() -> None:
    gone = _run([_account("connected")], remove_reply=(404, {"ok": False, "reason": "unknown_account"}))
    assert gone["status"] == "That account was already removed.", gone
    busy = _run([_account("connected")], remove_reply=(409, {"ok": False, "reason": "sync_in_progress", "detail": "x"}))
    assert busy["status"] == "This account is syncing right now. Try again in a moment." and busy["status_bad"], busy
    assert busy["cards_after"] == 1, busy


@pytest.mark.parametrize("locale", ["lt", "ja", "ar"])
def test_the_confirm_the_result_and_the_recovery_line_follow_the_page_language(locale: str) -> None:
    clear_catalog_cache()
    messages = catalog_for(locale)._messages
    result = _run([_account("disconnected")], locale=locale)
    assert result["recovery"] == messages["vcs.recovery.disconnected"] != catalog_for("en")._messages["vcs.recovery.disconnected"], result
    assert result["buttons"] == [messages["vcs.btn.reconnect"], messages["vcs.btn.remove"]], result
    assert result["confirms"] == [messages["vcs.remove.confirm"].replace("{name}", "Team server")], result
    assert result["status"] == messages["vcs.saved.removed"].replace("{name}", "Team server"), result
