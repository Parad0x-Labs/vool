"""A keep ask said in words no reader lists: the shape is read, a model judges what the lead means.

Measured on the real app (the v0.8 line base and the two commits after it, an independent question set): "stash
this for me: the spare key is under the blue watering can", "add to my list: replace the greenhouse glass in
spring" and "Two things for the flock book: shearing day is 19 june" wrote no row; the model answered "Got it" or
"added to your list". The lead's words are open-ended, and the same shape carries every other task ("put this into
German: ..."), so the lead is judged by a local model (`core.intent_arbiter.arbitrate_keep_ask`) and not by a list
of verbs. Nobody judging is never a keep.

All names, places and dates here are invented for this contract. The judge is replaced in these tests; its own
measurement on real local models is the probe kept with the fix's evidence.
"""
from __future__ import annotations

from unittest import mock

import pytest

from core import intent_arbiter as arbiter
from core import persistent_memory
from core.memory import everyday_remember as everyday
from core.memory.entries import list_memory_entries
from core.persistent_memory import append_conversation_event, maybe_handle_memory_command
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

CHAT = "keep-ask-chat"


@pytest.fixture(autouse=True)
def _fresh_judgements():
    persistent_memory._JUDGED_LEADS.clear()
    yield
    persistent_memory._JUDGED_LEADS.clear()


def _judge(monkeypatch, answer, seen: list | None = None):
    def judged(lead, body, **_):
        if seen is not None:
            seen.append((lead, body))
        return answer(lead, body) if callable(answer) else answer

    monkeypatch.setattr(arbiter, "arbitrate_keep_ask", judged)


def _say(text: str, chat: str = CHAT) -> tuple[bool, str]:
    handled, reply = maybe_handle_memory_command(text, session_id=chat, access_policy=_policy(chat))
    append_conversation_event(
        session_id=chat, user_input=text, assistant_output=str(reply or "ok"), persist_now=True,
        access_policy=_policy(chat),
    )
    return handled, str(reply or "")


def _facts(chat: str = CHAT) -> list[str]:
    rows = list_memory_entries(access_policy=_policy(chat), limit=200)
    return [str(row.get("fact") or row.get("text") or "") for row in rows]


# --- the shape ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "lead", "body"),
    [
        ("hang on to this for me: the meter key is on the hook by the door.",
         "hang on to this for me", "the meter key is on the hook by the door."),
        ("A couple of things for the recipe binder: the scones need 12 minutes",
         "A couple of things for the recipe binder", "the scones need 12 minutes"),
        ("put this into German: our dog sleeps in the kitchen", "put this into German", "our dog sleeps in the kitchen"),
    ],
)
def test_a_lead_a_colon_and_a_text_are_read_as_such(text, lead, body):
    assert everyday.led_statement(text) == (lead, body)


@pytest.mark.parametrize(
    "text",
    [
        "the standup moved to 9:40 every weekday",  # a clock time is not a lead
        "one thing: is the bakery open on mondays?",  # a question behind the lead
        "hang on to this for me:\nthe meter key is on the hook",  # pasted or multi-line material
        "my dog sleeps in the kitchen",
        "keep: it",
    ],
)
def test_other_text_has_no_lead(text):
    assert everyday.led_statement(text) is None


@pytest.mark.parametrize("lead", ["hang on to this for me", "one for the packing list", "write these on my shopping list",
                                  "for the house file, three things", "lodge it somewhere"])
def test_a_lead_that_points_at_its_text_or_names_what_it_is_for_may_be_an_ask(lead):
    assert everyday.lead_may_ask(lead)


@pytest.mark.parametrize("lead", ["error", "idea", "heads up", "my problem", "guess what", "status", "fun fact"])
def test_a_bare_label_is_never_an_ask(lead):
    assert not everyday.lead_may_ask(lead)


