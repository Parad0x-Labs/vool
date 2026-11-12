"""Web0 off is a real gate: no `.null` lookup and no Web0 tool reaches the network.

The Settings row says that while the Web0 switch is off, `.null` name lookups do
not reach the network and Web0 starts nothing. These tests drive the real doors
(resolver, `/api/null` with the dial opt-in ON, the CLI, the model's web0 tools,
in-chat registration, the poll thread) with the switch off and a trap on the
shared outbound door, and check that nothing is sent and that the person is told
Web0 is off. The "on" cases prove the gate does not block a turned-on switch.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from core import runtime_paths
from core.web0_settings import set_web0_enabled, web0_enabled


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


class _Trap:
    """Stands in for the shared outbound door; records every request it is handed."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, request, *args: Any, **kwargs: Any):
        self.urls.append(str(getattr(request, "full_url", request)))
        raise OSError("trap: no network in tests")


@pytest.fixture
def net(monkeypatch) -> _Trap:
    import core.remote_fetch_policy as remote_fetch_policy
    import core.web0_tools as web0_tools

    trap = _Trap()
    monkeypatch.setattr(remote_fetch_policy, "open_remote", trap)
    monkeypatch.setattr(web0_tools, "open_remote", trap)
    return trap


# -- .null lookups -----------------------------------------------------------------


def test_resolver_refuses_typed_and_sends_nothing_while_off(net) -> None:
    from core.null_resolver import resolve_null_domain, resolve_x402_endpoint

    assert web0_enabled() is False  # fresh install
    for lookup in (resolve_null_domain, resolve_x402_endpoint):
        with pytest.raises(Exception) as refused:
            lookup("parad0x")
        assert net.urls == []
        assert type(refused.value).__name__ == "Web0OffError"


def test_resolver_reaches_the_chain_once_switched_on(net) -> None:
    from core.null_resolver import resolve_null_domain

    set_web0_enabled(True)
    assert resolve_null_domain("parad0x") is None  # the trap answers like an unreachable RPC
    assert net.urls, "with Web0 on the lookup must go out through the shared door"


def test_api_null_with_dial_on_stays_local_while_web0_off(net, monkeypatch) -> None:
    from core import policy_engine
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    monkeypatch.setattr(policy_engine, "null_dial_enabled", lambda: True)

    def dial_must_not_run(*_a, **_k):
        raise AssertionError("dialed a .null agent while Web0 is off")

    resp = dispatch_post(
        path="/api/null",
        body={"uri": "null://web0/task", "prompt": "review"},
        headers={},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        run_agent_provider=lambda runtime, text, **_k: {"response": f"local: {text}", "confidence": 1.0},
        try_dial_provider=dial_must_not_run,
    )
    assert resp.status == 200
    assert json.loads(resp.body)["result"] == "local: review"
    assert net.urls == []


def test_resolve_helper_answers_web0_off_not_a_resolver_error(net, monkeypatch) -> None:
    from core import policy_engine
    from core.web.api.service import _web0_resolve_response

    monkeypatch.setattr(policy_engine, "local_only_mode", lambda: False)
    resp = _web0_resolve_response({"name": ["web0.null"]})
    body = json.loads(resp.body)
    assert resp.status == 403
    assert body["code"] == "web0_off"
    assert "Settings" in body["error"]
    assert net.urls == []


def test_cli_resolve_and_dial_say_web0_is_off(net, monkeypatch, capsys) -> None:
    from apps import vool_cli
    from core import policy_engine

    assert vool_cli.cmd_resolve("parad0x.null") == 2
    assert "Special web addresses are off" in capsys.readouterr().out

    monkeypatch.setattr(policy_engine, "null_dial_enabled", lambda: True)
    assert vool_cli.cmd_dial("parad0x.null", "review this", json_mode=True) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["web0_enabled"] is False and payload["resolved"] is False and payload["dialed"] is False
    assert net.urls == []


