"""C3 — the council card lives in the chat, and is DRIVEN to prove it.

Every assertion here comes from executing the served page under node and inspecting the
node tree the card actually built. Nothing greps the source: a string in a file proves
the string is in the file, and the defects this suite exists to catch — a card that is
never inserted, a requested model shown as the answer, a report body that cannot be
opened, a card that dies on chat switch, a composer left usable while the council owns
the global model pin — all pass a source grep and fail a real drive.

The stubs are servers, not answers: ``/api/council/status`` and ``/api/council/events``
return the shapes the real endpoints return, and the card has to read them.
"""

from __future__ import annotations

import functools
import json
import re

import pytest

from core.vool_chat_page import render_vool_chat_html
from tests.chat_page_js_harness import DOM, run_node

HTML = render_vool_chat_html(build_commit="c3-card")
CHAT = "openclaw:c3c3c3c3c3c3c3c3c3c3"
OTHER = "openclaw:d4d4d4d4d4d4d4d4d4d4"
RUN = "council-abc123def456"

# A report body that is hostile on purpose: if anything on this path ever assigns text
# through innerHTML, this string becomes live markup in the operator's chat.
HOSTILE_REPORT = (
    "DIAGNOSIS: <script>window.__pwned = true;</script> & \"quoted\" 'single' <b>bold</b>\n"
    "VERDICT: AGREE"
)


def page_scripts() -> list[str]:
    return re.findall(r"<script[^>]*>(.*?)</script>", HTML, re.DOTALL)


def _state(**over):
    state = {
        "run_id": RUN,
        "state": "round_open",
        "problem": "the council result never reaches the chat",
        "chat_session": CHAT,
        "max_rounds": 5,
        "started_at": 1000.0,
        "updated_at": 1240.0,
        "round_no": 2,
        "phase": "adjudicate",
        "candidate": "the run state never reached the chat log",
        "seats": [
            {"seat_id": "s1", "role_id": "builder", "model": "vendor/alpha", "votes": True},
            {"seat_id": "s2", "role_id": "falsifier", "model": "vendor/beta", "votes": True},
            {"seat_id": "s3", "role_id": "verifier", "model": "vendor/gamma", "votes": False},
        ],
        "rounds": [
            {"round_no": 1, "reports": [
                {"seat_id": "s1", "round_no": 1, "status": "landed", "verdict": None,
                 "receipt_count": 2, "attempts": 1, "retries_used": 0, "outcome": "VALID",
                 "model_requested": "vendor/alpha", "model_actual": "vendor/alpha-0125",
                 "model_evidence": "actual_adapter", "counterexample": None,
                 "text_ref": {"kind": "ledger", "seq": 3}},
                {"seat_id": "s2", "round_no": 1, "status": "landed", "verdict": None,
                 "receipt_count": 1, "attempts": 2, "retries_used": 1, "outcome": "VALID",
                 "model_requested": "vendor/beta", "model_actual": None,
                 "model_evidence": "unknown",
                 "counterexample": "the fix never ran on the reload path",
                 "text_ref": {"kind": "ledger", "seq": 6}},
                {"seat_id": "s3", "round_no": 1, "status": "failed", "verdict": None,
                 "receipt_count": 0, "attempts": 2, "retries_used": 1, "outcome": "TIMED_OUT",
                 "failure": "TimeoutError: seat read timed out",
                 "model_requested": "vendor/gamma", "model_actual": None,
                 "model_evidence": "unknown", "text_ref": None},
            ]},
            {"round_no": 2, "reports": [
                {"seat_id": "s1", "round_no": 2, "status": "landed", "verdict": "AGREE",
                 "receipt_count": 1, "attempts": 1, "retries_used": 0, "outcome": "VALID",
                 "model_requested": "vendor/alpha", "model_actual": "vendor/alpha-0125",
                 "model_evidence": "actual_adapter", "counterexample": None,
                 "text_ref": {"kind": "ledger", "seq": 9}},
            ]},
        ],
        "outcome": {},
    }
    state.update(over)
    return state


TERMINAL = _state(
    state="converged",
    outcome={
        "result": "adjudicated", "agree": 2, "disagree": 0, "rounds": 2,
        "candidate": "the run state never reached the chat log",
        "authority_note": "adjudication only — promotion, merge, and spend remain with the operator",
    },
)

LEDGER = {
    3: {"seq": 3, "type": "seat_report", "seat_id": "s1", "text": "DIAGNOSIS: the card never reached the chat."},
    6: {"seq": 6, "type": "seat_report", "seat_id": "s2", "text": "COUNTEREXAMPLE: the fix never ran on the reload path"},
    9: {"seq": 9, "type": "seat_report", "seat_id": "s1", "text": HOSTILE_REPORT},
}

# Node-side helpers: a tree walker so a test can look at what was actually BUILT.
HELPERS = r"""
function walk(node, out) {
  out = out || [];
  var kids = node && node.children ? Array.prototype.slice.call(node.children) : [];
  for (var i = 0; i < kids.length; i++) { out.push(kids[i]); walk(kids[i], out); }
  return out;
}
function all(node, pred) { return walk(node).filter(pred); }
function one(node, pred) { var m = all(node, pred); return m.length ? m[0] : null; }
// Matched on className, the attribute the card actually writes -- the shared DOM stub's
// classList is only populated by add/remove, so contains() would silently never match.
function cls(name) {
  return function (el) { return String((el && el.className) || '').split(/\s+/).indexOf(name) >= 0; };
}
function tag(name) { return function (el) { return el.tagName === String(name).toUpperCase(); }; }
function textOf(el) { return el ? String(el.textContent || '') : ''; }
function cardFor(runId) {
  return one(logEl, function (el) { return el.dataset && el.dataset.councilRun === runId; });
}
// The harness unrefs every timer so a page's own polls cannot hold node open. A test's
// own wait has to be re-ref'd, or node exits before the awaited work lands and the drive
// reports nothing at all.
function sleep(ms) {
  return new Promise(function (r) { var t = setTimeout(r, ms); if (t && t.ref) t.ref(); });
}
"""

