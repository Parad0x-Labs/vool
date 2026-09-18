from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlparse

import requests

from core import auto_local_only_mode, policy_engine
from core.cloud_credential_broker import CloudCredentialBroker
from core.remote_fetch_policy import note_remote_fetch_attempt, remote_fetch_forbidden

_CGNAT_V4 = ipaddress.ip_network("100.64.0.0/10")
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class CloudTransportError(RuntimeError):
    pass


def _address_is_internal(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return True
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
        or (isinstance(address, ipaddress.IPv4Address) and address in _CGNAT_V4)
    )


def _resolve_public_addresses(host: str, port: int, resolver: Callable[..., Iterable[Any]]) -> frozenset[str]:
    try:
        infos = resolver(host, port, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError, ValueError) as exc:
        raise CloudTransportError("provider_host_resolution_failed") from exc
    addresses: set[str] = set()
    for info in infos:
        sockaddr = info[4] if len(info) > 4 else None
        address = str(sockaddr[0]) if sockaddr else ""
        if not address or _address_is_internal(address):
            raise CloudTransportError("provider_host_resolved_to_blocked_address")
        addresses.add(address)
    if not addresses:
        raise CloudTransportError("provider_host_resolution_empty")
    return frozenset(addresses)


def _response_peer_ip(response: Any) -> str:
    raw = getattr(response, "raw", None)
    connection = getattr(raw, "_connection", None) or getattr(raw, "connection", None)
    sock = getattr(connection, "sock", None)
    if sock is None:
        original = getattr(raw, "_original_response", None)
        sock = getattr(getattr(getattr(original, "fp", None), "raw", None), "_sock", None)
    if sock is None:
        return ""
    try:
        return str(sock.getpeername()[0])
    except Exception:
        return ""


class PolicyBoundCloudTransport:
    def __init__(
        self,
        *,
        allowed_hosts: tuple[str, ...],
        credential_broker: CloudCredentialBroker | None = None,
        requester: Callable[..., Any] | None = None,
        resolver: Callable[..., Iterable[Any]] | None = None,
        peer_ip_getter: Callable[[Any], str] | None = None,
        network_allowed: Callable[[], bool] | None = None,
    ) -> None:
        self._allowed_hosts = frozenset(str(host).strip().lower() for host in allowed_hosts if str(host).strip())
        self._credentials = credential_broker or CloudCredentialBroker()
        self._requester = requester or requests.request
        self._resolver = resolver or socket.getaddrinfo
        self._peer_ip_getter = peer_ip_getter or _response_peer_ip
        self._network_allowed = network_allowed or (
            lambda: bool(policy_engine.get("network.outbound_enabled", False))
            and not policy_engine.local_only_mode()
            # The per-turn Local Only lane, on top of the machine-wide setting. This transport has
            # no request context to consult, so it reads the ambient scope; the turn-scoped block
            # that does cross threads lives at the router and adapter seams. Additive: this can
            # only refuse a call the other checks would have allowed.
            and not auto_local_only_mode.turn_is_local_only(None)
        )

    def request_json(
        self,
        *,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        credential_name: str = "",
        credential_env: str = "",
        credential_scheme: str = "bearer",
        timeout_seconds: float = 30.0,
    ) -> tuple[int, dict[str, str], Any]:
        if remote_fetch_forbidden() or not self._network_allowed():
            raise CloudTransportError("cloud_network_disabled_by_policy")
        parsed = urlparse(str(url or ""))
        host = str(parsed.hostname or "").strip().lower()
        if parsed.scheme != "https" or not host:
            raise CloudTransportError("cloud_endpoint_requires_https")
        if host not in self._allowed_hosts:
            raise CloudTransportError("cloud_endpoint_host_not_allowed")
        port = int(parsed.port or 443)
        approved_addresses = _resolve_public_addresses(host, port, self._resolver)

        request_headers = {str(key): str(value) for key, value in dict(headers or {}).items()}
        credential = self._credentials.resolve(credential_name, env_name=credential_env)
        if credential_name and not credential:
            raise CloudTransportError("cloud_credentials_missing")
        if credential:
            scheme = str(credential_scheme or "bearer").strip().lower()
            if scheme != "bearer":
                raise CloudTransportError("unsupported_cloud_credential_scheme")
            request_headers["Authorization"] = f"Bearer {credential}"

        if host == "openrouter.ai":
            # This is the final transport boundary for the policy-bound OpenRouter client. Even
            # if a provider or future caller supplies attribution headers, the shared factory
            # wins immediately before the request is handed to requests.
            from core.runtime_provider_defaults import apply_openrouter_attribution_headers

            request_headers = apply_openrouter_attribution_headers(request_headers)

        note_remote_fetch_attempt(url)
        try:
            response = self._requester(
                str(method or "GET").upper(),
                url,
                headers=request_headers,
                json=body,
                timeout=max(1.0, min(float(timeout_seconds), 300.0)),
                allow_redirects=False,
                stream=True,
            )
        except requests.Timeout as exc:
            raise CloudTransportError("cloud_provider_timeout") from exc
        except requests.RequestException as exc:
            raise CloudTransportError("cloud_provider_network_error") from exc
        except Exception as exc:
            raise CloudTransportError("cloud_provider_transport_error") from exc

        peer_ip = str(self._peer_ip_getter(response) or "")
        if not peer_ip or peer_ip not in approved_addresses or _address_is_internal(peer_ip):
            close = getattr(response, "close", None)
            if callable(close):
                close()
            raise CloudTransportError("cloud_provider_peer_address_mismatch")

        try:
            content = bytes(getattr(response, "content", b"") or b"")
            if len(content) > _MAX_RESPONSE_BYTES:
                raise CloudTransportError("cloud_provider_response_too_large")
            try:
                payload = response.json() if hasattr(response, "json") else json.loads(content.decode("utf-8"))
            except Exception as exc:
                raise CloudTransportError("cloud_provider_malformed_json") from exc
            response_headers = {
                str(key).lower(): str(value)
                for key, value in dict(getattr(response, "headers", {}) or {}).items()
                if str(key).lower()
                in {"content-type", "retry-after", "x-ratelimit-remaining", "x-ratelimit-reset"}
            }
            return int(getattr(response, "status_code", 0) or 0), response_headers, payload
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()


__all__ = ["CloudTransportError", "PolicyBoundCloudTransport"]
