"""A search engine that failed earlier in the SAME turn is not tried again for that turn's later queries.

Measured 2026-09-06 (served comparison, first two probes): the research lane's rounds each waited
on engines that had already failed for this turn, ~50 s per round, three rounds. Within one request
the outcome of an engine is remembered; the next request tries everything again.
"""
from __future__ import annotations

import pytest

from core.semantic.semantic_admissions import bound_request_context
from tools.web import web_research as wr


@pytest.fixture(autouse=True)
def _pinned_engines(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_PROVIDER_ORDER", "google_html")
    monkeypatch.setenv("ALLOW_BROWSER_FALLBACK", "0")
    monkeypatch.setattr(wr, "_provider_order", lambda: ["google_html"])
    monkeypatch.setattr(wr, "remote_fetch_forbidden", lambda: False)
    monkeypatch.setattr(wr, "_prefer_specialized_live_research", lambda _q: False)
    wr._reset_engine_memory_for_tests()


def test_a_failed_engine_is_skipped_for_the_rest_of_the_turn(monkeypatch) -> None:
    calls: list[str] = []

    def _failing(query, *, max_hits):
        calls.append(query)
        raise RuntimeError("HTTP 503")

    monkeypatch.setattr(wr, "_google_html_hits", _failing)
    with bound_request_context("req-breaker-1"):
        first = wr.web_research("vw passat and vw golf production periods", total_budget_s=5)
        second = wr.web_research("vw passat and vw golf sales", total_budget_s=5)
    assert calls == ["vw passat and vw golf production periods"], calls
    assert any(note.startswith("google_html_failed") for note in first.notes), first.notes
    assert any(note.startswith("google_html_skipped") for note in second.notes), second.notes


def test_the_next_turn_tries_the_engine_again(monkeypatch) -> None:
    calls: list[str] = []

    def _failing(query, *, max_hits):
        calls.append(query)
        raise RuntimeError("HTTP 503")

    monkeypatch.setattr(wr, "_google_html_hits", _failing)
    with bound_request_context("req-breaker-2"):
        wr.web_research("first question", total_budget_s=5)
    with bound_request_context("req-breaker-3"):
        wr.web_research("second question, new turn", total_budget_s=5)
    assert len(calls) == 2, calls


def test_a_working_engine_is_never_skipped(monkeypatch) -> None:
    calls: list[str] = []

    def _working(query, *, max_hits):
        calls.append(query)
        return [wr.WebHit(title="t", url="https://fixture.example/p", snippet="a useful snippet about " + query, engine="google_html", score=None)]

    monkeypatch.setattr(wr, "_google_html_hits", _working)
    monkeypatch.setattr(wr, "_accept_search_hits", lambda q, p, h: (True, ""))
    monkeypatch.setattr(wr, "_fetch_pages_for_hits", lambda *a, **k: ([], []), raising=False)
    with bound_request_context("req-breaker-4"):
        wr.web_research("q one", total_budget_s=5, max_pages=0)
        wr.web_research("q two", total_budget_s=5, max_pages=0)
    assert len(calls) == 2, calls
