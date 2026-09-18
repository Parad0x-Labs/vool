from __future__ import annotations

import json
import os
import re
import threading
from collections import OrderedDict
import time
import unicodedata
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache as _functools_lru_cache
from typing import Any

from core import policy_engine
from core.live_quote_contract import LiveQuoteResult, format_quote_timestamp
from core.market_intent import MARKET_TERMS, market_quote_intent_present
from core.remote_fetch_policy import (
    RemoteFetchRefusedError as _RemoteFetchRefusedError,
)
from core.remote_fetch_policy import (
    open_remote as _open_remote_door,
)
from core.remote_fetch_policy import (
    remote_fetch_forbidden,
)
from core.source_credibility import evaluate_source_domain
from core.weather_result_contract import WeatherResult
from tools.browser.browser_render import browser_render
from tools.web import coin_index
from tools.web.ddg_instant import best_text_blob, ddg_instant_answer
from tools.web.http_fetch import http_fetch_text
from tools.web.search_api_client import SearchApiError as _SearchApiError
from tools.web.searxng_client import SearchResult, SearXNGClient


def _weather_keywords() -> tuple[str, ...]:
    # Same marker list the fast-path classifier uses to recognize weather queries
    # (core/agent_runtime/fast_live_info_mode_weather_markers.py), including common
    # misspellings like "wheater"/"wheather". Without sharing this list, a typo'd
    # query could get correctly classified as weather mode by the classifier, then
    # silently fail here because this module's own keyword check didn't know the
    # same typo meant "weather" too.
    #
    # Imported locally (not at module level): core.agent_runtime's package
    # __init__ eagerly imports fast_live_info_search, which imports
    # retrieval.web_adapter, which imports this module — a module-level import
    # here would be circular. By the time this function actually runs, both
    # modules have finished their top-level loading.
    from core.agent_runtime.fast_live_info_mode_weather_markers import _WEATHER_MARKERS

    return tuple(sorted({token.strip() for token in _WEATHER_MARKERS if token.strip()}))


def _domain_from_url(url: str) -> str:
    """Extract bare domain from URL, stripping www. prefix."""
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    netloc = (parsed.netloc or "").lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc


@dataclass(frozen=True)
class WebHit:
    title: str
    url: str
    snippet: str = ""
    engine: str | None = None
    score: float | None = None


@dataclass(frozen=True)
class PageEvidence:
    url: str
    final_url: str | None
    status: str
    title: str = ""
    text: str = ""
    html_len: int = 0
    used_browser: bool = False
    screenshot_path: str | None = None


@dataclass(frozen=True)
class ResearchResult:
    query: str
    provider: str
    hits: list[WebHit]
    pages: list[PageEvidence]
    notes: list[str]
    ts_utc: float


def _demote_instant_answer(order: list[str]) -> list[str]:
    # DuckDuckGo's Instant-Answer API returns encyclopedia abstracts (Wikipedia/MDN), NOT real web
    # search results, so it must never win over a real search engine. Try real engines first and fall
    # back to instant-answer only if they all yield nothing.
    real = [provider for provider in order if provider not in ("ddg", "ddg_instant")]
    instant = [provider for provider in order if provider in ("ddg", "ddg_instant")]
    return real + instant


#: Re-exported so this module's callers and `except` clauses are unchanged by the move.
RemoteFetchRefusedError = _RemoteFetchRefusedError


def _open_remote(request: Any, *, timeout: float):
    """The ONE outbound HTTP door for this module: enforce the veto, then report, then open.

    Measured on c6eed761, and the reason this exists. `_weather_fallback` fetched wttr.in and
    returned live conditions while `remote_fetch_attempt_count()` read 0, and it did so again
    inside a scope where `remote_fetch_forbidden()` was True -- an explicit `allow_remote_fetch:
    false` veto. Seven of this module's eight outbound paths neither reported nor checked; only
    `structured_weather_lookup` reported, after the same hole was closed there alone on 2026-08-13.

    That made two separate claims untrue at once. The accounting claim -- stated in
    `structured_weather_lookup` and in `remote_fetch_scope_active`'s docstring, that EVERY remote
    HTTP path reports itself first, "which is what lets a zero prove a negative" -- was false, so a
    turn could quote live data while its terminal trace reported `web_calls: 0`. And the policy
    claim was false, because a veto nothing consults is not a veto.

    Routing every site through one door is what makes both claims true by construction: a new
    fetch added later cannot forget to report or to ask, because there is nowhere else to open a
    socket. Refusal raises rather than returning empty, so a caller that ignores it fails closed
    instead of silently reporting "no results" for a request that was never allowed to run.
    """

    return _open_remote_door(request, timeout=timeout)


def _provider_order() -> list[str]:
    allowed = policy_engine.allowed_web_engines()
    allowed_set = set(allowed)
    env_raw = str(os.getenv("WEB_SEARCH_PROVIDER_ORDER", "")).strip()
    if env_raw:
        chosen = [item.strip().lower() for item in env_raw.split(",") if item.strip() and item.strip().lower() in allowed_set]
        # WEB_SEARCH_PROVIDER_ORDER sets ORDER, not permission — allowed_web_engines() is the
        # security boundary. A launcher-baked env order can predate a newly-allowed engine
        # (google_html — the keyless Brave/Yahoo scraper — was added to allowed_engines after the
        # installers shipped their stale "searxng,ddg_instant,duckduckgo_html" default), so append
        # any policy-allowed engine the env omitted. Otherwise a stale env silently drops the only
        # reliable keyless general-web provider and the chain is searxng (often down) +
        # duckduckgo_html (bot-blocked) + ddg_instant (encyclopedia-only) => zero hits.
        chosen += [engine for engine in allowed if engine not in chosen]
    else:
        chosen = [item for item in policy_engine.web_provider_order() if item in allowed_set]
    if "browser_search" in chosen and not _browser_search_available():
        # A machine with no Chromium-family browser must behave exactly as it did before this
        # provider existed: drop it from the chain rather than spend a turn on a provider that can
        # only answer `missing_dependency`.
        chosen = [item for item in chosen if item != "browser_search"]
    return _with_keyed_search_apis(_demote_instant_answer(chosen))


def keyed_search_api_providers() -> tuple[str, ...]:
    """Key-backed search providers that can actually run right now (a key is stored for them).

    Same shape of gate as `_browser_search_available`: a provider that cannot run is not put in
    the chain at all, so it can never spend a turn's budget only to report that it lacks a
    credential.
    """
    if remote_fetch_forbidden():
        return ()
    try:
        from core.search_providers import keyed_providers

        return keyed_providers()
    except Exception:
        return ()


def _with_keyed_search_apis(chosen: list[str]) -> list[str]:
    """Put every keyed search API in front of the keyless chain.

    Ordering: a provider the user paid for (or holds a free-tier key for) answers a general web
    query far more reliably than the keyless scrapers behind it, which are variously bot-blocked,
    often-down, or encyclopedia-only -- on 2026-08-19 that keyless chain returned zero results on
    9 of 9 runs. So a keyed provider leads, and the existing chain stays intact underneath it as
    the fallback for when a key is missing, rate-limited or revoked.

    These names are added here rather than being required to appear in
    `policy_engine.allowed_web_engines()`. That list is the permission boundary for engines the
    runtime reaches for on its own, and its own comment records that an older installed policy file
    silently strips engines added after it was written. Applying that behaviour to a key-backed
    provider would mean a user pastes a key, sees a success message, and gets nothing -- with no
    way to find out why. The key itself is the narrower and more explicit consent, and every other
    control still applies: the per-turn veto above, the egress door's accounting inside the client,
    and the transport check on the key itself.
    """
    keyed = [item for item in keyed_search_api_providers() if item not in chosen]
    return keyed + list(chosen)


def _is_search_api_provider(provider: str) -> bool:
    try:
        from core.search_providers import config_for

        return config_for(provider) is not None
    except Exception:
        return False


def _search_api_hits(provider: str, query: str, *, max_hits: int, timeout_s: float) -> list[WebHit]:
    """Run one key-backed search and return runtime-shaped hits.

    The key is read at call time and never held anywhere in this module: it goes from the
    credential store into the request the client builds, and nothing else in the path sees it.
    """
    from core.credential_store import get_credential
    from core.search_providers import config_for
    from tools.web.search_api_client import search as _search_api

    cfg = config_for(provider)
    if cfg is None:
        raise _SearchApiError("unknown_provider", provider)
    key = get_credential(cfg.credential_slot) or ""
    if not key.strip():
        # The chain only offers keyed providers, so an empty slot here means the key was deleted
        # between the order being computed and this call. Fail with the classified reason rather
        # than an exception type that reads like a bug.
        raise _SearchApiError("no_key", provider)
    rows = _search_api(cfg, query, key, max_hits=max_hits, timeout_s=timeout_s)
    return [
        WebHit(title=row.title, url=row.url, snippet=row.snippet, engine=provider, score=None)
        for row in rows
    ]


def _browser_search_available() -> bool:
    """Whether the browser-backed provider can actually run here.

    Two gates, both pre-existing: `web.allow_browser_fallback` is the policy switch for using a
    browser at all, and a browser binary has to be on disk. Note that `web.playwright_enabled` is
    NOT consulted -- that switch is about the Playwright dependency, which this provider does not
    have and must never acquire (installs are forbidden on this machine).
    """

    if not _should_try_browser():
        return False
    try:
        from tools.web.browser_search import browser_available

        return browser_available()
    except Exception:
        return False


# Default wall-clock ceiling for one web_research call (seconds). Keeps a single
# search+fetch cycle responsive even when scrapers are slow or blocked.
_DEFAULT_WEB_BUDGET_S = 18.0
# The headless-browser fallback is the slowest leg (browser launch + render). Only
# attempt it when at least this much of the budget remains, else record the plain fetch.
_BROWSER_MIN_BUDGET_S = 12.0
# The browser-backed SEARCH provider runs first and pays the same launch cost (measured 4.0-9.4s
# for a rendered results page on this machine). Skip it when the shared deadline cannot absorb a
# render plus at least one page fetch -- an HTTP scraper that answers in 1s is worth more than a
# browser that gets killed at the deadline with nothing to show.
_BROWSER_SEARCH_MIN_BUDGET_S = 10.0
# Wall clock handed to one browser render. Bounded by what is left of the shared deadline, minus
# room for the page fetches that follow.
_BROWSER_SEARCH_MAX_TIMEOUT_S = 20.0


def _should_try_browser() -> bool:
    env_value = str(os.getenv("ALLOW_BROWSER_FALLBACK", "")).lower()
    if env_value:
        return env_value in {"1", "true", "yes"}
    return policy_engine.allow_browser_fallback()


def _text_too_short(text: str) -> bool:
    return len((text or "").strip()) < 600


def _needs_browser(fetch_status: str, text: str) -> bool:
    return fetch_status in {"captcha", "login_wall"} or _text_too_short(text)


def _looks_like_weather_query(query: str) -> bool:
    lowered = str(query or "").lower()
    return any(token in lowered for token in _weather_keywords())


_NEWS_WORD_RE = re.compile(r"\bnews\b")


def _looks_like_news_query(query: str) -> bool:
    lowered = str(query or "").lower()
    # The standalone word "news" (not "newsletter"/"newspaper") means the caller wants current
    # coverage, so "iran news" / "latest iran news" / "give me a summary of the latest iran news" all
    # route to the Google News RSS lane instead of the general search chain.
    if _NEWS_WORD_RE.search(lowered):
        return True
    return any(
        token in lowered
        for token in (
            "headlines",
            "what happened today",
            "what's the latest on",
            "what is the latest on",
            "whats the latest on",
            "latest on ",
            "latest about ",
        )
    )


def _extract_weather_location(query: str) -> str:
    clean = re.sub(r"[\?\!\.,]+", " ", str(query or "")).strip()
    clean = re.sub(r"\s+", " ", clean)
    lowered = clean.lower()
    # The misspellings belong here too. `_weather_keywords()` above shares the classifier's marker
    # list precisely so a typo'd query cannot be classified as weather and then fail in this module
    # -- and these two lists re-hardcoded the correct spelling anyway, which is the failure that
    # comment was written to prevent.
    #
    # Measured live in the shipped app on 2026-08-18:
    #   "give me wheather in vilnius, berlin and rome also forecast for next 3 days in each of them"
    #     requirements_for  -> LIVE_DATA, multipart, toolsets=('weather',)   [classified correctly]
    #     build_live_data_plan -> None                                       [no city extracted]
    #     user saw          -> "no current weather results came back"
    # The same sentence spelled "weather" yields ['Vilnius', 'Berlin', 'Rome'].
    for pattern in (
        rf"\b(?:{_WEATHER_WORD_RE}|forecast|temperature|rain|snow|wind|humidity)\s+(?:in|for|at)\s+(.+)$",
        rf"\b(?:what is|what's|tell me|show me)\s+the\s+(?:{_WEATHER_WORD_RE}|forecast)\s+(?:in|for|at)\s+(.+)$",
        r"\b(?:in|for|at)\s+(.+)$",
    ):
        match = re.search(pattern, lowered)
        if match:
            location = match.group(1).strip()
            break
    else:
        location = lowered
        for token in ("weather", "wheather", "weater", "weathr", "wetaher", "weahter", "wheter",
                      "wather", "forecast", "temperature", "rain", "snow", "wind", "humidity"):
            location = location.replace(token, " ")

    tokens = [item for item in location.split() if item]
    trailing_noise = {
        "today",
        "tomorrow",
        "tonight",
        "now",
        "currently",
        "current",
        "forecast",
        "please",
        "right",
        "this",
        "week",
        "weekend",
    }
    while tokens and tokens[-1] in trailing_noise:
        tokens.pop()
    # Drop leading question/filler words so "what is the weather?" resolves to IP
    # geolocation instead of extracting "what is the" as a bogus place name.
    leading_filler = {
        "what", "whats", "what's", "how", "hows", "how's", "is", "are",
        "the", "a", "an", "hi", "hello", "hey", "yo", "tell", "me", "show",
        "please", "so", "and", "ok", "okay", "um", "like", "about",
    }
    while tokens and tokens[0] in leading_filler:
        tokens.pop(0)
    candidate = " ".join(tokens).strip()
    if not candidate:
        # Genuine "weather?" with no place named - let wttr.in use IP geolocation.
        return "current location"
    if not _is_plausible_weather_location(candidate):
        # Non-empty but not a real place name (scaffolding, a merged multi-message
        # blob, a leaked "[... GMT+N]" bracket, etc.). Returning "" tells
        # _weather_fallback to bail instead of fuzzy-matching garbage to a random
        # city - this is what turned "weather in Riga" into "Los Vargas, Mexico".
        return ""
    return candidate


def _notes_scope_nouns() -> frozenset[str]:
    """The container nouns the Notes scope reader owns ("folder", "account", "acct") -- lazily
    borrowed, the same way `_next_request_opener_re` borrows the demand grain's heads, so the
    weather extractor and the Notes scope reader share one vocabulary and one boundary."""
    from core.operator.apple_notes import _SCOPE_NOUNS

    return frozenset(_SCOPE_NOUNS)


def _is_plausible_weather_location(candidate: str) -> bool:
    text = str(candidate or "").strip()
    # ANVIL review closure (2026-08-07): an `if not text: return False` early return used to sit
    # here. The mutation matrix proved it was not load-bearing -- bypassing it left every empty and
    # whitespace-only input still rejected, because the final "must contain a letter" arm already
    # returns False for them. It was the one arm of this predicate that could not be given a
    # meaningful regression, since deleting it changes no outcome. Removed rather than kept as an
    # arm no test can defend; emptiness is still rejected, and `test_arm_empty` still asserts that
    # behaviour (now guarded by the letter arm, whose own mutation IS caught).
    # Real place names have no structural/scaffolding characters and are short.
    if any(ch in text for ch in "[]{}\n\r\t|"):
        return False
    # A trailing (or embedded) colon is a label/header signal, never part of a place name --
    # "for weather include:" and "source timestamp:" are section/field labels, not cities.
    # General, structural, and not keyed to any specific word from a particular incident.
    if ":" in text:
        return False
    lowered = text.lower()
    if "gmt" in lowered or "queued user message" in lowered:
        return False
    # ANVIL closure (2026-08-07): a WHOLE candidate that is nothing but a bare pronoun or
    # possessive is never a place. This is the residue the legacy render reconstruction leaves when
    # it subtracts its phrases from a question -- "What is my forecast" loses "what is" and
    # "forecast" and hands back "my". Deliberately a whole-string check, not a token scan: "my" on
    # its own is a residue, while "My Tho" and "She Xian" are real cities, which is exactly why
    # these words were removed from `_CLAUSE_STRUCTURE_MARKERS` (see that set's comment). "us" is
    # excluded because "weather in US" is a legitimate country lookup, not a pronoun.
    if lowered in _BARE_PRONOUN_TOKENS:
        return False
    # A WHOLE candidate that is nothing but a unit of measurement is never a place. Measured in the
    # regression at 19b004ec, 2026-08-18:
    #
    #   "Search for the current temperature in Celsius and wind direction in Auckland, New Zealand
    #    right now."  ->  "Weather in Celsius: ..."
    #
    # The locative "in Celsius" reads exactly like the locative "in Auckland", and the scale won
    # because it comes first. The answer was headed with a unit as though it were a city. Whole
    # string only, for the same reason the pronoun arm above is: "Kelvin Grove" is a real suburb
    # and "Bar" is a real Montenegrin city, while a bare "kelvin" in a weather question is a scale.
    # Declining is the safe direction -- the caller says it could not determine a location and asks
    # the user to name one, which is recoverable; heading an answer with the wrong place is not.
    if lowered in _MEASUREMENT_UNIT_TOKENS:
        return False
    # A candidate that names a container another request's scope owns is never a place. Measured
    # 2026-09-15, served: "what is the weather in Rome, in celsius please, and in the Work folder,
    # open my Apple note "Plan"" -- the extractor read "the Work folder" as a second city, the live
    # plan's binder took the Notes request's unit, and the live lane claimed the whole turn: the
    # Notes half vanished under a weather table. The locative "in the Work folder" reads exactly
    # like the locative "in Auckland"; the container noun is what says it is not one. Tail check in
    # the Notes scope reader's own vocabulary (`apple_notes._SCOPE_NOUNS`, one home for both
    # grains), multi-word candidates only -- a real place name never ends in a container noun.
    if len(lowered.split()) > 1 and lowered.split()[-1] in _notes_scope_nouns():
        return False
    # A candidate that names a tradable asset's market request is never a place. The weather and
    # market extractors read the same fused clause text ("weather in rome and gold price"), and
    # the list-splitter hands the market half to THIS filter as though it were a second city --
    # measured 2026-08-28: a phantom weather lookup for "Gold Price" failed and was reported as an
    # unanswerable place. Multi-word candidates only, in both arms: single-token candidates keep
    # their legitimate place readings ("Brent" the London borough, "Price" the Utah town), and
    # multi-word real places carry no market word ("Gold Coast" survives both arms).
    tokens = lowered.split()
    if len(tokens) > 1:
        if any(token in _MARKET_REQUEST_TOKENS for token in tokens):
            return False
        market_aliases = {
            alias
            for target in _MARKET_QUOTE_TARGETS
            for alias in (*target.aliases, target.asset_name.lower())
            if " " in alias
        }
        if lowered in market_aliases:
            return False
    # ANVIL closure (2026-08-07, B4): a flat 6-word cap refused real fully-qualified place names --
    # "San Carlos de Bariloche Rio Negro Argentina" (7 words) was accepted by the base and deferred
    # here. The cap is not raised globally (that would just let longer garbage through); instead the
    # allowance is earned by STRUCTURE, separating a legitimate location from scaffolding:
    #   * it must end in a recognized real-world geographic qualifier -- a sovereign country or a
    #     first-level region from `_GEOGRAPHIC_QUALIFIER_TERMS` -- which is positive evidence this
    #     span is a fully-qualified place, not prose that merely ran long; and
    #   * it must contain NO clause-structure marker, so an instruction can never buy the extra
    #     room by happening to mention a country.
    # An instruction-shaped string satisfies neither, and keeps the ordinary cap.
    word_count = len(text.split())
    tail = text.split(",")[-1].strip()
    fully_qualified = bool(tail) and _is_geographic_qualifier(tail.split()[-1] if tail.split() else "")
    earns_extra_room = fully_qualified and not _has_clause_structure_marker(text)
    max_words, max_chars = (10, 90) if earns_extra_room else (6, 60)
    if len(text) > max_chars or word_count > max_words:
        return False
    # Must contain at least one letter (a place name is never pure punctuation/digits).
    return any(ch.isalpha() for ch in text)


