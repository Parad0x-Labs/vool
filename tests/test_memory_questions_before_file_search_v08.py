"""A question about the owner's own facts is checked before the lanes that read files or the machine, and the
memory lane has one trust gate at its door.

Measured on the real app (the v0.8 line base and the commits after it, local model, the Mac's own home folder):

- "when is our chalet booking?" with the booking saved was searched on the disk and answered from a document that
  mentions a chalet; "what's in the flock book?", a list saved in memory, was answered "the term appears in several
  documents";
- "what size is my trailer tent's gas bottle?", never told and in no file, was answered "I have not completed a
  search inside your files, so I can't say whether it is there." after two minutes;
- "how big is my caravan battery?" was answered with this computer's battery state;
- "my shredder oil is type F, the thin one." was answered "I see files related to oil ...";
- "what do you remember?" sent as a team agent's brief listed the owner's saved facts.

All names, places, sizes and dates here are invented for this contract.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import persistent_memory
from core.clean_loop import hook, route
from core.clean_loop.tools import NOT_FOUND_TEXT, UNSEARCHED_TEXT
from core.memory import everyday_remember as everyday
from core.memory import keep_receipt
from core.memory.entries import list_memory_entries
from core.persistent_memory import maybe_handle_memory_command
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home

OWNER = {"_owner_local": True}
FILES = {"chalet", "booking", "lease", "ledger", "barn", "oil", "pump"}


def _decide(text: str, session: str, context: dict | None = None) -> route.RouteDecision:
    return route.decide(text, source_context=dict(OWNER if context is None else context), session_id=session,
                        resolve_brain=lambda: object(), env={}, file_words=lambda: set(FILES))


# --- a saved fact answers before the file search ---------------------------------------------------------------------


def test_a_saved_fact_answers_a_question_about_my_booking(isolated_home):
    assert _say("Remember that our chalet booking is on 13 september", "seed")[1].startswith("Locked in")
    _policy("ask")
    decision = _decide("when is our chalet booking?", "ask")
    assert not decision.admit and decision.reason == "saved_memory_answers", decision


@pytest.mark.parametrize(
    "text",
    ["where's our chalet booking pdf?", "open our chalet booking", "where did I save the chalet booking letter?",
     "what does the chalet booking file say?"],
)
def test_an_ask_that_names_a_file_stays_a_file_ask_whatever_is_saved(isolated_home, text):
    assert _say("Remember that our chalet booking is on 13 september", "seed")[1].startswith("Locked in")
    _policy("ask")
    assert _decide(text, "ask").reason not in {"saved_memory_answers", "saved_list"}


def test_with_nothing_saved_the_question_about_my_lease_still_reaches_the_files(isolated_home):
    _policy("ask")
    decision = _decide("when does my lease end?", "ask")
    assert decision.admit, decision


@pytest.mark.parametrize(
    ("text", "strong"),
    [
        ("when is our chalet booking?", False),
        ("when does my lease end?", False),
        ("how much is my mortgage a month?", False),
        ("where's my lease pdf?", True),
        ("open my lease", True),
        ("how big are my photos?", True),
        ("when is my flight on my laptop?", True),
        ("what's in the barn ledger?", True),
    ],
)
def test_a_bare_my_matter_is_not_a_file_and_everything_else_still_is(text, strong):
    assert route.points_at_files(text)
    assert route.points_at_a_file_or_the_machine(text) is strong


# --- a list kept in memory is asked into by its name ------------------------------------------------------------------


def test_a_row_names_the_list_it_was_kept_under():
    assert everyday.row_list_subject("the hay delivery is on 4 march (the barn ledger)") == "the barn ledger"
    assert everyday.row_list_subject("for my kayak trip: a dry bag and a bilge sponge") == "my kayak trip"
    assert everyday.row_list_subject("my locker is 214 in the east wing") == ""


def test_a_question_into_a_saved_list_is_memorys(isolated_home, monkeypatch):
    from core import intent_arbiter

    monkeypatch.setattr(intent_arbiter, "arbitrate_keep_ask", lambda lead, body, **_: "yes")
    persistent_memory._JUDGED_LEADS.clear()
    assert _say("Two things for the barn ledger: the hay delivery is on 4 march.", "seed")[1].startswith("Locked in")
    _policy("ask")
    decision = _decide("what's in the barn ledger?", "ask")
    assert not decision.admit and decision.reason == "saved_list", decision
    assert _decide("what's in the barn ledger pdf?", "ask").reason != "saved_list"
    assert _decide("what's in the pump manual?", "ask").reason != "saved_list"
    persistent_memory._JUDGED_LEADS.clear()


# --- a statement the keep step kept is not a file ask ------------------------------------------------------------------


def test_a_stated_fact_the_keep_step_saved_is_left_to_the_chat(isolated_home):
    _policy("say")
    kept = {**OWNER, keep_receipt.CONTEXT_KEY: {"saved": ["my pump oil is the thin grade"], "refused": [],
                                               "in_passing": True}}
    decision = _decide("my pump oil is the thin grade.", "say", kept)
    assert not decision.admit and decision.reason == "stated_fact_kept", decision
    # Without a write on the receipt, and for a question, nothing changes.
    assert _decide("my pump oil is the thin grade.", "say").reason != "stated_fact_kept"
    assert _decide("which oil does my pump take?", "say", kept).reason != "stated_fact_kept"


# --- the loop's own "nothing found" replies are not an answer ---------------------------------------------------------


@pytest.mark.parametrize("answer", [UNSEARCHED_TEXT, NOT_FOUND_TEXT, NOT_FOUND_TEXT + " Which folder should I try?",
                                    "I couldn't find anything about that."])
def test_the_loops_no_find_replies_read_as_nothing_found(answer):
    assert hook.says_nothing_found(answer)


def test_an_answer_from_a_file_is_not_nothing_found():
    assert not hook.says_nothing_found("The barn ledger lists a hay delivery on 4 March.")
    assert not hook.says_nothing_found("")


def test_a_word_admitted_ask_whose_search_ended_unsearched_falls_through():
    steps = [{"tool": "find", "ok": True, "empty": False, "status": "ok", "result_chars": 40}]
    result = SimpleNamespace(steps=steps, staged="", seen=["pump oil chart.txt mentions pump oil"], answer=UNSEARCHED_TEXT)
    assert hook.loop_should_decline("what grade is my pump oil?", result, []) == "found_nothing_fell_through"
    answered = SimpleNamespace(steps=steps, staged="", seen=["pump oil chart.txt mentions pump oil"],
                               answer="The pump oil chart says thin grade.")
    assert hook.loop_should_decline("what grade is my pump oil?", answered, []) == ""


@pytest.mark.parametrize(
    ("text", "alone"),
    [
        ("how often does the hay barn get swept?", True),
        ("what grade is my pump oil?", True),
        ("and what does that sheet say about the lid?", False),
        ("how big is it?", False),
        ("and the other one?", False),
        ("ok", False),
    ],
)
def test_a_question_of_its_own_is_told_from_a_follow_up_that_points_back(text, alone):
    assert route.stands_alone(text) is alone


# --- the trust checks come first -------------------------------------------------------------------------------------


def _memory_lane_must_not_be_asked(monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("the memory lane was asked before the trust checks")

    monkeypatch.setattr(route, "_memory_lane_owns", never)


def test_a_private_chat_is_decided_before_the_memory_lane_is_asked(isolated_home, monkeypatch):
    from core import private_chat

    _memory_lane_must_not_be_asked(monkeypatch)
    monkeypatch.setattr(private_chat, "turn_is_private", lambda context, session_id=None: True)
    assert _decide("small update: the chalet booking moved to 14 september", "private").reason == "private_turn"


def test_a_remote_surface_and_a_red_turn_are_decided_before_the_memory_lane_is_asked(isolated_home, monkeypatch):
    _memory_lane_must_not_be_asked(monkeypatch)
    remote = {"surface": "mobile_companion"}
    assert _decide("small update: the chalet booking moved to 14 september", "phone", remote).reason == "not_owner_local"
    assert _decide("wire 400 to the landlord and delete the lease", "own").reason == "red_turn"


def test_a_forget_in_the_owners_chat_is_still_the_memory_lanes(isolated_home):
    _policy("own")
    assert _decide("forget my chalet booking", "own").reason == "memory_command"


# --- one trust gate at the memory lane's door -------------------------------------------------------------------------


AGENT = {"turn_author": "agent"}


def test_an_agents_brief_is_not_shown_what_is_saved(isolated_home):
    assert _say("Remember that our chalet booking is on 13 september", "seed")[1].startswith("Locked in")
    for ask in ("what do you remember", "what do you remember about my chalet booking?"):
        handled, reply = maybe_handle_memory_command(ask, session_id="seed", access_policy=_policy("seed"),
                                                     source_context=dict(AGENT))
        assert handled and reply == persistent_memory.AGENT_TURN_READ_REFUSED, (ask, reply)
        assert "13 september" not in reply
    handled, reply = maybe_handle_memory_command("what do you remember", session_id="seed",
                                                 access_policy=_policy("seed"), source_context={})
    assert handled and "13 september" in reply  # the owner's own turn still lists it


def test_a_turn_that_may_not_read_gets_no_chat_read_no_row_read_and_no_judge(isolated_home, monkeypatch):
    from core import intent_arbiter, private_chat

    def never(*args, **kwargs):
        raise AssertionError("read or judged behind the trust gate")

    assert _say("few things to remember for me: our chalet booking is on 13 september", "seed")[0]
    monkeypatch.setattr(persistent_memory, "_chat_earlier_user_turns", never)
    monkeypatch.setattr(intent_arbiter, "arbitrate_keep_ask", never)
    for text in ("and the keys are with the caretaker", "hang on to this for me: the keys are with the caretaker",
                 "small update: the chalet booking moved to 14 september"):
        assert not persistent_memory.everyday_memory_turn(text, session_id="seed", source_context=dict(AGENT))
    monkeypatch.setattr(private_chat, "turn_is_private", lambda context, session_id=None: True)
    # The chat itself is private (not only this one turn), so the refusal names private chat.
    monkeypatch.setattr(private_chat, "is_private", lambda session_id: True)
    assert not persistent_memory.everyday_memory_turn("and the keys are with the caretaker", session_id="seed",
                                                      source_context={})
    # An explicit lead is still the lane's, which refuses it in its own words and writes nothing.
    handled, reply = maybe_handle_memory_command("keep in mind that the keys are with the caretaker",
                                                 session_id="seed", access_policy=_policy("seed"), source_context={})
    assert handled and "Private chat is on" in reply
    rows = [str(row.get("fact") or row.get("text") or "") for row in list_memory_entries(access_policy=_policy("seed"), limit=50)]
    assert not any("caretaker" in row for row in rows)


# --- the machine lane -------------------------------------------------------------------------------------------------


def test_a_question_a_saved_fact_answers_is_not_about_this_computers_battery(isolated_home):
    from core.agent_runtime.fast_paths_machine import _a_saved_fact_answers

    assert _say("Remember that my camper's leisure battery is a 95 amp-hour one", "seed")[1].startswith("Locked in")
    _policy("ask")
    assert _a_saved_fact_answers("how big is my camper battery?", "ask", dict(OWNER))
    assert not _a_saved_fact_answers("what's the battery level?", "ask", dict(OWNER))


# --- a read vouches for no save ----------------------------------------------------------------------------------------


def test_a_file_search_that_ran_does_not_vouch_for_a_save_claim(isolated_home):
    from tests.test_keep_receipt_decides_save_claims_v08 import NOTHING_KEPT, _gate

    reply = "You're welcome! Whatever you meant by \"that\" is saved, I've got it. Let me know if you need anything else!"
    ask = "Thanks, so that's saved now, right?"
    for reads in (["machine.find_file"], ["machine.find_file", "machine.read_file", "machine.list_directory"]):
        published, record = _gate(reply, dict(NOTHING_KEPT), tools=reads, request=ask)
        assert "is saved" not in published and "Nothing from this message was saved" in published, (reads, record)
    # A tool that can keep something still stands the gate down: what the reply calls saved may be its doing.
    published, _ = _gate(reply, dict(NOTHING_KEPT), tools=["notes.create"], request=ask)
    assert "is saved" in published


# --- the hand-off does not depend on how the loop's model words a miss -------------------------------------------------


MISS_IN_OTHER_WORDS = "Your pump oil grade is not mentioned in the files. Is there a file you'd like me to check?"


def test_a_miss_in_words_the_hook_does_not_know_is_read_by_the_judgement():
    assert not hook.says_nothing_found(MISS_IN_OTHER_WORDS)  # the gap: measured on the real app, 1 run in 5
    assert not hook.found_nothing("what grade is my pump oil?", MISS_IN_OTHER_WORDS)  # nobody judging: as before
    assert hook.found_nothing("what grade is my pump oil?", MISS_IN_OTHER_WORDS, lambda q, a: False)
    assert not hook.found_nothing("what grade is my pump oil?", MISS_IN_OTHER_WORDS, lambda q, a: True)
    assert not hook.found_nothing("what grade is my pump oil?", MISS_IN_OTHER_WORDS, lambda q, a: None)

    def broken(question, answer):
        raise RuntimeError("judge down")

    assert not hook.found_nothing("what grade is my pump oil?", MISS_IN_OTHER_WORDS, broken)
    # A reading the hook already has needs no judgement.
    def never(question, answer):
        raise AssertionError("judged a reply the hook can read")

    assert hook.found_nothing("what grade is my pump oil?", UNSEARCHED_TEXT, never)


def test_a_word_admitted_ask_with_a_miss_in_other_words_falls_through_when_judged_a_miss():
    steps = [{"tool": "find", "ok": True, "empty": False, "status": "ok", "result_chars": 40}]
    result = SimpleNamespace(steps=steps, staged="", seen=["pump oil chart.txt mentions pump oil"], answer=MISS_IN_OTHER_WORDS)
    ask = "what grade is my pump oil?"
    assert hook.loop_should_decline(ask, result, []) == ""
    assert hook.loop_should_decline(ask, result, [], judged=lambda q, a: False) == "found_nothing_fell_through"
    assert hook.loop_should_decline(ask, result, [], judged=lambda q, a: True) == ""
    # A follow-up that the chat's own evidence may answer is not declined on this rule, judged or not.
    assert hook.loop_should_decline(ask, result, [], follow_up=True, judged=lambda q, a: False) == ""
    answered = SimpleNamespace(steps=steps, staged="", seen=["pump oil chart.txt mentions pump oil"],
                               answer="The pump oil chart says thin grade.")
    assert hook.loop_should_decline(ask, answered, [], judged=lambda q, a: True) == ""


def test_the_answered_question_is_a_judgement_on_the_chat_models_runner(monkeypatch):
    from unittest import mock

    from core import intent_arbiter as arbiter
    from core.turn_model_call_ledger import begin_turn, turn_call_accounting

    monkeypatch.setenv("VOOL_INTENT_ARBITER", "1")
    monkeypatch.setenv("VOOL_KEEP_JUDGE_MODEL", "qwen3:8b")
    monkeypatch.setattr(arbiter, "_local_models_disabled", lambda: False)
    arbiter.reset_breaker()
    reply = mock.Mock()
    reply.raise_for_status = mock.Mock()
    reply.json.return_value = {"message": {"content": '{"answer": "no"}'}}
    context: dict = {}
    begin_turn(context)
    with mock.patch("requests.post", return_value=reply) as post:
        assert arbiter.arbitrate_answered("what grade is my pump oil?", MISS_IN_OTHER_WORDS, source_context=context) == "no"
        assert hook._reply_answers("what grade is my pump oil?", MISS_IN_OTHER_WORDS, context=context, session_id="s") is False
    sent = post.call_args.kwargs["json"]
    assert sent["model"] == "qwen3:8b" and "Asked: \"what grade is my pump oil?\"" in sent["messages"][0]["content"]
    accounting = turn_call_accounting(context)
    assert accounting["completed_calls"] == accounting["judgement_calls"] == 2  # counted, never authorship
    monkeypatch.setattr(arbiter, "_local_models_disabled", lambda: True)
    with mock.patch("requests.post", side_effect=AssertionError("must not be called")):
        assert arbiter.arbitrate_answered("a question", "a reply") is None
        assert hook._reply_answers("a question", "a reply", context={}, session_id="s") is None


def test_a_miss_is_the_answer_to_a_question_about_a_file():
    def never(question, answer):
        raise AssertionError("judged the reply to a file question")

    file_ask = "is there a file about pump oil in my workspace?"
    assert route.points_at_files(file_ask)
    for reply in (MISS_IN_OTHER_WORDS, UNSEARCHED_TEXT, NOT_FOUND_TEXT):
        assert not hook.found_nothing(file_ask, reply, never) or hook.content_absence_claim(reply)
    assert not hook.found_nothing(file_ask, "I looked and there is nothing about pump oil. Shall I try another folder?", never)
    # The same replies to a question about the person's own fact are a miss and are handed on.
    own_ask = "what grade is my pump oil?"
    assert not route.points_at_files(own_ask)
    assert hook.found_nothing(own_ask, UNSEARCHED_TEXT, never)
    assert hook.found_nothing(own_ask, MISS_IN_OTHER_WORDS, lambda q, a: False)
