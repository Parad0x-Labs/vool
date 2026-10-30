"""The Settings and Setup page loaders under the node harness: a failed load never paints "empty".

Each FIXED loader is driven both ways, on the real served script:

- the Settings backup pane's chat picker (``/api/chat/sessions``): a non-OK answer, a "starting"
  answer and a thrown fetch must paint the calm retry row and pull again on the growing delay --
  never "No chats to export yet.", which reads as lost data. A real ready answer with an empty
  list is the only thing that paints that wording, and a ready list paints its rows as before.
- the Setup state feed (``/api/setup/state``): a failed read paints one calm retry line and
  retries; a real answer renders the wizard from the answer itself (a done step paints as done).

There is no settings node harness yet, so this file carries a minimal one: the same El/document
shape tests/chat_page_js_harness.py uses, trimmed to what the settings script needs to boot
(CSS.escape, history, sessionStorage), plus a per-URL fetch driver.
"""

from __future__ import annotations

import pytest

from core.vool_settings_page import render_vool_settings_html
from core.vool_setup_page import render_vool_setup_html
from tests.chat_page_js_harness import run_node, script_blocks

SETTINGS_HTML = render_vool_settings_html()
SETUP_HTML = render_vool_setup_html()


def settings_scripts() -> list[str]:
    """The settings page's own three script blocks (model+T, page logic, widgets), in order.

    The fragment scripts (wallet, add-ons, ...) are skipped: their surfaces are guarded behind
    ``window.Vool*`` mounts and are not under test here.
    """
    blocks = script_blocks(SETTINGS_HTML)
    picked = [b for b in blocks if ("const MODEL =" in b or "async function readSource" in b or "function widgetBundleExport" in b)]
    assert len(picked) == 3, f"expected the three settings page scripts, got {len(picked)}"
    return picked


def setup_scripts() -> list[str]:
    """The setup page's two own scripts: the STEPS/ON_MAC constants block and the main one.

    The i18n bootstrap is skipped, so T() serves its inline English fallbacks -- the exact
    strings asserted below.
    """
    blocks = [b for b in script_blocks(SETUP_HTML) if "const STEPS" in b or "__voolSetup" in b]
    assert len(blocks) == 2, f"expected the two setup page scripts, got {len(blocks)}"
    return blocks


