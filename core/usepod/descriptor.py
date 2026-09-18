"""UsePod as a provider: the facts VOOL relies on, where each came from, and the one way a
token-bearing URL is built.

UsePod authenticates a prepaid request by a token IN THE URL PATH (``<origin>/proxy/<token>/v1``),
not by an ``Authorization`` header, and its accountless x402 surface has no token at all. That
reshapes three things this module owns:

* The token is a credential, so any URL that carries it is a secret string. It is assembled only at
  the transport boundary (:meth:`ProxyTarget.wire_url`) and never persisted into a manifest, a log
  line, an exception or a receipt. ``str()``/``repr()`` of a target show ``{token}`` instead.
* What caches and receipts may carry is the ORIGIN and the surface. Discovery that depends on the
  credential is keyed by :func:`credential_fingerprint`, a one-way digest over origin and token, so
  rotating the token invalidates it without the key revealing the token.
* There is no API key to invent. The prepaid proxy ignores whatever ``Authorization`` a client
  sends, and x402 needs no account, so VOOL sends no ``Authorization`` header at all.

Every documented fact below names its source page, the day it was read, and the evidence class it
rests on. A fact marked ``documented`` was read in the provider's docs; ``live_observed`` was seen
in a live public response; ``unverified`` means the docs are silent or the behaviour has not been
exercised against the live service. Nothing here is a price -- prices come from the live feed
(:mod:`core.usepod.pricing`).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlsplit

from core.cloud_providers import PROVIDERS as _PROVIDER_TABLE

PROVIDER_ID = "usepod"
PROVIDER_LABEL = "UsePod"
#: From the one provider table (core.cloud_providers), never restated here.
DEFAULT_ORIGIN = _PROVIDER_TABLE[PROVIDER_ID].base_url.rstrip("/")
DOCS_READ_ON = "2026-09-14"

# Paths, relative to the origin. The prepaid token sits between PROXY_PREFIX and the surface path.
PROXY_PREFIX = "/proxy"
X402_SEGMENT = "x402"
OPENAI_CHAT_PATH = "/v1/chat/completions"
ANTHROPIC_MESSAGES_PATH = "/v1/messages"
MODELS_PATH = "/v1/models"
BALANCE_PATH = "/balance"
MARKETPLACE_MODELS_PATH = "/v1/marketplace/models"

# Request control headers (docs: api/proxy, using/spend-controls, marketplace/routing).
HEADER_MAX_PRICE_INPUT = "X-Pod-Max-Price-Input"
HEADER_MAX_PRICE_OUTPUT = "X-Pod-Max-Price-Output"
HEADER_ROUTING_MODE = "X-Pod-Routing-Mode"
HEADER_PROVIDERS = "X-Pod-Providers"
# Response metadata headers (docs: api/proxy).
HEADER_ROUTE = "X-Pod-Route"
HEADER_PROVIDER_ID = "X-Pod-Provider-Id"
HEADER_BALANCE_REMAINING = "X-Balance-Remaining"
# x402 headers (docs: api/x402-payments).
HEADER_PAYMENT_REQUIRED = "PAYMENT-REQUIRED"
HEADER_PAYMENT_SIGNATURE = "PAYMENT-SIGNATURE"
HEADER_PAYMENT_RESPONSE = "PAYMENT-RESPONSE"
# The Anthropic surface mirrors the upstream Messages API, which requires a version header.
ANTHROPIC_VERSION = "2023-06-01"

EVIDENCE_DOCUMENTED = "documented"
EVIDENCE_LIVE_OBSERVED = "live_observed"
EVIDENCE_UNVERIFIED = "unverified"

_DOCS = "https://docs.usepod.ai"


class WireProtocol(str, Enum):
    """The request dialect a call speaks. Both reach the same routing and billing at UsePod."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class TransportMode(str, Enum):
    """How a call is paid for. Distinct transports, never a flag on one another."""

    PREPAID = "prepaid_token"
    X402 = "x402"


@dataclass(frozen=True)
class DocumentedFact:
    key: str
    statement: str
    source: str
    evidence: str
    observed_on: str = DOCS_READ_ON

    def as_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "statement": self.statement,
            "source": self.source,
            "evidence": self.evidence,
            "observed_on": self.observed_on,
        }