# Every English personal pronoun and possessive determiner, used ONLY as a whole-string check by
# `_is_plausible_weather_location` -- a candidate that is nothing but one of these is a leftover
# fragment, never a place. Deliberately broader than `_CLAUSE_STRUCTURE_MARKERS`, which scans
# TOKENS: "my"/"she"/"he" had to leave that set so "My Tho", "My Son", "She Xian" and "He Xian"
# resolve, but a bare "my" standing alone as an entire location is still not a city.
# "us" is excluded on purpose -- "weather in US" is a legitimate country lookup.
#: Market-request vocabulary. Scanned as TOKENS of multi-word weather candidates only -- a
#: multi-word place name never contains one of these, while "gold price" / "btc rate" always do.
#: Deliberately NOT "worth" or "cost": "Ft. Worth" and "Cost, Texas" are real places, and the
#: regression suite pins the first (test_live_data_anvil_closure's plausibility-arm matrix).
_MARKET_REQUEST_TOKENS = frozenset({
    "price", "prices", "quote", "quotes", "rate", "rates",
})

#: Units and scales a weather question names beside its place, never instead of one.
_MEASUREMENT_UNIT_TOKENS = frozenset({
    "c", "f", "k",
    "celsius", "celcius", "centigrade", "fahrenheit", "farenheit", "kelvin",
    "mph", "kph", "kmh", "km/h", "m/s", "knots", "knot", "beaufort",
    "hpa", "mbar", "mbars", "millibar", "millibars", "pascal", "pascals",
    "degrees", "degree", "percent", "percentage",
})

_BARE_PRONOUN_TOKENS = frozenset({
    "i", "me", "my", "mine", "myself",
    "you", "your", "yours", "yourself",
    "he", "him", "his", "himself",
    "she", "her", "hers", "herself",
    "it", "its", "itself",
    "we", "our", "ours", "ourselves",
    "they", "them", "their", "theirs", "themselves",
})

_SENTENCE_PUNCTUATION_RE = re.compile(r"(\w+)([.!?]+)(\s+|$)")

# The abbreviations that actually occur INSIDE a place name, written with a trailing period.
# Live-release repair (2026-08-07): this replaces a "<=3 letters" SHAPE test, which was never a
# test for "is an abbreviation" at all -- it was a test for "is a short word", and English is full
# of short words that end sentences. "Check the current weather in Kaunas and summarize it." was
# live-verified to keep its terminal period, because "it" is 2 letters and all-alpha, so the
# sentence never ended and the weather clause ran on into the instruction (see this round's
# LIVE FAILURE 1). "the sky.", "the bay.", "go." would all have done the same.
#
# This is a closed, bounded set of REAL abbreviations -- the same closed-reference-vocabulary
# approach `_GEOGRAPHIC_QUALIFIER_TERMS` uses, and the opposite of enumerating the (unbounded)
# short English words that must NOT be protected. Single letters ("D.C.", "U.S.A.") and all-digit
# tokens ("1.", "2." list markers) are handled structurally alongside it, since neither is a word.
# ANVIL review closure (2026-08-07): "Sta." (Santa) and "Ste." (Sainte) were REGRESSED by the
# change that introduced this set -- the "<=3 letters" heuristic it replaced covered them
# incidentally, and enumerating the real abbreviations initially missed them. "Sta. Barbara"
# collapsed to "Sta", and in a "/"-delimited list ("Sta. Barbara / Tallinn") the false sentence
# break also swallowed the sibling city outright. Fixed the way this design is meant to be
# extended -- by naming the missing real abbreviation -- NOT by restoring the length heuristic.
# Every member of this set is individually guarded by a test, so a future removal of any one of
# them fails loudly instead of silently regressing another place name (see
# `tests/test_live_data_anvil_closure.py::TestEveryAbbreviationIsGuarded`).
_PLACE_NAME_ABBREVIATIONS = frozenset({
    "st",    # Saint / Street -- "St. Louis", "St. Petersburg", "St. John's"
    "sta",   # Santa          -- "Sta. Barbara"
    "ste",   # Sainte         -- "Ste. Genevieve"
    "ft",    # Fort           -- "Ft. Worth"
    "mt",    # Mount          -- "Mt. Vernon"
    "pt",    # Point / Port
    "dr",    # Drive
    "rd",    # Road
    "ave",   # Avenue
    "blvd",  # Boulevard
    "hwy",   # Highway
    "rte",   # Route
})


def _split_sentence_punctuation(text: str) -> list[str]:
    """Split on sentence-final `.`/`!`/`?`, without breaking a dotted abbreviation ("St." / "Ft."
    / "D.C.") or a numbered-list marker ("1." / "2.") mid-token -- see `_iter_prompt_blocks` for
    why this boundary needs to exist at all.

    `!`/`?` always end a sentence -- nobody abbreviates with those. A run of `.` ends a sentence
    UNLESS the word immediately before it is PROTECTED, which means exactly one of:
    - a known place-name abbreviation (`_PLACE_NAME_ABBREVIATIONS` -- "St", "Ft", "Mt", ...);
    - a single letter (the "D" and "C" of "D.C.", the initials of "U.S.A.") -- not a word at all;
    - an all-digit token (a "1." / "2." numbered-list marker) -- also not a word.
    Everything else ends the sentence normally, including any ordinary short word ("it.", "sky.")
    and any token mixing letters and digits ("Zzznotarealplace1234.").

    Mnemosyne review repair, round 3 (2026-08-07): an earlier version used a pure regex lookbehind
    requiring 4+ CONSECUTIVE LETTERS before the period, which protected "St."/"Ft." but silently
    stopped stripping the sentence-ending period off any word ending in digits
    ("Zzznotarealplace1234.") -- caught by the existing adversarial "one invalid sibling" test.
    That was replaced by a "<=3 all-letter characters" shape test, which had the mirror-image
    defect: it protected every SHORT word, not every ABBREVIATION. Live-release repair
    (2026-08-07) replaces the shape test with the closed set above, so the question asked is "is
    this a known abbreviation" rather than "is this word short".
    """
    pieces: list[str] = []
    cursor = 0
    for match in _SENTENCE_PUNCTUATION_RE.finditer(text):
        word, punct, _trailing = match.groups()
        is_abbreviation = punct == "." * len(punct) and (
            word.lower() in _PLACE_NAME_ABBREVIATIONS
            or (word.isalpha() and len(word) == 1)
            or word.isdigit()
        )
        if is_abbreviation:
            continue
        pieces.append(text[cursor:match.start(2)])
        cursor = match.end()
    pieces.append(text[cursor:])
    return [piece for piece in pieces if piece.strip()] or [text]


def _iter_prompt_blocks(text: str) -> list[list[str]]:
    """Paragraphs: blank-line-separated runs of non-empty lines, each its own list of lines in
    source order.

    Entity-contamination hotfix (2026-08-07, production incident): section-bounded extraction
    never scans across a blank line. A blank line is the one universal, content-independent
    signal a prompt uses to separate "this request's entity list" from everything else in the
    message -- a later paragraph describing the desired output format, a trailing disclaimer, a
    completely unrelated instruction. Bounding to the paragraph is what makes it structurally
    impossible for "For Weather include: - condition; - today's high and low..." (a LATER,
    blank-line-separated paragraph) to ever be scanned as part of the SAME clause as "weather in
    Kaunas, Tallinn, and Warsaw." (an EARLIER paragraph/line).

    Mnemosyne review repair (2026-08-07): a blank line is not the ONLY boundary a real prompt
    uses -- a "compact single-line instruction style" ("weather in Kaunas, Tallinn, and Warsaw.
    Run independent compatible live-data requests in parallel.") has no blank line, and no
    newline, at all between the entity list and the instructions that follow it. Two more
    universal, content-independent boundaries survive that flattening and split a physical line
    into separate units here, BEFORE the punctuation-stripping below:
    - Sentence-final punctuation, via `_split_sentence_punctuation` -- see that function for the
      exact rule (a `.` after a short all-letter or all-digit token is protected as an
      abbreviation/list-marker, not a sentence end; everything else, including `!`/`?` and a `.`
      after any longer or digit-containing word, ends the sentence). Round 2's first version of
      this fix treated ANY letter before a period as a sentence end and destroyed "St. Louis"
      down to "St"; the fix after that used a 4-consecutive-letter lookbehind that protected
      abbreviations correctly but then silently stopped stripping the sentence-ending period off
      any word ending in digits ("Zzznotarealplace1234.") -- live-verified as a regression this
      round, caught by the existing "one invalid weather sibling" adversarial test.
    - A bullet dash surrounded by whitespace (" - "), the one structural trace a flattened
      markdown bullet list leaves behind ("two tables: - Markets - Weather For Markets include:
      ..." was written as a nested bullet list; flattened, "Weather" sits immediately before "For
      Markets" with nothing but this dash between them, which is otherwise indistinguishable from
      "weather for <location>"). A hyphen with no surrounding whitespace ("24-hour", "Kaunas-
      Lithuania") is a compound word, not a bullet, and is untouched.
    A blank raw line still contributes exactly one empty placeholder, preserving the existing
    block-boundary behavior. Case is preserved here (only whitespace is collapsed) -- callers that
    need capitalization as a signal (see `_split_weather_candidates`'s trailing-prose handling)
    depend on it surviving this far; the single point where candidates are finally lower-cased is
    `_extract_weather_locations`'s own `_add()`.
    """
    lines: list[str] = []
    for raw_line in str(text or "").splitlines():
        if not raw_line.strip():
            lines.append("")
            continue
        for piece in re.split(r"\s-\s+", raw_line):
            for sentence in _split_sentence_punctuation(piece):
                cleaned = re.sub(r"[ \t]+", " ", sentence).strip()
                if cleaned:
                    lines.append(cleaned)
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line:
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


def _strip_list_marker(line: str) -> str:
    """Remove one leading markdown bullet ("-", "*", "•") or numbered-list marker ("1.", "2)") --
    a line's own content is the candidate; its list decoration is not."""
    return re.sub(r"^(?:[-*•]|\d+[\.\)])\s*", "", line).strip()


# Mnemosyne review repair, round 3 (2026-08-07): "Kaunas, Tallinn" (two cities) and "Kaunas,
# Lithuania" (one city qualified by its country) are the SAME comma shape -- there is no purely
# structural signal (segment count, parity, punctuation) that distinguishes them, because the
# distinction is genuinely about what the second word NAMES, not how the sentence is punctuated.
# Round 2's segment-count-parity rule guessed from shape alone and was proven wrong on both a
# plain 2-city list ("Kaunas, Tallinn" merged into one place) and a plain 4-city list ("Kaunas,
# Tallinn, Warsaw, Helsinki" paired into two nonsense composites), live-verified to reach wttr.in
# and return a plausible-looking answer for an undefined location.
#
# This is a closed, stable, real-world reference vocabulary -- sovereign countries and major
# first-level administrative regions -- not an incident-specific word list. It answers exactly one
# question, "does this specific comma segment name a geographic region a city could be qualified
# by," using actual evidence about what the word IS, rather than inferring meaning from sentence
# shape. It does not grow with each new reproduction the way a blacklist of "Run"/"Return"/
# "Source" would; the set of sovereign states and US states changes on a timescale of decades, not
# incidents. It is intentionally NOT exhaustive (no full ISO-3166 subdivision list, no historical
# names) -- it covers the common-use "City, Country" and "City, State" English convention, which is
# what this disambiguation exists to recognize.
_GEOGRAPHIC_QUALIFIER_TERMS = frozenset(term.lower() for term in (
    "Afghanistan", "Albania", "Algeria", "Andorra", "Angola", "Argentina", "Armenia",
    "Australia", "Austria", "Azerbaijan", "Bahamas", "Bahrain", "Bangladesh", "Barbados",
    "Belarus", "Belgium", "Belize", "Benin", "Bhutan", "Bolivia", "Bosnia", "Herzegovina",
    "Botswana", "Brazil", "Brunei", "Bulgaria", "Burkina Faso", "Burundi", "Cambodia",
    "Cameroon", "Canada", "Chad", "Chile", "China", "Colombia", "Comoros", "Congo",
    "Costa Rica", "Croatia", "Cuba", "Cyprus", "Czechia", "Czech Republic", "Denmark",
    "Djibouti", "Dominica", "Ecuador", "Egypt", "El Salvador", "Eritrea", "Estonia",
    "Eswatini", "Ethiopia", "Fiji", "Finland", "France", "Gabon", "Gambia", "Georgia",
    "Germany", "Ghana", "Greece", "Grenada", "Guatemala", "Guinea", "Guyana", "Haiti",
    "Honduras", "Hungary", "Iceland", "India", "Indonesia", "Iran", "Iraq", "Ireland",
    "Israel", "Italy", "Jamaica", "Japan", "Jordan", "Kazakhstan", "Kenya", "Kiribati",
    "Kosovo", "Kuwait", "Kyrgyzstan", "Laos", "Latvia", "Lebanon", "Lesotho", "Liberia",
    "Libya", "Liechtenstein", "Lithuania", "Luxembourg", "Madagascar", "Malawi", "Malaysia",
    "Maldives", "Mali", "Malta", "Mauritania", "Mauritius", "Mexico", "Moldova", "Monaco",
    "Mongolia", "Montenegro", "Morocco", "Mozambique", "Myanmar", "Namibia", "Nauru",
    "Nepal", "Netherlands", "New Zealand", "Nicaragua", "Niger", "Nigeria",
    "North Korea", "North Macedonia", "Norway", "Oman", "Pakistan", "Palau", "Panama",
    "Papua New Guinea", "Paraguay", "Peru", "Philippines", "Poland", "Portugal", "Qatar",
    "Romania", "Russia", "Rwanda", "Samoa", "San Marino", "Saudi Arabia", "Senegal",
    "Serbia", "Seychelles", "Sierra Leone", "Singapore", "Slovakia", "Slovenia",
    "Solomon Islands", "Somalia", "South Africa", "South Korea", "South Sudan", "Spain",
    "Sri Lanka", "Sudan", "Suriname", "Sweden", "Switzerland", "Syria", "Taiwan",
    "Tajikistan", "Tanzania", "Thailand", "Timor-Leste", "Togo", "Tonga",
    "Trinidad", "Tobago", "Tunisia", "Turkey", "Turkmenistan", "Tuvalu", "Uganda",
    "Ukraine", "United Arab Emirates", "United Kingdom", "United States",
    "Uruguay", "Uzbekistan", "Vanuatu", "Vatican", "Venezuela", "Vietnam", "Yemen",
    "Zambia", "Zimbabwe",
    # Common short/adjective forms actually used mid-sentence, distinct from the full name above.
    "USA", "US", "U.S.", "U.S.A.", "UK", "U.K.", "England", "Scotland", "Wales",
    "Northern Ireland",
    # US states -- "City, State" is as common an English qualifier convention as "City, Country".
    "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado", "Connecticut",
    "Delaware", "Florida", "Georgia", "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa",
    "Kansas", "Kentucky", "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan",
    "Minnesota", "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
    "New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina",
    "North Dakota", "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island",
    "South Carolina", "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
    "Washington", "West Virginia", "Wisconsin", "Wyoming",
    # Mnemosyne review repair, final narrow round (2026-08-07), Blocker 3: "Toronto, Ontario",
    # "Vancouver, British Columbia", "Sydney, New South Wales", "Munich, Bavaria", "Barcelona,
    # Catalonia" were each splitting into TWO independent requested cities ("Toronto" and "Ontario"
    # as separate provider calls) because the qualifier scan above only recognized SOVEREIGN
    # COUNTRIES and US STATES -- a real region name it did not happen to know was treated exactly
    # like a second, unrelated city. These are the first-level administrative subdivisions of the
    # other English-language-common "City, Region" countries, same closed-vocabulary evidence-based
    # approach as the country/US-state list above, not a new mechanism.
    #
    # Deliberately excludes any subdivision whose name is ALSO commonly used as a standalone CITY
    # name in its own right (Germany's Berlin/Hamburg/Bremen are city-states; Spain's Madrid,
    # Valencia, and Murcia share their name with their own capital city) -- including one of these
    # would risk the exact opposite failure this list exists to prevent: merging a genuine two-city
    # list like "Warsaw, Berlin" into one bogus composite, which is Blocker A's failure mode, not
    # Blocker 3's. A subdivision beyond what is listed here (or one of these excluded ambiguous
    # names) still falls through to the existing bare-comma-segment behavior -- two independent
    # requested entities -- since there is no purely structural way to tell an unrecognized region
    # name apart from a second city without a full gazetteer; this is a named, accepted limitation,
    # not a claim of exhaustive coverage.
    #
    # Canada -- provinces and territories.
    "Alberta", "British Columbia", "Manitoba", "New Brunswick", "Newfoundland and Labrador",
    "Nova Scotia", "Ontario", "Prince Edward Island", "Quebec", "Saskatchewan",
    "Northwest Territories", "Nunavut", "Yukon",
    # Australia -- states and territories.
    "New South Wales", "Victoria", "Queensland", "Western Australia", "South Australia",
    "Tasmania", "Australian Capital Territory", "Northern Territory",
    # Germany -- states (Bundesländer), excluding the three city-states (see note above).
    "Baden-Württemberg", "Bavaria", "Brandenburg", "Hesse", "Lower Saxony",
    "Mecklenburg-Vorpommern", "North Rhine-Westphalia", "Rhineland-Palatinate", "Saarland",
    "Saxony", "Saxony-Anhalt", "Schleswig-Holstein", "Thuringia",
    # Spain -- autonomous communities, excluding the three that share a name with their own
    # capital city (see note above).
    "Andalusia", "Aragon", "Asturias", "Balearic Islands", "Basque Country", "Canary Islands",
    "Cantabria", "Castile and León", "Castile-La Mancha", "Catalonia", "Extremadura", "Galicia",
    "La Rioja", "Navarre",
    # A common short form for a country already listed above by its full name.
    "UAE",
))


def _is_geographic_qualifier(segment: str) -> bool:
    """Whether this ONE comma segment, on its own, names a country/region a preceding city
    segment could be qualified by -- see `_GEOGRAPHIC_QUALIFIER_TERMS` for what this is and is
    not. Matches on the whole trimmed segment, case-insensitively; "D.C." specifically is also
    recognized (Washington's own short form), since periods inside it survive extraction intact.
    """
    normalized = segment.strip().lower()
    if normalized in _GEOGRAPHIC_QUALIFIER_TERMS:
        return True
    no_periods = normalized.replace(".", "")
    return no_periods in _GEOGRAPHIC_QUALIFIER_TERMS or no_periods == "dc"


