"""A restore puts a saved fact back to an earlier value through the fact's stored history.

Measured on the v0.8 line (the long-chat proof, the diagnosis probes of 2026-11-16, and the real app on the commits
of this line): "found the first one, I'm using it again" left the newest number current; a restore sentence with a
change word ("I went back to the previous desk") was itself saved as the desk's value; "they moved my saddle rack,
it's number 73 now" was not read as an update at all, because "they" was taken for the thing that moved.

Here: an update leaves its old value as history (a replaced row that names its successor); a restore is bound to
that history, by the fact's whole subject or by what this chat changed last, and writes the earlier value back as
the current one. "one" as a count is no pointer and no value. Two things that share words are two facts.

All names, numbers and rooms are invented for this contract. The judge is replaced in these tests.
"""
from __future__ import annotations

from unittest import mock

import pytest

from core import intent_arbiter as arbiter
from core import persistent_memory
from core.memory import everyday_remember as everyday
from core.memory.entries import list_memory_entries, list_replaced_entries
from tests.test_everyday_remember_v08 import _say
from tests.test_keep_receipt_decides_save_claims_v08 import _turn
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # noqa: F401  (isolated_home is a fixture)

CHAT = "restore-chat"


def _current(chat: str = CHAT) -> list[str]:
    return [str(row.get("fact") or row.get("text") or "") for row in list_memory_entries(access_policy=_policy(chat), limit=200)]


def _history(chat: str = CHAT) -> list[str]:
    return [str(row.get("text") or "") for row in list_replaced_entries(access_policy=_policy(chat))]


def _no_judge(monkeypatch):
    def never(*args, **kwargs):
        raise AssertionError("the judge was asked")

    monkeypatch.setattr(arbiter, "arbitrate_restore", never)


def _peg_chain(*numbers: int) -> None:
    assert _say(f"Remember that my coat peg is number {numbers[0]}", CHAT)[0]
    for number in numbers[1:]:
        handled, reply = _say(f"my coat peg is number {number} now", CHAT)
        assert handled and reply.startswith("Updated."), reply


# --- the readers ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "back"),
    [("scrap that, I'm keeping the first one.", True), ("that one again", True), ("is it still there", True),
     ("I lost one glove last week.", False), ("one more thing about the hall", False), ("my coat peg is by the door", False)],
)
def test_one_as_a_count_does_not_point_back(text, back):
    assert everyday.points_back(text) is back


def test_a_fact_is_named_by_its_whole_subject_not_by_a_shared_word():
    assert everyday.names_subject("I'm back on my old coat peg", "my coat peg is number 14")
    assert not everyday.names_subject("my bike shed door code is 7702", "my boat shed door code is 4410")
    assert not everyday.names_subject("I dropped one rating point", "my chess rating is 1500")


def test_who_made_the_change_is_not_what_changed(isolated_home, monkeypatch):
    _no_judge(monkeypatch)
    assert _say("Remember that my coat peg is number 14", CHAT)[0]
    handled, reply = _say("they moved my coat peg, it's number 22 now.", CHAT)
    assert handled and reply.startswith("Updated."), reply
    assert _current() == ["they moved my coat peg, it's number 22 now"]
    assert _history() == ["my coat peg is number 14"]


# --- the restore ---------------------------------------------------------------------------------------------------


def test_a_restore_that_names_the_thing_puts_the_earlier_value_back(isolated_home, monkeypatch):
    _no_judge(monkeypatch)  # "back on my old ..." is a wording the capsule marking already reads
    _peg_chain(14, 22)
    handled, reply = _say("the swap was cancelled, I'm back on my old coat peg.", CHAT)
    assert handled and reply == "Changed back. I now have: my coat peg is number 14."
    assert _current() == ["my coat peg is number 14"]
    assert "my coat peg is number 22 now" in _history()  # the value that was undone is history, not gone
    assert not any("cancelled" in fact for fact in _current() + _history())  # the restore sentence is never a value


