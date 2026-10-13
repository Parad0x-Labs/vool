"""The answer-details line reads in plain words, and only says what the server's line says.

The line under an answer (`core/response_provenance.py`) is a machine record:
`tool | smalltalk_fast_path | no model | build 0.7.0`. The chat page now leads with plain words
("Quick built-in reply · not written by an AI model") and keeps the exact line one click away
behind a "details" button. Two things can go wrong, and both are checked in the real page in
Chromium, through a real chat turn whose commit carries the line:

* a plain phrase that claims something the line does not -- above all "not written by an AI
  model" on a turn where a model ran. Every line here is produced by the server's own
  `format_provenance_footer` from a turn result, so the page is tested against the shapes the
  server really emits, not against strings typed to match the parser;
* a line the page does not fully understand. Those must FAIL CLOSED: the raw line, visible, no
  plain sentence and no toggle -- never a best guess.

The raw line must stay in the bubble (behind the toggle and on the title), the toggle is a real
button with aria-expanded, the words come from the locale's catalog (an RTL locale included), and
nothing in the line can become markup.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from core.app_version import VOOL_VERSION
from core.i18n.catalog import catalog_for
from core.response_provenance import format_provenance_footer
from core.vool_chat_page import render_vool_chat_html
from tests import served_browser

BUILD = f"build {VOOL_VERSION}"

FAST_PATH = {
    "model_execution": {"source": "fast_path", "used_model": False},
    "model_calls": 0,
    "route": "deterministic:smalltalk_fast_path",
    "route_reason": "smalltalk_fast_path",
    "fast_path_hit": True,
}
LOCAL_USAGE = {"prompt_tokens": 900, "output_tokens": 30, "cost_class": "local", "model_id": "ollama/qwen2.5:7b"}
TOOL_TURN = {"model_execution": {"source": "fast_path", "used_model": False}, "route_reason": "workspace_runtime_fast_path"}
EXACT_CALL = {"input_tokens": 1000, "output_tokens": 200, "request_seconds": 2.0, "cost_state": "exact",
              "currency": "USDC", "amount": "0.0042"}
BOUNDED_CALL = {"input_tokens": 1000, "output_tokens": 200, "cost_state": "upper_bound", "currency": "SOL",
                "amount": "0.01"}


def _footer(result: dict, usage: dict | None = None, accounting: dict | None = None) -> str:
    line = format_provenance_footer(result, usage, accounting)
    assert line.startswith("`") and line.endswith("`"), line
    return line.strip("`")


def _t(cat, key: str, **params: Any) -> str:
    full = "chat.provenance." + key
    return cat.format(full, **params) if params else cat.text(full)


def _cases(cat, grouped) -> list[tuple[str, str, str]]:
    """(name, raw line from the server's own formatter, the plain words it must read as).

    `grouped` is the page locale's own number grouping (Arabic digits in ar), read from the page.
    """
    no_ai = _t(cat, "no_ai")
    return [
        ("smalltalk", _footer(FAST_PATH), " · ".join([_t(cat, "quick_reply"), no_ai])),
        ("local_model", _footer({"model_calls": 1}, LOCAL_USAGE),
         " · ".join([_t(cat, "local"), "qwen2.5:7b", _t(cat, "tokens", n=930, count=grouped(930))])),
        ("cloud_paid", _footer({"model_calls": 1}, {"prompt_tokens": 2000, "output_tokens": 450, "cost_class": "cloud_paid",
                                                    "model_id": "claude", "usd_actual": 0.0123}),
         " · ".join([_t(cat, "cloud"), "claude", _t(cat, "tokens", n=2450, count=grouped(2450)),
                     _t(cat, "charged", amount="$0.0123")])),
        ("model_tokens_unreported", _footer({"model_calls": 1}, {"cost_class": "cloud", "model_id": "x-large"}),
         " · ".join([_t(cat, "cloud"), "x-large"])),
        ("model_wrote_files", _footer({"model_calls": 1, "details": {"b": {"files_written": ["a", "b"]}}}, LOCAL_USAGE),
         " · ".join([_t(cat, "local"), "qwen2.5:7b", _t(cat, "tokens", n=930, count=grouped(930)), _t(cat, "files", n=2)])),
        ("model_usage_details", _footer({"model_calls": 1}, None, {
            "calls": 2, "lanes": ["cloud"], "models": ["m"], "completed_calls": 2, "failed_calls": 0, "pending_calls": 0,
            "served_usage": {"prompt_tokens": 1000, "output_tokens": 200, "cost_class": "cloud", "model_id": "m"},
            "usage_details": [EXACT_CALL, BOUNDED_CALL], "verification_receipts": [{"verification": {"status": "VERIFIED"}}]}),
         " · ".join([_t(cat, "cloud"), "m", _t(cat, "tokens", n=2400, count=grouped(2400)),
                     _t(cat, "charged", amount="0.0042 USDC"), _t(cat, "up_to", amount="0.01 SOL")])),
        ("model_cost_partly_unreported", _footer({"model_calls": 1}, None, {
            "calls": 2, "lanes": ["cloud"], "models": ["m"], "completed_calls": 2, "failed_calls": 0, "pending_calls": 0,
            "served_usage": {"prompt_tokens": 1000, "output_tokens": 200, "cost_class": "cloud", "model_id": "m"},
            "usage_details": [EXACT_CALL, {"input_tokens": 10, "output_tokens": 20, "request_seconds": 1.0,
                                           "cost_state": "unreported"}]}),
         " · ".join([_t(cat, "cloud"), "m", _t(cat, "tokens", n=1230, count=grouped(1230)), _t(cat, "cost_partial")])),
        # A model ran and the turn did not record which lane: it is still a model answer.
        ("model_lane_unrecorded", _footer({"model_calls": 1}), _t(cat, "model")),
        ("tool_after_routing", _footer(TOOL_TURN, None, {
            "calls": 2, "lanes": ["local"], "tools": ["workspace.read_file"],
            "completed_calls": 2, "failed_calls": 0, "pending_calls": 0}),
         " · ".join([_t(cat, "builtin_tool"), no_ai, _t(cat, "also_called")])),
        ("tool_after_cloud_failure", _footer(TOOL_TURN, None, {
            "calls": 1, "lanes": ["cloud"], "tools": ["workspace.write_file"],
            "completed_calls": 0, "failed_calls": 1, "pending_calls": 0}),
         " · ".join([_t(cat, "builtin_tool"), no_ai, _t(cat, "try_failed_cloud")])),
        ("tool_outcome_unrecorded", _footer(dict(FAST_PATH, route_reason="calendar_lane"), None, {"calls": 1}),
         " · ".join([_t(cat, "builtin_tool"), no_ai, _t(cat, "also_called")])),
        ("nothing_invoked", _footer({"route": "deterministic:z", "model_calls": 0,
                                     "model_execution": {"source": "other", "used_model": False}}), _t(cat, "no_model")),
        ("attempted_no_answer", _footer({"model_calls": 1, "model_execution": {"source": "x", "used_model": False}}, None, {
            "calls": 1, "failed_calls": 1, "completed_calls": 0, "pending_calls": 0, "models": ["p/m1"], "lanes": ["cloud"]}),
         " · ".join([_t(cat, "no_answer"), _t(cat, "try_failed_cloud")])),
        ("refused_before_send", _footer({"model_execution": {
            "source": "selected_model_blocked", "used_model": False,
            "details": {"block_reason": "usepod_dispatch_refused:MONEY_LIQUIDITY_UNVERIFIED"}}}, None, {
            "calls": 1, "failed_calls": 1, "completed_calls": 0, "pending_calls": 0, "models": ["m1"]}),
         _t(cat, "not_sent")),
        ("local_only", _footer(dict(FAST_PATH, local_only=True)),
         " · ".join([_t(cat, "quick_reply"), no_ai, _t(cat, "local_only")])),
        ("local_only_nothing_invoked", _footer({"route": "deterministic:z", "model_calls": 0, "local_only": True,
                                                "model_execution": {"source": "other", "used_model": False}}),
         " · ".join([_t(cat, "no_model"), _t(cat, "local_only")])),
    ]


#: Lines the page must NOT translate: unknown segments, a lane word with the wrong tail, a tool
#: line without the server's no-model marker, and markup. Each must show raw, with no toggle.
UNKNOWN_SHAPES = [
    f"tool | smalltalk_fast_path | {BUILD}",
    "local | qwen2.5:7b | 930 tok | a segment this page has never seen",
    "cloud | claude | lots of tok",
    "runtime | m1 | something new | no usable answer",
    f"tool | <img src=x onerror=window.__pwned=1> | no model | {BUILD}",
    f"tool | workspace.read_file | no model | 2 local routing calls | 3 mystery things | {BUILD}",
]


def _commit(answer: str, footer: str) -> dict:
    return {
        "type": "response.commit",
        "version": 2,
        "revision": 1,
        "turn_id": "",
        "request_id": "req-details-1",
        "canonical_content": answer,
        "content_hash": "sha256:" + hashlib.sha256(answer.encode("utf-8")).hexdigest(),
        "display_metadata": {"provenance": {}, "usage": {}, "provenance_footer": "`" + footer + "`"},
    }


class _Server:
    """The shipped page plus one scripted turn whose commit carries `footer`."""

    def __init__(self, html: str) -> None:
        self.html = html
        self.footer = ""

    def route(self, route) -> None:
        request = route.request
        url = request.url
        if request.resource_type == "document":
            route.fulfill(status=200, content_type="text/html", body=self.html)
            return
        if url.endswith("/api/chat") and request.method == "POST":
            answer = "Here you go."
            frames = [
                {"model": "vool", "created_at": "2026-10-13T00:00:00Z",
                 "message": {"role": "assistant", "content": answer}, "done": False},
                {"model": "vool", "created_at": "2026-10-13T00:00:00Z",
                 "message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop",
                 "vool_response_commit": _commit(answer, self.footer)},
            ]
            route.fulfill(status=200, content_type="application/x-ndjson",
                          body="\n".join(json.dumps(f) for f in frames) + "\n")
            return
        if "/api/cloud/model" in url and "models" not in url:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"ok": True, "model": "", "cost_state": "free"}))
            return
        if "/api/chat/history" in url:
            session = url.split("session=", 1)[1].split("&", 1)[0]
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps({"session_id": session, "messages": []}))
            return
        route.fulfill(status=200, content_type="application/json", body="{}")


@pytest.fixture(scope="module")
def browser():
    ctx, chromium = served_browser.launch_chromium()
    try:
        yield chromium
    finally:
        chromium.close()
        ctx.stop()


def _turn(page, server: _Server, footer: str) -> dict:
    """One real chat turn (type, Send, stream, commit), then the last answer's details line."""
    server.footer = footer
    page.evaluate("() => { newChat(); }")
    page.wait_for_timeout(60)
    page.fill("#input", "hello")
    page.click("#send")
    page.wait_for_function(
        "() => { const m = [...document.querySelectorAll('.msg.assistant:not(.pending)')]; "
        "return m.length && m[m.length - 1].querySelector('.chat-provenance'); }",
        timeout=15000,
    )
    return page.evaluate(
        """() => {
          const all = [...document.querySelectorAll('.msg.assistant:not(.pending)')];
          const p = all[all.length - 1].querySelector('.chat-provenance');
          const plain = p.querySelector('.prov-plain');
          const btn = p.querySelector('button.prov-more');
          const raw = p.querySelector('.prov-raw') || p.querySelector('code');
          return {
            plain: plain ? plain.textContent : null,
            hasButton: !!btn,
            expanded: btn ? btn.getAttribute('aria-expanded') : null,
            buttonText: btn ? btn.textContent : null,
            rawText: raw ? raw.textContent : null,
            rawHidden: raw ? raw.hidden : null,
            rawVisible: raw ? raw.getClientRects().length > 0 : false,
            title: p.title,
            visibleText: p.innerText,
            images: p.querySelectorAll('img').length,
          };
        }"""
    )


def _open(browser, locale: str):
    html = render_vool_chat_html(ui_locale=locale)
    server = _Server(html)
    context = browser.new_context()
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/*", server.route)
    page.set_viewport_size({"width": 1100, "height": 800})
    page.goto("http://vool.test/chat")
    page.wait_for_selector("#input", timeout=5000)
    page.evaluate("() => { localStorage.clear(); }")
    return context, page, server, errors


@pytest.mark.parametrize("locale", ["en", "ar"])
def test_every_server_shape_reads_as_true_plain_words_with_the_raw_line_kept(browser, locale) -> None:
    cat = catalog_for(locale)
    context, page, server, errors = _open(browser, locale)
    try:
        if locale == "ar":
            assert page.evaluate("() => document.documentElement.dir") == "rtl"
        def grouped(n: int) -> str:
            return page.evaluate("(n) => Number(n).toLocaleString(appLocale())", n)

        for name, raw, expected in _cases(cat, grouped):
            got = _turn(page, server, raw)
            assert got["plain"] == expected, (name, raw, got)
            # The record is still there, unchanged: on the title and behind the toggle.
            assert got["rawText"] == raw and got["title"] == raw, (name, got)
            assert got["rawHidden"] is True and got["rawVisible"] is False, (name, got)
            assert got["hasButton"] and got["expanded"] == "false", (name, got)
            assert got["buttonText"] == _t(cat, "details"), (name, got)
            # Truthfulness, independent of the expected strings above: the no-model phrase only
            # ever sits on a line where the server itself said no model wrote the answer, and a
            # line that names tokens is never described as model-free.
            says_no_ai = _t(cat, "no_ai") in got["plain"] or _t(cat, "no_model") in got["plain"]
            server_says_no_model = any(
                seg in ("no model", "no model for the answer", "tool-generated answer") for seg in raw.split(" | ")
            )
            assert says_no_ai == server_says_no_model, (name, raw, got)
            assert not (says_no_ai and " tok" in raw), (name, raw, got)
        assert errors == [], errors
    finally:
        context.close()


def test_unknown_shapes_fail_closed_to_the_raw_line_and_never_become_markup(browser) -> None:
    context, page, server, errors = _open(browser, "en")
    try:
        for raw in UNKNOWN_SHAPES:
            got = _turn(page, server, raw)
            assert got["plain"] is None and got["hasButton"] is False, (raw, got)
            assert got["rawText"] == raw and got["rawVisible"] is True, (raw, got)
            assert raw in got["visibleText"], (raw, got)
            assert got["images"] == 0, (raw, got)
        assert page.evaluate("() => window.__pwned === undefined")
        # The markdown path (a footer line inside streamed answer text) obeys the same rules: a
        # known line gets plain words over an escaped raw span; markup in a line stays text.
        md = page.evaluate(
            """(lines) => lines.map((line) => {
                 const el = document.createElement('div');
                 renderRichText(el, 'Answer.\\n\\n`' + line + '`');
                 const p = el.querySelector('.chat-provenance');
                 const plain = p && p.querySelector('.prov-plain');
                 return { plain: plain ? plain.textContent : null, raw: p ? p.textContent : '',
                          imgs: el.querySelectorAll('img').length };
               })""",
            [_footer(FAST_PATH), UNKNOWN_SHAPES[4]],
        )
        cat = catalog_for("en")
        assert md[0]["plain"] == " · ".join([_t(cat, "quick_reply"), _t(cat, "no_ai")]), md
        assert "smalltalk_fast_path" in md[0]["raw"], md
        assert md[1]["plain"] is None and md[1]["imgs"] == 0 and "<img src=x" in md[1]["raw"], md
        assert page.evaluate("() => window.__pwned === undefined")
        assert errors == [], errors
    finally:
        context.close()


@pytest.mark.parametrize("locale", ["en", "he"])
def test_the_details_toggle_is_a_keyboard_button_that_shows_and_hides_the_raw_line(browser, locale) -> None:
    cat = catalog_for(locale)
    context, page, server, errors = _open(browser, locale)
    try:
        raw = _footer(FAST_PATH)
        _turn(page, server, raw)
        button = page.locator(".msg.assistant:not(.pending) .chat-provenance button.prov-more").last
        button.focus()
        page.keyboard.press("Enter")
        opened = _turn_state(page)
        assert opened["expanded"] == "true" and opened["rawVisible"] is True, opened
        assert opened["buttonText"] == _t(cat, "hide_details"), opened
        assert raw in opened["visibleText"], opened
        assert opened["rawDir"] == "ltr", opened
        page.keyboard.press("Space")
        closed = _turn_state(page)
        assert closed["expanded"] == "false" and closed["rawVisible"] is False, closed
        assert closed["buttonText"] == _t(cat, "details"), closed
        assert raw not in closed["visibleText"], closed
        assert errors == [], errors
    finally:
        context.close()


def _turn_state(page) -> dict:
    return page.evaluate(
        """() => {
          const all = [...document.querySelectorAll('.msg.assistant:not(.pending)')];
          const p = all[all.length - 1].querySelector('.chat-provenance');
          const btn = p.querySelector('button.prov-more');
          const raw = p.querySelector('.prov-raw');
          return { expanded: btn.getAttribute('aria-expanded'), buttonText: btn.textContent,
                   rawVisible: raw.getClientRects().length > 0, visibleText: p.innerText,
                   rawDir: raw.getAttribute('dir') };
        }"""
    )