DOCUMENTED_FACTS: tuple[DocumentedFact, ...] = (
    DocumentedFact("prepaid_openai_surface", "OpenAI-compatible base is <origin>/proxy/<token>/v1 (chat/completions, models, responses, streaming).", f"{_DOCS}/using/drop-in-api/", EVIDENCE_DOCUMENTED),
    DocumentedFact("prepaid_anthropic_surface", "Anthropic-compatible base is <origin>/proxy/<token> (v1/messages).", f"{_DOCS}/api/proxy/", EVIDENCE_DOCUMENTED),
    DocumentedFact("path_token_auth", "The token in the path authenticates; an Authorization/api_key a client sends is ignored.", f"{_DOCS}/api/proxy/", EVIDENCE_DOCUMENTED),
    DocumentedFact("unknown_token_status", "An unknown token returns 401.", f"{_DOCS}/api/proxy/", EVIDENCE_DOCUMENTED),
    DocumentedFact("no_balance_rejected", "A token with no balance is rejected before any upstream call; the status code is not stated.", f"{_DOCS}/api/proxy/", EVIDENCE_UNVERIFIED),
    DocumentedFact("models_listing", "GET /v1/models behind the proxy lists models; its response schema is not published.", f"{_DOCS}/api/proxy/", EVIDENCE_UNVERIFIED),
    DocumentedFact("balance_endpoint", "GET <origin>/proxy/<token>/balance returns usdc_balance in USDC microunits.", f"{_DOCS}/api/deposit-on-chain/", EVIDENCE_DOCUMENTED),
    DocumentedFact("response_route_headers", "Responses carry X-Balance-Remaining, X-Pod-Route (marketplace, key relay or centralized) and X-Pod-Provider-Id (name for centralized, UUID for marketplace/key relay).", f"{_DOCS}/api/proxy/", EVIDENCE_DOCUMENTED),
    DocumentedFact("balance_header_units", "The unit and format of X-Balance-Remaining are not stated.", f"{_DOCS}/api/proxy/", EVIDENCE_UNVERIFIED),
    DocumentedFact("routing_mode_header", "X-Pod-Routing-Mode takes auto (default), marketplace-only or centralized-only; marketplace-only returns a no-provider-at-price result instead of using centralized.", f"{_DOCS}/marketplace/routing/", EVIDENCE_DOCUMENTED),
    DocumentedFact("provider_pin_header", "X-Pod-Providers pins a comma list tried in order; a pin implies centralized-only, a pin with marketplace-only is 400, an unknown name is 400, an unsatisfiable pin is 503.", f"{_DOCS}/marketplace/routing/", EVIDENCE_DOCUMENTED),
    DocumentedFact("price_ceiling_headers", "X-Pod-Max-Price-Input/-Output bound the price per million tokens in USDC microunits; a candidate is eligible only if both prices are at or below them.", f"{_DOCS}/using/spend-controls/", EVIDENCE_DOCUMENTED),
    DocumentedFact("cap_at_centralized", "Marketplace and key relay listings are capped at the cheapest centralized price for the model on both axes.", f"{_DOCS}/marketplace/pricing/", EVIDENCE_DOCUMENTED),
    DocumentedFact("billing_basis", "Billing uses actual token usage from the response stream at the selected provider's price; cache reads/writes are accounted separately where the upstream reports them.", f"{_DOCS}/marketplace/pricing/", EVIDENCE_DOCUMENTED),
    DocumentedFact("marketplace_feed", "GET <origin>/v1/marketplace/models is the public JSON the marketplace page reads: per model, cheapest marketplace and centralized per-million prices as integers, per-provider centralized prices, provider count and pricing_mode.", "https://usepod.ai/marketplace/", EVIDENCE_LIVE_OBSERVED),
    DocumentedFact("feed_price_units", "Feed prices are USDC microunits per million tokens: the live values matched the rendered marketplace table to the microunit.", "https://usepod.ai/marketplace/", EVIDENCE_LIVE_OBSERVED),
    DocumentedFact("cache_rates_in_feed", "The feed publishes no cache read or write rates.", "https://usepod.ai/marketplace/", EVIDENCE_LIVE_OBSERVED),
    DocumentedFact("trust_mechanisms", "Live trust mechanisms are provider bonds, reputation and ~1% hidden benchmark canaries; TEE attestation, content-addressed registries, on-chain slashing and a privacy layer are deferred.", f"{_DOCS}/marketplace/trust/", EVIDENCE_DOCUMENTED),
    DocumentedFact("marketplace_backend_path", "A marketplace request is dispatched to a provider agent that calls its own local backend; a key relay request is forwarded by the gateway with the operator's upstream key.", f"{_DOCS}/introduction/how-it-works/", EVIDENCE_DOCUMENTED),
    DocumentedFact("x402_endpoints", "Accountless x402 uses POST <origin>/proxy/x402/v1/chat/completions and /v1/messages; max_tokens (or max_completion_tokens) is required; text/chat only.", f"{_DOCS}/api/x402-payments/", EVIDENCE_DOCUMENTED),
    DocumentedFact("x402_quote", "An unpaid request gets 402 with PAYMENT-REQUIRED: base64 JSON {x402_version, quote_id, accepts[{asset, scheme, network, pay_to, amount_microunits, mode}]}; amount is USDC microunits on the USDC rail and lamports on the SOL rail.", f"{_DOCS}/api/x402-payments/", EVIDENCE_DOCUMENTED),
    DocumentedFact("x402_binding", "The request is bound to a hash of method, path and body; the paid retry must be byte-for-byte identical and carry PAYMENT-SIGNATURE (base64 JSON quote_id, network, asset, payer_wallet, signature).", f"{_DOCS}/api/x402-payments/", EVIDENCE_DOCUMENTED),
    DocumentedFact("x402_cap_surplus", "The quoted amount is a cap paid up front; unused remainder is credited to a UsePod balance keyed to payer_wallet, not returned on-chain.", f"{_DOCS}/api/x402-payments/", EVIDENCE_DOCUMENTED),
    DocumentedFact("x402_replay", "A transaction signature settles exactly one quote; reuse for a second quote is rejected.", f"{_DOCS}/api/x402-payments/", EVIDENCE_DOCUMENTED),
    DocumentedFact("x402_quote_expiry", "The quote schema documents no expiry field.", f"{_DOCS}/api/x402-payments/", EVIDENCE_UNVERIFIED),
    DocumentedFact("x402_payment_response_schema", "The PAYMENT-RESPONSE receipt header's schema is not published.", f"{_DOCS}/api/x402-payments/", EVIDENCE_UNVERIFIED),
    DocumentedFact("route_verification", "No cryptographic proof of which route or model served a request is documented as live.", f"{_DOCS}/marketplace/trust/", EVIDENCE_DOCUMENTED),
)


