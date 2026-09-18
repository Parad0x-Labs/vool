"""null:// remote dial — reach a named .null agent's endpoint, pay it, return its result.

A `null://` request normally runs LOCALLY. When remote dial is explicitly enabled
(policy `system.allow_null_dial` / `VOOL_ENABLE_NULL_DIAL=1`) a resolved .null
record that carries an on-chain ``x402_endpoint`` lets VOOL REACH that endpoint,
hand it the task, and return the named agent's answer instead of running it here.

Two independent opt-ins gate this path:
  * network egress — off unless dial is enabled (caller checks the policy flag);
  * spend — off unless ``allow_spend`` is passed, and always bounded by a cap.

The endpoint is attacker-controllable on-chain data (anyone can register a .null
name and point its endpoint anywhere), so ``is_ssrf_safe_url`` is the load-bearing
guard: it rejects private / loopback / link-local / reserved / multicast /
unspecified targets, and DNS-resolves the hostname to reject a public name that
maps to one of those ranges. This raises the bar against DNS rebinding but does
not fully close it — the check is at resolution time, so a record that flips to
an internal IP between this check and the connect is a residual TOCTOU. It fails
CLOSED on any doubt.

Any miss — endpoint unset, SSRF-unsafe, dial disabled, or a remote error — returns
None so the caller falls back to LOCAL execution unchanged.
"""
from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from core.null_resolver import NullDomainRecord, is_valid_x402_endpoint
from core.web0_tools import _http

# Hard ceiling on any single dial spend. A caller cap is additionally clamped to
# this; never exceed it.
_MAX_SPEND_CEILING_USDC = 1.0

# HTTP status a paywalled x402 resource returns to demand payment first.
_PAYMENT_REQUIRED = 402


# RFC 6598 carrier-grade NAT shared space — used by carrier NAT and some cloud
# internal networks; ipaddress does NOT classify it as private, so check it.
_CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")


