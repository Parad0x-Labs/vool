"""Regression tests for the 2026-07-31 "every web search provider is down" P0.

Measured live on this box before the fix, with the query construction already correct:

    provider: none
    notes: ['searxng_failed:URLError', 'duckduckgo_html_failed:RuntimeError',
            'ddg_instant_empty', 'no_search_hits']

Diagnosed per backend:
 - searxng            -- nothing listens on 127.0.0.1:8080 (no self-hosted instance here).
                         Refuses the connection in ~0.01s, so it stays in the chain for
                         users who do run one.
 - duckduckgo_html    -- DuckDuckGo answers every keyless scrape of html.duckduckgo.com
                         AND lite.duckduckgo.com with its anti-bot "anomaly" challenge
                         page (HTTP 202, zero results). Measured 0/8. Genuinely gone, so
                         it is out of the default chain.
 - ddg_instant        -- works, but serves encyclopedia abstracts only, not web results.
 - google_html        -- the keyless Yahoo/Brave scraper: the ONLY backend that answers
                         general queries. It was implemented and wired into web_research,
                         but was never added to web.allowed_engines, and allowed_engines
                         is the permission boundary -- so the chain that actually ran was
                         searxng(down) + duckduckgo_html(blocked) + ddg_instant(thin),
                         which is exactly the note list above.

Also locked here: the Yahoo parser, which google_html now leads with.
"""

from __future__ import annotations

import urllib.error

import pytest

from core import policy_engine
from tools.web import google_html

# --------------------------------------------------------------------------
# The chain itself
# --------------------------------------------------------------------------

def test_the_only_working_provider_is_permitted_by_default() -> None:
    # google_html was reachable in web_research but absent from allowed_engines, and
    # allowed_engines is the permission boundary -- so it never ran. Without it every
    # remaining provider is down and the chain yields provider: none.
    assert "google_html" in policy_engine.allowed_web_engines()
    assert "google_html" in policy_engine.web_provider_order()


def test_the_dead_duckduckgo_scrape_is_out_of_the_default_chain() -> None:
    # 0/8 live: every request comes back as the anomaly challenge page. Leaving it in
    # only guaranteed a 'duckduckgo_html_failed:RuntimeError' note on every search.
    assert "duckduckgo_html" not in policy_engine.allowed_web_engines()
    assert "duckduckgo_html" not in policy_engine.web_provider_order()


def test_a_stale_launcher_env_cannot_reduce_the_chain_to_dead_providers(monkeypatch) -> None:
    # This is the exact env the shipped launcher exports on this box.
    import tools.web.web_research as wr

    monkeypatch.setenv(
        "WEB_SEARCH_PROVIDER_ORDER", "searxng,google_html,ddg_instant,duckduckgo_html",
    )
    order = wr._provider_order()
    assert "google_html" in order, order
    # The dead provider named by the stale env is filtered out by allowed_engines.
    assert "duckduckgo_html" not in order, order
    # The encyclopedia-only backstop never outranks a real engine.
    assert order.index("google_html") < order.index("ddg_instant"), order


def test_no_provider_may_invent_hits_when_every_backend_is_down(monkeypatch) -> None:
    # Honest degradation: when all backends genuinely fail the caller must see
    # provider "none" and an empty hit list, never a fabricated result set that a
    # model answer could be dressed up as a citation.
    import tools.web.web_research as wr

    monkeypatch.setattr(wr, "_provider_order", lambda: ["searxng", "google_html", "ddg_instant"])

    def _boom(*args, **kwargs):
        raise RuntimeError("backend down")

    monkeypatch.setattr(wr, "_google_html_hits", _boom)
    monkeypatch.setattr(wr, "_searxng_hits", _boom, raising=False)
    monkeypatch.setattr(wr, "ddg_instant_answer", _boom)

    result = wr.web_research("who is the ceo of openai", max_hits=3, max_pages=1)
    assert result.provider == "none"
    assert not list(result.hits or [])
    assert "no_search_hits" in list(result.notes or [])


# --------------------------------------------------------------------------
# The Yahoo parser that google_html now leads with
# --------------------------------------------------------------------------