class UsePodConfigError(ValueError):
    """A configuration value UsePod cannot be reached with. ``code`` is stable; the message is not."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code)
        super().__init__(f"{self.code}: {detail}" if detail else self.code)


# A path-safe token segment. UsePod documents its token as a UUID; the shape accepted here is
# wider so a future opaque token is not refused for its spelling, but every character that could
# change the PATH (slash, dot, percent, whitespace, query/fragment markers) is excluded, which is
# what keeps a pasted value from steering the request to another endpoint.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{15,127}$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
# Literal path segments of UsePod's own surfaces that must never be taken for a token.
_RESERVED_SEGMENTS = frozenset({X402_SEGMENT, "v1", "balance", "proxy"})


def normalize_origin(value: str) -> str:
    """``scheme://host[:port]`` for a UsePod origin, or :class:`UsePodConfigError`.

    HTTPS to any host, or plain HTTP only to a genuine loopback host -- the same canonical
    transport gate every credential-carrying request in VOOL uses
    (:func:`core.cloud_providers.is_safe_key_transport`). No userinfo, path, query or fragment: an
    origin that carries anything else is not an origin.
    """
    from core.cloud_providers import is_safe_key_transport

    text = str(value or "").strip()
    if not text:
        raise UsePodConfigError("origin_missing")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise UsePodConfigError("origin_unparseable") from exc
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if not scheme or not host:
        raise UsePodConfigError("origin_unparseable")
    if parts.username is not None or parts.password is not None:
        raise UsePodConfigError("origin_has_userinfo")
    if parts.query or parts.fragment:
        raise UsePodConfigError("origin_has_query_or_fragment")
    if parts.path not in ("", "/"):
        raise UsePodConfigError("origin_has_path")
    netloc_host = f"[{host}]" if ":" in host else host
    netloc = netloc_host if port is None else f"{netloc_host}:{port}"
    origin = f"{scheme}://{netloc}"
    if not is_safe_key_transport(origin):
        raise UsePodConfigError("origin_requires_https_or_loopback")
    return origin


def validate_token(value: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise UsePodConfigError("token_missing")
    if token.lower() in _RESERVED_SEGMENTS:
        raise UsePodConfigError("token_is_a_reserved_path_segment")
    if not _TOKEN_RE.fullmatch(token):
        raise UsePodConfigError("token_not_path_safe")
    return token


def token_shape(token: str) -> str:
    """``uuid`` for the documented shape, ``opaque`` for anything else accepted."""
    return "uuid" if _UUID_RE.fullmatch(str(token or "")) else "opaque"


@dataclass(frozen=True)
class ParsedCredential:
    """A pasted UsePod credential, split into what is secret and what is not."""

    token: str = field(repr=False)
    origin: str
    #: True when the paste named an origin (a full URL) rather than a bare token.
    origin_from_paste: bool
    #: ``openai`` for a ``.../v1`` base, ``anthropic`` for the bare proxy base, else "".
    surface_hint: str
    token_shape: str


def parse_credential_input(value: str) -> ParsedCredential:
    """Accept a bare token or a pasted proxy base URL; never keep the URL as a URL.

    A user copying from the UsePod dashboard or the drop-in docs gets
    ``https://api.usepod.ai/proxy/<token>/v1``. Stored as a "base URL" that string would be a
    credential sitting in a non-secret slot, so it is split here: the token goes to the credential
    store, the origin is validated and stored separately, and the URL itself is discarded.
    """
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    if not text:
        raise UsePodConfigError("credential_missing")
    if any(ch.isspace() for ch in text):
        raise UsePodConfigError("credential_contains_whitespace")
    if "://" not in text:
        token = validate_token(text)
        return ParsedCredential(
            token=token,
            origin=DEFAULT_ORIGIN,
            origin_from_paste=False,
            surface_hint="",
            token_shape=token_shape(token),
        )
    try:
        parts = urlsplit(text)
        parts.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError as exc:
        raise UsePodConfigError("credential_url_unparseable") from exc
    if parts.username is not None or parts.password is not None:
        raise UsePodConfigError("credential_url_has_userinfo")
    if parts.query or parts.fragment:
        raise UsePodConfigError("credential_url_has_query_or_fragment")
    origin = normalize_origin(f"{parts.scheme}://{parts.netloc}")
    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) < 2 or segments[0] != PROXY_PREFIX.strip("/"):
        raise UsePodConfigError("credential_url_is_not_a_proxy_base")
    if segments[1].lower() == X402_SEGMENT:
        raise UsePodConfigError("x402_path_carries_no_token")
    token = validate_token(segments[1])
    rest = segments[2:]
    if not rest:
        hint = WireProtocol.ANTHROPIC.value
    elif rest[0] == "v1":
        hint = WireProtocol.OPENAI.value
    else:
        hint = ""
    return ParsedCredential(
        token=token,
        origin=origin,
        origin_from_paste=True,
        surface_hint=hint,
        token_shape=token_shape(token),
    )


class ProxyTarget:
    """One UsePod request target whose printable forms never contain the token.

    ``path_template`` is what logs, receipts, fingerprints and exceptions may carry
    (``/proxy/{token}/v1/chat/completions``). The real path exists only inside the object and
    leaves it through :meth:`wire_url`, which only the transport calls.
    """

    __slots__ = ("_real_path", "origin", "path_template", "requires_token")

    def __init__(self, *, origin: str, path_template: str, real_path: str, requires_token: bool) -> None:
        self.origin = origin
        self.path_template = path_template
        self._real_path = real_path
        self.requires_token = bool(requires_token)

    def wire_url(self) -> str:
        return f"{self.origin}{self._real_path}"

    def wire_path(self) -> str:
        return self._real_path

    @property
    def redacted_url(self) -> str:
        return f"{self.origin}{self.path_template}"

    def __repr__(self) -> str:
        return f"ProxyTarget({self.redacted_url})"

    __str__ = __repr__

    def __reduce__(self):  # pragma: no cover - guard, exercised by a test
        raise TypeError("a UsePod proxy target carries a credential and cannot be serialized")


def protocol_path(protocol: WireProtocol | str) -> str:
    wire = WireProtocol(str(getattr(protocol, "value", protocol)))
    return OPENAI_CHAT_PATH if wire is WireProtocol.OPENAI else ANTHROPIC_MESSAGES_PATH


def prepaid_target(*, origin: str, token: str, surface_path: str) -> ProxyTarget:
    """A token-path target. ``surface_path`` is one of UsePod's own relative paths."""
    clean_origin = normalize_origin(origin)
    clean_token = validate_token(token)
    suffix = str(surface_path or "")
    if not suffix.startswith("/") or ".." in suffix or "?" in suffix or "#" in suffix:
        raise UsePodConfigError("surface_path_invalid")
    return ProxyTarget(
        origin=clean_origin,
        path_template=f"{PROXY_PREFIX}/{{token}}{suffix}",
        real_path=f"{PROXY_PREFIX}/{clean_token}{suffix}",
        requires_token=True,
    )


