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
    HTTP_INGRESS_KEY,
    IN_PROCESS_OWNER_PEER,
    OWNER_LOCAL_KEY,
    execution_peer_host,
    in_process_owner_extra,
    is_loopback_host,
    owner_peer_for_turn,
    remote_turn_tool_refusal,
    turn_is_remote,
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
    "/api/cloud/diagnostics", "/api/runtime/capabilities", "/v1/runtime/capabilities", "/api/projects",
    "/api/runtime/usage", "/v1/runtime/usage", "/api/credits/balance", "/v1/credits/balance",
    "/api/runtime/receipts", "/api/reminders", "/api/runtime/unresolved-effects", "/api/chat/proof",
    "/api/adaptation/status", "/api/adaptation/loop", "/api/adaptation/jobs", "/api/adaptation/job-events",
    "/api/adaptation/evals", "/api/model-tool-certification", "/api/runtime/changes", "/api/bug-report/status",
    "/api/cloud/usepod/discovery", "/api/cloud/usepod/x402/unresolved", "/api/oauth/status", "/api/plugins",
    "/api/plugins/lifecycle", "/api/skills", "/api/addons", "/api/models/local", "/api/cloud/models",
    "/api/cloud/providers", "/api/search/providers", "/api/discovery", "/api/intake/quarantine/list",
    # fail closed: a door nobody has classified yet is the owner's
    "/api/a-route-added-next-month", "/v1/a-route-added-next-month",
]
OWNER_POSTS = [
    "/api/commands/dispatch", "/api/settings/prefs", "/api/settings/credentials", "/api/cloud/test",
    "/api/adaptation/loop/tick", "/api/credits/settle", "/v1/credits/settle", "/api/search/detect",
    "/api/search/test", "/api/chain/test", "/api/cloud/auto-model", "/api/generate",
    "/api/a-route-added-next-month",
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
    # no projection is the owner by itself: the owner's own terminal names itself explicitly
    assert execution_peer_host(_Ctx("cli", {})) == ""
    assert execution_peer_host(_Ctx("cli", in_process_owner_extra())) == IN_PROCESS_OWNER_PEER
    # a command the model runs for a turn is the owner's only on an explicitly owner-local turn
    assert owner_peer_for_turn({OWNER_LOCAL_KEY: True}) == IN_PROCESS_OWNER_PEER
    for turn in ({OWNER_LOCAL_KEY: False}, {OWNER_LOCAL_KEY: "true"}, {HTTP_INGRESS_KEY: True}, {}, None):
        assert owner_peer_for_turn(turn) == "", turn


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
        ("cli", {}, False),
        ("api", {"client_host": OWNER_PEER}, True),
        ("cli", {"client_host": IN_PROCESS_OWNER_PEER}, True),
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


def test_remote_turn_fence_fails_closed_on_served_turns():
    assert remote_turn_tool_refusal("machine.read_file", {OWNER_LOCAL_KEY: False}) == "owner_local_required"
    assert remote_turn_tool_refusal("machine.read_file", {OWNER_LOCAL_KEY: True}) == ""
    # a turn the HTTP service admitted with no stamp, or with anything but an explicit True, is remote
    for unknown in ({HTTP_INGRESS_KEY: True}, {HTTP_INGRESS_KEY: True, OWNER_LOCAL_KEY: None},
                    {HTTP_INGRESS_KEY: True, OWNER_LOCAL_KEY: "true"}, {OWNER_LOCAL_KEY: 1}):
        assert turn_is_remote(unknown), unknown
        assert remote_turn_tool_refusal("machine.read_file", unknown) == "owner_local_required", unknown
    assert not turn_is_remote({HTTP_INGRESS_KEY: True, OWNER_LOCAL_KEY: True})
    # a context built in-process (never crossed the HTTP boundary) is not a remote turn
    assert remote_turn_tool_refusal("machine.read_file", {"surface": "cli"}) == ""
    assert remote_turn_tool_refusal("web.fetch", {OWNER_LOCAL_KEY: False}) == ""
    # a browser cannot be pinned to checked public addresses, so it is the owner's only
    assert remote_turn_tool_refusal("browser.render", {OWNER_LOCAL_KEY: False}) == "owner_local_required"


def test_the_ingress_stamps_cannot_come_from_a_request_body():
    from core.request_trust import strip_reserved_trust_keys

    assert strip_reserved_trust_keys({HTTP_INGRESS_KEY: False, OWNER_LOCAL_KEY: True, "surface": "api"}) == {
        "surface": "api"}


def test_every_served_turn_carries_an_explicit_owner_local_state(monkeypatch):
    from core.web.api import runtime as runtime_mod

    seen: list = []

    class _Agent:
        def run_once(self, user_text, *, session_id_override=None, source_context=None):
            seen.append(dict(source_context or {}))
            return {"response": "ok", "confidence": 1.0}

    services = RuntimeServices(display_name="VOOL")
    services.agent = _Agent()
    for given, expected in (({}, False), ({OWNER_LOCAL_KEY: False}, False), ({OWNER_LOCAL_KEY: "yes"}, False),
                            ({OWNER_LOCAL_KEY: True}, True)):
        seen.clear()
        try:
            runtime_mod.run_agent(services, "hello", source_context=dict(given), workspace_root_provider=lambda: "")
        except Exception:
            pass  # the turn's own post-processing is not under test; the agent saw its context
        assert seen, given
        assert seen[0][HTTP_INGRESS_KEY] is True, given
        assert seen[0][OWNER_LOCAL_KEY] is expected, given


def test_generate_stamps_the_peer_it_came_from(runtime):
    from core.web.api.service import dispatch_post

    contexts: list = []

    def _run_agent(runtime_, text, *, session_id=None, source_context=None, workspace_root_provider=None):
        contexts.append(dict(source_context or {}))
        return {"response": "ok", "confidence": 1.0}

    response = dispatch_post(path="/api/generate", body={"prompt": "hi", "stream": False},
                             headers={"Host": "127.0.0.1:11435"}, runtime=runtime, model_name="vool",
                             workspace_root_provider=lambda: "", client_host=OWNER_PEER, run_agent_provider=_run_agent)
    assert response.status == 200, response.body
    assert contexts and contexts[0][OWNER_LOCAL_KEY] is True


def test_a_command_the_model_runs_for_a_turn_carries_that_turns_peer(monkeypatch):
    from core.command_registry import execute as execute_mod
    from core.command_registry import model_tools

    seen: list = []
    real = execute_mod.execute_command

    def _spy(command_id, input_data=None, *, context=None, **kw):
        seen.append(dict(context.extra))
        return real(command_id, input_data, context=context, **kw)

    monkeypatch.setattr(execute_mod, "execute_command", _spy)
    tool = model_tools.tool_intent_for("status.show")
    for turn, expected in (({OWNER_LOCAL_KEY: True}, IN_PROCESS_OWNER_PEER), ({OWNER_LOCAL_KEY: False}, ""), (None, "")):
        seen.clear()
        model_tools.execute_model_command(tool, {}, source_context=turn)
        assert seen and seen[0]["client_host"] == expected, (turn, seen)


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


# ---- the doors that stay open to the network ------------------------------------------------------

PUBLIC_GETS = ["/api/tags", "/v1/models", "/api/runtime/version", "/v1/runtime/version", "/v1/healthz",
               "/healthz", "/api/chat/attachments/limits", "/api/mobile/info"]


@pytest.mark.parametrize("path", PUBLIC_GETS)
def test_public_doors_answer_another_device(path, runtime):
    response = _get(path, runtime=runtime, client_host=LAN_PEER)
    assert response.status == 200, (path, response.body)


def test_a_chat_from_another_device_is_admitted_as_a_non_owner_turn(runtime):
    # /api/chat stays reachable (the phone and other chat clients use it); the 400 is the door's own
    # answer to a body with no message, not the owner gate.
    response = _post("/api/chat", {"messages": []}, runtime=runtime, client_host=LAN_PEER)
    assert response.status == 400, response.body
    assert _json(response).get("error") != "owner_local_required"


def test_the_raw_upload_doors_refuse_another_device(runtime):
    from core.web.api.service import dispatch_upload

    for path in ("/api/chat/attachments/upload", "/api/session/bundle/upload", "/api/chat/import/upload"):
        response = dispatch_upload(path=path, raw_body=b"hello", runtime=runtime, client_host=LAN_PEER,
                                   headers={"X-VOOL-Session-Id": "openclaw:00000000000000000000",
                                            "X-VOOL-Attachment-Name": "note.txt"})
        assert response.status == 403, (path, response.body)


# ---- what a turn from another device may fetch -----------------------------------------------------


@pytest.fixture()
def local_web_server(monkeypatch):
    """A real HTTP server on this computer, standing for a local service (a model server, a browser
    debugging port, a development server). It records every request that reaches it. Web access is
    on, as the owner sets it in Settings."""
    import http.server
    import threading

    from core import policy_engine

    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)

    hits: list[str] = []

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            if self.path.startswith("/redirect-to/"):
                target = self.path[len("/redirect-to/"):].replace("|", "/")
                if "\r" in target or "\n" in target:
                    self.send_response(400)
                    self.end_headers()
                    return
                self.send_response(302)
                self.send_header("Location", target.replace("\r", "").replace("\n", ""))
                self.end_headers()
                return
            body = b"local service: owner-only data 7731"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], hits
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("target", ["http://127.0.0.1:{port}/", "http://localhost:{port}/", "http://[::1]:{port}/",
                                    "http://169.254.169.254/latest/meta-data/", "http://10.0.0.1:11434/api/tags",
                                    "http://192.168.1.1/", "http://printer.local/", "http://0.0.0.0:{port}/"])
