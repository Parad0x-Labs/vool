"""CLI contract (vool commands --json / --check / help / dispatch) and the API
seam in service.py. Mutation checks: breaking the projection seam or the
registry law must fail loudly, never silently.
"""
from __future__ import annotations

import json


def _run(argv: list[str], capsys) -> tuple[int, str]:
    from core.command_registry import cli

    code = cli.main(argv)
    return code, capsys.readouterr().out


# -- vool commands ------------------------------------------------------------------


def test_commands_json_prints_exactly_the_envelope_free_payload(capsys):
    code, out = _run(["commands", "--json"], capsys)
    assert code == 0
    payload = json.loads(out)  # exactly one JSON document, no banners
    assert {"commands", "groups"} <= set(payload.keys())
    ids = [c["command_id"] for c in payload["commands"]]
    assert len(ids) == len(set(ids)), "no duplicated ids in the listing"


def test_commands_check_exits_zero_and_reports(capsys):
    code, out = _run(["commands", "--check"], capsys)
    assert code == 0
    assert "Command registry check passed" in out
    assert "duplicated: 0" in out and "orphaned: 0" in out
    assert "unbound: 0" in out and "contract_divergent: 0" in out


def test_commands_check_fails_when_a_command_goes_bad(capsys, monkeypatch):
    """Mutation check: corrupt one live spec → --check exits 1 naming it."""
    from core.command_registry import registry as get_registry

    get_registry()  # force registration
    import core.command_registry.groups.meta as meta

    original = meta.register

    def sabotaged(reg):
        import dataclasses

        original(reg)
        reg._commands["commands.check"] = dataclasses.replace(
            reg.lookup("commands.check"), description="  "
        )

    monkeypatch.setattr(meta, "register", sabotaged)
    import sys

    # NB: the package re-exports `registry` (the function), shadowing the module —
    # fetch the real module object from sys.modules to reset its global.
    reg_module = sys.modules["core.command_registry.registry"]

    reg_module._REGISTRY = None  # force re-registration with the sabotage

    code, out = _run(["commands", "--check"], capsys)
    assert code == 1
    assert "empty description" in out
    reg_module._REGISTRY = None  # clean up: next access re-registers honestly


def test_bare_vool_prints_help_with_every_group(capsys):
    code, out = _run([], capsys)
    assert code == 0
    for group in ("commands", "receipts", "blackbox", "models", "council", "approvals", "update", "faults", "tasks"):
        assert group in out


def test_cli_exit_code_is_the_envelope_exit_code(capsys):
    code, _ = _run(["receipts.show"], capsys)  # usage fault
    assert code == 2


# -- API seam in service.py ------------------------------------------------------------


def _rt():
    from core.web.api.runtime import RuntimeServices

    return RuntimeServices(display_name="VOOL")


def _get(path: str):
    from core.web.api.service import dispatch_get

    res = dispatch_get(path=path, query={}, runtime=_rt(), model_name="vool")
    return res.status, json.loads(res.body)


def test_api_commands_snapshot_route():
    status, payload = _get("/api/commands")
    assert status == 200
    assert len(payload["commands"]) >= 20
    # live availability truth is part of the snapshot
    assert all("available" in c for c in payload["commands"])


def test_api_palette_and_chat_and_schema_routes():
    status, palette = _get("/api/commands/palette")
    assert status == 200 and palette["surface"] == "palette"
    status, chat = _get("/api/commands/chat")
    assert status == 200 and chat["surface"] == "chat"
    status, schema = _get("/api/commands/schema")
    assert status == 200 and schema["dispatch"]["path"] == "/api/commands/dispatch"


def test_api_dispatch_unknown_command_is_404_with_envelope():
    status, _ = _get("/api/commands/does-not-exist")
    assert status == 404


def test_api_dispatch_mutating_without_approval_is_refused(tmp_path):
    """S4 parity: loopback POST for a destructive command without approval
    context → 403 + permission_required. Owner-local is not authorization."""
    from core.blackbox.recorder import recorded_workspace_mutation
    from core.runtime_execution_tools import RuntimeExecutionResult
    from core.web.api.service import dispatch_post

    ws = tmp_path / "ws"
    ws.mkdir()
    recorded_workspace_mutation(
        "workspace.write_file",
        {"path": "note.txt", "content": "s4"},
        workspace_root=ws,
        source_context={"session_id": "s4-parity", "surface": "test"},
        handler=lambda: (
            (ws / "note.txt").write_text("s4"),
            RuntimeExecutionResult(
                handled=True, ok=True, status="done",
                response_text="wrote note.txt", details={},
            ),
        )[1],
    )
    from core.blackbox.operator import list_turns

    turns = list_turns()
    assert turns, "fixture must record a turn"

    res = dispatch_post(
        path="/api/commands/dispatch",
        body={
            "command_id": "blackbox.rollback",
            "input": {"turn_id": str(turns[0].get("turn_id") or turns[0]), "workspace_root": str(ws)},
        },
        headers={"content-type": "application/json"},
        runtime=_rt(),
        model_name="vool",
        workspace_root_provider=lambda: str(ws),
    )
    payload = json.loads(res.body)
    assert res.status == 403
    assert payload["fault"]["code"] == "permission_required"
    assert (ws / "note.txt").read_text() == "s4", "no side effect without approval"


def test_api_dispatch_bad_body_is_typed_400():
    from core.web.api.service import dispatch_post

    res = dispatch_post(
        path="/api/commands/dispatch",
        body={"input": {"x": 1}},  # no command_id
        headers={"content-type": "application/json"},
        runtime=_rt(),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
    )
    assert res.status == 400
    assert json.loads(res.body)["fault"]["code"] == "usage"
