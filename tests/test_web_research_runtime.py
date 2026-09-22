from __future__ import annotations

import json
import unittest
from unittest import mock

from core.effect_gateway import named_background_effect_scope
from core.live_quote_contract import format_quote_timestamp
from core.remote_fetch_policy import remote_fetch_policy_scope
from tools.web.web_research import (
    PageEvidence,
    WebHit,
    _extract_weather_location,
    _is_plausible_weather_location,
    _looks_like_news_query,
    _looks_like_price_query,
    _weather_fallback,
    web_research,
)


class WeatherLocationGuardTests(unittest.TestCase):
    def test_extracts_clean_place_names(self) -> None:
        self.assertEqual(_extract_weather_location("whats the weather in Riga?"), "riga")
        self.assertEqual(_extract_weather_location("wheater in vilnius?"), "vilnius")
        self.assertEqual(_extract_weather_location("weather in new york today"), "new york")

    def test_no_place_named_falls_back_to_current_location(self) -> None:
        self.assertEqual(_extract_weather_location("what is the weather?"), "current location")
        self.assertEqual(_extract_weather_location("weather?"), "current location")
        self.assertEqual(_extract_weather_location("hows the weather"), "current location")

    def test_scaffolding_and_garbage_locations_are_rejected(self) -> None:
        # Regression: the OpenClaw queued-turn scaffolding blob used to be handed to
        # wttr.in as a location and fuzzy-matched to "Los Vargas, Mexico" for a
        # "weather in Riga" question. It (and any bracket/GMT/over-long blob) must
        # now extract to "" so the weather fast-path bails.
        blob = "[Queued user message that arrived while the previous turn was still active] whats the riga? hi"
        self.assertEqual(_extract_weather_location(blob), "")
        self.assertEqual(_extract_weather_location("weather in [Wed 2026-07-01 18:07 GMT+3] riga"), "")
        self.assertEqual(
            _extract_weather_location("weather in foo bar baz qux quux corge grault garply waldo"),
            "",
        )

    def test_plausibility_predicate(self) -> None:
        for good in ("Riga", "new york", "san francisco", "current location"):
            self.assertTrue(_is_plausible_weather_location(good), good)
        for bad in ("", "[queued]", "a b c d e f g h", "weather GMT+3 blah", "12345"):
            self.assertFalse(_is_plausible_weather_location(bad), bad)

    def test_weather_fallback_bails_on_rejected_location_without_network(self) -> None:
        # If the location is rejected, _weather_fallback must return None BEFORE any
        # network call - assert by making urlopen explode if it's ever reached.
        with mock.patch(
            "tools.web.web_research.urllib.request.urlopen",
            side_effect=AssertionError("must not hit wttr.in with a rejected location"),
        ):
            blob = "[Queued user message that arrived while the previous turn was still active] whats the riga? hi"
            self.assertIsNone(_weather_fallback(blob, timeout_s=5.0))


