"""The one egress VOOL hands to a KAS adapter.

Everything an adapter is not allowed to decide is decided here, once, in VOOL:

* **permission** — the request crosses ``core.remote_fetch_policy.open_remote``, the repo's one
  outbound HTTP door, which refuses outright when no turn or background effect ledger is open;
* **effects** — that door runs the full ``authorized -> started -> succeeded|failed|cancelled``
  lifecycle into the turn's ledger, so an adapter call is a receipt, not a rumour;
* **privacy** — the adapter never holds a secret. It names a credential BINDING; this module
  resolves the binding and attaches the credential itself, and refuses an adapter that tries to
  set its own ``Authorization`` header. Nothing derived from the secret is returned or logged;
* **truth about outcome** — a mutating request whose reply never arrived raises
  :class:`~core.kas.contract.TransportUnknownError`, never a failure. UNKNOWN is not FAILED. Only a
  failure that proves nothing was transmitted is a refusal, and a success status that arrived is
  acceptance even when the body behind it is lost (:class:`~core.kas.contract.TransportAcceptedError`).

An adapter is constructed with the transport this module builds. It has no other way out, which
is what makes :mod:`core.kas.conformance`'s import scan a real law rather than a style rule.
"""

from __future__ import annotations

import urllib.error
from typing import Any
from urllib.parse import urlsplit

from core.kas.contract import (
    KasRequest,
    KasResponse,
    TransportAcceptedError,
    TransportDeniedError,
    TransportUnknownError,
)

#: Headers an adapter may never set. Each one is an authority the adapter does not hold.
FORBIDDEN_REQUEST_HEADERS = frozenset(
    {"authorization", "proxy-authorization", "cookie", "x-api-key", "private-token", "set-cookie"}
)

#: Schemes that may leave the machine. Anything else is a local-resource read wearing a URL.
ALLOWED_SCHEMES = frozenset({"https", "http"})

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _host(url: str) -> str:
    try:
        return str(urlsplit(str(url or "")).hostname or "")
    except Exception:
        return ""


def _scheme(url: str) -> str:
    try:
        return str(urlsplit(str(url or "")).scheme or "").lower()
    except Exception:
        return ""


def _safe_reason(exc: BaseException) -> str:
    """A failure reason derived from the exception TYPE, never its text.

    Exception text on an HTTP failure routinely carries the full URL, and a URL can carry a
    token. The type is enough to act on and cannot leak.
    """

    from core.remote_fetch_policy import RemoteFetchRefusedError

    if isinstance(exc, RemoteFetchRefusedError):
        return "denied"
    if isinstance(exc, urllib.error.HTTPError):
        return "http_error"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, TimeoutError):
            return "timeout"
        return "unreachable"
    return type(exc).__name__.lower()


def _proven_unsent(exc: BaseException) -> bool:
    """True only for a failure that proves no request byte reached the provider.

    The name did not resolve, the connection was refused, or the certificate was rejected during the
    handshake. Every other failure -- a timeout, a reset, a connection closed without a reply, a
    garbled status line -- can follow a request the provider already received.
    """

    import socket
    import ssl

    cause = getattr(exc, "reason", None) if isinstance(exc, urllib.error.URLError) else exc
    return isinstance(cause, (socket.gaierror, ConnectionRefusedError, ssl.SSLCertVerificationError))


def _reply_lost(request: KasRequest, status: int, exc: BaseException) -> RuntimeError:
    """The typed outcome when a reply's status line arrived but its body did not.

    A mutation answered with a success status was accepted, whatever happened to the body. A mutation
    without a readable success status stays unknown. A read has no effect to double-apply and keeps the
    read failure mapping.
    """

    reason = _safe_reason(exc)
    if request.mutating and 200 <= status < 300:
        return TransportAcceptedError(status, detail=reason)
    if request.mutating:
        return TransportUnknownError("outcome_unproven", detail=reason)
    return TransportDeniedError("transport_failed", detail=reason)


def _resolve_auth_header(binding: str) -> tuple[str, str]:
    """Turn an opaque binding id into one header pair, inside VOOL.

    Returns ``(header_name, header_value)``. The secret exists only in this function's frame and
    in the request object handed straight to the transport; it is never returned to the adapter,
    never journaled, and never placed in a URL.
    """

    handle = str(binding or "").strip()
    if not handle:
        return "", ""
    from core.credential_intelligence.binding import load_index
    from core.credential_store import get_credential

    rows = load_index()
    row = rows.get(handle) or next(
        (r for r in rows.values() if str(r.get("binding_id") or "") == handle), None
    )
    if row is None:
        raise TransportDeniedError(
            "unknown_credential_binding",
            detail=f"no verified credential binding `{handle}` exists",
        )
    if str(row.get("status") or "") != "verified":
        raise TransportDeniedError(
            "credential_binding_not_verified",
            detail=f"binding `{handle}` is `{row.get('status')}`, not verified",
        )
    slot = str(row.get("slot") or row.get("credential_name") or "").strip()
    secret = get_credential(slot) if slot else None
    if not secret:
        raise TransportDeniedError(
            "credential_unavailable",
            detail=f"binding `{handle}` resolves to no stored credential",
        )
    scheme = str(row.get("auth_scheme") or "bearer").strip().lower()
    if scheme == "token":
        return "Authorization", f"token {secret}"
    if scheme == "private-token":
        return "PRIVATE-TOKEN", str(secret)
    return "Authorization", f"Bearer {secret}"