def test_the_previous_value_and_the_first_value_are_read_from_the_history(isolated_home, monkeypatch):
    _no_judge(monkeypatch)
    _peg_chain(4, 11, 19)
    assert _say("I went back to the previous coat peg.", CHAT)[1] == "Changed back. I now have: my coat peg is number 11 now."
    assert _current() == ["my coat peg is number 11 now"]
    assert _say("turns out I'm back on my first coat peg.", CHAT)[1] == "Changed back. I now have: my coat peg is number 4."
    assert _current() == ["my coat peg is number 4"]


def test_a_restore_with_no_noun_binds_to_what_this_chat_changed_last(isolated_home, monkeypatch):
    seen: list = []

    def judged(message, current, earlier, **_):
        seen.append((message, current, earlier))
        return "yes"

    monkeypatch.setattr(arbiter, "arbitrate_restore", judged)
    assert _say("Remember that my yoga class is in the oak room", CHAT)[0]
    assert _say("my yoga class moved to the elm room.", CHAT)[1].startswith("Updated.")
    handled, reply = _say("scrap that, I'm keeping the first one.", CHAT)
    assert handled and reply == "Changed back. I now have: my yoga class is in the oak room."
    assert _current() == ["my yoga class is in the oak room"]
    assert seen == [("scrap that, I'm keeping the first one.", "my yoga class moved to the elm room", "my yoga class is in the oak room")]


def test_nobody_judging_restores_nothing(isolated_home, monkeypatch):
    monkeypatch.setattr(arbiter, "arbitrate_restore", lambda *a, **k: None)
    assert _say("Remember that my yoga class is in the oak room", CHAT)[0]
    assert _say("my yoga class moved to the elm room.", CHAT)[1].startswith("Updated.")
    _say("scrap that, I'm keeping the first one.", CHAT)
    assert "my yoga class is in the oak room" not in _current()
    assert any("elm room" in fact for fact in _current())


def test_a_question_a_count_and_a_new_value_are_not_restores(isolated_home, monkeypatch):
    _no_judge(monkeypatch)
    _peg_chain(14, 22)
    assert not _say("which coat peg is mine?", "another-chat")[0]  # a question alone is never judged
    assert not _say("I lost one glove last week.", CHAT)[0]
    assert _current() == ["my coat peg is number 22 now"]
    handled, reply = _say("my coat peg is number 30 now", CHAT)  # a new value is an update
    assert handled and reply.startswith("Updated.")
    assert _current() == ["my coat peg is number 30 now"]


def test_a_statement_about_the_thing_that_is_no_restore_changes_nothing(isolated_home, monkeypatch):
    monkeypatch.setattr(arbiter, "arbitrate_restore", lambda *a, **k: "no")
    _peg_chain(14, 22)
    _say("my coat peg has a brass hook", CHAT)
    assert "my coat peg is number 22 now" in _current() and "my coat peg is number 14" not in _current()


def test_two_things_that_share_words_are_two_facts(isolated_home, monkeypatch):
    _no_judge(monkeypatch)
    assert _say("Remember that my boat shed door code is 4410", CHAT)[0]
    assert _say("Remember that my bike shed door code is 7702", "other-chat")[0]
    facts = _current()
    assert "my boat shed door code is 4410" in facts and "my bike shed door code is 7702" in facts
    assert _history() == []


def test_a_restore_said_beside_a_question_is_put_back_by_the_keep_step(isolated_home, monkeypatch):
    monkeypatch.setattr(arbiter, "arbitrate_restore", lambda *a, **k: "yes")
    _peg_chain(14, 22)
    handled, _reply, receipt = _turn("the swap was undone, my old coat peg is mine again - which peg is mine?", CHAT)
    assert not handled  # the question is the model's to answer
    assert receipt["in_passing"] and receipt["saved"] == ["my coat peg is number 14"]
    assert _current() == ["my coat peg is number 14"]


def test_no_history_means_nothing_to_restore(isolated_home, monkeypatch):
    _no_judge(monkeypatch)
    assert _say("Remember that my coat peg is number 14", CHAT)[0]
    assert persistent_memory._restore_target(
        "the swap was cancelled, I'm back on my old coat peg.", access_policy=_policy(CHAT), session_id=CHAT
    ) is None


# --- the judge ------------------------------------------------------------------------------------------------------


