"""Search over the transcript and over the Activity panel, driven in a real browser.

Two engines, deliberately different. The transcript is prose someone half-remembers, so it tolerates
typos: "vool xplanation" has to find "VOOL explanation". The Activity panel is machine output --
event names, tool ids, paths, model tags -- where a near miss is worse than no match, so it stays
literal.

The anti-overfit rule for this feature: every query family is driven against a transcript holding
several kinds of content at once (prose, a markdown table, a filename, a pasted log, and a turn's
tool-result card), with sloppy variants and negative controls in the same pass. A search that only
worked on the one sentence it was written against would pass a single-string test and fail here.

The negative controls matter as much as the hits. Searching is a read: it must not send a turn, call
a model, run a tool or write a file, and a one-letter query must not light up the whole chat.
"""

from __future__ import annotations

import json

import pytest

from core.vool_chat_page import render_vool_chat_html
from tests import served_browser

HTML = render_vool_chat_html()

# One turn's worth of real runtime events, carrying every string the Activity family searches for.
LEDGER = [
    {"seq": 1, "event_type": "task_received", "message": "Received request: write the file"},
    {"seq": 2, "event_type": "tool_selected", "message": "Running workspace.write_file"},
    {"seq": 3, "event_type": "tool_executed", "message": "Wrote exact_one_file_6a73.txt"},
    {"seq": 4, "event_type": "model.call_started", "message": "Model call started with ollama-local:qwen2.5:7b."},
    {"seq": 5, "event_type": "model.call_completed", "message": "Model call completed with ollama-local:qwen2.5:7b."},
    {"seq": 6, "event_type": "tool_failed", "message": "workspace.edit_file refused: stale_base"},
    {"seq": 7, "event_type": "task_completed", "message": "Task completed"},
]

# A transcript that is not one sentence: prose, a table, a filename, a pasted log, mixed casing.
SEED = """
(() => {
  logEl.innerHTML = '';
  addMsg('user', 'Give me a VOOL explanation of the Thunder Bay rollout');
  addMsg('assistant', 'Here is the VOOL explanation you asked for. Thunder Bay is covered below.');
  addMsg('assistant', '| Region | Status |\\n| --- | --- |\\n| Thunder Bay | shipped |\\n| Kenora | pending |');
  addMsg('assistant', 'I wrote exact_one_file_6a73.txt into the workspace and left the rest alone.');
  addMsg('assistant', 'Pasted log:\\n\\n    2026-08-12 00:12:03 INFO  workspace.write_file ok\\n    2026-08-12 00:12:04 WARN  stale_base ignored\\n');
  addMsg('user', 'thanks, no further changes');
  return logEl.querySelectorAll('.msg').length;
})()
"""


def _launch():
    # Availability decisions live in the gate-aware helper: under VOOL_GATE a missing browser
    # FAILS the authoritative lane, outside it the long-standing availability skip remains.
    return served_browser.launch_chromium()


def _route(route):
    request = route.request
    if request.resource_type == "document":
        route.fulfill(status=200, content_type="text/html", body=HTML)
        return
    if "/api/runtime/events" in request.url:
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps({"events": LEDGER, "next_after": 7}))
        return
    route.fulfill(status=200, content_type="application/json", body="{}")


@pytest.fixture(scope="module")
def page_ctx():
    ctx, browser = _launch()
    try:
        page = browser.new_page()
        page.on("pageerror", lambda e: None)
        # Every request the page makes, so the negative controls can prove search sends nothing.
        seen: list[tuple[str, str]] = []
        page.on("request", lambda r: seen.append((r.method, r.url)))
        page.route("**/*", _route)
        page.set_viewport_size({"width": 1280, "height": 860})
        page.goto("http://vool.test/chat")
        page.wait_for_timeout(220)
        assert page.evaluate(SEED) == 6
        yield page, seen
    finally:
        browser.close()
        ctx.stop()


def _chat_search(page, query: str) -> dict:
    return page.evaluate(
        """(q) => {
            openChatSearch();
            searchInputEl.value = q;
            const matched = runChatSearch(false);
            const marks = [...logEl.querySelectorAll('mark.cs-hit')];
            return {
              matched,
              count: searchCountEl.textContent,
              markTexts: marks.map((m) => m.textContent),
              turnsHighlighted: logEl.querySelectorAll('.msg:has(mark.cs-hit)').length,
              currentTurn: logEl.querySelectorAll('.cs-current-turn').length,
              barHidden: searchBarEl.hidden,
            };
        }""",
        query,
    )


