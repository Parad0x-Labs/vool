"""The wallet's outbound confinement: every RPC, facilitator and paid-resource request
traverses the canonical outbound-effect door (:func:`core.remote_fetch_policy.open_remote`)
with the wallet's OWN origin policy wrapped around it.

What this layer adds on top of the door:

* an approved-origin policy — RPC endpoints must be the chain row's declared origins or the
  operator's explicit override; paid-resource targets must be public https (loopback only
  behind the explicit simulator switch);
* DNS/IP rebinding screening — a hostname that resolves into private, reserved, link-local,
  loopback (unless the switch is on) or otherwise non-global space is refused before any
  socket;
* redirect confinement — the door is called with ``redirect_policy="refuse"`` and each hop
  is re-validated here; a payment header is attached (as an UNREDIRECTED header) only when
  the hop's origin IS the approved challenge origin, so a redirect can neither change the
  origin nor carry payment material;
* bounded reads — a response larger than the byte limit is refused, not swallowed;
* session/turn ownership — inside a turn the turn's ledger covers the call; outside one,
  this module opens the wallet's own named background effect scope at THIS entry point.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit

from core.wallet import chains
from core.wallet.security import wallet_fault

AUTHORITY = "core.wallet.outbound"
PROVIDER_ID = "core.wallet"

#: One paid resource or RPC response. A larger answer is a refusal, not a memory incident.
MAX_RESPONSE_BYTES = 1_048_576
#: Redirect hops per fetch, each re-validated.
MAX_HOPS = 3
LOOPBACK_ALLOWED_ENV = chains._loopback_allowed  # the same simulator switch the registry uses


def _loopback_switch() -> bool:
    import os

    return str(os.environ.get("VOOL_WALLET_X402_ALLOW_LOOPBACK") or "").strip().lower() in {"1", "true", "yes", "on"}


def resolve_host_addresses(host: str) -> list[str]:
    """All addresses the name resolves to. A name that resolves to nothing refuses."""
    try:
        infos = socket.getaddrinfo(str(host or "").strip().rstrip("."), None)
    except OSError as exc:
        raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"reason": f"dns_failure:{type(exc).__name__}", "host": str(host)[:80]}) from None
    return sorted({info[4][0] for info in infos})


def _address_allowed(address_text: str) -> bool:
    try:
        address = ipaddress.ip_address(address_text)
    except ValueError:
        return False
    if _loopback_switch() and address.is_loopback:
        return True
    return not (address.is_private or address.is_link_local or address.is_reserved or address.is_multicast or address.is_unspecified or address.is_loopback)


def validate_target(url: str, *, spec: chains.ChainIdentity | None = None, public_only: bool = True) -> str:
    """The origin policy every wallet request passes BEFORE any socket. Returns the origin
    (scheme://host[:port]) the request is pinned to."""
    parts = urlsplit(str(url or "").strip())
    host = (parts.hostname or "").strip().lower()
    if parts.scheme not in {"http", "https"} or not host:
        raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"reason": "scheme_or_host_invalid", "host": host[:80]})
    if host.endswith((".local", ".internal", ".lan", ".home.arpa")):
        raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"reason": "private_namespace", "host": host[:80]})
    loopback = host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".localhost")
    for address_text in resolve_host_addresses(host):
        if not _address_allowed(address_text):
            raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"reason": "non_global_address", "host": host[:80], "address": "private-or-reserved"})
    if loopback:
        if not _loopback_switch():
            raise wallet_fault("wallet_network_disabled", authority=AUTHORITY, context={"reason": "loopback_not_enabled", "host": host[:80]})
    elif public_only and parts.scheme != "https":
        raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"reason": "insecure_transport", "host": host[:80]})
    if spec is not None:
        chains.rpc_origin_allowed_or_refuse(spec, str(url))
    port = parts.port
    origin = f"{parts.scheme}://{host}" + (f":{port}" if port else "")
    return origin


def _origin_of(url: str) -> str:
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    if not host:
        return ""
    port = parts.port
    return f"{parts.scheme}://{host}" + (f":{port}" if port else "")


def _open_through_door(request, *, timeout: float, retry_of: str):
    """The ONE door, with the wallet's named scope opened when no turn ledger is active."""
    from core.effect_gateway import current_effect_ledger
    from core.remote_fetch_policy import open_remote

    if current_effect_ledger() is None:
        from core.effect_gateway import named_background_effect_scope

        with named_background_effect_scope("wallet.outbound"):
            return open_remote(request, timeout=timeout, provider_id=PROVIDER_ID, keyed_or_keyless="keyless", retry_of=retry_of, redirect_policy="refuse")
    return open_remote(request, timeout=timeout, provider_id=PROVIDER_ID, keyed_or_keyless="keyless", retry_of=retry_of, redirect_policy="refuse")


