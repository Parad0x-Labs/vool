"""The deterministic i18n bootstrap injected into served pages.

Renders one ``<script>`` that (a) publishes the locale's resolved message bundle,
(b) defines ``VOOLT(key, params)`` — the ONLY string lookup the page and fragments
use — with English-then-key fallback and a bounded console diagnostic, and
(c) applies ``data-i18n`` / ``data-i18n-title`` / ``data-i18n-placeholder`` /
``data-i18n-aria-label`` / ``data-i18n-html`` attributes already present in the DOM.

The JS plural/select formatter mirrors ``core.i18n.catalog.format_message`` and
``core.i18n.plurals`` exactly: a plural selects its branch by the bundle tag's
CLDR cardinal category for the integer count (falling back to the required
``other`` branch); a select matches the value or takes ``other`` — so a key
formats identically on the server and in the browser — pinned by tests.
"""
from __future__ import annotations

import json
from typing import Any

from core.i18n.catalog import MessageCatalog, catalog_for
from core.i18n.locales import DIRECTION_RTL, get_locale

# `<` inside a JSON string is escaped so the script element cannot be prematurely
# closed by translated content — hostile or accidental.
#
# The plural rules below are the JS mirror of core/i18n/plurals.py (CLDR cardinal
# categories for integer counts, keyed by the bundle tag's primary subtag). Keep
# the two in lockstep: tests format every plural key at probe counts through both
# and fail on any divergence.
_TEMPLATE = """<script id="vool-i18n">(function () {
  var B = __BUNDLE__;
  var warned = {};
  function pluralCat(tag, n) {
    var p = String(tag || 'en').split('-')[0].toLowerCase(), m100 = n % 100, m10 = n % 10;
    var million = (n !== 0 && n % 1000000 === 0);
    switch (p) {
      case 'ar':
        if (n === 0) return 'zero';
        if (n === 1) return 'one';
        if (n === 2) return 'two';
        if (m100 >= 3 && m100 <= 10) return 'few';
        if (m100 >= 11 && m100 <= 99) return 'many';
        return 'other';
      case 'he':
        if (n === 1) return 'one';
        if (n === 2) return 'two';
        return 'other';
      case 'pl':
        if (n === 1) return 'one';
        if (m10 >= 2 && m10 <= 4 && !(m100 >= 12 && m100 <= 14)) return 'few';
        return 'many';
      case 'ru': case 'uk':
        if (m10 === 1 && m100 !== 11) return 'one';
        if (m10 >= 2 && m10 <= 4 && !(m100 >= 12 && m100 <= 14)) return 'few';
        return 'many';
      case 'lt':
        if (m10 === 1 && !(m100 >= 11 && m100 <= 19)) return 'one';
        if (m10 >= 2 && m10 <= 9 && !(m100 >= 11 && m100 <= 19)) return 'few';
        return 'other';
      case 'hi': case 'vi':
        return (n === 0 || n === 1) ? 'one' : 'other';
      case 'es':
        if (n === 1) return 'one';
        return million ? 'many' : 'other';
      case 'fr': case 'pt':
        if (n >= 0 && n <= 1) return 'one';
        return million ? 'many' : 'other';
      case 'de': case 'en': case 'tr':
        return n === 1 ? 'one' : 'other';
      default:
        return 'other';
    }
  }
  function walkBraced(t, start) {
    var depth = 0;
    for (var i = start; i < t.length; i++) {
      if (t[i] === "{") depth++;
      else if (t[i] === "}") { depth--; if (depth === 0) return i + 1; }
    }
    return t.length;
  }
  var PLURAL_CATS = { zero: 1, one: 1, two: 1, few: 1, many: 1, other: 1 };
  function branches(body, kind) {
    // For a plural only the CLDR categories head a branch; a translated word before {n}
    // inside a branch is text, not a category (mirrors core.i18n.catalog._branches).
    var out = {}, re = /([A-Za-z][A-Za-z0-9_]*)\\s*\\{/g, m;
    while ((m = re.exec(body)) !== null) {
      if (Object.prototype.hasOwnProperty.call(out, m[1])) continue;
      if (kind === "plural" && !PLURAL_CATS[m[1]]) continue;
      var close = walkBraced(body, m.index + m[0].length - 1);
      out[m[1]] = body.slice(m.index + m[0].length, close - 1);
    }
    return out;
  }
  function fmt(t, p, tag) {
    var head = /\\{(\\w+),\\s*(plural|select),\\s*/.exec(t);
    while (head) {
      var open = t.indexOf("{", head.index);
      var end = walkBraced(t, open);
      var body = t.slice(head.index + head[0].length, end - 1);
      var b = branches(body, head[2]), chosen;
      if (head[2] === "plural") {
        var num = p ? parseInt(p[head[1]], 10) : NaN;
        if (isNaN(num)) num = 2;
        var cat = pluralCat(tag || (B && B.tag) || 'en', num);
        chosen = (b[cat] !== undefined) ? b[cat] : (b.other !== undefined ? b.other : "");
      } else {
        var v = p ? String(p[head[1]] != null ? p[head[1]] : "") : "";
        chosen = (b[v] !== undefined) ? b[v] : (b.other !== undefined ? b.other : "");
      }
      t = t.slice(0, head.index) + chosen + t.slice(end);
      head = /\\{(\\w+),\\s*(plural|select),\\s*/.exec(t);
    }
    if (p) Object.keys(p).forEach(function (k) { t = t.split("{" + k + "}").join(String(p[k])); });
    return t;
  }
  function pick(key) {
    var t = (B.messages && B.messages[key]) || null;
    if (t === null || t === "") t = (B.english && B.english[key]) || null;
    return t;
  }
  window.VOOL_I18N = B;
  window.VOOLT = function (key, params) {
    var t = pick(key);
    if (t === null) {
      if (!warned[key] && Object.keys(warned).length < 20) {
        warned[key] = 1;
        try { console.warn("[vool-i18n] missing key, English fallback: " + key); } catch (e) {}
      }
      return key;
    }
    return params ? fmt(t, params, B.tag) : t;
  };
  window.VOOLFMT = fmt;
  // In a browser window === globalThis and this is a no-op; under non-DOM harnesses
  // (the node boot test) `window` is a plain object, so the bare global the page
  // script calls must be published explicitly.
  try {
    globalThis.VOOLT = window.VOOLT;
    globalThis.VOOLFMT = fmt;
    globalThis.VOOL_I18N = B;
  } catch (e) {}
  function apply(root) {
    var scope = root || document;
    scope.querySelectorAll("[data-i18n]").forEach(function (el) {
      var t = VOOLT(el.getAttribute("data-i18n"));
      if (t && t !== el.getAttribute("data-i18n")) el.textContent = t;
    });
    scope.querySelectorAll("[data-i18n-title]").forEach(function (el) {
      var t = VOOLT(el.getAttribute("data-i18n-title"));
      if (t) el.title = t;
    });
    scope.querySelectorAll("[data-i18n-placeholder]").forEach(function (el) {
      var t = VOOLT(el.getAttribute("data-i18n-placeholder"));
      if (t) el.placeholder = t;
    });
    scope.querySelectorAll("[data-i18n-aria-label]").forEach(function (el) {
      var t = VOOLT(el.getAttribute("data-i18n-aria-label"));
      if (t) el.setAttribute("aria-label", t);
    });
    scope.querySelectorAll("[data-i18n-html]").forEach(function (el) {
      var key = el.getAttribute("data-i18n-html");
      var t = VOOLT(key);
      // html keys are whitelist-validated server-side; anything unresolved keeps the
      // server-rendered English markup already inside the element.
      if (t && t !== key) el.innerHTML = t;
    });
  }
  function wireLocalePicker() {
    var sel = document.getElementById("uiLocaleSelect");
    if (!sel || sel.dataset.wired) return;
    sel.dataset.wired = "1";
    var locs = B.uiLocales || {};
    Object.keys(locs).sort(function (a, b) { return locs[a].localeCompare(locs[b], undefined, { sensitivity: "base" }); }).forEach(function (tag) {
      var o = document.createElement("option");
      o.value = tag;
      o.textContent = locs[tag] + " (" + tag + ")";
      sel.appendChild(o);
    });
    sel.value = B.tag || "en";
    sel.addEventListener("change", function () {
      // Persist the operator's explicit choice, then reload into it. The cookie is
      // origin-scoped and survives app restarts; the reload renders deterministically.
      // Before the reload tears the page down, give the page ONE synchronous chance to
      // flush what only lives in memory (the composer draft's debounce window): a
      // language switch must never cost the operator their unstarted sentence.
      document.cookie = "vool_ui_locale=" + encodeURIComponent(sel.value)
        + ";path=/;max-age=31536000;samesite=strict";
      try {
        if (typeof window.VOOL_BEFORE_UI_RELOAD === "function") window.VOOL_BEFORE_UI_RELOAD();
      } catch (e) {}
      location.reload();
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { apply(document); wireLocalePicker(); });
  } else {
    apply(document);
    wireLocalePicker();
  }
  window.VOOL_I18N_APPLY = apply;
})();</script>"""


