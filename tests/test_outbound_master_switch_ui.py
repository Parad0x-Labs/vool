"""The pages say calmly that online access is off, and lead to the switch, instead of failing silently.

The update popover in the chat header, the Updates row in Settings and the Calendars panel each run their real
shipped JavaScript under node. When the server reports the outbound switch off (status ``online_access: false``,
or a refused Check / Sync now answering ``online_access_off``), the line says so in catalog words and a button
opens Settings at "Go online on its own". The switch's own help names updates and calendar sync.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from core.i18n.catalog import CATALOGS_DIR
from tests.chat_page_js_harness import DOM, HTML, run_node, script, slice_source

EN = json.loads((CATALOGS_DIR / "en.json").read_text(encoding="utf-8"))["messages"]
UPDATES_OFF = "Online access is off, so VOOL does not check for updates."
CALENDARS_OFF = ("Online access is off, so VOOL does not read calendars from the internet. "
                 "Calendars on this computer keep working.")
OPEN_SETTING = "Go to the online access setting"
REFUSED = {"ok": False, "check_ok": False, "reason": "online_access_off",
           "detail": "Online access is off, so VOOL did not check for updates."}


def test_chat_update_popover_names_the_switch_and_opens_it():
    code = slice_source(script(), "const updateChip =", "setInterval(maybeRepaintSidebarLifecycle")
    result = run_node(DOM + r"""
const calls = []; const opened = [];
const pageT = (key, fallback) => fallback;
const pageTF = (key, fallback, args) => fallback.replace(/\{(\w+)\}/g, (_, name) => args[name]);
globalThis.openSettings = (section) => opened.push(section);
globalThis.fetch = async (url, options) => { calls.push([url, options && options.method]);
  if (url === '/api/update/check') return {ok:false, status:409, json:async () => (""" + json.dumps(REFUSED) + r""")};
  return {ok:true, json:async () => ({phase:'idle', online_access:false})}; };
globalThis.setTimeout = () => 1; globalThis.setInterval = () => 1;
""" + code + r"""
await new Promise(resolve => process.nextTick(resolve));
renderUpdateChip({phase:'idle', message:'', online_access:false});
const shown = {message: updateMessageEl.textContent, button: !updateOnlineBtn.hidden, label: updateOnlineBtn.textContent};
updateMessageEl.textContent = '';
updateCheckBtn.click();
await new Promise(resolve => setImmediate(resolve)); await new Promise(resolve => setImmediate(resolve));
const pressed = updateMessageEl.textContent;
updateOnlineBtn.click();
renderUpdateChip({phase:'up_to_date', message:'Up to date', online_access:true});
out({shown, pressed, opened, onAgain: {message: updateMessageEl.textContent, button: !updateOnlineBtn.hidden},
     posts: calls.filter(c => c[1] === 'POST').map(c => c[0])});
""")
    assert result["errors"] == []
    assert result["shown"]["message"] == UPDATES_OFF and result["shown"]["button"] is True
    assert result["pressed"] == UPDATES_OFF, "a refused Check must not read as a failed check"
    assert result["opened"] == ["permissions/automatic_checks"]
    assert result["onAgain"] == {"message": "Up to date", "button": False}
    assert result["posts"] == ["/api/update/check"]
    assert 'id="updateOnlineBtn"' in HTML and 'data-i18n="online_access.open_setting"' in HTML
    assert f">{OPEN_SETTING}</button>" in HTML


def _settings_updates_code() -> str:
    from core.vool_settings_page import render_vool_settings_html

    source = next(part for part in re.findall(r"<script[^>]*>(.*?)</script>", render_vool_settings_html(), re.DOTALL)
                  if "function widgetUpdates(stack)" in part)
    return slice_source(source, "function widgetUpdates(stack)", "function widgetBuild(stack)")


@pytest.mark.parametrize("status_says_off", [True, False])
def test_settings_updates_row_says_online_access_is_off_and_links_the_switch(status_says_off):
    status = {"phase": "idle", "online_access": False} if status_says_off else {"phase": "up_to_date"}
    result = run_node(DOM + """
const state = {sources:{'/api/update/status':""" + json.dumps(status) + """},sourceErr:{}};
const T = (key, fallback) => fallback;
const el = (tag, cls, text) => {const node = new El(tag); node.textContent = text; return node;};
const calls = [];
const readSource = async url => { calls.push(['GET', url]); };
globalThis.location = {hash: ''};
globalThis.fetch = async (url, options) => { calls.push([options.method, url]);
  return {ok:false, status:409, json:async () => (""" + json.dumps(REFUSED) + """)}; };