def fetch(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    spec: chains.ChainIdentity | None = None,
    byte_limit: int = MAX_RESPONSE_BYTES,
    timeout: float = 20.0,
    payment_headers: dict[str, str] | None = None,
    payment_origin: str = "",
    payment_redirect: str = "drop",
    source_context: dict[str, Any] | None = None,
    retry_of: str = "",
) -> dict[str, Any]:
    """One confined outbound fetch. ``payment_headers`` ride ONLY to the hop whose origin
    equals ``payment_origin``, and only as unredirected headers — a redirect to any other
    origin cannot receive or forward them. ``payment_redirect`` governs a redirect AT that
    payment-carrying hop: ``"drop"`` follows the re-validated hop without the headers (the
    outbound-layer default), ``"refuse"`` is a typed refusal — payment material goes to the
    challenged origin and nowhere else. Returns {status, headers, body, url}."""
    import urllib.error
    import urllib.request

    clean_url = str(url or "").strip()
    current = validate_target(clean_url, spec=spec, public_only=True)
    remaining_payment = dict(payment_headers or {})
    for _hop in range(MAX_HOPS + 1):
        request = urllib.request.Request(clean_url, data=body, headers=dict(headers or {}), method=str(method or "GET").upper())
        current_origin = _origin_of(clean_url)
        if remaining_payment and payment_origin and current_origin == payment_origin:
            for name, value in remaining_payment.items():
                # UNREDIRECTED: urllib's redirect handler never copies these to a follow-up
                # request, so payment material cannot survive a hop we did not make.
                request.add_unredirected_header(name, value)
        try:
            response = _open_through_door(request, timeout=timeout, retry_of=retry_of)
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location", "") if exc.headers else ""
            if 300 <= exc.code < 400 and location:
                if remaining_payment and payment_origin and current_origin == payment_origin and payment_redirect == "refuse":
                    # the payment-carrying hop redirected: the proof is pinned to the
                    # challenged origin, so the hop itself is the refusal.
                    raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"reason": "payment_hop_redirected", "approved_origin": payment_origin[:80], "location": location[:120]}) from None
                if _hop >= MAX_HOPS:
                    raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"reason": "too_many_redirects", "host": _origin_of(location)[:80]}) from None
                from urllib.parse import urljoin

                clean_url = urljoin(clean_url, location)
                current = validate_target(clean_url, spec=spec, public_only=True)
                continue
            if 300 <= exc.code < 400:
                raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"reason": f"redirect_refused_{exc.code}", "host": current[:80]}) from None
            # A 4xx/5xx is a RESPONSE, not a transport failure: an x402 challenge (402) is
            # exactly the data this lane exists to parse. Bounded read, same as success.
            payload = exc.read(byte_limit + 1)
            if len(payload) > byte_limit:
                raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"reason": "response_over_byte_limit", "limit": str(byte_limit), "host": current[:80]}) from None
            return {"status": int(exc.code), "headers": {k.lower(): v for k, v in (exc.headers or {}).items()}, "body": payload, "url": clean_url}
        payload = response.read(byte_limit + 1)
        if len(payload) > byte_limit:
            raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"reason": "response_over_byte_limit", "limit": str(byte_limit), "host": current[:80]})
        response_headers = {k.lower(): v for k, v in response.headers.items()}
        return {"status": int(response.status), "headers": response_headers, "body": payload, "url": clean_url}
    raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"reason": "redirect_exhausted"})


def rpc_call(url: str, *, payload: dict, spec: chains.ChainIdentity, byte_limit: int = MAX_RESPONSE_BYTES, timeout: float = 20.0, retry_of: str = "") -> Any:
    """One chain RPC call: origin policy from the chain row, JSON in/out, bounded read."""
    import json

    body = json.dumps(payload).encode("utf-8")
    result = fetch(
        url, method="POST", headers={"Content-Type": "application/json"}, body=body,
        spec=spec, byte_limit=byte_limit, timeout=timeout, retry_of=retry_of,
    )
    if result["status"] != 200:
        raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"reason": f"rpc_http_{result['status']}", "host": _origin_of(url)[:80]})
    try:
        import json as _json

        return _json.loads(result["body"].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise wallet_fault("wallet_outbound_refused", authority=AUTHORITY, context={"reason": "rpc_payload_undecodable", "host": _origin_of(url)[:80]}) from None


def target_allowed(url: str) -> bool:
    """The boolean form of the target policy (for read-only checks that must not raise)."""
    try:
        validate_target(url, spec=None, public_only=True)
    except Exception:
        return False
    return True



__all__ = ["AUTHORITY", "MAX_HOPS", "MAX_RESPONSE_BYTES", "PROVIDER_ID", "fetch", "resolve_host_addresses", "rpc_call", "target_allowed", "validate_target"]
