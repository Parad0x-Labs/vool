from __future__ import annotations

import io
from types import SimpleNamespace
from unittest import mock

import pytest

from apps.vool_api_server import VoolAPIHandler
from core.request_trust import is_loopback_host
from core.web.api.runtime import RuntimeServices
from core.web.api.service import json_response


@pytest.mark.parametrize(
    ("peer_host", "expected_owner_local"),
    (
        ("127.0.0.1", True),
        ("198.51.100.17", False),
    ),
)
def test_legacy_post_uses_peer_host_for_owner_local_derivation(
    peer_host: str,
    expected_owner_local: bool,
) -> None:
    handler = object.__new__(VoolAPIHandler)
    handler.client_address = (peer_host, 4242)
    handler.headers = {
        "Host": "127.0.0.1",
        "Content-Length": "2",
    }
    handler.path = "/api/test"
    handler.rfile = io.BytesIO(b"{}")
    handler.server = SimpleNamespace(
        vool_runtime=RuntimeServices(display_name="VOOL")
    )
    handler._write_response = mock.Mock()

    with mock.patch(
        "apps.vool_api_server._dispatch_post",
        return_value=json_response(200, {"ok": True}),
    ) as dispatch:
        VoolAPIHandler.do_POST(handler)

    forwarded_host = dispatch.call_args.kwargs["client_host"]
    assert forwarded_host == peer_host
    assert is_loopback_host(forwarded_host) is expected_owner_local
