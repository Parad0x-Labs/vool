"""A new turn on a chat never wears the previous turn's terminal phrase.

Measured 2026-09-06 on the delivered build: the task card read `Working` in its header while its
stage line still said `Completed — not independently reviewed.` -- the previous turn's terminal
phrase. The companion reducer resets its terminal only on the server's typed `task.started`, which
arrives after ingress (0.35-1.2 s on an idle stub daemon, longer under load), and the paint loop
writes the chat-level phrase into the LAST task card -- the new one.

The page knows a turn opened the moment it creates the run. This drives the REAL page script and
the REAL companion fragment under the node DOM harness with a fetch whose stream sends nothing
until told to, and reads -- in that window -- the phrase the paint loop writes into the last card
(`vnResolve(view).phraseText`; the node DOM stub has no descendant selectors, so the painted
element itself is proven in the served browser drive of this lane).
"""
from __future__ import annotations

import re

import pytest

from core.companion_presentation_fragment import render_companion_fragment
from tests.chat_page_js_harness import DOM, run_node, script
from tests.test_multichat_navigation_unlock import NET, RAW_TIMER

A = "sess-send-reset-a"
TERMINAL = "Completed — not independently reviewed."


def _fragment_script() -> str:
    found = re.findall(r"<script[^>]*>(.*?)</script>", render_companion_fragment(), re.DOTALL)
    assert found, "companion fragment carries no script"
    return "\n;\n".join(found)


def _drive(body: str) -> dict:
    program = (
        RAW_TIMER
        + DOM
        + NET
        + "globalThis.__winH = {};\n"
        + "window.addEventListener = (t, f) => { (__winH[t] = __winH[t] || []).push(f); };\n"
        + "document.addEventListener = (t, f) => { (__winH['doc:' + t] = __winH['doc:' + t] || []).push(f); };\n"
        + script()
        + "\n;\n"
        + _fragment_script()
        + "\n;window.VoolCompanionBoot();\n"
        + "const VC = window.VoolCompanion;\n"
        + "function lastStage() { return String(VC.resolve(VC.view('" + A + "')).phraseText); }\n"
        + "\n;(async () => {\n"
        + body
        + "\n})().then(() => { __report(); process.exit(0); })"
        + ".catch((e) => { errors.push('harness: ' + (e && e.stack || e)); __report(); process.exit(0); });\n"
    )
    data = run_node(program, timeout=120)
    assert data.get("errors") == [], "the page threw while being driven:\n" + "\n".join(data["errors"])
    return data


FIRST_TURN = f"""
setDisplayedChat('{A}');
runTurn('first question', null, {{ chatId: '{A}' }});
await tick();
__streams['{A}'].push(ev({{ type: 'task.started', seq: 1, stage: 'Understanding' }}));
__streams['{A}'].push(chunk('answer one'));
__streams['{A}'].push(ev({{ type: 'task.completed', seq: 5, summary: 'Completed' }}));
__streams['{A}'].close();
await tick(6);
VC.paintNow();
const afterFirst = lastStage();
const terminalAfterFirst = VC.view('{A}').terminal;
"""


def test_the_second_turns_card_does_not_carry_the_first_turns_terminal_phrase() -> None:
    data = _drive(FIRST_TURN + f"""
runTurn('second question', null, {{ chatId: '{A}' }});
await tick(2);            // the request is in flight; the server has acknowledged nothing yet
VC.paintNow();
const beforeAck = lastStage();
const terminalBeforeAck = VC.view('{A}').terminal;
__streams['{A}'].push(ev({{ type: 'task.started', seq: 10, stage: 'Understanding' }}));
await tick(2);
VC.paintNow();
const afterAck = lastStage();
out({{ afterFirst, terminalAfterFirst, beforeAck, terminalBeforeAck, afterAck, runs: __posts.length }});
""")
    assert data["terminalAfterFirst"] == "COMPLETED_UNREVIEWED"
    assert data["afterFirst"] == TERMINAL, "the first turn's card carries its own terminal phrase"
    assert data["runs"] == 2, "the second turn is its own run"
    assert data["terminalBeforeAck"] is None, "a new turn on the lane resets the previous terminal truth at send time"
    assert data["beforeAck"] != TERMINAL, (
        "the new card wore the previous turn's terminal phrase while the request was in flight"
    )
    assert data["afterAck"] != TERMINAL


def test_a_send_that_the_server_never_acknowledges_still_does_not_claim_understanding() -> None:
    """The client-side reset is not a fake acknowledgement: before `task.started` the companion has
    no category and claims nothing about the server's progress."""
    data = _drive(FIRST_TURN + f"""
runTurn('second question', null, {{ chatId: '{A}' }});
await tick(2);
const view = VC.view('{A}');
const pres = VC.resolve(view);
out({{ started: view.started, state: pres.state, phrase: pres.phraseText }});
""")
    assert data["started"] is False
    assert data["state"] == "IDLE"
    assert data["phrase"] == ""
