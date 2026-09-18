"""The SearXNG provider contract: results, failure taxonomy, configuration, authority, fallback.

WHAT WAS ALREADY THERE. `tools/web/searxng_client.py` predates this suite: JSON API, configurable
base URL, bounded timeout, normalised results, and a place in the default provider chain
(`browser_search, searxng, google_html, ddg_instant`) deliberately ahead of the scrapers so a
self-hoster keeps their queries private. This is an audit and repair, not an implementation.

WHAT WAS NOT.

1. The client opened its OWN socket, and `tools/registry.py` registers `web.search` straight onto
   `client.search` while `core/execution/web_tools.py` gates only on the GLOBAL
   `allow_web_fallback()` switch -- so a turn that FORBADE remote fetching still reached the
   endpoint with the user's query. Closed by routing through `core.remote_fetch_policy.open_remote`.
2. Every failure arrived as `HTTPError` or `URLError`, so 429, 500, a refused connection and a
   timeout were indistinguishable and nothing could back off or say "check your URL".
3. One test existed: it mocked `urlopen` and asserted parsing.

NOT A REAL-INSTANCE PROOF, and deliberately so. Running a SearXNG container means pulling and
executing a third-party image on a machine that holds live signing keys, which this project forbids.
Everything below drives a protocol-faithful local fixture: real `urllib` request objects, real
SearXNG JSON response shapes, real `HTTPError`/`URLError`/`TimeoutError` raised at the socket. That
proves the adapter and the authority path; it does not prove interoperability with a live instance,
and it is labelled as a fixture wherever it is cited.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from core.remote_fetch_policy import (
    RemoteFetchRefusedError,
    remote_fetch_attempts,
    remote_fetch_policy_scope,
)
from tools.web.searxng_client import SearXNGClient, SearXNGUnavailableError

ALLOWED = {"allow_remote_fetch": True}
FORBIDDEN = {"allow_remote_fetch": False}

SEARX_JSON = {
    "results": [
        {"title": "Domain Name System", "url": "https://en.wikipedia.org/wiki/DNS",
         "content": "DNS translates names to addresses.", "engine": "wikipedia", "score": 1.0},
        {"title": "How DNS works", "url": "https://cloudflare.com/learning/dns/",
         "content": "A resolver walks the hierarchy.", "engine": "google", "score": 0.8},
        {"title": "RFC 1035", "url": "https://rfc-editor.org/rfc/rfc1035",
         "content": "Domain names - implementation.", "engine": "bing", "score": 0.6},
    ]
}


class _Body:
    def __init__(self, payload) -> None:
        raw = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
        self._body = io.BytesIO(raw.encode() if isinstance(raw, str) else raw)

    def read(self) -> bytes:
        return self._body.read()

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def searx(monkeypatch):
    """A local protocol-faithful fixture. Returns a setter for the socket behaviour and the log of
    request URLs the adapter actually opened."""
    opened: list[str] = []
    behaviour: dict = {"respond": SEARX_JSON}

    def fake_urlopen(request, timeout=None):
        opened.append(str(getattr(request, "full_url", request)))
        action = behaviour["respond"]
        if isinstance(action, Exception):
            raise action
        if callable(action):
            return action(request)
        return _Body(action)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return behaviour, opened


def _search(base_url: str = "http://searx.test:8080", **kw):
    return SearXNGClient(base_url=base_url, timeout_s=3).search("what is dns", **kw)


# =============================================================== POSITIVE (1-5)


def test_1_a_configured_instance_returns_results(searx) -> None:
    with remote_fetch_policy_scope(ALLOWED):
        assert len(_search()) == 3


def test_2_multiple_results_normalise_in_order(searx) -> None:
    with remote_fetch_policy_scope(ALLOWED):
        results = _search()

    assert [r.url for r in results] == [
        "https://en.wikipedia.org/wiki/DNS",
        "https://cloudflare.com/learning/dns/",
        "https://rfc-editor.org/rfc/rfc1035",
    ]


def test_3_title_url_and_snippet_are_preserved(searx) -> None:
    with remote_fetch_policy_scope(ALLOWED):
        first = _search()[0]

    assert first.title == "Domain Name System"
    assert first.url == "https://en.wikipedia.org/wiki/DNS"
    assert first.snippet == "DNS translates names to addresses."


def test_4_provenance_identifies_the_engine_and_score(searx) -> None:
    with remote_fetch_policy_scope(ALLOWED):
        results = _search()

    assert [r.engine for r in results] == ["wikipedia", "google", "bing"]
    assert results[0].score == 1.0


def test_5_the_request_uses_the_json_api_not_html(searx) -> None:
    _, opened = searx
    with remote_fetch_policy_scope(ALLOWED):
        _search()

    assert opened and "format=json" in opened[0]
    assert "/search?" in opened[0]


# =============================================================== FAILURE (6-12)


@pytest.mark.parametrize(
    "raised,reason",
    (
        (urllib.error.URLError(ConnectionRefusedError("refused")), "unreachable"),
        (TimeoutError("timed out"), "timeout"),
        (urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None), "rate_limited"),
        (urllib.error.HTTPError("u", 500, "Server Error", {}, None), "server_error"),
        (urllib.error.HTTPError("u", 503, "Unavailable", {}, None), "server_error"),
        (urllib.error.HTTPError("u", 403, "Forbidden", {}, None), "unauthorized"),
        (urllib.error.HTTPError("u", 404, "Not Found", {}, None), "bad_request"),
    ),
)
def test_6_to_10_each_transport_failure_is_named(searx, raised, reason) -> None:
    behaviour, _ = searx
    behaviour["respond"] = raised
    with remote_fetch_policy_scope(ALLOWED):
        with pytest.raises(SearXNGUnavailableError) as caught:
            _search()

    assert caught.value.reason == reason


def test_11_a_non_json_body_is_reported_as_such(searx) -> None:
    """An HTML login page or proxy notice returning 200 is a misconfigured endpoint, and saying so
    beats reporting an empty result set."""
    behaviour, _ = searx
    behaviour["respond"] = "<html><body>Sign in to continue</body></html>"
    with remote_fetch_policy_scope(ALLOWED):
        with pytest.raises(SearXNGUnavailableError) as caught:
            _search()

    assert caught.value.reason == "invalid_json"


def test_12_an_empty_result_set_is_not_a_failure(searx) -> None:
    behaviour, _ = searx
    behaviour["respond"] = {"results": []}
    with remote_fetch_policy_scope(ALLOWED):
        assert _search() == []


def test_12b_a_malformed_row_does_not_discard_the_good_ones(searx) -> None:
    behaviour, _ = searx
    behaviour["respond"] = {
        "results": [None, "junk", {"no_url": 1},
                    {"title": "A", "url": "https://a.example", "content": "c"}]
    }
    with remote_fetch_policy_scope(ALLOWED):
        results = _search()

    assert [r.url for r in results] == ["https://a.example"]


def test_12c_a_row_with_an_unparseable_score_still_returns(searx) -> None:
    behaviour, _ = searx
    behaviour["respond"] = {"results": [{"title": "A", "url": "https://a.example", "score": "high"}]}
    with remote_fetch_policy_scope(ALLOWED):
        results = _search()

    assert results[0].score is None


# =============================================================== CONFIGURATION (13-15)


def test_13_the_configured_base_url_is_honoured(searx) -> None:
    _, opened = searx
    with remote_fetch_policy_scope(ALLOWED):
        SearXNGClient(base_url="https://search.example.com", timeout_s=3).search("q")

    assert opened[0].startswith("https://search.example.com/search?")


def test_14_a_trailing_slash_does_not_double(searx) -> None:
    _, opened = searx
    with remote_fetch_policy_scope(ALLOWED):
        SearXNGClient(base_url="https://search.example.com/", timeout_s=3).search("q")

    assert "//search?" not in opened[0]


def test_15_the_environment_overrides_the_policy_default(searx, monkeypatch) -> None:
    _, opened = searx
    monkeypatch.setenv("SEARXNG_URL", "http://from-env.test:9999")
    with remote_fetch_policy_scope(ALLOWED):
        SearXNGClient(timeout_s=3).search("q")

    assert opened[0].startswith("http://from-env.test:9999/")


def test_15b_an_empty_query_never_opens_a_socket(searx) -> None:
    _, opened = searx
    with remote_fetch_policy_scope(ALLOWED):
        assert SearXNGClient(base_url="http://searx.test:8080", timeout_s=3).search("   ") == []

    assert opened == []


# =============================================================== AUTHORITY (16-18)


def _call_the_production_tool(query: str):
    from tools.registry import call_tool, load_builtin_tools

    load_builtin_tools()
    return call_tool("web.search", query=query, max_results=3)


@pytest.mark.parametrize(
    "text",
    (
        "Do not search the web. Explain what DNS is.",
        "Do not fetch anything. Count the letters in `SearXNG`.",
        # Mutated wording and sloppy typing -- the contract is the turn's veto, not a sentence.
        "dont search the web pls, just explain what DNS is",
        "NO WEB. what is dns",
        "1. do not use the internet  2. explain dns",
    ),
)
def test_16_and_17_a_forbidden_turn_makes_zero_searxng_calls(searx, text: str) -> None:
    _, opened = searx
    with remote_fetch_policy_scope(FORBIDDEN):
        with pytest.raises(RemoteFetchRefusedError):
            _call_the_production_tool(text)

    assert opened == [], f"a request was issued for a turn that forbade it: {text!r}"


def test_18_an_ordinary_live_request_is_allowed_and_recorded(searx) -> None:
    _, opened = searx
    with remote_fetch_policy_scope(ALLOWED):
        results = _call_the_production_tool("what is dns")

        assert len(results) == 3
        recorded = remote_fetch_attempts()

    assert opened, "the allowed turn never reached the endpoint"
    assert len(recorded) == 1
    assert "format=json" in recorded[0]["url"]


# =============================================================== FALLBACK (19-20)


def test_19_a_failing_instance_leaves_the_chain_free_to_continue(searx) -> None:
    """The chain catches per provider and moves on, so the contract this lane owes is a clean,
    named failure rather than a silent empty result that would look like "no hits anywhere"."""
    behaviour, _ = searx
    behaviour["respond"] = urllib.error.HTTPError("u", 502, "Bad Gateway", {}, None)
    with remote_fetch_policy_scope(ALLOWED):
        with pytest.raises(SearXNGUnavailableError) as caught:
            _search()

    assert caught.value.reason == "server_error"
    assert "502" in caught.value.detail


def test_20_a_successful_search_opens_exactly_one_connection(searx) -> None:
    """No unnecessary retry or second provider hop while this one is answering."""
    _, opened = searx
    with remote_fetch_policy_scope(ALLOWED):
        assert _search()

    assert len(opened) == 1


# =============================================================== SABOTAGE


def test_sabotage_a_private_socket_reopens_the_authority_bypass(searx, monkeypatch) -> None:
    _, opened = searx
    import tools.web.searxng_client as client_module

    monkeypatch.setattr(client_module, "open_remote",
                        lambda request, *, timeout: urllib.request.urlopen(request, timeout=timeout))
    with remote_fetch_policy_scope(FORBIDDEN):
        _call_the_production_tool("Do not search the web. Explain what DNS is.")

    assert opened, "sabotage did not bite: the client no longer opens its own socket"


def test_sabotage_collapsing_the_taxonomy_makes_429_and_500_identical(searx, monkeypatch) -> None:
    """Revert to one undifferentiated failure and no backoff decision can be made from it."""
    import tools.web.searxng_client as client_module

    reasons = set()
    for code in (429, 500):
        behaviour, _ = searx
        behaviour["respond"] = urllib.error.HTTPError("u", code, "e", {}, None)
        monkeypatch.setattr(
            client_module, "SearXNGUnavailableError",
            type("Flat", (RuntimeError,), {"reason": "searxng_failed", "detail": ""}),
        )
        with remote_fetch_policy_scope(ALLOWED):
            try:
                _search()
            except RuntimeError as exc:
                reasons.add(getattr(exc, "reason", "searxng_failed"))

    assert reasons == {"searxng_failed"}, "the sabotage did not flatten the taxonomy"
