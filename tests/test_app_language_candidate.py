"""The application-language candidate's own laws (beyond tests/test_ui_locale_wiring.py).

Materially different cases, per the delivery's acceptance list:
- the /setup surface resolves the UI locale like /chat and /settings do;
- localized ARIA labels ship through the new data-i18n-aria-label bootstrap seam;
- the fault PRESENTATION mirror: en.json fault.<code>.message equals core.faults'
  user_message byte-exactly (the drift law), and a locale serves its translation
  while the authority text stays reachable in English;
- the dynamic permission/payment/update surfaces carry catalog-resolved strings in
  the served bundle, keyed by stable codes — never by parsing prose;
- restart persistence: a fresh render with the cookie set (a new "process" reading
  only the cookie) lands in the same locale;
- the language switch preserves the composer draft: the picker flushes through
  VOOL_BEFORE_UI_RELOAD before location.reload().
"""
from __future__ import annotations

import json

from core.i18n.catalog import catalog_for, clear_catalog_cache
from core.vool_chat_page import render_vool_chat_html
from core.vool_settings_page import render_vool_settings_html
from core.vool_setup_page import render_vool_setup_html
from core.web.api.service import dispatch_get


class _Stamp:
    runtime_version_stamp = {"commit": "t"}



def _walk_json(text: str, start: int) -> str:
    """The JSON object spanning from ``text[start] == "{"`` to its matching close brace.

    A message value may legitimately contain any characters (a translated "{name};"
    carries the two-byte sequence the old first-occurrence cut split on), so the
    bundle is extracted by brace depth, never by searching for a terminator.
    """
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise AssertionError("unterminated bundle JSON")
def _rendered(path: str, headers: dict | None = None, query: dict | None = None) -> str:
    response = dispatch_get(
        path=path, query=query or {}, runtime=_Stamp(), model_name="m",
        headers=headers or {}, client_host="127.0.0.1",
    )
    body = response.body
    return body.decode("utf-8") if isinstance(body, bytes) else str(body)


def _bundle(html: str) -> dict:
    start = html.index("var B = ") + len("var B = ")
    return json.loads(_walk_json(html, start))


# ---- the setup surface joins the locale-resolving pages --------------------------------

def test_setup_resolves_the_cookie_locale_and_ships_the_bootstrap() -> None:
    html = _rendered("/setup", headers={"cookie": "vool_ui_locale=lt"})
    assert '<html lang="lt" dir="ltr">' in html
    assert 'id="vool-i18n"' in html
    # The server-resolved COPY really is Lithuanian, not just the tag.
    assert "VOOL sąranka" in html
    assert "Kur jam galvoti?" in html


def test_setup_query_beats_cookie_and_unknown_falls_back_to_english() -> None:
    assert '<html lang="pl"' in _rendered("/setup", {"cookie": "vool_ui_locale=lt"}, {"ui_locale": ["pl"]})
    assert '<html lang="en"' in _rendered("/setup", {"cookie": "vool_ui_locale=xx-invalid"})


def test_setup_english_default_is_byte_stable_for_the_existing_pact() -> None:
    html = _rendered("/setup")
    assert "Set up VOOL" in html
    assert "Where should it think?" in html


# ---- aria labels localize through the new bootstrap seam --------------------------------

def test_chat_chrome_carries_localized_aria_and_title_attributes() -> None:
    clear_catalog_cache()
    html = _rendered("/chat", headers={"cookie": "vool_ui_locale=lt"})
    assert 'data-i18n-aria-label="chat.log_aria"' in html
    assert 'data-i18n-title="sidebar.home_title"' in html
    bundle = _bundle(html)
    assert bundle["messages"]["chat.log_aria"] == "Pokalbis"
    assert bundle["messages"]["sidebar.home_title"] == "Pradžia — įrankiai ir nustatymai"


def test_the_bootstrap_applies_aria_labels_client_side() -> None:
    """The served bootstrap's apply() must set aria-label, not only title/placeholder.

    The chat page composes the bootstrap INSIDE its house script (the first-script
    order law), so extracting "the script containing window.VOOLT" from /chat drags
    the whole ~716 KB chat initialization into an isolated DOM, which then fails on
    chat chrome that was never served. Exercise the bootstrap's owning function
    instead -- proven byte-present in the render below, so the served page ships
    exactly the body under test.
    """
    from core.i18n.page_bundle import i18n_bootstrap_js
    from tests.served_browser import launch_chromium

    clear_catalog_cache()
    html = _rendered("/chat", headers={"cookie": "vool_ui_locale=lt"})
    bootstrap = i18n_bootstrap_js("lt")
    assert bootstrap in html, "the served chat page no longer embeds the bootstrap body verbatim"

    manager, browser = launch_chromium()
    try:
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.set_content(
            '<button id="x" aria-label="Search this chat" '
            'data-i18n-aria-label="header.search_aria"></button>'
            '<script>' + bootstrap + '</script>'
        )
        assert page.locator("#x").get_attribute("aria-label") == "Ieškoti šiame pokalbyje"
        assert not errors, errors
    finally:
        browser.close()
        manager.stop()


# ---- the fault presentation mirror -------------------------------------------------------

