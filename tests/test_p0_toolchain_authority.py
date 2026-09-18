"""P0 — one authority for the toolbelt: registry declares, graph projects, census counts.

RED at 96c2fb96 (measured before any fix in this lane):

- the registry had no epoch, so the graph could not tell a stale index from a fresh one and a
  tool registered after boot stayed invisible until someone called ``refresh_from_registry``;
- MCP tools entered the graph through a side channel (spec fingerprint), never the registry, so
  the permission layer, the claim binder and the census could not see a contract for them;
- the prompt-catalog seam (``_tool_intent_catalog_text``) took only a family hint, so a local
  model read a fixed 8-seat family set whatever the user's words asked for;
- there was no census: nothing could say, per intent, whether it was declared, registered,
  selectable (or why not), permitted, executable, executed and receipted.

Each test names the desired behaviour and holds after the fix; the sabotage pack proves the
mechanisms are load-bearing.
"""
from __future__ import annotations

import json

import pytest

from tests._toolchain_fixtures import (
    make_plugin,
    read_only_pin,
    reset_toolchain_state,
    write_mcp_config,
)


def _intents(specs) -> set[str]:
    return {str(s.get("intent") or "") for s in specs}


@pytest.fixture(autouse=True)
def _clean_world(monkeypatch):
    from core.runtime_flags import override

    monkeypatch.delenv("VOOL_MCP_CONFIG", raising=False)
    monkeypatch.setenv("VOOL_PLUGINS_DIR", "/nonexistent/toolchain-authority")
    reset_toolchain_state()
    with override("plugin_runtime_tools", True):
        yield
    reset_toolchain_state()


def _probe_contract(intent: str = "probe.read"):
    from core.runtime_tool_contracts import RuntimeToolContract

    return RuntimeToolContract(
        intent=intent,
        description="Probe contract for the registry epoch tests.",
        tool_surface="plugin",
        capability_id="plugin.probe",
        capability_claim="probe",
        supported=True,
        unsupported_reason="",
        input_schema={},
        output_schema={},
        side_effect_class="read_only",
        approval_requirement="none",
        timeout_policy="plugin_declared",
        retry_policy="none",
        artifact_emission="none",
        error_contract="returns_structured_error_result",
        handler=json.dumps({"kind": "subprocess", "entry": "bin/run"}),
        source="plugin:probe",
    )


# ---------------------------------------------------------------------------
# Registry is the declaration authority; the graph is its epoch-indexed projection
# ---------------------------------------------------------------------------


def test_registry_epoch_advances_on_registration() -> None:
    from core import tool_registry

    before = tool_registry.registry_epoch()
    tool_registry.register(_probe_contract())
    after_register = tool_registry.registry_epoch()
    assert after_register > before
    tool_registry.unregister("probe.read")
    assert tool_registry.registry_epoch() > after_register


def test_graph_reindexes_on_registry_epoch_change_without_manual_refresh() -> None:
    from core import capability_graph, tool_registry
    from core.capability_graph import ImplementationId, ensure_registry_bootstrap

    ensure_registry_bootstrap()
    assert ImplementationId("probe.read") not in {i.id for i in capability_graph.all_implementations()}
    tool_registry.register(_probe_contract())
    # No refresh_from_registry() call: the projection follows the registry epoch on its own.
    ensure_registry_bootstrap()
    rows = {i.id: i for i in capability_graph.all_implementations()}
    assert ImplementationId("probe.read") in rows
    assert rows[ImplementationId("probe.read")].source == "plugin:probe"
    assert capability_graph.indexed_registry_epoch() == tool_registry.registry_epoch()


def test_unregistered_tool_leaves_the_graph() -> None:
    from core import capability_graph, tool_registry
    from core.capability_graph import ImplementationId, ensure_registry_bootstrap

    tool_registry.register(_probe_contract())
    ensure_registry_bootstrap()
    assert ImplementationId("probe.read") in {i.id for i in capability_graph.all_implementations()}
    tool_registry.unregister("probe.read")
    ensure_registry_bootstrap()
    assert ImplementationId("probe.read") not in {i.id for i in capability_graph.all_implementations()}
    assert capability_graph.capability_for_intent("probe.read") is None


# ---------------------------------------------------------------------------
# MCP tools are registry contracts, not a side channel into the graph
# ---------------------------------------------------------------------------


