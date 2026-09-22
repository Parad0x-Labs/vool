"""The browser-backed search provider, driven against real captured DOM -- never the network.

Every fixture here is a verbatim DOM captured on 2026-08-17 from the same headless-Chrome command
the engine issues, gzipped only so it fits in the tree. Nothing in this file opens a socket: the
subprocess call itself is replaced, so the REAL engine code (decode, block-page classification,
title/link extraction, per-engine result extraction) runs end to end against real markup.

The corpus is ordinary use first -- three everyday questions -- and then the failures that the
measurements actually turned up.
"""

from __future__ import annotations

import gzip
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from core import policy_engine
from tools.browser import browser_render as br
from tools.web import browser_search as bs
from tools.web import http_fetch, web_research
from tools.web.browser_identity import DESKTOP_USER_AGENT

_FIXTURES = Path(__file__).parent / "fixtures"
_DDG_FIXTURE = _FIXTURES / "duckduckgo_rendered_moons_of_jupiter.html.gz"
_BING_FIXTURE = _FIXTURES / "bing_rendered_moons_of_jupiter.html.gz"

_QUERY = "how many moons does jupiter have"


def _dom(path: Path) -> str:
    with gzip.open(path, "rt", encoding="utf-8", errors="ignore") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def ddg_dom() -> str:
    return _dom(_DDG_FIXTURE)


@pytest.fixture(scope="module")
def bing_dom() -> str:
    return _dom(_BING_FIXTURE)


@pytest.fixture
def offline_browser(monkeypatch: pytest.MonkeyPatch):
    """Replace the subprocess call so the real engine runs over a captured DOM, with no network."""

    def _install(dom: str, *, returncode: int = 0):
        def _fake_run(argv, *, timeout_s):
            # (returncode, stdout, stderr) — the engine captures the browser's stderr for its
            # typed empty-DOM diagnosis; the fake speaks the same 3-tuple seam.
            return returncode, dom.encode("utf-8"), b""

        monkeypatch.setattr(br, "_run_chrome", _fake_run)
        monkeypatch.setattr(br, "find_browser_binary", lambda: "/fake/chrome")

    return _install


# --------------------------------------------------------------------------------------------
# 1. Ordinary use: a rendered results page becomes (title, url, snippet)
# --------------------------------------------------------------------------------------------


def test_the_rendered_duckduckgo_dom_yields_answer_bearing_results(ddg_dom: str) -> None:
    results = bs.extract_duckduckgo_results(ddg_dom)

    assert len(results) >= 8, len(results)
    urls = [row["url"] for row in results]
    assert "https://science.nasa.gov/jupiter/jupiter-moons/" in urls, urls
    assert "https://en.wikipedia.org/wiki/Moons_of_Jupiter" in urls, urls

    # Every row is a usable triple, not a URL with two empty strings beside it.
    for row in results:
        assert row["title"], row
        assert row["url"].startswith("https://"), row
        assert row["snippet"], row

    # The answer is carried in the snippet itself -- this is what the old HTTP path never returned.
    blob = " ".join(row["snippet"] for row in results)
    assert "95" in blob, blob[:400]


def test_each_snippet_stays_with_its_own_result(ddg_dom: str) -> None:
    # Association is by containment, not by zipping three independent scans together. A zip
    # silently shifts every snippet onto the wrong link the moment one result lacks one, and each
    # field stays individually plausible, so nothing downstream can notice.
    by_url = {row["url"]: row for row in bs.extract_duckduckgo_results(ddg_dom)}

    nasa = by_url["https://science.nasa.gov/jupiter/jupiter-moons/"]
    wiki = by_url["https://en.wikipedia.org/wiki/Moons_of_Jupiter"]

    assert "NASA" in nasa["title"], nasa
    assert "Wikipedia" in wiki["title"], wiki
    assert "Galilean moons" in wiki["snippet"], wiki


