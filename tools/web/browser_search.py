"""General-web search that runs the query through a real browser instead of scraping HTML.

WHY THIS EXISTS (measured 2026-08-17, all four probes on this machine)
----------------------------------------------------------------------
Plain-HTTP search scraping does not return answers any more, and it does not fail loudly -- it
returns a DECOY INDEX: HTTP 200, real HTML, wrong subject. Against Bing with the old bot
User-Agent:

    "how many moons does jupiter have"    -> dictionary definitions of the word "many"
    "boiling point of ethanol in celsius" -> German Google Chrome help pages
    "who is the president of Kenya"       -> US presidents
    "Garmin Fenix 7 weight battery life"  -> garmin.com homepages, Alza.cz, Wikipedia "Garmin"

Four separate repairs were tried and measured, and none of them fixed it:

  1. a real desktop User-Agent + Accept-Language + `&mkt=en-US` -- removed the geographic scatter
     (no more German help pages, no more Czech retailer) but still returned brand homepages;
  2. query reformulation, four variants including `site:` -- no change;
  3. DuckDuckGo's html/ and lite/ endpoints -- the anti-bot challenge page, 8 out of 8;
  4. Brave's HTML endpoint -- HTTP 429.

So the defect is not in the headers, not in the query, and not in the choice of engine. Keyless
plain-HTTP scraping cannot return relevant search results at all, and the failure mode is
indistinguishable from a bad ranking, which is why it survived this long.

Driving the browser that is ALREADY on this machine does fix it. Same three queries, rendered
through `--headless=new --dump-dom` against DuckDuckGo:

    "how many moons does jupiter have"    -> science.nasa.gov/jupiter/jupiter-moons/,
                                             en.wikipedia.org/wiki/Moons_of_Jupiter, and the
                                             snippet itself carries "Jupiter has 95 ... moons"
    "who is the president of Kenya"       -> en.wikipedia.org/wiki/William_Ruto, president.go.ke
                                             (the official site), britannica.com/biography/...
    "Garmin Fenix 7 weight battery life"  -> www8.garmin.com/manuals/.../Specifications (the
                                             official spec page), mobilespecs.net, real reviews

WHAT THIS DOES NOT FIX
----------------------
Bing serves the SAME decoy index to a real headless browser: rendering "how many moons does
jupiter have" through Chrome against bing.com still returned Cambridge Dictionary and
Merriam-Webster entries for the word "many". Bing is kept as a second engine because it answers
when DuckDuckGo rate-limits, not because a browser repaired it. DuckDuckGo is primary and that
ordering is load-bearing.

EXTRACTION NOTE
---------------
DuckDuckGo's rendered markup uses hashed CSS class names that rotate (`Rn_JXVtoPVAFyGkcaXyK`),
so every selector here keys on `data-testid` / `data-result` attributes, which are stable and are
what DuckDuckGo's own tests use. Bing wraps every result URL in a `bing.com/ck/a?...&u=a1<base64>`
redirector that has to be decoded, or the model is handed "bing.com" as its source domain.
"""

from __future__ import annotations

import base64
import binascii
import html as html_module
import re
import urllib.parse
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any

from tools.browser.browser_render import chrome_render

# Tags that never have a closing tag. Counting them into the element depth would desynchronize the
# container tracking within a few hundred bytes of a 250 KB document.
_VOID_TAGS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }
)
_HEADING_TAGS = frozenset({"h1", "h2", "h3"})

_WHITESPACE_RE = re.compile(r"\s+")


def _collapse(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text or "").strip()


