"""Chat continuity, round 5: a rerun of an answered ask, closings, a pick outside the printed list, and a question
that is not an answer when nothing was searched.

Wordings here are fresh (not taken from any acceptance scenario). The model is a scripted stand-in: `harness`.
"""
from __future__ import annotations

import pytest

from core import conversation_state as cs
from core.clean_loop import route
from core.clean_loop.loop import is_bare_question, run_loop
from core.clean_loop.registry import Registry, ToolContext
from core.clean_loop.tools import NOT_FOUND_TEXT, UNSEARCHED_TEXT, install_builtin_tools
from tests.test_clean_local_loop_turn import turn_setup  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _fresh_state():
    cs.clear_all()
    yield
    cs.clear_all()


def _pending(outcome, request="how do i split a pdf on a mac", slots=None, turns=0):
    return cs.PendingRequest(request, outcome, "", dict(slots or {}), turns)


# --- 1. a rerun of an answered ask carries no "it ended answered" record -------------------------------------------

@pytest.mark.parametrize(
    "request_text, message",
    [
        ("how do i split a pdf on a mac", "go again pls"),
        ("whats the best way to shrink a video for whatsapp", "one more time"),
        ("explain how to zip a folder in linux", "redo that please"),
        ("can you tell me how to merge two word docs", "ok try once more"),
    ],
)
def test_a_rerun_of_an_answered_ask_asks_for_the_answer_not_a_report(request_text, message):
    res = cs.resolve("s", message, pending=_pending(cs.ANSWERED, request_text), cleared=None)
    assert res.kind == "retry" and res.request == request_text
    assert "ended" not in res.note and "answered" not in res.note
    assert "directly" in res.note


def test_a_rerun_of_a_refused_ask_still_states_how_it_ended():
    pending = cs.PendingRequest("what is the weather in porto", cs.REFUSED, "web_off", {}, 0)
    res = cs.resolve("s", "ok try again", pending=pending, cleared=True)
    assert res.kind == "retry" and "It ended refused" in res.note


def test_a_rerun_with_a_change_of_an_answered_ask_carries_the_same_note():
    res = cs.resolve("s", "same again but for windows 11", pending=_pending(cs.ANSWERED, "how do i take a screenshot on windows 10"), cleared=None)
    assert res.kind in {"retry", "retry_changed"}
    assert "ended" not in res.note


def test_the_note_reaches_the_model_context_as_written():
    from core.prompt_normalizer import _followup_context_message

    note = cs.resolve("s", "go again pls", pending=_pending(cs.ANSWERED), cleared=None).note
    message = _followup_context_message({"conversation_state_note": note})
    assert message is not None and note in message.content


# --- 2. a closing is never the previous task ------------------------------------------------------------------------

CLOSINGS = ["cheers mate", "nah all good thanks", "ok thx bye", "meh never mind thanks", "thanks anyway", "ugh whatever, thanks"]


@pytest.mark.parametrize("message", CLOSINGS)
@pytest.mark.parametrize("outcome", [cs.DONE, cs.ANSWERED, cs.REFUSED])
def test_a_closing_carries_no_request_and_no_slots(message, outcome):
    pending = cs.PendingRequest("is there a scan of my vet bill in my files", outcome, "web_off" if outcome == cs.REFUSED else "", {"path": "/h/Downloads", "kind": "pdf"}, 0)
    res = cs.resolve("s", message, pending=pending, cleared=True)
    assert res.kind == "none" and res.note == cs.CLOSING_NOTE
    assert not res.slots and "vet bill" not in res.note


@pytest.mark.parametrize("message", ["yes thanks", "sure, thanks", "please go ahead thanks", "ok do it thanks"])
def test_an_acceptance_with_thanks_is_not_a_closing(message):
    assert not cs.is_closing(message)


@pytest.mark.parametrize("message", ["thanks, now the same for desktop", "thanks but what about the other one", "cheers, list them again", "ok and the biggest one?"])
def test_a_thanks_with_a_task_is_not_a_closing(message):
    assert not cs.is_closing(message)


