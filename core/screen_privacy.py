"""Hide my name on screen: a display-only mask for streaming or sharing the screen.

When the person turns it on (Settings -> Advanced, preference ``hide_name_on_screen``), every
surface VOOL draws shows the home folder as ``~`` and hides the computer's user name, the
person's own name and the computer's name. It is a mask over what is SHOWN and nothing else:
files, chats, memory, tool calls, receipts and the paths VOOL really uses keep their real bytes.

One rule set, two engines
-------------------------
:func:`rules` builds the masking rules ONCE, as regular-expression sources that read the same in
Python ``re`` and in browser JavaScript (no look-behind, no named groups, no Unicode flag, so
older macOS WebKit compiles them too). The pages apply them in the browser (:data:`SCRIPT`, put
into every page by :func:`inject`), and the phone companion and the native notification
helper apply them here (:func:`mask_text` / :func:`mask_value`). Every rule has the same shape:
group 1 is kept as it is (the boundary in front of the match) and the rest of the match becomes
the replacement, so both engines produce identical text.

Failure posture: the mask only ever hides. A name it cannot read (no account database, no
``scutil``) simply adds no rule; a stored preference that is not exactly ``true`` reads off.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import getpass
import json
import os
import socket
import sys
from typing import Any

MASK = "\u2022\u2022\u2022"   # "•••": plainly hidden, never mistaken for a real name
HOME = "~"

# Words that are account names on many machines and ordinary words everywhere. Masking them in
# running text would wreck sentences ("user", "admin"); inside a home path they are still hidden
# by the path rules.
_COMMON_WORDS = frozenset({
    "admin", "administrator", "default", "guest", "home", "mac", "macbook", "me", "owner", "pc",
    "public", "root", "shared", "test", "user", "users", "vool", "you", "local", "desktop", "laptop",
})
# Home-folder siblings that belong to no person.
_SHARED_HOMES = ("Shared", "Public", "Default", "Default User", "All Users", "Guest")
_MIN_TERM = 3          # shorter names would hit inside ordinary words
_ANYWHERE_TERM = 6     # names this long are hidden even when glued to other letters (rivermapleCV.pdf)


def _js_escape(text: str) -> str:
    """Escape for a pattern both engines read the same way (no ``re.escape``: its output for
    some characters is not valid JavaScript in every mode)."""
    return "".join("\\" + ch if ch in "\\^$.|?*+()[]{}/-" else ch for ch in text)


def _path_body(path: str) -> str:
    """A home folder as a pattern that matches it with either slash style, and with the doubled
    backslashes a Windows path gets inside JSON or a log line."""
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    drive = ""
    if parts and len(parts[0]) == 2 and parts[0][1] == ":":
        drive = _js_escape(parts.pop(0)) + "[\\\\/]+"
    elif path.startswith(("/", "\\")):
        drive = "[\\\\/]"
    return drive + "[\\\\/]+".join(_js_escape(p) for p in parts)


def _clean_name(value: Any) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) >= _MIN_TERM and text.lower() not in _COMMON_WORDS else ""


@functools.lru_cache(maxsize=1)
def _machine_identity() -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    """(home folder, account names, computer names) read from this machine, cached per process."""
    home = os.path.expanduser("~")
    accounts: list[str] = []
    for getter in (getpass.getuser, lambda: os.path.basename(home.rstrip("\\/"))):
        try:
            accounts.append(str(getter() or ""))
        except Exception:
            pass
    try:
        import pwd

        entry = pwd.getpwuid(os.getuid())
        accounts.append(entry.pw_name)
        accounts.append(str(entry.pw_gecos or "").split(",", 1)[0])   # the full name
    except Exception:
        pass
    hosts: list[str] = []
    try:
        hosts.append(socket.gethostname())
    except Exception:
        pass
    if sys.platform == "darwin":
        import subprocess

        for key in ("ComputerName", "LocalHostName"):
            try:
                done = subprocess.run(["/usr/sbin/scutil", "--get", key], capture_output=True, text=True, timeout=2)
                hosts.append(done.stdout.strip() if done.returncode == 0 else "")
            except Exception:
                pass
    host_names: list[str] = []
    for raw in hosts:
        raw = str(raw or "").strip()
        for candidate in (raw, raw.split(".", 1)[0]):
            if candidate and candidate not in host_names:
                host_names.append(candidate)
    return home, tuple(a for a in accounts if a), tuple(host_names)


def _profile_name() -> str:
    """How the person asked VOOL to address them (the Operator Profile), if set."""
    try:
        from core.user_preferences import user_address

        return str(user_address() or "")
    except Exception:
        return ""


def enabled() -> bool:
    try:
        from core.user_preferences import load_preferences

        return load_preferences().hide_name_on_screen is True
    except Exception:
        return False


def _name_terms(accounts: tuple[str, ...], hosts: tuple[str, ...], profile_name: str) -> list[tuple[str, bool]]:
    """(term, case-insensitive) for every name to hide, longest first so a computer name like
    ``River-MacBook-Pro`` is hidden whole before its owner's first name is looked at."""
    terms: dict[str, bool] = {}

    def add(value: str, insensitive: bool) -> None:
        name = _clean_name(value)
        if name and (name not in terms or insensitive):
            terms[name] = insensitive

    for host in hosts:
        add(host, True)
    for account in accounts:
        add(account, True)
        for part in account.replace(".", " ").replace("_", " ").replace("-", " ").split():
            # A short part of a full name ("Ann", "Will") is hidden only as written, so ordinary
            # words in running text stay readable.
            add(part, len(part) >= 5)
    for part in [profile_name, *str(profile_name).split()]:
        add(part, len(part) >= 5)
    return [(t, terms[t]) for t in sorted(terms, key=lambda t: (-len(t), t))]


