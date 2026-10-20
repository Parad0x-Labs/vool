"""A non-English chat page must not show the strings its page script used to hard-code in English.

The served /chat page is rendered through the real dispatcher with the UI-locale cookie and
loaded in Chromium. The chat page's own render paths (sidebar empty state, archived header,
queue chips, attachment chip, copy feedback, run status, dictation title) then paint into the
live DOM, and what they paint must be the locale's catalog text, not the English the script
used to hard-code. Separately, every catalog lookup call site in the served script
(pageT/pageTF/dictT) is evaluated in the live page: a key no catalog carries, or a key built
wrong (dictT already prefixes "dictation."), silently shows English to every user.
"""

from __future__ import annotations

import json
import re

import pytest

from core.i18n.catalog import catalog_for
from core.web.api.service import dispatch_get
from tests import served_browser

ORIGIN = "http://vool.test"
LOCALES = ("ja", "ar")


class _Stamp:
    """The sliver of RuntimeServices the page route reads (the version stamp)."""

    runtime_version_stamp = {"commit": "t"}


def _served_chat(locale: str) -> str:
    response = dispatch_get(
        path="/chat",
        query={},
        runtime=_Stamp(),
        model_name="m",
        headers={"cookie": f"vool_ui_locale={locale}"},
        client_host="127.0.0.1",
    )
    body = response.body
    return body.decode("utf-8") if isinstance(body, bytes) else str(body)


def _house_script(html: str) -> str:
    start = html.index("<script>")
    return html[start : html.index("</script>", start)]


@pytest.fixture(scope="module")
def browser():
    manager, chromium = served_browser.launch_chromium()
    try:
        yield chromium
    finally:
        chromium.close()
        manager.stop()


def _open(browser, html: str):
    page = browser.new_page()
    errors: list[str] = []
    missing: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on(
        "console",
        lambda msg: missing.append(msg.text) if "[vool-i18n] missing key" in msg.text else None,
    )

    def route(r):
        url = r.request.url
        if url == ORIGIN + "/chat":
            r.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)
        elif url.startswith(ORIGIN + "/api/chat/sessions"):
            r.fulfill(status=200, content_type="application/json", body='{"sessions": []}')
        else:
            r.fulfill(status=404, content_type="application/json", body="{}")

    page.route("**/*", route)
    page.goto(ORIGIN + "/chat", wait_until="load")
    page.wait_for_function(
        "document.querySelector('#sessions .side-empty') !== null", timeout=10000
    )
    return page, errors, missing


# Real render paths of the page, driven the way the app drives them; each returns what the user sees.
PAINT = """async () => {
  const seen = {};
  seen.sidebar_empty = document.querySelector('#sessions .side-empty').textContent;
  renderSessions([{ session_id: 's-arch', title: 'Old', archived: true }]);
  seen.archived_head = document.querySelector('#sessions .arch-head').textContent;
  renderQueue([{ status: 'in_flight', queue_item_id: 'q0', payload: { text: 'alpha' } },
               { status: 'pending', queue_item_id: 'q1', payload: { text: 'beta' } }]);
  const chips = [...document.querySelectorAll('#queue .qchip-text')].map((e) => e.textContent);
  seen.queue_running = chips[0]; seen.queue_queued = chips[1];
  seen.queue_cancel_title = document.querySelector('#queue .qchip-x').title;
  const up = buildAttachChip({ localId: 'a1', state: 'uploading', kind: 'file', name: 'n.txt' }, { draft: true });
  seen.attach_uploading = up.querySelector('.att-meta').textContent;
  seen.attach_remove_title = up.querySelector('.att-x').title;
  const btn = document.createElement('button'); btn.textContent = 'x';
  copiedFail(btn); seen.copy_failed = btn.textContent;
  seen.stage_permission = humanActivityState({ permission: true });
  seen.stage_unknown = humanActivityState({ stage: 'no such stage' });
  seen.dictation_idle_title = dictationIdleTitle();
  return seen;
}"""

