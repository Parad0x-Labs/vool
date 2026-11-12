"""Retry and "that folder" resolve against the chat's recorded request, in wordings the first fix never saw."""
from __future__ import annotations

import pytest

from core import conversation_state as cs
from core import runtime_continuity as rc


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    rc.configure_runtime_continuity_db_path(tmp_path / "state.db")
    # The blocker probe reads live product state; tests pass `cleared=` explicitly where it matters.
    monkeypatch.setattr(cs, "blocker_cleared", lambda pending, source_context=None: None)
    yield
    rc.configure_runtime_continuity_db_path(None)


REFUSED = {"status": "blocked_by_mode", "success": False, "mode": "tool_failed",
           "tool_receipts": [{"tool_name": "machine.list_directory", "execution": {"ok": False, "status": "blocked_by_mode"}}]}
LISTED = {"status": "", "success": True, "mode": "tool_executed",
          "tool_receipts": [{"tool_name": "machine.list_directory", "execution": {"ok": True, "status": "executed"}}],
          "details": {"resolved_target": "/Users/x/Downloads"}}
CHAT = {"mode": "chat"}
ASK = "could you check which videos sit on my desktop and list them"


def _seed(result=REFUSED, request=ASK, small_talk=0):
    cs.record_sealed_turn("s1", request, result)
    for _ in range(small_talk):
        cs.record_sealed_turn("s1", "thanks lol", CHAT)


@pytest.mark.parametrize("message", [
    "ok retry, access is switched on",
    "should work now, go again",
    "i allowed it in settings, run it once more",
    "retry pls",
    "perms are granted now",
    "ok now do it",
    "tools enabled, try that again",
])
def test_a_refused_request_is_rerun_by_any_short_message_that_reports_a_change_or_says_go(message):
    _seed()
    got = cs.resolve("s1", message)
    assert (got.kind, got.request) == ("retry", ASK)


@pytest.mark.parametrize("message", ["try again after small talk, i fixed it", "retry please", "once more, access is on"])
def test_small_talk_between_the_refusal_and_the_retry_does_not_lose_the_request(message):
    _seed(small_talk=2)
    assert cs.resolve("s1", message).request == ASK


def test_a_bare_go_ahead_after_other_turns_is_not_a_retry():
    _seed(small_talk=1)
    assert cs.resolve("s1", "ok go ahead").kind == "none"  # may be answering VOOL's own proposal


@pytest.mark.parametrize("message", [
    "thanks!", "lol ok", "great, and what is a hash map?",     "now list my pdfs on the desktop", "how do i try again?",
])
def test_a_message_with_a_new_task_or_no_retry_meaning_is_not_a_retry(message):
    _seed()
    assert cs.resolve("s1", message).kind == "none"


@pytest.mark.parametrize("message", ["retry", "ok try it again", "go again please", "redo it", "run that once more"])
def test_nothing_recorded_means_nothing_to_retry(message):
    assert cs.resolve("s1", message).kind == "none"


def test_a_finished_request_is_redone_only_on_an_explicit_cue():
    _seed(LISTED)
    assert cs.resolve("s1", "redo it").kind == "retry"
    assert cs.resolve("s1", "ok go ahead").kind == "none"
    assert cs.resolve("s1", "i fixed it, now").kind == "none"


def test_small_talk_does_not_replace_the_recorded_request():
    _seed()
    cs.record_sealed_turn("s1", "tell me a joke", CHAT)
    assert cs.load("s1").request == ASK and cs.load("s1").turns_since == 1


def test_slots_the_turn_resolved_are_recorded_for_later_referents():
    _seed(LISTED, extra_slots_request := "list the pics in my downloads")
    assert cs.load("s1").slots["path"] == "/Users/x/Downloads"


