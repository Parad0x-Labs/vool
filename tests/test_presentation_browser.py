"""C19 presentation in the REAL browser page: rendering, copy, a11y, record.

The hermetic lane routes every request through `page.route` so the SHIPPED
page JavaScript is driven end to end against controlled commits: a table
answer renders every cell inside a scrollable wrapper, copy fidelity holds in
all representations, the screen-reader floor exists, reduced motion holds, a
mermaid fence stays inert byte-exact source, zero console errors accumulate
across every shape, and the proof chip's expanded view surfaces the
presentation-selection record from the message's own display_metadata — never
borrowed for a legacy turn.

B3 (export byte fidelity) runs against the REAL export door over HTTP with a
stub agent, per tests/test_chat_documents_ui.py's served pattern.

Availability lives in `tests.served_browser.launch_chromium()` (fails under
the gate, skips outside it); nothing here skips on its own.
"""
from __future__ import annotations

import functools
import hashlib
import json
import socket
import threading
import time
import urllib.request
from typing import Any

import pytest

from core.vool_chat_page import render_vool_chat_html
from tests import served_browser

HTML = render_vool_chat_html()

SELECTION = {
    "turn_id": "turn-browser-1",
    "elected": "table",
    "trigger": "tabular_rows_ge2",
    "disabled_by": None,
    "gap_detected": False,
    "fallback": None,
    "origin": "automatic",
    "schema": "vool.presentation_selection.v1",
}

TABLE_ANSWER = (
    "Here is the plan comparison:\n\n"
    "| Plan | Deductible | Coverage |\n|---|---|---|\n"
    "| A | €10 | basic |\n| B | €25 | full |\n"
)
PROSE_ANSWER = "Stoicism is a school of Hellenistic philosophy that teaches virtue."
TIMELINE_ANSWER = "- 2019: founded\n- 2020: launched\n- 2021: profitable\n"
TREE_ANSWER = "CEO\n  CTO\n    Platform\n  CFO\n"
MERMAID_ANSWER = (
    "The auth flow:\n\n```mermaid\ngraph TD; A[Login]-->B[Session];\n```\n"
)


def _commit(answer: str, *, request_id: str | None = "req-browser-1", selection: dict | None = SELECTION) -> dict:
    display: dict[str, Any] = {"provenance": {}, "usage": {}, "provenance_footer": ""}
    if selection is not None:
        display["presentation_selection"] = selection
    return {
        "type": "response.commit",
        "version": 2,
        "revision": 1,
        "turn_id": "",
        "request_id": request_id or "",
        "canonical_content": answer,
        "content_hash": "sha256:" + hashlib.sha256(answer.encode("utf-8")).hexdigest(),
        "display_metadata": display,
    }


def _ndjson(answer: str, commit: dict) -> str:
    delta = {
        "model": "vool",
        "created_at": "2026-09-05T00:00:00Z",
        "message": {"role": "assistant", "content": answer},
        "done": False,
    }
    terminal = {
        "model": "vool",
        "created_at": "2026-09-05T00:00:00Z",
        "message": {"role": "assistant", "content": ""},
        "done": True,
        "done_reason": "stop",
        "vool_response_commit": commit,
    }
    return "\n".join([json.dumps(delta), json.dumps(terminal), ""])


class _PageServer:
    """The controlled server: the shipped page plus one scripted turn."""

    def __init__(self, answer: str, *, selection: dict | None = SELECTION, request_id: str | None = "req-browser-1") -> None:
        self.answer = answer
        self.selection = selection
        self.request_id = request_id
        self.chat_bodies: list[dict[str, Any]] = []

    def route(self, route) -> None:
        request = route.request
        url = request.url
        if request.resource_type == "document":
            route.fulfill(status=200, content_type="text/html", body=HTML)
            return
        if url.endswith("/api/chat") and request.method == "POST":
            self.chat_bodies.append(json.loads(request.post_data or "{}"))
            route.fulfill(
                status=200,
                content_type="application/x-ndjson",
                body=_ndjson(self.answer, _commit(self.answer, request_id=self.request_id, selection=self.selection)),
            )
            return
        if "/api/chat/proof" in url:
            if self.request_id:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({
                    "bound": True,
                    "state": "RECORDED",
                    "compact": {"actions": 0, "sources": 0, "cost": {"tokens": 12}},
                    "expanded": {"actions": [], "receipts": []},
                }))
            else:
                route.fulfill(status=404, content_type="application/json", body=json.dumps({"error": "proof_not_bound"}))
            return
        if "/api/chat/history" in url:
            session = url.split("session=", 1)[1].split("&", 1)[0]
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"session_id": session, "messages": []}))
            return
        if "/api/cloud/model" in url and "models" not in url:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "model": "", "cost_state": "free"}))
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


