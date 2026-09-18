"""The HTTP boundary for UsePod: one immutable request envelope, sent exactly as sealed.

Three rules are enforced here instead of being hoped for at call sites:

* **Serialize once.** :func:`seal_request_envelope` turns a provider payload into bytes exactly once
  (ASCII JSON, so the bytes cross the provider HTTP worker's text channel unchanged) and records their
  SHA-256 and a binding hash over method, path and body. Every send -- the x402 paid retry included --
  writes ``envelope.body``. Nothing downstream re-serializes a dict.
* **The token never becomes a string that leaves.** Requests are built from
  :class:`core.usepod.descriptor.ProxyTarget`; failures carry typed codes and redacted details, never
  an HTTP library's own message (which embeds the full URL) and never a chained exception holding
  one; response headers are filtered to the documented metadata; redirects are refused, not followed.
* **Say what is known about dispatch.** Every failure states whether the request was certainly not
  sent, whether a response arrived, or whether the outcome is unknown. Money is settled on that
  distinction, and UNKNOWN is never read as unsent.

The x402 half is transport only. It quotes, parses the untrusted 402 strictly, binds a proof that the
wallet authority produced to that exact quote and envelope, and sends the paid retry. It never signs,
never pays, never follows a redirect with a payment attached, and never obtains a second payment for an
operation that already has one: a paid operation whose outcome is unknown can only be re-sent with the
SAME proof and the SAME bytes, explicitly, a bounded number of times.
"""
from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

from core.normalized_provider_result import MalformedProviderResponseError
from core.secret_redaction import redact_secrets, register_public_identifier
from core.usepod.descriptor import (
    ANTHROPIC_VERSION,
    BALANCE_PATH,
    HEADER_PAYMENT_SIGNATURE,
    MODELS_PATH,
    PROVIDER_ID,
    PROXY_PREFIX,
    ProxyTarget,
    TransportMode,
    WireProtocol,
    normalize_origin,
    prepaid_target,
    protocol_path,
    x402_target,
)
from core.usepod.monetary import (
    ACCOUNT_KIND_X402_PAYER,
    EXACT_COST_NOT_SUPPLIED,
    OUTCOME_FAILED_AFTER_SEND,
    OUTCOME_UNKNOWN,
    MonetaryAuthority,
    MonetaryAuthorityRefusedError,
    MonetaryAuthorityUnavailableError,
    MonetaryReservation,
    ProviderLiability,
    SettlementEvidence,
    monetary_authority,
)

MAX_REQUEST_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_STREAM_LINE_BYTES = 512 * 1024
MAX_DISCOVERY_BYTES = 2 * 1024 * 1024
MAX_PAYMENT_HEADER_CHARS = 16 * 1024

DISPATCH_NOT_SENT = "not_sent"
DISPATCH_OUTCOME_UNKNOWN = "sent_outcome_unknown"
DISPATCH_RESPONSE_RECEIVED = "response_received"

X402_SUPPORTED_VERSION = 2
X402_SCHEME_EXACT = "exact"
X402_MODE_CAP_WITH_SURPLUS = "cap-with-surplus-credit"
ATOMIC_UNIT_BY_ASSET = {"USDC": "usdc_microunit", "SOL": "lamport"}
#: A quote the provider says expires sooner than this is not paid: the owner still has to approve, the wallet
#: still has to sign, send and see confirmation, and the settle request still has to arrive before the expiry.
X402_MIN_QUOTE_VALIDITY_SECONDS = 20.0
#: How this client names itself on every UsePod request (the documented x402 example sends one too).
USER_AGENT = "vool-usepod/1"

_KEPT_RESPONSE_HEADERS = frozenset(
    {
        "content-type",
        "retry-after",
        "x-pod-route",
        "x-pod-provider-id",
        "x-balance-remaining",
        "payment-required",
        "payment-response",
        "location",
        "x-request-id",
        "request-id",
    }
)
_FORBIDDEN_REQUEST_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie", "x-api-key"})
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")
_HEADER_VALUE_RE = re.compile(r"^[\x20-\x7e]{0,16384}$")
_BASE58_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_BASE58_SIGNATURE_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{64,88}$")
_QUOTE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_NETWORK_RE = re.compile(r"^[a-z0-9-]{3,32}:[A-Za-z0-9]{1,64}$")
_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,255}$")
_HINT_RE = re.compile(r"[^A-Za-z0-9_.:-]")
_RFC3339_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[Tt ](\d{2}:\d{2}:\d{2})(\.\d{1,9})?([Zz]|[+-]\d{2}:\d{2})$")
# An HTML document where JSON was owed is named as such, and an edge block page names the host that blocked
# the gateway and its ray id -- the only two facts a provider can act on. Nothing else is read out of HTML.
_HTML_DOCUMENT_RE = re.compile(rb"<!doctype\s+html|<html[\s>]", re.IGNORECASE)
_HTML_TITLE_RE = re.compile(rb"<title>\s*([^<]{1,120}?)\s*</title>", re.IGNORECASE | re.DOTALL)
_CF_BLOCKED_HOST_RE = re.compile(rb"unable to access</span>\s*([A-Za-z0-9.-]{1,253})")
_CF_RAY_RE = re.compile(rb"Cloudflare Ray ID:\s*<strong[^>]*>\s*([0-9a-fA-F]{8,32})\s*</strong>")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_rfc3339_epoch(value: Any) -> float | None:
    """An RFC 3339 timestamp (any fractional precision, Z or numeric offset) as a UTC epoch; None when unreadable."""
    if not isinstance(value, str):
        return None
    match = _RFC3339_RE.fullmatch(value.strip())
    if match is None:
        return None
    date_part, time_part, fraction, zone = match.groups()
    micros = (fraction or ".0")[1:7].ljust(6, "0")
    offset = "+00:00" if zone in ("Z", "z") else zone
    try:
        moment = datetime.fromisoformat(f"{date_part}T{time_part}.{micros}{offset}")
    except ValueError:
        return None
    return moment.timestamp()


class UsePodTransportError(RuntimeError):
    """A typed UsePod transport failure. ``dispatch_state`` is what is known about the request."""

    def __init__(
        self,
        code: str,
        *,
        dispatch_state: str,
        http_status: int | None = None,
        retry_after_seconds: float | None = None,
        detail: str = "",
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = str(code)
        self.dispatch_state = str(dispatch_state)
        self.http_status = int(http_status) if isinstance(http_status, int) else None
        self.retry_after_seconds = retry_after_seconds
        self.detail = redact_secrets(str(detail or ""))[:240]
        self.provider_evidence = dict(evidence or {})
        status = f" status={self.http_status}" if self.http_status is not None else ""
        suffix = f": {self.detail}" if self.detail else ""
        super().__init__(redact_secrets(f"usepod_transport:{self.code}{status} dispatch={self.dispatch_state}{suffix}"))


class X402QuoteError(UsePodTransportError):
    """A 402 quote that cannot be trusted as a payment request."""

    def __init__(self, code: str, *, detail: str = "") -> None:
        super().__init__(code, dispatch_state=DISPATCH_RESPONSE_RECEIVED, http_status=402, detail=detail)


class X402ProofError(RuntimeError):
    """A proof that does not bind to the quote and request it claims to pay for."""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(f"x402_proof_rejected:{self.code}")


class X402OperationStateError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code)
        super().__init__(f"x402_operation:{self.code}" + (f": {detail}" if detail else ""))


# --- the immutable envelope ----------------------------------------------------------------------------


def _binding_sha256(method: str, path: str, body_sha256: str) -> str:
    return hashlib.sha256(f"{method}\n{path}\n{body_sha256}".encode("ascii")).hexdigest()


def _validate_header(name: str, value: str) -> None:
    if not isinstance(name, str) or not _HEADER_NAME_RE.fullmatch(name):
        raise UsePodTransportError("request_header_name_invalid", dispatch_state=DISPATCH_NOT_SENT)
    if not isinstance(value, str) or not _HEADER_VALUE_RE.fullmatch(value):
        raise UsePodTransportError("request_header_value_invalid", dispatch_state=DISPATCH_NOT_SENT, detail=name)
    if name.lower() in _FORBIDDEN_REQUEST_HEADERS:
        raise UsePodTransportError("request_header_forbidden", dispatch_state=DISPATCH_NOT_SENT, detail=name.lower())


def prepaid_path_template(protocol: WireProtocol | str) -> str:
    return f"{PROXY_PREFIX}/{{token}}{protocol_path(protocol)}"


@dataclass(frozen=True)
class RequestEnvelope:
    """One request, sealed: the bytes that will be written, and nothing that can change them."""

    operation_id: str
    transport_mode: str
    protocol: str
    method: str
    origin: str
    path_template: str
    body: bytes = field(repr=False)
    body_sha256: str
    binding_sha256: str
    control_headers: tuple[tuple[str, str], ...]
    model_id: str
    max_output_tokens: int
    stream: bool
    route_approval_id: str
    created_at: float

    def __post_init__(self) -> None:
        if self.method != "POST":
            raise ValueError("a UsePod inference envelope is a POST")
        # Raises ValueError for a mode or dialect this transport does not speak.
        TransportMode(self.transport_mode)
        WireProtocol(self.protocol)
        if not isinstance(self.body, bytes):
            raise TypeError("envelope body must be immutable bytes")
        if hashlib.sha256(self.body).hexdigest() != self.body_sha256:
            raise ValueError("envelope body does not match its hash")
        if _binding_sha256(self.method, self.path_template, self.body_sha256) != self.binding_sha256:
            raise ValueError("envelope binding does not match method, path and body")
        for name, value in self.control_headers:
            _validate_header(name, value)
        if isinstance(self.max_output_tokens, bool) or not isinstance(self.max_output_tokens, int) or self.max_output_tokens < 1:
            raise ValueError("envelope needs a positive output ceiling")

    def payload(self) -> dict[str, Any]:
        """A fresh parse of the sealed bytes -- for reading, never for re-sending."""
        return json.loads(self.body.decode("ascii"))

    def as_evidence(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "transport_mode": self.transport_mode,
            "protocol": self.protocol,
            "method": self.method,
            "origin": self.origin,
            "path": self.path_template,
            "body_sha256": self.body_sha256,
            "body_bytes": len(self.body),
            "binding_sha256": self.binding_sha256,
            "control_headers": [[name, value] for name, value in self.control_headers],
            "model_id": self.model_id,
            "max_output_tokens": self.max_output_tokens,
            "stream": self.stream,
            "route_approval_id": self.route_approval_id,
            "created_at": self.created_at,
        }