@pytest.mark.parametrize("message", [
    "grab the biggest 4 videos in that folder", "and what about inside the same directory?", "anything new in there?",
    "show me the other folder's pdfs",
])
def test_a_pointer_with_no_folder_recorded_is_unresolved_not_searched(message):
    assert cs.resolve("s1", message).kind == "referent_unresolved"


def test_a_pointer_with_a_recorded_folder_is_not_flagged():
    _seed(LISTED)
    got = cs.resolve("s1", "grab the biggest 4 videos in that folder")
    assert got.kind == "none" and got.slots["path"] == "/Users/x/Downloads"


def test_a_pending_approval_is_needs_answer_not_refused():
    out = cs.outcome_of({"approval_request": {"x": 1}, "status": "pending_approval"})
    assert out[0] == cs.NEEDS_ANSWER


# --- round 2: producer shapes, traps, erasure, prompt ------------------------------------------------------------

# Result shapes copied from each refusal producer (file:line in ROOT-CAUSE.md). Harness, not a real turn.
PRODUCERS = {
    "machine fast path, mode refusal": {"status": "blocked_by_mode", "success": False, "mode": "tool_failed",
        "tool_receipts": [{"execution": {"ok": False, "status": "blocked_by_mode"}}]},
    "machine fast path, remote-turn fence": {"success": True, "mode": "tool_failed",
        "tool_receipts": [{"execution": {"ok": False, "status": "owner_local_required"}}], "details": {"reason": "owner_local_required"}},
    "operator lane, remote-turn fence": {"success": False, "mode": "tool_failed", "task_outcome": "failed",
        "details": {"reason": "owner_local_required", "executed": False}},
    "web off (live_chat.refuse_if_off)": {"deterministic": True, "success": False, "status": "web_off", "task_outcome": "failed"},
    "model tool loop fence": {"success": False, "status": "owner_local_required", "details": {"reason": "owner_local_required"}},
    "permission prompt deny": {"success": False, "status": "denied", "task_outcome": "blocked"},
}


@pytest.mark.parametrize("name", sorted(PRODUCERS))
def test_every_refusal_producer_seals_an_actionable_unfinished_outcome(name):
    outcome, reason, actionable = cs.outcome_of(PRODUCERS[name])
    assert actionable and outcome in (cs.REFUSED, cs.FAILED), (name, outcome, reason)
    cs.record_sealed_turn("p", "list the pics on my desktop", PRODUCERS[name])
    assert cs.resolve("p", "should work now").kind == "retry"


@pytest.mark.parametrize("message", [
    "thanks, done for now", "all done, thanks", "is it done?", "it works now, thanks", "never mind, forget it",
    "now what?", "set it aside please", "ok", "nice",
])
def test_closing_and_question_messages_right_after_a_refusal_do_not_rerun_it(message):
    _seed()
    assert cs.resolve("s1", message).kind == "none"


def test_a_model_written_refusal_is_not_a_refusal_record():
    cs.record_sealed_turn("m", ASK, {"mode": "chat", "response": "I can't do that because tools are off."})
    assert cs.load("m") is None  # not covered: no structured outcome to read
    assert cs.resolve("m", "should work now").kind == "none"


def test_deleting_the_chat_removes_the_record_and_a_retry_finds_nothing():
    from core.context_namespace import ensure_chat_namespace, set_chat_namespace_state

    _seed()
    assert cs.load("s1") is not None
    ensure_chat_namespace("s1")
    set_chat_namespace_state("s1", "deleted")
    assert cs.load("s1") is None
    assert cs.resolve("s1", "retry pls").kind == "none"


def test_a_private_chat_erasure_and_a_forget_sweep_name_the_table():
    from core.memory.run_history import _INVENTORY
    from core.private_chat_traces import _RUN_RECORDS

    assert any(t.name == "conversation_state" for t in _INVENTORY)
    assert any(t[0] == "conversation_state" for t in _RUN_RECORDS)