def test_a_closing_after_a_found_nothing_loop_turn_gets_small_talk_not_work(turn_setup, monkeypatch):  # noqa: F811
    agent, fake, _home = turn_setup
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    fake.replies[:] = ['{"tool": "find", "query": "zzqq"}', '{"answer": "I found no vet bill in your files."}', '{"answer": "You are welcome."}']
    sc = {"surface": "cli", "platform": "cli"}
    r1 = agent.run_once("is there a scan of my vet bill in my files", source_context=sc)
    r2 = agent.run_once("nah forget it, cheers", source_context=dict(sc, session_id=r1.get("session_id")))
    assert r2.get("route_reason") != "clean_local_loop"
    assert not r2.get("tool_receipts")
    ctx = r2.get("source_context") or {}
    assert ctx.get("conversation_state_note") == cs.CLOSING_NOTE
    assert "vet bill" not in str(ctx.get("_conversation_request") or "")


# --- 3. a pick beyond the printed list ------------------------------------------------------------------------------

def _listed(*names):
    return {"listed": [{"name": n, "path": f"~/Documents/{n}"} for n in names]}


@pytest.mark.parametrize(
    "message, names, shown",
    [
        ("the second one", ["tax_2025.pdf"], "tax_2025.pdf"),
        ("open the third file", ["a.pdf", "b.pdf"], "b.pdf"),
        ("number 4", ["a.pdf", "b.pdf", "c.pdf"], "c.pdf"),
        ("whats #9", ["one.pdf"], "one.pdf"),
    ],
)
def test_a_pick_outside_the_list_says_how_long_the_list_was(message, names, shown):
    res = cs.resolve("s", message, pending=_pending(cs.DONE, "list my pdfs", _listed(*names)), cleared=None)
    assert res.kind == "referent_unresolved"
    assert f"only {len(names)} item" in res.answer and shown in res.answer


@pytest.mark.parametrize("message, expect", [("the second one", "b.pdf"), ("number 1", "a.pdf"), ("the last one", "b.pdf"), ("the first one", "a.pdf")])
def test_a_pick_inside_the_list_still_picks(message, expect):
    res = cs.resolve("s", message, pending=_pending(cs.DONE, "list my pdfs", _listed("a.pdf", "b.pdf")), cleared=None)
    assert res.kind == "pick" and expect in res.request and not res.answer


def test_a_pick_with_no_printed_list_has_no_invented_answer():
    res = cs.resolve("s", "the second one", pending=_pending(cs.DONE, "how do i boil an egg"), cleared=None)
    assert res.kind == "referent_unresolved" and not res.answer


def test_the_turn_answers_a_pick_outside_the_recorded_list_without_a_model(turn_setup, monkeypatch):  # noqa: F811
    agent, fake, _home = turn_setup
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    sc = {"surface": "cli", "platform": "cli", "session_id": "pick-chat"}
    cs.record_sealed_turn(
        "pick-chat", "show me the spreadsheets in my documents",
        {"tool_receipts": [{"tool_name": "machine.list_directory", "execution": {"ok": True, "status": "executed"}}],
         "details": {"path": "~/Documents", "entries": [{"name": "budget.xlsx", "path": "~/Documents/budget.xlsx"}]}},
    )
    result = agent.run_once("give me the second one", source_context=sc)
    assert result.get("route_reason") == "conversation_pick_outside_list", result.get("route_reason")
    assert "only 1 item" in result["response"] and "budget.xlsx" in result["response"]
    assert not fake.chats


def test_an_unresolved_pointer_is_not_handed_to_the_loop(turn_setup, monkeypatch):  # noqa: F811
    agent, fake, _home = turn_setup
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    result = agent.run_once("and what about that folder", source_context={"surface": "cli", "platform": "cli"})
    assert result.get("route_reason") != "clean_local_loop"
    assert "clean_local_loop" not in result


# --- 4. a question is not an answer when nothing was read ----------------------------------------------------------