class _ResultDomParser(HTMLParser):
    """Pull (title, url, snippet) out of a rendered results page, one result container at a time.

    Association is by CONTAINMENT, not by document order. Zipping three independent regex passes
    over titles, links and snippets -- the shape the older scrapers use -- silently pairs the wrong
    snippet with the wrong link the moment one result is missing a snippet, and that mispairing is
    invisible downstream because every field is individually plausible.
    """

    def __init__(
        self,
        *,
        is_container: Callable[[str, dict[str, str]], bool],
        is_title_anchor: Callable[[str, dict[str, str], bool], bool],
        is_snippet: Callable[[str, dict[str, str]], bool],
        resolve_url: Callable[[str], str],
    ) -> None:
        super().__init__(convert_charrefs=True)
        self._is_container = is_container
        self._is_title_anchor = is_title_anchor
        self._is_snippet = is_snippet
        self._resolve_url = resolve_url
        self._container_depth = 0
        self._heading_depth = 0
        self._capture: str | None = None
        self._capture_depth = 0
        self._buf: list[str] = []
        self.rows: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        void = tag in _VOID_TAGS
        if self._capture is not None:
            if not void:
                self._capture_depth += 1
            return

        attr = {key.lower(): (value or "") for key, value in attrs}

        if self._container_depth > 0:
            if not void:
                self._container_depth += 1
                if tag in _HEADING_TAGS or self._heading_depth > 0:
                    self._heading_depth += 1
            if void:
                return
            if self._is_title_anchor(tag, attr, self._heading_depth > 0):
                resolved = self._resolve_url(attr.get("href", ""))
                self.rows.append({"title": "", "url": resolved, "snippet": ""})
                self._capture = "title"
                self._capture_depth = 1
                self._buf = []
            elif self._is_snippet(tag, attr):
                self._capture = "snippet"
                self._capture_depth = 1
                self._buf = []
            return

        if not void and self._is_container(tag, attr):
            self._container_depth = 1
            self._heading_depth = 0

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        if self._capture is not None:
            self._capture_depth -= 1
            if self._capture_depth > 0:
                return
            text = _collapse("".join(self._buf))
            if self.rows:
                if self._capture == "title":
                    self.rows[-1]["title"] = text
                elif not self.rows[-1]["snippet"]:
                    self.rows[-1]["snippet"] = text
            self._capture = None
            self._buf = []
            # The captured element also occupied one level of the container (and possibly of the
            # heading), which the early return above skipped.
            self._close_container_level()
            return
        if self._container_depth > 0:
            self._close_container_level()

    def _close_container_level(self) -> None:
        if self._heading_depth > 0:
            self._heading_depth -= 1
        self._container_depth -= 1
        if self._container_depth <= 0:
            self._container_depth = 0
            self._heading_depth = 0

    def handle_data(self, data: str) -> None:
        if self._capture is not None:
            self._buf.append(data)


# --------------------------------------------------------------------------------------------
# URL unwrapping
# --------------------------------------------------------------------------------------------


def unwrap_bing_redirect(href: str) -> str:
    """Decode `bing.com/ck/a?...&u=a1<urlsafe-base64>` into the real destination.

    Returns "" for a redirector whose payload cannot be decoded. It deliberately does NOT fall back
    to returning the bing.com URL: a redirector passed off as a result makes the source domain read
    as "bing.com", which feeds the credibility scorer a lie about where the evidence came from.
    """

    raw = html_module.unescape(str(href or "")).strip()
    if not raw:
        return ""
    if "bing.com/ck/a" not in raw and "/ck/a?" not in raw:
        return raw

    try:
        parsed = urllib.parse.urlparse(raw)
        values = urllib.parse.parse_qs(parsed.query).get("u") or []
    except ValueError:
        return ""

    for value in values:
        payload = value[2:] if value.startswith("a1") else value
        if not payload:
            continue
        padded = payload + "=" * (-len(payload) % 4)
        try:
            decoded = base64.urlsafe_b64decode(padded).decode("utf-8", errors="ignore")
        except (binascii.Error, ValueError, TypeError):
            continue
        if decoded.startswith("http://") or decoded.startswith("https://"):
            return decoded
    return ""


def unwrap_duckduckgo_redirect(href: str) -> str:
    """Unwrap `duckduckgo.com/l/?uddg=<percent-encoded>` when DuckDuckGo serves the indirect form."""

    raw = html_module.unescape(str(href or "")).strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    if "uddg=" not in raw:
        return raw
    try:
        parsed = urllib.parse.urlparse(raw)
        target = (urllib.parse.parse_qs(parsed.query).get("uddg") or [""])[0]
    except ValueError:
        return ""
    return urllib.parse.unquote(target) if target else ""


_INTERNAL_HOSTS = (
    "duckduckgo.com",
    "bing.com",
    "microsofttranslator.com",
    "go.microsoft.com",
)