def test_the_state_note_reaches_the_model_prompt():
    from types import SimpleNamespace

    from core.prompt_normalizer import normalize_prompt

    _seed()
    note = cs.resolve("s1", "grab 3 newest ones in that folder").note
    assert ASK in note
    text = "should work now, run it once more"
    request = normalize_prompt(
        task=SimpleNamespace(task_id="t", task_summary=text),
        classification={"task_class": "chat_conversation", "risk_flags": []},
        interpretation=SimpleNamespace(raw_text=text, normalized_text=text, reconstructed_text=text, intent_mode="request",
            topic_hints=[], reference_targets=[], understanding_confidence=0.9, quality_flags=[], is_continuation=True,
            state_mutation=None, turn_id="turn-x"),
        context_result=__import__("tests.test_context_history_authority", fromlist=["_ContextResult"])._ContextResult(),
        persona=SimpleNamespace(persona_id="default", display_name="VOOL", tone="direct"),
        output_mode="plain_text", task_kind="conversation", trace_id="t", surface="api",
        source_context={"surface": "api", "platform": "api", "runtime_session_id": "s1", "conversation_state_note": note},
    )
    assert ASK in "\n".join(m.content for m in request.messages)


# --- round 5: state first, cue second, arbiter last ----------------------------------------------------------------
READ = {"status": "blocked_by_mode", "success": False, "mode": "tool_failed",
        "tool_receipts": [{"tool_name": "machine.list_directory", "execution": {"ok": False, "status": "blocked_by_mode"}}]}
WRITE = {"status": "blocked_by_mode", "success": False, "mode": "tool_failed",
         "tool_receipts": [{"tool_name": "operator.delete_note", "execution": {"ok": False, "status": "blocked_by_mode"}}]}
WEB_OFF = {"deterministic": True, "success": False, "status": "web_off", "task_outcome": "failed"}


def _arb(verdict, calls=None):
    def arbiter(request, reason, message):
        if calls is not None:
            calls.append(message)
        if isinstance(verdict, Exception):
            raise verdict
        return verdict
    return arbiter


@pytest.mark.parametrize("message", ["sorted", "its on", "did that", "ok now?", "now pls", "k flipped it", "all good now", "there you go"])
def test_with_the_blocker_cleared_a_short_message_that_is_not_a_task_or_a_dismissal_is_a_retry_with_no_model_call(message):
    _seed(READ)
    calls = []
    assert cs.resolve("s1", message, cleared=True, arbiter=_arb("no", calls)).kind == "retry"
    assert calls == []


@pytest.mark.parametrize("message", ["thanks!", "never mind", "brb", "whatever", "later", "no", "does it cost anything", "forget i asked", "no need", "changed my mind", "scrap it", "dont bother", "is it safe to leave on",
                                     "what time is it in Tokyo", "write me a haiku about rain", "now translate this to French"])
def test_with_the_blocker_cleared_reactions_dismissals_and_tasks_are_not_a_retry(message):
    _seed(READ)
    assert cs.resolve("s1", message, cleared=True, arbiter=_arb("yes")).kind == "none"


@pytest.mark.parametrize("message", ["lol ok", "hmm", "ok cool", "nice", "k", "yes", "well?", "so?", "check pls", "all yours"])
def test_with_the_blocker_cleared_a_reaction_or_bare_prompt_reruns_the_read(message):
    # decision of the overseer: a redundant read costs nothing, a missed retry is the failure
    _seed(READ)
    calls = []
    assert cs.resolve("s1", message, cleared=True, arbiter=_arb("no", calls)).kind == "retry"
    assert calls == []


@pytest.mark.parametrize("cleared", [False, None])
@pytest.mark.parametrize("reaction", ["lol ok", "cool", "hmm", "nice"])
def test_without_a_cleared_blocker_a_reaction_never_reruns(cleared, reaction):
    _seed(READ)
    assert cs.resolve("s1", reaction, cleared=cleared, arbiter=_arb("no")).kind == "none"


