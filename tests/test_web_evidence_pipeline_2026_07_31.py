"""Regression tests for the 2026-07-31 "the search worked and the answer was still ungrounded" P0.

With the provider chain fixed, live turns still came back with things like:

    "The search results don't include the exact population figure - they only list the source
     domains."
    "the summary is garbled"

The searches HAD run and the hits were good. Three separate defects between the engine's results
and the model's prompt threw the answer away before it got there.

1. `_best_summary` preferred the fetched page's first 280 characters over the engine's snippet.
   The engine picks its snippet FOR THE QUERY; the top of a real page is the navigation bar. A hit
   whose snippet read "Iceland has a total population of 402,329" was summarised for the model as
   "Iceland Population 2026 World Population Review Data by Location chevron Data by Location
   Browse stats by country" -- so the model's refusal was correct on the evidence it was handed.

2. `http_fetch_text` never looked at Content-Encoding. python.org answers `Content-Encoding: gzip`
   to a request that sends no Accept-Encoding at all; decoding those bytes as UTF-8 does not raise,
   it produces mojibake, and the mojibake was passed to the model as the page's content.

3. The captcha / login-wall check ran against the WHOLE document. `_LOGIN_RE` matches "sign in",
   which is in the header of most sites on the web, and `_CAPTCHA_RE` matches the word anywhere --
   so every en.wikipedia.org article came back as `status="captcha"` with empty text. Measured: the
   Sydney Harbour Bridge article went from 0 characters to 99,896.
"""

from __future__ import annotations

import gzip
import zlib

import pytest

from retrieval.web_adapter import _best_summary, _looks_like_binary_noise
from tools.web import http_fetch
from tools.web.web_research import WebHit


class _Page:
    def __init__(self, text: str) -> None:
        self.text = text


def _hit(*, snippet: str = "", title: str = "T") -> WebHit:
    return WebHit(title=title, url="https://example.com/x", snippet=snippet, engine="google_html", score=None)


# --------------------------------------------------------------------------
# 1. The snippet answers the query; the top of the page does not
# --------------------------------------------------------------------------

def test_the_query_targeted_snippet_beats_the_pages_navigation_bar() -> None:
    chrome = (
        "Iceland Population 2026 World Population Review Data by Location chevron Data by Location "
        "Browse stats by country, state, or city. Continents Countries World Cities US States"
    )
    answer = "Iceland By the Numbers As of 2026, Iceland has a total population of 402,329."
    summary = _best_summary(_hit(snippet=answer), _Page(chrome))
    assert "402,329" in summary
    assert "chevron" not in summary


def test_page_text_is_still_used_when_the_engine_gave_no_snippet() -> None:
    prose = (
        "The Sydney Harbour Bridge is a steel through arch bridge in Sydney, New South Wales, "
        "Australia, and it opened to the public on 19 March 1932 after eight years of construction."
    )
    summary = _best_summary(_hit(snippet=""), _Page(prose))
    assert "19 March 1932" in summary


def test_undecoded_bytes_are_never_offered_as_a_summary() -> None:
    # Exactly what the old code produced: a gzip body decoded as UTF-8 with errors ignored. This is
    # generated rather than pasted so the test measures the real artifact, not a typed impression
    # of one -- a hand-written "garbage" string is mostly ASCII and would not reproduce the bug.
    garbage = gzip.compress(
        b"<html><body>" + b"The Sydney Harbour Bridge opened on 19 March 1932. " * 60 + b"</body></html>",
    ).decode("utf-8", errors="ignore")
    assert _looks_like_binary_noise(garbage) is True
    # With no snippet and unusable page text, fall back to the title rather than pass mojibake on.
    summary = _best_summary(_hit(snippet="", title="Download Python"), _Page(garbage))
    assert summary == "Download Python"


def test_ordinary_prose_is_not_mistaken_for_binary_noise() -> None:
    prose = (
        "Samuel Harris Altman (born April 22, 1985) is an American entrepreneur and investor who "
        "has been the chief executive officer of the artificial intelligence company OpenAI."
    )
    assert _looks_like_binary_noise(prose) is False


# --------------------------------------------------------------------------
# 2. Compressed bodies
# --------------------------------------------------------------------------

class _Resp:
    def __init__(self, body: bytes, *, encoding: str = "", url: str = "https://example.com/p") -> None:
        self._body = body
        self.headers = {"Content-Encoding": encoding} if encoding else {}
        self._url = url

    def geturl(self) -> str:
        return self._url

    def read(self, n: int = -1) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


_ARTICLE = (
    "<html><body><p>" + ("The Sydney Harbour Bridge opened on 19 March 1932. " * 60) + "</p></body></html>"
).encode("utf-8")


@pytest.fixture(autouse=True)
def _fetch_under_a_turn_scope(monkeypatch) -> None:
    """R2b2b: the page transport goes through the ONE door, and the door
    refuses any fetch with no active ledger. Production reads pages inside a
    turn's fetch scope; these tests now do too (the stubbed socket still
    answers; nothing here reaches a network)."""
    from core.remote_fetch_policy import remote_fetch_policy_scope

    original = http_fetch.http_fetch_text

    def _scoped(*args, **kwargs):
        with remote_fetch_policy_scope({"surface": "api"}):
            return original(*args, **kwargs)

    monkeypatch.setattr(http_fetch, "http_fetch_text", _scoped)