_WEATHER_TRAILING_NOISE = {
    "today", "tomorrow", "tonight", "now", "currently", "current", "forecast",
    "please", "right", "this", "week", "weekend",
    # A forward-looking qualifier is built from these too. Without them "for next week" strips to
    # "for next", which jams the loop and discards the whole candidate -- and with it the entire
    # live-data plan, so a request that names its city is answered as if it named none.
    "next", "coming", "upcoming", "day", "days", "hour", "hours",
    # Standoff dashes. "moscow — which city is warmst?" (M3B, measured live 2026-08-30)
    # left the dash glued to the candidate, and a needle carrying it can never bind a
    # demand unit's span -- the city was planned but provably served nobody. A dash is
    # punctuation a place name never ends in, the same law as the noise words above.
    "-", "--", "—", "–",
}
#: Prepositions that introduce a temporal qualifier and dangle once it is removed.
_WEATHER_DANGLING_PREPOSITIONS = {"for", "over", "during", "within", "in", "on", "at"}
#: A temporal qualifier standing immediately before the preposition that introduces the place --
#: "tomorrow in Vilnius", "next week in Oslo". Trailing-edge stripping alone never reaches these,
#: so the candidate keeps a time word and stops resolving. Anchored on the following preposition
#: so a place name that merely contains a time word ("Sunday River", "Christmas Island") is safe.
_LEADING_TIME_QUALIFIER_RE = re.compile(
    r"\b(?:today|tomorrow|tonight|now|currently|"
    r"(?:this|next|the\s+coming|the\s+upcoming)\s+(?:week|weekend|month)|"
    r"(?:the\s+)?next\s+\d{1,2}\s+(?:hours?|days?|weeks?)|"
    r"over\s+the\s+weekend)\s+(?=(?:in|for|at|around)\s)",
    re.IGNORECASE,
)
_WEATHER_LEADING_FILLER = {
    "what", "whats", "what's", "how", "hows", "how's", "is", "are",
    "the", "a", "an", "hi", "hello", "hey", "yo", "tell", "me", "show",
    "please", "so", "and", "ok", "okay", "um", "like", "about", "in", "for", "at",
}

# Mnemosyne review repair, final mixed-intent round (2026-08-07): a closed, small, universal
# CLOSED-CLASS of English function words -- WH-words, personal pronouns, auxiliary/modal verbs, and
# sequence adverbs -- that a genuine place-name span never contains, at any position. This is a
# fundamentally different kind of list from a growing incident blacklist (`_BAD_WEATHER_WORDS =
# {inspect, tell, run, execute, ...}`, explicitly rejected): English has thousands of ordinary verbs
# and nouns that could plausibly open a NEW instruction clause ("inspect", "execute", "run" today;
# some other verb in the next incident), and enumerating them one at a time never converges. But
# English has only a few dozen function words total, full stop -- they do not grow, and they mark
# CLAUSE STRUCTURE (a subject, a question, a verb phrase) rather than naming any specific action.
# `_WEATHER_LEADING_FILLER` already treats several of these the same way at the START of a
# candidate (stripped, not rejected, since "the weather in Kaunas" is legitimate with "the" removed
# from the FRONT); this set catches the same category of word surviving in a NON-initial position,
# where it can only mean the candidate is not a bare place name at all -- "inspect THE provider
# retry implementation" (a determiner deep inside an instruction clause), "then tell ME WHICH"
# (a pronoun and a WH-word). A real, legitimate multi-word place name -- "Rio de Janeiro", "Ho Chi
# Minh City", "Isle of Man", "District of Columbia" -- never contains any of these, at any position,
# in any language's common-use English rendering. See `_split_weather_candidates`'s docstring for
# where and why this is applied.
_CLAUSE_STRUCTURE_MARKERS = {
    # WH-words -- introduce a question, never a place name.
    "which", "who", "how", "what's", "whats", "whose", "whom", "whether", "why",
    # Pronouns. Membership here is NOT "every English pronoun" -- it is the intersection of two
    # requirements, and a word must satisfy both:
    #   (a) a real instruction continuation actually needs it ("summarize IT", "rank THEM",
    #       "show ME"), and
    #   (b) it is not a syllable that appears as a standalone token inside a real place name.
    #
    # ANVIL closure (2026-08-07): taking every pronoun broke requirement (b) and regressed real
    # cities the base resolved -- "My Tho" and "My Son" (Vietnam) died on "my", "She Xian" and
    # "He Xian" (China) on "she"/"he", and in a list "My Tho and Tallinn" silently returned only
    # Tallinn, losing a requested sibling. Romanized Asian toponyms are full of short syllables
    # that collide with English function words, so a subject/possessive pronoun is only safe here
    # if some required continuation actually depends on it.
    #
    # REMOVED for collision with no demonstrated need: he, she, we, i, my, his, hers, our, your.
    # Every LIVE FAILURE 1 continuation is still contained without them -- "summarize/describe/
    # compare/explain it" on "it", "rank them" on "them", "show/tell/give me" on "me" -- each
    # asserted in `tests/test_live_data_anvil_final_closure.py::TestContinuationsStayContained`.
    #
    # "us" stays excluded for the same class of reason it always was: it collides with the "US"
    # short form for the United States in `_GEOGRAPHIC_QUALIFIER_TERMS` ("Boston, US").
    "me", "you", "him", "her", "them", "myself", "yourself", "himself", "herself",
    "ourselves", "themselves",
    "it", "they",
    # Possessive determiners -- kept only where a continuation needs them and no place-name
    # collision is known ("their"); the rest were removed with the subject pronouns above.
    "its", "their",
    # Auxiliary/modal verbs -- mark a verb phrase, never appear inside a place name.
    "is", "are", "was", "were", "has", "have", "had", "does", "did", "will", "would", "can",
    "could", "should", "must", "shall", "been", "being",
    # A determiner surviving past the leading position means the span still has clause-internal
    # structure ("inspect THE provider..."), not just a name with its own leading article stripped.
    "the", "a", "an",
    # Sequence adverbs -- introduce the NEXT instruction in a list of actions, never part of a name.
    "then", "next", "finally", "afterward", "afterwards", "subsequently", "meanwhile",
    # Demonstrative determiners -- ANVIL review closure (2026-08-07). Same closed grammatical class
    # as the bare determiners above (English has exactly four), and the one that "Rank these
    # cities" turns on: it points AT something previously mentioned, so a span containing one is
    # referring back to a list, not naming a place.
    "these", "those",
}


# A trailing clock time, with or without a governing preposition: "at 15:00", "14:30", "at 9:05 pm".
# ANVIL closure (2026-08-07, B3): "weather in London at 15:00" resolved to nothing, because the
# clause captured "London at 15:00" and the plausibility predicate rejects any string containing a
# colon (a label/header signal). The colon rule is right; the input simply is not a label -- it is
# a real city plus a TIME QUALIFIER. This is recognized by numeric structure (1-2 digits, colon,
# exactly 2 digits, optional meridiem), not by a phrase list, and only ever consumes a trailing
# run, so a real place name is never shortened by it.
_TIME_QUALIFIER_SUFFIX_RE = re.compile(
    r"\s*(?:\b(?:at|around|about|by|near|from)\b\s*)?"
    r"\d{1,2}:\d{2}\s*(?:[ap]\.?m\.?)?\s*$",
    re.IGNORECASE,
)
# Left over once the time itself is gone ("Paris at 14:30 today" -> trailing-noise drops "today",
# the rule above drops "14:30", and this drops the now-dangling preposition).
_DANGLING_TIME_PREPOSITIONS = {"at", "around", "about", "by", "near", "from", "on"}


def _strip_time_qualifier(text: str) -> str:
    """`text` with a trailing clock-time qualifier removed -- see `_TIME_QUALIFIER_SUFFIX_RE`."""
    stripped = _TIME_QUALIFIER_SUFFIX_RE.sub("", str(text or "")).strip()
    tokens = stripped.split()
    while tokens and tokens[-1].lower() in _DANGLING_TIME_PREPOSITIONS:
        tokens.pop()
    return " ".join(tokens) if tokens else stripped


#: The interrogative openers of `_CLAUSE_STRUCTURE_MARKERS`. A segment whose FIRST marker
#: is one of these is a trailing QUESTION riding the tail of the place list ("moscow which
#: one is warmr?" -- the comparison ask absorbed into the last list item because no sentence
#: separator preceded it), which is the one absorption shape whose head is a place the
#: user actually requested. `whether` is deliberately ABSENT: it opens a verb-phrase
#: complement ("check whether the cache is warm"), not a question -- including it
#: recovered the verb "check" as a phantom city (measured: mnemosyne round-5's deployment
#: prose). The instruction continuations the ANVIL closures pin ("summarize it", "rank
#: them", "show me") carry pronoun/verb markers instead, so they keep the strict
#: whole-candidate rejection and recover nothing.
_WH_MARKERS = frozenset(
    {"which", "who", "how", "what's", "whats", "whose", "whom", "why"}
)


def _first_structure_marker(tokens: list[str]) -> tuple[int, str] | None:
    """(index, normalized token) of the first clause-structure marker, or None."""
    for index, raw_token in enumerate(tokens):
        normalized = raw_token.strip(".,;:!?'\"()").lower()
        if normalized in _CLAUSE_STRUCTURE_MARKERS:
            return index, normalized
    return None


def _has_clause_structure_marker(text: str) -> bool:
    """Whether `text` contains a closed-class English function word anywhere in it.

    ANVIL review closure (2026-08-07): factored out of `_split_weather_candidates` so the legacy
    render reconstruction (`core.agent_runtime.fast_live_info_weather_rendering`) applies the SAME
    instruction vocabulary the extraction path already applies, rather than growing a second,
    parallel notion of "this is an instruction, not a place". One definition, two call sites --
    which is also why the render side needed no new phrase list to reject "Rank these cities",
    "Which is warmer", or "Summarize the weather".

    Tokenized on whitespace with surrounding punctuation stripped per token, because the raw split
    keeps it attached: the released build could not match "it." against "it", which is precisely
    why LIVE FAILURE 1's phantom survived the check that was supposed to catch it.
    """
    for raw_token in str(text or "").split():
        if raw_token.strip(".,;:!?'\"()").lower() in _CLAUSE_STRUCTURE_MARKERS:
            return True
    return False
_WEATHER_HEADER_RE = re.compile(r"^(?:\d+[\.\)]\s*)?weather\s*:?\s*$", re.IGNORECASE)
# A NEW section starting is what stops a header-block's list from being read any further -- a bare
# "Markets"/"Market" header, or another "Weather" header (a second, later weather-labeled block
# should never be folded into an earlier one). Structural (what shape a line has), not a list of
# words drawn from any one incident.
_NEW_SECTION_HEADER_RE = re.compile(r"^(?:\d+[\.\)]\s*)?(?:weather|markets?)\s*:?\s*$", re.IGNORECASE)

# The vocabulary that signals "a market/price list is being introduced here" -- shared between
# `_MARKET_LIST_CUE_RE` (finds where a market clause STARTS, defined further below) and
# `_WEATHER_LIST_BOUNDARY_RE` (finds where a weather clause on the same line must STOP, because a
# market clause has begun). One fragment, two uses, so the two lists can never drift apart.
_PRICE_CUE_FRAGMENT = (
    r"price(?:s)?(?:\s+and\s+(?:daily|24[\s-]?hour)\s+change)?\s+(?:only\s+)?(?:for|of)"
    r"|market\s+data\s+(?:only\s+)?(?:for|of|on)"
    r"|quote(?:s)?\s+(?:for|of|on)"
    r"|value\s+of"
    r"|cost\s+of"
    r"|current\s+price(?:s)?\s+(?:for|of)"
)

# The mirror image of `_MARKET_LIST_BOUNDARY_RE` below: where a weather clause ends and something
# else begins on the SAME line. Shapes: (1) a market clause starts ("Weather in Gold Coast
# and Silver Spring, plus the price of gold, silver, and Bitcoin") -- without this, the weather
# clause's own `(.+)$` capture runs straight through "plus the price of gold, silver, and Bitcoin"
# and hands back "silver"/"bitcoin" as bogus cities. (2) a SECOND, unrelated weather-trigger
# mention appears later on the same line ("...weather in Vilnius, Riga, and Helsinki... weather
# needs today's high and low...") -- the same trigger vocabulary the main pattern already matches
# against, reused as a stop signal rather than a list of words specific to any one reproduction.
# (3) "market"/"markets" bare, the domain's OWN other-table name, mirroring why bare "weather" is
# already in this list: "Weather For Markets include: ..." (a nested bullet list flattened to
# prose, "Weather"/"Markets" both table-section labels) matches pattern 1's "weather ... for ..."
# shape. This is the SAME two-table vocabulary this module already treats as structural
# (`_MARKET_HEADER_RE`, `_MARKET_LIST_CUE_RE`), not a new incident-specific word. (4) the request's
# OWN execution-instruction language -- "live-data"/"in parallel" -- added in the final narrow
# repair round (2026-08-07) once capitalization-based trailing-prose recovery (see the removed
# `_title_case_run`, below) was retired: "weather in Kaunas and Tallinn live-data request in
# parallel" has no comma, no "and", no period between "Tallinn" and "live-data" for anything else
# to cut on, so the clause-boundary scan itself has to recognize where the entity list ends and
# this system's OWN description of how it executes a live-data request begins. This is the same
# category of thing as (1)-(3) above -- the DOMAIN's own recurring vocabulary for describing its
# own request shape (verified against this benchmark's actual production prompt, which uses this
# exact phrase: "Run independent compatible live-data requests in parallel") -- not a growing list
# of words drawn from one incident's specific wording. A trailing continuation this vocabulary does
# not name (see `_split_weather_candidates`'s docstring) is a documented, accepted gap, not a claim
# of exhaustive coverage.
# Every weather-word list in this module reads from HERE. Six separate hardcoded copies of
# "weather|forecast|..." existed, and `_weather_keywords()` above already carries a comment
# explaining that a typo'd query classified as weather would "silently fail here because this
# module's own keyword check didn't know the same typo meant weather too" -- which is exactly what
# happened, measured live in the shipped app on 2026-08-18:
#
#   "give me wheather in vilnius, berlin and rome ..."
#     classified LIVE_DATA multipart weather, then build_live_data_plan -> None (no city found),
#     and the user was told "no current weather results came back".
#
# `whether` is deliberately excluded: ordinary English, and matching it would send non-weather
# turns to a weather provider.
_WEATHER_WORD_RE = r"(?:weather|wheather|weater|weathr|wetaher|weahter|wheter|wather)"

_WEATHER_LIST_BOUNDARY_RE = re.compile(
    r"\b(?:plus|and\s+also|weather|forecast|temperature|humidity|rain|snow|wind|market(?:s)?|"
    r"live[\s-]?data|in\s+parallel|"
    + _PRICE_CUE_FRAGMENT + r")\b",
    re.IGNORECASE,
)


def _split_weather_candidates(clause: str) -> list[tuple[str, bool]]:
    """Split an ALREADY-BOUNDED clause into (candidate, confident) pairs -- never decides how much
    of the prompt that clause may span (see `_weather_clause_on_line`/the header-block path for
    that). `confident` is True when something OTHER than "ran out of text" closed the candidate's
    right edge (another delimiter, or a matched geographic qualifier); False for the one
    unavoidable exception -- the LAST candidate of a multi-item split, which nothing but the
    clause's own end bounds. `_extract_weather_locations` threads this into each subtask's
    provenance as `extraction_confidence`, recorded for what it establishes about HOW the
    candidate was bounded, not consulted as a length gate (see
    `core.agent_runtime.live_data_runner._run_weather_subtask` for why that changed).

    Mnemosyne review repair, final narrow round (2026-08-07, Blockers 1/2): a `confident=False`
    candidate is returned exactly as extracted, in full, regardless of its word count or
    capitalization -- this function no longer trims or truncates it. An earlier version tried to
    recover a bounded-looking span from an unconfident candidate by keeping only its leading
    capitalized tokens (or its first token, if none were capitalized), which is what turned "Rio de
    Janeiro" into "Rio" and an all-lowercase "new york city" into "New": capitalization is not proof
    a place name ended ("Isle of Man", "District of Columbia" both have lowercase connector words),
    and a real multi-word place is not made less real by being long ("Ho Chi Minh City" is 4
    words). See `_WEATHER_LIST_BOUNDARY_RE`'s docstring for where the actual trailing-prose defense
    now lives (the clause boundary, computed before this function ever runs), instead of a per-
    candidate trim.

    Mnemosyne review repair, round 3 (2026-08-07, Blocker A): round 2 resolved a bare comma (no
    "and"/"&") by SEGMENT-COUNT PARITY -- odd count = flat list, even count = (city, qualifier)
    pairs. Proven wrong, live: "Kaunas, Tallinn" (2 real cities, no qualifier) merged into one
    place, and "Kaunas, Tallinn, Warsaw, Helsinki" (4 real cities) paired into two nonsense
    composites, both reaching wttr.in and returning a plausible-looking answer for an undefined
    location. Parity guesses from comma COUNT alone; it cannot distinguish "Kaunas, Tallinn" from
    "Kaunas, Lithuania" because they have the identical shape -- the difference is what the SECOND
    word actually names, not how many commas came before it.

    Replaced with a greedy scan against `_GEOGRAPHIC_QUALIFIER_TERMS`: walk the segments
    left-to-right; if the NEXT segment is a real country/region name, the current segment is
    qualified by it and the two merge into one candidate (real evidence, not a guess); otherwise
    the current segment stands alone as its own city and the scan continues from the next one.
    "Kaunas, Tallinn" -> neither is a qualifier -> two cities. "Kaunas, Lithuania" -> "Lithuania"
    IS one -> one place. "Kaunas, Lithuania, Tallinn, Estonia" -> "Lithuania" and "Estonia" both
    match -> two qualified pairs, using the SAME evidence-based rule, not a parity coincidence.

    Mnemosyne review repair, final mixed-intent round (2026-08-07, Blocker B): "and"/"&" used to
    force EVERY comma in the whole clause into a flat-list separator, bypassing the qualifier scan
    entirely -- so "Toronto, Ontario and Tallinn" split into THREE segments ("Toronto", "Ontario",
    "Tallinn") instead of recognizing "Ontario" as Toronto's qualifier. There was never a real
    reason for "and" to behave differently from a comma here: the qualifier scan already handles a
    bare comma list correctly ("Kaunas, Tallinn" stays two cities because neither is a qualifier),
    so the SAME scan, applied uniformly regardless of whether segments are joined by ","/";"/"and"/
    "&", handles both shapes with one rule -- a flat "and"-list merges nothing (no segment's
    follower is a qualifier) exactly as before, and a qualified pair followed by "and X" now
    correctly recognizes its own qualifier first. "/" still divides the clause into independent
    GROUPS ("Kaunas, Lithuania / Tallinn, Estonia"), each scanned on its own.

    Mnemosyne review repair, final mixed-intent round (2026-08-07, Blocker A): a weather clause's
    right edge used to be entirely `_WEATHER_LIST_BOUNDARY_RE`'s job (vocabulary-based, and
    explicitly NOT meant to grow into a list of every possible instruction phrase). Two more
    STRUCTURAL signals -- not word-count, not capitalization, not an incident-specific verb list --
    now stop the entity list from consuming a neighboring instruction clause within THIS function:

    - A semicolon ends the group's entity list outright, rather than acting as another list
      separator (which the pre-this-round code treated it as, though no required case ever
      exercised that). English uses ";" to join independent CLAUSES, not items in one list --
      nobody writes "weather for Kaunas; Tallinn; Warsaw" to ask for three cities. Once one is
      seen, the group's list is closed; anything after it is not scanned as a candidate at all.
    - `_CLAUSE_STRUCTURE_MARKERS` (see that set's own docstring) rejects a candidate OUTRIGHT --
      never trims it, per the ban on mutilating a span -- if it contains a closed-class function
      word (a WH-word, pronoun, auxiliary/modal verb, non-leading determiner, or sequence adverb)
      anywhere in it. "inspect the provider retry implementation" (a non-leading "the"), "then tell
      me which" (a "then"/"me"/"which"), "run the remaining work" (a "the") are all rejected this
      way; "Rio de Janeiro", "Ho Chi Minh City", and every other required multiword place name
      contain none of these words, at any position, so none of them are ever at risk.
    """
    groups = clause.split("/") if "/" in clause else [clause]
    sub_candidates: list[tuple[str, bool]] = []
    for group_index, group in enumerate(groups):
        group_is_last = group_index == len(groups) - 1
        # A semicolon closes this group's entity list outright -- see the docstring above.
        group = group.split(";", 1)[0]
        segments = [seg.strip() for seg in re.split(r",|\band\b|&", group) if seg.strip()]
        i, n = 0, len(segments)
        while i < n:
            if i + 1 < n and _is_geographic_qualifier(segments[i + 1]):
                # A matched qualifier is real evidence on its own, independent of position or of
                # whether "," or "and" joined the two segments.
                sub_candidates.append((f"{segments[i]}, {segments[i + 1]}", True))
                i += 2
            else:
                is_last_overall = group_is_last and i == n - 1
                sub_candidates.append((segments[i], not is_last_overall))
                i += 1
    candidates: list[tuple[str, bool]] = []
    for part, confident in sub_candidates:
        part = _LEADING_TIME_QUALIFIER_RE.sub("", part)
        tokens = [item for item in part.split() if item]
        # ANVIL closure (2026-08-07, B3): trailing noise and a trailing clock time can interleave
        # in either order -- "Paris at 14:30 today" puts the noise word AFTER the time, so a single
        # pass in a fixed order leaves whichever one is now last in place ("today" blocks the
        # `$`-anchored time rule; strip "today" first and the time is exposed). Alternate until the
        # tail stops changing, which is order-independent and terminates because every pass either
        # shortens the token list or ends the loop.
        while True:
            before = list(tokens)
            while tokens and (
                tokens[-1].lower() in _WEATHER_TRAILING_NOISE
                # "for the next 3 days" / "over the weekend" put an article or a bare count at the
                # tail between noise words. Neither ever ends a place name, and leaving one in
                # place jams the loop and discards the candidate whole.
                or tokens[-1].lower() == "the"
                or tokens[-1].isdigit()
            ):
                tokens.pop()
            # Removing the qualifier leaves its preposition behind ("... in vilnius for"), and a
            # dangling preposition is not part of a place name either.
            while tokens and tokens[-1].lower() in _WEATHER_DANGLING_PREPOSITIONS:
                tokens.pop()
            tokens = [item for item in _strip_time_qualifier(" ".join(tokens)).split() if item]
            if tokens == before:
                break
        while tokens and tokens[0].lower() in _WEATHER_LEADING_FILLER:
            tokens.pop(0)
        if tokens and tokens[0][:1].isdigit():
            # A digit-LED candidate ("24-hour" out of "..., 24-hour forecast needed...") is never
            # a place name -- a token shape signal, not a word-count or capitalization one: no
            # legitimate place name (lower-case or not) begins with a digit. Applies regardless of
            # `confident`, for the same reason `_CLAUSE_STRUCTURE_MARKERS` below does.
            continue
        if _has_clause_structure_marker(" ".join(tokens)):
            # Reject the WHOLE candidate as a place -- never trim it -- see
            # `_CLAUSE_STRUCTURE_MARKERS`'s and this function's own docstring. Checked
            # regardless of `confident`, deliberately: a phantom instruction fragment is
            # not made real by happening to be bounded by a delimiter on both sides
            # ("weather for Kaunas and summarize it and Tallinn" bounds "summarize it"
            # on both sides and it is still not a city), and a genuine multiword place
            # name never contains one of these words. ANVIL review closure (2026-08-07)
            # added a direct regression for that independence -- it was previously only
            # implied.
            #
            # Routed through `_has_clause_structure_marker` rather than matching raw tokens
            # in-line, so this and the legacy render reconstruction share ONE definition, and so
            # that punctuation-attached tokens ("it.") are matched -- the released build compared the
            # raw token "it." against the set and missed, which is what let LIVE FAILURE 1 through.
            #
            # ONE recovery, narrower than the rejection (M3B, measured live 2026-08-30): a
            # trailing QUESTION absorbed into the last list item -- "moscow which one is
            # warmr?", no sentence separator before the comparison ask -- still yields its
            # place head instead of silently dropping a requested city. The recovery fires
            # ONLY when the segment's first marker is a WH-word (see `_WH_MARKERS`): a
            # question is what a WH-word opens, and the instruction fragments the ANVIL
            # closures pin ("summarize it" and kin, pronoun/verb markers) keep the strict
            # whole-candidate rejection. The head is `confident=False` (its right edge was
            # closed by prose, so the runner's tighter bar applies) and runs the same
            # plausibility gates as every other candidate. This is structural evidence,
            # not the banned capitalization trim: the marker says the place ended.
            first_marker = _first_structure_marker(tokens)
            if first_marker is not None and first_marker[1] in _WH_MARKERS and first_marker[0] > 0:
                head = list(tokens[: first_marker[0]])
                # The same trailing-noise cleanup the non-rejected path applies: a
                # standoff dash before the question ("moscow — which...") rides the
                # head's tail and would keep the needle from ever binding a span.
                while head and head[-1].lower() in _WEATHER_TRAILING_NOISE:
                    head.pop()
                head_text = " ".join(head).strip()
                if head_text and not _has_clause_structure_marker(head_text):
                    candidates.append((head_text, False))
            continue
        # Mnemosyne review repair, final narrow round (2026-08-07): this used to trim an
        # unconfident (`tail_fallback`) multi-word candidate to its leading capitalized run, or to
        # its first token when nothing was capitalized at all -- proven wrong, live: "Rio de
        # Janeiro" (a real lowercase connector word, "de") truncated to "Rio"; "new york city" and
        # "ho chi minh city" typed all-lowercase (as users commonly do) truncated to "New"/"Ho" via
        # the first-token fallback. Capitalization is not proof a place name ended -- "Isle of
        # Man", "City of London", and "District of Columbia" all contain lowercase connector words,
        # and word count is not validation either ("Ho Chi Minh City" is 4 words and entirely
        # legitimate). Neither mechanism is used anymore, for candidates of ANY length: a
        # `tail_fallback` candidate is now kept exactly as extracted, in full, every time.
        candidate = " ".join(tokens).strip()
        if candidate:
            candidates.append((candidate, confident))
    return candidates