def i18n_client_bundle_for(catalog: MessageCatalog) -> dict[str, Any]:
    from core.i18n.locales import ui_catalog_tags

    spec = get_locale(catalog.locale) or get_locale("en")
    bundle = catalog.client_bundle()
    bundle["tag"] = spec.tag
    bundle["dir"] = spec.direction
    bundle["rtl"] = spec.direction == DIRECTION_RTL
    bundle["uiLocales"] = {tag: get_locale(tag).endonym for tag in ui_catalog_tags()}
    return bundle


def render_i18n_bootstrap(locale: str) -> str:
    """The head-injected bootstrap as one <script> element (the Settings page injects it in
    ``<head>``; its own scripts carry no document-order law). It MUST run before the page's
    own scripts, which call VOOLT at evaluation time (label maps, greetings)."""
    return '<script id="vool-i18n">' + i18n_bootstrap_js(locale) + "</script>"


def i18n_bootstrap_js(locale: str) -> str:
    """The bootstrap's JS body, for callers that compose it into an EXISTING first script:
    the chat page's house script must remain the document's first <script> (the council
    page-script law pins that order), so the chat page prepends this body inside the house
    script -- VOOLT is then defined before any of the house script's own statements evaluate.

    Finalization note (Goal 2, 2026-09-18): this split repairs the regression introduced
    when the head-injected element began preceding the house script (found first by the
    council script-order law after the i18n landing)."""
    catalog = catalog_for(locale)
    bundle = i18n_client_bundle_for(catalog)
    payload = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    body = _TEMPLATE.replace("__BUNDLE__", payload)
    # the template carries its own script wrapper; strip it for the body form
    if body.startswith("<script"):
        body = body.split(">", 1)[1]
    if body.endswith("</script>"):
        body = body[: -len("</script>")]
    return body
