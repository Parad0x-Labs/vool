"""One generic client for every key-backed web-search API in ``core.search_providers``.

There is deliberately no per-provider code path here. A provider is fully described by its
``SearchProviderConfig`` (endpoint, auth style, where the results list lives in the JSON, which
fields carry title/url/snippet), so this module translates ONE wire format into the runtime's
``WebHit`` shape and nothing else. That is the canonical-tool-protocol rule applied to search:
adapters translate wire formats, they never change what a provider is allowed to do.

Two rules this module exists to keep:

1. **Every request goes through the one outbound door.** ``core.remote_fetch_policy.open_remote``
   enforces the per-turn veto and records what was fetched. A provider that opened its own socket
   is exactly how, on 2026-08-18, a turn that FORBADE remote fetching still reached SearXNG with
   the user's query and reported ``web_calls: 0``. There is no urlopen in this file.

2. **The key is never returned, logged, or put in a URL.** Auth rides a header wherever the
   provider allows one. When a provider only accepts the key as a query parameter, the URL is
   still built here but callers get the redacted form for notes and receipts -- ``_safe_url`` is
   what any error path is allowed to quote.

Failures are classified, not swallowed: the caller gets a ``SearchApiError`` whose ``reason`` is a
short stable token ("unauthorized", "rate_limited", "timeout", "bad_shape", ...), because a note
that says which leg died is the difference between "the web had nothing" and "this key is dead" --
a distinction the 2026-08-19 audit found the search stack could not make at all.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from core.remote_fetch_policy import (
    CredentialRedirectRefusedError,
    RemoteFetchRefusedError,
    open_keyed_request,
    open_remote,
)
from core.search_providers import SearchProviderConfig

# A search API answers in well under this; the ceiling exists so one dead endpoint cannot eat the
# turn's whole web budget. Callers pass their remaining budget and this caps it.
_MAX_TIMEOUT_S = 12.0
# Response bodies are small JSON. Anything larger is a misconfigured endpoint (or an HTML error
# page), and reading it in full would be the only unbounded allocation in the path.
_MAX_BODY_BYTES = 2_000_000


class SearchApiError(RuntimeError):
    """A key-backed search call failed, with a stable machine-readable ``reason``."""

    def __init__(self, reason: str, detail: str = "", *, status: int | None = None) -> None:
        super().__init__(f"{reason}:{detail}" if detail else reason)
        self.reason = reason
        self.detail = detail
        self.status = status


@dataclass(frozen=True)
class SearchApiHit:
    """One normalized result. Deliberately the same three fields every provider can supply."""

    title: str
    url: str
    snippet: str


def _safe_url(cfg: SearchProviderConfig, url: str) -> str:
    """The URL with any key-bearing query parameter masked, for notes/receipts/errors."""
    if cfg.auth_style != "query" or not cfg.auth_name:
        return url
    try:
        parts = urllib.parse.urlsplit(url)
        pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        # A word, not punctuation: urlencode percent-escapes "***" into "%2A%2A%2A", which is
        # unreadable in a note and no longer obviously a redaction.
        masked = [(k, "REDACTED" if k == cfg.auth_name else v) for k, v in pairs]
        return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(masked)))
    except Exception:
        # Never let a formatting failure be the reason a key reaches a log: drop the query wholly.
        return url.split("?", 1)[0]


def _dig(payload: Any, path: tuple[str, ...]) -> Any:
    """Walk ``path`` into nested dicts, returning None the moment the shape disagrees."""
    node = payload
    for step in path:
        if not isinstance(node, dict):
            return None
        node = node.get(step)
    return node


def _first_text(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = row.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
        # Some providers nest the snippet one level down (e.g. {"description": {"text": ...}}).
        if isinstance(value, dict):
            inner = value.get("text") or value.get("value")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
        if isinstance(value, list) and value and isinstance(value[0], str):
            return value[0].strip()
    return ""


def build_request(
    cfg: SearchProviderConfig,
    query: str,
    key: str,
    *,
    max_hits: int,
) -> tuple[urllib.request.Request, str]:
    """Build the authed request for one search. Returns (request, redacted_url_for_notes)."""
    text = str(query or "").strip()
    if not text:
        raise SearchApiError("empty_query")
    params: dict[str, str] = dict(cfg.extra_params)
    headers: dict[str, str] = {"Accept": "application/json", **dict(cfg.extra_headers)}
    body_bytes: bytes | None = None
    # The key header is attached UNREDIRECTED below: urllib's redirect handler copies only ordinary
    # headers onto a redirect hop, so the key can never ride a redirect to another origin.
    credential_header: tuple[str, str] | None = None

    if cfg.auth_style == "bearer":
        credential_header = ("Authorization", f"Bearer {key}")
    elif cfg.auth_style == "header":
        credential_header = (cfg.auth_name, key)

    if cfg.method == "POST":
        payload: dict[str, Any] = {cfg.query_field: text, **cfg.extra_body}
        if cfg.count_param:
            payload[cfg.count_param] = max_hits
        if cfg.auth_style == "body":
            payload[cfg.auth_name] = key
        body_bytes = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        url = cfg.search_url
    else:
        params[cfg.query_param] = text
        if cfg.count_param:
            params[cfg.count_param] = str(max_hits)
        if cfg.auth_style == "query":
            params[cfg.auth_name] = key
        url = f"{cfg.search_url}?{urllib.parse.urlencode(params)}"

    request = urllib.request.Request(url, data=body_bytes, headers=headers, method=cfg.method)
    if credential_header is not None:
        request.add_unredirected_header(*credential_header)
    return request, _safe_url(cfg, url)


def parse_results(cfg: SearchProviderConfig, payload: Any, *, max_hits: int) -> list[SearchApiHit]:
    """Map a provider's JSON onto ``SearchApiHit`` rows using only its config."""
    rows = _dig(payload, cfg.results_path)
    if rows is None and cfg.fallback_results_path:
        rows = _dig(payload, cfg.fallback_results_path)
    if not isinstance(rows, list):
        raise SearchApiError("bad_shape", "results list not found at configured path")
    hits: list[SearchApiHit] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _first_text(row, cfg.url_fields)
        if not url.startswith(("http://", "https://")):
            continue
        hits.append(
            SearchApiHit(
                title=_first_text(row, cfg.title_fields) or url,
                url=url,
                snippet=_first_text(row, cfg.snippet_fields),
            )
        )
        if len(hits) >= max_hits:
            break
    return hits


