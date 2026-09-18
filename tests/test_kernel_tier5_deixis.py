"""TIER 5 (artifact-identity deixis) — block A, review-20260820-141451.

t10: "Halve the Oslo figure. Output only the number." bound silently to 419.52 / 2 =
209.76 while 170 (the Oslo layover result) also stood. A definite reference whose
entity has more than one answer-figure is ambiguous — the kernel clarifies rather
than binding by adjacency/salience. Raw operands (300, 90) are not answer-figures.
"""
from core.kernel import repl

OSLO_FACTS = {
    "s15": "user said earlier: A 5-hour Oslo layover runs 300 minutes; subtract a 90-minute security buffer",
    "s16": "computed in an earlier turn: computed locally: 300 - 90 - 40 = 170; inputs from user",
    "s17": "task data from an earlier turn: Separately: the Oslo to Bergen EV trip costs 419.52 NOK total.",
    "s18": "answered in an earlier turn: 300 - 90 - 40 = 170",
}


def test_deixis_ambiguous_when_entity_has_two_answer_figures():
    amb = repl._ambiguous_deixis("Halve the Oslo figure. Output only the number.", OSLO_FACTS)
    assert amb is not None
    entity, cands = amb
    assert entity == "Oslo"
    assert set(cands) == {"170", "419.52"}         # 300 / 90 / 40 are operands, excluded


def test_deixis_not_ambiguous_with_a_single_answer_figure():
    facts = {"s1": "computed locally: 300 - 90 - 40 = 170; the Oslo layover free minutes"}
    assert repl._ambiguous_deixis("Halve the Oslo figure.", facts) is None


def test_deixis_no_reference_or_explicit_number_returns_none():
    assert repl._ambiguous_deixis("What is 2 + 2?", OSLO_FACTS) is None
    # an explicit number is not a deictic reference — the user named the value
    assert repl._ambiguous_deixis("Halve 419.52.", OSLO_FACTS) is None


def test_deixis_ignores_facts_that_do_not_name_the_entity():
    facts = {"s1": "Bergen charge = 500", "s2": "Bergen trip costs 300 NOK"}
    assert repl._ambiguous_deixis("Halve the Oslo figure.", facts) is None
