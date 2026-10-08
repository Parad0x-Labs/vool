"""v14.6 hardening item 2 (ASTRA Pro review, 2026-10-07): the claim binder reports four states, SUPPORTED, AMBIGUOUS,
UNSUPPORTED, CONTRADICTED. SUPPORTED is memory-sourced; any other state leaves the reply unsupported and the guard
withdraws it: VOOL says it has no record instead of giving the value with a "not found in your records" caveat (owner
decision 2026-10-08, replacing the v14.6 qualification). Contributor: sls_0x."""
import pytest

from core.evidence_kernel.claim_binder import (
    AMBIGUOUS,
    CONTRADICTED,
    SUPPORTED,
    UNSUPPORTED,
    bind_claims,
)

EVIDENCE = """<retrieved_context>
- [2025-02-03] user said: I bought a helmet for $95 and a bike computer for $140 last week.
- [2025-03-10] user said: My longest tunnel route so far is 26 km.
- [2025-04-02] user said: Correction, my longest tunnel route is now 31 km.
- [2025-04-05] user said: My neighbour's longest route is 40 km, she is relentless.
- [2025-04-06] user said: My coach's best deadlift is 180 kg; I have never logged mine.
</retrieved_context>"""


def _bind(question, reply):
    return bind_claims(question=question, reply=reply, evidence_text=EVIDENCE)


def _states(result):
    # keyed by the claim's value: the binder's claim text carries a little context ("26 km, and")
    return {("$" if c.kind == "money" else "") + f"{c.value:g}": c.state for c in result.claims}


def test_a_stated_current_value_is_supported():
    r = _bind("What is my longest tunnel route?", "31 km.")
    assert _states(r) == {"31": SUPPORTED} and r.all_supported


def test_a_derived_total_is_supported():
    r = _bind("How much did I spend on the helmet and the bike computer together?", "$235 in total.")
    assert _states(r) == {"$235": SUPPORTED} and r.all_supported


def test_a_superseded_value_is_contradicted():
    r = _bind("What is my longest tunnel route?", "26 km.")
    assert _states(r) == {"26": CONTRADICTED}
    assert r.contradicted and not r.all_supported


def test_a_third_party_value_against_my_own_record_is_contradicted():
    # my longest tunnel route has its own current record (31 km); the neighbour's 40 km contradicts it
    r = _bind("What is my longest tunnel route?", "40 km.")
    assert _states(r) == {"40": CONTRADICTED}, r.as_dict()
    assert r.contradicted and not r.all_supported


def test_a_third_party_value_with_no_record_of_my_own_is_ambiguous():
    # no record of MY deadlift exists; the only 180 kg is the coach's: a record carries the value but is not mine
    r = _bind("What is my best deadlift?", "180 kg.")
    assert _states(r) == {"180": AMBIGUOUS}, r.as_dict()
    assert not r.contradicted and not r.all_supported


def test_a_value_no_record_carries_is_unsupported():
    r = _bind("How much did I spend on cycling gear this year?", "$500 in total.")
    assert _states(r) == {"$500": UNSUPPORTED} and not r.all_supported and not r.contradicted


def test_a_mixed_reply_states_each_value():
    r = _bind("How much did I spend on cycling gear this year?", "$235 on the helmet and bike computer, plus $60 on gloves.")
    s = _states(r)
    assert s["$235"] == SUPPORTED and s["$60"] == UNSUPPORTED and not r.all_supported


def test_a_contradiction_beside_a_supported_value_is_contradicted():
    r = _bind("What is my longest tunnel route, and what did the helmet cost?", "26 km, and the helmet was $95.")
    s = _states(r)
    assert s["26"] == CONTRADICTED and s["$95"] == SUPPORTED
    assert r.contradicted and not r.all_supported


def test_states_count_every_claim():
    r = _bind("How much did I spend on cycling gear this year?", "$235 on the helmet and bike computer, plus $60 on gloves.")
    assert r.states() == {SUPPORTED: 1, AMBIGUOUS: 0, UNSUPPORTED: 1, CONTRADICTED: 0}


# ─── the guard's verdict and the delivered text ───────────────────────────────────────────────

def _verdict(question, reply):
    from core.unsourced_current_claim import inspect_unsourced_current_claim

    ctx = {"chat_id": "chat-states", "admitted_capsule_evidence": {"text": EVIDENCE, "chat_id": "chat-states", "source": "test"}}
    return inspect_unsourced_current_claim(answer=reply, requires_current=True, notes=[], session_id="chat-states", turn_id="t1",
                                           source_context=ctx, user_turn_text=question), ctx


def test_the_guard_withdraws_an_unsupported_value_instead_of_qualifying_it():
    v, ctx = _verdict("How much did I spend on cycling gear this year?", "$500 in total.")
    assert v.unsupported and not v.has_evidence and ctx["claim_binding"]["attempted"]


def test_the_guard_withdraws_a_mixed_reply_rather_than_state_its_unrecorded_value():
    v, ctx = _verdict("How much did I spend on cycling gear this year?",
                      "$235 on the helmet and bike computer, plus $60 on gloves.")
    assert v.unsupported and not v.has_evidence and not ctx["claim_binding"]["contradicted"]


def test_the_guard_withdraws_a_third_party_value_with_no_record_of_my_own():
    v, ctx = _verdict("What is my best deadlift?", "180 kg.")
    assert v.unsupported and not v.has_evidence and not ctx["claim_binding"]["contradicted"]


def test_the_guard_still_withdraws_a_contradicted_value():
    v, ctx = _verdict("What is my longest tunnel route?", "26 km.")
    assert v.unsupported and ctx["claim_binding"]["contradicted"]


def test_the_guard_keeps_a_supported_answer_untouched():
    v, _ctx = _verdict("What is my longest tunnel route?", "31 km.")
    assert v.has_evidence and not v.unsupported


def test_a_live_world_ask_is_left_to_the_live_reading_law():
    # the binder does not attempt a live-world quantity, so the live-reading law stays whole: withdrawn
    v, ctx = _verdict("What is my bitcoin worth right now, I bought at $40,000?", "$40,000.")
    assert v.unsupported and not ctx["claim_binding"]["attempted"]
