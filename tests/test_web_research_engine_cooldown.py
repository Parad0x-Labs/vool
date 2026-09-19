"""A keyless engine that could not be REACHED is skipped for a bounded window; a success clears it.

MEASURED on the final pack (6c661fff): every keyless engine was unreachable on every turn and
each current-information turn re-spent ~24 s discovering it. The window is stated in the notes,
applies only to transport failures (never to "no results"), and never to keyed providers.
"""

from __future__ import annotations

import pytest

from tools.web import web_research as wr
from tools.web.searxng_client import SearchResult, SearXNGUnavailableError


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    wr._reset_engine_memory_for_tests()
    monkeypatch.setattr(wr, "remote_fetch_forbidden", lambda: False)
    monkeypatch.setattr(wr, "_provider_order", lambda: ["searxng"])
    monkeypatch.setattr(wr, "_turn_key_for_engine_memory", lambda: "")
    yield
    wr._reset_engine_memory_for_tests()


def _search_raising(reason: str):
    def _search(self, query, **_k):
        raise SearXNGUnavailableError(reason, "stub")

    return _search


def test_an_unreachable_engine_is_cooled_and_the_next_turn_skips_it(monkeypatch) -> None:
    monkeypatch.setattr(wr.SearXNGClient, "search", _search_raising("unreachable"))
    first = wr.web_research("latest python", total_budget_s=5.0)
    assert any(note.startswith("searxng_failed:unreachable") for note in first.notes), first.notes
    second = wr.web_research("latest python", total_budget_s=5.0)
    assert "searxng_skipped:cooldown:unreachable" in second.notes, second.notes
    assert not any(note.startswith("searxng_failed") for note in second.notes)


def test_an_engine_that_answered_nothing_is_not_cooled(monkeypatch) -> None:
    monkeypatch.setattr(wr.SearXNGClient, "search", lambda self, query, **_k: [])
    wr.web_research("latest python", total_budget_s=5.0)
    second = wr.web_research("latest python", total_budget_s=5.0)
    assert not any("cooldown" in note for note in second.notes), second.notes


def test_a_non_transport_failure_is_not_cooled(monkeypatch) -> None:
    monkeypatch.setattr(wr.SearXNGClient, "search", _search_raising("rate_limited"))
    wr.web_research("latest python", total_budget_s=5.0)
    second = wr.web_research("latest python", total_budget_s=5.0)
    assert not any("cooldown" in note for note in second.notes), second.notes


def test_a_success_clears_the_cooldown(monkeypatch) -> None:
    monkeypatch.setattr(wr.SearXNGClient, "search", _search_raising("timeout"))
    wr.web_research("latest python", total_budget_s=5.0)
    assert wr._engine_skip_reason("searxng").startswith("cooldown:")
    hits = [SearchResult(title="Python 3.13 released", url="https://www.python.org/downloads/", snippet="Python 3.13 is the latest stable release.", engine="stub", score=1.0)]
    monkeypatch.setattr(wr.SearXNGClient, "search", lambda self, query, **_k: hits)
    wr._reset_engine_memory_for_tests()
    result = wr.web_research("latest python", total_budget_s=5.0)
    assert "provider_accepted:searxng" in result.notes, result.notes
    assert wr._engine_skip_reason("searxng") == ""


def test_the_cooldown_expires(monkeypatch) -> None:
    monkeypatch.setattr(wr, "_ENGINE_COOLDOWN_S", 0.0)
    monkeypatch.setattr(wr.SearXNGClient, "search", _search_raising("unreachable"))
    wr.web_research("latest python", total_budget_s=5.0)
    assert wr._engine_skip_reason("searxng") == ""


def test_two_empty_answers_on_distinct_queries_cool_a_scraper(monkeypatch) -> None:
    """MEASURED in-process 2026-09-10: browser_search answered nothing on every query and spent
    the whole budget doing it; "empty" is not a transport failure, so only a streak can say
    "this scraper is not working here"."""
    monkeypatch.setattr(wr, "_provider_order", lambda: ["browser_search"])
    monkeypatch.setattr(wr, "_browser_search_available", lambda: True)
    monkeypatch.setattr(wr, "_browser_search_hits", lambda query, **_k: [])
    monkeypatch.setattr(wr, "_BROWSER_SEARCH_MIN_BUDGET_S", 0.0)
    first = wr.web_research("latest python", total_budget_s=5.0)
    assert "browser_search_empty" in first.notes
    assert wr._engine_skip_reason("browser_search") == "", "one empty is not a streak"
    same = wr.web_research("latest python", total_budget_s=5.0)
    assert wr._engine_skip_reason("browser_search") == "", "the SAME query twice is one empty"
    second = wr.web_research("latest node", total_budget_s=5.0)
    assert "browser_search_empty" in second.notes
    assert wr._engine_skip_reason("browser_search").startswith("cooldown:empty_streak")
    third = wr.web_research("latest rust", total_budget_s=5.0)
    assert any(n.startswith("browser_search_skipped:cooldown:empty_streak") for n in third.notes), third.notes


def test_an_encyclopedia_engine_is_never_cooled_for_answering_nothing() -> None:
    wr._note_engine_empty("ddg_instant", "q1"); wr._note_engine_empty("ddg_instant", "q2")
    assert wr._engine_skip_reason("ddg_instant") == ""


def test_the_browser_scraper_leaves_half_the_budget_to_the_engines_behind_it(monkeypatch) -> None:
    seen = {}
    monkeypatch.setattr(wr, "_provider_order", lambda: ["browser_search", "searxng"])
    monkeypatch.setattr(wr, "_browser_search_available", lambda: True)
    monkeypatch.setattr(wr, "_BROWSER_SEARCH_MIN_BUDGET_S", 0.0)

    def _hits(query, **kw):
        seen["timeout"] = kw.get("timeout_s"); return []

    monkeypatch.setattr(wr, "_browser_search_hits", _hits)
    monkeypatch.setattr(wr.SearXNGClient, "search", lambda self, query, **_k: [])
    wr.web_research("latest python", total_budget_s=24.0)
    assert seen["timeout"] is not None and seen["timeout"] <= 12.5, seen
    monkeypatch.setattr(wr, "_provider_order", lambda: ["browser_search"])
    wr._reset_engine_memory_for_tests()
    wr.web_research("latest python", total_budget_s=24.0)
    assert seen["timeout"] is not None and seen["timeout"] >= 17.0, "alone, the scraper keeps the budget"