#: The clause shapes that tie a weather trigger word to what it is the weather OF. Named once and
#: shared by the singular and plural scanners below, so the two can never drift apart.
_WEATHER_CLAUSE_PATTERNS = (
    # "only"/"just"/"specifically" between the subject and the preposition -- mirrors
    # `_WEATHER_LIVE_REQUEST_RE` in `fast_live_info_mode_classifier.py`, which documents
    # the same production incident ("weather only for Berlin and Copenhagen" was measured to miss
    # the classifier entirely without this filler support).
    rf"\b(?:{_WEATHER_WORD_RE}|forecast|temperature|rain|snow|wind|humidity)\s+(?:only\s+|just\s+|specifically\s+|like\s+|looking\s+|going\s+to\s+be\s+)?(?:in|for|at)\s+(.+)$",
    r"\b(?:what is|what's|tell me|show me)\s+the\s+(?:weather|forecast)\s+(?:in|for|at)\s+(.+)$",
    # An INLINE colon label sharing its line with its list ("Weather: Kaunas/Tallinn/Warsaw."),
    # as distinct from `_WEATHER_HEADER_RE`'s bare-header-then-list-below shape. The trigger
    # word must be DIRECTLY followed by the colon (no words in between), so an unrelated
    # sentence like "For Weather include: - condition..." never matches here.
    r"\b(?:weather|forecast)\s*:\s*(.+)$",
    # The PREPOSITION-LESS subject-first form ("weather warsaw and moscow, which is warmer") --
    # M3B, measured live 2026-08-30: the operator's users type the ask without "in/for/at", and
    # both this extractor and the classifier's `_WEATHER_LIVE_REQUEST_RE` required the
    # preposition, so the turn fell to the model lane entirely. THREE structural gates keep it
    # from claiming prose that merely MENTIONS weather, all from closed classes the marker set
    # already uses (no noun vocabulary): the weather word may not be function-word-led -- a
    # determiner OR preposition before it ("the weather results", "one for weather (city,
    # condition...)" from an output-format instruction) marks a noun mention, not a request
    # subject; it may not be followed by one of the prepositions/temporal fillers the patterns
    # above bound better; and its tail may contain no colon (an "include:"-style label list).
    # Deliberately LAST: the preposition forms bound the same tail better when they apply, and
    # this form's capture is defended by the same candidate gates as any other clause.
    rf"(?<!the )(?<!a )(?<!an )(?<!for )(?<!of )(?<!in )(?<!on )(?<!at )(?<!with )(?<!about )"
    rf"(?<!from )(?<!by )(?<!to )"
    rf"\b(?:{_WEATHER_WORD_RE})\s+(?!(?:in|for|at|like|around|over|today|tomorrow|tonight|now|currently|only|just|specifically|is|are|was|were|be|been|being|has|have|had|does|did|will|would|can|could|should|must|shall|may|might)\b)(?=[^:]*$)(?=(?:[^:\n]*\b(?:and|,|&)\b[^:\n]*)$)(.+)$",
)


def _weather_clause_on_line(line: str) -> str | None:
    """The location clause on ONE LINE, or None -- bounded to this line by construction (there is
    no later line for `.+$` to reach into, so the regexes below can never leak past a paragraph or
    even a line break the way the pre-fix whole-text version could).

    Requires an EXPLICIT governing preposition ("weather in X"/"weather for X"/"weather at X")
    tying the trigger word to what follows -- a bare mention of "weather" elsewhere on the line,
    with no preposition naming what it's the weather OF, is not a location clause. A prior version
    of this function fell back to "blank the trigger word out, treat the rest of the line as the
    clause" whenever a trigger word was merely present anywhere on the line; that fallback is what
    turned "...one for weather (city, condition, today's high and low, source)... the warmest city
    among the weather results" -- prose that only MENTIONS weather, on the SAME line as the output-
    format instructions, because the whole paragraph was typed as one line -- into bogus location
    candidates ("24-Hour Change", "Today'S High", "Warmest City Among The Results"). No test in the
    suite requires that fallback: every documented case already has an explicit preposition, or is
    served by the separate header-block path (a bare "Weather:" section header) below.

    Mnemosyne review repair, round 3 (2026-08-07): matches case-insensitively against the line
    directly (`re.IGNORECASE`) rather than pre-lowering it, so the captured clause keeps its
    ORIGINAL casing -- `_split_weather_candidates`'s trailing-prose trim depends on capitalization
    surviving this far to tell "Tallinn" (a place) from "live-data" (not one).
    """
    line = _fold_weather_words(line)
    for pattern in _WEATHER_CLAUSE_PATTERNS:
        match = re.search(pattern, line, re.IGNORECASE)
        if match:
            clause = match.group(1)
            boundary = _WEATHER_LIST_BOUNDARY_RE.search(clause)
            if boundary:
                clause = clause[: boundary.start()]
            opener = _next_request_opener_re().search(clause)
            if opener:
                # The clause also ends where the NEXT request opens: "also ...", "then ...", or
                # "and <demand head> ..." ("weather in Bergen and convert 30 NOK to SEK"). Measured
                # 2026-09-07: without this the conductor's obligation floor named "Much Gold I Get"
                # and "Also Change 250 Eur To Gbp" as places. The head list is the demand grain's own,
                # so the two readers of a request cannot disagree about where one ends.
                clause = clause[: opener.start()]
            return _trim_trailing_filler(clause).strip()
    return None


#: The words a weather clause is recognized by. A token one closed typo away from exactly one of them
#: is read as that word before the clause patterns run (`core.typo_fold`, the demand grain's budget):
#: measured 2026-09-07, "temprature in Oslo pls" named no clause and the Oslo part of a three-part turn
#: went unanswered. "whether" is not one edit away from "weather" and stays what it is.
_WEATHER_VOCABULARY = frozenset({"weather", "temperature", "forecast", "humidity", "rain", "snow", "wind"})
_TRAILING_FILLER = frozenset({"please", "pls", "plz", "thanks", "thank", "thankyou", "thx", "ty", "cheers"})


def _fold_weather_words(line: str) -> str:
    from core.typo_fold import fold_near_miss_tokens

    return fold_near_miss_tokens(str(line or ""), _WEATHER_VOCABULARY)


def _trim_trailing_filler(clause: str) -> str:
    """"Oslo pls" is Oslo: trailing politeness is not part of a place name."""
    words = str(clause or "").rstrip(" ,;:!?").split()
    while words and words[-1].strip(",.;:!?").lower() in _TRAILING_FILLER:
        words.pop()
    text = " ".join(words)
    # A sentence-final period closes a word ("what's the weather."), so it is trailing prose
    # and goes. A period closing a SINGLE UPPERCASE LETTER is the tail of a dotted
    # abbreviation ("Washington, D.C.", "the U.S."), and stripping it renames the place the
    # candidate was extracted for -- measured by the mnemosyne dotted-location pin, which
    # watched "Washington, D.C." reach the lookup lane as "Washington, D.C".
    if text.endswith(".") and not re.search(r"\b[A-Z]\.$", text):
        text = text[:-1].rstrip(" ,;:!?")
    return text


_NEXT_REQUEST_OPENER_RE: re.Pattern[str] | None = None


def _next_request_opener_re() -> re.Pattern[str]:
    """`also ...` / `then ...` / `and <demand head> ...` inside a weather clause -- compiled once, from
    the demand grain's own head list (a lazy import: this module is imported by the grain's callers)."""
    global _NEXT_REQUEST_OPENER_RE
    if _NEXT_REQUEST_OPENER_RE is None:
        from core.agent_runtime.answer_coverage import _DEMAND_HEADS

        heads = "|".join(sorted((re.escape(h) for h in _DEMAND_HEADS), key=len, reverse=True))
        _NEXT_REQUEST_OPENER_RE = re.compile(
            rf"\b(?:also|then)\b|\b(?:and|plus)\s+(?:{heads})\b",
            re.IGNORECASE,
        )
    return _NEXT_REQUEST_OPENER_RE


#: The most clauses one line may contribute. A bound, not a policy: it stops a pathological line
#: from producing unbounded subtasks, and is far above any real request.
_MAX_WEATHER_CLAUSES_PER_LINE = 8


def _weather_clauses_on_line(line: str) -> list[str]:
    """EVERY location clause on one line, in order -- not just the first.

    A line can govern more than once. "the weather in Paris and the weather in Berlin" has two
    prepositions and two places, and the singular form above returns only the first: measured, it
    yields the clause "Paris and the" (the trailing-prose trim doing its job correctly on a clause
    that already lost its second half) and Berlin is never seen. The turn then answers Paris and
    reports "1/1 succeeded" -- truthful accounting over incomplete planning, with nothing anywhere
    saying a request was dropped.

    `finditer` cannot do this: every pattern captures `(.+)$`, so the first match consumes the rest
    of the line and there is nothing left to iterate over. The scan therefore advances past each
    TRIMMED clause and searches the remainder, which is exactly the text the trim excluded.

    A shared-verb list ("weather in Kaunas, Tallinn and Warsaw") is unaffected: it is one clause,
    and `_split_weather_candidates` continues to split it.
    """

    clauses: list[str] = []
    offset = 0
    line = _fold_weather_words(line)
    while offset < len(line) and len(clauses) < _MAX_WEATHER_CLAUSES_PER_LINE:
        remainder = line[offset:]
        earliest = None
        for pattern in _WEATHER_CLAUSE_PATTERNS:
            match = re.search(pattern, remainder, re.IGNORECASE)
            if match is not None and (earliest is None or match.start(1) < earliest.start(1)):
                earliest = match
        if earliest is None:
            break
        clause = earliest.group(1)
        boundary = _WEATHER_LIST_BOUNDARY_RE.search(clause)
        if boundary:
            clause = clause[: boundary.start()]
        opener = _next_request_opener_re().search(clause)
        if opener:
            clause = clause[: opener.start()]
        clause = _trim_trailing_filler(clause).strip()
        if clause:
            clauses.append(clause)
        # Advance past the captured text this clause consumed, so the next pass sees only what the
        # trim left behind. `max(1, ...)` guarantees forward progress on an empty capture.
        offset += earliest.start(1) + max(1, len(clause))
    return clauses


def _extract_weather_locations_with_confidence(query: str) -> list[tuple[str, bool]]:
    """Every place name a weather request names, in the order given, each paired with whether it
    was CONFIDENTLY bounded -- see `_split_weather_candidates`'s docstring for what that means and
    why. `build_live_data_plan` uses this to stamp each weather subtask's provenance (see
    `_run_weather_subtask`, which gives an unconfident candidate a much tighter bar before ever
    reaching the network). `_extract_weather_locations` below is the stable, unchanged public
    shape every other caller already depends on; it is a thin wrapper over this.

    Bounded to the paragraph (blank-line-separated block) and line that actually names it, never
    the whole prompt -- see the module-level production-incident history in `_iter_prompt_blocks`
    and `_weather_clause_on_line` for why that boundary exists at all.

    `_extract_weather_location` (singular) strips commas before matching because it only ever
    needed one place; reusing it here would destroy exactly the boundary this function exists to
    find. Kept as a separate function rather than a refactor of that one, so its existing tested
    behaviour is untouched.
    """
    # A temporal qualifier standing between the weather word and the place ("weather tomorrow in
    # Vilnius") breaks the clause patterns below before any candidate is ever formed, so it is
    # removed once here rather than inside each path. Anchored on the following preposition, so a
    # place name that merely contains a time word is untouched.
    query = _LEADING_TIME_QUALIFIER_RE.sub("", str(query or ""))
    locations: list[tuple[str, bool]] = []
    seen: set[str] = set()

    def _add(candidate: str, confident: bool) -> None:
        # Lowercased at this single collection point regardless of which path found it -- the
        # header-block path hands back lines in their original source casing (`_iter_prompt_blocks`
        # does not lower-case), while the inline-clause path now ALSO preserves original casing
        # (needed for `_split_weather_candidates`'s capitalization-based trailing-prose trim).
        # Downstream resolution (`_resolve_price_alias`-style lookups, dict keys) is case-sensitive,
        # so both paths must agree on case before dedup and before the caller ever sees the
        # candidate.
        normalized = candidate.strip().lower()
        if normalized and normalized not in seen and _is_plausible_weather_location(normalized):
            seen.add(normalized)
            locations.append((normalized, confident))

    for block in _iter_prompt_blocks(query):
        if _WEATHER_HEADER_RE.match(block[0]):
            # A bare "Weather"/"2. Weather:" header -- every following line in THIS SAME
            # paragraph (already blank-line-bounded) is a list item, stopping early only if
            # another section header appears before the paragraph's own end. Always confident:
            # each item is its OWN real newline-bounded line, so there is no unbounded prose tail
            # to worry about the way an inline clause has -- the confidence flag
            # `_split_weather_candidates` computes for a header-block line's OWN internal commas
            # is not about that risk, so it is not consulted here.
            for line in block[1:]:
                if _NEW_SECTION_HEADER_RE.match(line):
                    break
                item = _strip_list_marker(line)
                for candidate, _confident in _split_weather_candidates(item):
                    _add(candidate, True)
            continue
        for line in block:
            # EVERY clause on the line: a line can govern more than once ("the weather in Paris and
            # the weather in Berlin"), and taking only the first silently drops the rest.
            for clause in _weather_clauses_on_line(line):
                for candidate, confident in _split_weather_candidates(clause):
                    _add(candidate, confident)
    return locations


def _extract_weather_locations(query: str) -> list[str]:
    """Every place name a weather request names, in the order given -- see
    `_extract_weather_locations_with_confidence` for the full contract and the incident history
    behind the paragraph/line boundary this respects. Kept as the stable `list[str]` shape every
    existing caller depends on.
    """
    return [location for location, _confident in _extract_weather_locations_with_confidence(query)]


_MARKET_LIST_CUE_RE = re.compile(r"\b(?:" + _PRICE_CUE_FRAGMENT + r")\s+(.+)$", re.IGNORECASE)

# Where a market entity list ends and something else (most often a weather clause in a mixed
# request) begins.
_MARKET_LIST_BOUNDARY_RE = re.compile(r"\b(?:plus|and\s+also|,?\s*(?:the\s+)?weather)\b", re.IGNORECASE)


_MARKET_HEADER_RE = re.compile(r"^(?:\d+[\.\)]\s*)?markets?\s*:?\s*$", re.IGNORECASE)
_MARKET_TRAILING_NOISE = {"today", "now", "current", "currently", "please", "right"}
_MARKET_LEADING_FILLER = {"the", "a", "an", "and", "please", "also", "only", "for", "of", "on"}