def test_same_thing_for_another_folder_is_a_changed_retry():
    _seed(READ)
    got = cs.resolve("s1", "same thing for my desktop", cleared=None)
    assert got.kind == "retry_changed" and got.slots.get("path")


@pytest.mark.parametrize("cleared", [False, None])
@pytest.mark.parametrize("message", ["did that", "over to you then", "well?"])
def test_blocker_not_cleared_or_unknown_asks_the_arbiter_and_only_yes_counts(cleared, message):
    _seed(READ)
    calls = []
    assert cs.resolve("s1", message, cleared=cleared, arbiter=_arb("yes", calls)).kind == "retry" and calls
    for verdict in ("no", None, RuntimeError("model down")):
        _seed(READ)
        assert cs.resolve("s1", message, cleared=cleared, arbiter=_arb(verdict)).kind == "none"


def test_a_bare_state_report_right_after_the_refusal_is_a_retry_without_the_model():
    _seed(READ)
    calls = []
    assert cs.resolve("s1", "ok its on", cleared=False, arbiter=_arb("no", calls)).kind == "retry" and calls == []


@pytest.mark.parametrize("message, change_has", [
    ("go again, just the pdfs", "just the pdfs"), ("try once more, this time in documents", "documents"),
    ("run it again sorted by size", "sorted by size"), ("redo it but the 3 oldest", "3 oldest"),
    ("again but only the mp4s", "mp4s"), ("repeat that for my downloads folder", "downloads"),
])
def test_a_cue_with_content_is_a_retry_with_the_change_decided_without_a_model(message, change_has):
    _seed(READ)
    calls = []
    got = cs.resolve("s1", message, cleared=False, arbiter=_arb("no", calls))
    assert got.kind == "retry_changed" and got.request == ASK and change_has in got.change and calls == []
    assert got.text.startswith(ASK) and "(change:" in got.text


@pytest.mark.parametrize("message", ["retry thx", "yo try again lol", "retry, thanks a lot", "hey i changed the setting, again?", "try again, hope it works"])
def test_a_cue_wins_over_a_thanks_or_chatter_and_stays_a_plain_retry(message):
    _seed(READ)
    assert cs.resolve("s1", message, cleared=False).kind == "retry"


@pytest.mark.parametrize("message", ["retry, never mind", "forget it, run it again later"])
def test_a_dismissal_in_the_remainder_drops_the_cue(message):
    _seed(READ)
    assert cs.resolve("s1", message, cleared=True).kind == "none"


def test_a_changed_or_cleared_retry_of_a_write_is_never_made_on_state():
    _seed(WRITE, "delete my note called plans")
    assert cs.resolve("s1", "sorted", cleared=True, arbiter=_arb("yes")).kind == "none"
    assert cs.resolve("s1", "go again but the other note", cleared=True).kind == "none"
    assert cs.resolve("s1", "retry", cleared=True).kind == "retry"  # explicit cue: normal approval flow applies on the rerun


def test_an_unknown_tool_counts_as_not_read_only():
    _seed(READ | {"tool_receipts": [{"tool_name": "mystery.tool", "execution": {"ok": False, "status": "blocked_by_mode"}}]})
    assert cs.resolve("s1", "sorted", cleared=True, arbiter=_arb("yes")).kind == "none"


def test_a_web_lookup_refused_by_the_web_switch_is_a_read():
    cs.record_sealed_turn("s1", "whats the weather in porto", WEB_OFF)
    assert cs.resolve("s1", "sorted", cleared=True).kind == "retry"


