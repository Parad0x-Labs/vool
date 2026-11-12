"""Retry and "that folder" resolve against the chat's recorded request, in wordings the first fix never saw."""
from __future__ import annotations

import pytest

from core import conversation_state as cs
from core import runtime_continuity as rc


@pytest.fixture(autouse=True)
def _db(tmp_path):
    rc.configure_runtime_continuity_db_path(tmp_path / "state.db")
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
    "thanks!", "lol ok", "great, and what is a hash map?", "retry in the Documents folder instead",
    "try again but only the mp4s", "now list my pdfs on the desktop", "how do i try again?",
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


# --- round 3: arbitration instead of a retry word list ----------------------------------------------------------------
def _fake(verdict, calls=None):
    def arbiter(request, reason, message):
        if calls is not None:
            calls.append((request, reason, message))
        if isinstance(verdict, Exception):
            raise verdict
        return verdict
    return arbiter


READ = {"status": "blocked_by_mode", "success": False, "mode": "tool_failed",
        "tool_receipts": [{"tool_name": "machine.list_directory", "execution": {"ok": False, "status": "blocked_by_mode"}}]}
WRITE = {"status": "blocked_by_mode", "success": False, "mode": "tool_failed",
         "tool_receipts": [{"tool_name": "operator.delete_note", "execution": {"ok": False, "status": "blocked_by_mode"}}]}


@pytest.mark.parametrize("message", ["k sorted, have another go", "did that", "ok now?", "hmm still nothing?", "its up", "yo, over to u"])
def test_a_wording_no_list_covers_is_decided_by_the_arbiter(message):
    _seed(READ)
    calls = []
    assert cs.resolve("s1", message, arbiter=_fake("retry", calls)).kind == "retry"
    assert calls and calls[0][2] == message and calls[0][0] == ASK


@pytest.mark.parametrize("verdict", ["new", "dismiss", None, RuntimeError("model down")])
def test_anything_but_retry_from_the_arbiter_is_not_a_retry(verdict):
    _seed(READ)
    assert cs.resolve("s1", "hmm ok so", arbiter=_fake(verdict)).kind == "none"


def test_a_write_request_is_never_retried_on_the_arbiter_say_so():
    _seed(WRITE, "delete my note called plans")
    assert cs.resolve("s1", "k sorted, have another go", arbiter=_fake("retry")).kind == "none"


def test_an_unknown_tool_counts_as_not_read_only():
    _seed(READ | {"tool_receipts": [{"tool_name": "mystery.tool", "execution": {"ok": False, "status": "blocked_by_mode"}}]})
    assert cs.resolve("s1", "k sorted, have another go", arbiter=_fake("retry")).kind == "none"


def test_the_arbiter_is_not_asked_for_normal_turns():
    calls = []
    assert cs.resolve("s1", "k sorted, have another go", arbiter=_fake("retry", calls)).kind == "none"  # nothing recorded
    _seed(LISTED)
    assert cs.resolve("s1", "k sorted, have another go", arbiter=_fake("retry", calls)).kind == "none"  # finished request
    _seed(READ, small_talk=2)
    assert cs.resolve("s1", "k sorted, have another go", arbiter=_fake("retry", calls)).kind == "none"  # not fresh
    _seed(READ)
    for closing in ["thanks anyway", "never mind", "no", "later maybe"]:
        assert cs.resolve("s1", closing, arbiter=_fake("retry", calls)).kind == "none"
    assert calls == []


def test_the_arbiter_decision_is_parsed_strictly():
    from core.intent_arbiter import parse_followup_decision

    assert parse_followup_decision('{"decision": "RETRY"}') == "retry"
    assert parse_followup_decision('noise {"decision": "dismiss"} noise') == "dismiss"
    for bad in ["RETRY", '{"decision": "maybe"}', '{"decision": ["RETRY"]}', "", "[]", None]:
        assert parse_followup_decision(bad) is None
