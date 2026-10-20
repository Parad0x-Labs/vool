"""Owner-local doors on a daemon bound to the local network.

The documented mobile-companion setup binds the API beyond loopback (``--bind`` plus
``VOOL_ALLOWED_HOSTS``). Any device on that network can then send ``Host: 127.0.0.1``, so the
Host guard is no owner check. These rows pin the owner check that is: the REAL socket peer
(``client_host``) must be loopback for every owner door, for every registry command that changes
the owner's settings (by capability, not by route), and for every tool a chat turn can run.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from core.request_trust import (
    IN_PROCESS_OWNER_PEER,
    OWNER_LOCAL_KEY,
    execution_peer_host,
    is_loopback_host,
    remote_turn_tool_refusal,
)
from core.runtime_paths import configure_runtime_home
from core.web.api.runtime import RuntimeServices

LAN_PEER = "192.168.1.55"
OWNER_PEER = "127.0.0.1"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    from core import runtime_paths

    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "vool"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    previous = runtime_paths._VOOL_HOME_OVERRIDE
    configure_runtime_home(tmp_path / "vool")
    try:
        yield
    finally:
        configure_runtime_home(previous)


@pytest.fixture()
def runtime():
    from storage.migrations import run_migrations

    run_migrations()
    return RuntimeServices(display_name="VOOL")


def _get(path, query=None, *, runtime, client_host):
    from core.web.api.service import dispatch_get

    return dispatch_get(
        path=path, query=query or {}, runtime=runtime, model_name="vool",
        client_host=client_host, headers={"Host": "127.0.0.1:11435"},
    )


def _post(path, body, *, runtime, client_host):
    from core.web.api.service import dispatch_post

    return dispatch_post(
        path=path, body=body,
        headers={"Host": "127.0.0.1:11435", "Content-Type": "application/json"},
        runtime=runtime, model_name="vool", workspace_root_provider=lambda: "",
        client_host=client_host,
    )


def _json(response) -> dict:
    return json.loads(response.body or b"{}")


# ---- the peer test itself -----------------------------------------------------------------------


@pytest.mark.parametrize("peer", ["127.0.0.1", "127.8.9.10", "::1", "[::1]", "::ffff:127.0.0.1",
                                  "0:0:0:0:0:0:0:1", "localhost"])
def test_loopback_peers_are_the_owner(peer):
    assert is_loopback_host(peer) is True


@pytest.mark.parametrize("peer", ["", "192.168.1.55", "10.0.0.2", "::ffff:192.168.1.55", "0.0.0.0",
                                  "127.evil.com", "127.0.0.1.example.com", "localhost.evil", "testclient",
                                  "fe80::1%eth0"])
def test_every_other_peer_is_not(peer):
    assert is_loopback_host(peer) is False


# ---- HTTP doors -------------------------------------------------------------------------------------

OWNER_GETS = [
    "/api/commands", "/api/commands/schema", "/api/commands/palette", "/api/commands/chat",
    "/api/chat/sessions", "/api/chat/history", "/api/chat/pins",
    "/api/settings/prefs", "/api/settings/credentials", "/api/files", "/api/files/raw",
    "/api/runtime/sessions", "/api/runtime/events", "/api/runtime/control-plane/status",
    "/api/repoops/sessions", "/api/repoops/session", "/api/cloud/keys", "/api/cloud/status",
    "/api/cloud/spend-limits", "/api/connections", "/api/onboarding/state",
    "/api/learning/procedures", "/api/bug-report/receipts",
]
OWNER_POSTS = [
    "/api/commands/dispatch", "/api/settings/prefs", "/api/settings/credentials", "/api/cloud/test",
    "/api/adaptation/loop/tick", "/api/credits/settle", "/v1/credits/settle", "/api/search/detect",
]


@pytest.mark.parametrize("path", OWNER_GETS)
@pytest.mark.parametrize("peer", [LAN_PEER, "", "::ffff:10.0.0.9"])
def test_owner_get_doors_refuse_any_peer_but_the_owner(path, peer, runtime):
    response = _get(path, runtime=runtime, client_host=peer)
    assert response.status == 403, (path, peer, response.body)
    assert _json(response)["error"] == "owner_local_required"


@pytest.mark.parametrize("path", OWNER_POSTS)
def test_owner_post_doors_refuse_a_lan_peer(path, runtime):
    response = _post(path, {"command_id": "settings.prefs.list", "provider": "openrouter"},
                     runtime=runtime, client_host=LAN_PEER)
    assert response.status == 403, (path, response.body)
    assert _json(response)["error"] == "owner_local_required"


def test_a_lan_prefs_write_changes_nothing_and_the_owner_write_lands(runtime):
    from core.user_preferences import load_preferences

    before = load_preferences().humor_percent
    refused = _post("/api/settings/prefs", {"humor_percent": 77}, runtime=runtime, client_host=LAN_PEER)
    assert refused.status == 403
    assert load_preferences().humor_percent == before
    saved = _post("/api/settings/prefs", {"humor_percent": 41}, runtime=runtime, client_host=OWNER_PEER)
    assert saved.status == 200, saved.body
    assert load_preferences().humor_percent == 41


def test_the_owner_still_reads_their_doors(runtime):
    for path in ("/api/chat/sessions", "/api/settings/prefs", "/api/settings/credentials", "/api/files",
                 "/api/commands", "/api/cloud/status", "/api/chat/pins"):
        response = _get(path, runtime=runtime, client_host=OWNER_PEER)
        assert response.status == 200, (path, response.body)


def test_a_lan_credential_write_stores_nothing(runtime):
    from core.credential_store import has_credential

    body = {"name": "llm.cloud.openrouter",
            "value": "lan-peer-test-credential-not-a-real-key"}
    assert _post("/api/settings/credentials", body, runtime=runtime, client_host=LAN_PEER).status == 403
    assert has_credential("llm.cloud.openrouter") is False


def test_the_authorities_refuse_a_caller_that_names_no_peer(runtime):
    from core.web.api.registry_authorities import set_credentials_authority, set_prefs_authority

    headers = {"Content-Type": "application/json"}
    assert set_prefs_authority({"humor_percent": 50}, headers, runtime).status == 403
    assert set_credentials_authority({"name": "llm.cloud.openrouter", "delete": True}, headers, runtime).status == 403
    assert set_prefs_authority({"humor_percent": 50}, headers, runtime, OWNER_PEER).status == 200


# ---- the command registry -----------------------------------------------------------------------


def test_commands_dispatch_carries_the_real_peer(runtime):
    lan = _post("/api/commands/dispatch", {"command_id": "settings.prefs.list", "input": {}},
                runtime=runtime, client_host=LAN_PEER)
    assert lan.status == 403
    owner = _post("/api/commands/dispatch", {"command_id": "settings.prefs.list", "input": {}},
                  runtime=runtime, client_host=OWNER_PEER)
    assert owner.status == 200, owner.body


def test_handle_commands_dispatch_without_a_peer_fails_closed():
    from core.command_registry.api import handle_commands_dispatch

    status, payload = handle_commands_dispatch({"command_id": "chats.search", "input": {"query": "holiday plans"}})
    assert status == 403, payload
    status, _payload = handle_commands_dispatch(
        {"command_id": "chats.search", "input": {"query": "holiday plans"}}, client_host=OWNER_PEER)
    assert status == 200


@dataclass(frozen=True)
class _Ctx:
    projection: str
    extra: dict


def test_the_execution_peer_is_the_transport_fact_or_unknown():
    assert execution_peer_host(_Ctx("api", {})) == ""
    assert execution_peer_host(_Ctx("model", {})) == ""
    assert execution_peer_host(_Ctx("api", {"client_host": LAN_PEER})) == LAN_PEER
    assert execution_peer_host(_Ctx("api", {"client_host": ""})) == ""
    # the owner's own terminal runs in-process, with no network boundary to cross
    assert execution_peer_host(_Ctx("cli", {})) == IN_PROCESS_OWNER_PEER


_HANDLER_CALLS: list[str] = []


@dataclass(frozen=True)
class _ToggleInput:
    enabled: bool = False


def _toggle_handler(inp, _ctx):
    from core.command_registry.spec import HandlerOk

    _HANDLER_CALLS.append(str(inp.enabled))
    return HandlerOk(data={"enabled": inp.enabled}, summary="toggled",
                     receipts=({"kind": "settings_write", "surface": "test"},))


def _unrouted_settings_registry():
    """A settings-class command with NO route and an open permission: only its capability says
    it changes the owner's settings (the shape of a command a later branch adds)."""
    from core.command_registry.registry import CommandRegistry
    from core.command_registry.spec import CommandSpec, GroupSpec, Handler, OpenRead

    reg = CommandRegistry()
    reg.add_group(GroupSpec(group_id="settings", description="test settings group"))
    reg.add(CommandSpec(
        command_id="settings.test_toggle.set", group="settings",
        description="Turn a test feature on or off",
        input_schema=_ToggleInput, effects="mutating",
        permission=OpenRead(), capabilities=frozenset({"change_settings"}),
        handler=Handler("tests.test_lan_owner_gates:_toggle_handler"), exit_codes=(0, 20, 21),
    ))
    return reg


