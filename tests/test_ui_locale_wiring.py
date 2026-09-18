"""The UI-locale wiring: cookie/query → served pages, EN+LT proof, RTL, fallback.

NEW for product/desktop-usability-20260917. The i18n ENGINE (core/i18n) and its catalogs
existed and were fully tested — but nothing served them: no page rendered a locale, no
selector existed, and the engine commit itself said "UI surface OFF". These tests pin the
WIRING (a different layer than tests/i18n/test_i18n_engine_law.py, which pins the engine):

* /settings and /chat resolve the UI locale (?ui_locale= > vool_ui_locale cookie > en) and
  render <html lang dir> plus the deterministic bootstrap with the resolved catalog;
* the always-discoverable selector block ships in Settings' top-left with the coverage note;
* the browser-side bundle mirrors the engine's plural/select and English-fallback laws;
* an unknown locale falls back to English rather than guessing;
* Hebrew (a shipped RTL catalog) renders dir="rtl".
"""

from __future__ import annotations

import json
import re

from core.i18n.catalog import catalog_for
from core.vool_chat_page import render_vool_chat_html
from core.vool_settings_page import render_vool_settings_html
from core.web.api.service import dispatch_get


class _Stamp:
    """The sliver of RuntimeServices the page routes read (the version stamp)."""

    runtime_version_stamp = {"commit": "t"}


def _rendered(path: str, headers: dict | None = None, query: dict | None = None) -> str:
    response = dispatch_get(
        path=path,
        query=query or {},
        runtime=_Stamp(),
        model_name="m",
        headers=headers or {},
        client_host="127.0.0.1",
    )
    body = response.body
    return body.decode("utf-8") if isinstance(body, bytes) else str(body)


def _render_settings(headers: dict | None = None, query: dict | None = None) -> str:
    return _rendered("/settings", headers, query)


def _render_chat(headers: dict | None = None, query: dict | None = None) -> str:
    return _rendered("/chat", headers, query)


def _locale_of(html: str) -> str:
    match = re.search(r'<html lang="([a-zA-Z-]+)"', html)
    return match.group(1) if match else "(none)"


def test_settings_ships_the_selector_and_the_bootstrap_in_the_default_locale() -> None:
    html = _render_settings()
    assert 'id="uiLocaleSelect"' in html, "the compact language control must ship"
    assert "uiLocaleWrap" in html
    assert 'id="vool-i18n"' in html, "the deterministic bootstrap must ship"
    assert '<html lang="en" dir="ltr">' in html
    # The honest coverage line states the source language, never a translated-app claim.
    assert "English (the source language)" in html
    # The selector block sits ABOVE the back row: top-left and always discoverable.
    assert html.index("uiLocaleWrap") < html.index('id="backRow"')


def test_lithuanian_cookie_renders_lt_on_both_surfaces() -> None:
    headers = {"cookie": "vool_ui_locale=lt; other=x"}
    settings_html = _render_settings(headers=headers)
    chat_html = _render_chat(headers=headers)
    assert '<html lang="lt" dir="ltr">' in settings_html
    assert '<html lang="lt" dir="ltr">' in chat_html
    # The resolved bundle really carries the Lithuanian catalog (not just the tag): a known
    # translated string from the catalog is embedded for the page's VOOLT.
    assert catalog_for("lt").text("composer.send") == "Siųsti"
    assert "Siųsti" in chat_html
    # The selector lists every language in its OWN name (endonyms), never flags-only.
    assert "lietuvių" in settings_html
    assert "English" in settings_html
    # The coverage line is computed from the real catalog and names English fallback honestly.
    assert ("fall back to English" in settings_html) or ("no English fallback needed" in settings_html)


def test_query_beats_cookie_and_unknown_values_fall_back_to_english() -> None:
    assert _locale_of(_render_settings({"cookie": "vool_ui_locale=lt"}, {"ui_locale": ["en"]})) == "en"
    assert _locale_of(_render_settings({"cookie": "vool_ui_locale=xx-invalid"})) == "en"
    assert _locale_of(_render_settings({"cookie": "vool_ui_locale=de"})) == "de"
    assert _locale_of(_render_settings({}, {"ui_locale": ["LT"]})) == "lt", "case-normalised negotiation"
    # A language with NO UI catalog still renders (English bundle) rather than guessing.
    assert _locale_of(_render_chat({"cookie": "vool_ui_locale=sw"})) == "en"


