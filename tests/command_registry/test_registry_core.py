"""Registry core: registration contracts, --check failure classes, projection
generation, envelope/exit-code laws. The scratch-registry sabotage tests are the
mutation checks: each tampers one declaration and asserts --check names it.
"""
from __future__ import annotations

import dataclasses

import pytest

from core.command_registry.check import check_registry
from core.command_registry.registry import CommandRegistry, RegistryError
from core.command_registry.spec import (
    ApprovalGate,
    Availability,
    CommandSpec,
    GroupSpec,
    Handler,
    NextAction,
    OpenRead,
)


@pytest.fixture(scope="module")
def full_registry():
    from core.command_registry.registry import registry

    return registry()


def _scratch() -> CommandRegistry:
    reg = CommandRegistry()
    reg.add_group(GroupSpec(group_id="demo", description="demo group"))
    reg.add(
        CommandSpec(
            command_id="demo.read",
            group="demo",
            description="a read",
            input_schema=dataclasses.make_dataclass("In", [("x", int)]),
            output_schema=dataclasses.make_dataclass("Out", [("y", int)]),
            handler=Handler("core.command_registry.groups.meta:_handle_commands_check"),
            next_actions=(NextAction(command_id="demo.read", label="self"),),
        )
    )
    reg.add(
        CommandSpec(
            command_id="demo.write",
            group="demo",
            description="a write",
            effects="mutating",
            input_schema=dataclasses.make_dataclass("In2", [("path", str)]),
            permission=ApprovalGate(
                kind="demo.write",
                verifier="core.command_registry.groups.blackbox_group:_gate_rollback_approval",
            ),
            handler=Handler("core.command_registry.groups.meta:_handle_commands_check"),
            availability=Availability("core.command_registry.groups.blackbox_group:_probe_store_healthy"),
        )
    )
    return reg


# -- registration-time collision ------------------------------------------------


def test_duplicate_command_id_is_a_registration_error():
    reg = _scratch()
    with pytest.raises(RegistryError, match="duplicate command id"):
        reg.add(CommandSpec(command_id="demo.read", group="demo", description="again"))


def test_duplicate_surface_is_a_registration_error():
    reg = _scratch()
    with pytest.raises(RegistryError, match="route collision"):
        reg.add(CommandSpec(command_id="demo.other", group="demo", description="thief", aliases=("demo.read",)))


# -- --check failure classes (mutation checks) ----------------------------------


def test_check_passes_on_a_clean_scratch():
    assert check_registry(_scratch()).ok


def test_check_fails_orphaned_next_action():
    reg = _scratch()
    spec = reg.lookup("demo.read")
    broken = dataclasses.replace(
        spec, next_actions=(NextAction(command_id="ghost.command", label="nowhere"),)
    )
    reg._commands["demo.read"] = broken
    report = check_registry(reg)
    kinds = [f.kind for f in report.findings]
    assert "orphaned" in kinds
    assert any("ghost.command" in f.detail for f in report.findings)


def test_check_fails_unbound_handler():
    reg = _scratch()
    reg._commands["demo.read"] = dataclasses.replace(
        reg.lookup("demo.read"), handler=Handler("core.no.such_module:nope")
    )
    report = check_registry(reg)
    assert any(f.kind == "unbound" and f.command_id == "demo.read" for f in report.findings)


def test_check_fails_unbound_missing_handler():
    reg = _scratch()
    reg._commands["demo.read"] = dataclasses.replace(reg.lookup("demo.read"), handler=None)
    report = check_registry(reg)
    assert any(f.kind == "unbound" and "no handler bound" in f.detail for f in report.findings)


def test_check_fails_contract_divergent_raw_dict_schema():
    reg = _scratch()
    reg._commands["demo.read"] = dataclasses.replace(reg.lookup("demo.read"), input_schema=dict)
    report = check_registry(reg)
    assert any(
        f.kind == "contract_divergent" and "input_schema" in f.detail and "typed dataclass" in f.detail
        for f in report.findings
    )


