"""A reference date survives only its admitted runtime carrier, never a premise."""
from datetime import date
from copy import deepcopy
import pytest
from core.model_output_guard import ReferenceClock, stated_past_time_claims, replace_unsupported_past_time_claims

QUESTION = "How many months ago did I attend the photography workshop?"
EVIDENCE = ["- user said (stated 2023-11-01): I went to a 3-day photography workshop today."]
ANSWER = "You attended the photography workshop on November 1, 2023. Today is February 1, 2024, so that was three months ago."
CLOCK_TEXT = "The current date and time is Thursday 01 February 2024, 18:06 (UTC, UTC offset +0000)."

def bound_clock():
    return ReferenceClock(date(2024, 2, 1), "request-unit-hash", "clock-unit-hash", "clock-turn")


def sealed_context():
    from core.bootstrap_context import seal_request_evidence
    context = {"chat_id": "clock-session", "current_turn_id": "clock-turn"}
    messages = [{"role": "system", "content": CLOCK_TEXT}, {"role": "system", "content": EVIDENCE[0]}, {"role": "user", "content": QUESTION}]
    seal_request_evidence(context, session_id="clock-session", question=QUESTION, turn_id="clock-turn", messages=messages,
                          evidence_texts=EVIDENCE, capsule_text="", evidence_message_indices=[0, 1],
                          reference_clock={"source": "runtime_clock", "date": "2024-02-01", "text": CLOCK_TEXT})
    return context, messages


def test_exact_clock_event_and_calendar_claim_survive_together():
    assert replace_unsupported_past_time_claims(ANSWER, question=QUESTION, evidence_texts=EVIDENCE, reference_clock=bound_clock()) == ANSWER

@pytest.mark.parametrize("answer", ["You attended the photography workshop three months ago.", "Today is February 1, 2024, so that was three months ago."])
def test_reference_and_event_operands_are_both_required(answer):
    assert stated_past_time_claims(answer, question=QUESTION, evidence_texts=EVIDENCE)
    assert stated_past_time_claims(answer, question=QUESTION, evidence_texts=[], reference_clock=bound_clock())


def test_reference_clock_does_not_authorize_an_event_today():
    answer = "Today is February 1, 2024. You attended the photography workshop on February 1, 2024."
    out = replace_unsupported_past_time_claims(answer, question="When did I attend the photography workshop?", evidence_texts=EVIDENCE, reference_clock=bound_clock())
    assert "Today is February 1, 2024." in out
    assert "You attended" not in out


def test_current_year_clock_clause_is_separate_from_event_year():
    assert not stated_past_time_claims("The current year is 2024.", question=QUESTION, evidence_texts=EVIDENCE, reference_clock=bound_clock())
    assert stated_past_time_claims("You attended the photography workshop in 2024.", question=QUESTION, evidence_texts=EVIDENCE, reference_clock=bound_clock())


def test_wrong_person_or_event_cannot_donate_calendar_operand():
    for ev in [["Nora went to a photography workshop on November 1, 2023."], ["I went to a watercolor workshop on November 1, 2023."]]:
        assert stated_past_time_claims("Three months ago.", question=QUESTION, evidence_texts=ev, reference_clock=bound_clock())

@pytest.mark.parametrize("event,ref,quantity,q", [
    ("December 15, 2023", date(2024, 3, 15), "three months", "How many months ago did I finish the telescope rebuild?"),
    ("March 20, 2022", date(2024, 3, 20), "two years", "How many years ago did I finish the telescope rebuild?"),
    ("November 15, 2023", date(2024, 2, 1), "two months", "How many completed calendar months ago did I finish the telescope rebuild?"),
])
def test_checked_calendar_anniversaries(event, ref, quantity, q):
    clock = ReferenceClock(ref, "r", "c")
    assert not stated_past_time_claims(quantity + " ago.", question=q, evidence_texts=["I finished the telescope rebuild on " + event + "."], reference_clock=clock)

@pytest.mark.parametrize("ev,clock", [
    (["I finished the telescope rebuild on January 31, 2024."], ReferenceClock(date(2024,2,29), "r", "c")),
    (["I finished the telescope rebuild on March 15."], ReferenceClock(date(2024,6,15), "r", "c")),
    (["I finished the telescope rebuild on March 15, 2024 or April 15, 2024."], ReferenceClock(date(2024,6,15), "r", "c")),
    (["I did not finish the telescope rebuild on March 15, 2024."], ReferenceClock(date(2024,6,15), "r", "c")),
    (["I finished the telescope rebuild on March 15, 2025."], ReferenceClock(date(2024,6,15), "r", "c")),
])
def test_ambiguous_missing_negated_or_future_event_does_not_authorize_math(ev, clock):
    assert stated_past_time_claims("Three months ago.", question="How many months ago did I finish the telescope rebuild?", evidence_texts=ev, reference_clock=clock)


def test_non_aligned_months_require_an_explicit_calendar_convention():
    receipt = {}
    assert stated_past_time_claims("Two months ago.", question=QUESTION, evidence_texts=["I attended the photography workshop on November 15, 2023."], reference_clock=bound_clock(), decision_receipt=receipt)
    assert receipt["checks"][0]["calendar_derivation"]["reason"] == "calendar_convention_required"


