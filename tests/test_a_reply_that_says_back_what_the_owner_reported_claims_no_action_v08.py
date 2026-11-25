"""A reply that says back what the owner reported in this chat is not VOOL claiming it did something.

Measured on the real app on two cloud models: the owner wrote "the taxi to the station is booked for 5:40 and costs 22
pounds.", asked "what time is the taxi and what does it cost?", the model answered "The taxi is booked for 5:40 and
costs £22." and the reply delivered was "No calendar is configured or chosen in Settings yet ...". The passive done
form with a calendar word read as a booking VOOL said it had made.

The owner's own statement of the same done thing, in this chat, is what makes the sentence a report said back. Pinned
beside it: a request, a question, a wish, a negation, a detail the owner never gave, VOOL speaking of itself, a
forgotten statement and a statement only the assistant made all leave the sentence a claim, corrected as before.
"""
from __future__ import annotations

import itertools

import pytest

import core.context_retrieval as cr
from core.action_claim_gate import _owner_reported, _owner_words, gate_action_claims
from core.grounding_lifecycle import record_model_authorship, register_required
from core.grounding_lifecycle import reset_for_tests as reset_lifecycles
from core.runtime_paths import configure_runtime_home
from core.semantic.semantic_admissions import bound_request_context
from core.turn_model_call_ledger import begin_turn

CHAT = "chat-fete"
SAID = "the bouncy castle is booked for 2:30 and costs 85 pounds."
ASK = "what time is the bouncy castle and what does it cost?"
_ids = itertools.count()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    from core.final_answer_authorship import reset_for_tests

    profile = tmp_path / "profile"
    profile.mkdir()
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    configure_runtime_home(profile)
    from storage.migrations import run_migrations

    run_migrations()
    # A served profile has its forget ledger from boot; without one the readers support nothing.
    from core.memory.revocation import initialize_memory_home

    initialize_memory_home(profile)
    reset_lifecycles()
    reset_for_tests()
    yield str(profile)
    reset_lifecycles()
    reset_for_tests()
    configure_runtime_home(None)


def gate(reply: str, *, said=(SAID,), request: str = ASK, assistant=()) -> tuple[str, dict]:
    rid = f"req:owner-reported:{next(_ids)}"
    history = [{"role": "user", "content": text} for text in said]
    history += [{"role": "assistant", "content": text} for text in assistant]
    history.append({"role": "user", "content": request})
    with bound_request_context(rid):
        context = {"session_id": CHAT, "chat_id": CHAT, "request_id": rid, "turn_id": rid, "conversation_history": history}
        register_required(context, request_text=request, reason_codes=("test",))
        record_model_authorship(context)
        begin_turn(context)
        return gate_action_claims(reply, turn_id=rid, source_context=context, request_text=request, connected=lambda name: False)


# ------------------------------------------------------------ said back, delivered unchanged


@pytest.mark.parametrize("reply", [
    "The bouncy castle is booked for 2:30 and costs £85.",
    "The bouncy castle is booked for 2:30 and costs 85 pounds.",
    "It is booked for 2:30.",
])
def test_what_the_owner_reported_is_said_back_unchanged(reply):
    published, record = gate(reply)
    assert published == reply, record
    assert record["publication"] == "stood_down:owner_reported"
    assert record["owner_reported_sentences"]


@pytest.mark.parametrize("reply", [
    "The coach to the showground is booked for 7:10 and costs 28 pounds.",
    "The coach is booked for 7:10 and costs £28.",
    "The coach to the showground is booked for 7:10, and it costs 28 pounds.",
])
def test_a_place_the_thing_goes_to_does_not_make_the_report_a_plan(reply):
    # The first cut read the "to" of "the coach to the showground is booked" as the "to" of "to be booked" and set
    # nothing aside: measured on the real app, where the owner's sentence had those words.
    published, record = gate(reply, said=("the coach to the showground is booked for 7:10 and costs 28 pounds.",),
                             request="what time is the coach and what does it cost?")
    assert published == reply, record
    assert record["publication"] == "stood_down:owner_reported"


@pytest.mark.parametrize("owner_sentence", [
    "the coach is to be booked for 7:10.",
    "I need to get the coach booked for 7:10.",
    "the coach has to be booked for 7:10.",
])
def test_a_plan_said_with_to_is_still_not_a_report(owner_sentence):
    _corrected("The coach is booked for 7:10.", said=(owner_sentence,), request="so where are we with it")


def test_a_wording_the_gate_never_read_as_a_claim_is_unchanged_as_before():
    reply = "Bouncy castle's booked for 2:30 and it's £85."
    published, record = gate(reply)
    assert published == reply and record["publication"] == "stood_down:no_action_claims"