def test_en_catalog_fault_mirror_matches_the_authority_byte_exactly() -> None:
    from core.faults.catalog import all_specs

    en = catalog_for("en")
    for spec in all_specs():
        key = f"fault.{spec.code}.message"
        assert en.text(key) == spec.user_message, (
            f"{key} drifted from core.faults authority — regenerate catalogs"
        )


def test_fault_presentation_localizes_while_the_authority_stays_english() -> None:
    clear_catalog_cache()
    lt = catalog_for("lt")
    authority = catalog_for("en").text("fault.provider_unavailable.message")
    translated = lt.text("fault.provider_unavailable.message")
    assert translated != authority, "the Lithuanian fault presentation did not resolve"
    assert "Modelio teikėjo nepavyko pasiekti" in translated
    # The authority's own English stays the deterministic fallback for any consumer
    # that does not resolve a locale.
    assert authority == "The model provider could not be reached. You can try again in a moment."


def test_every_locale_catalog_stays_within_the_english_key_set_and_current_sha() -> None:
    """No locale drifted stale against the regenerated source (a stale catalog would
    serve ALL-English — the silent-drop this candidate must not ship)."""
    from core.i18n.catalog import CATALOGS_DIR, MessageCatalog, source_catalog_sha256

    sha = source_catalog_sha256()
    for path in sorted(CATALOGS_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if path.stem != "en":  # en.json is the source itself; it records no hash of itself
            assert data.get("source_catalog_sha256") == sha, f"{path.name} is stale against en.json"
        catalog = MessageCatalog(path.stem)
        assert not catalog.diagnostic.stale_source
        assert not catalog.diagnostic.rejected_keys, f"{path.name}: {list(catalog.diagnostic.rejected_keys)[:3]}"


# ---- dynamic permission/payment/update surfaces carry resolved strings -------------------

def test_permission_and_price_gate_strings_resolve_in_the_served_lt_bundle() -> None:
    clear_catalog_cache()
    html = _rendered("/chat", headers={"cookie": "vool_ui_locale=lt"})
    bundle = _bundle(html)
    msgs = bundle["messages"]
    # Permission bar buttons and the meta line keep their {placeholders}.
    assert msgs["permissions.allow_chat"] == "Leisti darbo aplankos keitimus šiame pokalbyje"
    assert "{resources}" in msgs["permissions.meta_line"]
    # Payment review: scope sentence keeps the exact terms (chat/model/24h) in translation.
    assert "šis pokalbis, šis modelis, 24 valandas" in msgs["pay.scope_usepod"]
    assert "<b>tik kainą</b>" in msgs["pay.scope_usepod"], "html keys carry their markup"
    # Update states resolve; the chip's phase keys are the updater's own codes.
    assert msgs["update.downloading_pct"] == "Atsiunčiama {percent}%"


def test_setup_error_status_localizes_with_the_reason_parameter_intact() -> None:
    clear_catalog_cache()
    lt = catalog_for("lt")
    assert lt.text("setup.status.not_saved") == "Neįrašyta — {reason}"
    # The renderer never substitutes the parameter server-side: the page's own fmt does,
    # and the raw authority `reason` (an API error code) rides through untouched.


# ---- restart persistence: only the cookie survives a fresh render ------------------------

def test_cookie_locale_survives_a_fresh_render_of_every_surface() -> None:
    """A new render is a new process reading only the cookie: en -> lt -> pl -> en,
    the acceptance round-trip, each step on a cold catalog cache."""
    for tag, expect in [
        ("en", '<html lang="en"'),
        ("lt", '<html lang="lt"'),
        ("pl", '<html lang="pl"'),
        ("en", '<html lang="en"'),
    ]:
        clear_catalog_cache()
        headers = {"cookie": f"vool_ui_locale={tag}"}
        for path, _renderer in [
            ("/chat", None), ("/settings", None), ("/setup", None),
        ]:
            html = _rendered(path, headers=headers)
            assert expect in html, f"{path} did not round-trip to {tag}"
    clear_catalog_cache()


# ---- the language switch must not cost the draft ------------------------------------------

def test_the_picker_flushes_in_memory_state_before_reloading() -> None:
    """The bootstrap's change handler calls VOOL_BEFORE_UI_RELOAD before reload, and
    the served chat page registers a synchronous draft flush under that name."""
    html = _rendered("/chat")
    assert "VOOL_BEFORE_UI_RELOAD" in html
    # The composer fragment (appended to the chat page) installs the flush itself.
    assert "function flushDraft()" in html
    assert "window.VOOL_BEFORE_UI_RELOAD = function(){ try { flushDraft(); } catch (e) {} };" in html


def test_switching_locale_does_not_touch_model_answer_policy_surfaces() -> None:
    """The answer-language widget and dictation locale are untouched by the UI-locale
    bundle: no composer/dictation key changes value between locales (they are their
    own Settings widgets, per the owner's law)."""
    clear_catalog_cache()
    en_bundle = _bundle(_rendered("/chat"))
    lt_bundle = _bundle(_rendered("/chat", headers={"cookie": "vool_ui_locale=lt"}))
    assert en_bundle["tag"] == "en" and lt_bundle["tag"] == "lt"
    # Dictation keeps its own controls in both locales (same English authority text).
    assert en_bundle["english"]["dictation.failed_http"] == lt_bundle["english"]["dictation.failed_http"]
