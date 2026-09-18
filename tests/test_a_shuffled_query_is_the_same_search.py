"""A free-text query's identity is its bag of tokens, not their order.

Measured on the served surface, 2026-08-15, 13:15 -- one turn, one ticket question::

    round 1: web.search  "Vilnius Klaipeda train tickets weekend prices 2026 August"   16,025 tok
    round 2: web.search  "Vilnius Klaipeda train tickets weekend prices August 2026"   16,855 tok
    round 3: web.search  "Vilnius Klaipeda train tickets August 2026 weekend"          17,552 tok
    round 4: (exact repeat)  -> tool_repeat_blocked                                    18,077 tok

The duplicate guard keys tool calls on their exact argument JSON, so each word-order shuffle of
the same search read as a brand-new request -- and each bought a full 16-18k-token model round
before an exact repeat finally tripped the guard. Search engines are order-insensitive; a guard
that is order-sensitive lets a model burn the loop budget re-asking one question in trivially
different costumes.

Only query-ish keys are normalized (query/queries/q/search). A path, a command, or file content
is order-sensitive by nature and must stay exact -- collapsing those would merge genuinely
different requests, which is the over-fire direction.
"""

from __future__ import annotations

from core.agent_runtime.research_tool_loop_facade import _semantic_payload_signature


def _search(query: str) -> dict:
    return {"intent": "web.search", "arguments": {"query": query, "limit": 10}}


def test_the_measured_shuffle_is_one_search() -> None:
    """The two rounds from the served turn that differ ONLY in word order."""

    first = _semantic_payload_signature(_search("Vilnius Klaipeda train tickets weekend prices 2026 August"))
    second = _semantic_payload_signature(_search("Vilnius Klaipeda train tickets weekend prices August 2026"))

    assert first == second


def test_case_and_duplicate_tokens_do_not_make_a_new_search() -> None:
    assert _semantic_payload_signature(_search("weather TALLINN today")) == _semantic_payload_signature(
        _search("today weather tallinn weather")
    )


def test_a_genuinely_different_query_stays_distinct() -> None:
    """Round 3 of the measured turn dropped the token "prices" -- that IS a different search, and
    merging it would suppress a legitimately new request."""

    with_prices = _semantic_payload_signature(_search("Vilnius Klaipeda train tickets weekend prices August 2026"))
    without_prices = _semantic_payload_signature(_search("Vilnius Klaipeda train tickets August 2026 weekend"))

    assert with_prices != without_prices


def test_order_sensitive_arguments_are_never_normalized() -> None:
    """Paths, commands and content mean different things reordered; only queries are bags."""

    read_a = _semantic_payload_signature({"intent": "workspace.read_file", "arguments": {"path": "a/b.txt"}})
    read_b = _semantic_payload_signature({"intent": "workspace.read_file", "arguments": {"path": "b/a.txt"}})

    assert read_a != read_b


def test_different_intents_never_collide() -> None:
    assert _semantic_payload_signature(_search("x y z")) != _semantic_payload_signature(
        {"intent": "web.research", "arguments": {"query": "x y z", "limit": 10}}
    )