class WebResearchRuntimeTests(unittest.TestCase):
    @staticmethod
    def _json_response(payload: dict[str, object]):
        class _Response:
            def __init__(self, body: bytes):
                self._body = body

            def read(self, _limit: int | None = None) -> bytes:
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Response(json.dumps(payload).encode("utf-8"))

    def test_ddg_instant_empty_falls_through_to_duckduckgo_html(self) -> None:
        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["ddg_instant", "duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research.ddg_instant_answer",
            return_value={},
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            return_value=[
                WebHit(
                    title="Telegram Bot API",
                    url="https://core.telegram.org/bots/api",
                    snippet="HTTP-based interface for building Telegram bots.",
                    engine="duckduckgo_html",
                )
            ],
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            return_value={"status": "ok", "text": "Useful docs text " * 60, "html": "<html></html>"},
        ), mock.patch(
            "tools.web.web_research._should_try_browser",
            return_value=False,
        ):
            result = web_research("Telegram Bot API docs", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "duckduckgo_html")
        self.assertIn("ddg_instant_empty", result.notes)
        self.assertTrue(result.hits)
        self.assertEqual(result.hits[0].url, "https://core.telegram.org/bots/api")

    def test_remote_fetch_disabled_short_circuits_all_providers(self) -> None:
        with remote_fetch_policy_scope({"allow_remote_fetch": False}), mock.patch(
            "tools.web.web_research._specialized_live_research",
            side_effect=AssertionError("specialized live provider must not run"),
        ), mock.patch(
            "tools.web.web_research._provider_order",
            side_effect=AssertionError("search provider order must not be evaluated"),
        ):
            result = web_research("latest news on OpenAI", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "disabled")
        self.assertIn("remote_fetch_disabled", result.notes)
        self.assertEqual(result.hits, [])

    def test_crypto_price_query_uses_word_boundaries(self) -> None:
        self.assertEqual(_looks_like_price_query("ETH price now?"), "ethereum")
        self.assertEqual(_looks_like_price_query("what is Seth price?"), "")

    def test_browser_disabled_keeps_http_text_without_claiming_browser_use(self) -> None:
        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            return_value=[
                WebHit(
                    title="Telegram Bot API",
                    url="https://core.telegram.org/bots/api",
                    snippet="Canonical Telegram docs.",
                    engine="duckduckgo_html",
                )
            ],
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            return_value={"status": "ok", "text": "short docs text", "html": "<html>short</html>"},
        ), mock.patch(
            "tools.web.web_research._should_try_browser",
            return_value=True,
        ), mock.patch(
            "tools.web.web_research.browser_render",
            return_value={"status": "disabled", "final_url": "https://core.telegram.org/bots/api"},
        ):
            result = web_research("Telegram Bot API docs", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "duckduckgo_html")
        self.assertTrue(result.pages)
        self.assertEqual(result.pages[0].status, "empty")
        self.assertEqual(result.pages[0].text, "short docs text")
        self.assertFalse(result.pages[0].used_browser)

    def test_low_remaining_budget_skips_slow_browser_fallback(self) -> None:
        # Regression: a 3-minute hang came from the headless-browser fallback firing on
        # every short page. With little of the wall-clock budget left, the browser leg
        # must be skipped and the plain HTTP text recorded instead -- even though the
        # browser would otherwise be attempted (short text triggers _needs_browser).
        # Non-news query so it reaches the provider+page path (news uses the RSS fast-path).
        browser = mock.MagicMock(side_effect=AssertionError("browser fallback must not run under a low budget"))
        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            return_value=[
                WebHit(
                    title="Telegram Bot API",
                    url="https://core.telegram.org/bots/api",
                    snippet="Canonical Telegram docs.",
                    engine="duckduckgo_html",
                )
            ],
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            return_value={"status": "ok", "text": "short docs text", "html": "<html>short</html>"},
        ), mock.patch(
            "tools.web.web_research._should_try_browser",
            return_value=True,
        ), mock.patch(
            "tools.web.web_research.browser_render",
            browser,
        ):
            # 5s remains: above the 1s page-fetch floor (so the fetch runs) but below the
            # 12s browser floor (so the browser fallback is gated off).
            result = web_research(
                "Telegram Bot API docs",
                max_hits=1,
                max_pages=1,
                total_budget_s=5.0,
            )

        browser.assert_not_called()
        self.assertTrue(result.pages)
        self.assertFalse(result.pages[0].used_browser)
        self.assertEqual(result.pages[0].text, "short docs text")

    def test_weather_query_uses_specialized_live_fallback_when_search_providers_fail(self) -> None:
        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["ddg_instant", "duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research.ddg_instant_answer",
            return_value={},
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            side_effect=RuntimeError("duckduckgo_anomaly_challenge"),
        ), mock.patch(
            "tools.web.web_research._specialized_live_research",
            return_value=(
                "wttr_in",
                [
                    WebHit(
                        title="wttr.in weather for London",
                        url="https://wttr.in/London",
                        snippet="London: Rain, 12 C.",
                        engine="wttr_in",
                    )
                ],
                [
                    PageEvidence(
                        url="https://wttr.in/London",
                        final_url="https://wttr.in/London",
                        status="ok",
                        title="wttr.in weather for London",
                        text="London: Rain, 12 C.",
                        html_len=128,
                        used_browser=False,
                        screenshot_path=None,
                    )
                ],
                ["live_weather_fallback:wttr_in"],
            ),
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            side_effect=AssertionError("prebuilt weather page should skip refetch"),
        ):
            result = web_research("what is the weather in London today?", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "wttr_in")
        self.assertIn("live_weather_fallback:wttr_in", result.notes)
        self.assertEqual(result.pages[0].text, "London: Rain, 12 C.")

    def test_weather_fallback_accepts_nested_data_payload(self) -> None:
        payload = {
            "data": {
                "current_condition": [
                    {
                        "localObsDateTime": "2026-03-17 11:32 PM",
                        "weatherDesc": [{"value": "Overcast"}],
                        "temp_C": "4",
                        "FeelsLikeC": "2",
                        "humidity": "86",
                        "windspeedKmph": "5",
                    }
                ],
                "nearest_area": [
                    {
                        "areaName": [{"value": "Vilnius"}],
                        "country": [{"value": "Lithuania"}],
                    }
                ],
            }
        }
        with mock.patch(
            "tools.web.web_research.urllib.request.urlopen",
            return_value=self._json_response(payload),
        ), named_background_effect_scope("test.web-research.weather-nested-payload"):
            result = web_research("what is weather in Vilnius now?", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "wttr_in")
        self.assertIn("live_weather_fallback:wttr_in", result.notes)
        self.assertIn("Vilnius, Lithuania: Overcast, 4 C", result.hits[0].snippet)

    def test_weather_query_prefers_specialized_live_fallback_over_generic_provider_hits(self) -> None:
        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research._specialized_live_research",
            return_value=(
                "wttr_in",
                [
                    WebHit(
                        title="wttr.in weather for Vilnius",
                        url="https://wttr.in/Vilnius",
                        snippet="Vilnius, Lithuania: Clear, 8 C.",
                        engine="wttr_in",
                    )
                ],
                [
                    PageEvidence(
                        url="https://wttr.in/Vilnius",
                        final_url="https://wttr.in/Vilnius",
                        status="ok",
                        title="wttr.in weather for Vilnius",
                        text="Vilnius, Lithuania: Clear, 8 C.",
                        html_len=128,
                        used_browser=False,
                        screenshot_path=None,
                    )
                ],
                ["live_weather_fallback:wttr_in"],
            ),
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            side_effect=AssertionError("weather queries should not prefer generic provider hits over structured live weather"),
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            side_effect=AssertionError("prebuilt weather page should skip refetch"),
        ):
            result = web_research("what is weather in Vilnius now?", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "wttr_in")
        self.assertIn("live_weather_fallback:wttr_in", result.notes)
        self.assertEqual(result.pages[0].text, "Vilnius, Lithuania: Clear, 8 C.")

    def test_news_query_uses_specialized_live_fallback_and_preserves_final_url(self) -> None:
        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["ddg_instant", "duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research.ddg_instant_answer",
            return_value={},
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            side_effect=RuntimeError("duckduckgo_anomaly_challenge"),
        ), mock.patch(
            "tools.web.web_research._specialized_live_research",
            return_value=(
                "google_news_rss",
                [
                    WebHit(
                        title="OpenAI to acquire Promptfoo",
                        url="https://news.google.com/rss/articles/demo",
                        snippet="OpenAI | 2026-03-09 | OpenAI to acquire Promptfoo",
                        engine="google_news_rss",
                    )
                ],
                [],
                ["live_news_fallback:google_news_rss"],
            ),
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            return_value={
                "status": "ok",
                "text": "OpenAI announced it will acquire Promptfoo.",
                "html": "<html>OpenAI announced it will acquire Promptfoo.</html>",
                "final_url": "https://openai.com/index/openai-to-acquire-promptfoo/",
            },
        ), mock.patch(
            "tools.web.web_research._should_try_browser",
            return_value=False,
        ):
            result = web_research("latest news on OpenAI", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "google_news_rss")
        self.assertIn("live_news_fallback:google_news_rss", result.notes)
        self.assertEqual(result.pages[0].final_url, "https://openai.com/index/openai-to-acquire-promptfoo/")

    def test_latest_on_query_is_treated_as_news(self) -> None:
        self.assertTrue(_looks_like_news_query("What's the latest on Iran war?"))

    def test_news_word_query_is_treated_as_news(self) -> None:
        # The bug in sls's transcript: "latest iran news" missed the news lane and fell to the junk
        # instant-answer chain. The standalone word "news" now routes to Google News RSS.
        for query in ("latest iran news", "iran news", "give me a summary of the latest iran news"):
            self.assertTrue(_looks_like_news_query(query), query)
        # But "news" as a substring of another word, or a non-news query, must not trigger it.
        self.assertFalse(_looks_like_news_query("newsletter signup"))
        self.assertFalse(_looks_like_news_query("who is the us president"))

    def test_provider_order_demotes_instant_answer_to_last(self) -> None:
        from tools.web.web_research import _demote_instant_answer

        self.assertEqual(
            _demote_instant_answer(["searxng", "ddg_instant", "duckduckgo_html"]),
            ["searxng", "duckduckgo_html", "ddg_instant"],
        )
        # Real engines keep their relative order; instant-answer variants sink to the end.
        self.assertEqual(_demote_instant_answer(["ddg_instant", "duckduckgo_html"]), ["duckduckgo_html", "ddg_instant"])
        self.assertEqual(_demote_instant_answer(["ddg_instant"]), ["ddg_instant"])

    def test_market_quote_query_uses_specialized_live_fallback_when_search_providers_fail(self) -> None:
        yahoo_payload = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "symbol": "BZ=F",
                            "currency": "USD",
                            "exchangeName": "NYM",
                            "regularMarketPrice": 102.36,
                            "previousClose": 100.21,
                            "regularMarketTime": 1773763816,
                        },
                        "timestamp": [1773763740, 1773763800, 1773763816],
                        "indicators": {
                            "quote": [
                                {
                                    "close": [102.82, 102.63, 102.36],
                                }
                            ]
                        },
                    }
                ]
            }
        }

        with mock.patch(
            "tools.web.web_research._provider_order",
            return_value=["ddg_instant", "duckduckgo_html"],
        ), mock.patch(
            "tools.web.web_research.ddg_instant_answer",
            return_value={},
        ), mock.patch(
            "tools.web.web_research._duckduckgo_html_hits",
            side_effect=RuntimeError("duckduckgo_anomaly_challenge"),
        ), mock.patch(
            "tools.web.web_research.urllib.request.urlopen",
            return_value=self._json_response(yahoo_payload),
        ), mock.patch(
            "tools.web.web_research.http_fetch_text",
            side_effect=AssertionError("prebuilt market quote page should skip refetch"),
        ), named_background_effect_scope("test.web-research.market-quote-fallback"):
            result = web_research("Brent crude price now?", max_hits=1, max_pages=1)

        self.assertEqual(result.provider, "yahoo_finance")
        self.assertIn("live_price_fallback:yahoo_finance:brent_crude", result.notes)
        self.assertEqual(result.hits[0].title, "Brent crude quote")
        self.assertIn("Brent crude: $102.36 USD per barrel", result.hits[0].snippet)
        self.assertIn("session change", result.pages[0].text)
        self.assertIn(f"as of {format_quote_timestamp(1773763816)}", result.pages[0].text)


