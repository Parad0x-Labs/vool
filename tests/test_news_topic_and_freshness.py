"""News fast-path hygiene + freshness: topic extraction, prose intent, and RSS recency ordering.

Pins the fixes for the transcript failures where VOOL searched (and titled results with) the raw
message ("Latest coverage on ok, whats the latest on Iran newS?=:"), matched filler words instead of
the topic, and returned month-old items for a "latest" request.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest import mock

from core.agent_runtime.fast_live_info_mode_query import normalize_live_info_query
from core.agent_runtime.fast_live_info_news_rendering import render_news_response
from core.agent_runtime.fast_live_info_news_topic import extract_news_topic, wants_prose_summary


def test_extract_news_topic_strips_filler_and_preserves_casing() -> None:
    assert extract_news_topic("ok, whats the latest on Iran newS?=") == "Iran"
    assert extract_news_topic("give me the latest on OpenAI") == "OpenAI"
    assert extract_news_topic("what happened in Gaza today") == "Gaza"
    assert extract_news_topic("what's the latest news on Iran") == "Iran"
    assert extract_news_topic("show me the latest news on Tesla") == "Tesla"
    # No real subject -> empty, so the caller defers instead of searching filler.
    assert extract_news_topic("i am looking for fresh latest news! summary") == ""
    assert extract_news_topic("news") == ""
    assert extract_news_topic("what's the latest news?") == ""
    assert extract_news_topic("any news today") == ""


def test_extract_news_topic_keeps_subject_words_that_look_like_framing() -> None:
    # A framing word that is a load-bearing part of the subject must NOT be stripped.
    assert extract_news_topic("news about Breaking Bad") == "Breaking Bad"
    assert extract_news_topic("latest on News Corp") == "News Corp"
    assert extract_news_topic("The Who") == "The Who"
    assert extract_news_topic("The Today Show") == "Today Show"
    assert extract_news_topic("Current Affairs") == "Current Affairs"
    # A trailing "summary" that is part of the subject is kept, not treated as a format ask.
    assert extract_news_topic("news on the Mueller summary") == "the Mueller summary"


def test_wants_prose_summary_detects_summarize_intent() -> None:
    assert wants_prose_summary("i am looking for fresh latest news! summary")
    assert wants_prose_summary("give me a summary not links")
    assert wants_prose_summary("tldr please")
    assert wants_prose_summary("summarize the Iran news")
    assert not wants_prose_summary("latest news on Iran")
    # "summary"/"recap" as part of the subject must NOT flip to prose (keep the link list).
    assert not wants_prose_summary("news on the Mueller summary")
    assert not wants_prose_summary("latest news on the game recap")


def test_normalize_news_query_uses_topic_or_empties() -> None:
    # topic + appended "latest news" (routes the classifier to RSS; the title extractor reduces it back)
    assert normalize_live_info_query("ok, whats the latest on Iran newS?=", mode="news") == "Iran latest news"
    # filler-only news request -> "" signals no topic (caller defers to the reasoning lane)
    assert normalize_live_info_query("i am looking for fresh latest news! summary", mode="news") == ""


def test_render_news_title_uses_topic_not_raw_message() -> None:
    out = render_news_response(query="ok, whats the latest on Iran newS?=", notes=[])
    assert out.splitlines()[0] == "Latest coverage on Iran:"
    # normalized form flows to the same clean title
    assert render_news_response(query="Iran latest news", notes=[]).splitlines()[0] == "Latest coverage on Iran:"


def _rss_item(title: str, url: str, dt: datetime) -> str:
    return (
        f"<item><title>{title}</title><link>{url}</link>"
        f"<source url=\"{url}\">Src</source><pubDate>{format_datetime(dt)}</pubDate></item>"
    )


class _FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *_a: object) -> bool:
        return False

    def read(self, *_a: object) -> bytes:
        return self._payload


def test_news_rss_fallback_sorts_newest_first_and_windows_recency() -> None:
    from tools.web import web_research

    now = datetime.now(timezone.utc)
    xml = (
        "<rss><channel>"
        + _rss_item("Old", "https://reuters.com/old", now - timedelta(days=40))
        + _rss_item("New", "https://apnews.com/new", now - timedelta(days=2))
        + _rss_item("Mid", "https://bbc.com/mid", now - timedelta(days=9))
        + "</channel></rss>"
    )
    captured: dict[str, str] = {}

    def fake_urlopen(request: object, timeout: float | None = None) -> _FakeResp:
        captured["url"] = getattr(request, "full_url", "")
        return _FakeResp(xml.encode("utf-8"))

    with mock.patch("tools.web.web_research.urllib.request.urlopen", side_effect=fake_urlopen), mock.patch(
        "tools.web.web_research._resolve_redirect_url", side_effect=lambda url, timeout_s: url
    ), mock.patch(
        "tools.web.web_research.evaluate_source_domain", return_value=SimpleNamespace(blocked=False)
    ):
        # The fallback runs inside a turn's fetch policy in production; called directly like
        # this it must name its own scope or the effect gateway denies the fetch before the
        # (faked) socket is ever reached.
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope("tests.news_rss_fallback"):
            result = web_research._news_rss_fallback("latest news Iran", max_hits=3, timeout_s=5.0)

    assert result is not None
    _, hits, _, _ = result
    # `when:14d` scopes Google News to recent items at the source (url-encoded ':' == %3A)
    assert "when%3A14d" in captured["url"]
    # newest first, and the 40-day-old item is dropped by the 14-day recency window
    assert [hit.title for hit in hits] == ["New", "Mid"]