def test_hebrew_catalog_renders_rtl() -> None:
    assert '<html lang="he" dir="rtl">' in _render_settings({"cookie": "vool_ui_locale=he"})
    assert '<html lang="he" dir="rtl">' in _render_chat({"cookie": "vool_ui_locale=he"})


def test_touched_composer_controls_carry_catalog_attributes() -> None:
    html = _render_chat()
    assert 'data-i18n="composer.send"' in html
    assert 'data-i18n="composer.attach"' in html
    assert 'data-i18n-placeholder="composer.placeholder"' in html


def _run_bootstrap(driver: str, headers: dict | None = None) -> dict:
    """Run the served bootstrap under node with the page's DOM stub, then a driver."""
    from tests.chat_page_js_harness import DOM, run_node

    html = _render_chat(headers=headers)
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
    bootstrap = next(s for s in scripts if "window.VOOLT" in s)
    program = DOM + "\n;(function(){\n" + bootstrap + "\n" + driver + "\n})();\n"
    return run_node(program, timeout=60)


def test_browser_bundle_mirrors_plural_select_and_english_fallback() -> None:
    out = _run_bootstrap(
        r"""
const res = { errors: [] };
try {
  // The engine's compound law, executed by the bundle's OWN formatter (VOOLFMT).
  res.pluralOne = VOOLFMT('{n, plural, one {{n} failas} other {{n} failai}}', { n: 1 });
  res.pluralOther = VOOLFMT('{n, plural, one {{n} failas} other {{n} failai}}', { n: 3 });
  res.selectKnown = VOOLFMT('{kind, select, free {nemokama} other {mokama}}', { kind: 'free' });
  res.selectOther = VOOLFMT('{kind, select, free {nemokama} other {mokama}}', { kind: 'promo' });
  // Fallback law: a key the Lithuanian catalog carries resolves translated; a key that does
  // not exist returns the key itself (never blank, never a guess).
  res.ltSend = VOOLT('composer.send');
  res.missingKey = VOOLT('no.such.key.anywhere');
  res.localeTag = window.VOOL_I18N && window.VOOL_I18N.tag;
  res.uiLocales = window.VOOL_I18N && window.VOOL_I18N.uiLocales;
  // Placeholder values are NOT HTML-escaped by the page mirror at insertion points the
  // engine escapes server-side only; the bundle contract mirrors the engine's compound law.
  res.paramFill = VOOLT('composer.send');
} catch (e) { res.errors.push(String(e && e.stack || e)); }
out(res);
""",
        headers={"cookie": "vool_ui_locale=lt"},
    )
    assert not out["errors"], out["errors"]
    assert out["localeTag"] == "lt"
    assert out["pluralOne"] == "1 failas" and out["pluralOther"] == "3 failai"
    assert out["selectKnown"] == "nemokama" and out["selectOther"] == "mokama"
    assert out["ltSend"] == "Siųsti"
    assert out["missingKey"] == "no.such.key.anywhere", "unknown keys stay visible, never blank"
    assert out["uiLocales"]["lt"] == "lietuvių" and out["uiLocales"]["en"] == "English"


def test_settings_route_serves_the_lt_bundle_through_the_real_dispatcher() -> None:
    """The GET route itself (not just the renderer) resolves the cookie into the bundle."""
    body = _render_settings({"cookie": "vool_ui_locale=lt"})
    assert '<html lang="lt"' in body
    raw = body.split("var B = {", 1)[1]
    payload = json.loads("{" + raw.split("};", 1)[0] + "}")
    assert payload["locale"] == "lt"
    assert payload["messages"]["composer.send"] == "Siųsti"
    assert payload["english"]["composer.send"] == "Send", "the English fallback ships alongside"