def _is_usable_result_url(url: str) -> bool:
    if not url.startswith("http://") and not url.startswith("https://"):
        return False
    host = urllib.parse.urlparse(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return not any(host == internal or host.endswith("." + internal) for internal in _INTERNAL_HOSTS)


# --------------------------------------------------------------------------------------------
# Per-engine extraction
# --------------------------------------------------------------------------------------------


def extract_duckduckgo_results(dom: str) -> list[dict[str, str]]:
    parser = _ResultDomParser(
        is_container=lambda tag, attr: attr.get("data-testid") == "result",
        is_title_anchor=lambda tag, attr, in_heading: (
            tag == "a" and attr.get("data-testid") == "result-title-a" and bool(attr.get("href"))
        ),
        is_snippet=lambda tag, attr: (
            attr.get("data-result") == "snippet" or attr.get("data-testid") == "result-snippet"
        ),
        resolve_url=unwrap_duckduckgo_redirect,
    )
    parser.feed(dom or "")
    return _finish(parser.rows, engine="browser_search:duckduckgo")


def extract_bing_results(dom: str) -> list[dict[str, str]]:
    parser = _ResultDomParser(
        is_container=lambda tag, attr: tag == "li" and "b_algo" in attr.get("class", "").split(),
        is_title_anchor=lambda tag, attr, in_heading: (
            tag == "a" and in_heading and bool(attr.get("href"))
        ),
        is_snippet=lambda tag, attr: "b_caption" in attr.get("class", "").split(),
        resolve_url=unwrap_bing_redirect,
    )
    parser.feed(dom or "")
    return _finish(parser.rows, engine="browser_search:bing")


def _finish(rows: list[dict[str, str]], *, engine: str) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        url = (row.get("url") or "").strip()
        if not _is_usable_result_url(url) or url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "title": (row.get("title") or "").strip(),
                "url": url,
                "snippet": (row.get("snippet") or "").strip(),
                "engine": engine,
            }
        )
    return out


# --------------------------------------------------------------------------------------------
# The provider
# --------------------------------------------------------------------------------------------


def _duckduckgo_url(query: str) -> str:
    return "https://duckduckgo.com/?q=" + urllib.parse.quote_plus(query)


def _bing_url(query: str) -> str:
    # &mkt=en-US is what removed the geographic scatter in the measured runs. It does not repair
    # Bing's decoy index; see the module docstring.
    return "https://www.bing.com/search?q=" + urllib.parse.quote_plus(query) + "&mkt=en-US"


_ENGINES: dict[str, tuple[Callable[[str], str], Callable[[str], list[dict[str, str]]]]] = {
    "duckduckgo": (_duckduckgo_url, extract_duckduckgo_results),
    "bing": (_bing_url, extract_bing_results),
}

DEFAULT_ENGINES: tuple[str, ...] = ("duckduckgo", "bing")


def browser_available() -> bool:
    """Whether a browser this engine can drive exists on this machine.

    The locator is looked up on the module at call time rather than bound at import, so that
    neutralising it in a test neutralises it for BOTH this check and the engine itself -- a
    sabotage arm that only reached one of the two would leave the other quietly compensating.
    """

    from tools.browser.browser_render import find_browser_binary

    return find_browser_binary() is not None


def browser_search(
    query: str,
    *,
    max_results: int = 8,
    timeout_s: float = 25.0,
    engines: tuple[str, ...] = DEFAULT_ENGINES,
    render: Callable[..., dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """Run `query` through a real browser and return [{title, url, snippet, engine}].

    Returns an empty list when every engine yields nothing. It never fabricates a hit and never
    reports a rendered-but-empty page as a result: an empty list is the correct answer when the
    web did not give one, and the caller's provider chain moves on.
    """

    text = str(query or "").strip()
    if not text:
        return []

    render_fn = render or chrome_render
    timeout_ms = int(max(5.0, float(timeout_s)) * 1000)

    for name in engines:
        entry = _ENGINES.get(name)
        if entry is None:
            continue
        build_url, extract = entry
        rendered = render_fn(build_url(text), timeout_ms=timeout_ms)
        if str(rendered.get("status") or "") != "ok":
            continue
        results = extract(str(rendered.get("html") or ""))
        if results:
            return results[: max(1, int(max_results))]
    return []