const stack = new El();
""" + _settings_updates_code() + """
widgetUpdates(stack);
const find = id => stack.children.find(c => c.dataset && c.dataset.testid === id);
const before = {line: find('settings-update-status').textContent, link: !!find('settings-update-online-setting')};
find('settings-update-check').click();
await new Promise(resolve => setImmediate(resolve)); await new Promise(resolve => setImmediate(resolve));
const after = {line: find('settings-update-status').textContent, link: !!find('settings-update-online-setting'),
               checkEnabled: !find('settings-update-check').disabled};
find('settings-update-online-setting').click();
out({before, after, hash: location.hash, calls});
""")
    assert result["errors"] == []
    if status_says_off:
        assert result["before"] == {"line": UPDATES_OFF, "link": True}
    assert result["after"] == {"line": UPDATES_OFF, "link": True, "checkEnabled": True}
    assert result["hash"] == "#permissions/automatic_checks"
    assert result["calls"] == [["POST", "/api/update/check"]]


_FRAGMENT_DOM = """
function elem(tag) {
  return { tag: tag, children: [], attrs: {}, _text: '', className: '',
    appendChild(c) { this.children.push(c); return c; },
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(type, fn) { (this.__on = this.__on || {})[type] = fn; },
    __click() { if (this.__on && this.__on.click) this.__on.click({ preventDefault() {} }); },
    querySelector() { return null; }, querySelectorAll() { return []; },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); this.children = []; } };
}
globalThis.document = { createElement: elem, body: elem('body'), getElementById() { return null; },
  addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; }, readyState: 'complete',
  documentElement: { lang: 'en' } };
globalThis.window = globalThis;
globalThis.location = { hash: '' };
"""


def test_calendar_panel_says_online_access_is_off_and_sync_now_refuses_calmly():
    from core.calendar_settings_fragment import _JS

    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the fragment")
    view = {"ok": True, "online_access": False, "bindings": [], "providers": {}, "accounts": [
        {"account_id": "a1", "provider": "caldav", "label": "Work server", "status": "connected", "sync_enabled": True,
         "alerts_enabled": True, "default_lead_minutes": [15], "calendars": [
             {"calendar_id": "c1", "display_name": "Work", "selected": True, "present": True, "can_write": True}]}]}
    refused = {"ok": False, "reports": [{"ok": False, "status": "online_access_off", "reason": "online_access_off",
                                         "detail": "Online access is off, so VOOL did not contact this calendar."}]}
    program = _FRAGMENT_DOM + "const M = " + json.dumps(EN, ensure_ascii=False) + """;
globalThis.VOOLT = key => (M[key] === undefined ? key : M[key]);
globalThis.fetch = (url, options) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(
  url === '/api/calendar/sync' ? """ + json.dumps(refused) + " : " + json.dumps(view) + """) });
""" + _JS + """
const flat = n => [n].concat(...(n.children || []).map(flat));
var host = elem('div'); VoolCalendarSettings.mountInto(host);
await new Promise(r => setTimeout(r, 30));
var root = host.children[0];
var all = flat(root);
var banner = all.find(n => n.attrs && n.attrs['data-testid'] === 'vcs-online-off');
var link = all.find(n => n.attrs && n.attrs['data-testid'] === 'vcs-online-setting');
var sync = all.find(n => n.tag === 'button' && n._text === M['vcs.btn.sync_now']);
sync.__click();
await new Promise(r => setTimeout(r, 40));
var syncLine = root.children[1].textContent, syncBad = root.children[1].className;
link.__click();
console.log(JSON.stringify({banner: banner ? flat(banner).map(n => n._text).filter(Boolean) : null,
  syncLine, syncBad, hash: location.hash}));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(program)
        path = handle.name
    try:
        run = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    assert run.returncode == 0, run.stderr[:800]
    result = json.loads(run.stdout.strip().splitlines()[-1])
    assert result["banner"] == [CALENDARS_OFF, OPEN_SETTING]
    assert result["syncLine"] == CALENDARS_OFF and "vcs-bad" not in result["syncBad"]
    assert result["hash"] == "#permissions/automatic_checks"


def test_the_switch_help_names_updates_and_calendar_sync():
    from core.vool_settings_page import settings_groups

    row = next(r for g in settings_groups() for r in g.get("rows", []) if r["id"] == "automatic_checks")
    assert row["help"] == EN["settings.row.automatic_checks.help"]
    assert (EN.get("update.online_off"), EN.get("vcs.online_off"), EN.get("online_access.open_setting")) == (
        UPDATES_OFF, CALENDARS_OFF, OPEN_SETTING), "the page fallbacks must be the English catalog source"
    for words in ("check for updates", "sync the online calendars you chose", "even when you press Check for updates or Sync now"):
        assert words in row["help"]