def _error_code(exc: Any) -> str:
    """The provider's short error-code token from an HTTP error body, or "" -- never its message."""
    try:
        raw = exc.read(65536) if getattr(exc, "fp", None) is not None else b""
    except Exception:
        return ""
    from core.credential_intelligence.verification import provider_error_code_from_body

    return provider_error_code_from_body(raw)


def _read_json(response: Any) -> Any:
    raw = response.read(_MAX_BODY_BYTES)
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, AttributeError) as exc:
        # An HTML error page or a truncated body lands here. This is a distinct failure from
        # "the provider returned no results" and must stay distinct in the notes.
        raise SearchApiError("bad_shape", "response was not JSON") from exc


def search(
    cfg: SearchProviderConfig,
    query: str,
    key: str,
    *,
    max_hits: int = 5,
    timeout_s: float = _MAX_TIMEOUT_S,
) -> list[SearchApiHit]:
    """Run one key-backed search. Raises ``SearchApiError`` with a classified reason on failure.

    The call goes through ``open_remote``, so a turn that is not permitted to reach the network
    fails closed here (``RemoteFetchRefusedError`` is re-raised untouched) rather than quietly
    returning zero hits, which would be indistinguishable from a search that ran and found nothing.
    """
    if not str(key or "").strip():
        raise SearchApiError("no_key")
    timeout = max(1.0, min(float(timeout_s), _MAX_TIMEOUT_S))
    request, redacted = build_request(cfg, query, key, max_hits=max_hits)

    def _send(current: urllib.request.Request) -> Any:
        # The door records WHO this call went to, not just which host. Reaching
        # this line means a stored credential is on the request (the `no_key`
        # guard above is unconditional), so "keyed" is a fact here rather than a
        # lookup — and it is the fact every downstream surface needs to prove
        # the user's key actually did the work.
        return open_remote(current, timeout=timeout, provider_id=cfg.provider_id, keyed_or_keyless="keyed")

    try:
        # The key rides this request, so it is exchanged as a keyed request: an answer that came
        # from another origin is refused, and a same-origin redirect target is asked once more.
        with open_keyed_request(request, _send) as response:
            payload = _read_json(response)
    except RemoteFetchRefusedError:
        # The turn-level veto is not this module's to soften.
        raise
    except CredentialRedirectRefusedError as exc:
        raise SearchApiError(
            "redirect_refused", f"redirect to {exc.redirect_origin or 'another origin'}; the key was not sent there",
        ) from exc
    except urllib.error.HTTPError as exc:
        status = int(getattr(exc, "code", 0) or 0)
        code = _error_code(exc)
        if code and code in cfg.invalid_key_error_codes:
            # The provider's own error code names the credential (e.g. Brave's 422
            # SUBSCRIPTION_TOKEN_INVALID). Any other code on the same status stays http_error.
            raise SearchApiError("unauthorized", f"HTTP {status} {code}", status=status) from exc
        if status in (401, 403):
            raise SearchApiError("unauthorized", f"HTTP {status}", status=status) from exc
        if status == 429:
            raise SearchApiError("rate_limited", "HTTP 429", status=status) from exc
        if status == 402:
            raise SearchApiError("quota_exhausted", "HTTP 402", status=status) from exc
        if 300 <= status < 400:
            # A redirect urllib declined to follow (a POST answered 307/308): nothing was re-sent.
            raise SearchApiError(
                "redirect_refused", f"HTTP {status} redirect not followed; the key was not sent again", status=status,
            ) from exc
        raise SearchApiError("http_error", f"HTTP {status} at {redacted}", status=status) from exc
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", "") or type(exc).__name__)
        token = "timeout" if "timed out" in reason.lower() else "unreachable"
        raise SearchApiError(token, reason) from exc
    except TimeoutError as exc:
        raise SearchApiError("timeout", "socket timeout") from exc
    except OSError as exc:
        # The test network seal and a genuinely offline machine both arrive here.
        raise SearchApiError("unreachable", type(exc).__name__) from exc
    return parse_results(cfg, payload, max_hits=max_hits)


__all__ = ["SearchApiError", "SearchApiHit", "build_request", "parse_results", "search"]