# A DOM small enough to boot either page: the same semantics as the chat harness (textContent
# aggregation, real-subtree-first querySelector, live id stubs), plus the pieces only these pages
# reach (CSS.escape, history, sessionStorage). Timers are unref'd so a pending retry never holds
# the node process open.
DOM = """
globalThis.self = globalThis;
globalThis.CSS = { escape: (s) => String(s) };
globalThis.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
globalThis.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
class El {
  constructor(tag = 'div', id = '') {
    this.tagName = String(tag).toUpperCase(); this.id = id; this.children = []; this.__text = '';
    this._html = ''; this.dataset = {}; this.attrs = {}; this.hidden = false;
    this.style = { setProperty(k, v) { this[k] = String(v); }, getPropertyValue(k) { return k in this ? String(this[k]) : ''; } };
    this.title = ''; this.value = ''; this.disabled = false; this.checked = false;
    this.scrollTop = 0; this.offsetTop = 0;
    this.classList = {
      _s: new Set(),
      add: (...c) => c.forEach((x) => this.classList._s.add(x)),
      remove: (...c) => c.forEach((x) => this.classList._s.delete(x)),
      toggle(c, on) { (on === undefined ? !this.classList._s.has(c) : on) ? this.classList._s.add(c) : this.classList._s.delete(c); },
      contains: (c) => this.classList._s.has(c),
    };
  }
  get className() { return [...this.classList._s].join(' '); }
  set className(v) { this.classList._s = new Set(String(v || '').split(/\\s+/).filter(Boolean)); }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); this.__text = ''; this.children = []; }
  get textContent() {
    if (this.children.length) return this.children.map((c) => c.textContent || '').join('');
    if (this.__text) return this.__text;
    return this._html ? String(this._html).replace(/<[^>]*>/g, '') : '';
  }
  set textContent(v) {
    this.__text = String(v == null ? '' : v);
    this.children = [];
    this._html = this.__text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  appendChild(c) { this.children.push(c); c.parentNode = this; return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); return c; }
  remove() {}
  get childNodes() { return this.children; }
  _selectorTest(sel) {
    const s = String(sel || '').trim();
    if (s.startsWith('.') && /^\\w+$/.test(s.slice(1))) return (el) => String(el.className).split(/\\s+/).indexOf(s.slice(1)) >= 0;
    if (s.startsWith('#') && /^\\w+$/.test(s.slice(1))) return (el) => el && el.id === s.slice(1);
    if (/^[a-zA-Z]\\w*$/.test(s)) { const want = s.toUpperCase(); return (el) => el && el.tagName === want; }
    return null;
  }
  querySelector(sel) {
    const test = this._selectorTest(sel);
    if (test) {
      const stack = this.children.slice();
      while (stack.length) {
        const node = stack.shift();
        if (test(node)) return node;
        if (node && node.children) for (const kid of node.children) stack.push(kid);
      }
    }
    if (!this._q) this._q = {};
    return this._q[sel] || (this._q[sel] = new El('div'));
  }
  querySelectorAll() { return []; }
  closest() { return null; }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }
  hasAttribute(k) { return k in this.attrs; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(type, fn) { (this.__on || (this.__on = {}))[type] = fn; }
  removeEventListener(type) { if (this.__on) delete this.__on[type]; }
  __click() { if (this.__on && this.__on.click) this.__on.click({ preventDefault() {}, stopPropagation() {} }); }
  focus() {} select() {} blur() {} click() { this.__click(); }
  scrollIntoView() {}
  getBoundingClientRect() { return { top: 0, left: 0, width: 100, height: 100, bottom: 100, right: 100 }; }
  dispatchEvent() { return true; }
}
const made = new Map();
globalThis.document = {
  body: new El('body'), documentElement: new El('html'), head: new El('head'), hidden: false,
  createElement: (t) => new El(t),
  createTextNode: (t) => ({ textContent: t }),
  getElementById: (id) => { if (!made.has(id)) made.set(id, new El('div', id)); return made.get(id); },
  querySelector: (s) => { if (!made.has('sel:' + s)) made.set('sel:' + s, new El('div')); return made.get('sel:' + s); },
  querySelectorAll: () => [],
  addEventListener() {}, removeEventListener() {},
};
globalThis.window = {
  devicePixelRatio: 1, innerWidth: 1200, innerHeight: 800,
  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  addEventListener() {}, removeEventListener() {}, open: () => null, scrollTo() {},
  location: { origin: 'http://127.0.0.1:11434', href: 'http://127.0.0.1:11434/settings', hash: '', search: '' },
  getComputedStyle: () => ({ position: 'static', display: 'block', getPropertyValue: () => '' }),
};
globalThis.location = window.location;
globalThis.history = { pushState() {}, replaceState() {} };
globalThis.getComputedStyle = window.getComputedStyle;
globalThis.matchMedia = window.matchMedia;
const store = new Map(), session = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k), clear: () => store.clear(),
};
globalThis.sessionStorage = {
  getItem: (k) => (session.has(k) ? session.get(k) : null),
  setItem: (k, v) => session.set(k, String(v)),
  removeItem: (k) => session.delete(k), clear: () => session.clear(),
};
Object.defineProperty(globalThis, 'navigator', {
  value: { clipboard: { writeText: async () => {}, write: async () => {} }, userAgent: 'node', platform: 'MacIntel' },
  configurable: true,
});
globalThis.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = () => {};
const _si = globalThis.setInterval, _st = globalThis.setTimeout;
globalThis.setInterval = (fn, ms) => { const t = _si(fn, ms); if (t && t.unref) t.unref(); return t; };
globalThis.setTimeout = (fn, ms) => { const t = _st(fn, ms); if (t && t.unref) t.unref(); return t; };
// The DRIVING code waits on the raw timer: an unref'd one would let node exit mid-wait and leave
// the top-level await unsettled, while the page's own pending retries stay unref'd so they never
// hold the process open after the assertions have their answer.
globalThis.__wait = (ms) => new Promise((r) => _st(r, ms));
const calls = [];
globalThis.__drove = calls;
// One route per URL: {"status": n, "body": {...}} answers that status/body; "throw" rejects the
// fetch itself. Any URL without a route gets the calm default answer.
globalThis.fetch = async (input) => {
  const url = String(typeof input === 'string' ? input : (input && input.url) || '');
  calls.push(url);
  const route = globalThis.__routes[url] !== undefined ? globalThis.__routes[url] : globalThis.__defaultRoute;
  if (route === 'throw') throw new TypeError('fetch failed');
  return {
    ok: route.status >= 200 && route.status < 300, status: route.status,
    headers: { get: () => null },
    json: async () => route.body, text: async () => JSON.stringify(route.body), blob: async () => ({}),
  };
};
const errors = [];
process.on('unhandledRejection', (e) => errors.push('unhandledRejection: ' + ((e && e.stack) || e)));
process.on('uncaughtException', (e) => errors.push('uncaughtException: ' + ((e && e.stack) || e)));
let __reported = false;
function __report() {
  if (__reported) return;
  __reported = true;
  console.log(JSON.stringify(Object.assign({ errors, calls }, globalThis.__out || {})));
}
globalThis.out = (payload) => { globalThis.__out = payload; };
process.on('exit', __report);
"""