def test_a_judged_lead_carries_what_it_is_for_and_a_count_opens_a_list():
    one = everyday.judged_lead("write this on my shopping list", "bin bags and a washing-up brush")
    assert one.fact == "bin bags and a washing-up brush (my shopping list)" and not one.opens_list
    several = everyday.judged_lead("a couple of things for the recipe binder", "the scones need 12 minutes.")
    assert several.fact == "the scones need 12 minutes (the recipe binder)"
    assert several.opens_list and several.subject == "the recipe binder"
    assert several.under_subject("the flapjacks keep for a week.") == "the flapjacks keep for a week (the recipe binder)"
    plain = everyday.judged_lead("hang on to this for me", "the meter key is on the hook by the door")
    assert plain.fact == "the meter key is on the hook by the door" and not plain.opens_list and plain.subject == ""


# --- the memory lane, in process ---------------------------------------------------------------------------------


def test_a_lead_judged_a_keep_ask_saves_the_text_without_the_lead(isolated_home, monkeypatch):
    seen: list = []
    _judge(monkeypatch, "yes", seen)
    handled, reply = _say("hang on to this for me: the meter key is on the hook by the door.")
    assert handled and reply.startswith("Locked in")
    assert _facts() == ["the meter key is on the hook by the door."]
    assert seen == [("hang on to this for me", "the meter key is on the hook by the door.")]  # judged once, then known


def test_a_lead_judged_another_task_keeps_nothing(isolated_home, monkeypatch):
    _judge(monkeypatch, "no")
    handled, _ = _say("put this into German for me: our dog sleeps in the kitchen")
    assert not handled
    assert _facts() == []


def test_nobody_judging_is_never_a_keep_and_is_asked_again(isolated_home, monkeypatch):
    seen: list = []
    _judge(monkeypatch, None, seen)
    assert not _say("hang on to this for me: the meter key is on the hook by the door.")[0]
    assert _facts() == []
    asked = len(seen)
    assert asked >= 1
    _judge(monkeypatch, "yes", seen)
    assert _say("hang on to this for me: the meter key is on the hook by the door.")[0]
    assert len(seen) > asked  # a failed judgement is not remembered as a no


def test_a_label_or_an_explicit_command_never_reaches_the_judge(isolated_home, monkeypatch):
    def never(lead, body, **_):
        raise AssertionError(f"judge asked about {lead!r}")

    monkeypatch.setattr(arbiter, "arbitrate_keep_ask", never)
    assert not _say("error: connection refused at line 14 of sync.py")[0]
    assert not _say("idea: we could paint the hallway green")[0]
    assert _say("Remember that our rent is due on the 4th")[0]  # the explicit command keeps its own path
    assert _say("note this down: the alarm code changes monthly")[0]  # so does a listed everyday lead
    assert not _say("what is this for: the blue lever under the sink?")[0]


def test_a_note_sent_to_the_notes_is_the_notes_lanes_and_any_other_lead_is_judged(isolated_home, monkeypatch):
    seen: list = []
    _judge(monkeypatch, "yes", seen)
    assert not _say("put this in my notes: the meter key is on the hook by the door")[0]
    assert seen == [] and _facts() == []  # the notes lane's own object: not judged, not a memory row
    handled, reply = _say("drop this in your head for later: the meter key is on the hook by the door")
    assert handled and reply.startswith("Locked in")
    assert _facts() == ["the meter key is on the hook by the door"]


def test_a_judged_list_stays_open_across_a_question(isolated_home, monkeypatch):
    _judge(monkeypatch, lambda lead, body: "yes" if "recipe binder" in lead else "no")
    assert _say("A couple of things for the recipe binder: the scones need 12 minutes.")[0]
    assert not _say("how hot should the oven be for scones?")[0]
    handled, reply = _say("and the flapjacks keep for a week.")
    assert handled and reply.startswith("Locked in")
    facts = _facts()
    # Both items are current: with the list's name in front of each, the two rows shared one fact key and the
    # second write marked both disputed.
    assert "the scones need 12 minutes (the recipe binder)" in facts
    assert "the flapjacks keep for a week (the recipe binder)" in facts
    assert not any("oven" in fact for fact in facts)


def test_a_secret_behind_the_lead_is_not_shown_to_the_judge_and_not_saved(isolated_home, monkeypatch):
    seen: list = []
    _judge(monkeypatch, "yes", seen)
    handled, reply = _say("hang on to this for me: my router password is Xk9-fennel-Quay-2231")
    assert handled and reply == persistent_memory.EVERYDAY_SECRET_REFUSED
    assert _facts() == []
    assert seen and all("fennel" not in body for _, body in seen)