def test_mcp_tools_are_registry_contracts(tmp_path, monkeypatch) -> None:
    from core.execution import mcp_bridge
    from core.tool_registry import tool_for_intent

    write_mcp_config(tmp_path, monkeypatch, trust=read_only_pin("echo"))
    mcp_bridge.reset_mcp_state()
    mcp_bridge.mcp_tool_specs()
    contract = tool_for_intent("mcp.stub.echo")
    assert contract is not None
    assert contract.source == "mcp:stub"
    assert contract.tool_surface == "mcp"
    assert contract.side_effect_class == "read_only"
    assert tuple(contract.permission_actions) == ("read_files",)
    unpinned = tool_for_intent("mcp.stub.add")
    assert unpinned is not None
    assert unpinned.side_effect_class == mcp_bridge.UNPINNED_SIDE_EFFECT_CLASS
    assert tuple(unpinned.permission_actions) == ()


def test_mcp_rows_in_graph_are_projected_from_the_registry(tmp_path, monkeypatch) -> None:
    from core import capability_graph
    from core.capability_graph import ImplementationId, model_visible_specs
    from core.tool_registry import tool_for_intent

    write_mcp_config(tmp_path, monkeypatch, trust=read_only_pin("echo"))
    reset_toolchain_state()
    specs = model_visible_specs(family_hint="mcp")
    assert "mcp.stub.echo" in _intents(specs)
    rows = {i.id: i for i in capability_graph.all_implementations()}
    row = rows[ImplementationId("mcp.stub.echo")]
    assert row.source == "mcp:stub"
    assert row.read_only is True  # projected from the pinned contract, not a hard-coded False
    assert tool_for_intent("mcp.stub.echo") is not None


# ---------------------------------------------------------------------------
# Every supported contract is selectable, or carries a typed reason
# ---------------------------------------------------------------------------


def test_every_supported_contract_is_selectable_or_typed(tmp_path, monkeypatch) -> None:
    from core.capability_census import NotModelFacingReason, capability_census

    make_plugin(tmp_path)
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    write_mcp_config(tmp_path, monkeypatch, trust=read_only_pin("echo"))
    reset_toolchain_state()
    census = capability_census(mode="manual")
    problems = []
    for row in census.rows:
        if not row.registered:
            continue
        if row.selectable:
            assert row.offer_paths, f"{row.intent}: selectable without a path"
            continue
        if row.not_model_facing_reason not in {r.value for r in NotModelFacingReason}:
            problems.append(f"{row.intent}: reason {row.not_model_facing_reason!r} is not typed")
    assert not problems, problems
    summary = census.summary()
    assert summary["registered"] == summary["selectable"] + summary["not_model_facing"]
    # The four surfaces all reach the selectable stage.
    by_intent = {row.intent: row for row in census.rows}
    assert by_intent["workspace.read_file"].selectable
    assert by_intent["pack.echo"].selectable
    assert by_intent["mcp.stub.echo"].selectable
    assert by_intent["email.send"].not_model_facing_reason == NotModelFacingReason.POLICY_DISABLED.value


def test_census_stages_are_monotone_and_named() -> None:
    from core.capability_census import STAGES, capability_census, render_census

    census = capability_census(mode="manual")
    assert STAGES == ("declared", "registered", "selectable", "permitted", "executable", "executed", "receipted")
    summary = census.summary()
    assert summary["declared"] >= summary["registered"] >= summary["selectable"] >= summary["permitted"]
    assert summary["permitted"] >= summary["executable"] >= summary["executed"] >= summary["receipted"]
    assert summary["declared"] >= 72  # the contracted surface, never the eight operator tools
    text = render_census(census)
    for stage in STAGES:
        assert stage in text


def test_census_counts_execution_and_receipts_for_a_session(tmp_path, monkeypatch) -> None:
    from core import execution_records
    from core.capability_census import capability_census
    from core.mode_permission_policy import reset_mode_permission_state
    from core.tool_intent_executor import execute_tool_intent
    from tests._toolchain_fixtures import executor_kwargs

    reset_mode_permission_state()
    session = "census-session"
    execution_records.clear(session)
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws" / "README.md").write_text("hello census\n", encoding="utf-8")
    out = execute_tool_intent(
        {"intent": "workspace.read_file", "arguments": {"path": "README.md"}},
        **executor_kwargs(session, workspace=str(tmp_path / "ws"), workspace_root=str(tmp_path / "ws")),
    )
    assert out.ok, out.status
    census = capability_census(mode="manual", session_id=session)
    row = {r.intent: r for r in census.rows}["workspace.read_file"]
    assert row.executed >= 1
    assert row.receipted >= 1
    assert census.summary()["executed"] >= 1
    reset_mode_permission_state()