def x402_target(*, origin: str, protocol: WireProtocol | str) -> ProxyTarget:
    """The accountless target for a protocol. No token exists, so the template is the real path."""
    clean_origin = normalize_origin(origin)
    path = f"{PROXY_PREFIX}/{X402_SEGMENT}{protocol_path(protocol)}"
    return ProxyTarget(origin=clean_origin, path_template=path, real_path=path, requires_token=False)


def public_target(*, origin: str, path: str) -> ProxyTarget:
    """A public, credential-free path on the origin (the marketplace feed)."""
    clean_origin = normalize_origin(origin)
    clean_path = str(path or "")
    if not clean_path.startswith("/") or clean_path.startswith(f"{PROXY_PREFIX}/"):
        raise UsePodConfigError("public_path_invalid")
    return ProxyTarget(origin=clean_origin, path_template=clean_path, real_path=clean_path, requires_token=False)


def credential_fingerprint(origin: str, token: str) -> str:
    """A one-way identity for (origin, token): stable for one binding, different after rotation.

    Domain-separated SHA-256 over both, truncated to 128 bits. A UsePod token is a UUID (122 bits
    of entropy by the docs' own description), so the digest cannot be walked back to the token,
    and binding the origin means the same token re-pointed at another origin is a different
    identity -- discovery made against one host is never reused for another.
    """
    material = b"usepod.credential.v1\x00" + normalize_origin(origin).encode("utf-8") + b"\x00" + validate_token(token).encode("utf-8")
    return "upc_" + hashlib.sha256(material).hexdigest()[:32]


