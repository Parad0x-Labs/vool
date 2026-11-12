"""The Web0 switch is the owner's own setting: only this computer's VOOL session turns it on.

With VOOL listening on the local network, another device (or one spoofing a
`Host: 127.0.0.1` header) must not be able to flip the switch, through the
route or straight through the command registry. The owner's own computer still can.
"""
from __future__ import annotations

import json

import pytest

from core import runtime_paths
from core.web0_settings import web0_enabled

LAN_PEER = "192.168.1.55"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    import core.runtime_backbone as backbone

    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    monkeypatch.setattr(backbone, "start_web0_background_workers", lambda: None)
    yield
    runtime_paths.configure_runtime_home(None)


def _post(body: dict, *, client_host: str, headers: dict | None = None):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    return dispatch_post(
        path="/api/web0/settings",
        body=body,
        headers=headers or {},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host=client_host,
    )


@pytest.mark.parametrize("headers", [{}, {"Host": "127.0.0.1:11435"}])
def test_a_lan_peer_cannot_turn_web0_on(headers) -> None:
    resp = _post({"enabled": True}, client_host=LAN_PEER, headers=headers)
    assert resp.status == 403
    assert json.loads(resp.body)["error"] == "owner_local_required"
    assert web0_enabled() is False


def test_a_lan_peer_cannot_reach_the_registry_command_directly() -> None:
    from core.command_registry.legacy import forward

    status, payload = forward("settings.web0.set", {"enabled": True}, client_host=LAN_PEER)
    assert status == 403 and payload["error"] == "owner_local_required"
    assert web0_enabled() is False


def test_the_owner_computer_still_turns_it_on_and_off() -> None:
    on = _post({"enabled": True}, client_host="127.0.0.1")
    assert on.status == 200 and json.loads(on.body) == {"ok": True, "enabled": True}
    off = _post({"enabled": False}, client_host="127.0.0.1")
    assert off.status == 200 and web0_enabled() is False