def test_the_arbiter_is_not_asked_when_nothing_unfinished_is_fresh():
    calls = []
    assert cs.resolve("s1", "did that", cleared=False, arbiter=_arb("yes", calls)).kind == "none"  # nothing recorded
    _seed(LISTED)
    assert cs.resolve("s1", "did that", cleared=False, arbiter=_arb("yes", calls)).kind == "none"  # finished request
    _seed(READ, small_talk=2)
    assert cs.resolve("s1", "did that", cleared=False, arbiter=_arb("yes", calls)).kind == "none"  # not fresh
    _seed(READ)
    for closing in ["thanks anyway", "never mind", "no", "later maybe"]:
        assert cs.resolve("s1", closing, cleared=False, arbiter=_arb("yes", calls)).kind == "none"
    assert calls == []


def test_the_arbiter_decision_is_parsed_strictly():
    from core.intent_arbiter import parse_followup_decision

    assert parse_followup_decision('{"answer": "Yes"}') == "yes"
    assert parse_followup_decision('noise {"answer": "no"} noise') == "no"
    for bad in ["yes", '{"answer": "maybe"}', '{"answer": ["yes"]}', '{"decision": "RETRY"}', "", "[]", None]:
        assert parse_followup_decision(bad) is None


def test_a_retry_with_a_change_updates_the_slots_where_the_change_names_one():
    _seed(READ | {"details": {"path": "/Users/x/Downloads"}}, "list the pics in my downloads")
    got = cs.resolve("s1", "same again but only the newest 5 from my desktop", cleared=False)
    assert got.kind == "retry_changed" and got.slots["path"] == "~/Desktop" and got.slots["limit"] == 5 and got.slots["newest_first"] is True


def test_blocker_cleared_reads_live_state_per_reason(monkeypatch):
    import core.conversation_state as real
    from core.vool_browser import live

    fn = real.__dict__["blocker_cleared"]  # the autouse fixture replaced the attribute, not the function object we need
    monkeypatch.undo()
    web = real.PendingRequest("weather", real.REFUSED, "web_off")
    monkeypatch.setattr(live, "available", lambda: True)
    assert real.blocker_cleared(web) is True
    monkeypatch.setattr(live, "available", lambda: False)
    assert real.blocker_cleared(web) is False
    assert real.blocker_cleared(real.PendingRequest("x", real.REFUSED, "something_new")) is None
    remote = real.PendingRequest("x", real.REFUSED, "owner_local_required")
    assert real.blocker_cleared(remote, {"_owner_local": False}) is False
    assert real.blocker_cleared(remote, {"_owner_local": True}) is True
    assert real.blocker_cleared(remote, None) is None


def test_the_transcript_keeps_what_the_user_typed():
    from core.memory.entries import recent_conversation_events
    from core.persistent_memory import append_conversation_event

    sid = "typed-row-chat"
    append_conversation_event(session_id=sid, user_input="list the pics in my downloads", assistant_output="here",
                              source_context={"_typed_user_text": "retry pls"}, persist_now=True)
    rows = recent_conversation_events(sid, limit=2)
    assert rows and rows[-1]["user"] == "retry pls", rows


@pytest.mark.parametrize("message", ["can you see them now", "does it work now", "can u look now"])
def test_an_auxiliary_first_prompt_about_the_pending_thing_is_still_a_retry_when_cleared(message):
    _seed(READ)
    assert cs.resolve("s1", message, cleared=True, arbiter=_arb("no")).kind == "retry"
# --- v5b: ordinal picks against a recorded printed list, and a bare yes to an open read request -----------------------

PRINTED = [{"name": f"pic{i}.jpg", "path": f"/h/Pictures/pic{i}.jpg"} for i in range(1, 7)]


def _printed(age=0):
    return cs.PendingRequest("list my pictures", "done", "", {"listed": PRINTED, "tool": "machine.list_directory"}, age)