# ---------------------------------------------------------------- chat: the query family


@pytest.mark.parametrize(
    "query,expect_words",
    [
        ("Thunder", ["thunder"]),                       # exact
        ("thunder", ["thunder"]),                       # lowercase against mixed case
        ("VOOL", ["vool"]),                             # uppercase against mixed case
        ("vool", ["vool"]),                             # lowercase
        ("explanation", ["explanation"]),               # plain word
        ("xplanation", ["explanation"]),                # typo: a dropped leading letter
        ("explanatoin", ["explanation"]),               # typo: a transposition
        ("vool explanation", ["vool", "explanation"]),  # multiword, both must match
        ("vool xplanation", ["vool", "explanation"]),   # multiword with one sloppy term
        ("exact_one_file_6a73.txt", ["exact_one_file_6a73.txt"]),   # identifier kept whole
        ("workspace.write_file", ["workspace.write_file"]),         # dotted tool id in a pasted log
        ("Kenora", ["kenora"]),                         # a markdown table cell
    ],
)
def test_the_chat_query_family_finds_its_text(page_ctx, query, expect_words) -> None:
    page, _ = page_ctx
    result = _chat_search(page, query)
    assert result["matched"] >= 1, f"{query!r} found nothing"
    assert result["turnsHighlighted"] >= 1, f"{query!r} matched but highlighted nothing"
    assert result["currentTurn"] == 1, f"{query!r} did not mark exactly one current turn"
    marks = [m.lower() for m in result["markTexts"]]
    for word in expect_words:
        # The highlight covers the span that matched, which for a typo query is the part of the word
        # the query actually spelled ("xplanation" inside "explanation"). Either direction counts.
        assert any(m in word or word in m for m in marks), (
            f"{query!r} highlighted {result['markTexts']!r}, nothing relates to {word!r}"
        )


def test_a_markdown_table_cell_is_searchable_as_rendered_text(page_ctx) -> None:
    """The table is rendered markup by the time a user reads it, not the pipe syntax they pasted."""
    page, _ = page_ctx
    result = page.evaluate(
        """() => {
            openChatSearch(); searchInputEl.value = 'Kenora'; runChatSearch(false);
            const mark = logEl.querySelector('mark.cs-hit');
            return { tag: mark ? mark.closest('td,th,li,p,div').tagName : null,
                     inTable: !!(mark && mark.closest('table')) };
        }"""
    )
    assert result["inTable"] is True, "the table cell match was not inside the rendered table"


def test_no_result_says_so_and_highlights_nothing(page_ctx) -> None:
    page, _ = page_ctx
    result = _chat_search(page, "zzzznotpresentanywhere")
    assert result["matched"] == 0
    assert result["markTexts"] == []
    assert "no matches" in result["count"]


def test_next_and_previous_cycle_through_every_turn(page_ctx) -> None:
    page, _ = page_ctx
    result = page.evaluate(
        """() => {
            openChatSearch(); searchInputEl.value = 'thunder'; runChatSearch(false);
            const total = chatSearchHits.length;
            const forward = [];
            for (let i = 0; i < total + 1; i++) { forward.push(chatSearchIndex); stepChatSearch(1); }
            runChatSearch(false);                 // back to the first result before walking backwards
            const back = [];
            for (let i = 0; i < total + 1; i++) { back.push(chatSearchIndex); stepChatSearch(-1); }
            return { total, forward, back, count: searchCountEl.textContent };
        }"""
    )
    assert result["total"] >= 2, "need at least two matching turns to prove cycling"
    # Forward visits each index in order and wraps back to the first.
    assert result["forward"][: result["total"]] == list(range(result["total"]))
    assert result["forward"][result["total"]] == 0, "next did not wrap to the first result"
    assert result["back"][1] == result["total"] - 1, "previous did not wrap to the last result"
    assert result["count"].strip().endswith(str(result["total"]))


def test_clear_removes_every_mark_and_the_count(page_ctx) -> None:
    page, _ = page_ctx
    result = page.evaluate(
        """() => {
            openChatSearch(); searchInputEl.value = 'thunder'; runChatSearch(false);
            const before = logEl.querySelectorAll('mark.cs-hit').length;
            clearChatSearch();
            return { before, after: logEl.querySelectorAll('mark.cs-hit').length,
                     current: logEl.querySelectorAll('.cs-current-turn').length,
                     count: searchCountEl.textContent, value: searchInputEl.value };
        }"""
    )
    assert result["before"] > 0
    assert result["after"] == 0, "clear left highlights behind"
    assert result["current"] == 0
    assert result["value"] == ""


