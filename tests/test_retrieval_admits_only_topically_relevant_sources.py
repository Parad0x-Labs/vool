"""Retrieval admits only material that is ABOUT the question, not merely on an allowed domain.

Measured on the live daemon 2026-09-17: an OpenRouter-documentation turn's planned search ran
domain-restricted profile rewrites (``site:github.com``, ``site:wikipedia.org``) of a long
request text while the configured search backend was partly unreachable. The engines answered
those rewrites with an Apify store-actor pull request and the BGPsec and JWT Wikipedia pages --
credible domains, topically unrelated, sharing zero distinctive terms with the question. Every
hit from an allowed domain was admitted, stored and bound as "retrieved support", and the
answering model then correctly refused to lean on any of it while the binding row counted four
sources.

The floor: a hit's own title and summary must carry at least one DISTINCTIVE query term (the
passage scorer's tokenization, compounds split, minus a glue vocabulary of words unrelated
pages carry anyway). These cases drive the real adapter with the goal's four controlled fixture
classes: relevant official documentation, irrelevant results, unavailable search, and genuine
retrieval failure.
"""
from __future__ import annotations

from unittest import mock

from retrieval.web_adapter import WebAdapter
from tools.web.web_research import PageEvidence, ResearchResult, WebHit

# The two requests from the incident chats, shortened to their subject matter.
MODELS_SCRIPT_QUERY = (
    "Research the current OpenRouter API/docs and build a script that fetches the currently "
    "available models from OpenRouter and extracts model IDs, context length and pricing."
)
HEALTH_CHECKER_QUERY = (
    "Build me a small Python provider-health checker for OpenRouter. Research the current "
    "OpenRouter API first; verify the model exists and retrieve its pricing metadata."
)


def _hit(title: str, url: str, snippet: str, *, page_text: str = "") -> tuple[WebHit, PageEvidence | None]:
    web_hit = WebHit(title=title, url=url, snippet=snippet)
    page = (
        PageEvidence(
            url=url, final_url=url, status="ok", title=title, text=page_text or snippet,
            html_len=1200, used_browser=False, screenshot_path=None,
        )
        if page_text or snippet
        else None
    )
    return web_hit, page


def _research(hits: list[WebHit], pages: list[PageEvidence]) -> ResearchResult:
    return ResearchResult(query="q", provider="duckduckgo_html", hits=hits, pages=pages, notes=[], ts_utc=0.0)


def test_relevant_official_documentation_is_admitted() -> None:
    hit, page = _hit(
        "OpenRouter API documentation",
        "https://openrouter.ai/docs/api-reference",
        "The OpenRouter API lists available models at GET /api/v1/models with pricing and "
        "context length per model.",
    )
    with mock.patch.object(WebAdapter, "research_query", return_value=_research([hit], [page] if page else [])):
        notes = WebAdapter.search_query(MODELS_SCRIPT_QUERY, limit=3)
    assert notes, "the official documentation page, which names the question's subject, was rejected"
    assert notes[0]["origin_domain"] == "openrouter.ai"


def test_the_incident_irrelevant_results_are_rejected() -> None:
    """Exactly the junk the incident bound: an allowed-domain GitHub PR and two Wikipedia pages,
    none about the question. None may be stored, ranked or bound."""
    apify_hit, apify_page = _hit(
        "Add Scrape.do provider adapter",
        "https://github.com/usestring/web-data-frontier-benchmark/pull/23",
        "Routes the 77 current targets with verified inputs through fixed Store Actors. Rejects "
        "the other 23 targets before creating an Apify client, with no generic crawler fallback.",
    )
    bgpsec_hit, bgpsec_page = _hit(
        "BGPsec - Wikipedia",
        "https://en.wikipedia.org/wiki/BGPsec",
        "BGPsec provides security by allowing receivers of BGPsec UPDATE messages to "
        "cryptographically verify the received AS path using router certificates.",
    )
    jwt_hit, jwt_page = _hit(
        "JSON Web Token - Wikipedia",
        "https://en.wikipedia.org/wiki/JSON_Web_Token",
        "If the client passes a valid JWT assertion the server will generate an access_token "
        "valid for making calls to the application.",
    )
    for query in (MODELS_SCRIPT_QUERY, HEALTH_CHECKER_QUERY):
        for hit, page in ((apify_hit, apify_page), (bgpsec_hit, bgpsec_page), (jwt_hit, jwt_page)):
            with mock.patch.object(WebAdapter, "research_query", return_value=_research([hit], [page] if page else [])):
                notes = WebAdapter.search_query(query, limit=3)
            assert notes == [], (
                f"an unrelated page ({hit.title!r}) was admitted as support for the OpenRouter question"
            )


def test_a_page_naming_the_subject_on_an_unrelated_domain_is_admitted() -> None:
    """The floor is topical, not a domain list: a blog post that names the subject stays in."""
    hit, page = _hit(
        "Comparing OpenRouter pricing tables",
        "https://example.dev/blog/openrouter-pricing",
        "OpenRouter exposes per-model pricing; here is how the pricing fields are shaped.",
    )
    with mock.patch.object(WebAdapter, "research_query", return_value=_research([hit], [page] if page else [])):
        notes = WebAdapter.search_query(HEALTH_CHECKER_QUERY, limit=3)
    assert notes and notes[0]["origin_domain"] == "example.dev"


def test_unavailable_search_admits_nothing() -> None:
    """The search backend unreachable (the incident's `connection refused`): a None research
    result admits nothing. A raised transport error propagates to the retrieval facade, whose
    receipt closes it as a failure -- it must not be swallowed into an empty success here."""
    import pytest

    with mock.patch.object(WebAdapter, "research_query", return_value=None):
        assert WebAdapter.search_query(MODELS_SCRIPT_QUERY, limit=3) == []
    with mock.patch.object(WebAdapter, "research_query", side_effect=OSError("[Errno 61] Connection refused")):
        with pytest.raises(OSError):
            WebAdapter.search_query(MODELS_SCRIPT_QUERY, limit=3)


def test_genuine_empty_result_stays_empty() -> None:
    """A working search that honestly found nothing for the question."""
    with mock.patch.object(WebAdapter, "research_query", return_value=_research([], [])):
        assert WebAdapter.search_query(MODELS_SCRIPT_QUERY, limit=3) == []


def test_a_glue_only_match_is_not_the_question() -> None:
    """A page whose only overlap is generic vocabulary (`api`, `script`, `provider`) is junk even
    on a technical domain -- the exact way the incident's pages matched."""
    hit, page = _hit(
        "Understanding API keys",
        "https://developer.mozilla.org/en-US/docs/Web/API/key-concepts",
        "An API key is a token that a client supplies when making API requests to a service.",
    )
    with mock.patch.object(WebAdapter, "research_query", return_value=_research([hit], [page] if page else [])):
        notes = WebAdapter.search_query(HEALTH_CHECKER_QUERY, limit=3)
    assert notes == [], "a glue-word match on a credible domain was admitted as support"