READY_EMPTY = {"status": 200, "body": {"ok": True, "state": "ready", "sessions": []}}
STARTING_503 = {"status": 503, "body": {"ok": False, "state": "starting"}}


def _routes_js(routes: dict) -> str:
    """The per-URL route table as JSON; "throw" (a Python string) stays a JS string marker."""
    import json

    return f"globalThis.__routes = {json.dumps(routes)};\nglobalThis.__defaultRoute = {{'status': 200, 'body': {{'ok': True}}}};\n".replace(
        "True", "true"
    )


def run_settings(routes: dict, drive: str, wait_ms: int) -> dict:
    program = (
        DOM
        + _routes_js(routes)
        + "\n".join(settings_scripts())
        + "\n"
        + drive
        + f"\nawait globalThis.__wait({wait_ms});\n"
        + """
const paneBody = document.querySelector('#paneBody');
const status = paneBody.querySelector('#sbExportStatus');
const chatSel = paneBody.querySelector('#sbChat');
out({
  statusText: status ? status.textContent : null,
  paneText: paneBody.textContent,
  chatDisabled: chatSel ? !!chatSel.disabled : null,
  options: chatSel ? chatSel.children.map((o) => o.textContent) : [],
  sessionsCalls: globalThis.__drove.filter((u) => u === '/api/chat/sessions').length,
});
"""
    )
    return run_node(program)


def run_setup(routes: dict, wait_ms: int) -> dict:
    program = (
        DOM
        + _routes_js(routes)
        + "\n".join(setup_scripts())
        + "\n"
        + f"await globalThis.__wait({wait_ms});\n"
        + """
out({
  note: document.querySelector('#stepNote').textContent,
  title: document.querySelector('#stepTitle').textContent,
  firstDot: (document.querySelector('#dots').children[0] || {}).getAttribute ? document.querySelector('#dots').children[0].getAttribute('aria-label') : null,
  stateCalls: globalThis.__drove.filter((u) => u.indexOf('/api/setup/state') >= 0).length,
});
"""
    )
    return run_node(program)


RETRY_ROW = "Couldn't load your chats yet. Trying again."
EMPTY_ROW = "No chats to export yet."