def _yahoo_result_block(*, target: str, title: str, snippet: str | None) -> str:
    """One Yahoo result, shaped like the live markup (breadcrumb + h3 + compText)."""
    quoted = target.replace(":", "%3a").replace("/", "%2f")
    body = (
        '<div class="compTitle options-toggle">'
        f'<a class="d-ib va-top" href="https://r.search.yahoo.com/_ylt=Awr/RV=2/RU={quoted}/RK=2/RS=x-">'
        # The breadcrumb block: site name plus the URL path. Cleaning the whole anchor
        # glues this in front of the real title.
        '<div class="d-ib p-abs t-0 l-0 fz-13"><span class="d-ib va-mid">'
        '<span class="fc-141414 d-b">Wikipedia</span>'
        'https://en.wikipedia.org &rsaquo; wiki &rsaquo; Sam_Altman</span></div>'
        f'<h3 style="display:block" class="title fc-2015C2-imp pt-6"><span class="d-b fz-20">{title}</span></h3>'
        '</a></div>'
    )
    if snippet is not None:
        body += f'<div class="compText aAbs"><p class="fc-dustygray fz-14">{snippet}</p></div>'
    return body


def test_yahoo_title_drops_the_breadcrumb_glued_in_front_of_it(monkeypatch) -> None:
    # Live, the old parser produced '› Sam_AltmanSam Altman - Wikipedia' because it
    # cleaned the entire anchor, breadcrumb included.
    html = "<html>" + _yahoo_result_block(
        target="https://en.wikipedia.org/wiki/Sam_Altman",
        title="Sam Altman - Wikipedia",
        snippet="Samuel Harris Altman is an American entrepreneur and investor.",
    ) + "</html>"
    monkeypatch.setattr(google_html, "_fetch", lambda *a, **k: html)

    results = google_html._yahoo_search("ceo of openai", max_results=5, timeout_s=1.0)
    assert len(results) == 1
    assert results[0]["title"] == "Sam Altman - Wikipedia"
    assert results[0]["url"] == "https://en.wikipedia.org/wiki/Sam_Altman"


def test_a_snippet_stays_attached_to_the_result_it_describes(monkeypatch) -> None:
    # The old parser collected every title and every snippet into two flat lists and
    # paired them by index. A result carrying no snippet shifted the whole column, so a
    # cited source ended up described by text it never contained.
    html = (
        "<html>"
        + _yahoo_result_block(
            target="https://example.com/alpha", title="Alpha Page", snippet=None,
        )
        + _yahoo_result_block(
            target="https://example.com/bravo",
            title="Bravo Page",
            snippet="Bravo body text that belongs to the Bravo page only.",
        )
        + "</html>"
    )
    monkeypatch.setattr(google_html, "_fetch", lambda *a, **k: html)

    results = google_html._yahoo_search("anything", max_results=5, timeout_s=1.0)
    by_title = {r["title"]: r for r in results}
    assert set(by_title) == {"Alpha Page", "Bravo Page"}
    # Alpha has no snippet of its own and must not borrow Bravo's.
    assert by_title["Alpha Page"]["snippet"] == ""
    assert "Bravo body text" in by_title["Bravo Page"]["snippet"]


def test_yahoo_leads_the_keyless_chain_because_brave_rate_limits_this_box(monkeypatch) -> None:
    # Measured over 8 varied queries: Brave 3/8 (five HTTP 429s, ~2.6s each), Yahoo 8/8
    # at ~1.1s. Trying Brave first burned budget on a near-certain rate limit.
    calls: list[str] = []

    def _yahoo(query, *, max_results, timeout_s):
        calls.append("yahoo")
        return [{"title": "T", "url": "https://example.com/x", "snippet": "s"}]

    def _brave(query, *, max_results, timeout_s):
        calls.append("brave")
        return [{"title": "B", "url": "https://example.com/b", "snippet": "s"}]

    monkeypatch.setattr(google_html, "_yahoo_search", _yahoo)
    monkeypatch.setattr(google_html, "_brave_search", _brave)

    results = google_html.google_html_search("anything", max_results=3, timeout_s=1.0)
    assert calls == ["yahoo"], calls
    assert results[0]["url"] == "https://example.com/x"


