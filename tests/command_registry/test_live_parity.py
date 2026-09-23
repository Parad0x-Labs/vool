"""Live four-surface parity: installed-package CLI, REAL HTTP server, the chat
palette projection, and the model-offered tool — one command, one truth.

The HTTP round-trip runs against a REAL socket (BaseHTTPRequestHandler serving
the product's own dispatch_get/dispatch_post), not an in-process function call.
"""
from __future__ import annotations

import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture(scope="module")
def live_server():
    """A real HTTP server on 127.0.0.1 serving the product's own dispatchers."""
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_get, dispatch_post

    class Handler(BaseHTTPRequestHandler):
        def _cors(self): pass

        def do_GET(self):
            res = dispatch_get(
                path=self.path, query={}, runtime=RuntimeServices(display_name="VOOL"), model_name="vool"
            )
            self.send_response(res.status)
            self.send_header("content-type", res.content_type)
            self.end_headers()
            self.wfile.write(res.body or b"{}")

        def do_POST(self):
            length = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            res = dispatch_post(
                path=self.path,
                body=body,
                headers={k: v for k, v in self.headers.items()},
                runtime=RuntimeServices(display_name="VOOL"),
                model_name="vool",
                workspace_root_provider=lambda: "/tmp",
                client_host=self.client_address[0],
            )
            self.send_response(res.status)
            self.send_header("content-type", res.content_type)
            self.end_headers()
            self.wfile.write(res.body or b"{}")

        def log_message(self, *args): pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _http_json(url: str, payload: dict | None = None) -> tuple[int, dict]:
    if payload is None:
        with urllib.request.urlopen(url) as response:
            return response.status, json.loads(response.read())
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"content-type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _semantics(payload: dict) -> dict:
    return {
        "ok": payload["ok"],
        "exit_code": payload["execution"]["exit_code"],
        "fault": payload["fault"],
        "summary": payload["summary"],
    }


def test_read_command_parity_across_cli_http_palette_and_model(live_server):
    """blackbox.status: the SAME envelope truth through four surfaces."""
    from core.command_registry.cli import main as cli_main
    from core.command_registry.model_tools import execute_model_command
    from core.command_registry.projections import palette_data

    base = live_server

    # 1. the REAL HTTP dispatch route
    http_status, http_payload = _http_json(
        base + "/api/commands/dispatch", {"command_id": "blackbox.status"}
    )
    assert http_status == 200 and http_payload["ok"] is True

    # 2. the palette's data source (the palette JS fetches exactly this projection)
    from core.command_registry.registry import registry

    palette_rows = {r["command_id"]: r for r in palette_data(registry())["commands"]}
    assert palette_rows["blackbox.status"]["available"] is True

    # 3. the model-offered tool (operator.command.blackbox.status)
    model_result = execute_model_command("operator.command.blackbox.status", {})
    assert model_result.ok is True
    assert model_result.details["envelope"]["data"]["store"] == http_payload["data"]["store"]

    # 4. the CLI seam (same function the installed `vool` script binds)
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exit_code = cli_main(["blackbox", "status", "--json"])
    cli_payload = json.loads(buffer.getvalue())
    assert exit_code == 0 == http_payload["execution"]["exit_code"]
    assert _semantics(cli_payload) == _semantics(http_payload)


def test_denied_mutation_parity_cli_http_and_unavailable(live_server, monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "empty-plugin-lifecycle.json"))
    base = live_server
    import contextlib
    import io

    from core.command_registry.cli import main as cli_main

    # denied mutation (update.apply without the press) — CLI vs real HTTP
    with contextlib.redirect_stdout(io.StringIO()) as buffer:
        cli_exit = cli_main(["update", "apply", "--json"])
    cli_payload = json.loads(buffer.getvalue())
    http_status, http_payload = _http_json(
        base + "/api/commands/dispatch", {"command_id": "update.apply", "input": {}}
    )
    assert cli_exit == 20
    assert cli_payload["fault"]["code"] == "permission_required"
    assert http_status == 403
    assert http_payload["fault"]["code"] == "permission_required"
    assert _semantics(cli_payload) == _semantics(http_payload)

    # unavailable — CLI vs real HTTP. This used council.stop with no runs recorded; the C14
    # council lane deliberately dropped that availability gate (whether a run EXISTS is not an
    # availability fact — the handler answers a typed truth), so council.stop is available now
    # and stopped exercising this law. plugins.lifecycle.transition is unavailable for a
    # STRUCTURAL reason on this machine (no plugin packs discovered), so the law keeps a case.
    with contextlib.redirect_stdout(io.StringIO()) as buffer:
        cli_exit = cli_main(
            ["plugins", "lifecycle", "transition", "--action", "enable", "--plugin-id", "nope", "--json"]
        )
    cli_payload = json.loads(buffer.getvalue())
    http_status, http_payload = _http_json(
        base + "/api/commands/dispatch",
        {"command_id": "plugins.lifecycle.transition", "input": {"action": "enable", "plugin_id": "nope"}},
    )
    assert cli_exit == 10
    assert cli_payload["fault"]["code"] == "unavailable"
    assert http_status == 409
    assert http_payload["fault"]["code"] == "unavailable"
    assert _semantics(cli_payload) == _semantics(http_payload)


def test_palette_route_served_over_real_http(live_server, monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_PLUGIN_LIFECYCLE_PATH", str(tmp_path / "empty-plugin-lifecycle.json"))
    status, payload = _http_json(live_server + "/api/commands/palette")
    assert status == 200
    assert payload["surface"] == "palette"
    assert len(payload["commands"]) >= 40
    # availability truth rides along (unavailable rows carry the reason)
    # see the note above: council.stop is available by design now, so the availability-truth
    # law is pinned on a structurally unavailable command instead.
    stop_row = next(
        r for r in payload["commands"] if r["command_id"] == "plugins.lifecycle.transition"
    )
    assert stop_row["available"] is False and stop_row["unavailable_reason"]


def test_registry_route_traverses_the_authority_exactly_once(live_server, monkeypatch):
    """Exactly-once law: a mutated command through the real dispatch route calls
    the owning authority handler exactly once (no legacy double-dispatch)."""
    from core.command_registry.registry import registry, reset_registry

    reset_registry()
    reg = registry()
    calls = []
    original = reg.lookup("blackbox.status")

    import dataclasses

    from core.command_registry.spec import Handler, HandlerOk

    def counting_handler(inp, ctx):
        calls.append(1)
        return HandlerOk(data={"store": "counted"}, summary="counted")

    reg._commands["blackbox.status"] = dataclasses.replace(
        original, handler=Handler("core.command_registry.groups.convergence:_counting_probe")
    )
    import core.command_registry.groups.convergence as conv

    monkeypatch.setattr(conv, "_counting_probe", counting_handler, raising=False)

    status, payload = _http_json(live_server + "/api/commands/dispatch", {"command_id": "blackbox.status"})
    assert status == 200
    assert payload["data"]["store"] == "counted"
    assert len(calls) == 1, "the authority handler must run EXACTLY once per dispatch"
    reset_registry()