@pytest.mark.parametrize(
    ("projection", "extra", "allowed"),
    [
        ("api", {"client_host": LAN_PEER}, False),
        ("api", {"client_host": "::ffff:192.168.1.55"}, False),
        ("api", {}, False),
        ("model", {}, False),
        ("api", {"client_host": OWNER_PEER}, True),
        ("cli", {}, True),
    ],
)
def test_a_settings_capability_is_owner_local_whatever_the_route(projection, extra, allowed):
    from core.command_registry.execute import ExecutionContext, execute_command
    from core.command_registry.legacy import envelope_status

    _HANDLER_CALLS.clear()
    envelope = execute_command(
        "settings.test_toggle.set", {"enabled": True}, reg=_unrouted_settings_registry(),
        context=ExecutionContext(projection=projection, extra=extra),
    )
    if allowed:
        assert envelope.ok, envelope.to_json()
        assert _HANDLER_CALLS == ["True"]
    else:
        assert not envelope.ok
        assert envelope.fault.code == "permission_denied"
        assert envelope_status(envelope) == 403
        assert envelope.fault.detail["error"] == "owner_local_required"
        assert _HANDLER_CALLS == [], "the handler must never run for a non-owner peer"


def test_a_forwarded_settings_command_from_a_lan_peer_is_refused():
    from core.command_registry.legacy import forward
    from core.user_preferences import load_preferences

    before = load_preferences().humor_percent
    status, payload = forward("settings.prefs.set", {"prefs": {"humor_percent": 88}}, client_host=LAN_PEER)
    assert status == 403, payload
    assert load_preferences().humor_percent == before


