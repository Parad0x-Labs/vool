"""Shared node harness for the chat page's inline JavaScript.

Not a test module (no `test_` prefix, so pytest does not collect it). It exists because two suites
need the same thing -- the real page source and a DOM complete enough to EXECUTE it -- and a second
copy of a 100-line browser stand-in is a second thing to drift.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from core.vool_chat_page import render_vool_chat_html

HTML = render_vool_chat_html()


def script() -> str:
    """The page's inline script, verbatim."""
    scripts = sorted(re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL), key=len, reverse=True)
    assert scripts, "no inline script found in the chat page"
    return scripts[0]


def slice_source(source: str, start: str, end: str) -> str:
    lo = source.index(start)
    hi = source.index(end, lo)
    return source[lo:hi]


def run_node(program: str, *, timeout: int = 90) -> dict:
    """Execute one .mjs program and parse its single JSON line of output."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available to execute the chat page script")
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", encoding="utf-8", delete=False) as handle:
        handle.write(program)
        path = handle.name
    try:
        result = subprocess.run([node, path], capture_output=True, text=True, timeout=timeout)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, f"the chat page harness failed under node:\n{result.stderr}"
    assert result.stdout.strip(), f"harness produced no output:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


# A DOM complete enough to BOOT: getElementById/querySelector always return a live stub element, so
# the top-level script runs end to end rather than bailing on the first missing node.
DOM = """
globalThis.self = globalThis;   // the page reads `self.crypto`; node defines neither `self` nor a browser DOM
// Visibility is supplied only by real-browser rendering tests. Companion/state tests may boot
// lazy frames without inventing viewport intersections or claiming that a visual rendered.
globalThis.IntersectionObserver = class {
  constructor(callback) { this.callback = callback; this.targets = new Set(); }
  observe(target) { this.targets.add(target); }
  unobserve(target) { this.targets.delete(target); }
  disconnect() { this.targets.clear(); }
};
globalThis.MutationObserver = class {
  constructor(callback) { this.callback = callback; this.targets = new Set(); }
  observe(target) { this.targets.add(target); }
  disconnect() { this.targets.clear(); }
  takeRecords() { return []; }
};
const made = new Map();
class Ctx {
  constructor() { this.fillStyle = ''; this.strokeStyle = ''; this.lineWidth = 1; this.globalAlpha = 1; this.font = ''; this.lineCap = ''; this.lineJoin = ''; this.imageSmoothingEnabled = false; }
  scale() {} clearRect() {} beginPath() {} moveTo() {} lineTo() {} quadraticCurveTo() {} bezierCurveTo() {}
  closePath() {} arc() {} fill() {} stroke() {} strokeRect() {} fillRect() {} setLineDash() {} save() {} restore() {}
  translate() {} rotate() {} fillText() {} measureText() { return { width: 10 }; }
  createLinearGradient() { return { addColorStop() {} }; }
  // The offscreen-buffer half of the Canvas2D surface. VoolCore shades into an ImageData it owns
  // and blits the offscreen canvas onto the visible one, so a page that renders a run card touches
  // all three of these. A real ImageData-shaped buffer is returned rather than a stub object
  // because the shader writes into `.data` by index -- handing back a no-op would make the paint
  // path silently do nothing, and these tests would then pass for the wrong reason.
  createImageData(w, h) { return { width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }; }
  putImageData() {}
  drawImage() {}
}
class El {
  constructor(tag = 'div', id = '') {
    this.tagName = String(tag).toUpperCase(); this.id = id; this.children = []; this.__text = '';
    // `style` is a plain bag of assigned properties PLUS the CSSStyleDeclaration methods the page
    // actually calls -- the layout code writes a custom property (--app-header-h) through
    // setProperty, and a bare object would throw on boot rather than record it.
    this._html = ''; this.dataset = {}; this.attrs = {}; this.hidden = false;
    this.style = {
      setProperty(k, v) { this[k] = String(v); },
      getPropertyValue(k) { return k in this ? String(this[k]) : ''; },
      removeProperty(k) { delete this[k]; },
    };
    this.title = ''; this.value = ''; this.disabled = false; this.checked = false;
    this.width = 112; this.height = 112; this.scrollTop = 0; this.scrollHeight = 0;
    this.offsetWidth = 100; this.clientWidth = 100;
    this.classList = {
      _s: new Set(),
      add: (...c) => c.forEach((x) => this.classList._s.add(x)),
      remove: (...c) => c.forEach((x) => this.classList._s.delete(x)),
      toggle: (c, on) => {
        if (on === undefined) { this.classList._s.has(c) ? this.classList._s.delete(c) : this.classList._s.add(c); }
        else if (on) { this.classList._s.add(c); } else { this.classList._s.delete(c); }
      },
      contains: (c) => this.classList._s.has(c),
    };
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); this.__text = ''; if (!v) this.children = []; }
  // Browser-like text semantics, because real page code depends on them: esc() round-trips a string
  // through textContent -> innerHTML, and reading a bubble's text has to aggregate its children.
  // A stub that returned '' for both would make a passing assertion mean nothing.
  get textContent() {
    if (this.children.length) return this.children.map((c) => c.textContent || '').join('');
    if (this.__text) return this.__text;
    return this._html ? String(this._html).replace(/<[^>]*>/g, '') : '';
  }
  set textContent(v) {
    this.__text = String(v == null ? '' : v);
    this.children = [];
    this._html = this.__text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  get firstChild() { return this.children[0] || null; }
  appendChild(c) { this.children.push(c); c.parentNode = this; return c; }
  insertBefore(node, ref) {
    const i = this.children.indexOf(ref);
    if (i === -1) this.children.push(node); else this.children.splice(i, 0, node);
    node.parentNode = this;
    return node;
  }
  insertBefore(c) { this.children.unshift(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); return c; }
  replaceChild(a) { return a; }
  remove() {}
  // Simple selectors (.class, #id, tag) are resolved against the REAL subtree first. Page
  // code reaches its own children this way -- msgTextEl(el) is `el.querySelector('.msg-text')`
  // -- and a stub that always handed back a fresh detached div meant every such lookup wrote
  // into a node nobody could see, so a test could watch a renderer 'work' into the void.
  // Anything that matches nothing real still falls back to the live stub, so a page that only
  // writes into the result keeps booting instead of bailing on the first missing node.
  _selectorTest(sel) {
    const s = String(sel || '').trim();
    if (s.startsWith('.') && /^[\\w-]+$/.test(s.slice(1))) {
      const want = s.slice(1);
      return (el) => String((el && el.className) || '').split(/\\s+/).indexOf(want) >= 0;
    }
    if (s.startsWith('#') && /^[\\w-]+$/.test(s.slice(1))) {
      const want = s.slice(1);
      return (el) => el && el.id === want;
    }
    if (/^[a-zA-Z][\\w-]*$/.test(s)) {
      const want = s.toUpperCase();
      return (el) => el && el.tagName === want;
    }
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
  // Fire a recorded listener -- lets a test press the real Stop/View button rather than reaching
  // past the UI to call the handler's internals.
  __click() { if (this.__on && this.__on.click) this.__on.click({ preventDefault() {}, stopPropagation() {} }); }
  focus() {} select() {} blur() {} click() { this.__click(); }
  scrollIntoView() {}
  getBoundingClientRect() { return { top: 0, left: 0, width: 100, height: 100, bottom: 100, right: 100 }; }
  // The page dispatches synthetic input/change events after programmatic value writes (the
  // dictation draft path). Node has a global Event; the stub only needs the delivery no-op.
  dispatchEvent() { return true; }
  getContext() { if (!this._ctx) this._ctx = new Ctx(); return this._ctx; }
}
globalThis.document = {
  body: new El('body'), documentElement: new El('html'), head: new El('head'), hidden: false,
  createElement: (t) => new El(t),
  createTextNode: (t) => ({ textContent: t }),
  getElementById: (id) => { if (!made.has(id)) made.set(id, new El('div', id)); return made.get(id); },
  querySelector: (s) => { if (!made.has('sel:' + s)) made.set('sel:' + s, new El('div')); return made.get('sel:' + s); },
  querySelectorAll: () => [],
  addEventListener() {}, removeEventListener() {}, execCommand() { return true; },
};
globalThis.window = {
  devicePixelRatio: 1, innerWidth: 1200, innerHeight: 800,
  matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  addEventListener() {}, removeEventListener() {}, open: () => null, scrollTo() {},
  location: { origin: 'http://127.0.0.1:11434', href: 'http://127.0.0.1:11434/chat' },
  // The layout code branches on `position` (the panel overlays the chat only when the stylesheet
  // has made it fixed) and on `display`. Node has no cascade, so the double reports the initial
  // values rather than undefined -- an in-flow, visible element, which is the honest default here.
  getComputedStyle: () => ({ position: 'static', display: 'block', getPropertyValue: () => '' }),
};
globalThis.location = window.location;
globalThis.getComputedStyle = window.getComputedStyle;
globalThis.matchMedia = window.matchMedia;
const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k), clear: () => store.clear(),
};
// navigator and crypto are getter-only on node's globalThis; define rather than assign.
Object.defineProperty(globalThis, 'navigator', {
  value: { clipboard: { writeText: async () => {}, write: async () => {} }, userAgent: 'node', platform: 'MacIntel' },
  configurable: true,
});
globalThis.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = () => {};
// unref every timer so the harness can exit instead of being held open by the page's own polls.
const _si = globalThis.setInterval, _st = globalThis.setTimeout;
globalThis.setInterval = (fn, ms) => { const t = _si(fn, ms); if (t && t.unref) t.unref(); return t; };
globalThis.setTimeout = (fn, ms) => { const t = _st(fn, ms); if (t && t.unref) t.unref(); return t; };
// One response shape broad enough for whichever caller receives it.
const RESP = {
  ok: true, status: 200, sessions: [], messages: [], queue: [], pins: [], projects: [], models: [],
  events: [], receipts: [], counts: {}, connections: [], data: [], next_after: 0,
  grant: { token: 't', expires_at: 0 }, state: { mode: 'manual' }, approval: { scope: 'once' },
  release_version: '0.0.0', build_id: 'b', commit: 'c',
};
globalThis.fetch = async () => ({
  ok: true, status: 200, headers: { get: () => null },
  json: async () => ({ ...RESP }), text: async () => '{}', blob: async () => ({}),
});
// Use Node's native AbortController so cancellation and listener cleanup are real.
globalThis.Image = class { constructor() { this.src = ''; } };
globalThis.ClipboardItem = class {};
const errors = [];
globalThis.__drove = [];
process.on('unhandledRejection', (e) => errors.push('unhandledRejection: ' + ((e && e.stack) || e)));
process.on('uncaughtException', (e) => errors.push('uncaughtException: ' + ((e && e.stack) || e)));
// A throw during top-level evaluation is caught by the handler above, after which node simply has
// nothing left to do and exits silently. Report on the way out so a BOOT failure is a named error
// rather than empty stdout.
let __reported = false;
function __report() {
  if (__reported) return;
  __reported = true;
  // `__out` is whatever the driving harness handed to out(); errors always ride along, so a run
  // that threw is never mistaken for a run that simply reported nothing.
  console.log(JSON.stringify(Object.assign({ errors, drove: globalThis.__drove }, globalThis.__out || {})));
}
globalThis.out = (payload) => { globalThis.__out = payload; };
process.on('exit', __report);
"""