def test_the_search_engine_itself_never_appears_as_a_source(ddg_dom: str, bing_dom: str) -> None:
    # A result pointing back at duckduckgo.com or bing.com would be cited as its own evidence, and
    # the credibility scorer would read the search engine as the source domain.
    for row in bs.extract_duckduckgo_results(ddg_dom) + bs.extract_bing_results(bing_dom):
        assert "duckduckgo.com" not in row["url"], row
        assert "bing.com" not in row["url"], row


# --------------------------------------------------------------------------------------------
# 2. Bing's base64 redirector
# --------------------------------------------------------------------------------------------


def test_bing_result_urls_are_decoded_out_of_the_redirector(bing_dom: str) -> None:
    results = bs.extract_bing_results(bing_dom)

    assert len(results) >= 5, len(results)
    for row in results:
        assert "/ck/a" not in row["url"], row
        assert row["url"].startswith("http"), row
    assert "https://www.merriam-webster.com/dictionary/many" in [row["url"] for row in results]


def test_a_base64_payload_needing_padding_still_decodes() -> None:
    # Bing strips the '=' padding, so every payload whose length is not a multiple of four has to
    # be re-padded before decoding. This one is 43 characters -- 3 mod 4.
    href = (
        "https://www.bing.com/ck/a?!&&p=abc"
        "&u=a1aHR0cHM6Ly9zY2llbmNlLm5hc2EuZ292L2p1cGl0ZXIvanVwaXRlci1tb29ucy8"
        "&ntb=1"
    )
    assert bs.unwrap_bing_redirect(href) == "https://science.nasa.gov/jupiter/jupiter-moons/"


@pytest.mark.parametrize(
    "href",
    [
        "https://www.bing.com/ck/a?!&&u=a1!!!!not-base64-at-all!!!!&ntb=1",
        "https://www.bing.com/ck/a?!&&u=a1&ntb=1",
        "https://www.bing.com/ck/a?nope=1",
        "https://www.bing.com/ck/a?u=a1aGVsbG8",  # decodes, but not to a URL
        "https://www.bing.com/ck/a?u=" + "a1" + "%" * 40,
    ],
)
def test_a_malformed_redirector_is_dropped_rather_than_raised(href: str) -> None:
    assert bs.unwrap_bing_redirect(href) == ""


def test_a_plain_url_passes_through_the_unwrapper_untouched() -> None:
    assert bs.unwrap_bing_redirect("https://example.com/a?b=c") == "https://example.com/a?b=c"
    assert bs.unwrap_bing_redirect("") == ""
    assert bs.unwrap_bing_redirect(None) == ""  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------
# 3. The measured limitation, recorded so it cannot be quietly forgotten
# --------------------------------------------------------------------------------------------


def test_bing_serves_the_decoy_index_even_to_a_real_browser(bing_dom: str) -> None:
    """A real browser does NOT repair Bing, and this file is where that is written down.

    This DOM is Bing's answer to "how many moons does jupiter have", rendered through the same
    headless Chrome that makes DuckDuckGo return NASA and Wikipedia. Bing returned dictionary
    entries for the word "many". That is why DuckDuckGo is primary and Bing is only the fallback
    for when DuckDuckGo declines to answer -- the ordering is a measurement, not a preference.
    """

    urls = [row["url"] for row in bs.extract_bing_results(bing_dom)]

    assert any("dictionary" in url or "thesaurus" in url for url in urls), urls
    assert not any("nasa.gov" in url or "wikipedia.org/wiki/Moons" in url for url in urls), urls


# --------------------------------------------------------------------------------------------
# 4. The block-page classifier
# --------------------------------------------------------------------------------------------


def test_a_full_results_page_is_not_called_a_captcha(ddg_dom: str) -> None:
    # The string `anomaly-modal` is a CSS class in DuckDuckGo's bundled stylesheet and appears on
    # EVERY page it serves. Scanning the raw markup for it condemned every successful render.
    text = http_fetch.strip_html(ddg_dom)
    assert len(text) > 2500, len(text)
    assert br._classify_rendered_content(ddg_dom, text) == "ok"


def test_a_real_block_page_is_still_caught() -> None:
    assert (
        br._classify_rendered_content(
            '<div class="anomaly-modal__title">Unfortunately, bots use DuckDuckGo too.</div>',
            "Select all squares containing a duck.",
        )
        == "captcha"
    )