def seal_request_envelope(
    *,
    payload: Mapping[str, Any],
    transport_mode: TransportMode | str,
    protocol: WireProtocol | str,
    origin: str,
    model_id: str,
    route_approval_id: str,
    control_headers: tuple[tuple[str, str], ...] = (),
    operation_id: str | None = None,
    clock: Callable[[], float] = time.time,
) -> RequestEnvelope:
    """Serialize ``payload`` ONCE and seal it with its path and control headers."""
    mode = TransportMode(str(getattr(transport_mode, "value", transport_mode)))
    wire = WireProtocol(str(getattr(protocol, "value", protocol)))
    clean_origin = normalize_origin(origin)
    if not isinstance(payload, Mapping):
        raise UsePodTransportError("payload_not_an_object", dispatch_state=DISPATCH_NOT_SENT)
    if str(payload.get("model") or "") != str(model_id or "") or not _MODEL_ID_RE.fullmatch(str(model_id or "")):
        raise UsePodTransportError("payload_model_mismatch", dispatch_state=DISPATCH_NOT_SENT)
    if wire is WireProtocol.ANTHROPIC:
        max_output = payload.get("max_tokens")
    else:
        max_output = payload.get("max_tokens", payload.get("max_completion_tokens"))
    if isinstance(max_output, bool) or not isinstance(max_output, int) or max_output < 1:
        # Without an output ceiling no cost bound exists, and x402 cannot even be quoted.
        raise UsePodTransportError("max_output_tokens_required", dispatch_state=DISPATCH_NOT_SENT)
    try:
        body = json.dumps(dict(payload), ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    except (TypeError, ValueError):
        body = b""
    if not body:
        raise UsePodTransportError("payload_not_serializable", dispatch_state=DISPATCH_NOT_SENT)
    if len(body) > MAX_REQUEST_BYTES:
        raise UsePodTransportError("request_too_large", dispatch_state=DISPATCH_NOT_SENT)
    path = prepaid_path_template(wire) if mode is TransportMode.PREPAID else x402_target(origin=clean_origin, protocol=wire).path_template
    body_sha = hashlib.sha256(body).hexdigest()
    return RequestEnvelope(
        operation_id=str(operation_id or f"upo_{uuid.uuid4().hex}"),
        transport_mode=mode.value,
        protocol=wire.value,
        method="POST",
        origin=clean_origin,
        path_template=path,
        body=body,
        body_sha256=body_sha,
        binding_sha256=_binding_sha256("POST", path, body_sha),
        control_headers=tuple((str(name), str(value)) for name, value in control_headers),
        model_id=str(model_id),
        max_output_tokens=int(max_output),
        stream=bool(payload.get("stream")),
        route_approval_id=str(route_approval_id or ""),
        created_at=float(clock()),
    )


# --- responses ------------------------------------------------------------------------------------------


def _kept_headers(raw: Any) -> dict[str, str]:
    kept: dict[str, str] = {}
    try:
        items = list(raw.items()) if hasattr(raw, "items") else list(raw or [])
    except Exception:
        return kept
    for key, value in items:
        name = str(key).lower()
        if name not in _KEPT_RESPONSE_HEADERS:
            continue
        text = str(value)
        # Location is server-chosen and may repeat a credential-bearing path; everything else kept here
        # is metadata the provider sends about itself and must stay byte-exact (a base64 quote). One
        # character past the bound is kept so an oversized quote reads as oversized, never as a
        # truncated document that happens to decode.
        kept[name] = redact_secrets(text)[:512] if name == "location" else text[: MAX_PAYMENT_HEADER_CHARS + 1]
    return kept


class UsePodResponse:
    """A response whose body is read with bounds and whose failures are typed."""

    def __init__(self, *, status: int, headers: Mapping[str, str], raw: Any, streaming: bool) -> None:
        self.status = int(status)
        self.headers = dict(headers)
        self._raw = raw
        self.streaming = bool(streaming)

    def header(self, name: str) -> str | None:
        return self.headers.get(str(name).lower())

    def read_bytes(self, *, limit: int = MAX_RESPONSE_BYTES) -> bytes:
        failure = ""
        content = b""
        try:
            content = bytes(getattr(self._raw, "content", b"") or b"")
        except Exception as exc:
            failure = type(exc).__name__
        finally:
            self.close()
        if failure:
            raise UsePodTransportError("response_body_interrupted", dispatch_state=DISPATCH_OUTCOME_UNKNOWN, http_status=self.status, detail=failure)
        if len(content) > limit:
            raise UsePodTransportError("response_too_large", dispatch_state=DISPATCH_RESPONSE_RECEIVED, http_status=self.status)
        return content

    def json(self) -> Any:
        body = self.read_bytes()
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            pass
        raise MalformedProviderResponseError("malformed provider response: usepod response body is not JSON")

    def iter_lines(self) -> Iterator[str]:
        """Lines of a streamed body. A transfer that breaks mid-stream raises a typed UNKNOWN."""
        pending = b""
        chunks = getattr(self._raw, "iter_content", None)
        if not callable(chunks):
            raise UsePodTransportError("response_not_streamable", dispatch_state=DISPATCH_RESPONSE_RECEIVED, http_status=self.status)
        iterator = iter(chunks(chunk_size=4096))
        while True:
            failure = ""
            chunk = b""
            try:
                chunk = next(iterator)
            except StopIteration:
                break
            except Exception as exc:
                failure = type(exc).__name__
            if failure:
                self.close()
                raise UsePodTransportError("stream_interrupted", dispatch_state=DISPATCH_OUTCOME_UNKNOWN, http_status=self.status, detail=failure)
            pending += bytes(chunk or b"")
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                yield line.rstrip(b"\r").decode("utf-8", errors="replace")
            if len(pending) > MAX_STREAM_LINE_BYTES:
                self.close()
                raise MalformedProviderResponseError("malformed provider response: usepod stream line exceeds the bound")
        if pending:
            yield pending.rstrip(b"\r").decode("utf-8", errors="replace")
        self.close()

    def close(self) -> None:
        close = getattr(self._raw, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()


_STATUS_CODES = {
    400: "request_rejected",
    401: "token_rejected",
    402: "payment_or_balance_required",
    403: "request_forbidden",
    404: "model_or_surface_not_found",
    409: "request_conflict",
    413: "request_too_large_for_provider",
    422: "request_rejected",
    429: "throttled",
}


def _error_hint(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return ""
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        hint = str(error.get("type") or error.get("code") or "")
    elif isinstance(error, str):
        hint = error
    else:
        hint = ""
    return _HINT_RE.sub("_", hint)[:64]


def _html_document_facts(body: bytes, content_type: str) -> dict[str, Any]:
    """What an HTML body says about itself where JSON was owed: kind, title, and for a Cloudflare block page the
    blocked host and ray id. ``hint`` is the sanitized short form for the typed error's detail."""
    head = body[:4096]
    if "html" not in str(content_type or "").lower() and not _HTML_DOCUMENT_RE.search(head):
        return {}
    facts: dict[str, Any] = {"kind": "html", "bytes": len(body)}
    title = _HTML_TITLE_RE.search(head)
    if title is not None:
        facts["title"] = redact_secrets(title.group(1).decode("utf-8", errors="replace"))[:120]
    host = _CF_BLOCKED_HOST_RE.search(body)
    ray = _CF_RAY_RE.search(body)
    if host is not None and (b"cf-error-details" in body or ray is not None):
        facts["edge"] = "cloudflare"
        facts["blocked_host"] = host.group(1).decode("ascii", errors="replace").lower()[:253]
        if ray is not None:
            facts["ray_id"] = ray.group(1).decode("ascii", errors="replace").lower()
        facts["hint"] = _HINT_RE.sub("_", f"edge_block:cloudflare:{facts['blocked_host']}" + (f":ray-{facts['ray_id']}" if ray is not None else ""))[:200]
    else:
        facts["hint"] = "html_document"
    return facts


def _retry_after(headers: Mapping[str, str]) -> float | None:
    try:
        value = float(headers.get("retry-after") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def error_for_response(response: UsePodResponse, *, origin: str) -> UsePodTransportError:
    """The typed error for a non-2xx response. Reads (and closes) a bounded body for a hint."""
    status = response.status
    try:
        body = response.read_bytes(limit=16 * 1024)
    except Exception:
        body = b""
    if 300 <= status < 400:
        location = response.header("location") or ""
        try:
            target = urlsplit(location)
            host = (target.hostname or "").lower()
            cross = bool(target.scheme and host) and normalize_origin(f"{target.scheme}://{target.netloc}") != normalize_origin(origin)
        except Exception:
            host, cross = "", True
        return UsePodTransportError(
            "redirect_refused",
            dispatch_state=DISPATCH_RESPONSE_RECEIVED,
            http_status=status,
            detail=f"location_host={host or 'unparseable'} cross_origin={str(cross).lower()}",
        )
    if status == 503:
        code = "no_provider_available"
    elif status in _STATUS_CODES:
        code = _STATUS_CODES[status]
    elif status >= 500:
        code = "upstream_failure"
    else:
        code = "unexpected_status"
    document = _html_document_facts(body, response.header("content-type") or "")
    return UsePodTransportError(
        code,
        dispatch_state=DISPATCH_RESPONSE_RECEIVED,
        http_status=status,
        retry_after_seconds=_retry_after(response.headers),
        detail=_error_hint(body) or str(document.get("hint") or ""),
        evidence={"response_document": {key: value for key, value in document.items() if key != "hint"}} if document else None,
    )


# --- the transport --------------------------------------------------------------------------------------


class UsePodHttpTransport:
    """POSTs sealed envelopes through the provider HTTP worker; GETs discovery through the governed door."""

    def __init__(self, *, http: Any | None = None, connect_timeout_seconds: float = 10.0) -> None:
        self._http = http
        self._connect_timeout = max(1.0, float(connect_timeout_seconds))

    def _client(self) -> Any:
        if self._http is not None:
            return self._http
        from core import provider_http

        return provider_http

    def post_envelope(
        self,
        envelope: RequestEnvelope,
        *,
        origin: str,
        token: str | None,
        extra_headers: tuple[tuple[str, str], ...] = (),
        read_timeout_seconds: float,
    ) -> UsePodResponse:
        clean_origin = normalize_origin(origin)
        if clean_origin != envelope.origin:
            raise UsePodTransportError("envelope_origin_mismatch", dispatch_state=DISPATCH_NOT_SENT)
        mode = TransportMode(envelope.transport_mode)
        wire = WireProtocol(envelope.protocol)
        if mode is TransportMode.PREPAID:
            if not token:
                raise UsePodTransportError("prepaid_token_missing", dispatch_state=DISPATCH_NOT_SENT)
            target = prepaid_target(origin=clean_origin, token=token, surface_path=protocol_path(wire))
        else:
            if token:
                raise UsePodTransportError("x402_request_must_not_carry_a_token", dispatch_state=DISPATCH_NOT_SENT)
            target = x402_target(origin=clean_origin, protocol=wire)
        if target.path_template != envelope.path_template:
            raise UsePodTransportError("envelope_path_mismatch", dispatch_state=DISPATCH_NOT_SENT)
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if envelope.stream else "application/json",
            "User-Agent": USER_AGENT,
        }
        if wire is WireProtocol.ANTHROPIC:
            headers["anthropic-version"] = ANTHROPIC_VERSION
        for name, value in (*envelope.control_headers, *extra_headers):
            _validate_header(name, value)
            headers[name] = value
        failure = ""
        raw: Any = None
        try:
            raw = self._client().post(
                target.wire_url(),
                data=envelope.body.decode("ascii"),
                headers=headers,
                timeout=(min(self._connect_timeout, float(read_timeout_seconds)), float(read_timeout_seconds)),
                allow_redirects=False,
                stream=envelope.stream,
            )
        except Exception as exc:
            failure = type(exc).__name__
        if failure:
            # Raised OUTSIDE the except block: the library exception (whose text can hold the URL)
            # is not chained onto what the runtime logs.
            raise UsePodTransportError("transfer_ended_without_response", dispatch_state=DISPATCH_OUTCOME_UNKNOWN, detail=failure)
        return UsePodResponse(
            status=int(getattr(raw, "status_code", 0) or 0),
            headers=_kept_headers(getattr(raw, "headers", {}) or {}),
            raw=raw,
            streaming=envelope.stream,
        )

    def get_json(self, target: ProxyTarget, *, timeout_seconds: float = 10.0) -> tuple[int, dict[str, str], Any]:
        """A discovery GET through the governed outbound door, redirects refused."""
        import urllib.error
        import urllib.request

        from core.remote_fetch_policy import RemoteFetchRefusedError, open_remote

        request = urllib.request.Request(
            target.wire_url(), headers={"Accept": "application/json", "User-Agent": USER_AGENT}, method="GET"
        )
        status = 0
        headers: dict[str, str] = {}
        body = b""
        failure = ""
        refused = False
        try:
            response = open_remote(
                request,
                timeout=float(timeout_seconds),
                provider_id=PROVIDER_ID,
                keyed_or_keyless="keyed" if target.requires_token else "keyless",
                redirect_policy="refuse",
            )
            try:
                status = int(getattr(response, "status", 0) or 0)
                headers = _kept_headers(getattr(response, "headers", {}) or {})
                body = response.read(MAX_DISCOVERY_BYTES + 1)
            finally:
                with contextlib.suppress(Exception):
                    response.close()
        except urllib.error.HTTPError as exc:
            status = int(exc.code or 0)
            headers = _kept_headers(getattr(exc, "headers", {}) or {})
            with contextlib.suppress(Exception):
                body = exc.read(16 * 1024)
            with contextlib.suppress(Exception):
                exc.close()
        except RemoteFetchRefusedError:
            refused = True
        except Exception as exc:
            failure = type(exc).__name__
        if refused:
            raise UsePodTransportError("remote_fetch_refused", dispatch_state=DISPATCH_NOT_SENT)
        if failure:
            raise UsePodTransportError("discovery_unreachable", dispatch_state=DISPATCH_OUTCOME_UNKNOWN, detail=failure)
        if len(body) > MAX_DISCOVERY_BYTES:
            raise UsePodTransportError("discovery_response_too_large", dispatch_state=DISPATCH_RESPONSE_RECEIVED, http_status=status)
        if not 200 <= status < 300:
            return_error = error_for_response(
                UsePodResponse(status=status, headers=headers, raw=_BufferedRaw(body), streaming=False), origin=target.origin
            )
            raise return_error
        try:
            payload = json.loads(bytes(body).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            payload = _NOT_JSON
        if payload is _NOT_JSON:
            raise MalformedProviderResponseError("malformed provider response: usepod discovery body is not JSON")
        return status, headers, payload


_NOT_JSON = object()


class _BufferedRaw:
    def __init__(self, content: bytes) -> None:
        self.content = bytes(content or b"")

    def close(self) -> None:
        return None


# --- credentialed discovery (no inference, no spend) ------------------------------------------------------


@dataclass(frozen=True)
class ModelListing:
    model_ids: tuple[str, ...]
    shape: str
    rejected_entries: int
    observed_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_ids": list(self.model_ids),
            "shape": self.shape,
            "rejected_entries": self.rejected_entries,
            "observed_at": self.observed_at,
            "evidence": "response_to_this_credential",
            "schema_published": False,
        }


def list_token_models(
    *, transport: UsePodHttpTransport, origin: str, token: str, clock: Callable[[], float] = time.time
) -> ModelListing:
    """``GET /proxy/<token>/v1/models``. A model list, never a price list."""
    from core.effect_gateway import named_background_effect_scope

    target = prepaid_target(origin=origin, token=token, surface_path=MODELS_PATH)
    with named_background_effect_scope("usepod.model_discovery"):
        _status, _headers, payload = transport.get_json(target)
    entries = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise MalformedProviderResponseError("malformed provider response: usepod /v1/models is not a data list")
    ids: list[str] = []
    rejected = 0
    for entry in entries:
        model_id = entry.get("id") if isinstance(entry, dict) else None
        if isinstance(model_id, str) and _MODEL_ID_RE.fullmatch(model_id):
            if model_id not in ids:
                ids.append(model_id)
        else:
            rejected += 1
    return ModelListing(model_ids=tuple(ids), shape="openai_list", rejected_entries=rejected, observed_at=float(clock()))


@dataclass(frozen=True)
class BalanceObservation:
    usdc_balance_microunits: int | None
    state: str
    observed_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "usdc_balance_microunits": self.usdc_balance_microunits,
            "state": self.state,
            "observed_at": self.observed_at,
            "unit": "usdc_microunit",
            "evidence": "response_to_this_credential",
            "note": "an observation, not a spending authority; concurrent use makes it stale immediately",
        }


def read_token_balance(
    *, transport: UsePodHttpTransport, origin: str, token: str, clock: Callable[[], float] = time.time
) -> BalanceObservation:
    """``GET /proxy/<token>/balance`` -- documented to return ``usdc_balance`` in microunits."""
    from core.effect_gateway import named_background_effect_scope

    target = prepaid_target(origin=origin, token=token, surface_path=BALANCE_PATH)
    with named_background_effect_scope("usepod.balance_probe"):
        _status, _headers, payload = transport.get_json(target)
    if not isinstance(payload, dict) or "usdc_balance" not in payload:
        return BalanceObservation(None, "field_absent", float(clock()))
    value = payload.get("usdc_balance")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return BalanceObservation(None, "field_malformed", float(clock()))
    return BalanceObservation(int(value), "reported", float(clock()))


# --- x402: the untrusted quote, the bound proof, the paid retry ------------------------------------------


@dataclass(frozen=True)
class X402PaymentOption:
    index: int
    asset: str
    scheme: str
    network: str
    pay_to: str
    amount_atomic: int | None
    atomic_unit: str
    mode: str
    problems: tuple[str, ...]
    extra_fields: tuple[str, ...]
    #: The provider's own validity limit for this option (UTC epoch), when the quote states one.
    expires_at_epoch: float | None = None

    @property
    def usable(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "asset": self.asset,
            "scheme": self.scheme,
            "network": self.network,
            "pay_to": self.pay_to,
            "amount_atomic": self.amount_atomic,
            "atomic_unit": self.atomic_unit,
            "mode": self.mode,
            "problems": list(self.problems),
            "extra_fields": list(self.extra_fields),
            "expires_at_epoch": self.expires_at_epoch,
        }


_DOCUMENTED_OPTION_FIELDS = frozenset({"asset", "scheme", "network", "pay_to", "amount_microunits", "mode"})
#: Read when present although the published schema omits it: the live gateway states a per-option expiry.
_READ_OPTION_FIELDS = _DOCUMENTED_OPTION_FIELDS | {"expires_at"}


def _parse_option(index: int, raw: Any) -> X402PaymentOption:
    if not isinstance(raw, dict):
        return X402PaymentOption(index, "", "", "", "", None, "", "", ("option_not_an_object",), ())
    problems: list[str] = []
    asset = raw.get("asset") if isinstance(raw.get("asset"), str) else ""
    unit = ATOMIC_UNIT_BY_ASSET.get(asset, "")
    if not unit:
        problems.append("asset_unrecognized")
    scheme = raw.get("scheme") if isinstance(raw.get("scheme"), str) else ""
    if scheme != X402_SCHEME_EXACT:
        problems.append("scheme_unrecognized")
    network = raw.get("network") if isinstance(raw.get("network"), str) else ""
    if not _NETWORK_RE.fullmatch(network):
        problems.append("network_malformed")
    pay_to = raw.get("pay_to") if isinstance(raw.get("pay_to"), str) else ""
    if not _BASE58_ADDRESS_RE.fullmatch(pay_to):
        problems.append("pay_to_malformed")
    raw_amount = raw.get("amount_microunits")
    amount: int | None = None
    if isinstance(raw_amount, bool) or not isinstance(raw_amount, int):
        # The docs define this field per asset (USDC microunits or lamports): a fraction or a string
        # is not a quantity in either unit.
        problems.append("amount_not_integer_atomic_units")
    elif raw_amount < 1:
        problems.append("amount_not_positive")
    else:
        amount = raw_amount
    mode = raw.get("mode") if isinstance(raw.get("mode"), str) else ""
    if mode != X402_MODE_CAP_WITH_SURPLUS:
        problems.append("mode_unrecognized")
    expires_at: float | None = None
    if "expires_at" in raw:
        expires_at = parse_rfc3339_epoch(raw.get("expires_at"))
        if expires_at is None:
            # A stated expiry that cannot be read is a validity limit nobody can honour; the option is not paid.
            problems.append("expiry_unreadable")
    extra = tuple(sorted(str(key)[:48] for key in raw if key not in _READ_OPTION_FIELDS))
    return X402PaymentOption(index, asset, scheme, network[:96], pay_to[:64], amount, unit, mode[:64], tuple(problems), extra, expires_at)


@dataclass(frozen=True)
class X402Quote:
    operation_id: str
    envelope_binding_sha256: str
    origin: str
    path: str
    x402_version: int
    quote_id: str
    options: tuple[X402PaymentOption, ...]
    header_sha256: str
    received_at: float
    extra_fields: tuple[str, ...]

    def usable_options(self) -> tuple[X402PaymentOption, ...]:
        return tuple(option for option in self.options if option.usable)

    def as_evidence(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "envelope_binding_sha256": self.envelope_binding_sha256,
            "origin": self.origin,
            "path": self.path,
            "x402_version": self.x402_version,
            "quote_id": self.quote_id,
            "options": [option.as_dict() for option in self.options],
            "header_sha256": self.header_sha256,
            "received_at": self.received_at,
            "extra_fields": list(self.extra_fields),
            "expiry": self.expiry_evidence(),
        }

    def expiry_evidence(self) -> dict[str, Any]:
        stated = [option.expires_at_epoch for option in self.options if option.expires_at_epoch is not None]
        if not stated:
            return {"state": "not_stated_by_provider"}
        return {"state": "stated_per_option", "earliest_epoch": min(stated)}


def parse_payment_required(
    header_value: str | None, *, envelope: RequestEnvelope, origin: str, received_at: float
) -> X402Quote:
    """Parse the ``PAYMENT-REQUIRED`` header strictly. The quote is hostile until every field reads."""
    text = str(header_value or "").strip()
    if not text:
        raise X402QuoteError("quote_header_missing")
    if len(text) > MAX_PAYMENT_HEADER_CHARS:
        raise X402QuoteError("quote_header_too_large")
    try:
        decoded = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        decoded = None
    if decoded is None:
        raise X402QuoteError("quote_header_not_base64")
    try:
        data = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        data = _NOT_JSON
    if data is _NOT_JSON:
        raise X402QuoteError("quote_not_json")
    if not isinstance(data, dict):
        raise X402QuoteError("quote_not_an_object")
    version = data.get("x402_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise X402QuoteError("quote_version_missing")
    if version != X402_SUPPORTED_VERSION:
        raise X402QuoteError("quote_version_unsupported", detail=str(version)[:16])
    quote_id = data.get("quote_id")
    if not isinstance(quote_id, str) or not _QUOTE_ID_RE.fullmatch(quote_id):
        raise X402QuoteError("quote_id_invalid")
    accepts = data.get("accepts")
    if not isinstance(accepts, list) or not accepts:
        raise X402QuoteError("quote_has_no_payment_options")
    return X402Quote(
        operation_id=envelope.operation_id,
        envelope_binding_sha256=envelope.binding_sha256,
        origin=normalize_origin(origin),
        path=envelope.path_template,
        x402_version=version,
        quote_id=quote_id,
        options=tuple(_parse_option(index, raw) for index, raw in enumerate(accepts[:16])),
        header_sha256=hashlib.sha256(text.encode("ascii", errors="replace")).hexdigest(),
        received_at=float(received_at),
        extra_fields=tuple(sorted(str(key)[:48] for key in data if key not in {"x402_version", "quote_id", "accepts"})),
    )


@dataclass(frozen=True)
class X402PaymentProof:
    """What the wallet authority returns after it has validated, reserved, signed and confirmed."""

    operation_id: str
    envelope_binding_sha256: str
    quote_header_sha256: str
    quote_id: str
    network: str
    asset: str
    pay_to: str
    amount_atomic: int
    payer_wallet: str
    signature: str
    authority_label: str
    issued_at: float

    def public_fields(self) -> dict[str, Any]:
        return {
            "quote_id": self.quote_id,
            "network": self.network,
            "asset": self.asset,
            "pay_to": self.pay_to,
            "amount_atomic": self.amount_atomic,
            "payer_wallet": self.payer_wallet,
            "signature": self.signature,
            "authority_label": self.authority_label,
            "issued_at": self.issued_at,
        }


class X402PaidResultUnknownError(UsePodTransportError):
    """The wallet paid for this request (its proof is bound to these bytes) and no usable answer came back.

    It keeps the underlying failure's code, dispatch state and HTTP status, so every caller that judges a transport failure
    judges this one the same way. Its message names the case, so the chat reports a paid call whose result is unknown
    instead of an ordinary failure. It carries the payment's public facts only.
    """

    MARKER = "usepod_x402_paid_result_unknown"

    def __init__(self, cause: UsePodTransportError, *, proof: X402PaymentProof, operation_id: str, paid_attempts: int) -> None:
        super().__init__(
            cause.code,
            dispatch_state=cause.dispatch_state,
            http_status=cause.http_status,
            retry_after_seconds=cause.retry_after_seconds,
            detail=cause.detail,
            evidence=cause.provider_evidence,
        )
        self.provider_evidence.update(
            {"x402_operation_id": operation_id, "payment_retained": True, "paid_attempts": paid_attempts, "x402_payment": proof.public_fields()}
        )
        status = f" status={self.http_status}" if self.http_status is not None else ""
        suffix = f": {self.detail}" if self.detail else ""
        self.args = (f"{self.MARKER}:{self.code}{status} dispatch={self.dispatch_state}{suffix}",)


def payment_signature_header(proof: X402PaymentProof, *, quote: X402Quote, envelope: RequestEnvelope) -> str:
    """The ``PAYMENT-SIGNATURE`` value, built only from a proof bound to THIS quote and THESE bytes."""
    if not isinstance(proof, X402PaymentProof):
        raise X402ProofError("proof_type_invalid")
    if not (proof.operation_id == envelope.operation_id == quote.operation_id):
        raise X402ProofError("proof_bound_to_another_operation")
    if not (proof.envelope_binding_sha256 == envelope.binding_sha256 == quote.envelope_binding_sha256):
        raise X402ProofError("proof_bound_to_different_request_bytes")
    if proof.quote_id != quote.quote_id or proof.quote_header_sha256 != quote.header_sha256:
        raise X402ProofError("proof_bound_to_another_quote")
    if not any(
        option.usable
        and option.asset == proof.asset
        and option.network == proof.network
        and option.pay_to == proof.pay_to
        and option.amount_atomic == proof.amount_atomic
        for option in quote.options
    ):
        raise X402ProofError("proof_does_not_match_a_quoted_option")
    if not _BASE58_ADDRESS_RE.fullmatch(str(proof.payer_wallet or "")):
        raise X402ProofError("payer_wallet_malformed")
    if not _BASE58_SIGNATURE_RE.fullmatch(str(proof.signature or "")):
        raise X402ProofError("signature_malformed")
    label = str(proof.authority_label or "")
    if not label or label.startswith("unavailable:"):
        raise X402ProofError("proof_without_a_payment_authority")
    document = {
        "quote_id": proof.quote_id,
        "network": proof.network,
        "asset": proof.asset,
        "payer_wallet": proof.payer_wallet,
        "signature": proof.signature,
    }
    return base64.b64encode(json.dumps(document, separators=(",", ":")).encode("ascii")).decode("ascii")


def decode_payment_response(value: str | None) -> dict[str, Any]:
    """The ``PAYMENT-RESPONSE`` receipt as evidence. Its schema is unpublished; nothing is inferred."""
    if not value:
        return {"state": "absent"}
    text = str(value).strip()[:MAX_PAYMENT_HEADER_CHARS]
    digest = hashlib.sha256(text.encode("ascii", errors="replace")).hexdigest()
    try:
        data = json.loads(base64.b64decode(text, validate=True).decode("utf-8"))
    except Exception:
        return {"state": "undecodable", "sha256": digest}
    if not isinstance(data, dict):
        return {"state": "not_an_object", "sha256": digest}
    fields: dict[str, Any] = {}
    for key, item in list(data.items())[:24]:
        name = str(key)[:48]
        if isinstance(item, bool) or item is None or isinstance(item, int):
            fields[name] = item
        elif isinstance(item, (float, str)):
            fields[name] = str(item)[:128]
    return {"state": "decoded_schema_unpublished", "sha256": digest, "fields": fields}


class PaymentAuthorityUnavailableError(RuntimeError):
    code = "wallet_payment_authority_unavailable"


class PaymentAuthorityRefusedError(RuntimeError):
    """The authority refused BEFORE signing. Nothing was sent to the chain."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = str(code or "wallet_payment_refused")
        super().__init__(f"{self.code}: {detail}" if detail else self.code)


@runtime_checkable
class X402PaymentAuthority(Protocol):
    label: str
    #: ``namespace:reference`` networks this authority independently verified and will sign on. A quote on any
    #: other network is refused before a reservation exists; mainnet is never substituted for another network.
    networks: tuple[str, ...]

    def obtain_proof(
        self,
        *,
        quote: X402Quote,
        option: X402PaymentOption,
        envelope: RequestEnvelope,
        liability: ProviderLiability,
        reservation: MonetaryReservation,
    ) -> X402PaymentProof: ...


@dataclass(frozen=True)
class X402WalletFacts:
    """The identity and fee facts the WALLET AUTHORITY supplies for one x402 operation, BEFORE
    the monetary reservation and before any signing. The crypto owner implements
    ``wallet_facts(network=, asset=, atomic_unit=)`` beside ``obtain_proof``; without it an x402
    operation is refused before a liability exists (never guessed). Balances are the authority's
    own liquidity evidence, recorded under its label -- a wallet observation, not a provider one.

    pay_to is NOT here: it comes from the quote the provider signed, and the liability binds both.
    """

    payer_account: str
    fee_network: str
    fee_asset: str
    fee_decimals: int
    fee_max_atomic: int
    principal_balance_atomic: int | None = None
    fee_balance_atomic: int | None = None


@dataclass(frozen=True)
class X402ChainConfirmation:
    """What the WALLET AUTHORITY read back from the chain for one paid x402 operation, after the paid retry: the
    transaction signature that carried the payment, the principal that reached the quote's pay-to, and the network fee
    the transaction actually charged (``None`` while the wallet has not recorded it). Optional on an authority
    (``chain_confirmation(proof=)``); the money law records it as chain evidence only after the provider's settlement."""

    signature: str
    wallet_outflow_atomic: int
    network_fee_atomic: int | None
    fee_asset: str


class UnavailablePaymentAuthority:
    label = "unavailable:wallet_payment_authority_not_integrated"
    networks: tuple[str, ...] = ()

    def obtain_proof(self, **_kwargs: Any) -> X402PaymentProof:
        raise PaymentAuthorityUnavailableError("no wallet payment authority is integrated")


_AUTHORITY_LOCK = threading.Lock()
_PAYMENT_AUTHORITY: Any = UnavailablePaymentAuthority()


def payment_authority() -> X402PaymentAuthority:
    with _AUTHORITY_LOCK:
        return _PAYMENT_AUTHORITY


def install_payment_authority(authority: X402PaymentAuthority, *, label: str) -> X402PaymentAuthority:
    clean = str(label or "").strip()
    if not clean or str(getattr(authority, "label", "") or "") != clean:
        raise ValueError("a payment authority must be installed under its own non-empty label")
    if not isinstance(authority, X402PaymentAuthority):
        raise TypeError("object does not implement the x402 payment authority contract")
    global _PAYMENT_AUTHORITY
    with _AUTHORITY_LOCK:
        previous = _PAYMENT_AUTHORITY
        _PAYMENT_AUTHORITY = authority
    return previous


def reset_payment_authority() -> None:
    global _PAYMENT_AUTHORITY
    with _AUTHORITY_LOCK:
        _PAYMENT_AUTHORITY = UnavailablePaymentAuthority()


# --- the x402 operation journal ----------------------------------------------------------------------------

X402_CREATED = "created"
X402_QUOTE_REQUESTED = "quote_requested"
X402_QUOTED = "quoted"
X402_QUOTE_REFUSED = "quote_refused"
X402_PROOF_BOUND = "proof_bound"
X402_PAID_RETRY_SENT = "paid_retry_sent"
X402_COMPLETED = "completed"
X402_PAID_RETRY_FAILED = "paid_retry_failed"
X402_OUTCOME_UNKNOWN = "outcome_unknown"

_X402_TRANSITIONS: dict[str, frozenset[str]] = {
    X402_CREATED: frozenset({X402_QUOTE_REQUESTED}),
    X402_QUOTE_REQUESTED: frozenset({X402_QUOTED, X402_QUOTE_REFUSED}),
    # From QUOTED an authority failure of unknown outcome may already have moved money.
    X402_QUOTED: frozenset({X402_PROOF_BOUND, X402_QUOTE_REFUSED, X402_OUTCOME_UNKNOWN}),
    X402_PROOF_BOUND: frozenset({X402_PAID_RETRY_SENT, X402_OUTCOME_UNKNOWN}),
    X402_PAID_RETRY_SENT: frozenset({X402_COMPLETED, X402_PAID_RETRY_FAILED, X402_OUTCOME_UNKNOWN}),
    # Only ever back to a paid retry with the same proof -- never to a new quote.
    X402_OUTCOME_UNKNOWN: frozenset({X402_PAID_RETRY_SENT}),
    X402_PAID_RETRY_FAILED: frozenset({X402_PAID_RETRY_SENT}),
}
_UNRESOLVED_PAID_STATES = (X402_PROOF_BOUND, X402_PAID_RETRY_SENT, X402_OUTCOME_UNKNOWN, X402_PAID_RETRY_FAILED)
_JOURNAL_FIELDS = frozenset({"quote_id", "quote_header_sha256", "quote_json", "proof_json", "proof_signature", "last_status", "last_code"})

_JOURNAL_DDL = """
CREATE TABLE IF NOT EXISTS usepod_x402_operations (
    operation_id TEXT PRIMARY KEY,
    binding_sha256 TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    origin TEXT NOT NULL,
    path TEXT NOT NULL,
    protocol TEXT NOT NULL,
    model_id TEXT NOT NULL,
    max_output_tokens INTEGER NOT NULL,
    state TEXT NOT NULL,
    quote_id TEXT NOT NULL DEFAULT '',
    quote_header_sha256 TEXT NOT NULL DEFAULT '',
    quote_json TEXT NOT NULL DEFAULT '',
    proof_json TEXT NOT NULL DEFAULT '',
    proof_signature TEXT NOT NULL DEFAULT '',
    paid_attempts INTEGER NOT NULL DEFAULT 0,
    last_status INTEGER NOT NULL DEFAULT 0,
    last_code TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usepod_x402_binding_state ON usepod_x402_operations(binding_sha256, state);
CREATE TABLE IF NOT EXISTS usepod_x402_resume_records (
    operation_id TEXT PRIMARY KEY,
    envelope_json TEXT NOT NULL,
    liability_json TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""


def _envelope_record(envelope: RequestEnvelope) -> dict[str, Any]:
    return {
        "operation_id": envelope.operation_id, "transport_mode": envelope.transport_mode, "protocol": envelope.protocol,
        "method": envelope.method, "origin": envelope.origin, "path_template": envelope.path_template,
        "body_b64": base64.b64encode(envelope.body).decode("ascii"), "body_sha256": envelope.body_sha256,
        "binding_sha256": envelope.binding_sha256, "control_headers": [[name, value] for name, value in envelope.control_headers],
        "model_id": envelope.model_id, "max_output_tokens": envelope.max_output_tokens, "stream": envelope.stream,
        "route_approval_id": envelope.route_approval_id, "created_at": envelope.created_at,
    }


def _envelope_from_record(data: Mapping[str, Any]) -> RequestEnvelope:
    """The recorded request, rebuilt; the envelope re-checks its own body hash and binding."""
    return RequestEnvelope(
        operation_id=str(data["operation_id"]), transport_mode=str(data["transport_mode"]), protocol=str(data["protocol"]),
        method=str(data["method"]), origin=str(data["origin"]), path_template=str(data["path_template"]),
        body=base64.b64decode(str(data["body_b64"]), validate=True), body_sha256=str(data["body_sha256"]),
        binding_sha256=str(data["binding_sha256"]), control_headers=tuple((str(name), str(value)) for name, value in data["control_headers"]),
        model_id=str(data["model_id"]), max_output_tokens=int(data["max_output_tokens"]), stream=bool(data["stream"]),
        route_approval_id=str(data["route_approval_id"]), created_at=float(data["created_at"]),
    )


def _quote_from_evidence(data: Mapping[str, Any]) -> X402Quote:
    """The recorded quote, rebuilt field for field from what the journal kept when it was quoted."""
    return X402Quote(
        operation_id=str(data["operation_id"]), envelope_binding_sha256=str(data["envelope_binding_sha256"]), origin=str(data["origin"]),
        path=str(data["path"]), x402_version=int(data["x402_version"]), quote_id=str(data["quote_id"]),
        options=tuple(
            X402PaymentOption(
                index=int(option["index"]), asset=str(option["asset"]), scheme=str(option["scheme"]), network=str(option["network"]),
                pay_to=str(option["pay_to"]), amount_atomic=None if option["amount_atomic"] is None else int(option["amount_atomic"]),
                atomic_unit=str(option["atomic_unit"]), mode=str(option["mode"]), problems=tuple(option["problems"]),
                extra_fields=tuple(option["extra_fields"]),
                expires_at_epoch=None if option.get("expires_at_epoch") is None else float(option["expires_at_epoch"]),
            )
            for option in data["options"]
        ),
        header_sha256=str(data["header_sha256"]), received_at=float(data["received_at"]), extra_fields=tuple(data["extra_fields"]),
    )


class X402OperationJournal:
    """Durable transport state for x402 operations: hashes and public identifiers, plus -- only while a paid operation's
    outcome can still be unresolved -- its sealed request bytes and liability, so a restart or a lost answer resends the
    same bytes with the same proof. That record is deleted when the operation completes or is refused."""

    def __init__(self, *, connection_factory: Callable[[], Any] | None = None) -> None:
        if connection_factory is None:
            from storage.db import get_connection

            connection_factory = get_connection
        self._connect = connection_factory
        self._schema_ready = False

    def _conn(self) -> Any:
        conn = self._connect()
        if not self._schema_ready:
            conn.executescript(_JOURNAL_DDL)
            conn.commit()
            self._schema_ready = True
        return conn

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        return dict(row) if row is not None else {}

    def open(self, envelope: RequestEnvelope) -> dict[str, Any]:
        if envelope.transport_mode != TransportMode.X402.value:
            raise X402OperationStateError("not_an_x402_envelope")
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM usepod_x402_operations WHERE operation_id = ?", (envelope.operation_id,)
            ).fetchone()
            if existing is not None:
                conn.rollback()
                if existing["binding_sha256"] != envelope.binding_sha256:
                    raise X402OperationStateError("operation_id_reused_for_different_request")
                return self._row(existing)
            placeholders = ",".join("?" for _ in _UNRESOLVED_PAID_STATES)
            unresolved = conn.execute(
                f"SELECT operation_id, state FROM usepod_x402_operations WHERE binding_sha256 = ? AND state IN ({placeholders})",
                (envelope.binding_sha256, *_UNRESOLVED_PAID_STATES),
            ).fetchall()
            if unresolved:
                conn.rollback()
                # Identical bytes already carry a payment whose outcome is not settled. A fresh quote
                # here is exactly how a timeout becomes a second payment.
                raise X402OperationStateError(
                    "identical_request_has_unresolved_paid_operation",
                    ",".join(f"{row['operation_id']}={row['state']}" for row in unresolved[:4]),
                )
            now = _utcnow_iso()
            conn.execute(
                """INSERT INTO usepod_x402_operations
                (operation_id, binding_sha256, body_sha256, origin, path, protocol, model_id, max_output_tokens,
                 state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    envelope.operation_id, envelope.binding_sha256, envelope.body_sha256, envelope.origin,
                    envelope.path_template, envelope.protocol, envelope.model_id, envelope.max_output_tokens,
                    X402_CREATED, now, now,
                ),
            )
            conn.commit()
            return self.get(envelope.operation_id)
        finally:
            conn.close()

    def get(self, operation_id: str) -> dict[str, Any]:
        conn = self._conn()
        try:
            return self._row(
                conn.execute("SELECT * FROM usepod_x402_operations WHERE operation_id = ?", (str(operation_id),)).fetchone()
            )
        finally:
            conn.close()

    def transition(self, operation_id: str, to_state: str, **fields: Any) -> dict[str, Any]:
        unknown = set(fields) - _JOURNAL_FIELDS
        if unknown:
            raise X402OperationStateError("journal_field_not_allowed", ",".join(sorted(unknown)))
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM usepod_x402_operations WHERE operation_id = ?", (str(operation_id),)).fetchone()
            if row is None:
                conn.rollback()
                raise X402OperationStateError("operation_unknown")
            current = str(row["state"])
            if to_state not in _X402_TRANSITIONS.get(current, frozenset()):
                conn.rollback()
                raise X402OperationStateError("transition_not_allowed", f"{current}->{to_state}")
            assignments = ["state = ?", "updated_at = ?"]
            values: list[Any] = [to_state, _utcnow_iso()]
            for key in sorted(fields):
                assignments.append(f"{key} = ?")
                values.append(fields[key])
            if to_state == X402_PAID_RETRY_SENT:
                assignments.append("paid_attempts = paid_attempts + 1")
            cursor = conn.execute(
                f"UPDATE usepod_x402_operations SET {', '.join(assignments)} WHERE operation_id = ? AND state = ?",
                (*values, str(operation_id), current),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                raise X402OperationStateError("concurrent_transition")
            conn.commit()
        finally:
            conn.close()
        return self.get(operation_id)


    def keep_resume_record(self, envelope: RequestEnvelope, liability: ProviderLiability, context: Mapping[str, Any] | None = None) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO usepod_x402_resume_records (operation_id, envelope_json, liability_json, context_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    envelope.operation_id,
                    json.dumps(_envelope_record(envelope), sort_keys=True, separators=(",", ":")),
                    json.dumps(liability.as_dict(), sort_keys=True, separators=(",", ":"), default=str),
                    json.dumps(dict(context or {}), sort_keys=True, separators=(",", ":"), default=str),
                    _utcnow_iso(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def resume_record(self, operation_id: str) -> dict[str, Any] | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM usepod_x402_resume_records WHERE operation_id = ?", (str(operation_id),)).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def drop_resume_record(self, operation_id: str) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM usepod_x402_resume_records WHERE operation_id = ?", (str(operation_id),))
            conn.commit()
        finally:
            conn.close()

    def unresolved_operations(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Paid operations whose outcome is unresolved, newest first. Public facts only: never the request bytes."""
        placeholders = ",".join("?" for _ in _UNRESOLVED_PAID_STATES)
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT o.operation_id, o.state, o.model_id, o.protocol, o.path, o.quote_id, o.proof_signature, o.paid_attempts, "
                "o.last_status, o.last_code, o.updated_at, CASE WHEN r.operation_id IS NULL THEN 0 ELSE 1 END AS resumable "
                "FROM usepod_x402_operations o LEFT JOIN usepod_x402_resume_records r ON r.operation_id = o.operation_id "
                f"WHERE o.state IN ({placeholders}) ORDER BY o.updated_at DESC LIMIT ?",
                (*_UNRESOLVED_PAID_STATES, max(1, min(int(limit), 500))),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()


# --- the x402 client -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class X402PaidExchange:
    response: UsePodResponse
    quote: X402Quote
    option: X402PaymentOption
    proof: X402PaymentProof
    reservation: MonetaryReservation
    attempts: int


def _evidence(operation_id: str, outcome: str, *, code: str, http_status: int | None = None, extra: Mapping[str, Any] | None = None) -> SettlementEvidence:
    return SettlementEvidence(
        operation_id=operation_id,
        outcome=outcome,
        http_status=http_status,
        usage={},
        input_tokens=None,
        output_tokens=None,
        upper_bound_cost_atomic=None,
        exact_cost_atomic=None,
        exact_cost_state=EXACT_COST_NOT_SUPPLIED,
        route=dict(extra or {}),
        balance_remaining_raw=None,
        usage_exceeds_liability_bound=False,
        detail=code,
    )


class UsePodX402Client:
    """Quote -> reserve -> proof -> paid retry, with every step journaled and every refusal typed."""

    def __init__(
        self,
        *,
        transport: UsePodHttpTransport,
        journal: X402OperationJournal | None = None,
        payment: X402PaymentAuthority | None = None,
        monetary: MonetaryAuthority | None = None,
        clock: Callable[[], float] = time.time,
        max_paid_attempts: int = 2,
    ) -> None:
        self._transport = transport
        self._journal = journal or X402OperationJournal()
        self._payment = payment
        self._monetary = monetary
        self._clock = clock
        self._max_paid_attempts = max(1, int(max_paid_attempts))

    @property
    def journal(self) -> X402OperationJournal:
        return self._journal

    def _payment_authority(self) -> X402PaymentAuthority:
        return self._payment or payment_authority()

    def _monetary_authority(self) -> MonetaryAuthority:
        return self._monetary or monetary_authority()

    def request_quote(self, envelope: RequestEnvelope, *, origin: str, read_timeout_seconds: float) -> X402Quote:
        self._journal.open(envelope)
        self._journal.transition(envelope.operation_id, X402_QUOTE_REQUESTED)
        try:
            response = self._transport.post_envelope(envelope, origin=origin, token=None, read_timeout_seconds=read_timeout_seconds)
        except UsePodTransportError as exc:
            # An unpaid quote request carries no payment: whatever happened to it, nothing is owed.
            self._journal.transition(envelope.operation_id, X402_QUOTE_REFUSED, last_code=exc.code)
            raise
        if response.status == 402:
            header = response.header("payment-required")
            response.close()
            try:
                quote = parse_payment_required(header, envelope=envelope, origin=origin, received_at=float(self._clock()))
            except X402QuoteError as exc:
                self._journal.transition(envelope.operation_id, X402_QUOTE_REFUSED, last_status=402, last_code=exc.code)
                raise
            self._journal.transition(
                envelope.operation_id,
                X402_QUOTED,
                quote_id=quote.quote_id,
                quote_header_sha256=quote.header_sha256,
                quote_json=json.dumps(quote.as_evidence(), sort_keys=True, separators=(",", ":")),
                last_status=402,
            )
            return quote
        if 200 <= response.status < 300:
            response.close()
            self._journal.transition(envelope.operation_id, X402_QUOTE_REFUSED, last_status=response.status, last_code="unpaid_request_was_served")
            # Served without payment: not a result a paid-route receipt can describe, and not one to publish.
            raise UsePodTransportError("x402_unpaid_request_was_served", dispatch_state=DISPATCH_RESPONSE_RECEIVED, http_status=response.status)
        error = error_for_response(response, origin=origin)
        self._journal.transition(envelope.operation_id, X402_QUOTE_REFUSED, last_status=response.status, last_code=error.code)
        raise error

    def execute(
        self,
        envelope: RequestEnvelope,
        *,
        origin: str,
        allowed_assets: tuple[str, ...],
        local_bounds_atomic: Mapping[str, int],
        liability_basis: Mapping[str, Any],
        read_timeout_seconds: float,
        resume_context: Mapping[str, Any] | None = None,
    ) -> X402PaidExchange:
        quote = self.request_quote(envelope, origin=origin, read_timeout_seconds=read_timeout_seconds)
        authority = self._payment_authority()
        if isinstance(authority, UnavailablePaymentAuthority):
            # Nothing can pay, so nothing is reserved: refused before any liability exists.
            code = PaymentAuthorityUnavailableError.code
            self._journal.transition(envelope.operation_id, X402_QUOTE_REFUSED, last_code=code)
            raise UsePodTransportError(code, dispatch_state=DISPATCH_NOT_SENT, evidence={"payment_authority": authority.label})
        networks = tuple(str(item) for item in (getattr(authority, "networks", ()) or ()))
        asset_matches = [candidate for asset in allowed_assets for candidate in quote.usable_options() if candidate.asset == asset]
        option = next((candidate for candidate in asset_matches if candidate.network in networks), None)
        if option is None:
            code = "quote_network_not_verified_by_payment_authority" if asset_matches else "no_permitted_payment_option"
            self._journal.transition(envelope.operation_id, X402_QUOTE_REFUSED, last_code=code)
            raise UsePodTransportError(
                code,
                dispatch_state=DISPATCH_NOT_SENT,
                evidence={"quote": quote.as_evidence(), "payment_authority_networks": list(networks)},
            )
        bound = local_bounds_atomic.get(option.asset)
        if bound is None:
            self._journal.transition(envelope.operation_id, X402_QUOTE_REFUSED, last_code="no_local_bound_for_asset")
            raise UsePodTransportError("no_local_liability_bound_for_asset", dispatch_state=DISPATCH_NOT_SENT, detail=option.asset)
        if int(option.amount_atomic or 0) > int(bound):
            self._journal.transition(envelope.operation_id, X402_QUOTE_REFUSED, last_code="quote_exceeds_local_liability_bound")
            raise UsePodTransportError(
                "quote_exceeds_local_liability_bound",
                dispatch_state=DISPATCH_NOT_SENT,
                evidence={"quote_amount_atomic": option.amount_atomic, "local_bound_atomic": int(bound), "asset": option.asset},
            )
        if option.expires_at_epoch is not None:
            # A payment made after the quote's own expiry buys nothing the provider has promised to serve: an expired
            # quote, or one the wallet cannot possibly pay in time, is refused here -- before any reservation or proof.
            now = float(self._clock())
            remaining = float(option.expires_at_epoch) - now
            if remaining <= 0:
                code = "quote_expired"
            elif remaining < X402_MIN_QUOTE_VALIDITY_SECONDS:
                code = "quote_expires_too_soon"
            else:
                code = ""
            if code:
                self._journal.transition(envelope.operation_id, X402_QUOTE_REFUSED, last_code=code)
                raise UsePodTransportError(
                    code,
                    dispatch_state=DISPATCH_NOT_SENT,
                    evidence={
                        "quote_expires_at_epoch": float(option.expires_at_epoch),
                        "now_epoch": now,
                        "minimum_validity_seconds": X402_MIN_QUOTE_VALIDITY_SECONDS,
                    },
                )
        # The wallet authority supplies the payer identity, fee asset/maximum and its own
        # liquidity evidence BEFORE any liability is reserved or proof obtained. An authority
        # that does not publish them cannot activate x402 -- the facts are refused, never guessed.
        facts_provider = getattr(authority, "wallet_facts", None)
        if facts_provider is None:
            code = "wallet_payer_identity_unavailable"
            self._journal.transition(envelope.operation_id, X402_QUOTE_REFUSED, last_code=code)
            raise UsePodTransportError(
                code,
                dispatch_state=DISPATCH_NOT_SENT,
                evidence={"payment_authority": authority.label},
            )
        try:
            wallet_facts = facts_provider(network=option.network, asset=option.asset, atomic_unit=option.atomic_unit)
        except Exception as exc:
            code = "wallet_payer_identity_unavailable"
            self._journal.transition(envelope.operation_id, X402_QUOTE_REFUSED, last_code=code)
            raise UsePodTransportError(
                code,
                dispatch_state=DISPATCH_NOT_SENT,
                evidence={"payment_authority": authority.label, "detail": redact_secrets(str(exc))[:200]},
            ) from None
        liability = ProviderLiability(
            operation_id=envelope.operation_id,
            provider_id=PROVIDER_ID,
            transport_mode=TransportMode.X402.value,
            asset=option.asset,
            unit=option.atomic_unit,
            account_kind=ACCOUNT_KIND_X402_PAYER,
            account_ref=str(wallet_facts.payer_account),
            network=option.network,
            max_amount_atomic=int(option.amount_atomic or 0),
            model_id=envelope.model_id,
            route_approval_id=envelope.route_approval_id,
            envelope_binding_sha256=envelope.binding_sha256,
            basis={
                **dict(liability_basis),
                "quote_id": quote.quote_id,
                "quoted_cap_atomic": option.amount_atomic,
                "quote_expires_at_epoch": option.expires_at_epoch,
                "local_bound_atomic": int(bound),
                "pay_to": str(option.pay_to),
                "fee_network": str(wallet_facts.fee_network),
                "fee_asset": str(wallet_facts.fee_asset),
                "fee_decimals": int(wallet_facts.fee_decimals),
                "fee_max_atomic": int(wallet_facts.fee_max_atomic),
                "principal_balance_atomic": wallet_facts.principal_balance_atomic,
                "fee_balance_atomic": wallet_facts.fee_balance_atomic,
                "wallet_authority_label": str(authority.label),
            },
        )
        monetary = self._monetary_authority()
        refusal = ""
        try:
            reservation = monetary.reserve(liability)
        except (MonetaryAuthorityUnavailableError, MonetaryAuthorityRefusedError) as exc:
            refusal = str(getattr(exc, "code", "") or "monetary_authority_refused")
        if refusal:
            self._journal.transition(envelope.operation_id, X402_QUOTE_REFUSED, last_code=refusal)
            raise UsePodTransportError(refusal, dispatch_state=DISPATCH_NOT_SENT, evidence={"monetary_authority": getattr(monetary, "label", "")})

        # The pre-effect CLAIM is persisted BEFORE the payment proof is obtained: obtain_proof may
        # sign and spend, and a proof that exists without a prior claim is exactly the unordered
        # payment this law exists to prevent. A claim refusal proves nothing was sent (the money
        # law releases the never-sent reservation in the same transaction).
        claim = getattr(monetary, "claim", None)
        if claim is not None:
            try:
                claim(reservation, executor="usepod_x402_wallet")
            except (MonetaryAuthorityUnavailableError, MonetaryAuthorityRefusedError) as exc:
                self._journal.transition(envelope.operation_id, X402_QUOTE_REFUSED, last_code=str(getattr(exc, "code", "") or "claim_refused"))
                raise UsePodTransportError(
                    str(getattr(exc, "code", "") or "monetary_claim_refused"),
                    dispatch_state=DISPATCH_NOT_SENT,
                    evidence={"monetary_authority": getattr(monetary, "label", "")},
                ) from None

        # From here the wallet may pay. The sealed bytes and the liability are made durable first, so a restart or a lost
        # answer can finish THIS operation with the same proof -- never a new quote, never a second payment.
        self._journal.keep_resume_record(envelope, liability, resume_context)
        proof: X402PaymentProof | None = None
        refused_code = ""
        unknown_failure = ""
        try:
            proof = authority.obtain_proof(quote=quote, option=option, envelope=envelope, liability=liability, reservation=reservation)
        except (PaymentAuthorityUnavailableError, PaymentAuthorityRefusedError) as exc:
            refused_code = str(getattr(exc, "code", "") or "wallet_payment_refused")
        except Exception as exc:
            unknown_failure = type(exc).__name__
        if refused_code:
            monetary.release_unsent(reservation, reason=refused_code)
            self._journal.transition(envelope.operation_id, X402_QUOTE_REFUSED, last_code=refused_code)
            self._journal.drop_resume_record(envelope.operation_id)
            raise UsePodTransportError(refused_code, dispatch_state=DISPATCH_NOT_SENT, evidence={"payment_authority": getattr(authority, "label", "")})
        if unknown_failure or proof is None:
            code = "payment_authority_outcome_unknown"
            self._journal.transition(envelope.operation_id, X402_OUTCOME_UNKNOWN, last_code=code)
            monetary.retain_unknown(reservation, _evidence(envelope.operation_id, OUTCOME_UNKNOWN, code=code))
            raise UsePodTransportError(code, dispatch_state=DISPATCH_OUTCOME_UNKNOWN, detail=unknown_failure)
        try:
            header = payment_signature_header(proof, quote=quote, envelope=envelope)
        except X402ProofError as exc:
            # A proof exists but binds to something else: money may have moved for a different request.
            self._journal.transition(envelope.operation_id, X402_OUTCOME_UNKNOWN, last_code=exc.code)
            monetary.retain_unknown(reservation, _evidence(envelope.operation_id, OUTCOME_UNKNOWN, code=exc.code))
            raise UsePodTransportError(exc.code, dispatch_state=DISPATCH_OUTCOME_UNKNOWN) from None
        register_public_identifier(proof.signature)
        self._journal.transition(
            envelope.operation_id,
            X402_PROOF_BOUND,
            proof_json=json.dumps(proof.public_fields(), sort_keys=True, separators=(",", ":")),
            proof_signature=proof.signature,
        )
        monetary.mark_dispatched(reservation)
        return self._send_paid(envelope, origin=origin, header=header, quote=quote, option=option, proof=proof, reservation=reservation, read_timeout_seconds=read_timeout_seconds)

    def resend_paid_retry(
        self,
        envelope: RequestEnvelope,
        *,
        origin: str,
        quote: X402Quote,
        option: X402PaymentOption,
        proof: X402PaymentProof,
        reservation: MonetaryReservation,
        read_timeout_seconds: float,
    ) -> X402PaidExchange:
        """Explicitly re-send an already-paid operation: same bytes, same proof, bounded attempts."""
        row = self._journal.get(envelope.operation_id)
        if not row:
            raise X402OperationStateError("operation_unknown")
        if row["state"] not in {X402_OUTCOME_UNKNOWN, X402_PAID_RETRY_FAILED}:
            raise X402OperationStateError("resend_not_allowed_in_state", str(row["state"]))
        if row["binding_sha256"] != envelope.binding_sha256:
            raise X402OperationStateError("resend_bytes_differ_from_paid_request")
        if not row["proof_signature"] or row["proof_signature"] != proof.signature:
            raise X402OperationStateError("resend_proof_differs_from_recorded_payment")
        if int(row["paid_attempts"] or 0) >= self._max_paid_attempts:
            raise X402OperationStateError("paid_retry_attempts_exhausted")
        header = payment_signature_header(proof, quote=quote, envelope=envelope)
        return self._send_paid(envelope, origin=origin, header=header, quote=quote, option=option, proof=proof, reservation=reservation, read_timeout_seconds=read_timeout_seconds)

    def resume(self, operation_id: str, *, read_timeout_seconds: float) -> X402PaidExchange:
        """Finish ONE already-paid operation whose outcome is unresolved: the recorded bytes, quote and proof, resent
        within the attempt bound. Only while the money law holds its liability as unknown -- its claimant gave up or its
        process ended; a live claim is never taken over. Never a new quote, never a second payment."""
        row = self._journal.get(operation_id)
        if not row:
            raise X402OperationStateError("operation_unknown")
        if row["state"] not in _UNRESOLVED_PAID_STATES:
            raise X402OperationStateError("operation_not_unresolved", str(row["state"]))
        record = self._journal.resume_record(operation_id)
        if record is None:
            raise X402OperationStateError("resume_record_missing")
        try:
            envelope = _envelope_from_record(json.loads(record["envelope_json"]))
            liability = ProviderLiability(**json.loads(record["liability_json"]))
            quote = _quote_from_evidence(json.loads(row["quote_json"]))
            proof = X402PaymentProof(
                operation_id=str(operation_id),
                envelope_binding_sha256=str(row["binding_sha256"]),
                quote_header_sha256=str(row["quote_header_sha256"]),
                **json.loads(row["proof_json"]),
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise X402OperationStateError("resume_record_unreadable", type(exc).__name__) from None
        if envelope.operation_id != operation_id or envelope.binding_sha256 != row["binding_sha256"] or liability.operation_id != operation_id:
            raise X402OperationStateError("resume_bytes_differ_from_paid_request")
        option = next(
            (
                candidate
                for candidate in quote.options
                if candidate.usable and candidate.asset == proof.asset and candidate.network == proof.network
                and candidate.pay_to == proof.pay_to and candidate.amount_atomic == proof.amount_atomic
            ),
            None,
        )
        if option is None:
            raise X402OperationStateError("resume_proof_matches_no_quoted_option")
        monetary = self._monetary_authority()
        locate = getattr(monetary, "reservation_for_resume", None)
        if locate is None:
            raise X402OperationStateError("resume_not_supported_by_monetary_authority", str(getattr(monetary, "label", "")))
        reservation, state = locate(liability)
        if reservation is None or state != "unknown":
            raise X402OperationStateError("resume_liability_not_unknown", state or "absent")
        if row["state"] in (X402_PROOF_BOUND, X402_PAID_RETRY_SENT):
            self._journal.transition(operation_id, X402_OUTCOME_UNKNOWN, last_code="claimant_ended_before_outcome")
        register_public_identifier(proof.signature)
        return self.resend_paid_retry(
            envelope, origin=envelope.origin, quote=quote, option=option, proof=proof, reservation=reservation, read_timeout_seconds=read_timeout_seconds
        )

    def _send_paid(
        self,
        envelope: RequestEnvelope,
        *,
        origin: str,
        header: str,
        quote: X402Quote,
        option: X402PaymentOption,
        proof: X402PaymentProof,
        reservation: MonetaryReservation,
        read_timeout_seconds: float,
    ) -> X402PaidExchange:
        monetary = self._monetary_authority()
        row = self._journal.transition(envelope.operation_id, X402_PAID_RETRY_SENT)
        attempts = int(row.get("paid_attempts") or 0)
        try:
            response = self._transport.post_envelope(
                envelope,
                origin=origin,
                token=None,
                extra_headers=((HEADER_PAYMENT_SIGNATURE, header),),
                read_timeout_seconds=read_timeout_seconds,
            )
        except UsePodTransportError as exc:
            self._journal.transition(envelope.operation_id, X402_OUTCOME_UNKNOWN, last_code=exc.code)
            monetary.retain_unknown(reservation, _evidence(envelope.operation_id, OUTCOME_UNKNOWN, code=exc.code, extra={"quote_id": quote.quote_id}))
            raise X402PaidResultUnknownError(exc, proof=proof, operation_id=envelope.operation_id, paid_attempts=attempts) from None
        if 200 <= response.status < 300:
            self._journal.transition(envelope.operation_id, X402_COMPLETED, last_status=response.status)
            self._journal.drop_resume_record(envelope.operation_id)
            return X402PaidExchange(response, quote, option, proof, reservation, attempts)
        error = error_for_response(response, origin=origin)
        self._journal.transition(envelope.operation_id, X402_PAID_RETRY_FAILED, last_status=response.status, last_code=error.code)
        monetary.retain_unknown(
            reservation,
            _evidence(envelope.operation_id, OUTCOME_FAILED_AFTER_SEND, code=error.code, http_status=response.status, extra={"quote_id": quote.quote_id}),
        )
        raise X402PaidResultUnknownError(error, proof=proof, operation_id=envelope.operation_id, paid_attempts=attempts)


__all__ = [
    "ATOMIC_UNIT_BY_ASSET",
    "USER_AGENT",
    "X402_MIN_QUOTE_VALIDITY_SECONDS",
    "parse_rfc3339_epoch",
    "DISPATCH_NOT_SENT",
    "DISPATCH_OUTCOME_UNKNOWN",
    "DISPATCH_RESPONSE_RECEIVED",
    "X402_COMPLETED",
    "X402_CREATED",
    "X402_OUTCOME_UNKNOWN",
    "X402_PAID_RETRY_FAILED",
    "X402_PAID_RETRY_SENT",
    "X402_PROOF_BOUND",
    "X402_QUOTED",
    "X402_QUOTE_REFUSED",
    "X402_QUOTE_REQUESTED",
    "BalanceObservation",
    "ModelListing",
    "PaymentAuthorityRefusedError",
    "PaymentAuthorityUnavailableError",
    "RequestEnvelope",
    "UnavailablePaymentAuthority",
    "UsePodHttpTransport",
    "UsePodResponse",
    "UsePodTransportError",
    "UsePodX402Client",
    "X402OperationJournal",
    "X402OperationStateError",
    "X402PaidExchange",
    "X402PaidResultUnknownError",
    "X402PaymentAuthority",
    "X402PaymentOption",
    "X402PaymentProof",
    "X402ProofError",
    "X402Quote",
    "X402QuoteError",
    "decode_payment_response",
    "error_for_response",
    "install_payment_authority",
    "list_token_models",
    "parse_payment_required",
    "payment_authority",
    "payment_signature_header",
    "prepaid_path_template",
    "read_token_balance",
    "reset_payment_authority",
    "seal_request_envelope",
    "X402ChainConfirmation",
]
