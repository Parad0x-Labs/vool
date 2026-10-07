"""v14.6 hardening item 4 (ASTRA Pro review, 2026-10-07): speaker and ownership stay separate at the receipts, the
claim binder and the packet. The user's own statements are the only owner-grade facts; an assistant saying "you told
me X" is the assistant's record; a third party's state or preference reported by the user is not the user's; a quoted
line is a quotation. Authored cases, no model call. Contributor: sls_0x."""
from __future__ import annotations

import pytest

from core.evidence_kernel.claim_binder import assistant_lines, bind_claims, evidence_records, user_owned_lines
from core.memory_receipts import extract_facts

T = 1750000000.0


# ─── receipts: who owns a state or a preference ───────────────────────────────────────────────

@pytest.mark.parametrize("sentence", [
    "I drive a Fiat Panda these days.",
    "I'm allergic to penicillin, so I read every label.",
    "I prefer the night train over flying.",
])
def test_the_users_own_statement_is_an_owned_fact(sentence):
    kinds = {f.value_type for f in extract_facts(sentence, T, "user")}
    assert kinds & {"state", "preference"}, kinds


@pytest.mark.parametrize("sentence", [
    "You told me you drive a Fiat Panda.",
    "You said you're allergic to penicillin, so I left it out.",
    "As you mentioned, you prefer the night train over flying.",
    "I'd suggest you avoid anything with nuts, since you are allergic to them.",
])
def test_an_assistant_turn_never_yields_a_user_owned_state_or_preference(sentence):
    kinds = {f.value_type for f in extract_facts(sentence, T, "assistant")}
    assert not (kinds & {"state", "preference"}), kinds


@pytest.mark.parametrize("sentence", [
    "My sister said she is allergic to nuts.",
    "My coach drives a Fiat Panda and swears by it.",
    "He prefers the night train over flying.",
    "Our neighbour is a vegetarian, so she brings her own food.",
    "My brother told me he can't stand cilantro.",
])
def test_a_third_party_statement_reported_by_the_user_is_not_the_users_own(sentence):
    facts = extract_facts(sentence, T, "user")
    kinds = {f.value_type for f in facts}
    assert not (kinds & {"state", "preference"}), [(f.value_type, f.norm) for f in facts]


@pytest.mark.parametrize("sentence", [
    "My sister is allergic to nuts, and so am I.",          # the user's own statement rides beside the sister's
    "Like my coach, I drive a Fiat Panda.",
])
def test_a_first_person_clause_beside_a_third_party_one_still_owns_its_fact(sentence):
    kinds = {f.value_type for f in extract_facts(sentence, T, "user")}
    assert kinds & {"state", "preference"}, kinds


# ─── the claim binder: owner, quotation, speaker ──────────────────────────────────────────────

EVIDENCE = """<retrieved_context>
- [2025-03-10] user said: My longest tunnel route is 31 km.
- [2025-04-05] user said: My neighbour's longest route is 40 km, she is relentless.
- [2025-04-06] user said: My brother wrote "I run 12 km a day", which is more than me.
- [2025-04-07] assistant said: You told me your longest tunnel route is 26 km, so plan around that.
</retrieved_context>"""


def test_user_owned_lines_exclude_the_assistants_turns():
    lines = user_owned_lines(EVIDENCE)
    assert all("You told me" not in l for l in lines) and len(lines) == 3
    assert any("You told me" in l for l in assistant_lines(EVIDENCE))


def test_the_record_law_marks_owner_and_quotation():
    recs = {r.value: r for r in evidence_records(user_owned_lines(EVIDENCE))}
    assert recs[31.0].owned and recs[31.0].usable
    assert not recs[40.0].owned and not recs[40.0].usable           # the neighbour's
    assert recs[12.0].quoted and not recs[12.0].usable               # the brother's words, quoted


def test_an_assistant_restatement_cannot_source_a_user_record_ask():
    # "you told me ... 26 km" is the assistant's line; the user's own record says 31 km: 26 is contradicted, not sourced
    r = bind_claims(question="What is my longest tunnel route?", reply="26 km.", evidence_text=EVIDENCE)
    assert r.attempted and [c.state for c in r.claims] == ["CONTRADICTED"], r.as_dict()
    r = bind_claims(question="What is my longest tunnel route?", reply="31 km.", evidence_text=EVIDENCE)
    assert r.all_supported


def test_an_assistant_ask_binds_to_the_assistants_own_line():
    r = bind_claims(question="What did you tell me to plan around?", reply="26 km.", evidence_text=EVIDENCE)
    assert r.attempted and r.all_supported, r.as_dict()


# ─── measured quantities are typed records (item 6, family "duration-record") ─────────────────────

from core.memory_receipts import find_changes


def test_a_distance_is_a_measure_fact_not_an_age_and_a_correction_supersedes_it():
    first = extract_facts("My longest tunnel route so far is 26 km.", T, "user")
    assert [(f.value_type, f.norm) for f in first] == [("measure", "26 km")], [(f.value_type, f.norm) for f in first]
    earlier = [{"receipt_id": "r:1", "occurrence_id": "o:1", "role": "user", "statement_at": T, "facts": [f.as_dict() for f in first]}]
    later = extract_facts("Correction, my longest tunnel route is now 31 km.", T + 86400 * 20, "user")
    assert [(f.value_type, f.norm) for f in later] == [("measure", "31 km")]
    changes = find_changes(later, earlier, role="user", statement_at=T + 86400 * 20)
    assert changes and changes[0]["old_value"] == "26 km" and changes[0]["new_value"] == "31 km"


def test_an_older_stated_state_written_later_is_a_former_state_not_a_replacement():
    newer = extract_facts("I drive a Kia Niro now.", T + 86400 * 80, "user")
    earlier = [{"receipt_id": "r:k", "occurrence_id": "o:k", "role": "user", "statement_at": T + 86400 * 80, "facts": [f.as_dict() for f in newer]}]
    older = extract_facts("I drive a Skoda Octavia these days.", T, "user")
    changes = find_changes(older, earlier, role="user", statement_at=T)
    assert changes == [] and any(f.value_type == "state" and f.norm.endswith("|former") for f in older), [(f.value_type, f.norm) for f in older]