# --------------------------------------------------------------------------------------------
# 5. The engine: shapes, failures, and never raising
# --------------------------------------------------------------------------------------------


def test_the_engine_returns_the_same_shape_the_playwright_path_returns(
    ddg_dom: str, offline_browser
) -> None:
    offline_browser(ddg_dom)

    result = br.chrome_render("https://duckduckgo.com/?q=" + _QUERY.replace(" ", "+"))

    assert result["status"] == "ok"
    assert set(result) == {
        "status", "final_url", "title", "text", "html", "links", "screenshot_path",
    }, sorted(result)
    assert result["title"] == "how many moons does jupiter have at DuckDuckGo", result["title"]
    assert result["html"].startswith("<"), result["html"][:40]
    assert any("nasa.gov" in link for link in result["links"]), result["links"][:10]


def test_no_browser_on_the_machine_reports_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(br, "find_browser_binary", lambda: None)

    result = br.chrome_render("https://example.com/")

    assert result == {"status": "missing_dependency", "final_url": "https://example.com/"}


def test_browser_render_keeps_its_missing_dependency_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    # Past the policy gate, with no Playwright importable and no browser on disk, the public entry
    # point must return exactly what it returned before this engine existed.
    monkeypatch.delenv("PLAYWRIGHT_ENABLED", raising=False)
    monkeypatch.setattr(policy_engine, "playwright_enabled", lambda: True)
    monkeypatch.setitem(sys.modules, "playwright", None)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
    monkeypatch.setattr(br, "find_browser_binary", lambda: None)

    assert br.browser_render("https://example.com/") == {
        "status": "missing_dependency",
        "final_url": "https://example.com/",
    }


def test_the_policy_gate_still_wins_over_the_new_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PLAYWRIGHT_ENABLED", raising=False)
    monkeypatch.setattr(policy_engine, "playwright_enabled", lambda: False)
    monkeypatch.setattr(br, "find_browser_binary", lambda: "/fake/chrome")

    def _must_not_run(argv, *, timeout_s):
        raise AssertionError("the policy gate was bypassed")

    monkeypatch.setattr(br, "_run_chrome", _must_not_run)

    assert br.browser_render("https://example.com/")["status"] == "disabled_by_policy"


@pytest.mark.parametrize(
    ("boom", "expected_prefix"),
    [
        (subprocess.TimeoutExpired(cmd="chrome", timeout=1), "browser_timeout"),
        (OSError("no such binary"), "browser_error"),
        (ValueError("bad argv"), "browser_error"),
    ],
)
def test_a_browser_that_fails_returns_rather_than_raises(
    monkeypatch: pytest.MonkeyPatch, boom: Exception, expected_prefix: str
) -> None:
    monkeypatch.setattr(br, "find_browser_binary", lambda: "/fake/chrome")

    def _raise(argv, *, timeout_s):
        raise boom

    monkeypatch.setattr(br, "_run_chrome", _raise)

    result = br.chrome_render("https://example.com/")
    assert result["status"].startswith(expected_prefix), result
    assert result["final_url"] == "https://example.com/"


def test_an_empty_dom_is_a_failure_not_an_empty_success(monkeypatch: pytest.MonkeyPatch) -> None:
    # A clean exit code with nothing on stdout is a failed render. Reporting it as "ok" with empty
    # text would let a blank page be cited as evidence.
    monkeypatch.setattr(br, "find_browser_binary", lambda: "/fake/chrome")
    monkeypatch.setattr(br, "_run_chrome", lambda argv, *, timeout_s: (0, b"   \n  ", b""))

    assert br.chrome_render("https://example.com/")["status"].startswith("browser_empty_dom")