# The catalog key (and params) behind each painted value; its English is what used to be hard-coded.
PAINTED = {
    "sidebar_empty": ("session.no_past_chats", {}),
    "archived_head": ("session.archived_count", {"n": 1}),
    "queue_running": ("queue.item_running", {"preview": "alpha"}),
    "queue_queued": ("queue.item_queued", {"pos": 1, "preview": "beta"}),
    "queue_cancel_title": ("queue.cancel_title", {}),
    "attach_uploading": ("attach.uploading", {}),
    "attach_remove_title": ("attach.remove_title", {}),
    "copy_failed": ("msg.copy_failed", {}),
    "stage_permission": ("run.stage_waiting_approval", {}),
    "stage_unknown": ("run.stage_working", {}),
    "dictation_idle_title": ("dictation.dictate_title", {}),
}

# What the page painted before the strings went through the catalog (and still paints in English).
HARD_CODED_ENGLISH = {
    "sidebar_empty": "No past chats yet.",
    "archived_head": "Archived (1)",
    "queue_running": "Running: alpha",
    "queue_queued": "Queued 1: beta",
    "queue_cancel_title": "Cancel this queued message",
    "attach_uploading": "uploading…",
    "attach_remove_title": "Remove this attachment",
    "copy_failed": "Copy failed",
    "stage_permission": "Waiting for approval",
    "stage_unknown": "Working",
    "dictation_idle_title": "Dictate into this message — recorded and transcribed on this machine only",
}


@pytest.mark.parametrize("locale", LOCALES)
def test_rendered_chat_page_paints_the_locale_text(browser, locale) -> None:
    page, errors, _ = _open(browser, _served_chat(locale))
    try:
        seen = page.evaluate(PAINT)
    finally:
        page.close()
    local = catalog_for(locale)
    english_shown = {name: seen[name] for name, text in HARD_CODED_ENGLISH.items() if seen[name] == text}
    assert not english_shown, f"{locale} chat page still paints English: {english_shown}"
    wrong = {
        name: (seen[name], local.format(key, **params))
        for name, (key, params) in PAINTED.items()
        if seen[name] != local.format(key, **params)
    }
    assert not wrong, f"{locale} chat page painted text that is not its catalog's: {wrong}"
    assert not errors, errors


CALL = re.compile(r"""\b(pageTF?|dictT)\('([\w.]+)',\s*('(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*")""")


@pytest.mark.parametrize("locale", LOCALES)
def test_every_lookup_in_the_served_script_resolves_through_the_locale_bundle(browser, locale) -> None:
    html = _served_chat(locale)
    calls = sorted(set(CALL.findall(_house_script(html))))
    assert len(calls) > 200, f"call-site scan found only {len(calls)} lookups; the pattern no longer matches"
    # Each call site is evaluated in the live page exactly as written (function, key, fallback).
    expr = "[" + ",".join(
        f"[{json.dumps(fn)},{json.dumps(key)},{lit},{fn}({json.dumps(key)},{lit},{{}})]"
        for fn, key, lit in calls
    ) + "]"
    page, errors, missing = _open(browser, html)
    try:
        results = page.evaluate(expr)
    finally:
        page.close()
    english = catalog_for("en")
    local = catalog_for(locale)
    fell_back = []
    for fn, key, fallback, got in results:
        if got not in (fallback, re.sub(r"\{\w+\}", "", fallback)):
            continue
        full_key = ("dictation." + key) if fn == "dictT" else key
        # A product name the locale's own catalog deliberately keeps identical to English.
        if full_key in local.locale_keys and local.text(full_key) == english.text(full_key):
            continue
        fell_back.append(f"{fn}('{key}') -> {got!r}")
    assert not fell_back, f"{locale}: these lookups show the English fallback: {fell_back}"
    assert not missing, missing
    assert not errors, errors