def _ip_is_internal(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for any address class an attacker could use to reach internal hosts.

    Covers private (10./172.16-31./192.168.), loopback (127./::1), link-local
    (169.254. incl. the 169.254.169.254 cloud metadata endpoint), CGNAT
    (100.64.0.0/10), reserved, multicast, and unspecified (0.0.0.0/::) ranges.
    """
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_V4)
    )


def is_ssrf_safe_url(url: str) -> bool:
    """True only when ``url`` is a public host that is safe to POST a task to.

    Parses the URL, rejects any literal-IP host in an internal range, and — for a
    hostname — resolves it via ``socket.getaddrinfo`` and rejects if ANY resolved
    address lands in an internal range (raises the bar against DNS rebinding,
    though a record that flips after this resolution is a residual TOCTOU). Fails
    CLOSED (returns False) on a parse or resolution error, or on any doubt.

    This is an IP-level guard layered on top of ``is_valid_x402_endpoint`` (which
    handles scheme/charset/length); call that first, then this for the IP check.
    """
    if not isinstance(url, str) or not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = parsed.hostname
    if not host:
        return False

    # Literal IP host: check it directly.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return not _ip_is_internal(literal)

    # Hostname: every resolved address must be in a public range.
    try:
        infos = socket.getaddrinfo(host, parsed.port or None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError, ValueError):
        return False
    if not infos:
        return False
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            return False
        addr = sockaddr[0]
        try:
            resolved = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if _ip_is_internal(resolved):
            return False
    return True


def _result_preview(record: NullDomainRecord, endpoint: str, amount_usdc: float) -> dict[str, Any]:
    """A no-spend preview describing the payment the endpoint is asking for."""
    return {
        "status": "user_action_required",
        "reason": "remote endpoint requires payment; re-run with spend enabled to pay",
        "name": record.name,
        "owner": record.owner,
        "endpoint": endpoint,
        "amount_usdc": amount_usdc,
        "max_spend_usdc": min(amount_usdc, _MAX_SPEND_CEILING_USDC) if amount_usdc else None,
    }


def _requirements_from_402(response: dict[str, Any]) -> dict[str, Any] | None:
    """The canonical x402 paymentRequirements from a 402 body (``accepts[0]``)."""
    accepts = response.get("accepts")
    if isinstance(accepts, list) and accepts and isinstance(accepts[0], dict):
        return accepts[0]
    return None


def _amount_from_402(response: dict[str, Any]) -> float:
    # Canonical x402: accepts[0].maxAmountRequired is in atomic units (6 dp USDC).
    req = _requirements_from_402(response)
    if req is not None and req.get("maxAmountRequired") is not None:
        try:
            return int(req["maxAmountRequired"]) / 1_000_000
        except (TypeError, ValueError):
            pass
    for key in ("amountUsdc", "amount_usdc", "amount"):
        value = response.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return 0.0


def _dial_pay_x402(
    resource_url: str,
    wallet: Any,
    *,
    max_spend_usdc: float = _MAX_SPEND_CEILING_USDC,
    allow_spend: bool = False,
    owner_local: bool = False,
    requirements: dict[str, Any] | None = None,
    task_text: str = "",
    http: Callable[..., dict[str, Any]] = _http,
    **_: Any,
) -> dict[str, Any]:
    """RETIRED dial settlement: the 402 is surfaced; paying it goes through core.wallet.x402 (fetch -> proposal -> approval -> retry)."""
    if not allow_spend or not requirements:
        return {"error": "payment_not_attempted"}
    from core.wallet.authority import refuse_legacy

    fault = refuse_legacy("null_dial._dial_pay_x402")
    return {"error": fault.code, "fault_id": fault.fault_id, "detail": fault.user_message, "requirements": dict(requirements)}


def try_dial(
    uri: str,
    task_text: str,
    *,
    record: NullDomainRecord,
    wallet: Any = None,
    allow_spend: bool = False,
    owner_local: bool = False,
    max_spend_usdc: float = _MAX_SPEND_CEILING_USDC,
    http: Callable[..., dict[str, Any]] = _http,
    pay: Callable[..., dict[str, Any]] = _dial_pay_x402,
) -> dict[str, Any] | None:
    """Reach a resolved .null agent's endpoint with a task; return its result, or None.

    Returns None (caller falls back to LOCAL) when: the record has no endpoint,
    the endpoint is SSRF-unsafe, or the remote call errors.

    On a normal result the remote payload is returned as-is. On an HTTP 402
    (payment required): with ``allow_spend`` on (within the cap) it calls
    ``dna_pay_and_unlock`` and returns the unlocked resource; with ``allow_spend``
    off it returns a no-spend ``user_action_required`` preview.

    The cap is clamped to a 1.0 USDC ceiling and never exceeded; payment is never
    attempted without ``allow_spend`` or without a wallet.
    """
    endpoint = (record.x402_endpoint or "").strip() if record is not None else ""
    if not endpoint:
        return None
    # First pass: scheme/charset/length. Then the load-bearing IP-level guard.
    if not is_valid_x402_endpoint(endpoint) or not is_ssrf_safe_url(endpoint):
        return None

    cap = max(0.0, min(float(max_spend_usdc or 0.0), _MAX_SPEND_CEILING_USDC))

    try:
        response = http("POST", endpoint, body={"uri": uri, "prompt": task_text, "task": task_text})
    except Exception:
        return None
    if not isinstance(response, dict):
        return None

    status = response.get("status")
    is_402 = response.get("error") and status == _PAYMENT_REQUIRED

    if not is_402:
        if response.get("error"):
            # A non-payment remote error -> fall back to LOCAL.
            return None
        return response

    # Payment required.
    requirements = _requirements_from_402(response)
    amount_usdc = _amount_from_402(response)
    if not allow_spend:
        return _result_preview(record, endpoint, amount_usdc)
    if wallet is None:
        return None
    if amount_usdc and amount_usdc > cap:
        return _result_preview(record, endpoint, amount_usdc)

    paid = pay(
        endpoint, wallet, max_spend_usdc=cap, allow_spend=True, owner_local=owner_local,
        requirements=requirements, task_text=task_text, http=http,
    )
    if not isinstance(paid, dict) or paid.get("error"):
        return None
    return paid


__all__ = ["is_ssrf_safe_url", "try_dial"]