def test_closing_search_restores_the_transcript_exactly(page_ctx) -> None:
    """Highlighting rewrites text nodes, so closing has to put the message back byte for byte."""
    page, _ = page_ctx
    result = page.evaluate(
        """() => {
            const before = [...logEl.querySelectorAll('.msg-text')].map((n) => n.textContent);
            const rawBefore = [...logEl.querySelectorAll('.msg-text')].map((n) => n.dataset.raw || '');
            openChatSearch(); searchInputEl.value = 'thunder'; runChatSearch(false);
            closeChatSearch();
            const after = [...logEl.querySelectorAll('.msg-text')].map((n) => n.textContent);
            const rawAfter = [...logEl.querySelectorAll('.msg-text')].map((n) => n.dataset.raw || '');
            return { same: JSON.stringify(before) === JSON.stringify(after),
                     rawSame: JSON.stringify(rawBefore) === JSON.stringify(rawAfter),
                     marks: logEl.querySelectorAll('mark.cs-hit').length,
                     hidden: searchBarEl.hidden };
        }"""
    )
    assert result["same"] is True, "the transcript text changed after a search round trip"
    assert result["rawSame"] is True, "the raw text behind Copy was altered by highlighting"
    assert result["marks"] == 0
    assert result["hidden"] is True


# ---------------------------------------------------------------- chat: negative controls


@pytest.mark.parametrize("query", ["a", "e", "o", " ", "  "])
def test_a_one_character_query_matches_nothing_at_all(page_ctx, query) -> None:
    """"a" appears in nearly every message; treating it as a hit lights up the whole chat."""
    page, _ = page_ctx
    result = _chat_search(page, query)
    assert result["matched"] == 0, f"{query!r} matched {result['matched']} turns"
    assert result["markTexts"] == []


@pytest.mark.parametrize("query", ["vool zzzznotpresent", "thunder nonexistentword"])
def test_every_word_must_match_not_just_one(page_ctx, query) -> None:
    """Multiword is AND. One real term plus one absent term is not a hit."""
    page, _ = page_ctx
    assert _chat_search(page, query)["matched"] == 0


@pytest.mark.parametrize("query", ["cool", "wool", "tool", "pool"])
def test_a_short_word_does_not_fuzzy_match_its_neighbours(page_ctx, query) -> None:
    """At length four a single edit reaches "vool" from all of these -- that is a false positive."""
    page, _ = page_ctx
    assert _chat_search(page, query)["matched"] == 0, f"{query!r} was fuzzed into a match"


def test_searching_never_sends_a_turn_or_calls_anything(page_ctx) -> None:
    page, seen = page_ctx
    before = len(seen)
    page.evaluate(
        """() => {
            openChatSearch();
            for (const q of ['thunder', 'xplanation', 'vool explanation', 'zzz']) {
              searchInputEl.value = q; runChatSearch(false); stepChatSearch(1); stepChatSearch(-1);
            }
            clearChatSearch();
        }"""
    )
    page.wait_for_timeout(200)
    new_requests = seen[before:]
    assert new_requests == [], f"searching issued requests: {new_requests}"


def test_enter_in_the_search_box_does_not_send_the_composer(page_ctx) -> None:
    page, seen = page_ctx
    before = len(seen)          # the page's own boot traffic is not this test's subject
    result = page.evaluate(
        """() => {
            openChatSearch();
            inputEl.value = 'THIS MUST NOT BE SENT';
            searchInputEl.value = 'thunder';
            runChatSearch(false);
            const before = logEl.querySelectorAll('.msg').length;
            searchInputEl.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true, cancelable:true}));
            return { before, after: logEl.querySelectorAll('.msg').length, composer: inputEl.value };
        }"""
    )
    assert result["after"] == result["before"], "Enter in the search box appended a message"
    assert result["composer"] == "THIS MUST NOT BE SENT", "the composer was cleared as if sent"
    page.wait_for_timeout(150)
    posted = [(m, u) for m, u in seen[before:] if m != "GET"]
    assert posted == [], f"Enter in the search box issued {posted}"