@pytest.mark.parametrize(
    "text, expected",
    [
        ("do you mean a file name or a folder?", True),
        ("Which one? Do you mean the pdf?", True),
        ("I found nothing. Is it under another name?", False),
        ("Here is the list.", False),
        ("", False),
    ],
)
def test_bare_question_shape(text, expected):
    assert is_bare_question(text) is expected


def _registry():
    registry = Registry()
    install_builtin_tools(registry)
    return registry


def _brain(replies):
    seen = []

    def brain(messages):
        seen.append([dict(m) for m in messages])
        return replies[min(len(seen), len(replies)) - 1]

    brain.seen = seen
    return brain


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = (tmp_path / "home").resolve()
    for name in ("Desktop", "Downloads", "Documents"):
        (root / name).mkdir(parents=True)
    (root / "Downloads" / "plumber_invoice.txt").write_text("Plumber invoice\nTotal due: EUR 140.00\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(root))
    return root


@pytest.mark.parametrize("ask", [
    "do i have a pdf of my tenancy agreement anywhere",
    "got a scan of my vet bill somewhere on here",
    "is there a spreadsheet of my tax return in my folders",
    "any copy of my gym membership on this machine",
])
def test_a_question_after_an_empty_search_is_sent_back_for_a_plain_statement(home, ask):
    brain = _brain([
        '{"tool": "find", "query": "zzqx"}',
        '{"answer": "do you mean a file name or a folder?"}',
        '{"answer": "I did not find that in your files. Is it under a different name?"}',
    ])
    result = run_loop(ask, brain=brain, registry=_registry(), ctx=ToolContext(home=home, ask=ask))
    assert "did not find" in result.answer
    assert any(step.get("nudge") == "question_without_evidence" for step in result.steps)


@pytest.mark.parametrize("ask", [
    "do i have a pdf of my pet passport anywhere",
    "is there a scan of my boat licence in my files",
])
def test_a_model_that_keeps_asking_gets_the_search_result_as_the_answer(home, ask):
    brain = _brain(['{"tool": "find", "query": "zzqx"}', '{"answer": "which folder do you mean?"}'])
    result = run_loop(ask, brain=brain, registry=_registry(), ctx=ToolContext(home=home, ask=ask))
    assert result.answer in {NOT_FOUND_TEXT, UNSEARCHED_TEXT}
    assert "?" not in result.answer


@pytest.mark.parametrize("ask", [
    "do i have a pdf of my kayak insurance anywhere",
    "is there a scan of my mortgage offer in my files",
])
def test_a_first_step_question_with_no_search_makes_the_loop_search(home, ask):
    brain = _brain(['{"answer": "do you mean a file name or a folder?"}'])
    result = run_loop(ask, brain=brain, registry=_registry(), ctx=ToolContext(home=home, ask=ask))
    assert result.tools_used and result.tools_used[0] == "find"
    assert "?" not in result.answer


def test_a_clarifying_question_after_a_read_that_found_things_is_left_alone(home):
    ask = "which of my invoices is the plumber one"
    brain = _brain([
        '{"tool": "find", "query": "plumber"}',
        '{"answer": "Do you mean plumber_invoice.txt?"}',
    ])
    result = run_loop(ask, brain=brain, registry=_registry(), ctx=ToolContext(home=home, ask=ask))
    assert result.answer == "Do you mean plumber_invoice.txt?"


def test_the_whole_turn_says_plainly_it_found_none(turn_setup, monkeypatch):  # noqa: F811
    agent, fake, _home = turn_setup
    fake.replies[:] = [
        '{"tool": "find", "query": "zzqq"}',
        '{"answer": "do you mean a file name or a folder?"}',
        '{"answer": "I found no pet passport pdf in your files."}',
    ]
    monkeypatch.setenv("VOOL_CLEAN_LOCAL_LOOP", "1")
    result = agent.run_once("do i have a pdf of my pet passport anywhere", source_context={"surface": "cli", "platform": "cli"})
    assert result.get("route_reason") == "clean_local_loop", result.get("route_reason")
    assert "found no pet passport" in result["response"]
