"""A second retrieval of the SAME query, after this turn's whole chain ended unavailable, is declined.

MEASURED on the final pack (6c661fff, turn 19, 127.6 s): the live-info lane searched the user's
text for 24 s with every keyless engine unavailable; adaptive research then asked to search the
identical text and spent 25 s on the identical chain. The second ask cannot reach a provider the
first did not.
"""

from __future__ import annotations

import hashlib

from core.retrieval_authority_gate import (
    REASON_CHAIN_UNAVAILABLE,
    _same_query_chain_unavailable,
    authorize_retrieval,
)

QUERY = "What is the latest stable version of Python?"


def _receipt(query: str, status: str, *, count: int = 0, kind: str = "live_info_fast_path") -> dict:
    return {
        "schema": "vool.web_retrieval_receipt.v1",
        "kind": kind,
        "query_hash": hashlib.sha256(" ".join(query.split()).encode()).hexdigest(),
        "status": status,
        "source_count": count,
    }


def test_the_same_query_after_an_unavailable_chain_is_declined_with_the_first_outcome_named() -> None:
    context = {"web_retrieval_receipts": [_receipt(QUERY, "unavailable")]}
    assert _same_query_chain_unavailable(context, QUERY) == "live_info_fast_path:unavailable"
    assert authorize_retrieval(context, QUERY, lane="adaptive_research", query=QUERY) is False
    refusal = context["retrieval_authority_refusals"][-1]
    assert refusal["reason_code"] == REASON_CHAIN_UNAVAILABLE
    assert refusal["detail"] == "live_info_fast_path:unavailable"


def test_a_chain_that_found_sources_does_not_decline_a_second_ask() -> None:
    context = {"web_retrieval_receipts": [_receipt(QUERY, "available", count=3)]}
    assert _same_query_chain_unavailable(context, QUERY) == ""


def test_a_different_query_is_a_different_chain() -> None:
    context = {"web_retrieval_receipts": [_receipt(QUERY, "unavailable")]}
    assert _same_query_chain_unavailable(context, "Python 3.13 release date") == ""


def test_whitespace_is_not_a_different_query() -> None:
    context = {"web_retrieval_receipts": [_receipt(QUERY, "failed")]}
    assert _same_query_chain_unavailable(context, "  What is the latest   stable version of Python? ") != ""


def test_a_narrowed_query_in_the_same_turn_is_not_the_same_query() -> None:
    """Adaptive research narrows and broadens inside one turn; only the identical query is declined."""
    context = {"web_retrieval_receipts": [_receipt(QUERY, "unavailable")]}
    assert _same_query_chain_unavailable(context, "latest stable Python release notes") == ""


def test_a_lane_that_names_no_query_is_not_checked() -> None:
    context = {"web_retrieval_receipts": [_receipt(QUERY, "unavailable")]}
    assert "retrieval_authority_refusals" not in context or not context["retrieval_authority_refusals"]
    # The gate falls through to the authority when no query is given (nothing is guessed);
    # what the authority answers is its own business and not asserted here.
    authorize_retrieval(context, QUERY, lane="reasoning_fallback_search")
    refusals = context.get("retrieval_authority_refusals") or []
    assert all(r.get("reason_code") != REASON_CHAIN_UNAVAILABLE for r in refusals)


def test_no_receipts_no_decline() -> None:
    assert _same_query_chain_unavailable({}, QUERY) == ""
    assert _same_query_chain_unavailable(None, QUERY) == ""