def build_rules(home: str, accounts: tuple[str, ...], hosts: tuple[str, ...], profile_name: str = "") -> list[dict[str, str]]:
    rules: list[dict[str, str]] = []
    home = str(home or "").rstrip("\\/")
    if home and len(home.replace("\\", "/").strip("/").split("/")) >= 2:
        # The person's own home folder becomes "~" -- with the slash after it kept.
        rules.append({
            # Not after a drive letter: C:\\Users\\<name> on another machine is left to the rule below.
            "pattern": "(^|[^A-Za-z0-9:\\\\])" + _path_body(home) + "(?=[\\\\/]|$|[^A-Za-z0-9._-])",
            "flags": "i",
            "replacement": HOME,
        })
    # Any other home folder (another account, a path pasted from another machine, a Windows path)
    # keeps its shape but loses the name: /Users/•••/..., C:\Users\•••\...
    shared = "|".join(_js_escape(s) for s in _SHARED_HOMES)
    rules.append({
        "pattern": (
            "((?:^|[\\s\"'`(\\[{<>=,;:]|file:/*)(?:/Users|/home|[A-Za-z]:[\\\\/]+(?:Users|Documents and Settings))[\\\\/]+)"
            "(?!(?:" + shared + ")(?:[\\\\/\\s\"'`)<>,;]|$))"
            "[^\\\\/\\s\"'`<>|:*?]+"
        ),
        "flags": "i",
        "replacement": MASK,
    })
    for term, insensitive in _name_terms(accounts, hosts, profile_name):
        body = _js_escape(term).replace(" ", "\\s+")
        if len(term) >= _ANYWHERE_TERM and insensitive:
            pattern = "()" + body
        else:
            pattern = "(^|[^A-Za-z0-9])" + body + "(?=[^A-Za-z0-9]|$)"
        rules.append({"pattern": pattern, "flags": "i" if insensitive else "", "replacement": MASK})
    return rules


def rules() -> list[dict[str, str]]:
    home, accounts, hosts = _machine_identity()
    return build_rules(home, accounts, hosts, _profile_name())


def config() -> dict[str, Any]:
    """What a page needs: off carries no names at all."""
    if not enabled():
        return {"enabled": False, "rules": []}
    return {"enabled": True, "rules": rules()}


# Whether the page being rendered goes to this computer. Unset (a render outside a request, or
# a caller that never said) reads as "not this computer": the page then carries no names.
_PAGE_FOR_OWNER: contextvars.ContextVar[bool] = contextvars.ContextVar("vool_screen_privacy_owner", default=False)