def test_the_restore_question_is_a_judgement_on_the_chat_models_runner(monkeypatch):
    from core.turn_model_call_ledger import begin_turn, turn_call_accounting

    monkeypatch.setenv("VOOL_INTENT_ARBITER", "1")
    monkeypatch.setenv("VOOL_KEEP_JUDGE_MODEL", "qwen3:8b")
    monkeypatch.setattr(arbiter, "_local_models_disabled", lambda: False)
    arbiter.reset_breaker()
    reply = mock.Mock()
    reply.raise_for_status = mock.Mock()
    reply.json.return_value = {"message": {"content": '{"answer": "yes"}'}}
    context: dict = {}
    begin_turn(context)
    with mock.patch("requests.post", return_value=reply) as post:
        assert arbiter.arbitrate_restore("scrap that, I'm keeping the first one", "my yoga class moved to the elm room",
                                         "my yoga class is in the oak room", source_context=context) == "yes"
    sent = post.call_args.kwargs["json"]
    assert sent["model"] == "qwen3:8b"
    assert 'Message: "scrap that, I\'m keeping the first one" ->' in sent["messages"][0]["content"]
    accounting = turn_call_accounting(context)
    assert accounting["completed_calls"] == 1 and accounting["judgement_calls"] == 1
    monkeypatch.setattr(arbiter, "_local_models_disabled", lambda: True)
    with mock.patch("requests.post", side_effect=AssertionError("must not be called")):
        assert arbiter.arbitrate_restore("a", "b", "c") is None


# --- a value said as a story, its change, and its undoing ------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("oh, the club gave me a new darts average this season: 61.", True),
        ("my new bus pass number: 40417", True),
        ("my boat shed door code is 4410.", True),  # "code" is a task word; a bare value makes it a fact
        ("my code is throwing an error", False),
        ("my script is 300 lines", False),
        ("note for the build: 3", False),  # nothing of the person's
        ("what did the club give me this season: 61?", False),
        ("I need the report by: 5 March", False),
    ],
)
def test_a_statement_that_gives_a_thing_of_mine_its_value_is_a_fact(text, kept):
    assert bool(everyday.stated_facts(text)) is kept


def test_a_change_told_as_a_story_replaces_the_one_fact_it_names(isolated_home, monkeypatch):
    _no_judge(monkeypatch)
    _turn("oh, the club gave me a new darts average this season: 61.", CHAT)
    assert _current() == ["oh, the club gave me a new darts average this season: 61."] or _current() == [
        "the club gave me a new darts average this season: 61."]
    handled, reply, _ = _turn("they recounted my darts average, it's 58 now.", CHAT)
    assert handled and reply.startswith("Updated."), reply
    assert _current() == ["they recounted my darts average, it's 58 now"]
    assert len(_history()) == 1 and "61" in _history()[0]


def test_the_undoing_names_the_fact_by_a_shorter_phrase_and_a_count_changes_nothing(isolated_home, monkeypatch):
    monkeypatch.setattr(arbiter, "arbitrate_restore", lambda *a, **k: "yes")
    _turn("oh, the club gave me a new darts average this season: 61.", CHAT)
    _turn("they recounted my darts average, it's 58 now.", CHAT)
    handled, _reply, receipt = _turn("the recount was thrown out, my old average is the real one again - what's my average?", CHAT)
    assert not handled and receipt["in_passing"] and any("61" in fact for fact in receipt["saved"])
    assert len(_current()) == 1 and "61" in _current()[0]
    _no_judge(monkeypatch)
    _turn("i dropped one point off my game last night.", CHAT)
    assert len(_current()) == 1 and "61" in _current()[0]


def test_a_shorter_phrase_that_fits_two_facts_binds_neither(isolated_home, monkeypatch):
    _no_judge(monkeypatch)
    for chat, thing in (("a", "boat shed"), ("b", "bike shed")):
        assert _say(f"Remember that my {thing} door code is 1200", chat)[0]
        assert _say(f"my {thing} door code is 3400 now", chat)[1].startswith("Updated.")
    assert persistent_memory._restore_target(
        "I'm back on my old door code.", access_policy=_policy("c"), session_id="c"
    ) is None


