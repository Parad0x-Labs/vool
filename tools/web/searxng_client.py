from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from core import policy_engine
from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote

DEFAULT_TIMEOUT_SECONDS = 12.0


class SearXNGUnavailableError(RuntimeError):
    """This instance could not serve the query, with the reason it could not.

    The provider chain records `searxng_failed:{type(exc).__name__}`, so before this every outcome
    arrived as `HTTPError` or `URLError` and a 429 was indistinguishable from a 500, a refused
    connection or a timeout. Nothing downstream can back off, warn about a misconfigured URL, or
    tell "your instance is rate-limiting you" from "your instance is down" on evidence like that.

    `reason` is a stable token, not prose, because it is what a health or backoff decision would key
    on. `RemoteFetchRefusedError` is deliberately NOT wrapped: a turn-level veto is a policy
    refusal, not a provider failure, and collapsing the two would let a forbidden turn read as a
    broken instance.
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    engine: str | None = None
    score: float | None = None


class SearXNGClient:
    """Call a SearXNG instance using the JSON search API."""

    def __init__(self, base_url: str | None = None, timeout_s: float | None = None) -> None:
        configured_url = str(os.getenv("SEARXNG_URL") or policy_engine.searxng_url()).strip() or "http://127.0.0.1:8080"
        self.base_url = (base_url or configured_url).rstrip("/")
        env_timeout = os.getenv("SEARXNG_TIMEOUT")
        configured_timeout = policy_engine.searxng_timeout_seconds()
        raw_timeout: float | str = timeout_s if timeout_s is not None else (env_timeout if env_timeout else configured_timeout)
        self.timeout_s = float(raw_timeout)

    def search(
        self,
        query: str,
        *,
        language: str = "en",
        safesearch: int = 1,
        max_results: int = 10,
    ) -> list[SearchResult]:
        text = (query or "").strip()
        if not text:
            return []

        params = urllib.parse.urlencode(
            {
                "q": text,
                "format": "json",
                "language": language,
                "safesearch": str(max(0, int(safesearch))),
            }
        )
        request = urllib.request.Request(
            f"{self.base_url}/search?{params}",
            headers={"User-Agent": "VOOL-XSEARCH/1.0"},
        )
        # THE SHARED DOOR, not a private socket. This client opened its own until 2026-08-18, and
        # because `web.search` is registered straight onto `client.search` (tools/registry.py), a
        # turn that FORBADE remote fetching still reached the SearXNG endpoint with the user's
        # query -- measured, the socket was reached with `remote_fetch_forbidden()` True. The door
        # enforces the veto and reports the address, so a self-hosted endpoint can no longer be a
        # way around the turn's own retrieval rules, and its calls appear in `web_calls` like every
        # other provider's.
        start = time.time()
        try:
            with open_remote(request, timeout=self.timeout_s) as response:
                body = response.read().decode("utf-8", "replace")
        except RemoteFetchRefusedError:
            # A turn-level veto. Not this instance's fault and not a provider outcome; the caller
            # must see the refusal rather than a "provider unavailable" that invites a retry.
            raise
        except urllib.error.HTTPError as exc:
            code = int(getattr(exc, "code", 0) or 0)
            if code == 429:
                raise SearXNGUnavailableError("rate_limited", f"HTTP {code}") from exc
            if code in {401, 403}:
                raise SearXNGUnavailableError("unauthorized", f"HTTP {code}") from exc
            if 400 <= code < 500:
                raise SearXNGUnavailableError("bad_request", f"HTTP {code}") from exc
            raise SearXNGUnavailableError("server_error", f"HTTP {code}") from exc
        except TimeoutError as exc:
            raise SearXNGUnavailableError("timeout", f"{self.timeout_s}s") from exc
        except urllib.error.URLError as exc:
            # A timeout inside urllib arrives wrapped, so the inner reason decides.
            inner = getattr(exc, "reason", None)
            if isinstance(inner, TimeoutError) or "timed out" in str(inner).lower():
                raise SearXNGUnavailableError("timeout", f"{self.timeout_s}s") from exc
            raise SearXNGUnavailableError("unreachable", str(inner or exc)) from exc
        _ = time.time() - start
        try:
            payload = json.loads(body)
        except (ValueError, TypeError) as exc:
            # An HTML error page, a login redirect, or a proxy notice. A non-JSON 200 is a
            # misconfigured endpoint, and saying so is more useful than an empty result set.
            raise SearXNGUnavailableError("invalid_json", body[:120].strip()) from exc
        if not isinstance(payload, dict):
            raise SearXNGUnavailableError("invalid_json", f"top level is {type(payload).__name__}")
        items = list(payload.get("results") or [])[: max(1, int(max_results))]
        results: list[SearchResult] = []
        for item in items:
            # A malformed ROW is not a malformed response: skip it and keep the rest, the same way
            # a missing url already dropped one row rather than the whole answer.
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            snippet = str(item.get("content") or "").strip()
            if not url:
                continue
            score_raw = item.get("score")
            try:
                score = float(score_raw) if score_raw is not None else None
            except (TypeError, ValueError):
                score = None
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    engine=str(item.get("engine") or "").strip() or None,
                    score=score,
                )
            )
        return results