def test_a_remote_turn_cannot_fetch_this_computer_or_its_network(target, local_web_server):
    from core.runtime_execution_tools import execute_runtime_tool

    port, hits = local_web_server
    url = target.format(port=port)
    result = execute_runtime_tool("web.fetch", {"url": url},
                                  source_context={OWNER_LOCAL_KEY: False, HTTP_INGRESS_KEY: True})
    assert result is not None and result.ok is False, url
    assert result.status == "private_target_refused", (url, result.status, result.response_text)
    assert "7731" not in result.response_text
    assert hits == [], "no request may reach a local service for a remote turn"


def test_the_operator_lane_fetch_refuses_the_same_targets_for_a_remote_turn(local_web_server):
    from tools.web.http_fetch import http_fetch_text

    port, hits = local_web_server
    refused = http_fetch_text(f"http://127.0.0.1:{port}/", timeout_s=2, public_only=True)
    assert refused["status"] == "private_target_refused" and "7731" not in refused["text"]
    assert hits == []


def test_a_redirect_into_the_local_network_is_refused_at_its_hop(local_web_server, monkeypatch):
    """The first hop is admitted (this test admits exactly the local server's address as if it were
    public, standing for a public site that redirects); the redirect's own target is checked again
    before its socket opens."""
    from core import remote_turn_fetch

    port, hits = local_web_server
    real_check = remote_turn_fetch._address_is_public
    monkeypatch.setattr(remote_turn_fetch, "_address_is_public", lambda a: a == "127.0.0.1" or real_check(a))
    from core.runtime_execution_tools import execute_runtime_tool

    for hop in ("http:||169.254.169.254|latest|meta-data", "http:||printer.local|", "http:||10.1.2.3:9222|json"):
        hits.clear()
        result = execute_runtime_tool("web.fetch", {"url": f"http://127.0.0.1:{port}/redirect-to/{hop}"},
                                      source_context={OWNER_LOCAL_KEY: False, HTTP_INGRESS_KEY: True})
        assert result is not None and result.status == "private_target_refused", (hop, result.response_text)
        assert hits == [f"/redirect-to/{hop}"], "only the admitted first hop is ever contacted"
    # an allowed redirect is followed: back to the admitted server
    hits.clear()
    remote_turn_fetch_result = execute_runtime_tool(
        "web.fetch", {"url": f"http://127.0.0.1:{port}/redirect-to/http:||127.0.0.1:{port}|page"},
        source_context={OWNER_LOCAL_KEY: False, HTTP_INGRESS_KEY: True})
    assert remote_turn_fetch_result.ok is True, remote_turn_fetch_result.response_text
    assert hits == [f"/redirect-to/http:||127.0.0.1:{port}|page", "/page"]


def test_the_owner_still_fetches_their_own_local_service(local_web_server):
    from core.runtime_execution_tools import execute_runtime_tool

    port, hits = local_web_server
    owner = execute_runtime_tool("web.fetch", {"url": f"http://127.0.0.1:{port}/"},
                                 source_context={OWNER_LOCAL_KEY: True, HTTP_INGRESS_KEY: True})
    assert owner is not None and owner.ok is True, owner.response_text
    assert hits == ["/"]