@contextlib.contextmanager
def page_peer(is_owner: bool):
    token = _PAGE_FOR_OWNER.set(bool(is_owner))
    try:
        yield
    finally:
        _PAGE_FOR_OWNER.reset(token)


def page_config() -> dict[str, Any]:
    """What a page may carry: the names only when it is served to this computer. A page on
    another device (a phone browser on the LAN) is another screen, and gets no names."""
    if not _PAGE_FOR_OWNER.get():
        return {"enabled": False, "rules": []}
    return config()


def _compiled(rule_set: list[dict[str, str]]):
    import re

    out = []
    for rule in rule_set:
        try:
            out.append((re.compile(rule["pattern"], re.IGNORECASE if "i" in rule.get("flags", "") else 0), rule["replacement"]))
        except re.error:
            continue
    return out


def apply_rules(text: str, rule_set: list[dict[str, str]]) -> str:
    for pattern, replacement in _compiled(rule_set):
        text = pattern.sub(lambda m, r=replacement: (m.group(1) or "") + r, text)
    return text


def mask_text(text: str, cfg: dict[str, Any] | None = None) -> str:
    """The text as the screen should show it (unchanged when the switch is off)."""
    cfg = config() if cfg is None else cfg
    if not cfg.get("enabled") or not isinstance(text, str) or not text:
        return text
    return apply_rules(text, cfg.get("rules") or [])


def mask_value(value: Any, cfg: dict[str, Any] | None = None) -> Any:
    """Every string inside a JSON-shaped value, masked; keys and non-strings untouched. For
    projections that only go to a screen (the phone companion's reads)."""
    cfg = config() if cfg is None else cfg
    if not cfg.get("enabled"):
        return value
    if isinstance(value, str):
        return mask_text(value, cfg)
    if isinstance(value, dict):
        return {k: mask_value(v, cfg) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_value(v, cfg) for v in value]
    if isinstance(value, tuple):
        return tuple(mask_value(v, cfg) for v in value)
    return value


