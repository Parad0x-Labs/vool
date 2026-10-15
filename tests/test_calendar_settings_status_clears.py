"""The calendar panel's status line must tell the truth: loading ends, binding ids are real.

The panel mounts with "Loading calendar accounts…". Once GET /api/calendar/accounts answers,
that line has done its job and must clear — it used to stay forever because load() only
repainted the list. The account card's credential line must show the binding id through the
formatter — it used to call the param-less helper and render a literal "{binding}".

The real fragment runs under node (the localization-laws harness pattern) with the real
English bundle published as VOOLT and a fetch stub; the DOM stub records click listeners so
the reconnect button is pressed for real, exercising act()'s Working…/Done flow end to end.
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

# Same DOM the localization laws use, plus recorded listeners so a test can press a button.
_DOM_STUB = """
function elem(tag) {
  return { tag: tag, children: [], attrs: {}, _text: '', _html: '', className: '',
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
  readyState: 'complete' };
globalThis.document.body.contains = function () { return false; };
globalThis.window = globalThis;
"""


def _i18n_stub() -> str:
    clear_catalog_cache()
    bundle = dict(catalog_for("en")._messages)
    return (
        "const M = " + json.dumps(bundle, ensure_ascii=False) + ";\n"
        "globalThis.VOOLT = function (key) { var t = M[key];"
        " return (t === undefined || t === '') ? key : t; };\n"
        "globalThis.VOOLFMT = function (t, p) {\n"
        "  return String(t).replace(/\\{(\\w+)\\}/g, function (m, name) {\n"
        "    return (p && p[name] != null) ? String(p[name]) : m;\n"
        "  });\n"
        "};\n"
    )


def _fetch_stub(get_payload: dict, post_ok: bool = True) -> str:
    return (
        "globalThis.fetch = function (url, options) {\n"
        "  if (String(url).indexOf('/api/calendar/accounts') === 0 && (!options || options.method === 'GET')\n"
        "      && String(url) === '/api/calendar/accounts') {\n"
        "    var payload = " + json.dumps(get_payload) + ";\n"
        "    return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(payload); } });\n"
        "  }\n"
        "  return Promise.resolve({ ok: " + ("true" if post_ok else "false") + ", status: 200,"
        " json: function () { return Promise.resolve({ ok: " + ("true" if post_ok else "false") + " }); } });\n"
        "};\n"
    )


def _run_panel(get_payload: dict, *, press_reconnect: bool = False, post_ok: bool = True) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the fragment")
    program = (
        _DOM_STUB
        + _i18n_stub()
        + _fetch_stub(get_payload, post_ok=post_ok)
        + CAL_JS
        + "\n"
        + """
var host = elem('div'); document.body.appendChild(host);
VoolCalendarSettings.mountInto(host);
await new Promise(function (r) { setTimeout(r, 30); });
var root = host.children[0];
var out = { status: root.children[1].textContent, detail: '', reconnect_status: '' };
var list = root.children[2];
if (list.children.length) {
  var card = list.children[0];
  if (card.children.length > 1) out.detail = card.children[1].textContent;
  if (""" + ("true" if press_reconnect else "false") + """) {
    for (var i = 0; i < card.children.length; i++) {
      var node = card.children[i];
      if (node.tag === 'button' && node.__on && node.__on.click) { node.__click(); break; }
    }
    await new Promise(function (r) { setTimeout(r, 40); });
    out.reconnect_status = root.children[1].textContent;
  }
}
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
    assert result.returncode == 0, f"the fragment failed under node:\n{result.stderr[:800]}"
    return json.loads(result.stdout.strip().splitlines()[-1])


_ACCOUNT = {
    "account_id": "a1",
    "provider": "google",
    "label": "Work Google",
    "status": "connected",
    "credential_binding": "cb_fixture_google",
}
_CONNECTED = {
    "ok": True,
    "accounts": [_ACCOUNT],
    "bindings": [],
    "providers": {
        "caldav": {"label": "CalDAV", "connection": "a password", "events": "events", "links": "links"}
    },
}


def test_the_loading_line_clears_once_accounts_are_shown() -> None:
    """A successful load ends "Loading calendar accounts…" — the list itself is the state."""
    result = _run_panel(_CONNECTED)
    assert result["status"] == ""


def test_the_credential_binding_shows_the_real_id() -> None:
    """The binding line carries the binding id, never a literal placeholder."""
    result = _run_panel(_CONNECTED)
    assert result["detail"] == "google · credential binding cb_fixture_google"
    assert "{binding}" not in result["detail"]


def test_a_failed_load_keeps_its_error_text() -> None:
    """ok:false keeps the unavailable line; clearing is only for success."""
    result = _run_panel({"ok": False, "error": "calendar store offline"})
    assert result["status"] == "Calendar accounts are unavailable: calendar store offline"


def test_reconnect_still_ends_on_its_done_line() -> None:
    """act()'s flow survives the clear: Working… → load (clears) → the done text lands last."""
    payload = dict(_CONNECTED, accounts=[dict(_ACCOUNT, status="disconnected")])
    result = _run_panel(payload, press_reconnect=True)
    assert result["reconnect_status"] == (
        "Reconnected. Choose calendars and turn sync on to fetch again."
    )
