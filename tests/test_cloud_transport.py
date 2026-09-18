from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from core.cloud_credential_broker import CloudCredentialBroker
from core.cloud_transport import CloudTransportError, PolicyBoundCloudTransport


def _resolver_for(address: str):
    return lambda *_args, **_kwargs: [(2, 1, 6, "", (address, 443))]


@dataclass
class _Response:
    status_code: int = 200
    payload: Any = field(default_factory=lambda: {"ok": True})
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b"{}"
    closed: bool = False

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


def _transport(*, requester, resolver=None, peer="93.184.216.34", broker=None, allowed=True):
    return PolicyBoundCloudTransport(
        allowed_hosts=("api.example.com",),
        requester=requester,
        resolver=resolver or _resolver_for("93.184.216.34"),
        peer_ip_getter=lambda _response: peer,
        credential_broker=broker,
        network_allowed=lambda: allowed,
    )


def test_host_allowlist_blocks_before_request() -> None:
    called = False

    def requester(*_args, **_kwargs):
        nonlocal called
        called = True
        return _Response()

    transport = _transport(requester=requester)
    with pytest.raises(CloudTransportError, match="host_not_allowed"):
        transport.request_json(method="GET", url="https://evil.example/models")
    assert called is False


def test_private_dns_resolution_blocks_ssrf_before_request() -> None:
    transport = _transport(requester=lambda *_a, **_k: _Response(), resolver=_resolver_for("127.0.0.1"))
    with pytest.raises(CloudTransportError, match="blocked_address"):
        transport.request_json(method="GET", url="https://api.example.com/models")


def test_connected_peer_must_match_preflight_dns_set() -> None:
    response = _Response()
    transport = _transport(requester=lambda *_a, **_k: response, peer="10.0.0.8")
    with pytest.raises(CloudTransportError, match="peer_address_mismatch"):
        transport.request_json(method="GET", url="https://api.example.com/models")
    assert response.closed is True


def test_network_policy_blocks_before_request() -> None:
    called = False

    def requester(*_args, **_kwargs):
        nonlocal called
        called = True
        return _Response()

    with pytest.raises(CloudTransportError, match="disabled_by_policy"):
        _transport(requester=requester, allowed=False).request_json(
            method="GET", url="https://api.example.com/models"
        )
    assert called is False


def test_credential_is_attached_only_inside_transport_and_not_in_errors() -> None:
    secret = "test-only-transport-credential"
    captured: dict[str, Any] = {}
    broker = CloudCredentialBroker()
    broker.set_session_credential("provider.key", secret)

    def requester(*_args, **kwargs):
        captured.update(kwargs)
        return _Response()

    status, _headers, payload = _transport(requester=requester, broker=broker).request_json(
        method="GET",
        url="https://api.example.com/models",
        credential_name="provider.key",
    )
    assert status == 200 and payload == {"ok": True}
    assert captured["headers"]["Authorization"] == f"Bearer {secret}"

    missing = _transport(requester=requester)
    with pytest.raises(CloudTransportError) as exc:
        missing.request_json(
            method="GET",
            url="https://api.example.com/models",
            credential_name="missing.key",
        )
    assert secret not in str(exc.value)


def test_malformed_json_fails_closed_and_closes_response() -> None:
    response = _Response()

    def malformed():
        raise ValueError("invalid JSON")

    response.json = malformed
    with pytest.raises(CloudTransportError, match="malformed_json"):
        _transport(requester=lambda *_a, **_k: response).request_json(
            method="GET", url="https://api.example.com/models"
        )
    assert response.closed is True


def test_success_closes_streamed_response() -> None:
    response = _Response()
    assert _transport(requester=lambda *_a, **_k: response).request_json(
        method="GET", url="https://api.example.com/models"
    )[0] == 200
    assert response.closed is True
