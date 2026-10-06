"""The past-time withdrawal notice states the gap without pasting the user's request back.

Measured on the paid fresh probe: every withdrawn date answer shipped as "I don't have that time
... The request was: Answer using the imported prior conversations. You may derive only ...": the
notice builder echoed the whole user turn, instructions included, as part of the answer. The notice
still ships; it no longer repeats the request. All sentences are synthetic.
"""

from __future__ import annotations

from core.model_output_guard import (
    delivers_a_withdrawal_notice,
    replace_unsupported_past_time_claims,
    unverified_past_time_notice,
)

INSTRUCTION = "Reply in one line and only use what my notes say."
QUESTION = INSTRUCTION + "\nWhen did Ilse take the ferry to the island?"


def test_the_withdrawal_notice_ships_without_the_users_instructions():
    out = replace_unsupported_past_time_claims("Ilse took the ferry on 4 April 2024.", question=QUESTION, evidence_texts=[])
    assert delivers_a_withdrawal_notice(out)
    assert "4 April" not in out
    assert INSTRUCTION not in out and "ferry to the island" not in out and "The request was" not in out


def test_the_notice_builder_names_no_part_of_the_request():
    notice = unverified_past_time_notice(QUESTION)
    assert notice == unverified_past_time_notice("")
    assert delivers_a_withdrawal_notice(notice)


def test_a_kept_sentence_beside_a_withdrawn_one_carries_no_request_echo():
    evidence = ["- user said: Ilse: The ferry to the island was cancelled twice."]
    out = replace_unsupported_past_time_claims(
        "The ferry to the island was cancelled twice. Ilse took it on 4 April 2024.",
        question=QUESTION, evidence_texts=evidence)
    assert "4 April" not in out and INSTRUCTION not in out