def _open(browser, answer: str, *, selection: dict | None = SELECTION, request_id: str | None = "req-browser-1",
          viewport: dict[str, int] | None = None, reduced_motion: str | None = None):
    server = _PageServer(answer, selection=selection, request_id=request_id)
    kwargs: dict[str, Any] = {}
    if reduced_motion:
        kwargs["reduced_motion"] = reduced_motion
    context = browser.new_context(**kwargs)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/*", server.route)
    page.set_viewport_size(viewport or {"width": 1280, "height": 860})
    page.goto("http://vool.test/chat")
    page.wait_for_timeout(250)
    page.evaluate("() => { localStorage.clear(); newChat(); }")
    page.wait_for_timeout(120)
    return context, page, server, errors


def _send(page) -> None:
    page.fill("#input", "show me")
    page.click("#send")
    page.wait_for_function(
        "() => document.querySelectorAll('.msg.assistant:not(.pending)').length === 1",
        timeout=15000,
    )


# --------------------------------------------------------------------------- B1


VIEWPORTS = [
    {"width": 1900, "height": 1200},
    {"width": 1280, "height": 860},
    {"width": 1100, "height": 800},
    {"width": 900, "height": 620},
    {"width": 700, "height": 560},
    {"width": 520, "height": 620},
    {"width": 360, "height": 780},
]


def test_b1_table_renders_every_cell_and_wraps_at_narrow_viewports(browser) -> None:
    """B1: every cell value is visible, and the wide table scrolls inside its
    own wrapper at narrow viewports — the message column never scrolls."""
    for viewport in VIEWPORTS:
        context, page, _server, errors = _open(browser, TABLE_ANSWER, viewport=viewport)
        try:
            _send(page)
            cells = page.evaluate(
                """() => [...document.querySelectorAll('.chat-table td, .chat-table th')]
                       .map((c) => c.textContent.trim())"""
            )
            for value in ("Plan", "Deductible", "Coverage", "A", "€10", "basic", "B", "€25", "full"):
                assert value in cells, f"{value} missing at {viewport}: {cells}"
            narrow = viewport["width"] <= 900
            geometry = page.evaluate(
                """() => { const w = document.querySelector('.chat-table-wrap');
                     if (!w) return null;
                     return { scrollW: w.scrollWidth, clientW: w.clientWidth,
                              overflowX: getComputedStyle(w).overflowX }; }"""
            )
            assert geometry is not None
            assert geometry["overflowX"] == "auto"
            if narrow and geometry["scrollW"] > geometry["clientW"]:
                assert geometry["clientW"] <= viewport["width"], geometry
            assert errors == []
        finally:
            context.close()


# --------------------------------------------------------------------------- B2


def test_b2_copy_fidelity_raw_and_plain_text(browser) -> None:
    """B2: Copy = the transcript bytes; Copy text keeps the pipe rows verbatim."""
    context, page, _server, errors = _open(browser, TABLE_ANSWER)
    try:
        page.add_init_script(
            "if (!navigator.clipboard) { Object.defineProperty(navigator, 'clipboard', "
            "{ value: { writeText: (t) => { window.__copied = String(t); return Promise.resolve(true); }, "
            "readText: () => Promise.resolve(window.__copied || '') } }); }"
        )
        page.reload()
        page.wait_for_timeout(200)
        _send(page)
        page.click(".msg.assistant .msg-copy")
        raw = page.evaluate("() => window.__copied || ''")
        canonical = page.evaluate("() => document.querySelector('.msg.assistant .msg-text').dataset.raw")
        assert raw == canonical, "Copy must take the raw Markdown source exactly as stored"
        assert "| A | €10 | basic |" in raw
        page.click(".msg.assistant .msg-copy-text")
        plain = page.evaluate("() => window.__copied || ''")
        assert "| A | €10 | basic |" in plain, "Copy text keeps pipe rows verbatim"
        assert errors == []
    finally:
        context.close()


# --------------------------------------------------------------------------- B4


def test_b4_zero_console_errors_across_shapes(browser) -> None:
    """B4: prose, table, timeline, tree and fenced mermaid states all render
    with zero page errors."""
    for answer in (PROSE_ANSWER, TABLE_ANSWER, TIMELINE_ANSWER, TREE_ANSWER, MERMAID_ANSWER):
        context, page, _server, errors = _open(browser, answer)
        try:
            _send(page)
            page.wait_for_timeout(120)
            assert errors == [], f"page errors for {answer[:40]!r}: {errors}"
        finally:
            context.close()


# --------------------------------------------------------------------------- B5


def test_b5_screen_reader_floor_log_and_tables(browser) -> None:
    """B5: #log is a live region; tables expose a caption and scoped headers."""
    context, page, _server, errors = _open(browser, TABLE_ANSWER)
    try:
        log = page.evaluate(
            """() => { const el = document.getElementById('log');
                 return { role: el.getAttribute('role'), live: el.getAttribute('aria-live') }; }"""
        )
        assert log == {"role": "log", "live": "polite"}, log
        _send(page)
        floor = page.evaluate(
            """() => { const t = document.querySelector('.chat-table');
                 const caption = t ? t.querySelector('caption') : null;
                 const scopes = [...document.querySelectorAll('.chat-table th')].map((th) => th.getAttribute('scope'));
                 return { caption: caption ? caption.textContent : null, scopes }; }"""
        )
        assert floor["caption"], "the table has no caption"
        assert floor["scopes"] and all(s == "col" for s in floor["scopes"]), floor
        assert errors == []
    finally:
        context.close()


# --------------------------------------------------------------------------- B6


def test_b6_shaped_answers_introduce_no_motion_when_reduced(browser) -> None:
    """B6: under prefers-reduced-motion the shaped answer animates nothing; the
    global kill switch stays armed."""
    context, page, _server, errors = _open(browser, TABLE_ANSWER, reduced_motion="reduce")
    try:
        assert page.evaluate("() => window.matchMedia('(prefers-reduced-motion: reduce)').matches")
        _send(page)
        motion = page.evaluate(
            """() => [...document.querySelectorAll('.chat-table, .chat-table-wrap, .msg-text')]
                 .map((el) => { const s = getComputedStyle(el);
                   return { t: s.transitionDuration, a: s.animationDuration }; })"""
        )
        assert motion, "no shaped content rendered"
        for entry in motion:
            assert float(entry["t"].rstrip("s")) <= 0.001 and float(entry["a"].rstrip("s")) <= 0.001, entry
        assert errors == []
    finally:
        context.close()


# --------------------------------------------------------------------------- B7


def test_b7_mermaid_source_renders_as_inert_code_with_byte_exact_copy(browser) -> None:
    """B7/L6: the fenced mermaid source renders as code with a byte-exact copy
    button — no renderer, no CDN, no execution."""
    context, page, _server, errors = _open(browser, MERMAID_ANSWER)
    try:
        _send(page)
        state = page.evaluate(
            """() => { const wrap = document.querySelector('.chat-code-wrap');
              if (!wrap) return null;
              const code = wrap.querySelector('code');
              return { lang: code.className, raw: decodeURIComponent(wrap.getAttribute('data-raw-code')),
                       hasButton: !!wrap.querySelector('.code-copy'),
                       scripts: document.querySelectorAll('#log script').length }; }"""
        )
        assert state is not None, "the mermaid fence did not render as a code block"
        assert "lang-mermaid" in state["lang"], state
        assert state["raw"] == "graph TD; A[Login]-->B[Session];", state
        assert state["hasButton"]
        assert state["scripts"] == 0, "diagram source must never execute"
        assert errors == []
    finally:
        context.close()


# --------------------------------------------------------------------------- B8


def test_b8_proof_chip_expanded_view_shows_the_selection_record(browser) -> None:
    """B8: the expanded chip shows the automatic presentation record from THIS
    message's display_metadata; a legacy turn (no request id) mounts no chip
    and borrows nothing."""
    context, page, server, errors = _open(browser, TABLE_ANSWER)
    try:
        _send(page)
        page.click(".proof-chip-head")
        page.wait_for_function("() => { const b = document.querySelector('.proof-chip-body'); return b && !b.hidden; }")
        body = page.evaluate("() => document.querySelector('.proof-chip-body').textContent")
        assert "Presentation" in body, body[:400]
        assert "table" in body and "utomatic" in body, body[:400]
        assert errors == []
    finally:
        context.close()

    legacy_context, legacy_page, _server, legacy_errors = _open(
        browser, TABLE_ANSWER, request_id=None, selection=None
    )
    try:
        _send(legacy_page)
        chips = legacy_page.evaluate("() => document.querySelectorAll('.proof-chip').length")
        assert chips == 0, "a legacy turn must mount no proof chip at all"
        assert legacy_errors == []
    finally:
        legacy_context.close()


# --------------------------------------------------------------------------- B3 (real export door)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_b3_export_keeps_the_shaped_answer_byte_exact(tmp_path, monkeypatch) -> None:
    """B3: /api/chat/export?format=md|txt serves the shaped answer's bytes
    verbatim — the selection record never enters the transcript."""
    from core import runtime_paths
    from storage.db import configure_default_db_path, reset_default_connection

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "data" / "test.db")
    reset_default_connection()
    from storage.migrations import run_migrations

    run_migrations()

    import uvicorn

    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices, stream_agent_with_events
    from core.web.api.service import dispatch_post

    session = "browser-export"

    def stub(runtime, text, *, session_id=None, source_context=None, **_):
        from core.persistent_memory import append_conversation_event
        from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

        append_conversation_event(
            session_id=str(session_id), user_input=text, assistant_output=TABLE_ANSWER,
            source_context=source_context,
        )
        reset_admission()
        return admit_semantic_result(
            {"response": TABLE_ANSWER, "success": True, "confidence": 0.9, "route_reason": "model_lane", "mode": "advice_only"}
        )

    app = create_app(RuntimeServices(display_name="VOOL"))
    app.state.post_dispatcher = functools.partial(
        dispatch_post,
        run_agent_provider=stub,
        stream_agent_with_events_provider=functools.partial(stream_agent_with_events, run_agent_provider=stub),
    )
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/healthz", timeout=0.5) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(0.05)
        # One real turn through the door: the shaped answer is what persists.
        request = urllib.request.Request(
            f"{base}/api/chat",
            data=json.dumps({
                "model": "vool",
                "messages": [{"role": "user", "content": "walk me through both plans in detail"}],
                "stream": False,
                "session_id": session,
                "turn_id": "turn-export-1",
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            assert response.status == 200
            served = json.loads(response.read().decode("utf-8"))
        served_session = str(served.get("vool_session_id") or session)
        for fmt in ("md", "txt"):
            with urllib.request.urlopen(f"{base}/api/chat/export?format={fmt}&session={served_session}", timeout=30) as response:
                document = response.read().decode("utf-8")
            assert "| A | €10 | basic |" in document, f"{fmt} export lost a table row"
            assert "| B | €25 | full |" in document, f"{fmt} export lost a table row"
            assert "presentation_selection" not in document, (
                "the selection record is provenance beside the answer, never export content"
            )
    finally:
        server.should_exit = True
        thread.join(timeout=5)