def test_a_change_to_a_saved_fact_is_still_the_update_lanes(isolated_home, monkeypatch):
    def never(lead, body, **_):
        raise AssertionError(f"judge asked about {lead!r}")

    assert _say("Remember that my climbing session is on Tuesdays")[0]
    monkeypatch.setattr(arbiter, "arbitrate_keep_ask", never)
    handled, reply = _say("small change: my climbing session is on Thursdays now")
    assert handled and reply.startswith("Updated.")


# --- the judge's model ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("loaded", "picked"),
    [
        (("qwen3:0.6b", "qwen3:8b", "nomic-embed-text:latest"), "qwen3:8b"),
        (("qwen3:4b",), "qwen3:4b"),
        (("qwen3:0.6b",), ""),  # the helper tag answered yes to every lead of the probe
        (("qwen3:1.7b", "qwen3:0.6b"), ""),
        (("my-custom-model:latest",), ""),  # a tag that does not state its size is not asked
        ((), ""),  # nothing loaded: no cold load in the middle of a turn
    ],
)
def test_the_judge_is_the_largest_loaded_model_and_never_the_helper(monkeypatch, loaded, picked):
    monkeypatch.delenv("VOOL_KEEP_JUDGE_MODEL", raising=False)
    monkeypatch.setattr("core.local_ollama_inventory.loaded_ollama_model_names", lambda **_: loaded)
    monkeypatch.setattr(arbiter, "_local_models_disabled", lambda: False)
    assert arbiter._keep_judge_model() == picked


def test_no_judge_when_local_models_are_switched_off(monkeypatch):
    monkeypatch.setenv("VOOL_KEEP_JUDGE_MODEL", "qwen3:8b")
    monkeypatch.setattr(arbiter, "_local_models_disabled", lambda: True)
    assert arbiter._keep_judge_model() == ""
    with mock.patch("requests.post", side_effect=AssertionError("must not be called")):
        assert arbiter.arbitrate_keep_ask("hang on to this for me", "the meter key is on the hook") is None


def _model_reply(content: str):
    reply = mock.Mock()
    reply.raise_for_status = mock.Mock()
    reply.json.return_value = {"message": {"content": content}}
    return reply


def test_the_keep_question_goes_to_the_judge_model_on_the_chat_models_runner(monkeypatch):
    monkeypatch.setenv("VOOL_INTENT_ARBITER", "1")
    monkeypatch.setenv("VOOL_ARBITER_MODEL", "qwen3:0.6b")
    monkeypatch.setenv("VOOL_KEEP_JUDGE_MODEL", "qwen3:8b")
    monkeypatch.setattr(arbiter, "_local_models_disabled", lambda: False)
    arbiter.reset_breaker()
    with mock.patch("requests.post", return_value=_model_reply('{"answer": "yes"}')) as post:
        assert arbiter.arbitrate_keep_ask("hang on to this for me", "the meter key is on the hook") == "yes"
    sent = post.call_args.kwargs["json"]
    assert sent["model"] == "qwen3:8b"
    prompt = sent["messages"][0]["content"]
    assert 'Lead: "hang on to this for me" Text: "the meter key is on the hook" ->' in prompt
    from core.runtime_provider_defaults import ollama_direct_runner_options

    general = ollama_direct_runner_options("qwen3:8b", bundle_role="general")
    assert {key: sent["options"][key] for key in general} == general
    with mock.patch("requests.post", return_value=_model_reply("maybe")):
        assert arbiter.arbitrate_keep_ask("hang on to this for me", "the meter key is on the hook") is None
    with mock.patch("requests.post", side_effect=RuntimeError("ollama down")):
        assert arbiter.arbitrate_keep_ask("hang on to this for me", "the meter key is on the hook") is None


def test_the_follow_up_question_still_goes_to_the_helper(monkeypatch):
    monkeypatch.setenv("VOOL_INTENT_ARBITER", "1")
    monkeypatch.setenv("VOOL_ARBITER_MODEL", "qwen3:0.6b")
    arbiter.reset_breaker()
    with mock.patch("requests.post", return_value=_model_reply('{"answer": "no"}')) as post:
        assert arbiter.arbitrate_followup("show my documents", "blocked", "nice one") == "no"
    sent = post.call_args.kwargs["json"]
    assert sent["model"] == "qwen3:0.6b"
    assert "Earlier request" in sent["messages"][0]["content"]


