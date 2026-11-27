"""A Mac permission step names the Mac's own System Settings and opens the right pane there.

Owner's iMac, 2026-11-27: the calendar card said "System Settings > Privacy & Security > Calendars" and
the owner looked for "Privacy & Security" inside VOOL. The card now says "the Mac's System Settings"
and carries an "Open Mac settings" button that opens the Calendars pane of the Mac's settings app.

* The opener takes a pane by key from a fixed list, never a URL, and runs only on a Mac.
* The route answers only this computer.
* The Apple Calendar card with a permission step shows the button, and a press asks for the Calendars pane.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from core import mac_settings
from core.calendar_settings_fragment import _JS as CAL_JS
from core.i18n.catalog import catalog_for, clear_catalog_cache
from tests.test_calendar_settings_remove import _DOM_STUB, _PROVIDERS


def test_the_opener_opens_only_a_listed_pane_and_only_on_a_mac() -> None:
    ran: list[list[str]] = []

    def run(argv, **kwargs):
        ran.append(list(argv))
        return subprocess.CompletedProcess(argv, 0)

    assert mac_settings.open_pane("calendars", run=run, platform="darwin") == {"ok": True, "pane": "calendars"}
    assert ran == [["/usr/bin/open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars"]]
    for pane in ("", "https://example.invalid", "calendars;rm", "x-apple.systempreferences:other"):
        assert mac_settings.open_pane(pane, run=run, platform="darwin") == {"ok": False, "error": "unknown_pane"}
    assert mac_settings.open_pane("contacts", run=run, platform="linux") == {"ok": False, "error": "not_a_mac"}
    assert len(ran) == 1
    for pane in mac_settings.PANES:
        assert mac_settings.pane_url(pane).startswith("x-apple.systempreferences:com.apple.preference.security?Privacy_")


def test_the_route_answers_only_this_computer(monkeypatch) -> None:
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    asked: list[str] = []
    monkeypatch.setattr(mac_settings, "open_pane", lambda pane: asked.append(pane) or {"ok": True, "pane": pane})

    def post(host: str):
        response = dispatch_post(path="/api/system/open-mac-settings", body={"pane": "calendars"},
                                 headers={"content-type": "application/json"}, runtime=RuntimeServices(display_name="VOOL"),
                                 model_name="vool", workspace_root_provider=lambda: "", client_host=host)
        return response.status, json.loads(bytes(response.body).decode("utf-8"))

    assert post("169.254.10.20")[0] == 403
    assert asked == []
    assert post("127.0.0.1") == (200, {"ok": True, "pane": "calendars"})
    assert asked == ["calendars"]


def _card(account: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the fragment")
    clear_catalog_cache()
    bundle = dict(catalog_for("en")._messages)
    program = (
        _DOM_STUB
        + "const M = " + json.dumps(bundle, ensure_ascii=False) + ";\n"
        + "globalThis.VOOLT = function (key) { var t = M[key]; return (t === undefined || t === '') ? key : t; };\n"
        + "globalThis.VOOLFMT = function (t, p) { return String(t).replace(/\\{(\\w+)\\}/g, function (m, n) {"
          " return (p && p[n] != null) ? String(p[n]) : m; }); };\n"
        + "var calls = []; var accounts = " + json.dumps([account]) + ";\n"
        + "globalThis.fetch = function (url, options) {\n"
        + "  var method = (options && options.method) || 'GET'; calls.push(method + ' ' + url + (options && options.body ? ' ' + options.body : ''));\n"
        + "  var payload = method === 'GET' ? {ok: true, accounts: accounts, bindings: [], providers: " + json.dumps(_PROVIDERS) + "}\n"
        + "                                 : {ok: true, pane: 'calendars'};\n"
        + "  return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(payload); } });\n"
        + "};\n"
        + CAL_JS
        + """
function walk(node, out) { if (!node) return out; out.push(node); (node.children || []).forEach(function (c) { walk(c, out); }); return out; }
var host = elem('div'); document.body.appendChild(host);
VoolCalendarSettings.mountInto(host);
await new Promise(function (r) { setTimeout(r, 30); });
var root = host.children[0];
var nodes = walk(root.children[2].children[0], []);
var mac = nodes.filter(function (n) { return n.attrs && n.attrs['data-testid'] === 'vcs-mac-settings'; })[0];
var out = { button: mac ? mac.textContent : '' };
calls.length = 0;
if (mac) { mac.__click(); await new Promise(function (r) { setTimeout(r, 40); }); }
out.calls = calls; out.status = root.children[1].textContent;
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


def _apple(status: str, detail: str) -> dict:
    return {"account_id": "cal-mac", "provider": "eventkit", "label": "This Mac", "status": status, "status_detail": detail,
            "recovery": "", "calendars": [], "capability": {"label": "Apple Calendar"}}


def test_a_calendar_card_with_a_permission_step_opens_the_calendars_pane() -> None:
    from core.kas.adapters.eventkit_mac import _STATE_DETAIL

    detail = _STATE_DETAIL["denied"]
    assert "the Mac's System Settings" in detail
    shown = _card(_apple("denied", detail))
    assert shown["button"] == "Open Mac settings", shown
    assert shown["calls"][0] == 'POST /api/system/open-mac-settings {"pane":"calendars"}', shown
    assert "System Settings is open on Calendars" in shown["status"], shown


def test_a_connected_calendar_card_has_no_mac_settings_step() -> None:
    assert _card(_apple("connected", ""))["button"] == ""