# One fetch router shared by every drive. `__fetchLog` records what the card asked for,
# so "did it go through text_ref?" is answered by the request it made, not by a guess.
ROUTER_TEMPLATE = """
globalThis.__fetchLog = [];
var STATUS = __STATUS__;
var LEDGER = __LEDGER__;
var RUNS = __RUNS__;
var SCORECARD = __SCORECARD__;
globalThis.__statusMissing = false;
globalThis.__lockOverride = null;
globalThis.__actionRefusal = null;
globalThis.__posted = [];
globalThis.fetch = async function (url, init) {
  var u = String(url);
  globalThis.__fetchLog.push(u);
  function reply(status, payload) {
    return { ok: status < 400, status: status, headers: { get: function () { return null; } },
             json: async function () { return payload; }, text: async function () { return JSON.stringify(payload); } };
  }
  if (u.indexOf('/api/council/status') === 0) {
    if (globalThis.__statusMissing) return reply(404, { ok: false, error: 'no such council run' });
    return reply(200, { ok: true, live: STATUS.state === 'round_open', run: STATUS });
  }
  if (u.indexOf('/api/council/events') === 0) {
    if (globalThis.__statusMissing) return reply(404, { ok: false, error: 'no such council run' });
    var m = /[?&]seq=(\\d+)/.exec(u);
    if (m) {
      var row = LEDGER[m[1]];
      if (!row) return reply(404, { ok: false, error: 'no event at seq' });
      return reply(200, { ok: true, run_id: '__RUN__', events: [row], seq: Number(m[1]), text_verified: true });
    }
    return reply(200, { ok: true, events: [], next_after: 0, has_more: false, complete: true });
  }
  if (u.indexOf('/api/council/seat') === 0 || u.indexOf('/api/council/resume') === 0) {
    var sent = {};
    try { sent = JSON.parse((init && init.body) || '{}'); } catch (e) { sent = {}; }
    globalThis.__posted = globalThis.__posted || [];
    globalThis.__posted.push({ url: u, body: sent });
    if (globalThis.__actionRefusal) return reply(409, globalThis.__actionRefusal);
    return reply(200, { ok: true, run_id: '__RUN__', changed: true, state: STATUS.state });
  }
  if (u.indexOf('/api/council/scorecard') === 0) {
    if (!SCORECARD || !SCORECARD.run) return reply(404, { ok: false, error: 'no scorecard' });
    return reply(200, { ok: true, run_id: '__RUN__', scorecard: SCORECARD });
  }
  if (u.indexOf('/api/council/lock') === 0) {
    if (globalThis.__lockOverride) return reply(200, globalThis.__lockOverride);
    var live = !globalThis.__statusMissing && STATUS.state === 'round_open';
    return reply(200, live
      ? { ok: true, locked: true, run_id: '__RUN__', state: STATUS.state,
          reason: 'Council in session: it owns the machine model pin until the run ends.' }
      : { ok: true, locked: false, run_id: '', state: '', reason: '' });
  }
  if (u.indexOf('/api/council/runs') === 0) return reply(200, { ok: true, runs: RUNS });
  if (u.indexOf('/api/council/convene') === 0) return reply(200, { ok: true, run_id: '__RUN__', max_rounds: 5 });
  return reply(200, { ok: true, sessions: [], messages: [], pins: [], models: [], events: [],
                      receipts: [], counts: {}, projects: [], queue: [], data: [], next_after: 0,
                      state: { mode: 'manual' }, release_version: '0.0.0', build_id: 'b', commit: 'c' });
};
"""


def _router(status: dict, runs: list | None = None, scorecard: dict | None = None) -> str:
    return (
        ROUTER_TEMPLATE
        .replace("__SCORECARD__", json.dumps(scorecard or {}))
        .replace("__STATUS__", json.dumps(status))
        .replace("__LEDGER__", json.dumps({str(k): v for k, v in LEDGER.items()}))
        .replace("__RUNS__", json.dumps(runs if runs is not None else []))
        .replace("__RUN__", RUN)
    )


def drive(body: str, *, status: dict | None = None, runs: list | None = None,
          scorecard: dict | None = None) -> dict:
    program = (
        DOM
        + "\n;(async function(){\n"
        + "\n;\n".join(page_scripts())
        + "\n" + HELPERS
        + "\n" + _router(status if status is not None else _state(), runs, scorecard)
        + "\ntry {\n" + body + "\n} catch (e) { out({ threw: String((e && e.stack) || e) }); }\n"
        + "\n})();\n"
    )
    result = run_node(program, timeout=120)
    assert not result.get("errors"), f"page drive raised: {result['errors']}"
    assert not result.get("threw"), f"drive body threw: {result.get('threw')}"
    return result


@functools.lru_cache(maxsize=1)
def live_card() -> dict:
    """One shared drive of the everyday path: convene into the displayed chat, one poll."""
    return drive("""
      setDisplayedChat('""" + CHAT + """');
      renderChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      await window.VoolCouncilCard.refreshLock();
      var card = cardFor('""" + RUN + """');
      var head = one(card, cls('vcx-head'));
      var seatRows = all(card, cls('vcx-seat-row')).map(textOf);
      out({
        cards: all(logEl, function (el) { return el.dataset && el.dataset.councilRun; }).length,
        inLog: !!card,
        head: textOf(head),
        seatRows: seatRows,
        rounds: all(card, cls('vcx-round')).map(function (d) { return d.tagName; }),
        roundSummaries: all(card, cls('vcx-round')).map(function (d) { return textOf(one(d, tag('summary'))); }),
        composerDisabled: !!inputEl.disabled,
        sendDisabled: !!sendEl.disabled,
        lockHidden: document.getElementById('councilLock').hidden,
        lockText: textOf(document.getElementById('councilLock')),
      });
    """)


# --------------------------------------------------------- 1. it is in the chat


def test_convene_puts_exactly_one_council_card_into_the_active_chat_log():
    result = live_card()
    assert result["inLog"] is True, "convene produced no card inside the chat log"
    assert result["cards"] == 1, f"expected exactly one council card, got {result['cards']}"


def test_the_compact_head_reports_real_state_round_counts_retries_and_elapsed():
    head = live_card()["head"]
    assert "ROUND_OPEN" in head.upper(), head
    assert "r2/5" in head, "round and cap must both be visible: " + head
    assert "1 reported" in head, head        # round 2 has one landed report
    assert "2 working" in head, head         # two seats have not reported in round 2
    assert "0 failed" in head, head          # round 2 has no failure yet
    assert "2 retries" in head, "retries across the run must be counted: " + head
    assert re.search(r"\d+m\d\ds|\d+s", head), "elapsed must be shown: " + head