# --- the capsule marking --------------------------------------------------------------------------------------------


def test_the_marking_does_not_chain_two_things_on_a_shared_word_or_read_one_as_a_value():
    from core.updated_fact_marking import _different_things, _pair_kind, _values

    def entry(day: str, body: str) -> dict:
        from core.updated_fact_marking import _anchors

        # The marking reads anchors and values from the statement, and keeps the delivered line beside them.
        return {"line": f"- user said (stated {day}): {body}", "anchors": _anchors(body), "values": _values(body)}

    hut = entry("2026-11-01", "my boat shed door code is 4410.")
    bike = entry("2026-11-02", "my bike shed door code is 7702.")
    assert _different_things(hut, bike) and _pair_kind(hut, bike) == "none"
    old = entry("2026-11-01", "my coat peg is number 14.")
    new = entry("2026-11-02", "my coat peg is number 22 now.")
    assert not _different_things(old, new) and _pair_kind(old, new) == "numeric"
    assert "one" not in _values("found the first one, using it again")
    assert "one" not in _values("i dropped one point off my game") and "14" in _values("my coat peg is number 14")


# --- found on the real app with the first cut of this step -------------------------------------------------------------


def test_a_thing_placed_in_time_names_the_fact_by_a_shorter_phrase(isolated_home, monkeypatch):
    _no_judge(monkeypatch)
    assert _say("Remember that my studio bench is number 4", CHAT)[0]
    for number in (11, 19):
        assert _say(f"my studio bench is number {number} now", CHAT)[1].startswith("Updated.")
    handled, reply = _say("I went back to the previous bench.", CHAT)
    assert handled and reply == "Changed back. I now have: my studio bench is number 11 now."
    assert _current() == ["my studio bench is number 11 now"]


def test_a_went_back_sentence_with_nothing_to_put_back_is_never_saved_as_the_value(isolated_home, monkeypatch):
    _no_judge(monkeypatch)
    assert _say("Remember that my studio bench is number 4", CHAT)[0]
    handled, _ = _say("I went back to the previous bench.", CHAT)  # one value only: there is no history
    assert not handled
    assert _current() == ["my studio bench is number 4"]


def test_the_row_put_back_is_the_earlier_row_as_it_stood(isolated_home, monkeypatch):
    _no_judge(monkeypatch)
    assert _say("Remember that my coat peg is number 14", CHAT)[0]
    assert _say("they moved my coat peg, it's number 22 now.", CHAT)[1].startswith("Updated.")
    assert _say("the move was cancelled, I'm back on my old coat peg.", CHAT)[1] == "Changed back. I now have: my coat peg is number 14."
    assert _current() == ["my coat peg is number 14"]


def test_mine_is_not_a_word_the_saved_fact_must_hold(isolated_home):
    from core.agent_runtime.cross_chat_recall import saved_memory_answers

    assert _say("Remember that my coat peg is number 14", CHAT)[0]
    assert _say("they moved my coat peg, it's number 22 now.", CHAT)[1].startswith("Updated.")
    assert saved_memory_answers("which coat peg is mine?", session_id="ask", source_context={"_owner_local": True})


def test_a_forget_takes_the_whole_history_of_the_fact_whichever_row_its_words_match(isolated_home, monkeypatch):
    from core.persistent_memory import maybe_handle_memory_command

    monkeypatch.setattr(arbiter, "arbitrate_restore", lambda *a, **k: "yes")
    _turn("oh, the club gave me a new darts average this season: 61.", CHAT)
    _turn("they recounted my darts average, it's 58 now.", CHAT)
    _turn("the recount was thrown out, my old average is the real one again - what's my average?", CHAT)
    assert len(_current()) == 1 and "61" in _current()[0]
    # The words of the forget are in the undone row only ("my darts average"); the restored row says "a new darts
    # average". All three rows are one fact's history and go together.
    handled, reply = maybe_handle_memory_command("forget my darts average.", session_id="elsewhere",
                                                 access_policy=_policy("elsewhere"))
    assert handled and "Removed 3 memory entries" in reply, reply
    assert _current() == [] and _history() == []