# ------------------------------------------------------------------------------------------
# The page side. Runs first in <head>, before any of the page's own markup or scripts, so a
# name is never painted even for one frame. It rewrites only what is DRAWN: text nodes,
# tooltip-like attributes, the window title, alert/confirm/prompt wording and web
# notifications. Text the person is typing (inputs, text areas, editable areas) is left alone:
# rewriting it would change what gets saved or sent.
# ------------------------------------------------------------------------------------------
SCRIPT = r"""
(function () {
  'use strict';
  if (window.VoolScreenPrivacy) return;
  var cfg = window.__VOOL_SCREEN_PRIVACY__ || { enabled: false, rules: [] };
  var compiled = [];
  var textOrig = new WeakMap();
  var attrOrig = new WeakMap();
  var ATTRS = ['title', 'aria-label', 'alt', 'placeholder', 'data-tip', 'data-tooltip'];
  var SKIP = 'script,style,textarea,input,noscript,[contenteditable=""],[contenteditable="true"],[contenteditable="plaintext-only"]';

  function compile(rules) {
    var out = [];
    (rules || []).forEach(function (r) {
      try { out.push({ re: new RegExp(r.pattern, 'g' + (r.flags || '')), rep: r.replacement }); } catch (e) {}
    });
    return out;
  }
  function mask(s) {
    if (!cfg.enabled || typeof s !== 'string' || !s) return s;
    for (var i = 0; i < compiled.length; i++) {
      var rep = compiled[i].rep;
      s = s.replace(compiled[i].re, function (m, keep) { return (keep || '') + rep; });
    }
    return s;
  }
  function maskValue(v) {
    if (typeof v === 'string') return mask(v);
    if (Array.isArray(v)) return v.map(maskValue);
    if (v && typeof v === 'object') { var o = {}; Object.keys(v).forEach(function (k) { o[k] = maskValue(v[k]); }); return o; }
    return v;
  }
  function skipped(node) {
    var host = node.nodeType === 1 ? node : node.parentElement;
    if (!host || (host.closest && host.closest(SKIP))) return true;
    /* A list choice without its own value is worth its text: masking it would change the choice. */
    var option = host.closest && host.closest('option');
    return !!(option && !option.hasAttribute('value'));
  }
  function doText(node) {
    var cur = node.nodeValue, rec = textOrig.get(node);
    if (rec && cur === rec.shown) return;
    if (!cfg.enabled || skipped(node)) { if (rec) textOrig.delete(node); return; }
    var shown = mask(cur);
    if (shown !== cur) { textOrig.set(node, { orig: cur, shown: shown }); node.nodeValue = shown; }
    else if (rec) textOrig.delete(node);
  }
  function doAttr(el, name) {
    if (!el.hasAttribute(name)) return;
    var cur = el.getAttribute(name), map = attrOrig.get(el) || {}, rec = map[name];
    if (rec && cur === rec.shown) return;
    var shown = cfg.enabled ? mask(cur) : cur;
    if (shown !== cur) { map[name] = { orig: cur, shown: shown }; attrOrig.set(el, map); el.setAttribute(name, shown); }
    else if (rec) delete map[name];
  }
  /* Text boxes. A box the person is typing in shows exactly what they type. A box that only
     sits on screen (a saved name, a folder field) shows the masked text while it is not being
     edited, and its value -- what the page reads and saves -- stays the real one. */
  var fieldReal = new WeakMap();
  var FIELD_TYPES = /^(|text|search|url|email)$/;
  var protoIn = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
  var protoArea = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');
  function fieldDesc(el) {
    if (el.tagName === 'TEXTAREA') return protoArea;
    if (el.tagName === 'INPUT' && FIELD_TYPES.test(String(el.getAttribute('type') || '').toLowerCase())) return protoIn;
    return null;
  }
  function maskField(el) {
    var d = fieldDesc(el);
    if (!d || !cfg.enabled || el === document.activeElement) return;
    var real = fieldReal.has(el) ? fieldReal.get(el) : d.get.call(el);
    var shown = mask(real);
    if (shown === real) { unmaskField(el); return; }
    fieldReal.set(el, real);
    if (d.get.call(el) !== shown) d.set.call(el, shown);
  }
  function unmaskField(el) {
    if (!fieldReal.has(el)) return;
    var real = fieldReal.get(el);
    fieldReal.delete(el);
    fieldDesc(el).set.call(el, real);
  }
  [[HTMLInputElement.prototype, protoIn], [HTMLTextAreaElement.prototype, protoArea]].forEach(function (pair) {
    var d = pair[1];
    if (!d || !d.configurable) return;
    Object.defineProperty(pair[0], 'value', {
      configurable: true, enumerable: d.enumerable,
      get: function () { return fieldReal.has(this) ? fieldReal.get(this) : d.get.call(this); },
      set: function (v) {
        if (fieldReal.has(this)) fieldReal.set(this, v == null ? '' : String(v));
        else d.set.call(this, v);
        if (cfg.enabled) maskField(this);
      },
    });
  });
  document.addEventListener('focusin', function (e) { if (e.target && e.target.tagName) unmaskField(e.target); }, true);
  document.addEventListener('focusout', function (e) { var t = e.target; if (t && t.tagName) setTimeout(function () { maskField(t); }, 0); }, true);
  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) { doText(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;
    if (root.nodeType === 1) {
      if (root.matches && root.matches('script,style')) return;
      ATTRS.forEach(function (a) { doAttr(root, a); });
      maskField(root);
    }
    var it = document.createTreeWalker(root, 5 /* elements + text */);
    var n;
    while ((n = it.nextNode())) {
      if (n.nodeType === 3) doText(n);
      else { ATTRS.forEach(function (a) { doAttr(n, a); }); maskField(n); }
    }
  }
  function restore(root) {
    var it = document.createTreeWalker(root, 5);
    var n;
    while ((n = it.nextNode())) {
      if (n.nodeType === 3) {
        var rec = textOrig.get(n);
        if (rec && n.nodeValue === rec.shown) n.nodeValue = rec.orig;
        textOrig.delete(n);
      } else {
        unmaskField(n);
        var map = attrOrig.get(n);
        if (map) {
          Object.keys(map).forEach(function (a) { if (n.getAttribute(a) === map[a].shown) n.setAttribute(a, map[a].orig); });
          attrOrig.delete(n);
        }
      }
    }
  }
  var observer = new MutationObserver(function (records) {
    if (!cfg.enabled) return;
    records.forEach(function (r) {
      if (r.type === 'characterData') doText(r.target);
      else if (r.type === 'attributes') doAttr(r.target, r.attributeName);
      else r.addedNodes.forEach(walk);
    });
  });
  function start() {
    observer.observe(document.documentElement, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ATTRS });
  }
  function apply(next) {
    var key = JSON.stringify(next || {});
    if (key === JSON.stringify(cfg)) return;
    if (cfg.enabled) restore(document.documentElement);
    cfg = { enabled: !!(next && next.enabled), rules: (next && next.rules) || [] };
    compiled = compile(cfg.rules);
    if (cfg.enabled) walk(document.documentElement);
    try { window.dispatchEvent(new CustomEvent('vool-screen-privacy', { detail: { enabled: cfg.enabled } })); } catch (e) {}
  }
  function refresh() {
    if (!window.fetch) return Promise.resolve();
    return fetch('/api/screen-privacy', { headers: { Accept: 'application/json' }, cache: 'no-store' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) { if (j && typeof j.enabled === 'boolean') apply(j); })
      .catch(function () {});
  }

  // Wording the browser draws itself.
  ['alert', 'confirm'].forEach(function (name) {
    var orig = window[name];
    if (typeof orig === 'function') window[name] = function (m) { return orig.call(window, mask(m == null ? m : String(m))); };
  });
  var origPrompt = window.prompt;
  if (typeof origPrompt === 'function') window.prompt = function (m, d) { return origPrompt.call(window, mask(m == null ? m : String(m)), d); };
  if (typeof window.Notification === 'function') {
    var N = window.Notification;
    var Masked = function (title, opts) {
      var o = {}; for (var k in (opts || {})) o[k] = opts[k];
      if (typeof o.body === 'string') o.body = mask(o.body);
      return new N(mask(String(title)), o);
    };
    Masked.prototype = N.prototype;
    Masked.requestPermission = function () { return N.requestPermission.apply(N, arguments); };
    try { Object.defineProperty(Masked, 'permission', { get: function () { return N.permission; } }); } catch (e) {}
    window.Notification = Masked;
  }

  compiled = compile(cfg.rules);
  start();
  if (cfg.enabled) walk(document.documentElement);
  // Another window (Settings, the native app, a browser tab) may flip the switch.
  var channel = null;
  try { channel = new BroadcastChannel('vool-screen-privacy'); channel.onmessage = function () { refresh(); }; } catch (e) {}
  window.addEventListener('focus', refresh);
  document.addEventListener('visibilitychange', function () { if (!document.hidden) refresh(); });
  setInterval(function () { if (!document.hidden) refresh(); }, 5000);

  window.VoolScreenPrivacy = {
    mask: mask,
    maskValue: maskValue,
    enabled: function () { return !!cfg.enabled; },
    refresh: refresh,
    announce: function () { try { if (channel) channel.postMessage('changed'); } catch (e) {} return refresh(); },
  };
})();
"""


def head_snippet(cfg: dict[str, Any] | None = None) -> str:
    cfg = page_config() if cfg is None else cfg
    data = json.dumps(cfg, ensure_ascii=True).replace("<", "\\u003c")
    return f"<script data-vool-screen-privacy>window.__VOOL_SCREEN_PRIVACY__={data};</script><script data-vool-screen-privacy>{SCRIPT}</script>"


def inject(html: str, cfg: dict[str, Any] | None = None) -> str:
    """Put the mask at the very start of the page's <head> (or in front of everything when the
    page has none), so it is running before the first name could be drawn."""
    snippet = head_snippet(cfg)
    lower = html[:4096].lower()
    at = lower.find("<head")
    if at >= 0:
        close = html.find(">", at)
        if close >= 0:
            return html[: close + 1] + snippet + html[close + 1:]
    at = lower.find("<html")
    if at >= 0:
        close = html.find(">", at)
        if close >= 0:
            return html[: close + 1] + "<head>" + snippet + "</head>" + html[close + 1:]
    return snippet + html