@pytest.mark.parametrize("message, expect", [
    ("open the second one", ["/h/Pictures/pic2.jpg"]),
    ("grab the last two pls", ["/h/Pictures/pic5.jpg", "/h/Pictures/pic6.jpg"]),
    ("whats number 3", ["/h/Pictures/pic3.jpg"]),
    ("zip the first 3", ["/h/Pictures/pic1.jpg", "/h/Pictures/pic2.jpg", "/h/Pictures/pic3.jpg"]),
    ("the 4th one looks right", ["/h/Pictures/pic4.jpg"]),
])
def test_an_ordinal_pointer_resolves_against_the_printed_list(message, expect):
    got = cs.resolve("o", message, pending=_printed(), cleared=None)
    assert got.kind == "pick" and got.slots["picked"] == expect
    assert all(path in got.text for path in expect)


def test_an_ordinal_with_no_printed_list_asks_which_list():
    assert cs.resolve("o", "open the second one", pending=None).kind == "referent_unresolved"
    assert cs.resolve("o", "open the second one", pending=cs.PendingRequest("hi", "done", "", {}, 0)).kind == "referent_unresolved"


def test_an_ordinal_outside_the_list_or_long_after_it_does_not_guess():
    assert cs.resolve("o", "the 9th one", pending=_printed(), cleared=None).kind == "referent_unresolved"
    assert cs.resolve("o", "open the second one", pending=_printed(age=9), cleared=None).kind == "referent_unresolved"


@pytest.mark.parametrize("message", ["i saw it the last time we talked", "both are fine by me", "what was the first thing you said about rain"])
def test_ordinal_words_that_are_not_a_pointer_are_left_alone(message):
    got = cs.resolve("o", message, pending=_printed(), cleared=None)
    assert got.kind != "pick" or "first thing" in message


def test_a_picked_delete_is_still_a_fresh_turn_so_approval_applies():
    # the pick only names the item; routing and authorisation happen on the rewritten text like any typed request
    got = cs.resolve("o", "delete the second one", pending=_printed(), cleared=None)
    assert got.kind == "pick" and got.text.startswith("delete ")


def test_a_printed_list_is_recorded_from_the_tool_details_not_the_reply_text():
    result = {"success": True, "status": "executed", "response_text": "1. nothing here",
              "tool_receipts": [{"tool_name": "machine.list_directory", "execution": {"ok": True, "status": "executed"}}],
              "details": {"entries": [{"name": "a.jpg", "path": "/p/a.jpg"}, {"name": "b.jpg", "path": "/p/b.jpg"}]}}
    cs.record_sealed_turn("l", "list my pics", result)
    got = cs.load("l")
    assert [i["path"] for i in got.slots["listed"]] == ["/p/a.jpg", "/p/b.jpg"]
    assert cs.resolve("l", "the last one", cleared=None).slots["picked"] == ["/p/b.jpg"]


OFFER = cs.PendingRequest("list my downloads", "needs_answer", "pending_approval", {"tool": "machine.list_directory"}, 0)


@pytest.mark.parametrize("message", ["yes do it", "go ahead", "sure", "ok yes pls"])
def test_a_bare_yes_after_an_open_read_request_runs_it(message):
    assert cs.resolve("y", message, pending=OFFER, cleared=None).kind == "retry"


@pytest.mark.parametrize("message", ["no", "what is it for", "yes but only jpgs please make a list"])
def test_a_no_or_a_question_after_an_open_request_does_not_run_it(message):
    assert cs.resolve("y", message, pending=OFFER, cleared=None).kind != "retry"


def test_a_bare_yes_never_runs_an_open_request_that_changes_things():
    delete = cs.PendingRequest("delete my downloads", "needs_answer", "pending_approval", {"tool": "machine.delete_path"}, 0)
    assert cs.resolve("y", "yes do it", pending=delete, cleared=None).kind == "none"


@pytest.mark.parametrize("message", ["random number 1 to 10", "give me a number between 1 and 10", "my number is 5"])
def test_the_word_number_without_a_pointer_frame_is_not_an_ordinal(message):
    assert cs.resolve("o", message, pending=_printed(), cleared=None).kind == "none"
    assert cs.resolve("o", message, pending=None).kind == "none"