def test_backup_pane_starting_answer_paints_retry_not_empty() -> None:
    """A 503 "starting" answer must not read as 'No chats to export yet.'; it retries."""
    result = run_settings(
        {"/api/chat/sessions": STARTING_503},
        drive="""
await globalThis.__wait(80);
await window.__voolSettings.go('backup', 'session_export');
""",
        wait_ms=1300,
    )
    assert result["errors"] == [], result["errors"]
    assert RETRY_ROW in result["statusText"], result
    assert EMPTY_ROW not in result["paneText"], result
    # the growing-delay retry pulled the list again after the first failure
    assert result["sessionsCalls"] >= 2, result


def test_backup_pane_thrown_fetch_paints_retry_not_empty() -> None:
    """A fetch that throws (the daemon rebinding its port) paints the retry row, never 'empty'."""
    result = run_settings(
        {"/api/chat/sessions": "throw"},
        drive="""
await globalThis.__wait(80);
await window.__voolSettings.go('backup', 'session_export');
""",
        wait_ms=150,
    )
    assert result["errors"] == [], result["errors"]
    assert RETRY_ROW in result["statusText"], result
    assert EMPTY_ROW not in result["paneText"], result


def test_backup_pane_ready_empty_is_the_true_empty() -> None:
    """Only a real ready answer with an empty list paints the plain 'nothing to export' fact."""
    result = run_settings(
        {"/api/chat/sessions": READY_EMPTY},
        drive="""
await globalThis.__wait(80);
await window.__voolSettings.go('backup', 'session_export');
""",
        wait_ms=150,
    )
    assert result["errors"] == [], result["errors"]
    assert EMPTY_ROW in result["statusText"], result
    assert RETRY_ROW not in result["paneText"], result
    assert result["chatDisabled"] is True, result


def test_backup_pane_ready_list_paints_its_rows() -> None:
    """A successful answer still paints its chats in the picker, unchanged by the retry law."""
    route = {
        "/api/chat/sessions": {
            "status": 200,
            "body": {
                "ok": True,
                "state": "ready",
                "sessions": [
                    {"session_id": "s1", "title": "Trip notes"},
                    {"session_id": "s2", "title": "Budget chat"},
                ],
            },
        }
    }
    result = run_settings(
        route,
        drive="""
await globalThis.__wait(80);
await window.__voolSettings.go('backup', 'session_export');
""",
        wait_ms=150,
    )
    assert result["errors"] == [], result["errors"]
    assert "Trip notes" in result["options"] and "Budget chat" in result["options"], result
    assert EMPTY_ROW not in result["paneText"] and RETRY_ROW not in result["paneText"], result
    assert result["chatDisabled"] is False, result


SETUP_READY = {
    "status": 200,
    "body": {
        "ok": True,
        "complete": False,
        "show_entry": True,
        "done_count": 1,
        "total": 4,
        "first_undone": "folder",
        "steps": [
            {"id": "thinking", "title": "How VOOL thinks", "done": True, "skipped": False, "later": "Settings"},
            {"id": "folder", "title": "Pick a working folder", "done": False, "skipped": False, "later": "Settings"},
            {"id": "permissions", "title": "Ask before anything leaves", "done": False, "skipped": False, "later": "Settings"},
            {"id": "name", "title": "What to call you", "done": False, "skipped": False, "later": "Settings"},
        ],
        "cloud_default": None,
    },
}

SETUP_RETRY_LINE = "Can\u2019t reach VOOL right now, trying again\u2026"


def test_setup_state_failure_paints_retry_line_and_retries() -> None:
    """A failed state read shows one calm retry line and pulls again -- never a silent fresh wizard."""
    result = run_setup({"/api/setup/state": STARTING_503}, wait_ms=1300)
    assert result["errors"] == [], result["errors"]
    assert result["note"] == SETUP_RETRY_LINE, result
    assert result["stateCalls"] >= 2, result


def test_setup_state_ready_renders_the_real_answer() -> None:
    """A real answer renders the wizard from it: the done step paints as done, no retry line."""
    result = run_setup({"/api/setup/state": SETUP_READY}, wait_ms=150)
    assert result["errors"] == [], result["errors"]
    assert result["note"] == "", result
    assert result["title"] == "Pick a working folder", result
    assert "(done)" in result["firstDot"], result