def test_brave_still_covers_a_yahoo_outage(monkeypatch) -> None:
    def _yahoo(query, *, max_results, timeout_s):
        raise urllib.error.HTTPError("u", 500, "INKApi Error", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(google_html, "_yahoo_search", _yahoo)
    monkeypatch.setattr(
        google_html,
        "_brave_search",
        lambda q, *, max_results, timeout_s: [
            {"title": "B", "url": "https://example.com/b", "snippet": "s"},
        ],
    )
    results = google_html.google_html_search("anything", max_results=3, timeout_s=1.0)
    assert results and results[0]["url"] == "https://example.com/b"


@pytest.mark.parametrize("code", [429, 500, 502, 503])
def test_a_transient_http_status_is_retried_once(monkeypatch, code: int) -> None:
    # Yahoo intermittently answers "HTTP Error 500: INKApi Error" for a query it serves
    # fine a moment later; Brave answers 429. Failing out on the first attempt threw away
    # a backend that was about to work.
    attempts = {"n": 0}

    class _Resp:
        def read(self):
            return b"recovered"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(req, timeout=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise urllib.error.HTTPError("u", code, "transient", {}, None)  # type: ignore[arg-type]
        return _Resp()

    monkeypatch.setattr(google_html.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(google_html.time, "sleep", lambda *_a: None)

    assert google_html._fetch("https://example.com", timeout_s=1.0) == "recovered"
    assert attempts["n"] == 2


def test_a_real_refusal_is_not_retried(monkeypatch) -> None:
    # 403/404 mean the endpoint is refusing us; retrying only burns the turn budget.
    attempts = {"n": 0}

    def _urlopen(req, timeout=None):
        attempts["n"] += 1
        raise urllib.error.HTTPError("u", 403, "forbidden", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(google_html.urllib.request, "urlopen", _urlopen)
    monkeypatch.setattr(google_html.time, "sleep", lambda *_a: None)

    with pytest.raises(urllib.error.HTTPError):
        google_html._fetch("https://example.com", timeout_s=1.0)
    assert attempts["n"] == 1


# --------------------------------------------------------------------------
# The source profiles that decide WHERE each query is sent
# --------------------------------------------------------------------------

def test_a_general_question_is_not_searched_on_developer_docs_domains() -> None:
    """Measured live: "search the web for when the Sydney Harbour Bridge opened" answered "the other
    two sources are MDN Web Docs (web development documentation) which are unrelated".

    `official_docs` sat third in the `general` branch as a safety net for a technical question that
    was never classified technical. But `planned_search_query` does not try profiles in order until
    one works -- it runs them ALL and merges the results -- so third place is not a fallback, it is
    a guaranteed share of every general question's evidence, pinned to five developer-docs domains.
    """
    from core.source_reputation import profiles_for_topic, render_query

    for question in (
        "when was the Sydney Harbour Bridge opened",
        "current population of Iceland",
        "who won the Nobel Prize in Literature in 2024",
        "how much does a Tesla Model Y cost",
    ):
        rendered = " ".join(render_query(p, question) for p in profiles_for_topic("general", question))
        assert "developer.mozilla.org" not in rendered, question
        assert "docs.python.org" not in rendered, question
        # The user's own words still go out unfiltered.
        assert question in rendered, question


def test_a_developer_question_still_reaches_the_docs_domains() -> None:
    # The safety net the third profile existed for is kept, just aimed at questions that name
    # something developer-flavoured instead of at everything.
    from core.source_reputation import profiles_for_topic, render_query

    for question in ("latest version of Python", "how do I fix a python traceback", "css grid vs flexbox"):
        rendered = " ".join(render_query(p, question) for p in profiles_for_topic("general", question))
        assert "docs.python.org" in rendered, question


def test_a_technical_topic_is_unaffected() -> None:
    from core.source_reputation import profiles_for_topic

    ids = [p.profile_id for p in profiles_for_topic("technical", "what webhook format does stripe use")]
    assert "official_docs" in ids and "open_web" in ids