# ---------------------------------------------------------------- activity: literal search


def _activity_search(page, query: str) -> dict:
    return page.evaluate(
        """(q) => {
            document.getElementById('xpSearchInput').value = q;
            const matched = runActivitySearch(false);
            return {
              matched,
              count: document.getElementById('xpSearchCount').textContent,
              markTexts: [...xpBodyEl.querySelectorAll('mark.cs-hit')].map((m) => m.textContent),
              rows: xpBodyEl.querySelectorAll('.as-hit').length,
              current: xpBodyEl.querySelectorAll('.as-current').length,
            };
        }""",
        query,
    )


@pytest.fixture(scope="module")
def activity_page(page_ctx):
    page, seen = page_ctx
    page.evaluate(
        """async () => {
            await loadRecoveredLedger();
            document.body.classList.add('panel-open');
            panelTab = 'Event log';
            buildTabs();
            renderPanel();
        }"""
    )
    page.wait_for_timeout(200)
    rows = page.evaluate("() => xpBodyEl.querySelectorAll('.xp-row').length")
    assert rows >= 5, f"the Activity body did not render the seeded ledger (rows={rows})"
    return page, seen


@pytest.mark.parametrize(
    "query",
    [
        "workspace.write_file",
        "exact_one_file_6a73.txt",
        "model.call_started",
        "stale_base",
        "qwen2.5",
        "ollama-local",
        "tool_failed",
    ],
)
def test_the_activity_query_family_finds_its_rows(activity_page, query) -> None:
    page, _ = activity_page
    result = _activity_search(page, query)
    assert result["matched"] >= 1, f"{query!r} found no event row"
    assert result["rows"] >= 1, f"{query!r} matched but highlighted no row"
    assert result["current"] == 1
    assert query.lower() in " ".join(result["markTexts"]).lower()


@pytest.mark.parametrize("query", ["QWEN2.5", "Workspace.Write_File", "STALE_BASE"])
def test_activity_search_ignores_case(activity_page, query) -> None:
    page, _ = activity_page
    assert _activity_search(page, query)["matched"] >= 1, f"{query!r} missed on case alone"


def test_activity_search_stays_literal_and_does_not_fuzz(activity_page) -> None:
    """Activity is machine output: "stale_bse" is a different string, not a near miss to forgive."""
    page, _ = activity_page
    assert _activity_search(page, "stale_bse")["matched"] == 0
    assert _activity_search(page, "workspce.write_file")["matched"] == 0


def test_activity_no_result_and_one_character_controls(activity_page) -> None:
    page, _ = activity_page
    empty = _activity_search(page, "zzzznotpresentanywhere")
    assert empty["matched"] == 0
    assert "none" in empty["count"]
    for tiny in ("a", "e", "_"):
        assert _activity_search(page, tiny)["matched"] == 0, f"{tiny!r} lit up the panel"


def test_activity_clear_restores_the_panel_text(activity_page) -> None:
    page, _ = activity_page
    result = page.evaluate(
        """() => {
            const before = xpBodyEl.textContent;
            document.getElementById('xpSearchInput').value = 'qwen2.5';
            runActivitySearch(false);
            const marked = xpBodyEl.querySelectorAll('mark.cs-hit').length;
            clearActivitySearch();
            return { marked, same: xpBodyEl.textContent === before,
                     marks: xpBodyEl.querySelectorAll('mark.cs-hit').length,
                     hits: xpBodyEl.querySelectorAll('.as-hit').length };
        }"""
    )
    assert result["marked"] > 0
    assert result["same"] is True, "clearing the Activity filter changed the panel text"
    assert result["marks"] == 0 and result["hits"] == 0


def test_activity_next_and_previous_cycle(activity_page) -> None:
    page, _ = activity_page
    result = page.evaluate(
        """() => {
            document.getElementById('xpSearchInput').value = 'ollama-local';
            runActivitySearch(false);
            const total = activityHits.length;
            const seen = [];
            for (let i = 0; i < total + 1; i++) { seen.push(activityIndex); stepActivitySearch(1); }
            return { total, seen };
        }"""
    )
    assert result["total"] >= 2
    assert result["seen"][: result["total"]] == list(range(result["total"]))
    assert result["seen"][result["total"]] == 0, "Activity next did not wrap"


