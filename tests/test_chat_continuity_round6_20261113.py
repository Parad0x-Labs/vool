"""Chat continuity, round 6: a message with no task in it (thanks, a dismissal, a shrug, with or without filler) after a
sealed turn gets a short plain acknowledgement and runs nothing: no model, no tool, no file lane, no planner.

Measured on the Mac (build db761391): "meh ok, thanks anyway" after a found-nothing file ask replied "No files matched
inside `downloads`" and "thanks anyway" replied "Incomplete - model synthesis failed ... workspace.list_files failed".
The resolver had flagged both as closings, but only as a note; the turn went on through the lanes and the model flow."""
from __future__ import annotations

import pytest

from core import conversation_state as cs
from tests.test_clean_local_loop_turn import turn_setup  # noqa: F401  (fixture)

SC = {"surface": "cli", "platform": "cli"}

# --- the two measured asks, whole turn, stand-in brain -----------------------------------------------------------------

FOUND_NOTHING = ['{"tool": "list_dir", "path": "Downloads"}', '{"answer": "No photos of the camping trip found in Downloads."}']


def _two_turns(agent, fake, monkeypatch, first, closing, replies=None):
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    fake.replies[:] = replies or FOUND_NOTHING
    r1 = agent.run_once(first, source_context=dict(SC))
    calls_before = len(fake.chats)
    r2 = agent.run_once(closing, source_context=dict(SC, session_id=r1.get("session_id")))
    return r1, r2, len(fake.chats) - calls_before


@pytest.mark.parametrize("first,closing", [
    ("any photos of the camping trip in downloads", "meh ok, thanks anyway"),
    ("is there a scan of my car loan in my files", "thanks anyway"),
])
def test_the_two_measured_closings_run_nothing(turn_setup, monkeypatch, first, closing):
    agent, fake, _home = turn_setup
    r1, r2, model_calls = _two_turns(agent, fake, monkeypatch, first, closing)
    assert r1.get("route_reason") == "clean_local_loop"
    assert r2.get("route_reason") == "conversation_closing", r2.get("route_reason")
    assert model_calls == 0
    assert not r2.get("tool_receipts")
    assert len(r2["response"]) < 90 and "match" not in r2["response"].lower() and "incomplete" not in r2["response"].lower()


def test_a_closing_does_not_cost_the_chat_its_record(turn_setup, monkeypatch):
    agent, fake, _home = turn_setup
    _r1, r2, _n = _two_turns(agent, fake, monkeypatch, "any photos of the camping trip in downloads", "alright, cheers")
    assert r2.get("route_reason") == "conversation_closing"
    pending = cs.load(r2.get("session_id") or "")
    assert pending is not None and "camping" in pending.request


# --- fresh closings (resolver level and unit of the acknowledgement) -------------------------------------------------

CLOSINGS = [
    "oh well, thanks",
    "alright then, cheers",
    "ah ok no worries thanks",
    "never mind then",
    "all good, thank you so much",
    "ok cool thx",
    "hmm fine, thanks a lot",
    "nah it's fine",
    "that's all, thanks",
    "meh. whatever.",
    "great, thanks mate",
    "ty!",
    "no thanks, I'm good",
    "oh ok, thanks for trying",
    "okay perfect, appreciate it",
    "ugh ok never mind, thx",
]


@pytest.mark.parametrize("message", CLOSINGS)
def test_a_message_with_no_task_is_a_closing(message):
    assert cs.is_plain_closing(message), message
    ack = cs.closing_reply(message)
    assert ack and len(ack) < 90 and "?" not in ack


@pytest.mark.parametrize("message", CLOSINGS)
@pytest.mark.parametrize("outcome", [cs.DONE, cs.ANSWERED, cs.REFUSED, cs.FAILED])
def test_a_closing_resolves_to_no_work_after_any_record(message, outcome):
    pending = cs.PendingRequest("any photos of the camping trip in downloads", outcome, "x", {"path": "/h/Downloads"}, 0)
    res = cs.resolve("s", message, pending=pending, cleared=True)
    assert res.kind == "none" and res.closing and not res.slots and not res.request


# --- traps: a closing word beside a task or a question --------------------------------------------------------------

TRAPS = [
    "thanks, now list my desktop",
    "ok thanks, what about the documents folder?",
    "cheers. and the biggest one?",
    "no thanks, show me the pdfs instead",
    "alright, thanks. can you open the first one",
    "never mind that, find my passport scan",
    "ok but what is in there",
    "thanks! remind me at 5 to call mum",
    "meh ok, do the same for pictures",
    "oh well, try the other folder then",
    "nah, the desktop one",
    "whatever, just search the whole disk",
]


@pytest.mark.parametrize("message", TRAPS)
def test_a_closing_word_beside_a_task_is_not_swallowed(message):
    assert not cs.is_plain_closing(message), message
    pending = cs.PendingRequest("any photos of the camping trip in downloads", cs.ANSWERED, "", {}, 0)
    assert not cs.resolve("s", message, pending=pending, cleared=None).closing


@pytest.mark.parametrize("message", ["yes thanks", "ok do it", "sure, go ahead thanks", "ok"])
def test_an_acceptance_keeps_its_meaning(message):
    assert not cs.is_plain_closing(message)


def test_a_trap_turn_still_reaches_the_work(turn_setup, monkeypatch):
    agent, fake, _home = turn_setup
    _r1, r2, _n = _two_turns(agent, fake, monkeypatch, "any photos of the camping trip in downloads", "thanks, now how much do i owe the plumber?",
                             replies=FOUND_NOTHING + ['{"tool": "find", "query": "plumber"}', '{"answer": "EUR 140.00"}'])
    assert r2.get("route_reason") != "conversation_closing"



@pytest.mark.parametrize("message", [
    "that is not good enough", "no that is not it", "not that", "it's not good", "that's not all of them", "not helpful",
    "forget it all", "forget that", "no", "nope", "stop", "cancel", "never", "mind that",
])
def test_a_complaint_a_bare_no_or_a_forget_ask_is_not_swallowed_as_a_closing(message: str) -> None:
    assert not cs.is_plain_closing(message), message
    assert not cs.resolve("s", message, pending=None, cleared=None).closing


@pytest.mark.parametrize("message", ["cheers anyway", "ok forget it then", "never mind then", "nah leave it", "no thanks", "thanks again", "ok nvm"])
def test_a_shrug_or_a_thanks_is_a_plain_closing(message: str) -> None:
    assert cs.is_plain_closing(message), message
