"""A record answer naming its month by abbreviation was refused by the publication gate (port paid run, 2026-10-07).

"Around February 2024 — on Jan 7, 2024 Tim said ..." was unsupported by the record "Session date: 7 January, 2024 /
Tim: ... Next month, I'm off to Ireland": claim matching read "Jan" as a name the record lacked and fused "Around
February" into one proper noun, and the support rows split the record's date bullet from the words under it.
Months are now calendar entities under their full name (`core.claim_support`), matched by full name or
abbreviation, and a support row is a capsule record with the lines under it (`core.memory_grounding`).
"""
from __future__ import annotations

import pytest

from core.claim_support import match_claims
from core.memory_grounding import memory_record_rows

TIM = ("Tim: Hey John, long time no talk. On Friday, I got great news - I'm finally in the study abroad program I applied "
       "for! Next month, I'm off to Ireland for a semester. (stated: Session date: 7 January, 2024; stated: 2024-01-07)")


@pytest.mark.parametrize("answer", [
    'Around February 2024 — on Jan 7, 2024 Tim said he was "off to Ireland next month."',
    'February 2024 — Tim said on 7 January 2024 he was off to Ireland "next month."',
], ids=["abbreviated-month", "full-month"])
def test_a_month_named_either_way_is_the_records_month(answer):
    claim = match_claims(answer=answer, notes=[{"summary": TIM}], request_text="When will Tim leave for Ireland?").as_dict()["claims"][0]
    assert claim["status"] == "supported", claim


@pytest.mark.parametrize("answer", [
    "Around March 2023 — on Jun 9, 2023 Tim said he was off to Ireland.",
    "Tim leaves in August.",
], ids=["wrong-months", "month-not-in-record"])
def test_a_month_the_record_does_not_state_is_still_unsupported(answer):
    claim = match_claims(answer=answer, notes=[{"summary": TIM}], request_text="When will Tim leave for Ireland?").as_dict()["claims"][0]
    assert claim["status"] == "unsupported", claim


def test_a_record_row_keeps_its_bullet_date_with_the_words_under_it():
    evidence = (
        "<retrieved_context>\n"
        "Distilled local facts. Answer from these records.\n"
        '- user said [reported source prefix "Tim:"] (stated 2024-01-07): Session date: 7 January, 2024\n'
        "Tim: Great news - I'm finally in the study abroad program I applied for! Next month, I'm off to Ireland.\n"
        "- assistant said (recorded 2024-01-08): Session date: 8 January, 2024\n"
        "Tim leaves for Ireland on 3 February 2024.\n"
        "John: I started a reading club at work.\n"
        "</retrieved_context>"
    )
    rows = [row["summary"] for row in memory_record_rows(evidence)]
    assert rows == [
        '- user said [reported source prefix "Tim:"] (stated 2024-01-07): Session date: 7 January, 2024 '
        "Tim: Great news - I'm finally in the study abroad program I applied for! Next month, I'm off to Ireland.",
    ], rows
    claim = match_claims(answer='Around February 2024 — on Jan 7, 2024 Tim said he was "off to Ireland next month."',
                         notes=memory_record_rows(evidence), request_text="When will Tim leave for Ireland?").as_dict()["claims"][0]
    assert claim["status"] == "supported", claim