def test_rounds_and_seats_are_native_folded_details_that_start_closed():
    result = live_card()
    assert result["rounds"] == ["DETAILS", "DETAILS"], (
        "one native <details> per round, and nothing else standing in for one"
    )
    assert any("ROUND 1" in s for s in result["roundSummaries"]), result["roundSummaries"]
    assert any("ROUND 2" in s for s in result["roundSummaries"]), result["roundSummaries"]


# -------------------------------------------- 2. requested is never sold as actual


def test_every_seat_separates_the_model_asked_for_from_the_model_that_answered():
    rows = live_card()["seatRows"]
    assert len(rows) == 3, rows
    builder = next(r for r in rows if "builder" in r)
    assert "vendor/alpha" in builder and "vendor/alpha-0125" in builder
    falsifier = next(r for r in rows if "falsifier" in r)
    assert "vendor/beta" in falsifier, falsifier
    assert "actual model unknown" in falsifier, (
        "a seat with no streamed evidence must say so, never echo the request: " + falsifier
    )
    assert falsifier.count("vendor/beta") == 1, (
        "the requested model appeared twice — the second one is being sold as the answer"
    )


def test_a_seat_whose_actual_model_is_unknown_never_renders_the_requested_one_as_actual():
    """Driven against the exact shape C2 pins as the honest reading: request-strength
    identity on the wire yields model_actual null with evidence 'requested'."""
    state = _state()
    state["rounds"][1]["reports"][0].update(
        {"model_actual": None, "model_evidence": "requested"}
    )
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      var card = cardFor('""" + RUN + """');
      var round2 = all(card, cls('vcx-round'))[1];
      var seat = all(round2, cls('vcx-seat'))[0];
      var summary = one(seat, tag('summary'));
      if (summary) summary.__click();
      await sleep(30);
      out({ body: textOf(one(seat, cls('vcx-model'))), rows: all(card, cls('vcx-seat-row')).map(textOf) });
    """, status=state)
    assert "actual model unknown" in result["body"], result["body"]
    assert "requested" in result["body"], "the evidence label is part of the truth: " + result["body"]
    assert result["body"].count("vendor/alpha") == 1, (
        "the request must appear once, as the request: " + result["body"]
    )


# ------------------------------------------------ 3. the reasoning can be opened


def test_a_seat_report_body_is_fetched_through_text_ref_and_shown():
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      var card = cardFor('""" + RUN + """');
      var seat = all(card, cls('vcx-seat'))[0];
      var before = textOf(one(seat, cls('vcx-report')));
      one(seat, tag('summary')).__click();
      await sleep(40);
      out({
        before: before,
        after: textOf(one(seat, cls('vcx-report'))),
        asked: globalThis.__fetchLog.filter(function (u) { return u.indexOf('/api/council/events') === 0; }),
      });
    """)
    assert "DIAGNOSIS: the card never reached the chat." in result["after"], result
    assert any("seq=3" in u and "run=" + RUN in u for u in result["asked"]), (
        "the body must come from the ledger through text_ref: " + repr(result["asked"])
    )
    assert "DIAGNOSIS" not in result["before"], (
        "the body is fetched on open, not shipped inside every 2.5s poll of the view"
    )


def test_report_text_is_escaped_and_never_becomes_markup():
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      var card = cardFor('""" + RUN + """');
      var round2 = all(card, cls('vcx-round'))[1];
      var seat = all(round2, cls('vcx-seat'))[0];
      one(seat, tag('summary')).__click();
      await sleep(40);
      var report = one(seat, cls('vcx-report'));
      out({ text: textOf(report), html: report ? String(report.innerHTML || '') : '',
            pwned: !!globalThis.__pwned });
    """)
    assert result["pwned"] is False, "the report body executed as script"
    assert "<script>" not in result["html"], result["html"]
    assert "&lt;script&gt;" in result["html"], "the hostile text must survive, escaped"
    assert "window.__pwned = true;" in result["text"], "escaping must not delete evidence"


def test_verdict_counterexample_receipts_attempts_and_failures_all_reach_the_fold():
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      var card = cardFor('""" + RUN + """');
      var round1 = all(card, cls('vcx-round'))[0];
      var seats = all(round1, cls('vcx-seat'));
      seats.forEach(function (s) { var su = one(s, tag('summary')); if (su) su.__click(); });
      await sleep(60);
      out({ bodies: seats.map(textOf), verdict: textOf(all(card, cls('vcx-round'))[1]) });
    """)
    joined = "\n".join(result["bodies"])
    assert "2 receipts" in joined, joined
    assert "the fix never ran on the reload path" in joined, "the counterexample must be readable"
    assert "TimeoutError: seat read timed out" in joined, "a failed seat states its failure"
    assert "TIMED_OUT" in joined, "the typed attempt outcome is part of the record"
    assert "2 attempts" in joined and "1 retry" in joined, joined
    assert "AGREE" in result["verdict"], "a landed verdict must be visible on the round"


# ------------------------------------------- 4. the modal is not the run's home


def test_closing_the_convene_modal_neither_hides_nor_stops_the_run():
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      closeCouncil();
      var overlay = document.getElementById('councilOverlay');
      var card = cardFor('""" + RUN + """');
      out({
        overlayHidden: overlay.hidden === true,
        stillInLog: !!card,
        headStillReal: textOf(one(card, cls('vcx-head'))).indexOf('r2/5') !== -1,
        tracked: window.VoolCouncilCard.tracked().map(function (r) { return r.run_id + ':' + r.state; }),
      });
    """)
    assert result["overlayHidden"] is True, "the modal really was closed"
    assert result["stillInLog"] is True, "closing the convene form removed the run from the chat"
    assert result["headStillReal"] is True
    assert result["tracked"] == [RUN + ":round_open"], (
        "the run must still be owned and polled after the modal closes: " + repr(result["tracked"])
    )


def test_switching_chats_detaches_the_dom_but_keeps_ownership_and_rehydrates_on_return():
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      renderChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      var here = !!cardFor('""" + RUN + """');
      setDisplayedChat('""" + OTHER + """');
      renderChat('""" + OTHER + """');
      var away = !!cardFor('""" + RUN + """');
      var ownedWhileAway = window.VoolCouncilCard.tracked().length;
      await window.VoolCouncilCard.refresh('""" + RUN + """');   // polling continues off screen
      setDisplayedChat('""" + CHAT + """');
      renderChat('""" + CHAT + """');
      var back = cardFor('""" + RUN + """');
      out({ here: here, away: away, ownedWhileAway: ownedWhileAway,
            back: !!back, backHead: textOf(one(back, cls('vcx-head'))),
            backCards: all(logEl, function (el) { return el.dataset && el.dataset.councilRun; }).length });
    """)
    assert result["here"] is True
    assert result["away"] is False, "the other chat must not show this chat's council card"
    assert result["ownedWhileAway"] == 1, "leaving the chat must not drop ownership of the run"
    assert result["back"] is True, "returning to the chat must rehydrate the card"
    assert result["backCards"] == 1, "returning must not stack a second card"
    assert "r2/5" in result["backHead"]