def test_activity_search_survives_the_panel_re_rendering(activity_page) -> None:
    """renderPanelBody replaces the body wholesale; an active filter must come back with it."""
    page, _ = activity_page
    result = page.evaluate(
        """async () => {
            document.getElementById('xpSearchInput').value = 'qwen2.5';
            runActivitySearch(false);
            const before = xpBodyEl.querySelectorAll('.as-hit').length;
            renderPanel();                       // wipes and rebuilds the body
            await new Promise((r) => setTimeout(r, 120));
            return { before, after: xpBodyEl.querySelectorAll('.as-hit').length };
        }"""
    )
    assert result["before"] > 0
    assert result["after"] == result["before"], "the filter was lost when the panel re-rendered"


def test_both_searches_are_independent(activity_page) -> None:
    """The Activity filter must not paint the transcript, and the chat search must not paint events."""
    page, _ = activity_page
    result = page.evaluate(
        """() => {
            clearChatSearch(); clearActivitySearch();
            document.getElementById('xpSearchInput').value = 'qwen2.5';
            runActivitySearch(false);
            const chatMarksFromActivity = logEl.querySelectorAll('mark.cs-hit').length;
            clearActivitySearch();
            openChatSearch(); searchInputEl.value = 'thunder'; runChatSearch(false);
            const panelMarksFromChat = xpBodyEl.querySelectorAll('mark.cs-hit').length;
            clearChatSearch();
            return { chatMarksFromActivity, panelMarksFromChat };
        }"""
    )
    assert result["chatMarksFromActivity"] == 0
    assert result["panelMarksFromChat"] == 0


# ---------------------------------------------------------------- performance and layout


def test_a_large_transcript_still_searches_promptly(page_ctx) -> None:
    """A pasted-log chat is the shape that stalls a naive scan: 400 turns of dense text."""
    page, _ = page_ctx
    result = page.evaluate(
        """() => {
            const keep = logEl.innerHTML;
            logEl.innerHTML = '';
            const line = 'workspace.write_file ok 2026-08-12 INFO some fairly long pasted log line ';
            for (let i = 0; i < 400; i++) addMsg('assistant', line.repeat(12) + ' marker' + i);
            const started = performance.now();
            openChatSearch(); searchInputEl.value = 'xplanation'; runChatSearch(false);   // worst case: fuzzy, no hit
            const fuzzyMs = performance.now() - started;
            const t2 = performance.now();
            searchInputEl.value = 'marker399'; runChatSearch(false);                      // exact, one hit
            const exactMs = performance.now() - t2;
            const hits = chatSearchHits.length;
            clearChatSearch(); closeChatSearch();
            logEl.innerHTML = keep;
            return { fuzzyMs, exactMs, hits, turns: 400 };
        }"""
    )
    assert result["hits"] == 1, "the exact query should match exactly one of the 400 turns"
    assert result["fuzzyMs"] < 2000, f"fuzzy scan over 400 dense turns took {result['fuzzyMs']}ms"
    assert result["exactMs"] < 1000, f"exact scan over 400 dense turns took {result['exactMs']}ms"


def test_the_search_bar_costs_no_height_until_it_is_opened(page_ctx) -> None:
    page, _ = page_ctx
    result = page.evaluate(
        """() => {
            closeChatSearch();
            const closed = document.getElementById('searchBar').getBoundingClientRect().height;
            const logClosed = logEl.getBoundingClientRect().height;
            openChatSearch();
            const open = document.getElementById('searchBar').getBoundingClientRect().height;
            closeChatSearch();
            return { closed, open, logClosed };
        }"""
    )
    assert result["closed"] == 0, "the search bar takes height while closed"
    assert 0 < result["open"] <= 56, f"the search bar is not compact: {result['open']}px"


def test_the_search_row_does_not_displace_the_activity_scope_row() -> None:
    """The scope row must stay directly beneath the Activity header (test_activity_evidence_controls)."""
    head = HTML.index('<div class="xp-head">')
    scope_row = HTML.index('<div class="xp-scope-row">', head)
    between = HTML[HTML.index("</div>", head) + len("</div>") : scope_row].strip()
    assert between == "", f"the search row displaced the scope row: {between!r}"
    # The Activity search sits with the body it filters, after the tabs.
    tabs = HTML.index('<div class="xp-tabs"', scope_row)
    search_row = HTML.index('<div class="xp-search-row"', tabs)
    body = HTML.index('<div class="xp-body"', search_row)
    assert tabs < search_row < body
