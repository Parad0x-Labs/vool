"""A bare verb after a conjunction elaborates the request before it; it opens no request of its own.

Measured live 2026-09-07 (served, free cloud model): "give me table layout comparison for easy
read and compare" minted TWO demand units -- the second being "and compare" -- and the closure
told the reader that "and compare" was answered in part. "compare" is a demand head, but a head
with nothing after it names nothing to compare: it is the same predicate applied to the object
the first unit already carries. Both grains (mint and execution) must read it that way, and a
head WITH an object ("and compare it with silver", "and define liquidity") must still open.
"""
from __future__ import annotations

from core.agent_runtime.answer_coverage import demand_units, opens_a_fresh_request
from core.agent_runtime.demand_ownership import _continues_the_request_before_it, execution_unit_spans


def _texts(units) -> list[str]:
    return [str(getattr(u, "text", u)) for u in units]


def test_a_bare_head_after_a_conjunction_does_not_open_a_request() -> None:
    assert not opens_a_fresh_request("and compare")
    assert not opens_a_fresh_request("and then explain")
    assert opens_a_fresh_request("and compare it with silver")
    assert opens_a_fresh_request("and define liquidity")


def test_the_mint_keeps_the_operators_sentence_whole() -> None:
    units = demand_units("give me table layout comparison for easy read and compare")
    assert len(units) == 1, _texts(units)


def test_a_head_with_an_object_still_splits() -> None:
    assert len(demand_units("summarize the gold standard and define liquidity")) == 2
    assert len(demand_units("what is the gold price and compare it with silver")) == 2


def test_the_execution_grain_reads_the_bare_verb_as_a_continuation() -> None:
    assert _continues_the_request_before_it("and compare")
    assert _continues_the_request_before_it("and summarize")
    assert not _continues_the_request_before_it("and compare prices")
    spans = execution_unit_spans("give me table layout comparison for easy read and compare")
    assert len(spans) == 1, _texts(spans)