# ----------------------------------------------- 5. reload rebuilds from history


def test_a_persisted_marker_in_chat_history_is_rebuilt_into_the_folded_card():
    summary = (
        "COUNCIL — adjudicated after 2 rounds.\nCandidate: the run state never reached "
        "the chat log\n\ncouncil | run=" + RUN + " | converged | r2/5"
    )
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      chatState('""" + CHAT + """').history.push({ role: 'user', content: 'why did it break?', ts: '' });
      chatState('""" + CHAT + """').history.push({ role: 'assistant', content: """ + json.dumps(summary) + """, ts: '' });
      renderChat('""" + CHAT + """');
      await sleep(60);
      var card = cardFor('""" + RUN + """');
      out({
        rebuilt: !!card,
        rounds: all(card, cls('vcx-round')).length,
        head: textOf(one(card, cls('vcx-head'))),
        messages: logEl.children.length,
        userSurvived: textOf(logEl).indexOf('why did it break?') !== -1,
      });
    """, status=TERMINAL)
    assert result["rebuilt"] is True, "the marker in history did not rebuild a card"
    assert result["rounds"] == 2, "the rebuilt card must carry the run's real rounds"
    assert "CONVERGED" in result["head"].upper(), result["head"]
    assert result["userSurvived"] is True, "rebuilding must not eat the surrounding chat"
    assert result["messages"] == 2, "one user message and one council card, nothing invented"


def test_pruned_evidence_keeps_the_readable_summary_and_says_details_are_gone():
    summary = (
        "COUNCIL — adjudicated after 2 rounds.\nCandidate: the run state never reached "
        "the chat log\nAdjudication only — promotion, merge and spend remain with the "
        "operator.\n\ncouncil | run=" + RUN + " | converged | r2/5"
    )
    result = drive("""
      globalThis.__statusMissing = true;
      setDisplayedChat('""" + CHAT + """');
      chatState('""" + CHAT + """').history.push({ role: 'assistant', content: """ + json.dumps(summary) + """, ts: '' });
      renderChat('""" + CHAT + """');
      await sleep(60);
      var card = cardFor('""" + RUN + """');
      out({ rebuilt: !!card, text: textOf(card), rounds: all(card, cls('vcx-round')).length });
    """, status=TERMINAL)
    assert result["rebuilt"] is True, "a pruned run must still render its card"
    assert "the run state never reached the chat log" in result["text"], (
        "the compact summary must survive when the evidence is gone: " + result["text"]
    )
    assert "promotion, merge and spend" in result["text"]
    assert "details are unavailable" in result["text"].lower(), (
        "absent evidence must be stated, not silently rendered as an empty card"
    )
    assert result["rounds"] == 0, "no rounds may be invented for a run with no record"


def test_a_live_run_is_readopted_after_a_reload_from_the_runs_listing():
    """The reload case with nothing in history yet: the run is still going, and the chat
    it belongs to must pick it back up rather than leaving the operator with no surface."""
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      renderChat('""" + CHAT + """');
      await sleep(60);
      out({ tracked: window.VoolCouncilCard.tracked().map(function (r) { return r.run_id; }),
            inLog: !!cardFor('""" + RUN + """') });
    """, runs=[{"run_id": RUN, "state": "round_open", "chat_session": CHAT, "live": True},
               {"run_id": "council-elsewhere", "state": "round_open", "chat_session": OTHER, "live": True},
               {"run_id": "council-old", "state": "converged", "chat_session": CHAT, "live": False}])
    assert result["tracked"] == [RUN], (
        "exactly the live run belonging to THIS chat is readopted: " + repr(result["tracked"])
    )
    assert result["inLog"] is True


# ------------------------------------------------- 6. the global pin is fenced


def test_the_composer_is_disabled_and_explained_while_this_chat_owns_a_live_council():
    result = live_card()
    assert result["composerDisabled"] is True, "a normal turn could still be typed and sent"
    assert result["sendDisabled"] is True
    assert result["lockHidden"] is False, "the operator is given no reason the composer is dead"
    assert "council" in result["lockText"].lower()
    assert "model" in result["lockText"].lower(), (
        "the explanation must name WHY — the council owns model routing: " + result["lockText"]
    )


def test_a_send_during_a_live_council_never_starts_a_turn():
    """Belt and braces: the disabled attribute is the visible half, and a keyboard path
    or a stale handler must not get past it either."""
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      await window.VoolCouncilCard.refreshLock();
      var turns = 0;
      var realRunTurn = runTurn;
      runTurn = function () { turns += 1; return Promise.resolve(); };
      inputEl.value = 'what model am I even talking to?';
      await send();
      runTurn = realRunTurn;
      out({ turns: turns, draftKept: inputEl.value });
    """)
    assert result["turns"] == 0, "a normal turn ran while the council owned the model pin"
    assert result["draftKept"] == "what model am I even talking to?", (
        "a refused send must not eat the operator's draft"
    )


@pytest.mark.parametrize("terminal", ["converged", "failed", "no_convergence", "stopped", "crashed"])
def test_every_terminal_path_hands_the_composer_back(terminal):
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      await window.VoolCouncilCard.refreshLock();
      var lockedDuring = !!inputEl.disabled;
      STATUS.state = '""" + terminal + """';
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      await window.VoolCouncilCard.refreshLock();
      out({ lockedDuring: lockedDuring, disabledAfter: !!inputEl.disabled,
            lockHiddenAfter: document.getElementById('councilLock').hidden,
            state: window.VoolCouncilCard.tracked().length });
    """)
    assert result["lockedDuring"] is True
    assert result["disabledAfter"] is False, f"the composer stayed dead after {terminal}"
    assert result["lockHiddenAfter"] is True
    assert result["state"] == 0, "a terminal run must stop being polled"


