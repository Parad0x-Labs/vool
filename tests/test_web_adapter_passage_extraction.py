"""A note carries the fetched page's passage that answers the query, not only the engine's snippet.

Measured 2026-09-06 (served comparison, fixture transport): the runtime fetched every page it
found (18 page fetches) and then bound notes whose only content was the 160-character search
snippet. The claim "Passat B9 offers a 2.0 TSI with 265 PS" was withheld as unsupported although
the fetched page said exactly that; the snippet had been cut before the figure. The snippet still
leads (the engine chose it for this query); the most query-relevant passage of the fetched text
follows it, bounded, and navigation noise never qualifies.
"""
from __future__ import annotations

from retrieval.web_adapter import _best_summary
from tools.web.web_research import PageEvidence, WebHit

PAGE_TEXT = (
    "Home Menu Search Sign in. Passat B9 engine range. Updated 2026-05-12. Passat B9 (2024 model year): "
    "1.5 TSI eTSI 150 PS mild hybrid, 2.0 TSI 204 PS and 265 PS, 2.0 TDI 122 PS, 150 PS and 193 PS, and 1.5 TSI "
    "eHybrid plug-in hybrids with 204 PS and 272 PS and about 100 km electric range. Fixture page; synthetic values."
)
HIT = WebHit(title="Passat B9 engine range", url="https://motorspec-fixture.org/passat-b9-engines",
             snippet="Updated 2026-05-12. Passat B9 (2024 model year): 1.5 TSI eTSI 150 PS mild hybrid, 2.0 TSI 204 PS and 26",
             engine="yahoo_html", score=None)
PAGE = PageEvidence(url=HIT.url, final_url=HIT.url, status="ok", title="Passat B9 engine range", text=PAGE_TEXT)


def test_the_relevant_page_passage_follows_the_snippet() -> None:
    summary = _best_summary(HIT, PAGE, query_text="VW Passat and the VW Golf engines")
    assert summary.startswith("Updated 2026-05-12. Passat B9"), summary
    assert "265 PS" in summary and "272 PS" in summary, summary
    assert len(summary) <= 1100, len(summary)


def test_navigation_noise_never_becomes_the_passage() -> None:
    noisy = PageEvidence(url=HIT.url, final_url=HIT.url, status="ok", title="x",
                         text="Skip to content Home Menu Search Sign in Account notifications Privacy settings Open menu Home News Sport Weather")
    summary = _best_summary(HIT, noisy, query_text="VW Passat and the VW Golf engines")
    assert summary == HIT.snippet[:280], summary


def test_without_a_page_the_snippet_stands_alone() -> None:
    assert _best_summary(HIT, None, query_text="passat engines") == HIT.snippet[:280]