def test_a_gzip_body_is_decompressed_not_handed_over_as_mojibake(monkeypatch) -> None:
    monkeypatch.setattr(
        http_fetch.urllib.request, "urlopen",
        lambda req, timeout=None: _Resp(gzip.compress(_ARTICLE), encoding="gzip"),
    )
    result = http_fetch.http_fetch_text("https://www.python.org/downloads/")
    assert result["status"] == "ok"
    assert "19 March 1932" in result["text"]


def test_a_truncated_gzip_body_still_yields_what_arrived(monkeypatch) -> None:
    # The body is read under a byte cap, so a compressed stream is normally cut mid-block.
    # gzip.decompress() rejects the whole page in that case; a decompressobj returns the prefix.
    varied = ("<html><body>" + " ".join(
        f"Paragraph {i} records that the bridge opened on 19 March 1932 in Sydney." for i in range(400)
    ) + "</body></html>").encode("utf-8")
    full = gzip.compress(varied)
    monkeypatch.setattr(
        http_fetch.urllib.request, "urlopen",
        lambda req, timeout=None: _Resp(full[: int(len(full) * 0.9)], encoding="gzip"),
    )
    result = http_fetch.http_fetch_text("https://example.com/p")
    assert result["status"] == "ok"
    # A prefix, not an exception and not an empty page.
    assert "19 March 1932" in result["text"]
    assert len(result["text"]) > 1000


def test_a_deflate_body_is_decompressed(monkeypatch) -> None:
    monkeypatch.setattr(
        http_fetch.urllib.request, "urlopen",
        lambda req, timeout=None: _Resp(zlib.compress(_ARTICLE), encoding="deflate"),
    )
    assert "19 March 1932" in http_fetch.http_fetch_text("https://example.com/p")["text"]


def test_an_encoding_we_cannot_decode_reports_that_instead_of_emitting_garbage(monkeypatch) -> None:
    pytest.importorskip  # noqa: B018 - documented: brotli is not installed here on purpose
    monkeypatch.setattr(
        http_fetch.urllib.request, "urlopen",
        lambda req, timeout=None: _Resp(b"\x1b\x2a\x00\xff not really brotli", encoding="br"),
    )
    result = http_fetch.http_fetch_text("https://example.com/p")
    assert result["status"] == "unreadable_encoding"
    assert result["text"] == ""


def test_an_uncompressed_body_is_unaffected(monkeypatch) -> None:
    monkeypatch.setattr(
        http_fetch.urllib.request, "urlopen",
        lambda req, timeout=None: _Resp(_ARTICLE),
    )
    assert "19 March 1932" in http_fetch.http_fetch_text("https://example.com/p")["text"]


# --------------------------------------------------------------------------
# 3. Block-page detection must not eat real articles
# --------------------------------------------------------------------------

def test_a_long_article_that_merely_mentions_a_login_is_still_readable(monkeypatch) -> None:
    # Every Wikipedia article carries "Sign in"/"Log in" chrome, and this check discarded all of
    # them -- the single most useful grounding source on the web, returned as empty text.
    article = (
        "<html><body><nav>Sign in Log in</nav><p>"
        + ("The Sydney Harbour Bridge opened on 19 March 1932. " * 60)
        + "</p></body></html>"
    ).encode("utf-8")
    monkeypatch.setattr(http_fetch.urllib.request, "urlopen", lambda req, timeout=None: _Resp(article))
    result = http_fetch.http_fetch_text("https://en.wikipedia.org/wiki/Sydney_Harbour_Bridge")
    assert result["status"] == "ok"
    assert "19 March 1932" in result["text"]


def test_a_long_article_that_mentions_captcha_is_still_readable(monkeypatch) -> None:
    article = (
        "<html><body><p>A captcha is a challenge-response test used in computing. "
        + ("It distinguishes humans from software agents. " * 60)
        + "</p></body></html>"
    ).encode("utf-8")
    monkeypatch.setattr(http_fetch.urllib.request, "urlopen", lambda req, timeout=None: _Resp(article))
    assert http_fetch.http_fetch_text("https://en.wikipedia.org/wiki/CAPTCHA")["status"] == "ok"


def test_a_real_block_page_is_still_caught(monkeypatch) -> None:
    # A genuine block page is short and is almost entirely the notice itself.
    block = b"<html><body><h1>Are you human?</h1><p>Please complete the captcha to continue.</p></body></html>"
    monkeypatch.setattr(http_fetch.urllib.request, "urlopen", lambda req, timeout=None: _Resp(block))
    result = http_fetch.http_fetch_text("https://example.com/blocked")
    assert result["status"] == "captcha"
    assert result["text"] == ""


def test_a_real_login_wall_is_still_caught(monkeypatch) -> None:
    wall = b"<html><body><h1>Sign in to continue</h1><p>Enter your password.</p></body></html>"
    monkeypatch.setattr(http_fetch.urllib.request, "urlopen", lambda req, timeout=None: _Resp(wall))
    assert http_fetch.http_fetch_text("https://example.com/login")["status"] == "login_wall"