def endpoint_identity(origin: str, protocol: WireProtocol | str, mode: TransportMode | str) -> str:
    """The credential-free identity of one surface: safe for cache keys, events and receipts."""
    wire = WireProtocol(str(getattr(protocol, "value", protocol)))
    transport = TransportMode(str(getattr(mode, "value", mode)))
    return f"{PROVIDER_ID}|{normalize_origin(origin)}|{wire.value}|{transport.value}"


def remember_token_for_redaction(token: str) -> None:
    """Register the exact token with the central redactor the moment VOOL holds it."""
    from core.secret_redaction import register_exact_secret

    register_exact_secret(str(token or ""))


def redact(text: str) -> str:
    """The central redactor, re-exported so callers here never format a URL around it."""
    from core.secret_redaction import redact_secrets

    return redact_secrets(str(text or ""))


__all__ = [
    "ANTHROPIC_MESSAGES_PATH",
    "ANTHROPIC_VERSION",
    "BALANCE_PATH",
    "DEFAULT_ORIGIN",
    "DOCS_READ_ON",
    "DOCUMENTED_FACTS",
    "EVIDENCE_DOCUMENTED",
    "EVIDENCE_LIVE_OBSERVED",
    "EVIDENCE_UNVERIFIED",
    "HEADER_BALANCE_REMAINING",
    "HEADER_MAX_PRICE_INPUT",
    "HEADER_MAX_PRICE_OUTPUT",
    "HEADER_PAYMENT_REQUIRED",
    "HEADER_PAYMENT_RESPONSE",
    "HEADER_PAYMENT_SIGNATURE",
    "HEADER_PROVIDERS",
    "HEADER_PROVIDER_ID",
    "HEADER_ROUTE",
    "HEADER_ROUTING_MODE",
    "MARKETPLACE_MODELS_PATH",
    "MODELS_PATH",
    "OPENAI_CHAT_PATH",
    "PROVIDER_ID",
    "PROVIDER_LABEL",
    "DocumentedFact",
    "ParsedCredential",
    "ProxyTarget",
    "TransportMode",
    "UsePodConfigError",
    "WireProtocol",
    "credential_fingerprint",
    "endpoint_identity",
    "normalize_origin",
    "parse_credential_input",
    "prepaid_target",
    "protocol_path",
    "public_target",
    "redact",
    "remember_token_for_redaction",
    "token_shape",
    "validate_token",
    "x402_target",
]