def test_clock_is_read_from_existing_session_request_seal():
    from core.bootstrap_context import admitted_request_reference_clock, admitted_request_evidence_texts
    c, messages = sealed_context()
    clock = admitted_request_reference_clock(c, "clock-session", question=QUESTION)
    assert clock.day == date(2024,2,1)
    assert admitted_request_evidence_texts(c, "clock-session", question=QUESTION) == tuple(EVIDENCE)
    assert CLOCK_TEXT not in admitted_request_evidence_texts(c, "clock-session", question=QUESTION)
    assert admitted_request_reference_clock(c, "another-session", question=QUESTION) is None
    assert admitted_request_reference_clock(c, "clock-session", question="When did I go?") is None
    c["current_turn_id"] = "later-turn"
    assert admitted_request_reference_clock(c, "clock-session", question=QUESTION) is None


def test_clock_is_removed_when_actual_carrier_is_missing_or_clipped():
    from core.bootstrap_context import admitted_request_reference_clock, finalize_request_evidence
    c, messages = sealed_context()
    # An unmarked user echo is not a substitute for the omitted runtime carrier.
    final = [messages[1], {"role": "user", "content": CLOCK_TEXT}, messages[-1]]
    c["admitted_capsule_evidence"] = finalize_request_evidence(c["admitted_capsule_evidence"], final, evidence_messages=[messages[1]])
    assert admitted_request_reference_clock(c, "clock-session", question=QUESTION) is None
    c, messages = sealed_context()
    clipped = {"role": "system", "content": CLOCK_TEXT[:40]}
    c["admitted_capsule_evidence"] = finalize_request_evidence(c["admitted_capsule_evidence"], [clipped, *messages[1:]], evidence_messages=[clipped, messages[1]])
    assert admitted_request_reference_clock(c, "clock-session", question=QUESTION) is None


def test_clock_digest_tampering_or_legacy_context_cannot_supply_authority():
    from core.bootstrap_context import admitted_request_reference_clock
    c, _ = sealed_context()
    c["admitted_capsule_evidence"]["reference_clock"]["date"] = "2024-02-12"
    assert admitted_request_reference_clock(c, "clock-session", question=QUESTION) is None
    assert admitted_request_reference_clock({"chat_id":"clock-session", "reference_clock":{"date":"2024-02-01"}}, "clock-session", question=QUESTION) is None


def test_mixed_clock_clause_cannot_donate_its_date_to_an_event():
    answer = "Today is February 1, 2024 and you attended the photography workshop on February 1, 2024."
    assert stated_past_time_claims(answer, question="When did I attend the photography workshop?", evidence_texts=EVIDENCE, reference_clock=bound_clock())


def test_origin_stamped_system_wrapper_preserves_exact_runtime_fact():
    from core.bootstrap_context import admitted_request_reference_clock, finalize_request_evidence
    c, messages = sealed_context()
    wrapped = {"role": "system", "content": "Bound memory prefix.\n---\n" + CLOCK_TEXT}
    c["admitted_capsule_evidence"] = finalize_request_evidence(c["admitted_capsule_evidence"], [wrapped, *messages[1:]], evidence_messages=[wrapped, messages[1]])
    assert admitted_request_reference_clock(c, "clock-session", question=QUESTION).day == date(2024,2,1)


def test_app_normalizer_and_actual_adapter_preserve_bound_clock(monkeypatch):
    from datetime import datetime, timezone
    import core.prompt_normalizer as pn
    from core.bootstrap_context import admitted_request_reference_clock
    from core.agent_runtime.turn_reasoning import _past_time_guard_evidence, _past_time_guard_reference_clock
    from tests.test_prompt_assembly_profiles import _build_request
    from tests.test_provider_request_evidence_binding import _adapter
    from adapters.base_adapter import ModelRequest
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            stamp = cls(2024,2,1,18,6,tzinfo=timezone.utc)
            return stamp.astimezone(tz) if tz else stamp.replace(tzinfo=None)
    monkeypatch.setattr(pn, "datetime", FrozenDateTime)
    from core.context_namespace import ensure_chat_namespace
    ensure_chat_namespace("normal-clock", grant_current_receipts=False)
    context = {"surface":"openclaw", "platform":"openclaw", "chat_id":"normal-clock",
               "conversation_history":[{"role":"user","content":EVIDENCE[0]}, {"role":"assistant","content":"Noted."}]}
    internal, context_result = _build_request(QUESTION, task_class="chat_conversation", task_kind="conversation", output_mode="plain_text", source_context=context)
    record = deepcopy(context["admitted_capsule_evidence"])
    assert record["reference_clock"]["date"] == "2024-02-01"
    request = ModelRequest(task_kind="conversation", prompt=QUESTION, system_prompt=internal.system_prompt(),
                           messages=internal.as_openai_messages(), max_output_tokens=256,
                           metadata={"admitted_capsule_evidence":record,"request_evidence_message_indices":record["evidence_message_indices"]})
    monkeypatch.setattr("adapters.openai_compatible_adapter.build_memory_prefix_for_request", lambda _: "Synthetic wrapper; no private profile.")
    payload = _adapter(context_window=32768)._build_openai_payload(request, force_json=False, stream=False)
    context["admitted_capsule_evidence"] = request.metadata["admitted_capsule_evidence"]
    clock = _past_time_guard_reference_clock(context, "normal-clock", question=QUESTION)
    assert clock.day == date(2024,2,1)
    assert payload["messages"][0]["content"].startswith("Synthetic wrapper")
    evidence = _past_time_guard_evidence(context_result=context_result, source_context=context, web_notes=[], session_id="normal-clock", question=QUESTION)
    assert all(CLOCK_TEXT not in text for text in evidence)
    assert any("photography workshop" in text for text in evidence), "Authorized source must survive normalizer and wire before guard check"
    assert replace_unsupported_past_time_claims(ANSWER, question=QUESTION, evidence_texts=evidence, reference_clock=clock) == ANSWER