# ---------------------------------------------------------------------------
# The prompt seam is adaptive — the former fixed-8 family set is gone
# ---------------------------------------------------------------------------


def test_prompt_catalog_seam_is_adaptive_not_a_constant_eight() -> None:
    from core.prompt_normalizer import _tool_intent_catalog_text

    plain = _tool_intent_catalog_text(family_hint="workspace")
    adaptive = _tool_intent_catalog_text(
        family_hint="workspace", user_text="run the test suite in this repo"
    )
    assert "workspace.run_tests" in adaptive
    assert "workspace.run_tests" not in plain  # read-only-first ranking evicts it without the words
    assert adaptive != plain


def test_prompt_catalog_seam_honours_a_turn_expansion() -> None:
    from core.capability_graph import _DEFAULT_MAX_CANDIDATES
    from core.prompt_normalizer import _tool_intent_catalog_text
    from core.tool_offer_state import record_family_expansion, reset_offer_state

    reset_offer_state()
    context = {"turn_id": "prompt-expansion"}
    record_family_expansion(context, "filesystem")
    text = _tool_intent_catalog_text(family_hint="filesystem", source_context=context)
    listed = [line for line in text.split("- ") if "(" in line and ":" in line]
    assert len(listed) > _DEFAULT_MAX_CANDIDATES
    assert "machine.write_file" in text
    reset_offer_state()


def test_offer_stays_bounded_at_every_seam() -> None:
    from core.capability_graph import _DEFAULT_MAX_CANDIDATES, _HARD_MAX_CANDIDATES
    from core.runtime_tool_contracts import runtime_tool_contracts
    from core.tool_offer_assembly import assemble_tool_offer
    from core.tool_offer_state import record_family_expansion, reset_offer_state

    contracted = len([c for c in runtime_tool_contracts() if c.supported])
    offer = assemble_tool_offer(user_text="what can you do", task_class="conversation")
    assert len(offer.specs) <= _DEFAULT_MAX_CANDIDATES
    reset_offer_state()
    context = {"turn_id": "bounded"}
    record_family_expansion(context, "workspace")
    record_family_expansion(context, "filesystem")
    expanded = assemble_tool_offer(user_text="", task_class="debugging", source_context=context)
    assert _DEFAULT_MAX_CANDIDATES < len(expanded.specs) <= _HARD_MAX_CANDIDATES < contracted
    reset_offer_state()


def test_assembly_seam_and_native_seam_offer_the_same_intents() -> None:
    from core.capability_graph import model_visible_specs
    from core.tool_offer_assembly import assemble_tool_offer

    text = "read README.md and search the web for the release notes"
    offer = assemble_tool_offer(user_text=text, task_class="debugging", family_hint="workspace")
    native = model_visible_specs(family_hint="workspace", user_text=text)
    assert _intents(offer.specs) == _intents(native)


# ---------------------------------------------------------------------------
# Coding families are covered, and the missing one says so
# ---------------------------------------------------------------------------


def test_coding_families_are_covered_honestly() -> None:
    from core.capability_census import coding_family_coverage

    rows = {row["capability"]: row for row in coding_family_coverage()}
    expected_covered = {
        "filesystem_read": "workspace.read_file",
        "filesystem_write": "workspace.write_file",
        "search": "workspace.search_text",
        "shell": "sandbox.run_command",
        "tests": "workspace.run_tests",
        "lint": "workspace.run_lint",
        "git_read": "workspace.git_status",
    }
    for capability, intent in expected_covered.items():
        assert rows[capability]["status"] == "covered", rows[capability]
        assert intent in rows[capability]["intents"]
    git_mutation = rows["git_mutation"]
    assert git_mutation["status"] == "not_declared"
    assert git_mutation["intents"] == ()
    assert "sandbox.run_command" in git_mutation["reason"]
    assert "git_commit" in git_mutation["reason"]