def test_media_editor_commands_are_owner_local_and_no_longer_crash():
    from core.command_registry.execute import ExecutionContext, execute_command
    from core.command_registry.legacy import envelope_status

    lan = execute_command("media.editor.open", {"body": {"source_path": "/etc/hostname"}},
                          context=ExecutionContext(projection="api", extra={"client_host": LAN_PEER}))
    assert envelope_status(lan) == 403, lan.to_json()
    owner = execute_command("media.editor.open", {"body": {"source_path": ""}},
                            context=ExecutionContext(projection="api", extra={"client_host": OWNER_PEER}))
    # the owner reaches the editor itself (an empty path is its own 400), not a 500 TypeError
    assert envelope_status(owner) == 400, owner.to_json()


# ---- chat turns from another device ----------------------------------------------------------------


def test_remote_turn_fence_only_acts_on_the_server_stamp():
    assert remote_turn_tool_refusal("machine.read_file", {OWNER_LOCAL_KEY: False}) == "owner_local_required"
    assert remote_turn_tool_refusal("machine.read_file", {OWNER_LOCAL_KEY: True}) == ""
    assert remote_turn_tool_refusal("machine.read_file", {"surface": "cli"}) == ""
    assert remote_turn_tool_refusal("web.fetch", {OWNER_LOCAL_KEY: False}) == ""


def test_a_remote_turn_cannot_read_the_owners_files(tmp_path):
    from core.runtime_execution_tools import execute_runtime_tool

    docs = tmp_path / "home" / "Documents"
    docs.mkdir(parents=True)
    (docs / "private.txt").write_text("owner secret 4411\n", encoding="utf-8")
    remote = execute_runtime_tool("machine.read_file", {"path": "~/Documents/private.txt"},
                                  source_context={OWNER_LOCAL_KEY: False, "surface": "api"})
    assert remote is not None and remote.ok is False
    assert remote.status == "owner_local_required"
    assert "owner secret 4411" not in remote.response_text
    owner = execute_runtime_tool("machine.read_file", {"path": "~/Documents/private.txt"},
                                 source_context={OWNER_LOCAL_KEY: True, "surface": "api"})
    assert owner is not None and owner.ok is True, owner.response_text
    assert "owner secret 4411" in owner.response_text


def test_a_remote_turn_cannot_run_the_local_operator_lane():
    from core.execution.operator_tools import execute_operator_tool

    calls: list = []

    def _dispatch(intent, **kw):
        calls.append(intent)
        raise AssertionError("the operator lane must not run for a remote turn")

    result = execute_operator_tool("operator.inspect_processes", {}, task_id="t", session_id="s",
                                   dispatch_operator_action_fn=_dispatch,
                                   source_context={OWNER_LOCAL_KEY: False})
    assert result.ok is False and result.status == "owner_local_required"
    assert calls == []
    owner = execute_operator_tool("operator.inspect_processes", {}, task_id="t", session_id="s",
                                  source_context={OWNER_LOCAL_KEY: True})
    assert owner.ok is True, owner.response_text
