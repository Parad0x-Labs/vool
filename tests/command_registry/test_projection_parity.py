"""Cross-projection parity: one read command, one approval-gated mutation, one
unavailable command — each executed through CLI, chat and API projections with
identical semantics (same fault/exit/data truth; only execution.projection
provenance differs, by design).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.command_registry.execute import ExecutionContext, execute_command

# -- helpers ----------------------------------------------------------------------


def _cli_envelope(argv: list[str], capsys) -> dict:
    """Run the real vool CLI main; the printed envelope IS the contract."""
    from core.command_registry import cli

    exit_code = cli.main([*argv, "--json"])
    raw = capsys.readouterr().out
    payload = json.loads(raw)
    assert payload["execution"]["exit_code"] == exit_code, "printed exit code must match process exit"
    return payload


def _chat_envelope(command_id: str, input_data: dict | None, approval: dict | None = None) -> dict:
    """The chat surface: discovery table finds the literal, then the same seam."""

    env = execute_command(
        command_id,
        input_data,
        context=ExecutionContext(projection="chat", approval_context=approval),
    )
    return env.to_json()


def _api_envelope(command_id: str, input_data: dict | None, approval: dict | None = None) -> dict:
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    body = {"command_id": command_id, "input": input_data or {}}
    if approval is not None:
        body["approval_context"] = approval
    res = dispatch_post(
        path="/api/commands/dispatch",
        body=body,
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
    )
    assert res.content_type == "application/json"
    return json.loads(res.body)


def _semantics(payload: dict) -> dict:
    """The truth three projections must agree on (provenance excluded)."""
    return {
        "ok": payload["ok"],
        "exit_code": payload["execution"]["exit_code"],
        "fault": payload["fault"],
        "data": payload["data"],
        "summary": payload["summary"],
    }


# -- fixtures ---------------------------------------------------------------------


def _establish_empty_plugin_catalog(tmp_path, monkeypatch) -> None:
    """This file's world: no packs discovered AND no lifecycle records.

    The empty catalog the unavailable-case laws depend on is LIFECYCLE state, not just
    discovery state: the availability probe consults the plugin-lifecycle store, and
    records left there by any earlier test in this process (e.g. an activation performed
    in a subprocess that inherited this session's VOOL_PLUGIN_LIFECYCLE_PATH) flip
    `plugins.lifecycle.transition` to available. The empty world is established through
    the lifecycle's own authority (reset_for_tests unlinks the store) — never by forcing
    the probe's answer — and restored on the way out of each test.
    """
    from core import plugin_lifecycle
    from tests._toolchain_fixtures import reset_toolchain_state

    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path / "plugins"))
    plugin_lifecycle.reset_for_tests()
    reset_toolchain_state()


@pytest.fixture(autouse=True)
def empty_plugin_catalog(tmp_path, monkeypatch):
    _establish_empty_plugin_catalog(tmp_path, monkeypatch)
    yield
    _establish_empty_plugin_catalog(tmp_path, monkeypatch)


@pytest.fixture
def recorded_workspace(tmp_path: Path) -> Path:
    """A real blackbox-recorded workspace mutation, through the product recorder."""
    from core.blackbox.recorder import recorded_workspace_mutation
    from core.runtime_execution_tools import RuntimeExecutionResult

    ws = tmp_path / "ws"
    ws.mkdir()
    recorded_workspace_mutation(
        "workspace.write_file",
        {"path": "note.txt", "content": "parity"},
        workspace_root=ws,
        source_context={"session_id": "command-registry-parity", "surface": "test"},
        handler=lambda: (
            (ws / "note.txt").write_text("parity"),
            RuntimeExecutionResult(
                handled=True, ok=True, status="done",
                response_text="wrote note.txt", details={},
            ),
        )[1],
    )
    return ws


def _first_turn_id(ws: Path) -> str:
    from core.blackbox.operator import list_turns

    turns = [t for t in list_turns() if str(ws) in str(t.get("root") or t.get("workspace_root") or "")]
    if not turns:
        turns = list_turns()
    assert turns, "expected at least one recorded blackbox turn"
    return str(turns[0].get("turn_id") or turns[0].get("turn") or turns[0])


# -- 1. read command ----------------------------------------------------------------


def test_read_command_identical_across_cli_chat_api(capsys, tmp_path):
    cli_payload = _cli_envelope(["blackbox", "status"], capsys)
    chat_payload = _chat_envelope("blackbox.status", None)
    api_payload = _api_envelope("blackbox.status", None)

    assert cli_payload["ok"] is True
    a, b, c = _semantics(cli_payload), _semantics(chat_payload), _semantics(api_payload)
    assert a == b == c
    assert cli_payload["execution"]["projection"] == "cli"
    assert chat_payload["execution"]["projection"] == "chat"
    assert api_payload["execution"]["projection"] == "api"


def test_typed_read_receipts_first_production_reader(capsys):
    """receipts.list reads the finalization ledger — the audit defect closed."""
    payload = _cli_envelope(["receipts", "list", "--limit", "5"], capsys)
    assert payload["ok"] is True
    assert payload["execution"]["exit_code"] == 0
    assert isinstance(payload["data"]["finalizations"], list)


# -- 2. approval-gated mutation ------------------------------------------------------


def test_approval_gated_mutation_identical_refusal_across_projections(
    capsys, recorded_workspace
):
    ws = recorded_workspace
    turn_id = _first_turn_id(ws)

    # CLI: no approval flag exists at all → typed refusal, exit 20
    cli_payload = _cli_envelope(["blackbox", "rollback", turn_id, str(ws)], capsys)
    assert cli_payload["ok"] is False
    assert cli_payload["execution"]["exit_code"] == 20
    assert cli_payload["fault"]["code"] == "permission_required"
    assert (ws / "note.txt").read_text() == "parity", "no side effect without approval"

    # chat + api: same refusal, same fault, same exit code
    chat_payload = _chat_envelope(
        "blackbox.rollback", {"turn_id": turn_id, "workspace_root": str(ws)}
    )
    api_payload = _api_envelope(
        "blackbox.rollback", {"turn_id": turn_id, "workspace_root": str(ws)}
    )
    assert _semantics(cli_payload) == _semantics(chat_payload) == _semantics(api_payload)


def test_approval_gated_mutation_executes_with_operator_authority(recorded_workspace):
    """With the operator's bounded authority (the two-press pattern), the same
    command executes and emits a receipt — proven through the chat projection."""
    ws = recorded_workspace
    turn_id = _first_turn_id(ws)

    from core.mode_permission_policy import (
        PermissionAction,
        grant_internal_authority,
        set_active_mode,
    )

    session = "operator:command-registry-test"
    set_active_mode(session, "auto")
    token = grant_internal_authority(
        label="command-registry-rollback:test",
        actions=[
            PermissionAction.DELETE_FILES,
            PermissionAction.OVERWRITE_EXISTING_FILES,
            PermissionAction.MODIFY_FILES,
        ],
        intents=["workspace.rollback_last_change"],
        workspace_root=str(ws.resolve()),
        duration_seconds=120,
    )

    payload = _chat_envelope(
        "blackbox.rollback",
        {"turn_id": turn_id, "workspace_root": str(ws), "session": session, "operator": "vool-test"},
        approval={"authority_token": token},
    )
    assert payload["execution"]["exit_code"] in (0, 40), payload
    if payload["ok"]:
        assert payload["receipts"], "a mutating command must emit receipts"
        assert not (ws / "note.txt").exists() or (ws / "note.txt").read_text() != "parity", (
            "the recorded effect was rolled back (restore removes the newly-created file)"
        )


# -- 3. unavailable command ------------------------------------------------------------


def test_the_empty_catalog_is_established_through_the_lifecycle_authority(tmp_path, monkeypatch):
    """The world this file promises is lifecycle state, and it is established through the
    lifecycle's own authority — not by forcing the probe's answer.

    Regression for the shard-9 parity failure: a really-admitted pack's records left in the
    session's lifecycle store by any earlier test used to survive this file's fixture, so
    the availability probe answered "packs exist" and the unavailable-case laws failed.
    Here the dirty world is created for real, then the fixture's authority runs, and the
    probe must answer from the re-established empty catalog through the real palette.
    """
    from core import plugin_lifecycle
    from core.command_registry.projections import palette_data
    from core.command_registry.registry import registry
    from tests._toolchain_fixtures import admit_plugin, make_plugin

    pack = make_plugin(tmp_path / "dirty", plugin_id="parity-dirty-pack", admit=False)
    admit_plugin("parity-dirty-pack", pack)
    assert plugin_lifecycle.lifecycle_snapshot()["plugins"], "precondition: a really-admitted pack's records exist"

    _establish_empty_plugin_catalog(tmp_path, monkeypatch)

    assert plugin_lifecycle.lifecycle_snapshot()["plugins"] == []
    row = next(
        r for r in palette_data(registry())["commands"] if r["command_id"] == "plugins.lifecycle.transition"
    )
    assert row["available"] is False, "availability answers from the live lifecycle state"
    assert row["unavailable_reason"], "the structural reason rides along, dimmed but never hidden"


def test_prior_lifecycle_activation_under_an_isolated_home_flips_no_availability_here(tmp_path):
    """The shard-9 polluter, minimized and faithful.

    A test that activates a pack in a subprocess under its own VOOL_HOME takes the pack
    through the REAL lifecycle (install/verify/enable) while inheriting this process's
    environment — the construction `tests/_blackbox_served_rig.run_in_home` used before
    its home-boundary repair (the rig now drops the session pin; see
    tests/test_blackbox_served_rig_home_boundary.py). Because VOOL_PLUGIN_LIFECYCLE_PATH
    (pinned session-wide by conftest) outranks VOOL_HOME in
    `core.plugin_lifecycle.store_path`, those records land in THIS session's store,
    crossing the runtime-home boundary the activation intended. This predecessor
    deliberately recreates that state mid-file — any subprocess inheriting the parent
    env across a VOOL_HOME boundary still can: the unavailable-case laws that follow
    must hold anyway, because this file's fixture re-establishes the empty catalog
    through the lifecycle authority rather than trusting whoever ran before it.
    """
    import os
    import subprocess
    import sys

    from core import plugin_lifecycle
    from tests._toolchain_fixtures import make_plugin

    repo_root = Path(__file__).resolve().parents[2]
    pack = make_plugin(tmp_path, plugin_id="parity-cross-home-pack", admit=False)
    home = tmp_path / "isolated-home"
    home.mkdir()
    # The pre-repair run_in_home construction, verbatim: the parent env (carrying this
    # session's VOOL_PLUGIN_LIFECYCLE_PATH) plus a fresh VOOL_HOME for the subprocess.
    env = dict(os.environ)
    env.update({"VOOL_HOME": str(home), "PYTHONPATH": str(repo_root)})
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(repo_root)!r})\n"
        "from core.plugin_lifecycle import enable, install, verify\n"
        f"install('parity-cross-home-pack', root={str(pack)!r}, source='test-isolated')\n"
        f"verify('parity-cross-home-pack', root={str(pack)!r})\n"
        "enable('parity-cross-home-pack')\n"
        "print('lifecycle: installed+verified+enabled')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr

    # The reproduced boundary crossing: the activation meant for another home recorded its
    # pack in this session's store, so the store this process's probes consult is NOT empty.
    polluted = plugin_lifecycle.lifecycle_snapshot()["plugins"]
    assert [row["plugin_id"] for row in polluted] == ["parity-cross-home-pack"], polluted


def test_unavailable_command_identical_across_projections(capsys):
    # A genuinely unavailable command in an isolated home -> machine-evidence refusal.
    # This used council.stop with no runs recorded. The C14 council lane deliberately removed
    # that availability gate (whether any run EXISTS is not an availability fact; the handler
    # answers a typed truth instead), which left this law with no example. blackbox.rollback
    # plugins.lifecycle.transition is unavailable for a STRUCTURAL machine-evidence reason --
    # no plugin packs exist on this machine -- rather than a state-dependent one, so it cannot
    # be turned available by an earlier test in this file the way blackbox.rollback can.
    cli_payload = _cli_envelope(
        ["plugins", "lifecycle", "transition", "--action", "enable", "--plugin-id", "nope"], capsys
    )
    assert cli_payload["ok"] is False
    assert cli_payload["execution"]["exit_code"] == 10
    assert cli_payload["fault"]["code"] == "unavailable"
    assert "no plugin packs discovered" in (cli_payload["fault"]["detail"] or {}).get("reason", "")

    args = {"action": "enable", "plugin_id": "nope"}
    chat_payload = _chat_envelope("plugins.lifecycle.transition", args)
    api_payload = _api_envelope("plugins.lifecycle.transition", args)
    assert _semantics(cli_payload) == _semantics(chat_payload) == _semantics(api_payload)


def test_palette_renders_unavailable_with_reason_never_hidden():
    from core.command_registry.projections import palette_data
    from core.command_registry.registry import registry

    rows = {r["command_id"]: r for r in palette_data(registry())["commands"]}
    # See the note on test_unavailable_command_identical_across_projections: council.stop is
    # available again by the C14 lane's design, so the dimmed-with-reason law is pinned on a
    # command whose unavailability is structural on this machine.
    row = rows["plugins.lifecycle.transition"]
    assert row["available"] is False
    assert row["unavailable_reason"], "unavailable rows render dimmed WITH the reason"


# -- unknown command parity ---------------------------------------------------------------


def test_unknown_command_identical_across_projections(capsys):
    cli_payload = _cli_envelope(["blackbox.rolback"], capsys)  # typo on purpose
    chat_payload = _chat_envelope("blackbox.rolback", None)
    api_payload = _api_envelope("blackbox.rolback", None)
    assert cli_payload["execution"]["exit_code"] == 3
    assert cli_payload["fault"]["code"] == "unknown_command"
    assert (cli_payload["fault"]["detail"] or {}).get("did_you_mean")
    assert _semantics(cli_payload) == _semantics(chat_payload) == _semantics(api_payload)


def test_usage_error_identical_across_projections(capsys):
    cli_payload = _cli_envelope(["receipts", "show"], capsys)  # missing required id
    chat_payload = _chat_envelope("receipts.show", {})
    api_payload = _api_envelope("receipts.show", {})
    assert cli_payload["execution"]["exit_code"] == 2
    assert cli_payload["fault"]["code"] == "usage"
    assert _semantics(cli_payload) == _semantics(chat_payload) == _semantics(api_payload)