def test_every_chat_is_locked_because_the_pin_is_one_machine_wide_fact():
    """C3 locked only the originating chat — the browser half of a defect the server has
    since closed. The pin is global, so a council anywhere means no chat on this machine
    may run an ordinary turn, and the composer follows the SERVER, not the card."""
    result = drive("""
      setDisplayedChat('""" + OTHER + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refreshLock();
      out({ lockedElsewhere: !!inputEl.disabled,
            reason: textOf(document.getElementById('councilLock')),
            hidden: document.getElementById('councilLock').hidden });
    """)
    assert result["lockedElsewhere"] is True, (
        "a chat with no council card of its own was left free to send under a seat pin"
    )
    assert result["hidden"] is False
    assert "model pin" in result["reason"], result["reason"]


def test_a_council_convened_in_another_tab_locks_this_one_with_no_card_involved():
    """Nothing on this page ever adopted the run: the only thing closing the composer is
    the server saying the pin is held. That is what reading server truth buys."""
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      renderChat('""" + CHAT + """');
      await window.VoolCouncilCard.refreshLock();
      out({ locked: !!inputEl.disabled, tracked: window.VoolCouncilCard.tracked().length,
            reason: textOf(document.getElementById('councilLock')) });
    """)
    assert result["locked"] is True, "a council in another tab left this tab sending"
    assert result["reason"], "the operator is told nothing about why"


def test_switching_chats_keeps_the_lock_and_its_reason_on_screen():
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refreshLock();
      var lockedAtOrigin = !!inputEl.disabled;
      setDisplayedChat('""" + OTHER + """');
      renderChat('""" + OTHER + """');
      reflectComposer();
      out({ lockedAtOrigin: lockedAtOrigin, lockedAfterSwitch: !!inputEl.disabled,
            hidden: document.getElementById('councilLock').hidden,
            reason: textOf(document.getElementById('councilLock')),
            cardFollowed: !!cardFor('""" + RUN + """') });
    """)
    assert result["lockedAtOrigin"] is True
    assert result["lockedAfterSwitch"] is True, "switching chats unlocked the composer"
    assert result["hidden"] is False, "the reason vanished on a chat switch"
    assert result["reason"]
    assert result["cardFollowed"] is False, (
        "the card belongs to the originating chat; only the LOCK is machine-wide"
    )


def test_a_server_refusal_relocks_a_composer_the_page_believed_was_free():
    """DOM state is not authority. The fence can close between a poll and a keystroke, so
    the typed 409 is what re-closes the composer — and the refused turn says what happened
    instead of hanging on a stream that is never coming."""
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      renderChat('""" + CHAT + """');
      var refusal = { error: 'council_model_pin_active', run_id: '""" + RUN + """',
                      state: 'round_open', retry_after_state: ['converged'],
                      detail: 'a council currently owns the machine model pin' };
      var realFetch = globalThis.fetch;
      globalThis.fetch = async function (url, init) {
        if (String(url).indexOf('/api/chat') === 0) {
          return { ok: false, status: 409, headers: { get: function () { return null; } },
                   json: async function () { return refusal; },
                   clone: function () { return this; },
                   text: async function () { return JSON.stringify(refusal); } };
        }
        return realFetch(url, init);
      };
      var lockedBefore = !!inputEl.disabled;
      await runTurn('what model am I talking to?', null, { chatId: '""" + CHAT + """' });
      out({ lockedBefore: lockedBefore, lockedAfter: !!inputEl.disabled,
            hidden: document.getElementById('councilLock').hidden,
            reason: textOf(document.getElementById('councilLock')),
            log: textOf(logEl) });
    """)
    assert result["lockedBefore"] is False, "the page must genuinely believe it was free"
    assert result["lockedAfter"] is True, "a server refusal left the composer usable"
    assert result["hidden"] is False
    assert "model pin" in result["reason"], result["reason"]
    assert "ouncil" in result["log"], "the refused turn must say what happened, not hang"


# --------------------------------------------------------- 7. no invented state


def test_working_seats_are_only_ever_claimed_while_the_server_says_the_round_is_open():
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      STATUS.state = 'round_inconclusive';
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      out({ head: textOf(one(cardFor('""" + RUN + """'), cls('vcx-head'))) });
    """)
    assert "working" not in result["head"].lower(), (
        "seats were claimed to be working while the server said no round was open: " + result["head"]
    )


def test_a_legacy_run_whose_reports_are_inline_still_opens_its_reasoning():
    """Runs written before the ledger/snapshot split carry their text inline and have no
    text_ref. They are not migrated and must not be: the card reads what is actually
    there rather than fabricating a reference that resolves to nothing."""
    legacy = _state(state="converged", rounds=[{"round_no": 1, "reports": [
        {"seat_id": "s1", "round_no": 1, "status": "landed", "verdict": "AGREE",
         "receipt_count": 0, "attempts": 1, "retries_used": 0,
         "model_requested": "vendor/alpha", "model_actual": None, "model_evidence": "unknown",
         "text": "DIAGNOSIS: written before the ledger carried the text."},
    ]}], outcome={"result": "adjudicated", "agree": 1, "disagree": 0, "rounds": 1})
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      var seat = all(cardFor('""" + RUN + """'), cls('vcx-seat'))[0];
      one(seat, tag('summary')).__click();
      await sleep(40);
      out({ body: textOf(one(seat, cls('vcx-report'))),
            asked: globalThis.__fetchLog.filter(function (u) { return u.indexOf('/api/council/events') === 0; }) });
    """, status=legacy)
    assert "written before the ledger carried the text." in result["body"], result
    assert result["asked"] == [], "a legacy row has no reference to resolve — none may be invented"