def test_chat_registration_says_web0_is_off_not_already_registered(net) -> None:
    from core.null_register_chat import maybe_handle_null_registration

    staged: list[Any] = []
    response = maybe_handle_null_registration(
        "register freshname.null",
        "s1",
        wallet_fn=lambda: SimpleNamespace(pubkey="OwNeR11111111111111111111111111111111111111"),
        stage_fn=lambda *a, **k: staged.append((a, k)),
        clear_fn=lambda _sid: None,
    )
    assert response is not None
    text = json.dumps(response)
    assert "Special web addresses are off" in text
    assert "already registered" not in text
    assert staged == []  # no offer staged, so no "yes" can act on it
    assert net.urls == []


# -- the model's web0 tools --------------------------------------------------------


def test_web0_publish_with_trusted_opt_in_refuses_while_off(net) -> None:
    from core.runtime_execution_tools import execute_runtime_tool
    from core.web0_tools import web0_create_project

    created = web0_create_project("landing_page", "loop.null", "Loop", http=lambda *a, **k: {"error": True})
    res = execute_runtime_tool(
        "web0.publish",
        {"project_id": created["project_id"]},
        source_context={"allow_network_publish": True, "vool_wallet": SimpleNamespace(pubkey="P")},
    )
    assert res is not None and not res.ok
    assert res.status == "web0_off"
    assert "Special web addresses are off" in res.response_text
    assert net.urls == []


def test_web0_builder_tool_drafts_locally_and_sends_nothing_while_off(net) -> None:
    from core.runtime_execution_tools import execute_runtime_tool

    res = execute_runtime_tool(
        "web0.create_project", {"template_id": "landing_page", "domain": "loop.null", "project_name": "Loop"}
    )
    assert res is not None
    assert net.urls == []


def test_web0_portal_door_goes_out_once_switched_on(net) -> None:
    from core.web0_tools import web0_list_templates

    set_web0_enabled(True)
    web0_list_templates()
    assert net.urls, "with Web0 on the builder reaches its portal"


# -- background work ---------------------------------------------------------------


def test_poller_claims_nothing_after_being_switched_off_mid_wait(monkeypatch) -> None:
    import core.helper_scheduler as helper_scheduler
    import core.runtime_backbone as backbone

    set_web0_enabled(True)
    claimed: list[str] = []

    class _Scheduler:
        def __init__(self) -> None:
            claimed.append("scheduler built")

        def can_accept_mesh_task(self) -> bool:
            return False

    def _sleep_then_switch_off(_seconds: float) -> None:
        set_web0_enabled(False)

    monkeypatch.setattr(helper_scheduler, "HelperScheduler", _Scheduler)
    monkeypatch.setattr(backbone, "_web0_poll_wait", _sleep_then_switch_off)
    backbone._web0_task_poll_loop()
    assert claimed == []


# -- the switch's own routes go through the command registry -----------------------


def test_settings_routes_are_registry_commands(monkeypatch) -> None:
    import core.runtime_backbone as backbone
    from core.command_registry.execute import execute_command
    from core.command_registry.legacy import forward
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post

    monkeypatch.setattr(backbone, "start_web0_background_workers", lambda: None)
    shown = execute_command("settings.web0.show", {})
    assert shown.ok and shown.data["enabled"] is False

    bad = dispatch_post(
        path="/api/web0/settings", body={"enabled": "yes"}, headers={},
        runtime=RuntimeServices(display_name="VOOL"), model_name="vool", workspace_root_provider=lambda: "/tmp",
        client_host="127.0.0.1",
    )
    assert bad.status == 400

    # The owner's own computer, as the route forwards it (the transport peer travels along).
    status, written = forward("settings.web0.set", {"enabled": True}, client_host="127.0.0.1")
    assert status == 200 and written == {"ok": True, "enabled": True}
    got = dispatch_get(
        path="/api/web0/settings", query={}, runtime=RuntimeServices(display_name="VOOL"), model_name="vool",
        client_host="127.0.0.1",
    )
    assert got.status == 200 and json.loads(got.body) == {"ok": True, "enabled": True}

    status, _refused = forward("settings.web0.set", {"enabled": "on"}, client_host="127.0.0.1")
    assert status == 400
    assert web0_enabled() is True  # a refused write changes nothing
