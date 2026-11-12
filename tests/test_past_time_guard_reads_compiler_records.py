"""The past-time guard reads the evidence compiler's own record heads (2026-11-01).

The evidence compiler writes each recalled record as '- [YYYY-MM-DD] user said: "..."'
(core.evidence_compiler._render_line). The guard read that bracketed day as a clause of its own and
the quoted words as a third party's quote, so a recalled "starts April 1" could not support the
reader's correct "It started April 1, 2024" and the whole answer was replaced by the no-time notice
(measured on a BEAM memory question, Nov 1). Invented values must still be withdrawn.
"""
from __future__ import annotations

from core.model_output_guard import replace_unsupported_past_time_claims, stated_past_time_claims

QUESTION = "When did my pottery class at the community centre start, and how much did it cost?"
RECORD = ('- [2024-02-20] user said: "My pottery class at the community centre starts March 4 and costs $180, '
          'how do I fit it around work? ->-> 2,7"  <amount: $180,>  (event day 2024-03-04, explicit)')
NATIVE = ("- user said (stated 2024-02-20): My pottery class at the community centre starts March 4 and costs $180, "
          "how do I fit it around work? ->-> 2,7  <amount: $180,>  (event day 2024-03-04, explicit)")


def test_a_recalled_date_supports_the_answer_that_repeats_it():
    answer = "It started March 4, 2024, and cost $180."
    assert stated_past_time_claims(answer, question=QUESTION, evidence_texts=[RECORD]) == ()
    assert replace_unsupported_past_time_claims(answer, question=QUESTION, evidence_texts=[RECORD]) == answer


def test_a_day_the_record_does_not_hold_is_still_withdrawn():
    assert "m3:6:2024" in stated_past_time_claims(
        "It started March 6, 2024.", question=QUESTION, evidence_texts=[RECORD])
    assert "m3:4:2023" in stated_past_time_claims(
        "It started March 4, 2023.", question=QUESTION, evidence_texts=[RECORD])


def test_a_compiler_record_reads_exactly_like_the_same_record_in_the_native_form():
    # The compiler head adds no support of its own: it is read as '- user said (stated: day): words'.
    for answer in ("It started March 4, 2024.", "It started March 6, 2024.", "You started it on March 4, 2023."):
        for role in ("user", "assistant"):
            compiled = RECORD.replace("user said", f"{role} said")
            native = NATIVE.replace("user said", f"{role} said")
            assert stated_past_time_claims(answer, question=QUESTION, evidence_texts=[compiled]) \
                == stated_past_time_claims(answer, question=QUESTION, evidence_texts=[native])


def test_no_evidence_still_withdraws_the_time():
    out = replace_unsupported_past_time_claims("It started March 4, 2024.", question=QUESTION, evidence_texts=[])
    assert "March 4" not in out