def test_the_timeout_kills_the_whole_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chrome forks helpers; killing only the direct child leaks them until the box runs dry."""

    killed: list[int] = []

    class _FakeProcess:
        pid = 4242
        returncode = 0

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="chrome", timeout=timeout or 1)

        def kill(self) -> None:
            killed.append(-1)

    monkeypatch.setattr(br.subprocess, "Popen", lambda *a, **k: _FakeProcess())
    monkeypatch.setattr(br.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(br.os, "killpg", lambda pgid, sig: killed.append(pgid))

    with pytest.raises(subprocess.TimeoutExpired):
        br._run_chrome(["/fake/chrome"], timeout_s=1.0)

    assert killed == [4242], killed


# --------------------------------------------------------------------------------------------
# 6. The provider, end to end over a captured DOM
# --------------------------------------------------------------------------------------------


def test_the_provider_returns_real_hits_from_a_rendered_page(ddg_dom: str, offline_browser) -> None:
    offline_browser(ddg_dom)

    hits = web_research._browser_search_hits(_QUERY, max_hits=5, timeout_s=20.0)

    assert len(hits) == 5, hits
    assert hits[0].engine == "browser_search:duckduckgo", hits[0]
    assert any("nasa.gov" in hit.url for hit in hits), [h.url for h in hits]
    # The provider's own quality gate must accept these -- an accepted-by-nobody result set is the
    # same as no result set.
    accepted, reason = web_research._accept_search_hits(_QUERY, "browser_search", hits)
    assert accepted, reason


def test_bing_is_tried_only_after_duckduckgo_declines(bing_dom: str) -> None:
    seen: list[str] = []

    def _render(url: str, **kwargs):
        seen.append(url)
        if "duckduckgo" in url:
            return {"status": "captcha", "final_url": url}
        return {"status": "ok", "final_url": url, "html": bing_dom}

    results = bs.browser_search(_QUERY, max_results=4, render=_render)

    assert len(seen) == 2 and "duckduckgo" in seen[0] and "bing.com" in seen[1], seen
    assert results and all(row["engine"] == "browser_search:bing" for row in results), results


def test_every_engine_failing_yields_nothing_rather_than_a_fabricated_hit() -> None:
    def _render(url: str, **kwargs):
        return {"status": "captcha", "final_url": url}

    assert bs.browser_search(_QUERY, render=_render) == []
    assert bs.browser_search("", render=_render) == []


# --------------------------------------------------------------------------------------------
# 7. Position in the provider chain
# --------------------------------------------------------------------------------------------


def test_the_browser_provider_is_permitted_and_leads_the_default_order() -> None:
    assert "browser_search" in policy_engine.allowed_web_engines()
    assert policy_engine.web_provider_order()[0] == "browser_search"


def test_the_browser_provider_runs_before_the_scrapers_that_return_decoys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEB_SEARCH_PROVIDER_ORDER", raising=False)
    monkeypatch.setattr(web_research, "_browser_search_available", lambda: True)

    order = web_research._provider_order()

    assert order[0] == "browser_search", order
    # The HTTP providers stay, below it, as fallback.
    assert "google_html" in order and "searxng" in order, order
    assert order.index("browser_search") < order.index("google_html"), order


def test_a_machine_without_a_browser_behaves_exactly_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEB_SEARCH_PROVIDER_ORDER", raising=False)
    monkeypatch.setattr(web_research, "_browser_search_available", lambda: False)

    assert web_research._provider_order() == ["searxng", "google_html", "ddg_instant"]


def test_policy_can_switch_the_browser_provider_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # web.allow_browser_fallback is the pre-existing "may we use a browser at all" switch, and it
    # has to remain a real off switch for this provider too.
    monkeypatch.delenv("ALLOW_BROWSER_FALLBACK", raising=False)
    monkeypatch.setattr(policy_engine, "allow_browser_fallback", lambda: False)

    assert web_research._browser_search_available() is False


# --------------------------------------------------------------------------------------------
# 8. The plain-HTTP door presents a browser identity
# --------------------------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body
        self.headers = {"Content-Encoding": "identity"}

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def geturl(self) -> str:
        return "https://example.com/"

    def read(self, size: int) -> bytes:
        return self._body[:size]


def test_the_plain_fetch_no_longer_announces_itself_as_a_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    # A self-identifying bot User-Agent is not answered with a refusal -- it is answered with a
    # decoy index, which is indistinguishable from a bad ranking and so was never noticed.
    captured: list[urllib.request.Request] = []

    def _fake_urlopen(request, timeout=None):
        captured.append(request)
        return _FakeResponse(b"<html><body>" + b"content " * 500 + b"</body></html>")

    monkeypatch.setattr(http_fetch.urllib.request, "urlopen", _fake_urlopen)

    # R2b2b: the page transport goes through the ONE door, which refuses
    # fetches with no active ledger -- production reads pages inside a turn's
    # fetch scope, and so does this test (stubbed socket, no network).
    from core.remote_fetch_policy import remote_fetch_policy_scope

    with remote_fetch_policy_scope({"surface": "api"}):
        result = http_fetch.http_fetch_text("https://example.com/")

    assert result["status"] == "ok", result
    headers = {key.lower(): value for key, value in captured[0].header_items()}
    assert headers["user-agent"] == DESKTOP_USER_AGENT
    assert "VOOL" not in headers["user-agent"]
    assert headers["accept-language"].startswith("en-US")


# --------------------------------------------------------------------------------------------
# 9. Sabotage: each arm must name the cause, and the controls must stay green
# --------------------------------------------------------------------------------------------


def test_sabotage_removing_the_block_page_length_guard_returns_the_measured_zero_hits(
    ddg_dom: str, offline_browser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAUSE: classifying a full page by scanning its raw markup for block-page markers.

    `anomaly-modal` is a CSS class in DuckDuckGo's stylesheet, present on every page it serves.
    Without the length guard the classifier finds it in a page of ten real results and reports a
    captcha, the engine discards the render, and the provider returns zero hits with no error
    recorded anywhere -- exactly what was measured before the guard was added.
    """

    offline_browser(ddg_dom)

    # Control: the guard in place.
    assert len(web_research._browser_search_hits(_QUERY, max_hits=5, timeout_s=20.0)) == 5

    # Sabotage: make every page count as short enough to be a block notice, which is what
    # scanning the whole document amounts to.
    monkeypatch.setattr(br, "_BLOCK_PAGE_MAX_CHARS", 10**9)

    assert br.chrome_render("https://duckduckgo.com/?q=x")["status"] == "captcha"
    assert web_research._browser_search_hits(_QUERY, max_hits=5, timeout_s=20.0) == []