def test_the_card_renders_no_round_the_server_did_not_report():
    """The blunt form of 'renders state, never invents it': a run the server reports with
    no rounds at all draws no rounds, whatever its round cap says."""
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      var card = cardFor('""" + RUN + """');
      out({ rounds: all(card, cls('vcx-round')).length, seats: all(card, cls('vcx-seat')).length,
            head: textOf(one(card, cls('vcx-head'))) });
    """, status=_state(state="convened", rounds=[]))
    assert result["rounds"] == 0 and result["seats"] == 0
    assert "r0/5" in result["head"], result["head"]


def test_the_namespace_is_frozen_and_the_fragment_owns_one_window_key():
    from core.council_chat_card_fragment import render_council_card_fragment

    fragment = render_council_card_fragment()
    for assignment in re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=", fragment):
        assert assignment.startswith("Vool"), f"fragment leaks window.{assignment}"
    result = drive("""
      var ns = window.VoolCouncilCard;
      var frozen = false;
      try { ns.extra = 1; frozen = ns.extra !== 1; } catch (e) { frozen = true; }
      out({ frozen: frozen, tracked: ns.tracked() });
    """)
    assert result["frozen"] is True
    assert result["tracked"] == [], "no run may be claimed before one is adopted"


# =====================================================================================
# C4 — the paused card: what the operator sees, and the four decisions they can make.
# =====================================================================================

PAUSED = _state(
    state="needs_attention",
    rounds=[{"round_no": 1, "reports": [
        {"seat_id": "s1", "round_no": 1, "status": "landed", "verdict": None,
         "receipt_count": 2, "attempts": 1, "retries_used": 0, "outcome": "VALID",
         "superseded": False, "model_requested": "vendor/alpha",
         "model_actual": "vendor/alpha-0125", "model_evidence": "actual_adapter",
         "text_ref": {"kind": "ledger", "seq": 3}},
        {"seat_id": "s2", "round_no": 1, "status": "failed", "verdict": None,
         "receipt_count": 0, "attempts": 2, "retries_used": 1, "outcome": "TIMED_OUT",
         "superseded": False, "failure": "TimeoutError: seat read timed out",
         "model_requested": "vendor/beta", "model_actual": None,
         "model_evidence": "unknown", "text_ref": None},
    ]}],
    outcome={
        "result": "needs_attention",
        "detail": "a voting seat exhausted its bounded attempts and produced no report.",
        "round_no": 1,
        "blocking_seats": [{
            "seat_id": "s2", "role_id": "falsifier", "model": "vendor/beta",
            "round_no": 1, "attempts": 2, "retries_used": 1, "outcome": "TIMED_OUT",
            "failure": "TimeoutError: seat read timed out",
        }],
    },
)


def _paused_drive(body, *, status=None):
    return drive(body, status=status if status is not None else PAUSED)


def test_a_paused_card_names_the_dead_seat_its_model_attempts_and_reason():
    result = _paused_drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      var card = cardFor('""" + RUN + """');
      out({ head: textOf(one(card, cls('vcx-head'))),
            attention: textOf(one(card, cls('vcx-attention'))) });
    """)
    assert "NEEDS_ATTENTION" in result["head"].upper(), result["head"]
    panel = result["attention"]
    assert "falsifier" in panel, panel
    assert "vendor/beta" in panel, panel
    assert "2 attempts" in panel, panel
    assert "TIMED_OUT" in panel, panel
    assert "TimeoutError: seat read timed out" in panel, panel


def test_the_paused_card_offers_exactly_the_four_decisions():
    result = _paused_drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      var card = cardFor('""" + RUN + """');
      out({ actions: all(card, cls('vcx-act')).map(function (b) {
              return String(b.dataset.act || '') + ':' + textOf(b); }) });
    """)
    kinds = [a.split(":")[0] for a in result["actions"]]
    assert sorted(kinds) == ["disable", "replace", "resume", "retry"], result["actions"]


def test_a_control_click_posts_the_typed_action_for_that_seat_and_nothing_else():
    result = _paused_drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      var card = cardFor('""" + RUN + """');
      var retry = all(card, cls('vcx-act')).filter(function (b) {
        return b.dataset.act === 'retry'; })[0];
      globalThis.__posted = [];
      retry.__click();
      await sleep(40);
      out({ posted: globalThis.__posted });
    """)
    posted = result["posted"]
    assert len(posted) == 1, posted
    assert posted[0]["url"] == "/api/council/seat"
    assert posted[0]["body"] == {"run_id": RUN, "seat_id": "s2", "action": "retry"}


def test_replace_sends_the_operators_model_and_refuses_to_send_an_empty_one():
    result = _paused_drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      var card = cardFor('""" + RUN + """');
      var field = one(card, cls('vcx-model-input'));
      var replace = all(card, cls('vcx-act')).filter(function (b) {
        return b.dataset.act === 'replace'; })[0];
      globalThis.__posted = [];
      field.value = '   ';
      replace.__click();
      await sleep(30);
      var emptyPosts = globalThis.__posted.length;
      field.value = 'vendor/delta';
      replace.__click();
      await sleep(40);
      out({ emptyPosts: emptyPosts, posted: globalThis.__posted });
    """)
    assert result["emptyPosts"] == 0, "an empty model was sent as a replacement"
    assert len(result["posted"]) == 1
    assert result["posted"][0]["body"] == {
        "run_id": RUN, "seat_id": "s2", "action": "replace", "model": "vendor/delta"}


def test_resume_posts_to_resume_and_never_to_the_seat_door():
    result = _paused_drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      var card = cardFor('""" + RUN + """');
      globalThis.__posted = [];
      all(card, cls('vcx-act')).filter(function (b) {
        return b.dataset.act === 'resume'; })[0].__click();
      await sleep(40);
      out({ posted: globalThis.__posted });
    """)
    assert [p["url"] for p in result["posted"]] == ["/api/council/resume"]
    assert result["posted"][0]["body"] == {"run_id": RUN}