def _split_market_candidates(clause: str) -> list[str]:
    """The existing, unchanged delimiter/shape logic for a single ALREADY-BOUNDED market clause --
    see `_split_weather_candidates`'s docstring for why splitting itself never decides how much of
    the prompt a clause may span."""
    clause = clause.strip(" .,")
    if not clause:
        return []
    has_join_word = bool(re.search(r"\band\b|&|/", clause))
    parts = re.split(r",|\band\b|&|/", clause) if has_join_word else [clause]
    candidates: list[str] = []
    for part in parts:
        tokens = [item for item in part.split() if item]
        while tokens and tokens[-1] in _MARKET_TRAILING_NOISE:
            tokens.pop()
        while tokens and tokens[0] in _MARKET_LEADING_FILLER:
            tokens.pop(0)
        candidate = " ".join(tokens).strip()
        bare = candidate.replace(" ", "")
        # Alphanumeric, not alpha-only: a made-up or emerging ticker very often has digits in it
        # ("0x", "1inch", or a placeholder like "Qwertycoin999xyz") -- an isalpha()-only check
        # would silently drop exactly the class of unresolved entity this function exists to keep.
        # `any(isalpha)` still rejects pure-numeric junk ("2024", "100").
        if candidate and 1 <= len(tokens) <= 3 and bare.isalnum() and any(ch.isalpha() for ch in bare):
            candidates.append(candidate)
    return candidates


_MARKET_INLINE_LABEL_RE = re.compile(r"\bmarkets?\s*:\s*(.+)$", re.IGNORECASE)


def _market_clause_on_line(line: str) -> str | None:
    """The asset-list clause on ONE LINE, or None -- bounded to this line by construction, same
    reasoning as `_weather_clause_on_line`. `_MARKET_LIST_BOUNDARY_RE` (stop at "plus"/"and
    also"/"weather") still applies, now scoped to content that was already only ever this one
    line's worth in the first place."""
    lowered = line.lower()
    match = _MARKET_LIST_CUE_RE.search(lowered)
    if not match:
        # An INLINE "Market(s): gold/silver/Bitcoin." label sharing its line with its list, as
        # distinct from `_MARKET_HEADER_RE`'s bare-header-then-list-below shape -- mirrors the
        # equivalent weather-side fallback in `_weather_clause_on_line`.
        match = _MARKET_INLINE_LABEL_RE.search(lowered)
        if not match:
            return None
    clause = match.group(1)
    boundary = _MARKET_LIST_BOUNDARY_RE.search(clause)
    if boundary:
        clause = clause[: boundary.start()]
    return clause


