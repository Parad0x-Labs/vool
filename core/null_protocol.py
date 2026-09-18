from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

from core.x402.client import X402Client, X402Config, X402Mode, X402Quote

_NULL_SCHEME = "null"
_VALID_SERVICE = re.compile(r"^[a-z0-9]([a-z0-9_-]{0,62}[a-z0-9])?$")

_BASE_PRICES: dict[str, float] = {
    "task":  0.0005,   # ~500 output tokens worth
    "embed": 0.0001,
    "pay":   0.0,
}


class NullProtocolError(ValueError):
    """Raised for malformed or unsupported null:// URIs."""


@dataclass(frozen=True)
class NullUri:
    """
    Parsed null:// URI.

    null://service/path?param=value

    Examples
    --------
    null://task/code-review          → service="task", path="code-review"
    null://embed/search?q=foo        → service="embed", path="search"
    null://pay/transfer?to=<wallet>  → service="pay",  path="transfer"
    """
    service: str
    path: str
    params: dict[str, list[str]]
    raw: str


@dataclass
class NullRequest:
    """A resolved null:// request ready for routing through VOOL."""
    uri: NullUri
    session_id: str
    quote: X402Quote | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NullResponse:
    """Result of executing a null:// request."""
    session_id: str
    service: str
    path: str
    result: Any
    receipt_id: str | None = None
    # Dark-Null-Protocol slot — None until privacy layer is active
    zk_proof: str | None = None


def parse_null_uri(uri: str) -> NullUri:
    """
    Parse a null:// URI into its components.

    Raises NullProtocolError for invalid input.
    """
    raw = str(uri or "").strip()
    if not raw:
        raise NullProtocolError("null:// URI cannot be empty")

    parsed = urlparse(raw)
    if parsed.scheme.lower() != _NULL_SCHEME:
        raise NullProtocolError(
            f"Expected null:// scheme, got {parsed.scheme!r} in {raw!r}"
        )

    service = (parsed.netloc or "").strip()
    if not service:
        # fall back: treat first path segment as service
        parts = parsed.path.lstrip("/").split("/", 1)
        service = parts[0].strip()
    if not service:
        raise NullProtocolError(f"null:// URI must specify a service: {raw!r}")
    if not _VALID_SERVICE.match(service):
        raise NullProtocolError(
            f"Invalid service name {service!r} in {raw!r} "
            "(must be lowercase alphanumeric with hyphens/underscores)"
        )

    path_parts = parsed.path.lstrip("/").split("/", 1)
    if parsed.netloc:
        path = parsed.path.lstrip("/")
    else:
        path = path_parts[1].strip("/") if len(path_parts) > 1 else ""

    params = dict(parse_qs(parsed.query or ""))
    return NullUri(service=service, path=path, params=params, raw=raw)


def resolve_null_request(
    uri_str: str,
    *,
    session_id: str | None = None,
    x402_client: X402Client | None = None,
    price_per_token_usdc: float = 0.000001,
    recipient_wallet: str = "stub-wallet",
    measured_output_tokens: int | None = None,
) -> NullRequest:
    """
    Parse a null:// URI and build an x402 quote for the service.

    When ``measured_output_tokens`` is supplied (e.g. the adapter's eval_count
    after a run), the quote is metered as tokens × price — a real invoice tied to
    the count the model emitted — instead of a flat per-service estimate. An
    explicit ``?price=`` on the URI still wins. Uses a stub X402Client by default.
    """
    uri = parse_null_uri(uri_str)
    sid = session_id or f"null-{uuid.uuid4().hex[:12]}"
    client = x402_client or X402Client(X402Config(mode=X402Mode.STUB))
    amount = _price_for(
        uri,
        price_per_token_usdc=price_per_token_usdc,
        measured_output_tokens=measured_output_tokens,
    )
    quote = client.quote(amount_usdc=max(amount, price_per_token_usdc), recipient_wallet=recipient_wallet)
    return NullRequest(uri=uri, session_id=sid, quote=quote)


def _price_for(
    uri: NullUri,
    *,
    price_per_token_usdc: float,
    measured_output_tokens: int | None = None,
) -> float:
    # Precedence: explicit ?price= override > measured tokens × price > flat table.
    explicit = (uri.params.get("price") or [None])[0]
    if explicit is not None:
        try:
            return max(0.0, float(explicit))
        except Exception:
            pass
    if measured_output_tokens is not None and measured_output_tokens > 0:
        return max(0.0, float(measured_output_tokens) * price_per_token_usdc)
    return _BASE_PRICES.get(uri.service, price_per_token_usdc * 500)


__all__ = [
    "NullProtocolError",
    "NullRequest",
    "NullResponse",
    "NullUri",
    "parse_null_uri",
    "resolve_null_request",
]