def test_check_fails_contract_divergent_mutating_without_gate():
    reg = _scratch()
    reg._commands["demo.write"] = dataclasses.replace(
        reg.lookup("demo.write"),
        permission=OpenRead(),
        availability=None,
    )
    report = check_registry(reg)
    details = [f.detail for f in report.findings if f.command_id == "demo.write"]
    assert any("gate required" in d for d in details)
    assert any("availability evidence" in d for d in details)


def test_check_fails_contract_divergent_empty_description():
    reg = _scratch()
    reg._commands["demo.read"] = dataclasses.replace(reg.lookup("demo.read"), description="  ")
    report = check_registry(reg)
    assert any(f.kind == "contract_divergent" and "empty description" in f.detail for f in report.findings)


def test_check_fails_contract_divergent_vocabulary():
    reg = _scratch()
    reg._commands["demo.read"] = dataclasses.replace(reg.lookup("demo.read"), effects="kinda_writes")
    report = check_registry(reg)
    assert any("outside vocabulary" in f.detail for f in report.findings)


# -- the real registry ------------------------------------------------------------


def test_real_registry_check_passes(full_registry):
    report = check_registry(full_registry)
    assert report.ok, [f"{f.kind}: {f.command_id}: {f.detail}" for f in report.findings]


def test_milestone_groups_present(full_registry):
    groups = {g.group_id for g in full_registry.groups()}
    required = {
        "commands",
        "receipts",
        "blackbox",
        "models",
        "council",
        "approvals",
        "update",
        "faults",
        "tasks",
    }
    assert required <= groups


def test_every_milestone_command_bound(full_registry):
    for spec in full_registry.commands():
        assert spec.handler is not None, spec.command_id
        assert spec.description.strip(), spec.command_id
        if spec.effects != "read_only":
            assert not isinstance(spec.permission, OpenRead), spec.command_id
            assert spec.availability is not None, spec.command_id


def test_handlers_resolve_to_real_authorities(full_registry):
    from core.command_registry.registry import resolve_dotted

    for spec in full_registry.commands():
        fn = resolve_dotted(spec.handler.dotted)
        assert callable(fn), spec.command_id


# -- projections are generated, not hand-listed -----------------------------------


def test_commands_json_shape(full_registry):
    from core.command_registry.projections import commands_json

    payload = commands_json(full_registry)
    assert payload["commands"] and payload["groups"]
    row = payload["commands"][0]
    for key in (
        "command_id",
        "aliases",
        "group",
        "description",
        "effects",
        "permission",
        "capabilities",
        "lifecycle",
        "platforms",
        "input_schema",
        "output_schema",
        "exit_codes",
        "handler",
    ):
        assert key in row


def test_cli_help_lists_every_command(full_registry):
    from core.command_registry.projections import cli_help

    text = cli_help(full_registry)
    for spec in full_registry.commands():
        assert spec.command_id in text
        assert spec.description in text


def test_chat_discovery_and_palette_cover_the_registry(full_registry):
    from core.command_registry.projections import chat_discovery, palette_data

    chat = chat_discovery(full_registry)
    palette = palette_data(full_registry)
    ids = {s.command_id for s in full_registry.commands()}
    assert {r["literal"] for r in chat["commands"]} == ids
    assert {r["command_id"] for r in palette["commands"]} == ids
    for row in palette["commands"]:
        assert "available" in row and "unavailable_reason" in row


def test_api_schema_declares_dispatch_contract(full_registry):
    from core.command_registry.projections import api_schema

    schema = api_schema(full_registry)
    assert schema["dispatch"]["path"] == "/api/commands/dispatch"
    by_id = {c["command_id"]: c for c in schema["commands"]}
    assert by_id["blackbox.rollback"]["effects"] == "destructive"
    assert by_id["blackbox.rollback"]["permission"].startswith("approval:")


# -- envelope laws -----------------------------------------------------------------


def test_ok_iff_exit_zero(full_registry):
    from core.command_registry.execute import ExecutionContext, execute_command

    env = execute_command("blackbox.status", context=ExecutionContext())
    assert env.ok is True
    assert env.execution.exit_code == 0
    env2 = execute_command("no.such.command", context=ExecutionContext())
    assert env2.ok is False
    assert env2.execution.exit_code != 0
