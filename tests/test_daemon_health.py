from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from core.daemon import DaemonConfig
from core.daemon.health import create_health_app, start_health_server
from tests.asgi_harness import asgi_request


class _FakeDaemon:
    def __init__(self, *, host: str, token: str | None = None) -> None:
        self.config = DaemonConfig(
            bind_host="127.0.0.1",
            bind_port=49152,
            health_bind_host=host,
            health_bind_port=19090,
            health_auth_token=token,
        )
        self._health_server = None
        self._health_thread = None

    def _health_snapshot(self) -> dict[str, object]:
        return {"service": "daemon", "status": "ok"}


def test_create_health_app_allows_loopback_health_without_token_and_emits_request_id() -> None:
    app = create_health_app(_FakeDaemon(host="127.0.0.1"))

    status, headers, body = asgi_request(app, method="GET", path="/healthz", headers={"X-Request-ID": "req-daemon-123"})

    assert status == 200
    assert headers["x-request-id"] == "req-daemon-123"
    assert headers["x-correlation-id"] == "req-daemon-123"
    assert b'"ok": true' in body or b'"ok":true' in body


def test_create_health_app_requires_token_for_non_loopback_bind() -> None:
    app = create_health_app(_FakeDaemon(host="0.0.0.0", token="health-token"))

    unauthorized_status, _, _ = asgi_request(app, method="GET", path="/healthz")
    authorized_status, _, _ = asgi_request(
        app,
        method="GET",
        path="/healthz",
        headers={"X-Vool-Health-Token": "health-token"},
    )

    assert unauthorized_status == 401
    assert authorized_status == 200


def test_start_health_server_runs_uvicorn_on_configured_host_and_port() -> None:
    daemon = _FakeDaemon(host="127.0.0.1", token=None)

    with mock.patch("uvicorn.Server") as server_cls:
        fake_server = server_cls.return_value
        fake_server.run = mock.Mock()
        with mock.patch("uvicorn.Config") as config_cls:
            config_cls.return_value = SimpleNamespace(host="127.0.0.1", port=19090)
            start_health_server(daemon)

    config_cls.assert_called_once()
    _, kwargs = config_cls.call_args
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 19090
    assert kwargs["access_log"] is False
    assert kwargs["log_level"] == "warning"
    assert daemon._health_server is fake_server
    assert daemon._health_thread is not None