def test_sabotage_neutralising_the_chrome_engine_returns_the_measured_failure(
    monkeypatch: pytest.MonkeyPatch, ddg_dom: str, offline_browser
) -> None:
    """CAUSE: no browser engine. The whole capability rests on locating a browser already on disk.

    With the locator neutralised the provider yields nothing, the engine reports the pre-existing
    `missing_dependency` shape, and the chain falls back to precisely the three HTTP engines it
    used before -- the state in which the decoy index was measured.
    """

    offline_browser(ddg_dom)
    assert len(web_research._browser_search_hits(_QUERY, max_hits=5, timeout_s=20.0)) == 5

    monkeypatch.setattr(br, "find_browser_binary", lambda: None)

    assert br.chrome_render("https://duckduckgo.com/?q=x")["status"] == "missing_dependency"
    assert web_research._browser_search_hits(_QUERY, max_hits=5, timeout_s=20.0) == []
    monkeypatch.delenv("WEB_SEARCH_PROVIDER_ORDER", raising=False)
    assert web_research._provider_order() == ["searxng", "google_html", "ddg_instant"]


def test_sabotage_dropping_the_bing_decoder_strips_every_bing_result(
    bing_dom: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CAUSE: handing the raw redirector through instead of decoding it.

    Every Bing result URL is a `bing.com/ck/a` link. Undecoded, each one is filtered out as the
    search engine citing itself, so the Bing engine contributes nothing at all.
    """

    assert len(bs.extract_bing_results(bing_dom)) >= 5

    monkeypatch.setattr(bs, "unwrap_bing_redirect", lambda href: href)

    assert bs.extract_bing_results(bing_dom) == []