def test_a_statement_and_its_question_in_one_message():
    request = "the bouncy castle is booked for 2:30. what time did I say it was booked for?"
    published, record = gate("It is booked for 2:30.", said=(), request=request)
    assert published == "It is booked for 2:30.", record


def test_the_second_done_word_shares_the_owners_subject():
    owner = [_owner_words("I cancelled the papers and moved the appointment to the 9th.")]
    assert _owner_reported("The papers were cancelled and the appointment was moved to the 9th.", owner)
    assert not _owner_reported("The papers were cancelled and the appointment was moved to the 19th.", owner)


# ------------------------------------------------------------ still VOOL's claim, corrected as before


def _corrected(reply, **kwargs):
    published, record = gate(reply, **kwargs)
    assert record["publication"] == "action_claims_corrected", record
    assert "No calendar is configured" in published
    assert reply not in published
    return published


def test_a_request_is_not_a_report():
    _corrected("The bouncy castle is booked for 2:30.", said=(), request="book the bouncy castle for 2:30.")


@pytest.mark.parametrize("owner_sentence", [
    "is the bouncy castle booked for 2:30?",
    "I want the bouncy castle booked for 2:30.",
    "get the bouncy castle booked for 2:30.",
    "the bouncy castle is not booked for 2:30 yet.",
    "the bouncy castle isn't booked for 2:30.",
    "the bouncy castle should be booked for 2:30.",
    "the bouncy castle will be booked for 2:30 if the weather holds.",
])
def test_a_question_a_wish_a_negation_or_a_plan_is_not_a_report(owner_sentence):
    _corrected("The bouncy castle is booked for 2:30.", said=(owner_sentence,), request="so where are we with it")


@pytest.mark.parametrize("reply", [
    "The bouncy castle is booked for 2:30 on Saturday.",      # a day
    "The bouncy castle is booked for 3:30.",                    # another time
    "The bouncy castle is booked for 2:30 with Mr Tarn.",      # a name
    "The bouncy castle is booked for 2:30 and costs £95.",     # another amount
    "The second bouncy castle is booked for 2:30.",            # another thing
])
def test_a_value_or_a_thing_the_owner_never_gave_keeps_the_sentence_a_claim(reply):
    _corrected(reply)


@pytest.mark.parametrize("reply", [
    "The bouncy castle is booked for 2:30 pickup and costs £85.",
    "The bouncy castle is booked for a 2:30 start, costing £85 in all.",
])
def test_a_plain_word_added_after_the_done_word_changes_nothing(reply):
    # Measured on the real app on the cut that asked for every word: "The minibus is booked for 6:15 pickup and
    # costs £31." was still replaced by the no-calendar line.
    published, record = gate(reply)
    assert published == reply, record
    assert record["publication"] == "stood_down:owner_reported"


def test_a_sentence_that_names_nothing_before_its_done_word_must_be_the_owners_in_every_word():
    owner = [_owner_words(SAID)]
    assert _owner_reported("Booked for 2:30.", owner)
    assert not _owner_reported("Booked the marquee for 2:30.", owner)


def test_another_thing_than_the_one_reported_is_a_claim():
    _corrected("The face painter is booked for 3:15.", request="book the face painter for 3:15.")


def test_vool_speaking_of_itself_is_a_claim_whatever_the_owner_said():
    _corrected("I've booked the bouncy castle for 2:30.")
    _corrected("Done! The bouncy castle is booked for 2:30.")


def test_beside_a_claim_of_vools_own_nothing_is_set_aside():
    published, record = gate("The bouncy castle is booked for 2:30. I'll remind you at 2.")
    assert record["publication"] == "action_claims_corrected", record
    assert "booked for 2:30" not in published


def test_a_statement_only_the_assistant_made_is_not_the_owners_report():
    _corrected("The bouncy castle is booked for 2:30.", said=("what do we still need for the fete?",),
               assistant=("The bouncy castle is booked for 2:30.",), request="and the castle?")


def test_a_forgotten_statement_supports_nothing(monkeypatch):
    monkeypatch.setattr(cr, "revoked_tokens_for_chat", lambda *_a, **_k: ("castle",))
    _corrected("The bouncy castle is booked for 2:30.")


def test_an_unreadable_forget_ledger_supports_nothing(monkeypatch):
    def unreadable(*_a, **_k):
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(cr, "revoked_tokens_for_chat", unreadable)
    _corrected("The bouncy castle is booked for 2:30.")


# ------------------------------------------------------------ the owner's words quoted back (the second reader)