def test_accept_search_hits_quality_gate() -> None:
    from tools.web.web_research import WebHit, _accept_search_hits

    good = [
        WebHit(title="Iran latest", url="https://aljazeera.com/iran", snippet="Iran developments today", engine="google_html", score=None),
        WebHit(title="Reuters Iran", url="https://reuters.com/iran", snippet="Tehran latest", engine="google_html", score=None),
    ]
    assert _accept_search_hits("iran latest developments", "google_html", good)[0] is True

    # Instant-answer/encyclopedia never satisfies a current/officeholder query.
    enc = [WebHit(title="Iran", url="https://en.wikipedia.org/wiki/Iran", snippet="Iran is a country in Asia", engine="ddg_instant", score=None)]
    assert _accept_search_hits("who is the us president now", "ddg_instant", enc) == (False, "encyclopedia_for_current_query")

    # A hit that is only the search homepage is not a usable source.
    homepage = [WebHit(title="q", url="https://duckduckgo.com/?q=iran", snippet="", engine="ddg_instant", score=None)]
    assert _accept_search_hits("iran", "ddg_instant", homepage)[0] is False

    bot = [WebHit(title="Attention", url="https://example.com/x", snippet="Please verify you are human to continue (captcha)", engine="google_html", score=None)]
    assert _accept_search_hits("iran news", "google_html", bot) == (False, "anti_bot_or_consent")

    unrelated = [WebHit(title="Cooking pasta", url="https://example.com/pasta", snippet="boil water and add salt", engine="google_html", score=None)]
    assert _accept_search_hits("solana blockchain price", "google_html", unrelated) == (False, "unrelated_to_query")

    dup = [
        WebHit(title="widget report", url="https://x.example/a", snippet="widget", engine="google_html", score=None),
        WebHit(title="widget report copy", url="https://x.example/a", snippet="widget", engine="google_html", score=None),
    ]
    assert _accept_search_hits("widget report", "google_html", dup) == (False, "duplicate_only")

    assert _accept_search_hits("anything", "google_html", []) == (False, "empty")


def test_news_query_respects_word_boundary_and_names() -> None:
    from tools.web.web_research import _extract_news_topic, _looks_like_news_query

    assert _looks_like_news_query("iran news") is True
    assert _looks_like_news_query("BBC News") is True
    assert _looks_like_news_query("newsletter signup") is False
    assert _looks_like_news_query("newspaper delivery") is False
    # Meaningful names that contain "news" must not be mangled by topic extraction.
    for name in ("BBC News", "News Corp", "New York Daily News", "News UK"):
        assert _extract_news_topic(name) == name


def test_news_rss_fallback_returns_none_on_empty_feed() -> None:
    from unittest import mock

    from tools.web import web_research

    empty_rss = b'<?xml version="1.0"?><rss><channel></channel></rss>'

    class _Resp:
        def read(self, *args):
            return empty_rss

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    with mock.patch("urllib.request.urlopen", return_value=_Resp()), named_background_effect_scope(
        "test.web-research.news-rss-empty-feed"
    ):
        assert web_research._news_rss_fallback("latest iran news", max_hits=3, timeout_s=5.0) is None


if __name__ == "__main__":
    unittest.main()