# --- an answer that says where a thing is kept is not a save claim ---------------------------------------------------

from tests.test_keep_receipt_decides_save_claims_v08 import NOTHING_KEPT, _fresh, _gate


@pytest.mark.parametrize(
    ("request_text", "reply"),
    [
        ("where is my camping lantern kept?",
         "Your camping lantern is kept in the garage loft. Need help finding it or anything else?"),
        ("where are the spare bulbs?", "The spare bulbs are stored under the stairs, next to the meter."),
        ("where did I put my passport?", "Your passport is filed in the top drawer of the desk."),
    ],
)
def test_where_a_thing_is_kept_is_an_answer_not_a_save_claim(isolated_home, request_text, reply):
    published, record = _gate(reply, dict(NOTHING_KEPT), request=request_text)
    assert published == reply, record


@pytest.mark.parametrize(
    "reply",
    [
        "Got it. Your locker code is now saved in my memory.",
        "Noted, that is kept safe for you.",
        "Your camping lantern note is stored in memory.",
    ],
)
def test_a_save_claim_with_no_write_is_still_corrected(isolated_home, reply):
    published, record = _gate(reply, dict(NOTHING_KEPT))
    assert published != reply, record
    assert "saved" in published.lower() and record.get("memory_receipt") == "claims_without_receipt_corrected"


# --- the judge's call writes none of the reply ---------------------------------------------------------------------


def test_a_judgement_alone_does_not_make_the_turn_model_written():
    from core.action_claim_gate import gate_action_claims
    from core.final_answer_authorship import model_authored_turn
    from core.turn_model_call_ledger import (
        JUDGEMENT_ROLE,
        begin_turn,
        record_provider_call,
        record_provider_call_outcome,
        turn_call_accounting,
    )

    context: dict = {}
    begin_turn(context)
    judged = record_provider_call(context, provider_id="ollama", model_id="qwen3:8b", cost_class="free_local",
                                  call_role=JUDGEMENT_ROLE)
    assert record_provider_call_outcome(context, judged, outcome="completed")
    accounting = turn_call_accounting(context)
    assert accounting["completed_calls"] == 1 and accounting["judgement_calls"] == 1  # still a call of the turn
    assert not model_authored_turn(turn_id="keep-judge-only", source_context=context)[0]
    # The reply a lane composed after the judge said no is the lane's: measured on the real app, "Saved your note
    # ..." was replaced by "No note was saved" with the note on disk.
    lane_reply = "Saved your note 'our dog sleeps in the kitchen'."
    published, record = gate_action_claims(lane_reply, turn_id="keep-judge-only", source_context=context)
    assert published == lane_reply and record["publication"] == "stood_down:not_model_authored"
    answered = record_provider_call(context, provider_id="ollama", model_id="qwen3:8b", cost_class="free_local")
    assert record_provider_call_outcome(context, answered, outcome="completed")
    assert model_authored_turn(turn_id="keep-judge-only", source_context=context)[0]  # an answer call still counts


def test_the_keep_judge_records_its_call_as_a_judgement(monkeypatch):
    from core.turn_model_call_ledger import JUDGEMENT_ROLE, begin_turn, turn_call_accounting

    monkeypatch.setenv("VOOL_INTENT_ARBITER", "1")
    monkeypatch.setenv("VOOL_KEEP_JUDGE_MODEL", "qwen3:8b")
    monkeypatch.setattr(arbiter, "_local_models_disabled", lambda: False)
    arbiter.reset_breaker()
    context: dict = {}
    begin_turn(context)
    with mock.patch("requests.post", return_value=_model_reply('{"answer": "no"}')):
        assert arbiter.arbitrate_keep_ask("put this into German", "our dog sleeps in the kitchen",
                                          source_context=context) == "no"
    accounting = turn_call_accounting(context)
    assert accounting["completed_calls"] == 1 and accounting["judgement_calls"] == 1
    assert arbiter._judgement_role() == JUDGEMENT_ROLE