def build_transport(
    *,
    source_context: dict[str, Any] | None = None,
    provider_id: str = "",
    allowed_hosts: tuple[str, ...] = (),
):
    """Build the callable an adapter is constructed with.

    ``allowed_hosts`` pins the destination in VOOL rather than in the adapter: an adapter that
    computes a URL to somewhere else is refused here, so a compromised or careless adapter
    cannot redirect credentialed traffic.
    """

    pinned = tuple(h.strip().lower() for h in allowed_hosts if str(h).strip())

    def _send(request: KasRequest) -> KasResponse:
        from core.remote_fetch_policy import open_remote_url

        offending = sorted(
            name for name in {str(k).lower() for k in (request.headers or {})} if name in FORBIDDEN_REQUEST_HEADERS
        )
        if offending:
            raise TransportDeniedError(
                "adapter_set_credential_header",
                detail=(
                    "an adapter may not set "
                    + ", ".join(offending)
                    + ": name a credential binding instead, VOOL attaches the credential"
                ),
            )
        scheme = _scheme(request.url)
        if scheme not in ALLOWED_SCHEMES:
            raise TransportDeniedError("unsupported_scheme", detail=f"`{scheme or 'none'}` is not an egress scheme")
        host = _host(request.url)
        if not host:
            raise TransportDeniedError("malformed_url", detail="the request names no host")
        if pinned and host.lower() not in pinned:
            raise TransportDeniedError(
                "host_not_pinned",
                detail=f"`{host}` is not among the hosts this adapter is configured for",
            )

        headers = {str(k): str(v) for k, v in dict(request.headers or {}).items()}
        auth_name, auth_value = _resolve_auth_header(request.auth)
        if auth_name:
            headers[auth_name] = auth_value
        if request.idempotency_key:
            headers.setdefault("Idempotency-Key", str(request.idempotency_key))

        try:
            response = open_remote_url(
                request.url,
                data=request.body,
                headers=headers,
                method=str(request.method or "GET").upper(),
                timeout=float(request.timeout or 20.0),
                provider_id=str(provider_id or ""),
                keyed_or_keyless="keyed" if request.auth else "keyless",
                no_proxy=bool(request.auth),
            )
        except urllib.error.HTTPError as exc:
            # A non-2xx status IS the provider's answer, carried as an exception by urllib. It is
            # a response, not a transport failure, and the adapter must be allowed to read it.
            body = b""
            try:
                body = exc.read()[:MAX_RESPONSE_BYTES] or b""
            except Exception:
                body = b""
            return KasResponse(
                status=int(getattr(exc, "code", 0) or 0),
                body=body,
                headers={str(k): str(v) for k, v in dict(getattr(exc, "headers", {}) or {}).items()},
            )
        except Exception as exc:
            reason = _safe_reason(exc)
            if reason == "denied":
                raise TransportDeniedError("egress_denied", detail=reason) from None
            if request.mutating and not _proven_unsent(exc):
                # The request may have left the machine and no answer came back. Whether the
                # remote applied it is not knowable from here; only a failure that proves nothing
                # was transmitted may read as a refusal, or a retry could double-apply.
                raise TransportUnknownError("outcome_unproven", detail=reason) from None
            raise TransportDeniedError("transport_failed", detail=reason) from None

        # The status line arrived with the headers: take it BEFORE the body, so a body that stalls,
        # arrives incomplete or fails to close cannot erase the provider's answer to a mutation.
        status = int(getattr(response, "status", None) or getattr(response, "code", None) or 0)
        raw_headers = getattr(response, "headers", {}) or {}
        body: bytes | None = None
        try:
            with response:
                body = response.read(MAX_RESPONSE_BYTES) or b""
        except Exception as exc:
            if body is None:
                raise _reply_lost(request, status, exc) from None
            # The whole body was read; only closing the reply failed, and the answer stands.
        return KasResponse(
            status=status,
            body=body,
            headers={str(k): str(v) for k, v in dict(raw_headers).items()},
        )

    return _send


__all__ = ["ALLOWED_SCHEMES", "FORBIDDEN_REQUEST_HEADERS", "MAX_RESPONSE_BYTES", "build_transport"]
