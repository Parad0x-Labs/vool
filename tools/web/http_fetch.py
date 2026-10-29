from __future__ import annotations

import contextlib
import html
import re
import urllib.request
import zlib
from html.parser import HTMLParser

from core import policy_engine
from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote, remote_fetch_forbidden
from tools.web.browser_identity import DESKTOP_ACCEPT_LANGUAGE, DESKTOP_USER_AGENT

_CAPTCHA_RE = re.compile(r"(captcha|are you human|robot check)", re.IGNORECASE)
_LOGIN_RE = re.compile(r"(sign in|log in|password)", re.IGNORECASE)
# A real block page is SHORT: it is almost entirely the block notice. Matching these markers
# anywhere in a page of any size threw away the best grounding sources on the web -- every
# en.wikipedia.org article was returned as "captcha" with empty text because the word appears once
# somewhere in the page, and "sign in" appears in the header of most sites in existence.
_BLOCK_PAGE_MAX_CHARS = 2500


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        text = re.sub(r"\s+", " ", data or "").strip()
        if text:
            self.parts.append(text)


def strip_html(raw_html: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(raw_html or "")
    return " ".join(parser.parts).strip()


def _decompress(raw: bytes, content_encoding: str) -> bytes | None:
    """Decode a compressed body, or None when it cannot be decoded here.

    Servers compress whether or not we ask -- python.org answers `Content-Encoding: gzip` to a
    request that sends no Accept-Encoding at all. Decoding those bytes as UTF-8 does not fail, it
    produces GARBAGE, and that garbage was handed to the model as the page's content.

    The body is read under a byte cap, so a compressed stream is usually TRUNCATED mid-block. A
    decompressobj returns everything up to the cut; gzip.decompress would reject the whole page.
    """
    encoding = (content_encoding or "").strip().lower()
    if not encoding or encoding == "identity":
        return raw
    if encoding in {"gzip", "x-gzip"}:
        with contextlib.suppress(Exception):
            return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw)
        return None
    if encoding == "deflate":
        with contextlib.suppress(Exception):
            return zlib.decompressobj().decompress(raw)
        with contextlib.suppress(Exception):  # raw deflate, no zlib header
            return zlib.decompressobj(-zlib.MAX_WBITS).decompress(raw)
        return None
    if encoding == "br":
        try:  # optional dependency; never installed for this (the key-machine rule)
            import brotli  # type: ignore[import-not-found]
        except ImportError:
            return None
        with contextlib.suppress(Exception):
            return brotli.decompress(raw)
    return None


def http_fetch_text(
    url: str, *, timeout_s: float = 15.0, max_bytes: int = 2_000_000, public_only: bool = False,
    not_local: bool = False,
) -> dict[str, str]:
    """Fetch and normalize one page -- THROUGH THE ONE DOOR (R2b2b).

    This opened its own raw `urllib.request.urlopen`, which made it the
    biggest bypass in the R2b2 audit: the served web lane read every page
    through here while no gateway consult, no receipt and no lifecycle state
    existed for any of it. The door owns the socket now, so every page fetch
    runs `authorized → started → terminal` on the turn's ledger and a refusal
    is typed and visible. The fetch tally is the door's too: a refused fetch
    never went to the network and is not counted (the door's own law), where
    this module used to count its own vetoed attempts.

    ``public_only`` (a turn from another device, core.remote_turn_fetch) reaches public addresses
    only, checked after DNS and on every redirect hop; a refused target answers
    ``private_target_refused``.

    ``not_local`` (the owner's research reading search hits) uses the same checked, pinned
    resolution and per-hop redirect guard as ``public_only``. Neither mode permits a proxy
    to resolve the target again or follows an unchecked redirect.
    """
    from core.remote_turn_fetch import RemoteTurnTargetRefused, open_public_only, refuse_local_target

    max_bytes = max(1024, min(int(max_bytes), policy_engine.max_fetch_bytes()))

    def _request(target: str) -> urllib.request.Request:
        return urllib.request.Request(
            target,
            headers={
                # A self-identifying bot User-Agent is not answered with a refusal, it is answered with
                # a DECOY INDEX -- a 200 carrying real HTML that is not about the query at all. See
                # tools/web/browser_identity.py for the measurements. Nothing in the tree read the old
                # string; this was its only reference.
                "User-Agent": DESKTOP_USER_AGENT,
                # Without this the same engines geo-scatter English queries into other locales.
                "Accept-Language": DESKTOP_ACCEPT_LANGUAGE,
                # Ask for an uncompressed body. Politeness, not protection -- servers compress anyway,
                # which is why _decompress exists.
                "Accept-Encoding": "identity",
            },
        )

    try:
        opened = (
            open_public_only(url, make_request=_request, timeout=timeout_s, lookup=True)
            if public_only or not_local
            else open_remote(_request(url), timeout=timeout_s, lookup=True)
        )
        with opened as response:
            final_url = str(response.geturl() or url)
            if public_only or not_local:
                # The pinned door never follows a redirect on its own; a response that still
                # landed on this computer came through a door that did, and is not read.
                refuse_local_target(final_url, resolve=False)
            content_encoding = str(response.headers.get("Content-Encoding") or "")
            raw = response.read(max_bytes)
    except RemoteFetchRefusedError:
        # The lane's callers answer in statuses, not exceptions; the refusal
        # is already on the turn's ledger as a first-class denied effect.
        status = "remote_fetch_disabled" if remote_fetch_forbidden() else "fetch_denied"
        return {"status": status, "text": "", "html": "", "final_url": url}
    except RemoteTurnTargetRefused as refused:
        return {"status": "private_target_refused", "text": "", "html": "", "final_url": refused.url}
    body = _decompress(raw, content_encoding)
    if body is None:
        # Better to report nothing than to pass compressed bytes off as the page's text.
        return {"status": "unreadable_encoding", "text": "", "html": "", "final_url": final_url}
    html_text = body.decode("utf-8", errors="ignore")
    visible = html.unescape(strip_html(html_text))
    # Only a SHORT page can be a block page. Matching these markers against the whole document
    # discarded any article that merely mentions them -- which is most of the useful web.
    if len(visible) <= _BLOCK_PAGE_MAX_CHARS:
        lowered = visible.lower()
        if _CAPTCHA_RE.search(lowered):
            return {"status": "captcha", "text": "", "html": html_text, "final_url": final_url}
        if _LOGIN_RE.search(lowered):
            return {"status": "login_wall", "text": "", "html": html_text, "final_url": final_url}
    return {"status": "ok", "text": visible, "html": html_text, "final_url": final_url}