def _validated(reply: str, *, said: str, ask: str) -> dict:
    from core.agent_runtime.action_honesty_validator import enforce_final_action_honesty

    history = [{"role": "user", "content": said}, {"role": "assistant", "content": "Noted."}, {"role": "user", "content": ask}]
    return enforce_final_action_honesty(
        {"response": reply, "confidence": 0.8}, user_input=ask, effective_input=ask, session_id=CHAT,
        source_context={"surface": "api", "allow_remote_fetch": False, "chat_id": CHAT, "conversation_history": history},
    )


PAPERS = "I cancelled the papers and moved the oil delivery to the 9th."
PAPERS_ASK = "what did I say I did about the papers and the oil delivery?"


def test_the_owners_sentence_in_quotation_marks_is_not_the_assistants_action():
    # Measured on the real app: `You said: "I cancelled the hedge man and moved the saddler to the 23rd."` was
    # replaced by "I cannot verify the claimed action from this turn's execution record."
    for reply in (f'You said: "{PAPERS}"', f"You said: “{PAPERS}”", 'You said: "I cancelled the papers and moved the oil delivery to the 9th"'):
        result = _validated(reply, said=PAPERS, ask=PAPERS_ASK)
        assert result["response"] == reply, result.get("action_honesty_validator")
        assert "action_honesty_validator" not in result


@pytest.mark.parametrize("reply", [
    'You said: "I cancelled the papers and moved the oil delivery to the 19th."',   # not what the owner wrote
    "I cancelled the papers and moved the oil delivery to the 9th.",                 # the assistant's own words
    'Done: "I cancelled the milk and moved the coal delivery."',                     # words the owner never wrote
    'I deleted "the papers" from your list.',                                        # the claim begins outside the marks
])
def test_anything_else_in_the_first_person_is_still_the_assistants_claim(reply):
    result = _validated(reply, said=PAPERS, ask=PAPERS_ASK)
    assert result["response"] != reply
    assert result["action_honesty_validator"]["claim_kind"] == "mutation"


def test_a_forgotten_sentence_quoted_back_is_judged_as_before(monkeypatch):
    monkeypatch.setattr(cr, "revoked_tokens_for_chat", lambda *_a, **_k: ("papers",))
    result = _validated(f'You said: "{PAPERS}"', said=PAPERS, ask="what did I say I did about the oil delivery?")
    assert result["action_honesty_validator"]["claim_kind"] == "mutation"


# ------------------------------------------------------------ a pasted mail said back, in any language

_MAIL = ("Worum geht es in dieser Nachricht?\n\nVon: Autowerkstatt Kranich <termine@example.de>\nBetreff: Inspektion\n\n"
         "Ihr Wagen ist am Mittwoch um 9 Uhr zur Inspektion eingeplant. Bitte bringen Sie den Zweitschlüssel mit.")
_SUMMARY = ("Die Nachricht informiert, dass Ihr Wagen am Mittwoch um 9 Uhr zur Inspektion eingeplant ist. "
            "Sie werden gebeten, den Zweitschlüssel mitzubringen.")


@pytest.mark.parametrize("relayed", ["yes", "no", None])
def test_a_summary_of_a_pasted_mail_is_asked_whether_it_only_passes_it_on(monkeypatch, relayed):
    """Mac gate20 on 1d605955a: a German summary of a pasted garage mail had its first sentence cut and replaced by "no
    email account is connected, nothing was sent". The said-back reader knows English done words only; the relay
    judge reads any language. The judge here is a stand-in: what it answers is the test's choice."""
    from core import intent_arbiter

    asked = []
    monkeypatch.setattr(intent_arbiter, "arbitrate_claim_relayed",
                        lambda reply, given, **_k: asked.append((reply, given)) or relayed)
    published, record = gate(_SUMMARY, said=(), request=_MAIL)
    if record.get("publication") == "stood_down:no_action_claims":
        pytest.skip("this summary reads as no claim on this line")
    assert asked and "Zweitschlüssel" in asked[0][1]
    if relayed == "yes":
        assert published == _SUMMARY and record["publication"] == "stood_down:relays_given_text"
    else:
        assert record["publication"] == "action_claims_corrected"


def test_a_claim_with_no_material_of_the_persons_is_not_put_to_the_relay_judge(monkeypatch):
    from core import intent_arbiter

    monkeypatch.setattr(intent_arbiter, "arbitrate_claim_relayed", lambda *a, **k: pytest.fail("asked without material"))
    gate("Your appointment is scheduled for Friday at 9.", said=(), request="book me in for Friday at 9")