def test_a_typed_refusal_is_shown_and_the_card_invents_no_progress():
    """A disable the server refuses must say so on the card. Nothing may be drawn as if
    the seat had been taken out."""
    result = _paused_drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      var card = cardFor('""" + RUN + """');
      globalThis.__actionRefusal = { ok: false, error: 'quorum_impossible',
        detail: 'no voting seat would be left.' };
      all(card, cls('vcx-act')).filter(function (b) {
        return b.dataset.act === 'disable'; })[0].__click();
      await sleep(50);
      out({ note: textOf(one(card, cls('vcx-action-note'))),
            head: textOf(one(card, cls('vcx-head'))) });
    """)
    assert "quorum_impossible" in result["note"] or "no voting seat" in result["note"], result
    assert "NEEDS_ATTENTION" in result["head"].upper(), (
        "a refused action changed what the card claims the run is doing"
    )


def test_a_running_card_offers_no_operator_controls():
    result = drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      var card = cardFor('""" + RUN + """');
      out({ actions: all(card, cls('vcx-act')).length,
            attention: !!one(card, cls('vcx-attention')) });
    """)
    assert result["actions"] == 0, "a running council offered seat controls"
    assert result["attention"] is False


# =====================================================================================
# C5 — the finished card explains itself: a concise summary, the evidence folded under it.
# =====================================================================================

SCORECARD = {
    "run": {
        "terminal_state": "converged",
        "seats_used": ["s1", "s2", "s3"],
        "models_used": ["vendor/alpha", "vendor/beta", "vendor/gamma"],
        "agreement_first_reached_round": 2,
        "agreement_strength": {"agree": 2, "disagree": 1, "voting_seats": 3},
        "adopted_conclusions": ["the run state never reached the chat log"],
        "remaining_disagreements": [{
            "seat_id": "s3", "role_id": "reviewer", "model": "vendor/gamma", "round_no": 2,
            "verdict": "DISAGREE", "counterexample": "it still reproduces on the reload path",
            "receipt_backed": False,
        }],
        "most_adopted": {
            "seat_ids": ["s1"], "claims_adopted": 1, "tied": False,
            "insufficient_evidence": False, "label": "most adopted contributions",
            "basis": "1 claim(s) adopted into the committed answer; 1 seat(s) hold that count.",
        },
        "substantially_refuted": {
            "seat_ids": [], "claims_refuted": 0, "label": "no seat had a claim refuted",
            "basis": "counted from candidate_rejected events.",
        },
        "provisional": False,
        "provisional_note": "",
    },
    "seats": [
        {"seat_id": "s1", "role_id": "builder", "model": "vendor/alpha", "votes": True,
         "active": True, "valid_attempts": 2, "attempts": 2, "retries": 0,
         "claims_proposed": 1, "claims_adopted": 1, "claims_supported": 2,
         "claims_refuted": 0, "claims_unresolved": 0, "refutations_landed": 0,
         "failures": 0, "timeouts": 0, "reliability_note": "",
         "claim_attribution": [{"round_no": 1, "model": "vendor/alpha", "adopted": True}]},
        {"seat_id": "s3", "role_id": "reviewer", "model": "vendor/gamma", "votes": True,
         "active": True, "valid_attempts": 1, "attempts": 3, "retries": 1,
         "claims_proposed": 0, "claims_adopted": 0, "claims_supported": 0,
         "claims_refuted": 0, "claims_unresolved": 0, "refutations_landed": 0,
         "failures": 1, "timeouts": 1,
         "reliability_note": "1 failed attempt(s) (TIMED_OUT). Reliability, not contribution.",
         "claim_attribution": []},
    ],
}


def _scored_drive(body, *, state=None, scorecard=None):
    return drive(body, status=state if state is not None else TERMINAL,
                 scorecard=scorecard if scorecard is not None else SCORECARD)


def test_a_finished_card_summarises_what_was_agreed_and_when():
    result = _scored_drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      await sleep(40);
      var card = cardFor('""" + RUN + """');
      out({ summary: textOf(one(card, cls('vcx-verdict-summary'))) });
    """)
    summary = result["summary"]
    assert "the run state never reached the chat log" in summary, summary
    assert "round 2" in summary.lower(), summary
    assert "2 agree" in summary and "1 disagree" in summary, summary
    assert "most adopted contributions" in summary.lower(), summary
    assert "builder" in summary, "the credited seat is named by role"
    assert "best" not in summary.lower() and "winner" not in summary.lower()


def test_the_full_score_evidence_is_folded_not_shown_by_default():
    result = _scored_drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      await sleep(40);
      var card = cardFor('""" + RUN + """');
      var fold = one(card, cls('vcx-score'));
      out({ tag: fold ? fold.tagName : '', open: fold ? !!fold.open : null,
            summary: fold ? textOf(one(fold, tag('summary'))) : '',
            body: fold ? textOf(fold) : '' });
    """)
    assert result["tag"] == "DETAILS", "the evidence must fold like every other round"
    assert result["open"] is False, "it opens on request, not by default"
    assert "contribution" in result["summary"].lower(), result["summary"]
    body = result["body"]
    for expected in ("builder", "vendor/alpha", "1 adopted", "2 supported"):
        assert expected in body, (expected, body)


def test_a_ranking_on_the_card_always_shows_the_counts_it_came_from():
    result = _scored_drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      await sleep(40);
      out({ basis: textOf(one(cardFor('""" + RUN + """'), cls('vcx-score-basis'))) });
    """)
    assert "1 claim(s) adopted into the committed answer" in result["basis"], result["basis"]


def test_a_failed_seat_is_shown_as_unreliable_and_never_as_a_poor_contributor():
    result = _scored_drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      await sleep(40);
      var rows = all(cardFor('""" + RUN + """'), cls('vcx-score-seat')).map(textOf);
      out({ rows: rows });
    """)
    reviewer = next(r for r in result["rows"] if "reviewer" in r)
    assert "1 failed" in reviewer, reviewer
    assert "Reliability, not contribution" in reviewer, reviewer
    assert "refuted" not in reviewer.lower().replace("0 refuted", ""), reviewer