def _extract_market_entity_candidates(text: str) -> list[str]:
    """Every asset-shaped token named in a market-price clause, in order, whether or not it
    resolves to a known target -- bounded to the paragraph and line that actually names it, never
    the whole prompt (see `_extract_weather_locations`'s docstring for the production incident
    this mirrors on the weather side of the same benchmark, and why unbounded-to-end-of-string
    regex matching is unsafe for any realistically multi-paragraph prompt).

    Mirrors `_extract_weather_locations`'s approach: find the list clause (after a price-context
    cue like "price for"/"market data for"), split on commas/"and" the same way a weather clause
    is split, then hand back every part -- resolved or not. The caller
    (`core.agent_runtime.live_data_plan.build_live_data_plan`) is what decides an unresolved part
    becomes an explicit UNSUPPORTED_ENTITY subtask rather than silently vanishing; this function's
    only job is to not lose the word in the first place.

    Deliberately conservative: requires an explicit price-context cue phrase before the list, and
    each candidate must be 1-3 alphabetic words -- a message with no such cue (or an asset list
    with no recognizable price-language lead-in) returns nothing, same fail-safe direction as
    `_extract_weather_locations` returning "" for garbage.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(candidate: str) -> None:
        # Same case-normalization reasoning as `_extract_weather_locations._add` -- the header-block
        # path preserves source casing while the inline-clause path already lower-cases, and
        # `_resolve_price_alias` needs a lower-cased key from either path to resolve correctly.
        normalized = candidate.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    for block in _iter_prompt_blocks(text):
        if _MARKET_HEADER_RE.match(block[0]):
            for line in block[1:]:
                if _NEW_SECTION_HEADER_RE.match(line):
                    break
                item = _strip_list_marker(line)
                for candidate in _split_market_candidates(item):
                    _add(candidate)
            continue
        for line in block:
            clause = _market_clause_on_line(line)
            if clause is None:
                continue
            for candidate in _split_market_candidates(clause):
                _add(candidate)
    return candidates


def _extract_news_topic(query: str) -> str:
    clean = re.sub(r"\s+", " ", str(query or "").strip()).strip(" ?!.,")
    lowered = clean.lower()
    for prefix in (
        "what's the latest on ",
        "what is the latest on ",
        "whats the latest on ",
        "latest news on ",
        "latest news about ",
        "breaking news on ",
        "breaking news about ",
        "latest news ",
        "breaking news ",
        "latest on ",
        "latest about ",
        "news on ",
        "news about ",
        "headlines on ",
        "headlines about ",
        "latest ",
    ):
        if lowered.startswith(prefix):
            return clean[len(prefix):].strip(" ?!.,") or clean
    return clean


def _parse_pub_datetime(raw_value: str) -> datetime | None:
    """Parse an RSS pubDate into an aware UTC datetime, or None when absent/unparseable."""
    text = str(raw_value or "").strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except Exception:
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _price_query_fully_resolves(query: str) -> bool:
    """True when the specialized crypto path can answer EVERY asset this query names.

    The specialized shortcut is all-or-nothing on purpose. Before this check it claimed any query
    containing one recognized alias and then answered only that alias, suppressing the general
    search that would have found the rest -- so on 2026-08-04 "what is the price of ARB and LTC?"
    returned a single Litecoin quote in 0.3s and ARB vanished with nothing downstream ever seeing
    its name. Measured, that made the two-asset question strictly worse than either asset alone:
    "ARB price" on its own reaches the general search and returns five exchange results in 3.0s.

    So: resolve everything, or hand the whole turn to general search. Answering the subset is the
    one outcome that is never acceptable, because a partial answer reads as a complete one.
    """
    # Lazy import -- core.agent_runtime's package __init__ eagerly imports a chain that lands back
    # in this module, so a module-level import here would be circular. Same reasoning as
    # `_weather_keywords` above.
    from core.agent_runtime.fast_live_info_price import price_request_leaves_unresolved_content

    return not price_request_leaves_unresolved_content(query)


def _prefer_specialized_live_research(query: str) -> bool:
    return bool(
        _looks_like_weather_query(query)
        or _looks_like_news_query(query)
        or (_looks_like_price_query(query) and _price_query_fully_resolves(query))
        or _looks_like_market_quote_query(query) is not None
    )


def _compact_pub_date(raw_value: str) -> str:
    text = str(raw_value or "").strip()
    if not text:
        return ""
    try:
        parsed = parsedate_to_datetime(text)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.strftime("%Y-%m-%d")
    except Exception:
        return text


def _prebuilt_page_for_hit(pages: list[PageEvidence], hit: WebHit) -> PageEvidence | None:
    for page in list(pages or []):
        if page.url == hit.url or page.final_url == hit.url:
            return page
    return None


_CRYPTO_ALIASES: dict[str, str] = {
    "bitcoin": "bitcoin", "btc": "bitcoin",
    "ethereum": "ethereum", "eth": "ethereum",
    "solana": "solana", "sol": "solana",
    "cardano": "cardano", "ada": "cardano",
    "polkadot": "polkadot", "dot": "polkadot",
    "dogecoin": "dogecoin", "doge": "dogecoin",
    "ripple": "ripple", "xrp": "ripple",
    "litecoin": "litecoin", "ltc": "litecoin",
    "avalanche": "avalanche-2", "avax": "avalanche-2",
    "chainlink": "chainlink", "link": "chainlink",
    "matic": "matic-network", "polygon": "matic-network",
    "bnb": "binancecoin", "binance": "binancecoin",
}

# SENTINEL G4 repair, 2026-08-06: this tuple used to be tested with plain `in` containment, which
# is why "Not price — I mean the chemical structure of gold." wanted a quote (the NEGATED word
# `price` is still a substring) and why "Do you know ..." wanted one too (`now` is a substring of
# `know`). The vocabulary itself is unchanged and now lives in `core.market_intent`, which every
# market recognizer in this file reads through `market_quote_intent_present` — see that module for
# why one authority replaced four hand-rolled copies of this test. Re-exported under the old name
# because this file's own comments and `live_data_plan.py`'s reference it by name.
_PRICE_KEYWORDS = MARKET_TERMS


@dataclass(frozen=True)
class MarketQuoteTarget:
    asset_key: str
    asset_name: str
    symbol: str
    unit_label: str
    aliases: tuple[str, ...]


_MARKET_QUOTE_TARGETS: tuple[MarketQuoteTarget, ...] = (
    MarketQuoteTarget(
        asset_key="brent_crude",
        asset_name="Brent crude",
        symbol="BZ=F",
        unit_label="per barrel",
        # Bare "oil" lands here on purpose: it is the benchmark an unqualified
        # "oil price" means, and leaving it unmapped silently dropped the third
        # asset of "price of gold, silver n oil" (measured live 2026-08-29).
        # Bare "crude" reads the same way as bare "oil" ("how many gallons of crude would 0.2 BTC
        # fetch?" resolved no asset at all, measured 2026-09-07).
        aliases=("oil", "crude", "crude oil", "brent crude oil", "brent crude", "brent oil", "brent"),
    ),
    MarketQuoteTarget(
        asset_key="wti_crude",
        asset_name="WTI crude",
        symbol="CL=F",
        unit_label="per barrel",
        aliases=("wti crude oil", "wti crude", "wti oil", "wti"),
    ),
    MarketQuoteTarget(
        asset_key="gold",
        asset_name="Gold",
        symbol="GC=F",
        unit_label="per troy ounce",
        aliases=("gold spot", "gold price", "gold", "xau"),
    ),
    MarketQuoteTarget(
        asset_key="silver",
        asset_name="Silver",
        symbol="SI=F",
        unit_label="per troy ounce",
        aliases=("silver spot", "silver price", "silver", "xag"),
    ),
)


def _looks_like_price_query(query: str) -> str:
    """Return CoinGecko coin ID if query asks for a crypto price, else ''.

    Delegates to `_looks_like_price_query_all` and takes the first result rather than repeating the
    alias walk. This module's own history is the argument: "Four readings of one question is how
    they came to disagree" (`core.market_intent`), and this function was the fourth reading -- it
    kept a turn-global `market_quote_intent_present` gate plus an unbound alias scan after
    `_looks_like_price_query_all` had moved to per-mention authority, so "rate this restaurant near
    me" and "click the LINK to see pricing" still admitted the market lane through this door alone.
    One reading, one authority (`core.semantic_claim_authority`).

    Also fixes a defect this function had on its own terms: it returned whichever alias came first
    in `_CRYPTO_ALIASES` declaration order, not the one the user named first.
    """
    resolved = _looks_like_price_query_all(query)
    return resolved[0] if resolved else ""


def _looks_like_price_query_all(query: str) -> list[str]:
    """Every distinct CoinGecko coin ID a price-shaped query names, in the order first mentioned.

    `_looks_like_price_query` above returns exactly one id -- whichever alias happens to come
    first in `_CRYPTO_ALIASES`'s declaration order, not the order the user actually named them in
    (this is why "bnb and sol price?" resolved to solana: `sol` is declared earlier in the dict
    than `bnb`, regardless of word order in the query). `lookup_live_quote` then only ever fetches
    that one id, so any other named asset was never even looked up -- not dropped after the fact,
    never requested. This collects every match so a multi-asset query can fetch all of them.

    Longest-alias-first with span-claiming mirrors `price_assets_named` in fast_live_info_price.py
    (`bitcoin` must not also let `btc` claim the same span); deduped by resulting coin id so
    "bitcoin and btc" -- same coin, two aliases -- is not fetched twice.
    """
    lowered = str(query or "").lower()
    # AUTHORITY, 2026-08-16: the gate here used to be `market_quote_intent_present(lowered)` alone --
    # one TURN-GLOBAL boolean, after which every alias and every live-index token ANYWHERE in the
    # message was resolved. Both operands were turn-global and nothing bound them to each other, so
    # a request whose market vocabulary belonged to something else donated its domain to an
    # unrelated token: "three highly-rated, mid-priced dinner restaurants near me" answered
    # "Near: USD 1.62 ... Source: CoinGecko" (human test, 2fae1489).
    #
    # Authority is now asked PER MENTION -- see `core.semantic_claim_authority`. The turn-global
    # question is not asked at all: it is subsumed, because a turn with no authorized mention has
    # nothing to resolve, and asking it first would also refuse `$NEAR`, which self-authorizes with
    # no supporting market word.
    from core.semantic_claim_authority import mention_is_market_authorized

    hits: list[tuple[int, str]] = []
    claimed: list[tuple[int, int]] = []
    seen_ids: set[str] = set()
    for alias in sorted(_CRYPTO_ALIASES, key=len, reverse=True):
        cg_id = _CRYPTO_ALIASES[alias]
        for match in re.finditer(rf"\b{re.escape(alias)}\b", lowered):
            start, end = match.span()
            if any(start >= s and end <= e for s, e in claimed):
                continue
            claimed.append((start, end))
            if not mention_is_market_authorized(lowered, start, end, curated=True):
                continue
            if cg_id in seen_ids:
                continue
            seen_ids.add(cg_id)
            hits.append((start, cg_id))
    # Anything `_CRYPTO_ALIASES` does not know is resolved against CoinGecko's live rank-ordered
    # index instead of being dropped -- the dict above is a fast offline shortcut, not the list of
    # coins this runtime supports. "ARB and LTC price" answered only LTC before this, because `arb`
    # was simply not a key. Extending the dict per missing ticker is the Section 0.2 pattern; see
    # tools/web/coin_index.py for why the index is bounded by market-cap rank instead.
    for position, coin_id in coin_index.resolve_tokens(lowered, skip_spans=claimed):
        # The live index is exactly where the ordinary-word collisions live -- it really resolves
        # near, rain, atom, real, hash and meta (measured against the live index). A token it
        # returns is a CANDIDATE; whether this request authorizes reading it as an asset is the
        # authority's call, not the index's.
        token = re.match(r"[a-z0-9]+", lowered[position:])
        end = position + len(token.group(0)) if token else position
        if not mention_is_market_authorized(lowered, position, end, curated=False):
            continue
        if coin_id in seen_ids:
            continue
        seen_ids.add(coin_id)
        hits.append((position, coin_id))
    return [cg_id for _position, cg_id in sorted(hits)]


def _looks_like_market_quote_query(query: str) -> MarketQuoteTarget | None:
    lowered = " ".join(str(query or "").strip().lower().split())
    if not lowered:
        return None
    if not market_quote_intent_present(lowered):
        return None
    for target in _MARKET_QUOTE_TARGETS:
        for alias in target.aliases:
            if re.search(rf"\b{re.escape(alias)}\b", lowered):
                return target
    return None


def _looks_like_market_quote_query_all(query: str) -> list[MarketQuoteTarget]:
    """Every distinct commodity target a price-shaped query names, in the order first mentioned.

    `_looks_like_market_quote_query` above returns the first target that matches, in
    `_MARKET_QUOTE_TARGETS` declaration order -- so "gold, silver, bitcoin and bnb" only ever
    resolved gold, and silver was never even looked up. Mirrors `_looks_like_price_query_all`'s
    longest-alias-first, span-claiming approach so overlapping aliases ("gold" vs "gold spot")
    cannot double-count the same mention as two targets.
    """
    lowered = " ".join(str(query or "").strip().lower().split())
    if not lowered:
        return []
    if not market_quote_intent_present(lowered):
        return []
    all_aliases = sorted(
        ((alias, target) for target in _MARKET_QUOTE_TARGETS for alias in target.aliases),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    hits: list[tuple[int, MarketQuoteTarget]] = []
    claimed: list[tuple[int, int]] = []
    seen_keys: set[str] = set()
    # Same per-mention authority as the crypto lane. A commodity alias is an ordinary word far more
    # often than a ticker is ("gold standard", "silver lining", "the oil in the pan"), so the seam
    # that decides a mention is an ASSET has to be the same one for both, or the two disagree and
    # the looser wins. See `core.semantic_claim_authority`.
    from core.semantic_claim_authority import mention_is_market_authorized

    for alias, target in all_aliases:
        for match in re.finditer(rf"\b{re.escape(alias)}\b", lowered):
            start, end = match.span()
            if any(start >= s and end <= e for s, e in claimed):
                continue
            claimed.append((start, end))
            if not mention_is_market_authorized(lowered, start, end, curated=True):
                continue
            if target.asset_key in seen_keys:
                continue
            seen_keys.add(target.asset_key)
            hits.append((start, target))
    return [target for _position, target in sorted(hits, key=lambda item: item[0])]


# CoinGecko's keyless tier rate-limits aggressively, and the whole price lane funnels through one
# endpoint. Measured 2026-08-04: a short burst of price questions drove `simple/price` to a hard
# `HTTP 429` while the same host still served other endpoints fine -- and because both callers wrap
# the fetch in `except Exception`, the 429 became an empty result, then a 22-34s fallthrough into
# the model lane, then "I couldn't ground a confident answer" for a coin the runtime answers in
# 0.3s when the call succeeds. A silent rate limit reads exactly like a broken feature.
#
# 45s is chosen against the failure, not the freshness: it collapses the realistic repeat pattern
# (asking again, rephrasing, asking about an overlapping pair) into one call, while a quote can
# never be presented as newer than it is -- `as_of`/`timestamp_utc` come from CoinGecko's own
# `last_updated_at`, so a cached answer states the observation time it actually has.
_QUOTE_CACHE_TTL_S = 45.0
_quote_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_quote_cache_lock = threading.Lock()


def reset_quote_cache_for_test() -> None:
    with _quote_cache_lock:
        _quote_cache.clear()


def _simple_price_payload(coin_ids: list[str], *, timeout_s: float) -> dict[str, Any]:
    """CoinGecko `simple/price` for `coin_ids`, serving anything cached within the TTL.

    Only the ids that are actually missing or stale are requested, so a two-asset question where
    one asset was just asked about costs one id on the wire, not two.
    """
    wanted = list(dict.fromkeys(str(coin_id) for coin_id in coin_ids if str(coin_id).strip()))
    if not wanted:
        return {}
    now = time.monotonic()
    payload: dict[str, Any] = {}
    missing: list[str] = []
    with _quote_cache_lock:
        for coin_id in wanted:
            entry = _quote_cache.get(coin_id)
            if entry is not None and (now - entry[0]) <= _QUOTE_CACHE_TTL_S:
                payload[coin_id] = entry[1]
            else:
                missing.append(coin_id)
    if not missing:
        return payload
    api_url = (
        f"https://api.coingecko.com/api/v3/simple/price"
        f"?ids={urllib.parse.quote(','.join(missing))}"
        f"&vs_currencies=usd,eur,btc"
        f"&include_24hr_change=true&include_market_cap=true&include_last_updated_at=true"
    )
    req = urllib.request.Request(api_url, headers={"User-Agent": "VOOL-PRICE/1.0"})
    with _open_remote(req, timeout=min(max(timeout_s, 3.0), 12.0)) as resp:
        fetched = json.loads(resp.read(200000).decode("utf-8", errors="ignore"))
    if not isinstance(fetched, dict):
        return payload
    stamped = time.monotonic()
    with _quote_cache_lock:
        for coin_id, data in fetched.items():
            if isinstance(data, dict) and data:
                _quote_cache[str(coin_id)] = (stamped, data)
    payload.update({str(key): value for key, value in fetched.items()})
    return payload


def _crypto_price_fallback(
    query: str,
    coin_id: str,
    *,
    timeout_s: float,
) -> tuple[str, list[WebHit], list[PageEvidence], list[str]] | None:
    payload = _simple_price_payload([coin_id], timeout_s=timeout_s)

    data = payload.get(coin_id)
    if not data:
        return None
    usd = data.get("usd")
    if usd is None:
        return None
    eur = data.get("eur", "?")
    change_24h = data.get("usd_24h_change")
    mcap = data.get("usd_market_cap")
    last_updated_at = data.get("last_updated_at")
    name = coin_id.replace("-", " ").title()
    cg_url = f"https://www.coingecko.com/en/coins/{coin_id}"
    quote = LiveQuoteResult(
        asset_key=coin_id,
        asset_name=name,
        symbol=coin_id.replace("-", "_").upper(),
        value=float(usd),
        currency="USD",
        as_of=format_quote_timestamp(last_updated_at),
        source_label="CoinGecko",
        source_url=cg_url,
        kind="crypto",
        change_percent=None if change_24h is None else float(change_24h),
        change_window="24h",
        market_cap=None if not mcap else float(mcap),
        timestamp_utc=None if last_updated_at in {None, ""} else int(float(last_updated_at)),
    )
    summary = quote.summary_text()
    if eur != "?":
        summary = f"{summary} | EUR: {eur:,.2f}"
    hit = WebHit(title=f"{name} quote", url=cg_url, snippet=summary, engine="coingecko_api", score=None)
    page = PageEvidence(url=cg_url, final_url=cg_url, status="ok", title=hit.title, text=summary)
    return ("coingecko_api", [hit], [page], [f"live_price_fallback:coingecko_api:{coin_id}"])


def _crypto_price_fallback_multi(
    coin_ids: list[str],
    *,
    timeout_s: float,
) -> list[LiveQuoteResult]:
    """Fetch every named coin in ONE CoinGecko call.

    CoinGecko's `simple/price` endpoint already accepts a comma-separated `ids` list and returns
    one entry per id -- `_crypto_price_fallback` above just never passed more than one. Built
    directly from the API payload rather than round-tripping through a rendered summary string
    (what `lookup_live_quote` does for its single result), since there is no reason to re-parse a
    float out of text this function itself just formatted.
    """
    if not [coin_id for coin_id in coin_ids if str(coin_id).strip()]:
        return []
    payload = _simple_price_payload(list(coin_ids), timeout_s=timeout_s)

    results: list[LiveQuoteResult] = []
    for coin_id in coin_ids:
        data = payload.get(coin_id)
        if not data:
            continue
        usd = data.get("usd")
        if usd is None:
            continue
        change_24h = data.get("usd_24h_change")
        mcap = data.get("usd_market_cap")
        last_updated_at = data.get("last_updated_at")
        results.append(
            LiveQuoteResult(
                asset_key=coin_id,
                asset_name=coin_id.replace("-", " ").title(),
                symbol=coin_id.replace("-", "_").upper(),
                value=float(usd),
                currency="USD",
                as_of=format_quote_timestamp(last_updated_at),
                source_label="CoinGecko",
                source_url=f"https://www.coingecko.com/en/coins/{coin_id}",
                kind="crypto",
                change_percent=None if change_24h is None else float(change_24h),
                change_window="24h",
                market_cap=None if not mcap else float(mcap),
                timestamp_utc=None if last_updated_at in {None, ""} else int(float(last_updated_at)),
            )
        )
    return results


def _crypto_price_hits_multi(
    coin_ids: list[str],
    *,
    timeout_s: float,
) -> tuple[str, list[WebHit], list[PageEvidence], list[str]] | None:
    """`_crypto_price_fallback`'s hit/page shape for several coins at once.

    `_specialized_live_research` needs the four-tuple the general research pipeline consumes, while
    `_crypto_price_fallback_multi` returns quote objects; this adapts one to the other so a
    multi-asset question produces one hit per asset instead of collapsing to the first.
    """
    quotes = _crypto_price_fallback_multi(coin_ids, timeout_s=timeout_s)
    if not quotes:
        return None
    hits: list[WebHit] = []
    pages: list[PageEvidence] = []
    for quote in quotes:
        summary = quote.summary_text()
        title = f"{quote.asset_name} quote"
        hits.append(WebHit(title=title, url=quote.source_url, snippet=summary, engine="coingecko_api", score=None))
        pages.append(
            PageEvidence(url=quote.source_url, final_url=quote.source_url, status="ok", title=title, text=summary)
        )
    notes = [f"live_price_fallback:coingecko_api:{quote.asset_key}" for quote in quotes]
    return ("coingecko_api", hits, pages, notes)


def lookup_live_quotes(query: str, *, timeout_s: float = 8.0) -> list[LiveQuoteResult]:
    """Every live quote a query names, not just the first.

    Sibling to `lookup_live_quote` below, which this does not replace -- callers that only ever
    expected one result (the general specialized-search fallback) are untouched. This exists for
    callers that must answer every named asset: a price-shaped message naming two or more
    recognized assets ("bnb and sol price?") gets a quote for each, not a silent collapse to one.

    Crypto and commodities are two different tools (CoinGecko vs. Yahoo Finance) and this used to
    return as soon as it found ANY crypto id, never checking commodities at all -- "gold, silver,
    bitcoin and bnb price" answered only bitcoin and bnb, and gold/silver were never even looked
    up. Both are checked and their results unioned, deduped by `asset_key`, so a query naming both
    kinds gets both. Falls back to the single-result path only when NEITHER multi-path finds
    anything, so a plain single-asset query behaves exactly as before.
    """
    results: list[LiveQuoteResult] = []
    seen_keys: set[str] = set()

    coin_ids = _looks_like_price_query_all(query)
    if coin_ids:
        try:
            for quote in _crypto_price_fallback_multi(coin_ids, timeout_s=timeout_s):
                if quote.asset_key not in seen_keys:
                    seen_keys.add(quote.asset_key)
                    results.append(quote)
        except Exception:
            pass

    market_targets = _looks_like_market_quote_query_all(query)
    if market_targets:
        try:
            market_quotes = _market_quote_fallback_multi(query, market_targets, timeout_s=timeout_s)
        except Exception:
            market_quotes = []
        for quote in market_quotes:
            if quote.asset_key not in seen_keys:
                seen_keys.add(quote.asset_key)
                results.append(quote)

    if results:
        return results
    single = lookup_live_quote(query, timeout_s=timeout_s)
    return [single] if single else []


def lookup_live_quote(query: str, *, timeout_s: float = 8.0) -> LiveQuoteResult | None:
    coin_id = _looks_like_price_query(query)
    if coin_id:
        try:
            result = _crypto_price_fallback(query, coin_id, timeout_s=timeout_s)
            if result:
                _provider, hits, _pages, _notes = result
                if hits:
                    return _live_quote_from_summary(
                        asset_key=coin_id,
                        asset_name=coin_id.replace("-", " ").title(),
                        symbol=coin_id.replace("-", "_").upper(),
                        summary=hits[0].snippet,
                        source_label="CoinGecko",
                        source_url=hits[0].url,
                        kind="crypto",
                    )
        except Exception:
            pass
    target = _looks_like_market_quote_query(query)
    if not target:
        return None
    try:
        result = _market_quote_fallback(query, target, timeout_s=timeout_s)
    except Exception:
        return None
    if not result:
        return None
    _provider, hits, _pages, _notes = result
    if not hits:
        return None
    return _live_quote_from_summary(
        asset_key=target.asset_key,
        asset_name=target.asset_name,
        symbol=target.symbol,
        summary=hits[0].snippet,
        source_label="Yahoo Finance",
        source_url=hits[0].url,
        kind="market",
        unit_label=target.unit_label,
    )


def _live_quote_from_summary(
    *,
    asset_key: str,
    asset_name: str,
    symbol: str,
    summary: str,
    source_label: str,
    source_url: str,
    kind: str,
    unit_label: str = "",
) -> LiveQuoteResult | None:
    price_match = re.search(r"(?P<value>\d[\d,]*\.?\d*)", summary or "")
    if not price_match:
        return None
    try:
        value = float(price_match.group("value").replace(",", ""))
    except Exception:
        return None
    as_of_match = re.search(r"\bas of (?P<as_of>[^|]+)", summary or "", re.IGNORECASE)
    change_match = re.search(r"\b(?:24h|session) change: (?P<change>[+-]?\d+(?:\.\d+)?)%", summary or "", re.IGNORECASE)
    change_window_match = re.search(r"\b(?P<label>24h|session) change:", summary or "", re.IGNORECASE)
    return LiveQuoteResult(
        asset_key=asset_key,
        asset_name=asset_name,
        symbol=symbol,
        value=value,
        currency="USD",
        as_of=str(as_of_match.group("as_of") if as_of_match else "").strip(),
        source_label=source_label,
        source_url=source_url,
        kind=kind,
        unit_label=unit_label,
        change_percent=None if not change_match else float(change_match.group("change")),
        change_window=str(change_window_match.group("label") if change_window_match else "").strip(),
    )


def _last_numeric(items: Any) -> float | None:
    for item in reversed(list(items or [])):
        if item in {None, ""}:
            continue
        try:
            return float(item)
        except Exception:
            continue
    return None


def _market_quote_fallback(
    query: str,
    target: MarketQuoteTarget,
    *,
    timeout_s: float,
) -> tuple[str, list[WebHit], list[PageEvidence], list[str]] | None:
    api_url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        + urllib.parse.quote(target.symbol, safe="=")
        + "?interval=1m&range=1d"
    )
    req = urllib.request.Request(api_url, headers={"User-Agent": "VOOL-MARKETS/1.0"})
    with _open_remote(req, timeout=min(max(timeout_s, 3.0), 12.0)) as resp:
        payload = json.loads(resp.read(300000).decode("utf-8", errors="ignore"))

    chart = dict(payload.get("chart") or {})
    results = list(chart.get("result") or [])
    if not results:
        return None
    result = dict(results[0] or {})
    meta = dict(result.get("meta") or {})
    quote_block = dict((result.get("indicators") or {}).get("quote", [{}])[0] or {})
    price = meta.get("regularMarketPrice")
    if price in {None, ""}:
        price = _last_numeric(quote_block.get("close"))
    if price in {None, ""}:
        return None
    previous_close = meta.get("previousClose")
    if previous_close in {None, ""}:
        previous_close = meta.get("chartPreviousClose")
    change_percent: float | None = None
    try:
        previous_value = float(previous_close)
        if previous_value:
            change_percent = ((float(price) - previous_value) / previous_value) * 100.0
    except Exception:
        change_percent = None
    timestamp = meta.get("regularMarketTime")
    if timestamp in {None, ""}:
        timestamps = list(result.get("timestamp") or [])
        timestamp = timestamps[-1] if timestamps else None
    quote = LiveQuoteResult(
        asset_key=target.asset_key,
        asset_name=target.asset_name,
        symbol=target.symbol,
        value=float(price),
        currency=str(meta.get("currency") or "USD").strip().upper(),
        as_of=format_quote_timestamp(None if timestamp in {None, ""} else float(timestamp)),
        source_label="Yahoo Finance",
        source_url="https://finance.yahoo.com/quote/" + urllib.parse.quote(target.symbol, safe="="),
        kind="market",
        unit_label=target.unit_label,
        change_percent=change_percent,
        change_window="session",
        timestamp_utc=None if timestamp in {None, ""} else int(float(timestamp)),
        exchange=str(meta.get("exchangeName") or "").strip(),
    )
    summary = quote.summary_text()
    hit = WebHit(title=f"{target.asset_name} quote", url=quote.source_url, snippet=summary, engine="yahoo_finance", score=None)
    page = PageEvidence(url=quote.source_url, final_url=quote.source_url, status="ok", title=hit.title, text=summary)
    return ("yahoo_finance", [hit], [page], [f"live_price_fallback:yahoo_finance:{target.asset_key}"])


def _market_quote_fallback_multi(
    query: str,
    targets: list[MarketQuoteTarget],
    *,
    timeout_s: float,
) -> list[LiveQuoteResult]:
    """Fetch every named commodity target, isolating one target's failure from the rest.

    Yahoo's chart endpoint has no comma-batch form the way CoinGecko's `simple/price` does, so
    this is one HTTP call per target -- bounded, since `_MARKET_QUOTE_TARGETS` has four entries
    total. Concurrent because these are independent read-only network calls with no local-model
    cost, same reasoning as the multi-city weather fetch.
    """

    def _fetch_one(target: MarketQuoteTarget) -> LiveQuoteResult | None:
        try:
            result = _market_quote_fallback(query, target, timeout_s=timeout_s)
        except Exception:
            return None
        if not result:
            return None
        _provider, hits, _pages, _notes = result
        if not hits:
            return None
        return _live_quote_from_summary(
            asset_key=target.asset_key,
            asset_name=target.asset_name,
            symbol=target.symbol,
            summary=hits[0].snippet,
            source_label="Yahoo Finance",
            source_url=hits[0].url,
            kind="market",
            unit_label=target.unit_label,
        )

    if len(targets) == 1:
        quote = _fetch_one(targets[0])
        return [quote] if quote else []
    try:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
            fetched = list(pool.map(_fetch_one, targets))
    except Exception:
        fetched = [_fetch_one(target) for target in targets]
    return [quote for quote in fetched if quote is not None]


def _specialized_live_research(
    query: str,
    *,
    max_hits: int,
    fetch_timeout_s: float,
) -> tuple[str, list[WebHit], list[PageEvidence], list[str]] | None:
    if _looks_like_weather_query(query):
        return _weather_fallback(query, timeout_s=fetch_timeout_s)
    if _looks_like_news_query(query):
        return _news_rss_fallback(query, max_hits=max_hits, timeout_s=fetch_timeout_s)
    # Every coin the query names, not just the first: this path is only reached when the caller
    # already established the query fully resolves, so returning one quote for a two-asset question
    # would drop an asset the runtime demonstrably could have fetched.
    coin_ids = _looks_like_price_query_all(query)
    if len(coin_ids) > 1:
        try:
            multi = _crypto_price_hits_multi(coin_ids, timeout_s=fetch_timeout_s)
            if multi:
                return multi
        except Exception:
            pass
    coin_id = _looks_like_price_query(query)
    if coin_id:
        try:
            return _crypto_price_fallback(query, coin_id, timeout_s=fetch_timeout_s)
        except Exception:
            pass
    market_target = _looks_like_market_quote_query(query)
    if market_target:
        try:
            return _market_quote_fallback(query, market_target, timeout_s=fetch_timeout_s)
        except Exception:
            pass
    return None


def _normalize_place(value: str) -> str:
    """Casefold, strip diacritics and punctuation, collapse whitespace -- so "München" and
    "Munchen" compare equal and "Bouma'chouk" loses its apostrophe."""

    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9\s]+", " ", stripped.casefold()).split())


def _within_edit_distance(a: str, b: str, budget: int) -> bool:
    """True when a is at most `budget` single-character edits from b. Written out and capped so a
    genuine typo that the provider RESOLVED CORRECTLY ("talinn" -> "tallinn") still corresponds,
    while a fabrication ("talinn" -> "bouma chouk") never could."""

    if abs(len(a) - len(b)) > budget:
        return False
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        best = i
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
            best = min(best, current[-1])
        if best > budget:
            return False
        previous = current
    return previous[-1] <= budget


@_functools_lru_cache(maxsize=1)
def _tzdb_city_table() -> dict[str, tuple[float, float]]:
    """City-leaf -> (lat, lon) from the tzdb zone.tab the runtime already ships.

    Offline, authoritative for every zone city (capitals and major cities worldwide), and already
    the repo's precedent for canonical place knowledge (`_canonical_zone_leaves` in the clock
    lane). ISO 6709 +DDMM+DDDMM parsing, minutes to decimal."""

    import pathlib
    import zoneinfo

    table: dict[str, tuple[float, float]] = {}
    for root in zoneinfo.TZPATH:
        tab = pathlib.Path(root) / "zone.tab"
        if not tab.exists():
            continue
        for line in tab.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            match = re.match(r"([+-]\d{2})(\d{2})(\d{2})?([+-]\d{3})(\d{2})(\d{2})?", parts[1])
            if not match:
                continue
            lat = float(match.group(1)) + float(match.group(2)) / 60.0 * (1 if match.group(1)[0] == "+" else -1)
            lon = float(match.group(4)) + float(match.group(5)) / 60.0 * (1 if match.group(4)[0] == "+" else -1)
            leaf = parts[2].rsplit("/", 1)[-1].replace("_", " ").casefold()
            table[leaf] = (lat, lon)
        break
    return table


def _tzdb_coords_for(requested_norm: str) -> tuple[float, float] | None:
    """Coordinates for the requested city, tolerating the typo the provider itself tolerated."""

    table = _tzdb_city_table()
    if requested_norm in table:
        return table[requested_norm]
    budget = 1 if len(requested_norm) <= 4 else 2
    for leaf, coords in table.items():
        if abs(len(leaf) - len(requested_norm)) <= budget and _within_edit_distance(
            requested_norm, leaf, budget
        ):
            return coords
    return None


def _rough_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    import math

    lat = math.radians((a[0] + b[0]) / 2)
    dx = (a[1] - b[1]) * 111.32 * math.cos(lat)
    dy = (a[0] - b[0]) * 110.57
    return (dx * dx + dy * dy) ** 0.5


def _place_names_correspond(requested: str, named: str) -> bool:
    """NAME-STRICT correspondence for evidence whose place-name IS its subject.

    A search snippet naming "Mount Airy, NC" is ABOUT Mount Airy -- unlike a wttr station,
    whose name is routinely a district of the requested city. So snippets are judged on names
    (substring, word overlap, diacritics, bounded typo distance) with no fail-open default;
    the provider-payload gate below is coordinate-based and fail-open instead, and conflating
    the two broke each in turn: name-strict rejected Warsaw's station, fail-open accepted
    Mount Airy's snippet.
    """

    req = _normalize_place(requested)
    named_norm = _normalize_place(named)
    if not req or not named_norm:
        return False
    if req == named_norm or req in named_norm or named_norm in req:
        return True
    req_words = {w for w in req.split() if len(w) >= 3}
    named_words = {w for w in named_norm.split() if len(w) >= 3}
    if req_words & named_words:
        return True
    budget = 1 if len(req) <= 4 else 2
    return _within_edit_distance(req, named_norm, budget)


def _weather_place_corresponds(
    requested: str,
    area_name: str,
    country_name: str,
    region_name: str = "",
    latitude: object = None,
    longitude: object = None,
) -> bool:
    """Whether the place the provider actually resolved matches what the caller asked for.

    Measured on the served surface, 2026-08-15: "weather in Talinn now?" (a typo for Tallinn) made
    wttr.in geolocate to its nearest-area fallback "Bouma'chouk, Algeria", and the runtime answered
    "Weather in Talinn: Bouma'chouk, Algeria: Sunny, 31 C" with full confidence AFTER the lookup had
    logged a failure. A 200 response with parseable conditions is not evidence of a PLACE match --
    wttr.in resolves an unrecognized string to some arbitrary nearest area rather than erroring.

    Correspondence is deliberately generous toward SAFE outcomes: a real match, a country append
    ("Paris" -> "Paris, France"), a multi-word overlap ("New York City" -> "New York"), and a typo
    the provider fixed for us ("Talinn" -> "Tallinn") all pass. An exotic alias the provider mapped
    to an unrelated string ("NYC" -> "New York") declines instead -- declining is the safe
    direction the invariant demands; fabricating a confident wrong place is not.
    """

    req = _normalize_place(requested)
    if not req:
        # No specific place was requested (IP-geolocation path): whatever the provider resolved is
        # by definition the answer, so there is nothing to contradict.
        return True
    # DECLINE ON CONTRADICTION, NOT ON ABSENCE OF CONFIRMATION. The first version of this gate
    # required the resolved NAMES to confirm the request, and it broke live weather for real
    # cities twice in one day: nearest_area is the nearest weather STATION, and a station is
    # routinely a district sharing no words with the city -- measured, "Warsaw" resolves to
    # "Powisle", "Manchester" to "Sedgley Park", "Tallinn" to "Oleviste", and Krakow (no tzdb
    # zone of its own, station named differently, region in Polish) is unconfirmable by ANY name.
    # Name-mismatch-without-coordinates is therefore indistinguishable from a correct answer and
    # must pass. What CAN be known is a contradiction: when the requested city (or its
    # typo-neighbour) is a tzdb zone city, the runtime knows its coordinates offline, and the
    # provider reports where it actually resolved. Warsaw-to-Powisle is 2 km; the "Talinn" typo
    # geolocated to Algeria is 2,700 km from Tallinn; requested "Paris" resolved to Paris, Texas
    # is 7,900 km out. Coordinates cannot be fooled by station naming, in either direction.
    try:
        provider_coords: tuple[float, float] | None = (float(latitude), float(longitude))
    except (TypeError, ValueError):
        provider_coords = None
    if provider_coords is not None:
        expected = _tzdb_coords_for(req)
        if expected is not None:
            return _rough_km(provider_coords, expected) <= 300.0
    return True


def _weather_fallback(
    query: str,
    *,
    timeout_s: float,
) -> tuple[str, list[WebHit], list[PageEvidence], list[str]] | None:
    # This provider answers the ATMOSPHERE at a place. A water-temperature request is a different
    # measurement and must not be served from here -- measured live: "what is the watter temperature
    # in Balctic sea?" sent "balctic sea" to wttr.in, which GEOLOCATED IT TO SEATTLE and returned
    # Seattle's air conditions. The typed lane already declines these (see
    # `core.agent_runtime.fast_live_info_mode_classifier`); this is the research-fallback seam,
    # which reached the same fetcher by another road and produced the Seattle reading again.
    from core.measurement_medium import requests_a_water_temperature

    if requests_a_water_temperature(query):
        return None

    location = _extract_weather_location(query)
    if not location:
        # Extractor rejected the "location" as implausible (scaffolding / merged
        # blob / not a real place). Bail so this flows to normal handling instead
        # of wttr.in guessing a random city from garbage.
        return None
    page_url = "https://wttr.in/" + urllib.parse.quote(location)
    api_url = page_url + "?format=j1"
    request = urllib.request.Request(api_url, headers={"User-Agent": "VOOL-WEATHER/1.0"})
    with _open_remote(request, timeout=min(max(timeout_s, 3.0), 12.0)) as response:
        payload = json.loads(response.read(300000).decode("utf-8", errors="ignore"))

    root_payload = dict((payload.get("data") or payload) if isinstance(payload, dict) else {})
    current_items = list(root_payload.get("current_condition") or [])
    nearest_items = list(root_payload.get("nearest_area") or [])
    if not current_items:
        return None

    current = current_items[0] or {}
    area = nearest_items[0] if nearest_items else {}
    area_name = _first_nested_value(area.get("areaName")) or location
    country_name = _first_nested_value(area.get("country"))
    region_name = _first_nested_value(area.get("region"))
    if not _weather_place_corresponds(
        location, area_name, country_name, region_name, area.get("latitude"), area.get("longitude")
    ):
        # The provider resolved a place that does not correspond to the request (a typo geolocated
        # to an unrelated nearest-area). A failed match is not an answer -- decline rather than
        # report a confident wrong city.
        return None
    observed = str(current.get("localObsDateTime") or current.get("observation_time") or "").strip()
    weather_desc = _first_nested_value(current.get("weatherDesc")) or "Conditions unavailable"
    temp_c = str(current.get("temp_C") or "?").strip()
    feels_c = str(current.get("FeelsLikeC") or "?").strip()
    humidity = str(current.get("humidity") or "?").strip()
    wind_kmph = str(current.get("windspeedKmph") or "?").strip()
    place = area_name if not country_name or country_name.lower() == area_name.lower() else f"{area_name}, {country_name}"
    summary = (
        f"{place}: {weather_desc}, {temp_c} C (feels like {feels_c} C), "
        f"humidity {humidity}%, wind {wind_kmph} km/h."
    )
    if observed:
        summary += f" Observed {observed}."

    hit = WebHit(
        title=f"wttr.in weather for {place}",
        url=page_url,
        snippet=summary,
        engine="wttr_in",
        score=None,
    )
    page = PageEvidence(
        url=page_url,
        final_url=page_url,
        status="ok",
        title=hit.title,
        text=summary,
        html_len=len(json.dumps(payload)),
        used_browser=False,
        screenshot_path=None,
    )
    return ("wttr_in", [hit], [page], ["live_weather_fallback:wttr_in"])


def structured_weather_lookup(location: str, *, timeout_s: float = 8.0) -> WeatherResult | None:
    """A real structured weather observation for an ALREADY-VALIDATED location, or None.

    Sibling to `_weather_fallback`, not a replacement: that function re-extracts a location from a
    raw query string (via `_extract_weather_location`) and returns a prose summary for the general
    research pipeline. This function skips extraction -- the caller (a typed `LiveDataSubtask`'s
    `arguments["location"]`) has already been through a plausibility guard -- and keeps the numeric
    fields as typed data instead of folding them into a sentence, so a derived comparison like
    "warmest city" can be computed from the number the provider actually reported.

    TWO independent providers, tried in order, because one was a live single point of failure:
    measured 2026-08-28, wttr.in's geocoder resolved "Rome" (and "Roma"-less variants, cased or
    not) to Lome, Togo. `_weather_place_corresponds` rightly refused to present Lome's reading as
    Rome's, and the product's whole weather capability was down for that city while the provider
    itself was healthy. open-meteo geocodes by name with population ranking and returns the same
    typed fields; a mismatch or failure on the first provider falls through to it instead of
    becoming "no observation returned".
    """
    clean_location = str(location or "").strip()
    if not clean_location or not _is_plausible_weather_location(clean_location):
        return None
    wttr_error: Exception | None = None
    try:
        result = _wttr_weather_lookup(clean_location, timeout_s=timeout_s)
    except Exception as exc:
        wttr_error = exc
        result = None
    if result is not None:
        return result
    fallback = _open_meteo_weather_lookup(clean_location, timeout_s=timeout_s)
    if fallback is not None:
        return fallback
    if wttr_error is not None:
        raise wttr_error
    return None


#: WMO weather interpretation codes (open-meteo's `weather_code`) to reader-facing conditions.
_WMO_WEATHER_CODES: dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle",
    56: "Freezing drizzle", 57: "Dense freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Heavy freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light rain showers", 81: "Rain showers", 82: "Violent rain showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with heavy hail",
}


def _open_meteo_weather_lookup(clean_location: str, *, timeout_s: float = 8.0) -> WeatherResult | None:
    """The open-meteo reading for a place name, or None. Second provider, same typed contract.

    Two keyless calls through `_open_remote` (so web-call accounting holds): the geocoder resolves
    the NAME to coordinates with population ranking -- which is exactly the disambiguation the
    first provider got wrong -- and the forecast endpoint reads current conditions there. The
    geocoder's own resolution IS the place correspondence: it returns the name it matched and the
    country, and a query it cannot match returns no results rather than a fuzzy neighbour.
    """
    timeout = min(max(timeout_s, 3.0), 12.0)
    geo_url = (
        "https://geocoding-api.open-meteo.com/v1/search?count=1&language=en&format=json&name="
        + urllib.parse.quote(clean_location)
    )
    geo_request = urllib.request.Request(geo_url, headers={"User-Agent": "VOOL-WEATHER/1.0"})
    with _open_remote(geo_request, timeout=timeout) as response:
        geo_payload = json.loads(response.read(120000).decode("utf-8", errors="ignore"))
    results = list(geo_payload.get("results") or []) if isinstance(geo_payload, dict) else []
    if not results:
        return None
    hit = results[0] or {}
    latitude, longitude = hit.get("latitude"), hit.get("longitude")
    if latitude is None or longitude is None:
        return None
    area_name = str(hit.get("name") or clean_location)
    country_name = str(hit.get("country") or "")
    forecast_url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={float(latitude)}&longitude={float(longitude)}"
        "&current=temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code"
        "&daily=temperature_2m_max,temperature_2m_min&timezone=auto&forecast_days=1"
    )
    forecast_request = urllib.request.Request(
        forecast_url, headers={"User-Agent": "VOOL-WEATHER/1.0"}
    )
    with _open_remote(forecast_request, timeout=timeout) as response:
        forecast = json.loads(response.read(120000).decode("utf-8", errors="ignore"))
    current = dict(forecast.get("current") or {}) if isinstance(forecast, dict) else {}
    daily = dict(forecast.get("daily") or {}) if isinstance(forecast, dict) else {}

    def _numeric(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    temp_c = _numeric(current.get("temperature_2m"))
    if temp_c is None:
        return None
    code = current.get("weather_code")
    condition = _WMO_WEATHER_CODES.get(int(code), "Conditions unavailable") if isinstance(
        code, (int, float)
    ) else "Conditions unavailable"

    def _first_daily(key: str) -> float | None:
        values = daily.get(key)
        return _numeric(values[0]) if isinstance(values, list) and values else None

    place = (
        area_name
        if not country_name or country_name.lower() == area_name.lower()
        else f"{area_name}, {country_name}"
    )
    return WeatherResult(
        location=clean_location,
        place_label=place,
        condition=condition,
        temperature_c=temp_c,
        feels_like_c=_numeric(current.get("apparent_temperature")),
        humidity_pct=_numeric(current.get("relative_humidity_2m")),
        wind_kmph=_numeric(current.get("wind_speed_10m")),
        observed_at=str(current.get("time") or "").strip(),
        source_label="open-meteo.com",
        source_url="https://open-meteo.com/",
        high_c=_first_daily("temperature_2m_max"),
        low_c=_first_daily("temperature_2m_min"),
    )


def _wttr_weather_lookup(clean_location: str, *, timeout_s: float = 8.0) -> WeatherResult | None:
    """The wttr.in reading for an already-validated place, or None -- first provider."""
    page_url = "https://wttr.in/" + urllib.parse.quote(clean_location)
    api_url = page_url + "?format=j1"
    # Reporting used to happen here, because this was the one path that had been fixed (2026-08-13:
    # a Local Only turn quoting live wttr.in data while its trace reported `web_calls: 0`). It now
    # happens in `_open_remote`, which every outbound path in this module goes through -- keeping
    # the explicit call as well would count this fetch twice and break the same account from the
    # other direction.
    request = urllib.request.Request(api_url, headers={"User-Agent": "VOOL-WEATHER/1.0"})
    with _open_remote(request, timeout=min(max(timeout_s, 3.0), 12.0)) as response:
        payload = json.loads(response.read(300000).decode("utf-8", errors="ignore"))

    root_payload = dict((payload.get("data") or payload) if isinstance(payload, dict) else {})
    current_items = list(root_payload.get("current_condition") or [])
    nearest_items = list(root_payload.get("nearest_area") or [])
    daily_items = list(root_payload.get("weather") or [])
    if not current_items:
        return None

    current = current_items[0] or {}
    area = nearest_items[0] if nearest_items else {}
    # wttr.in's `weather` array is the daily forecast, one entry per day starting today --
    # `weather[0]` is today's own forecast, distinct from `current_condition` (the live reading).
    today = daily_items[0] if daily_items else {}
    area_name = _first_nested_value(area.get("areaName")) or clean_location
    country_name = _first_nested_value(area.get("country"))
    if not _weather_place_corresponds(
        clean_location,
        area_name,
        country_name,
        _first_nested_value(area.get("region")),
        area.get("latitude"),
        area.get("longitude"),
    ):
        return None
    place = area_name if not country_name or country_name.lower() == area_name.lower() else f"{area_name}, {country_name}"

    def _numeric(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    temp_c = _numeric(current.get("temp_C"))
    if temp_c is None:
        return None
    return WeatherResult(
        location=clean_location,
        place_label=place,
        condition=_first_nested_value(current.get("weatherDesc")) or "Conditions unavailable",
        temperature_c=temp_c,
        feels_like_c=_numeric(current.get("FeelsLikeC")),
        humidity_pct=_numeric(current.get("humidity")),
        wind_kmph=_numeric(current.get("windspeedKmph")),
        observed_at=str(current.get("localObsDateTime") or current.get("observation_time") or "").strip(),
        source_label="wttr.in",
        source_url=page_url,
        high_c=_numeric(today.get("maxtempC")),
        low_c=_numeric(today.get("mintempC")),
    )


def _news_rss_fallback(
    query: str,
    *,
    max_hits: int,
    timeout_s: float,
) -> tuple[str, list[WebHit], list[PageEvidence], list[str]] | None:
    topic = _extract_news_topic(query)
    if not topic:
        return None
    # When the request signals recency ("latest"/"breaking"/...), scope Google News to the last 14
    # days at the source with the `when:` operator so we don't rank a month-old-but-relevant article
    # to the top. Non-recency news queries (a specific past event) keep the full archive.
    wants_recent = any(term in str(query or "").lower() for term in ("latest", "breaking", "newest", "recent", "fresh", "today"))
    rss_q = f"{topic} when:14d" if wants_recent else topic
    rss_url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(rss_q)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    request = urllib.request.Request(rss_url, headers={"User-Agent": "VOOL-NEWS/1.0"})
    with _open_remote(request, timeout=min(max(timeout_s, 3.0), 12.0)) as response:
        xml_text = response.read(500000).decode("utf-8", errors="ignore")

    root = ET.fromstring(xml_text)
    # Collect every item first with its parsed date, then sort newest-first -- Google's relevance
    # order can float an old article to the top, so truncating the raw feed is what returned stale news.
    collected: list[tuple[datetime | None, WebHit]] = []
    seen_urls: set[str] = set()
    for item in root.findall(".//item"):
        title = str(item.findtext("title") or "").strip()
        link = str(item.findtext("link") or "").strip()
        source_el = item.find("source")
        source_name = str(source_el.text or "").strip() if source_el is not None and source_el.text else ""
        source_url = str(source_el.get("url") or "").strip() if source_el is not None else ""
        final_url = _resolve_redirect_url(link, timeout_s=timeout_s) or source_url or link
        if not final_url or final_url in seen_urls:
            continue
        verdict = evaluate_source_domain(_domain_from_url(final_url or source_url))
        if verdict.blocked:
            continue
        seen_urls.add(final_url)
        raw_pub = str(item.findtext("pubDate") or "")
        pub_dt = _parse_pub_datetime(raw_pub)
        pub_date = _compact_pub_date(raw_pub)
        summary_parts = [source_name, pub_date, title]
        summary = " | ".join(part for part in summary_parts if part)
        collected.append(
            (
                pub_dt,
                WebHit(
                    title=title or source_name or "News result",
                    url=final_url,
                    snippet=summary[:280],
                    engine="google_news_rss",
                    score=None,
                ),
            )
        )
    if not collected:
        return None
    # Newest first; undated items sort last. sorted() is stable, so same-date items keep feed order.
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    collected.sort(key=lambda pair: pair[0] or oldest, reverse=True)
    if wants_recent:
        cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        recent = [pair for pair in collected if pair[0] is not None and pair[0] >= cutoff]
        if recent:  # keep the newest few even if the whole feed is older than the window
            collected = recent
    hits = [hit for _, hit in collected[: max(1, int(max_hits))]]
    if not hits:
        return None
    return ("google_news_rss", hits, [], ["live_news_fallback:google_news_rss"])


def _resolve_redirect_url(url: str, *, timeout_s: float) -> str:
    target = str(url or "").strip()
    if not target:
        return ""
    request = urllib.request.Request(target, headers={"User-Agent": "VOOL-NEWS/1.0"})
    with _open_remote(request, timeout=min(max(timeout_s, 3.0), 12.0)) as response:
        return str(response.geturl() or target).strip()


def _first_nested_value(items: Any) -> str:
    for item in list(items or []):
        if isinstance(item, dict):
            value = str(item.get("value") or "").strip()
            if value:
                return value
        else:
            value = str(item or "").strip()
            if value:
                return value
    return ""


_RELEVANCE_STOPWORDS = {
    "the", "and", "for", "are", "was", "what", "who", "how", "why", "when", "where", "which", "that",
    "this", "with", "from", "your", "you", "about", "into", "give", "get", "show", "tell", "please",
    "latest", "current", "now", "today", "news", "find", "does", "did", "have", "has", "can", "could",
    "would", "should", "will", "there", "here", "some", "any", "our", "out",
}
# Pages that returned 200 but carry no real answer — a provider must not "win" merely by returning text.
_JUNK_PAGE_MARKERS = (
    "captcha", "verify you are human", "verify you are a human", "are you a robot", "unusual traffic",
    "please enable javascript", "enable javascript to", "accept cookies", "cookie consent",
    "before you continue", "we value your privacy", "consent to the use", "access denied",
    "403 forbidden", "too many requests", "rate limit exceeded", "detected unusual traffic",
)
# A hit URL that is just the search engine's own query page, not an actual result.
_SEARCH_HOMEPAGE_MARKERS = ("duckduckgo.com/?q=", "google.com/search", "bing.com/search", "/html/?q=", "/search?q=")


def _looks_like_current_query(query: str) -> bool:
    lowered = f" {str(query or '').lower()} "
    if _looks_like_news_query(query):
        return True
    return any(
        marker in lowered
        for marker in (
            " who is the ", " who's the ", " current president", " president now", " prime minister now",
            " right now ", " as of now ", " today ", " latest ", " current ",
        )
    )


def _accept_search_hits(query: str, provider: str, hits: list[WebHit]) -> tuple[bool, str]:
    """Quality gate: a provider result must be USABLE, not merely non-empty.

    Returns (accepted, reason). A rejected result makes ``web_research`` fall through to the next
    provider, so an encyclopedia abstract / anti-bot page / homepage / off-topic set never wins.
    """
    if not hits:
        return (False, "empty")
    usable = [
        h for h in hits
        if str(h.url or "").strip() and not any(marker in str(h.url).lower() for marker in _SEARCH_HOMEPAGE_MARKERS)
    ]
    if not usable:
        return (False, "no_usable_source_urls")
    blob = " ".join(str(h.snippet or "") for h in hits[:3]).lower()
    if any(marker in blob for marker in _JUNK_PAGE_MARKERS):
        return (False, "anti_bot_or_consent")
    # Encyclopedia / instant-answer is not real search — never let it satisfy a current/news query.
    if provider in {"ddg", "ddg_instant"} and _looks_like_current_query(query):
        return (False, "encyclopedia_for_current_query")
    # Relevance: at least one usable hit must share a meaningful query keyword.
    q_tokens = {tok for tok in re.findall(r"[a-z0-9]{3,}", str(query or "").lower()) if tok not in _RELEVANCE_STOPWORDS}
    if q_tokens:
        text = " ".join(f"{h.title or ''} {h.snippet or ''} {h.url or ''}" for h in usable).lower()
        if not any(tok in text for tok in q_tokens):
            return (False, "unrelated_to_query")
    # Duplicate-only: several hits all pointing at one URL.
    distinct = {str(h.url or "").lower() for h in usable}
    if len(usable) > 1 and len(distinct) == 1:
        return (False, "duplicate_only")
    return (True, "ok")


# One turn remembers which engines failed for it. A research turn runs several queries (an initial
# search plus one per enumerated facet); an engine that failed or returned nothing for the first is
# not tried again for the rest of THAT turn, and the skip is recorded in the notes. The next turn
# tries everything again. Measured 2026-09-06: three research rounds each waited ~50 s on engines
# that had already failed for the turn. Keyed by the bound request id; bounded in size.
_ENGINE_MEMORY_MAX_TURNS = 64
_ENGINE_FAILURES: "OrderedDict[str, dict[str, str]]" = OrderedDict()
_ENGINE_LOCK = threading.Lock()


def _turn_key_for_engine_memory() -> str:
    try:
        from core.semantic.semantic_admissions import current_request_id

        return str(current_request_id() or "").strip()
    except Exception:
        return ""


#: CROSS-TURN COOLDOWN for a keyless engine that could not be REACHED (connection refused, DNS,
#: socket timeout) -- never for one that answered "nothing". Measured on the final pack: every
#: keyless engine was unreachable on every turn, and each current-information turn re-spent
#: 24 s discovering it (turns 14/17/19: 106-128 s each). A transport failure is a fact about the
#: machine that a 120 s window states plainly in the notes (`<engine>_skipped:cooldown:<reason>`);
#: a success clears it, and keyed providers (the user's own credential) are never cooled.
_ENGINE_COOLDOWN_S = 120.0
_ENGINE_COOLDOWNS: dict[str, tuple[float, str]] = {}
_TRANSPORT_FAILURE_TOKENS = frozenset(
    {
        "unreachable", "timeout", "TimeoutError", "URLError", "ConnectionRefusedError",
        "ConnectionResetError", "ConnectionError", "RemoteDisconnected", "gaierror", "OSError",
        "socket.timeout",
    }
)


def _note_engine_unreachable(provider: str, reason: str) -> None:
    token = str(reason or "").split(":")[-1].strip()
    if token not in _TRANSPORT_FAILURE_TOKENS:
        return
    with _ENGINE_LOCK:
        _ENGINE_COOLDOWNS[str(provider)] = (time.monotonic() + _ENGINE_COOLDOWN_S, token[:60])


#: EMPTY STREAK. A keyless SCRAPER that answers nothing on two consecutive DISTINCT queries is
#: not working on this machine right now (bot-blocked, changed markup) -- measured in-process
#: 2026-09-10: `browser_search` ran first, spent ~48 s of a 30 s budget on a headless browser,
#: returned `browser_search_empty` on every query, and `deadline_exhausted:search` stopped the
#: chain before searxng or ddg_instant ran. "Empty" is not a transport failure, so the transport
#: cooldown never armed. A streak of empties on distinct queries cools the engine for the same
#: window; one accepted result clears it. Never applied to ddg_instant (encyclopedia-only: empty
#: is its ordinary answer to most queries) and never to keyed providers.
_EMPTY_STREAK_ENGINES = frozenset({"browser_search", "google_html", "duckduckgo_html"})
_EMPTY_STREAK_LIMIT = 2
_ENGINE_EMPTIES: dict[str, list[str]] = {}


def _note_engine_empty(provider: str, query: str) -> None:
    if str(provider) not in _EMPTY_STREAK_ENGINES:
        return
    import hashlib

    digest = hashlib.sha256(" ".join(str(query or "").split()).encode("utf-8")).hexdigest()[:16]
    with _ENGINE_LOCK:
        seen = _ENGINE_EMPTIES.setdefault(str(provider), [])
        if digest not in seen:
            seen.append(digest)
        del seen[:-8]
        if len(seen) >= _EMPTY_STREAK_LIMIT:
            _ENGINE_COOLDOWNS[str(provider)] = (
                time.monotonic() + _ENGINE_COOLDOWN_S,
                f"empty_streak_{len(seen)}",
            )


def _clear_engine_cooldown(provider: str) -> None:
    with _ENGINE_LOCK:
        _ENGINE_COOLDOWNS.pop(str(provider), None)
        _ENGINE_EMPTIES.pop(str(provider), None)


def _engine_skip_reason(provider: str) -> str:
    with _ENGINE_LOCK:
        cooled = _ENGINE_COOLDOWNS.get(str(provider))
        if cooled is not None:
            until, reason = cooled
            if time.monotonic() < until:
                return f"cooldown:{reason}"
            _ENGINE_COOLDOWNS.pop(str(provider), None)
    key = _turn_key_for_engine_memory()
    if not key:
        return ""
    with _ENGINE_LOCK:
        return str((_ENGINE_FAILURES.get(key) or {}).get(provider) or "")


def _note_engine_failure(provider: str, reason: str) -> None:
    key = _turn_key_for_engine_memory()
    if not key:
        return
    with _ENGINE_LOCK:
        bucket = _ENGINE_FAILURES.setdefault(key, {})
        bucket[provider] = str(reason or "failed_earlier_this_turn")
        _ENGINE_FAILURES.move_to_end(key)
        while len(_ENGINE_FAILURES) > _ENGINE_MEMORY_MAX_TURNS:
            _ENGINE_FAILURES.popitem(last=False)


def _reset_engine_memory_for_tests() -> None:
    with _ENGINE_LOCK:
        _ENGINE_FAILURES.clear()
        _ENGINE_COOLDOWNS.clear()
        _ENGINE_EMPTIES.clear()


def web_research(
    query: str,
    *,
    language: str = "en",
    safesearch: int = 1,
    max_hits: int = 8,
    max_pages: int = 3,
    fetch_timeout_s: float = 15.0,
    total_budget_s: float | None = None,
    browser_engine: str | None = None,
    evidence_screenshot_dir: str | None = None,
) -> ResearchResult:
    notes: list[str] = []
    hits: list[WebHit] = []
    pages: list[PageEvidence] = []
    provider_used = "none"
    specialized_attempted = False

    # Wall-clock ceiling for the whole call. Each provider search and page fetch is
    # sequential and network-bound; without a shared deadline, slow/blocked scrapers
    # plus the headless-browser fallback stack into minutes. The deadline bounds the
    # total: past it we stop and return whatever was gathered.
    budget_s = float(total_budget_s) if (total_budget_s and total_budget_s > 0) else _DEFAULT_WEB_BUDGET_S
    _deadline = time.monotonic() + budget_s

    def _remaining() -> float:
        return _deadline - time.monotonic()

    if remote_fetch_forbidden():
        return ResearchResult(
            query=query,
            provider="disabled",
            hits=[],
            pages=[],
            notes=["remote_fetch_disabled"],
            ts_utc=time.time(),
        )

    if _prefer_specialized_live_research(query):
        specialized_attempted = True
        try:
            specialized = _specialized_live_research(
                query,
                max_hits=max_hits,
                fetch_timeout_s=fetch_timeout_s,
            )
        except Exception as exc:
            notes.append(f"specialized_live_failed:{type(exc).__name__}")
            specialized = None
        if specialized is not None:
            provider_used, hits, pages, extra_notes = specialized
            notes.extend(extra_notes)

    if not hits:
        for provider in _provider_order():
            if _remaining() <= 0:
                notes.append("deadline_exhausted:search")
                break
            skip_reason = _engine_skip_reason(provider)
            if skip_reason:
                notes.append(f"{provider}_skipped:{skip_reason}")
                continue
            if _is_search_api_provider(provider):
                # One block serves every key-backed API. Their differences are wire format, which
                # `tools/web/search_api_client` resolves from the provider table -- nothing about
                # what a provider may do is decided here.
                try:
                    hits = _search_api_hits(
                        provider,
                        query,
                        max_hits=max_hits,
                        timeout_s=min(12.0, max(2.0, _remaining())),
                    )
                except _SearchApiError as exc:
                    # The reason is the point: "unauthorized" (the key is dead) must never read
                    # the same as "the web had nothing", which is the exact distinction the search
                    # stack could not make before this note existed.
                    notes.append(f"{provider}_failed:{exc.reason}")
                    continue
                except Exception as exc:
                    notes.append(f"{provider}_failed:{type(exc).__name__}")
                    continue
                if hits:
                    accepted, reason = _accept_search_hits(query, provider, hits)
                    if accepted:
                        provider_used = provider
                        notes.append(f"provider_accepted:{provider}")
                        break
                    notes.append(f"provider_rejected:{provider}:{reason}")
                    hits = []
                else:
                    notes.append(f"{provider}_empty")
                continue

            if provider == "browser_search":
                remaining = _remaining()
                if remaining < _BROWSER_SEARCH_MIN_BUDGET_S:
                    notes.append("browser_search_skipped:budget")
                    continue
                # A SHARE, not the whole budget, while cheaper engines still follow in the chain:
                # measured 2026-09-10, the headless browser took the entire budget, answered
                # nothing, and `deadline_exhausted:search` silenced searxng and ddg_instant.
                order = _provider_order()
                followers = [p for p in order[order.index("browser_search") + 1 :]] if "browser_search" in order else []
                share = remaining * 0.5 if followers else remaining - 6.0
                try:
                    hits = _browser_search_hits(
                        query,
                        max_hits=max_hits,
                        timeout_s=min(_BROWSER_SEARCH_MAX_TIMEOUT_S, max(6.0, share)),
                    )
                    if hits:
                        accepted, reason = _accept_search_hits(query, "browser_search", hits)
                        if accepted:
                            provider_used = "browser_search"
                            notes.append("provider_accepted:browser_search")
                            break
                        notes.append(f"provider_rejected:browser_search:{reason}")
                        hits = []
                    else:
                        notes.append("browser_search_empty")
                        _note_engine_empty("browser_search", query)
                except Exception as exc:
                    notes.append(f"browser_search_failed:{type(exc).__name__}")
                    _note_engine_failure("browser_search", f"failed_earlier_this_turn:{type(exc).__name__}")
                    continue

            if provider == "searxng":
                try:
                    client = SearXNGClient()
                    results: list[SearchResult] = client.search(
                        query,
                        language=language,
                        safesearch=safesearch,
                        max_results=max_hits,
                    )
                    hits = [WebHit(r.title, r.url, r.snippet, r.engine, r.score) for r in results if r.url]
                    if hits:
                        accepted, reason = _accept_search_hits(query, "searxng", hits)
                        if accepted:
                            provider_used = "searxng"
                            notes.append("provider_accepted:searxng")
                            _clear_engine_cooldown("searxng")
                            break
                        notes.append(f"provider_rejected:searxng:{reason}")
                        hits = []
                except Exception as exc:
                    # The client classifies its own failures, so the note says WHY rather than
                    # which exception class carried it -- `HTTPError` covered 429, 4xx and 5xx
                    # alike, which is not something a backoff or a "check your URL" hint can key on.
                    reason = str(getattr(exc, "reason", "") or type(exc).__name__)
                    notes.append(f"searxng_failed:{reason}")
                    _note_engine_unreachable("searxng", reason)
                    _note_engine_failure("searxng", f"failed_earlier_this_turn:{reason}"[:80])
                    continue

            if provider in {"ddg", "ddg_instant"}:
                try:
                    payload = ddg_instant_answer(query, timeout_s=min(10.0, max(2.0, _remaining())))
                    blob = best_text_blob(payload) or ""
                    url = str(payload.get("AbstractURL") or "").strip()
                    title = str(payload.get("Heading") or "DuckDuckGo Instant Answer").strip()
                    if not url:
                        url = "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(query)
                    if not blob and url.startswith("https://duckduckgo.com/?q="):
                        notes.append("ddg_instant_empty")
                        continue
                    hits = [WebHit(title=title, url=url, snippet=blob, engine="ddg_instant", score=None)]
                    accepted, reason = _accept_search_hits(query, "ddg_instant", hits)
                    if accepted:
                        provider_used = "ddg_instant"
                        notes.append("provider_accepted:ddg_instant")
                        _clear_engine_cooldown("ddg_instant")
                        break
                    notes.append(f"provider_rejected:ddg_instant:{reason}")
                    hits = []
                except Exception as exc:
                    notes.append(f"ddg_failed:{type(exc).__name__}")
                    _note_engine_unreachable(provider, type(exc).__name__)
                    continue

            if provider == "duckduckgo_html":
                try:
                    hits = _duckduckgo_html_hits(query, max_hits=max_hits)
                    if hits:
                        accepted, reason = _accept_search_hits(query, "duckduckgo_html", hits)
                        if accepted:
                            provider_used = "duckduckgo_html"
                            notes.append("provider_accepted:duckduckgo_html")
                            _clear_engine_cooldown("duckduckgo_html")
                            break
                        notes.append(f"provider_rejected:duckduckgo_html:{reason}")
                        hits = []
                except Exception as exc:
                    notes.append(f"duckduckgo_html_failed:{type(exc).__name__}")
                    _note_engine_unreachable("duckduckgo_html", type(exc).__name__)
                    continue

            if provider == "google_html":
                try:
                    hits = _google_html_hits(query, max_hits=max_hits)
                    if hits:
                        accepted, reason = _accept_search_hits(query, "google_html", hits)
                        if accepted:
                            provider_used = "google_html"
                            notes.append("provider_accepted:google_html")
                            _clear_engine_cooldown("google_html")
                            break
                        notes.append(f"provider_rejected:google_html:{reason}")
                        hits = []
                    else:
                        notes.append("google_html_empty")
                        _note_engine_failure("google_html", "empty_earlier_this_turn")
                        _note_engine_empty("google_html", query)
                except Exception as exc:
                    notes.append(f"google_html_failed:{type(exc).__name__}")
                    _note_engine_unreachable("google_html", type(exc).__name__)
                    _note_engine_failure("google_html", f"failed_earlier_this_turn:{type(exc).__name__}")
                    continue

    if not hits and not specialized_attempted:
        try:
            specialized = _specialized_live_research(
                query,
                max_hits=max_hits,
                fetch_timeout_s=fetch_timeout_s,
            )
        except Exception as exc:
            notes.append(f"specialized_live_failed:{type(exc).__name__}")
            specialized = None
        if specialized is None:
            notes.append("no_search_hits")
            return ResearchResult(query=query, provider=provider_used, hits=[], pages=[], notes=notes, ts_utc=time.time())
        provider_used, hits, pages, extra_notes = specialized
        notes.extend(extra_notes)

    for hit in hits[: max(1, int(max_pages))]:
        if _prebuilt_page_for_hit(pages, hit) is not None:
            continue
        if _remaining() <= 1.0:
            notes.append("deadline_exhausted:pages")
            break
        try:
            fetched = http_fetch_text(hit.url, timeout_s=max(2.0, min(fetch_timeout_s, _remaining())))
            status = str(fetched.get("status") or "fetch_error")
            text = str(fetched.get("text") or "")
            html_text = str(fetched.get("html") or "")
            final_url = str(fetched.get("final_url") or hit.url)

            if _should_try_browser() and _needs_browser(status, text) and _remaining() >= _BROWSER_MIN_BUDGET_S:
                screenshot_path = None
                if evidence_screenshot_dir:
                    os.makedirs(evidence_screenshot_dir, exist_ok=True)
                    screenshot_path = os.path.join(
                        evidence_screenshot_dir,
                        f"shot_{abs(hash(hit.url)) % 10_000_000}.png",
                    )
                rendered = browser_render(
                    hit.url,
                    engine=(browser_engine or os.getenv("BROWSER_ENGINE") or policy_engine.browser_engine()),
                    screenshot_path=screenshot_path,
                )
                rendered_status = str(rendered.get("status") or "fetch_error")
                if rendered_status == "ok":
                    pages.append(
                        PageEvidence(
                            url=hit.url,
                            final_url=str(rendered.get("final_url") or final_url),
                            status="ok",
                            title=str(rendered.get("title") or hit.title),
                            text=str(rendered.get("text") or "")[:200000],
                            html_len=len(str(rendered.get("html") or "")),
                            used_browser=True,
                            screenshot_path=rendered.get("screenshot_path"),
                        )
                    )
                else:
                    fallback_status = status
                    if fallback_status == "ok" and _text_too_short(text):
                        fallback_status = "empty"
                    pages.append(
                        PageEvidence(
                            url=hit.url,
                            final_url=str(rendered.get("final_url") or final_url),
                            status=fallback_status,
                            title=hit.title,
                            text=text[:200000],
                            html_len=len(html_text),
                            used_browser=False,
                            screenshot_path=None,
                        )
                    )
                continue

            pages.append(
                PageEvidence(
                    url=hit.url,
                    final_url=final_url,
                    status="empty" if status == "ok" and _text_too_short(text) else status,
                    title=hit.title,
                    text=text[:200000],
                    html_len=len(html_text),
                    used_browser=False,
                    screenshot_path=None,
                )
            )
        except Exception as exc:
            pages.append(
                PageEvidence(
                    url=hit.url,
                    final_url=None,
                    status=f"fetch_error:{type(exc).__name__}",
                    title=hit.title,
                    text="",
                    html_len=0,
                    used_browser=False,
                    screenshot_path=None,
                )
            )

    return ResearchResult(
        query=query,
        provider=provider_used,
        hits=hits[: max(1, int(max_hits))],
        pages=pages,
        notes=notes,
        ts_utc=time.time(),
    )


def to_jsonable(result: ResearchResult) -> dict[str, Any]:
    return {
        "query": result.query,
        "provider": result.provider,
        "hits": [asdict(item) for item in result.hits],
        "pages": [asdict(item) for item in result.pages],
        "notes": list(result.notes),
        "ts_utc": result.ts_utc,
    }


def _duckduckgo_html_hits(query: str, *, max_hits: int) -> list[WebHit]:
    from html import unescape

    text = (query or "").strip()
    if not text:
        return []
    request = urllib.request.Request(
        "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(text),
        headers={"User-Agent": "Mozilla/5.0 VOOL-XSEARCH/1.0"},
    )
    with _open_remote(request, timeout=10) as response:
        html_text = response.read().decode("utf-8", errors="ignore")
    if "Unfortunately, bots use DuckDuckGo too." in html_text or "anomaly-modal" in html_text:
        raise RuntimeError("duckduckgo_anomaly_challenge")
    snippet_matches = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', html_text, re.IGNORECASE | re.DOTALL)
    link_matches = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"', html_text, re.IGNORECASE)
    title_matches = re.findall(r'<a[^>]+class="result__a"[^>]*>(.*?)</a>', html_text, re.IGNORECASE | re.DOTALL)
    hits: list[WebHit] = []
    for raw_title, raw_snippet, raw_link in zip(title_matches, snippet_matches, link_matches):  # noqa: B905  -- strict= unsupported on Python 3.9
        resolved_url = _resolve_duckduckgo_result_url(raw_link)
        if not resolved_url:
            continue
        title = re.sub(r"<[^>]+>", "", raw_title).strip()
        snippet = re.sub(r"<[^>]+>", "", raw_snippet).strip()
        hits.append(
            WebHit(
                title=unescape(title),
                url=resolved_url,
                snippet=unescape(snippet),
                engine="duckduckgo_html",
                score=None,
            )
        )
        if len(hits) >= max(1, int(max_hits)):
            break
    return hits


def _browser_search_hits(query: str, *, max_hits: int, timeout_s: float) -> list[WebHit]:
    """Search by driving a real browser, because plain-HTTP scraping cannot return answers.

    Measured 2026-08-17: every keyless HTTP search path in this module returns a DECOY INDEX rather
    than a refusal -- HTTP 200, real HTML, wrong subject ("how many moons does jupiter have" came
    back as dictionary definitions of the word "many"). Real headers, query reformulation, the
    DuckDuckGo html/lite endpoints and Brave were each tried and each failed. Rendering the same
    query in the browser already installed here returns NASA, Wikipedia and the official
    president.go.ke. See tools/web/browser_search.py for the full measurement, including what this
    does NOT fix (Bing serves the decoy to a real browser too).

    The `engine` recorded on each hit names the search engine that answered, so a downstream reader
    can tell a DuckDuckGo render from a Bing render.
    """

    from tools.web.browser_search import browser_search as run_browser_search

    text = (query or "").strip()
    if not text:
        return []
    raw_results = run_browser_search(text, max_results=max_hits, timeout_s=timeout_s)
    return [
        WebHit(
            title=str(r.get("title") or "").strip(),
            url=str(r.get("url") or "").strip(),
            snippet=str(r.get("snippet") or "").strip(),
            engine=str(r.get("engine") or "browser_search"),
            score=None,
        )
        for r in raw_results
        if str(r.get("url") or "").strip()
    ]


def _google_html_hits(query: str, *, max_hits: int) -> list[WebHit]:
    from tools.web.google_html import google_html_search

    text = (query or "").strip()
    if not text:
        return []
    raw_results = google_html_search(text, max_results=max_hits, timeout_s=10.0)
    return [
        WebHit(
            title=str(r.get("title") or "").strip(),
            url=str(r.get("url") or "").strip(),
            snippet=str(r.get("snippet") or "").strip(),
            engine="google_html",
            score=None,
        )
        for r in raw_results
        if str(r.get("url") or "").strip()
    ]


def _resolve_duckduckgo_result_url(raw_href: str) -> str:
    from html import unescape
    from urllib.parse import parse_qs, urlparse

    href = unescape(raw_href or "").strip()
    if not href:
        return ""
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    if query.get("uddg"):
        return urllib.parse.unquote(query["uddg"][0])
    return href
