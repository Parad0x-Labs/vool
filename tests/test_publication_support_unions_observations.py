"""The turn's own observations support the claims made from them, beside any bound web notes.

Measured 2026-09-06 (served transcript, 23:31): a mixed turn fetched an ETH quote from CoinGecko
and a gold quote from Yahoo Finance as typed observations AND ran a web search whose notes were
bound and referenced by the answering call. The publication gate matched every claim against the
bound web notes ONLY -- `_support_rows` treated the two support origins as exclusive -- so the
sentences restating the runtime's own quotes were withheld as "statements the sources retrieved
for this turn do not support", and the published text said "I can't give you real values" while
the runtime held them.
"""
from __future__ import annotations

from core.grounding_lifecycle import GroundingLifecycle, SynthesisCall, TurnIdentity
from core.grounding_publication import ORIGIN_BOUND_EVIDENCE, _support_rows, publication_verdict

ETH_ROW = {
    "schema": "tool_observation_v1", "intent": "conductor.market_quote", "tool_surface": "web", "ok": True,
    "status": "executed", "node_id": "eth", "demand_id": "eth", "demand_text": "1 eth in usd",
    "summary": "Ethereum: USD 2,495.68 (24h change: +0.76%). Source: CoinGecko, retrieved 2026-09-06 20:30 UTC.",
    "response_preview": "price=2495.68 currency=USD change_24h_pct=0.76", "origin_domain": "www.coingecko.com",
    "result_url": "https://www.coingecko.com/en/coins/ethereum",
}
WEB_NOTE = {
    "result_title": "AIS message format reference", "result_url": "https://fixture-docs.example/ais",
    "summary": "The AIS protocol documentation describes message types and repository layout.",
    "origin_domain": "fixture-docs.example",
}


def _lifecycle() -> GroundingLifecycle:
    return GroundingLifecycle(
        lifecycle_id="union-test",
        identity=TurnIdentity(),
        request_text="what is the baltic sea temperature and how much is 1 eth in usd",
        binding={"evidence_set_id": "es-web-1"},
        evidence_set_ids=("es-web-1",),
        bound_notes=(dict(WEB_NOTE),),
        synthesis_calls=[SynthesisCall(model_call_id="m1", call_role="final_answer", evidence_set_id="es-web-1", sequence=1)],
        typed_observations=(dict(ETH_ROW),),
        model_authored=True,
    )


def test_support_rows_are_the_union_of_bound_notes_and_typed_observations() -> None:
    rows, origin = _support_rows(_lifecycle())
    assert origin == ORIGIN_BOUND_EVIDENCE
    urls = {str(row.get("result_url") or "") for row in rows}
    assert "https://fixture-docs.example/ais" in urls, "the bound web note stays a support row"
    assert "https://www.coingecko.com/en/coins/ethereum" in urls, (
        "the runtime's own observation was dropped from the support set when a web binding existed"
    )


def test_a_claim_restating_the_turns_own_quote_is_published_not_withheld() -> None:
    content = "Ethereum: USD 2,495.68 (24h change: +0.76%). Source: CoinGecko, retrieved 2026-09-06 20:30 UTC."
    verdict = publication_verdict(_lifecycle(), content)
    assert verdict.supported_claim_count >= 1, verdict
    assert not verdict.withheld_claims, f"the quote the runtime fetched itself was withheld: {verdict.withheld_claims}"
    assert "2,495.68" in verdict.content


def test_a_fabricated_value_beside_a_real_observation_is_still_withheld() -> None:
    """Negative control: the union widens support to what the runtime observed, never to invention."""
    content = (
        "Ethereum: USD 2,495.68 (24h change: +0.76%). Source: CoinGecko, retrieved 2026-09-06 20:30 UTC.\n"
        "The Baltic Sea surface temperature is 19.4 C today."
    )
    verdict = publication_verdict(_lifecycle(), content)
    assert any("19.4" in claim for claim in verdict.withheld_claims), verdict.withheld_claims
    assert "2,495.68" in verdict.content