def test_the_remaining_disagreement_is_named_with_its_party_and_its_text():
    result = _scored_drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      await sleep(40);
      out({ disputes: all(cardFor('""" + RUN + """'), cls('vcx-dispute')).map(textOf) });
    """)
    assert len(result["disputes"]) == 1, result["disputes"]
    row = result["disputes"][0]
    assert "reviewer" in row and "vendor/gamma" in row
    assert "it still reproduces on the reload path" in row
    assert "unverified" in row.lower() or "no receipts" in row.lower(), (
        "an unbacked counterexample must be labelled, never promoted: " + row
    )


def test_an_insufficient_evidence_run_names_nobody():
    thin = {**SCORECARD, "run": {**SCORECARD["run"],
            "adopted_conclusions": [], "agreement_first_reached_round": None,
            "agreement_strength": None, "remaining_disagreements": [],
            "most_adopted": {"seat_ids": [], "claims_adopted": 0, "tied": False,
                             "insufficient_evidence": True,
                             "label": "insufficient evidence to name a most-adopted contributor",
                             "basis": "0 adopted claims across the bench."}}}
    result = _scored_drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      await sleep(40);
      var card = cardFor('""" + RUN + """');
      out({ summary: textOf(one(card, cls('vcx-verdict-summary'))) });
    """, scorecard=thin)
    assert "insufficient evidence" in result["summary"].lower(), result["summary"]
    assert "vendor/" not in result["summary"], (
        "a run with no adopted claim named a model anyway: " + result["summary"]
    )


def test_a_paused_card_says_its_ranking_is_not_final():
    provisional = {**SCORECARD, "run": {**SCORECARD["run"],
        "terminal_state": "needs_attention", "provisional": True,
        "provisional_note": "this council is paused and has not committed an answer, so the "
                            "ranking below is not final",
        "adopted_conclusions": [],
        "most_adopted": {"seat_ids": [], "claims_adopted": 0, "tied": False,
                         "insufficient_evidence": True, "label": "insufficient evidence",
                         "basis": "0 adopted claims across the bench."}}}
    result = _scored_drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      await sleep(40);
      out({ summary: textOf(one(cardFor('""" + RUN + """'), cls('vcx-verdict-summary'))) });
    """, state=PAUSED, scorecard=provisional)
    assert "not final" in result["summary"].lower(), result["summary"]


def test_the_card_reads_the_scorecard_from_the_server_not_from_the_status_snapshot():
    result = _scored_drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      await sleep(40);
      out({ asked: globalThis.__fetchLog.filter(function (u) {
              return u.indexOf('/api/council/scorecard') === 0; }) });
    """)
    assert result["asked"], "the card never fetched the ledger-derived scorecard"
    assert "run=" + RUN in result["asked"][0]


def test_the_visible_winner_names_the_model_that_produced_the_adopted_claim():
    """A seat whose model was REPLACED mid-run still gets credit for the claim its FIRST
    model produced. The card's winner line must name that producing model, never the
    seat's current replacement."""
    replaced = json.loads(json.dumps(SCORECARD))
    replaced["seats"][0]["model"] = "vendor/replacement"
    replaced["run"]["most_adopted"]["adopted_claims"] = [{
        "claim_id": "clm:9", "seat_id": "s1", "round_no": 1,
        "model_requested": "vendor/asked", "model_actual": "vendor/producer",
        "model_evidence": "streamed", "produced_by": "vendor/producer",
        "produced_by_is_proven": True,
    }]
    result = _scored_drive("""
      setDisplayedChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      await sleep(40);
      var card = cardFor('""" + RUN + """');
      out({ summary: textOf(one(card, cls('vcx-verdict-summary'))) });
    """, scorecard=replaced)
    summary = result["summary"]
    assert "vendor/producer" in summary, summary
    assert "vendor/replacement" not in summary, summary


# ------------------- 9. a background run says it is still spending, and can be cancelled
#
# Navigating away deliberately leaves a council run alive — it is a background job, not a
# page. That is only acceptable if the chat it was convened from SAYS so and offers a way
# out. "Still running" is not the whole claim either: every seat turn is a paid cloud call
# (the server refuses local seats outright), so a live run is a run spending money, and the
# card has to say the part that costs something.


def _stop_probe(state: dict, *, runs=None) -> dict:
    return drive(
        """
      setDisplayedChat('""" + CHAT + """');
      renderChat('""" + CHAT + """');
      window.VoolCouncilCard.adopt('""" + CHAT + """', '""" + RUN + """');
      await window.VoolCouncilCard.refresh('""" + RUN + """');
      var card = cardFor('""" + RUN + """');
      var stop = one(card, cls('vcx-stop'));
      if (stop) stop.click();
      await sleep(30);
      out({
        head: textOf(one(card, cls('vcx-head'))),
        hasStop: !!stop,
        stopLabel: textOf(stop),
        posted: (globalThis.__fetchLog || []).filter(function (u) {
          return u.indexOf('/api/council/stop') === 0;
        }),
      });
    """,
        status=state,
        runs=runs,
    )


def test_a_live_council_says_in_the_chat_that_it_is_still_running_and_spending():
    head = _stop_probe(_state())["head"]
    assert "ROUND_OPEN" in head.upper(), head
    assert "spending" in head.lower(), (
        "a live council is dispatching paid cloud seat turns; the chat it was convened from "
        "must say so rather than only showing a round counter: " + head
    )


def test_a_live_council_offers_a_working_cancel_in_the_originating_chat():
    result = _stop_probe(_state())
    assert result["hasStop"] is True, "no cancel control in the originating chat"
    assert result["posted"] == ["/api/council/stop"], result["posted"]


def test_a_paused_council_still_offers_cancel_in_the_originating_chat():
    """`needs_attention` is not terminal and `api.stop()` handles it — but the run has left
    the live registry, so a card that gates its cancel on `live` removes the only way out of
    a run that is explicitly waiting for the operator."""
    result = _stop_probe(_state(state="needs_attention"))
    assert result["hasStop"] is True, (
        "a paused council is stoppable server-side but the chat offered no cancel"
    )
    assert result["posted"] == ["/api/council/stop"], result["posted"]


def test_a_paused_council_does_not_claim_to_be_spending():
    """The other half of the claim: a run parked on a failed seat is not paying for
    anything, and saying it is would be as wrong as hiding a live one."""
    head = _stop_probe(_state(state="needs_attention"))["head"]
    assert "spending" not in head.lower(), head


@pytest.mark.parametrize("terminal", ["converged", "stopped", "crashed", "no_convergence", "failed"])
def test_a_finished_council_neither_claims_spending_nor_offers_cancel(terminal):
    result = _stop_probe(_state(state=terminal))
    assert result["hasStop"] is False, f"{terminal} still offered a cancel"
    assert "spending" not in result["head"].lower(), result["head"]
