"""P0: every ENABLED contracted tool has a deterministic legal path to model selection.

RED baseline (census @ a6c8e3c4): the offer is hard-capped at 8 with read-only-first
ranking, one family per turn, no explicit-request priority, no navigator escalation,
MCP/plugin never seated in the graph, and 33 of 79 contracts structurally unreachable.

Each test below names one census defect and asserts the DESIRED behaviour, so the whole
file is RED on the base commit and GREEN only when the offer authority is fixed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core.capability_graph import (
    _DEFAULT_MAX_CANDIDATES,
    _FAMILY_REPRESENTATIVE,
    model_visible_specs,
)
from core.runtime_tool_contracts import runtime_tool_contract_map

_STUB_MCP = str(Path(__file__).parent / "mcp_stub_server.py")


@pytest.fixture(autouse=True)
def _isolated_permission_registry():
    """decide_tool_call raises a PENDING approval prompt into the PROCESS-GLOBAL
    mode/permission registry; one left behind hijacks a later suite's approval
    auto-resolve (its rig resolves the stale token, the real approval stays pending,
    the turn pauses as pending_approval, no transcript row is written, and
    first-run-pact claims fail evidence_missing -- the leak class measured across
    runs 35886493542/36063857499). Same setup/teardown discipline as the other
    permission-touching suites."""
    from core.mode_permission_policy import reset_mode_permission_state

    reset_mode_permission_state()
    yield
    reset_mode_permission_state()


def _intents(specs: list[dict]) -> set[str]:
    return {str(s.get("intent") or "") for s in specs}


# ---------------------------------------------------------------------------
# RED 1 — an explicit user request for an available tool cannot be ranked out
# ---------------------------------------------------------------------------


def test_explicit_tool_request_is_not_ranked_out() -> None:
    specs = model_visible_specs(
        family_hint="workspace",
        user_text="run the test suite in this repo",
    )
    assert "workspace.run_tests" in _intents(specs)


def test_explicit_read_request_wins_over_family_ranking() -> None:
    # `pdf.extract_text` is contracted and enabled, but no task class maps to the
    # pdf family: without explicit-request priority it is unreachable by phrasing.
    specs = model_visible_specs(
        family_hint="workspace",
        user_text="extract the text from report.pdf",
    )
    assert "pdf.extract_text" in _intents(specs)


# ---------------------------------------------------------------------------
# RED 2 — a workspace-write demand receives a mutation tool (permission still gates)
# ---------------------------------------------------------------------------


def test_workspace_write_demand_seats_a_mutation_tool() -> None:
    specs = model_visible_specs(
        family_hint="workspace",
        user_text="write hello world to notes/out.txt",
    )
    intents = _intents(specs)
    assert intents & {
        "workspace.write_file",
        "workspace.replace_in_file",
        "workspace.apply_unified_diff",
    }


def test_write_seat_does_not_bypass_the_permission_gate() -> None:
    # Offering the tool must not soften the controller: a write still needs a
    # decision in the caller's operating mode (default MANUAL -> approval).
    from core.mode_permission_policy import PermissionEffect, decide_tool_call

    permission = decide_tool_call(
        intent="workspace.write_file",
        arguments={"path": "notes/out.txt", "content": "hello"},
        task_id="p0-test",
        source_context={"session_id": "p0-sess", "operating_mode": "manual"},
    )
    assert permission.effect is PermissionEffect.REQUIRE_APPROVAL


# ---------------------------------------------------------------------------
# RED 3 — contextual follow-ups inherit the tool family instead of zero tools
# ---------------------------------------------------------------------------


def test_followup_turn_inherits_family_offer() -> None:
    from core.tool_offer_state import (
        last_offered_families,
        note_family_offer,
        reset_offer_state,
    )

    reset_offer_state()
    note_family_offer("p0-follow", ("workspace",))
    assert last_offered_families("p0-follow") == ("workspace",)


def test_followup_turn_is_admitted_by_the_tool_gate() -> None:
    from core.execution.planner import should_attempt_tool_intent
    from core.tool_offer_state import note_family_offer, reset_offer_state

    reset_offer_state()
    note_family_offer("p0-follow", ("workspace",))
    assert should_attempt_tool_intent(
        "so? audit the skills in there",
        task_class="chat_conversation",
        source_context={
            "runtime_session_id": "p0-follow",
            "session_id": "p0-follow",
            "surface": "api",
        },
    )


def test_followup_offer_seats_the_inherited_family() -> None:
    from core.tool_offer_state import reset_offer_state

    reset_offer_state()
    specs = model_visible_specs(
        user_text="so? audit the skills in there",
        family_hints=("workspace",),
    )
    assert _intents(specs)  # not zero
    assert len(specs) <= _DEFAULT_MAX_CANDIDATES + 3  # bounded, never the 58-def catalog


# ---------------------------------------------------------------------------
# RED 4 — mixed demands seat tools from every required family
# ---------------------------------------------------------------------------


def test_mixed_demand_seats_both_families() -> None:
    specs = model_visible_specs(
        user_text="read README.md and search the web for adaptive tool offering",
    )
    intents = _intents(specs)
    assert "workspace.read_file" in intents
    assert "web.search" in intents


def test_mixed_demand_stays_bounded() -> None:
    specs = model_visible_specs(
        user_text="read README.md and search the web for adaptive tool offering",
    )
    assert len(specs) <= _DEFAULT_MAX_CANDIDATES + 3


# ---------------------------------------------------------------------------
# RED 5 — family representatives must resolve to real contracts (import-time law)
# ---------------------------------------------------------------------------


def test_every_family_representative_resolves_to_a_real_contract() -> None:
    from core.capability_graph import assert_representatives_resolve

    assert_representatives_resolve()  # raises on any dead pointer


def test_representative_map_has_no_phantom_intents() -> None:
    contracts = runtime_tool_contract_map()
    dead = {f: r for f, r in _FAMILY_REPRESENTATIVE.items() if r not in contracts}
    assert not dead, f"representatives naming non-contracted intents: {dead}"


# ---------------------------------------------------------------------------
# RED 6 — MCP tools enter the capability graph when configured
# ---------------------------------------------------------------------------


@pytest.fixture()
def _stub_mcp(tmp_path, monkeypatch):
    from core.execution import mcp_bridge

    cfg = tmp_path / "mcp_servers.json"
    cfg.write_text(
        json.dumps(
            {"servers": [{"name": "stub", "command": sys.executable, "args": [_STUB_MCP]}]}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("VOOL_MCP_CONFIG", str(cfg))
    mcp_bridge.reset_mcp_state()
    yield
    mcp_bridge.reset_mcp_state()


def test_configured_mcp_tools_are_seatable(_stub_mcp) -> None:
    specs = model_visible_specs(family_hint="mcp")
    intents = _intents(specs)
    assert "mcp.stub.echo" in intents


def test_configured_mcp_tools_have_a_navigation_seat(_stub_mcp) -> None:
    specs = model_visible_specs()  # no hint: family-navigation set
    assert any(i.startswith("mcp.") for i in _intents(specs))


def test_mcp_tools_absent_when_not_configured() -> None:
    specs = model_visible_specs()
    assert not any(i.startswith("mcp.") for i in _intents(specs))


# ---------------------------------------------------------------------------
# RED 7 — plugin tools become discoverable when the flag is explicitly enabled
# ---------------------------------------------------------------------------


@pytest.fixture()
def _plugin_registered(tmp_path, monkeypatch):
    from core import tool_registry
    from core.runtime_flags import override

    manifest = tmp_path / "plugins" / "p0-pack" / ".codex-plugin"
    manifest.mkdir(parents=True)
    (manifest / "plugin.json").write_text(
        json.dumps(
            {
                "name": "p0-pack",
                "version": "1.0.0",
                "description": "census probe pack",
                "runtime": {"contract_version": 1},
                "tools": [
                    {
                        "intent": "p0-pack.probe",
                        "description": "Probe tool for the offer-authority census.",
                        "handler": {"kind": "subprocess", "entry": "bin/run", "args": ["probe"]},
                        "input_schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["query"],
                            "properties": {"query": {"type": "string"}},
                        },
                        "side_effect_class": "read_only",
                        "approval_requirement": "none",
                        "claim": {"target_argument": "query", "resolved_target_key": "query"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    tool_registry.reset()
    monkeypatch.setenv("VOOL_PLUGINS_DIR", str(tmp_path))
    # The plugin loader is ONE-SHOT per process (capability_graph._PLUGINS_LOADED): a suite that
    # read any spec with plugin_runtime_tools on already loaded whatever plugins root existed
    # then, and this fixture's own pack would never register -- the victim then seats only the
    # builtin catalog (measured: run 36063857499 shard 7; reproduced locally with
    # test_native_skill_library.py before this change). Reset the graph/loader state on ENTRY,
    # the same reset the teardown below performs on exit, so this pack loads whatever ran before.
    from core import capability_graph

    capability_graph.reset()
    capability_graph.init_graph()
    # Writing the manifest is DISCOVERY. The pack reaches the model's catalog only after the
    # lifecycle says installed, verified and enabled -- so the fixture performs those acts
    # instead of the census asserting on a pack nobody installed.
    from tests._toolchain_fixtures import admit_plugin

    admit_plugin("p0-pack", tmp_path / "plugins" / "p0-pack")
    with override("plugin_runtime_tools", True):
        yield str(tmp_path)
    # Graph rows outlive tool_registry.reset(); restore the builtin-only world
    # so no later test sees this pack.
    from core import capability_graph

    tool_registry.reset()
    capability_graph.reset()
    capability_graph.init_graph()
    capability_graph.bootstrap_from_registry()


def test_enabled_plugin_tool_is_seatable(_plugin_registered) -> None:
    specs = model_visible_specs(family_hint="plugin")
    assert "p0-pack.probe" in _intents(specs)


def test_enabled_plugin_tool_reachable_by_explicit_request(_plugin_registered) -> None:
    specs = model_visible_specs(user_text="use the p0-pack probe with query alpha")
    assert "p0-pack.probe" in _intents(specs)


def test_plugin_tools_stay_hidden_when_flag_off(tmp_path, monkeypatch) -> None:
    from core import tool_registry
    from core.runtime_flags import override

    tool_registry.reset()
    specs = model_visible_specs(family_hint="plugin")
    assert "p0-pack.probe" not in _intents(specs)
    with override("plugin_runtime_tools", False):
        assert specs == model_visible_specs(family_hint="plugin")


# ---------------------------------------------------------------------------
# RED 8 — one contracted name owns the browser-render lane
# ---------------------------------------------------------------------------


def test_browser_render_has_one_contracted_identity() -> None:
    contracts = runtime_tool_contract_map()
    assert "browser.render" in contracts
    assert "web.browser_render" not in contracts


def test_browser_render_is_dispatchable_on_its_contracted_name() -> None:
    from core.execution.constants import _WEB_TOOL_INTENTS

    assert "browser.render" in _WEB_TOOL_INTENTS


# ---------------------------------------------------------------------------
# Navigator + expansion (invariants 7/8) — same file, same baseline
# ---------------------------------------------------------------------------


def test_operator_list_tools_reports_the_real_catalog_by_family() -> None:
    from core.tool_navigator import catalog_family_table, render_family_table

    table = catalog_family_table()
    families = {str(row.get("family") or "") for row in table}
    assert "workspace" in families
    assert "web" in families
    row = next(r for r in table if r.get("family") == "workspace")
    assert isinstance(row.get("available_count"), int)
    assert row["available_count"] >= 1

    text = render_family_table(table)
    assert "capability.expand_family" in text  # the escalation instruction is part of the map


def test_navigator_keeps_disabled_tools_as_unavailable_metadata() -> None:
    from core.tool_navigator import catalog_family_table

    table = catalog_family_table()
    email_row = next((r for r in table if r.get("family") == "email"), None)
    assert email_row is not None
    assert email_row["unavailable_count"] >= 1  # email.send etc. stay visible...
    assert email_row["available_count"] == 0  # ...but never seat (metadata only)


def test_operator_lane_serves_the_family_table() -> None:
    from core.operator.handlers import handle_list_tools

    result = handle_list_tools(
        intent=None,
        task_id="p0-task",
        session_id="p0-sess",
        operator_capability_ledger_fn=lambda: [],
        audit_log_fn=lambda *a, **k: None,
    )
    assert result.ok
    assert "Tool families on this runtime:" in result.response_text
    assert isinstance(result.details.get("families"), list)


def test_expand_family_is_offered_and_returns_bounded_family_set() -> None:
    from core.tool_offer_state import (
        begin_turn_navigation,
        end_turn_navigation,
        record_family_expansion,
        reset_offer_state,
        turn_family_expansions,
    )

    specs = model_visible_specs(family_hint="workspace")
    assert "capability.expand_family" in _intents(specs)  # always offered

    reset_offer_state()
    context = {"runtime_session_id": "p0-sess", "turn_id": "p0-turn"}
    scope = begin_turn_navigation(context)
    assert record_family_expansion(context, "pdf") == ("pdf",)
    # Migrated in revision 6. This read used to be consume_family_expansions, read-and-clear, which
    # let a round's prompt catalog take the expansion from the native tool definitions (Gate B). The
    # expansion is turn navigation now: a read leaves it for every later round of the turn, and
    # closing the turn's scope drops it. A destructive read fails the second assertion.
    assert turn_family_expansions(context) == ("pdf",)
    assert turn_family_expansions(context) == ("pdf",)
    end_turn_navigation(context, scope)
    assert turn_family_expansions(context) == ()


def test_expansion_reseats_the_requested_family() -> None:
    from core.tool_offer_state import record_family_expansion, reset_offer_state

    reset_offer_state()
    context = {"runtime_session_id": "p0-sess", "turn_id": "p0-turn"}
    record_family_expansion(context, "pdf")
    specs = model_visible_specs(source_context=context, family_hint="workspace")
    assert "pdf.extract_text" in _intents(specs)


# ---------------------------------------------------------------------------
# THE stop condition: zero enabled contracted tools structurally unreachable
# ---------------------------------------------------------------------------


def test_every_enabled_contracted_tool_has_a_legal_path(tmp_path, monkeypatch) -> None:
    """Hint seating ∪ lone-family expansion ∪ STATEFUL session seats covers every enabled,
    model-visible contract; owner-only contracts are covered by their owner-local surface.

    This is the mission's stop condition as a pinned test: if a new contract is
    added and no family can ever seat it, this fails instead of shipping the
    silent unreachability the census found (33 of 79 at a6c8e3c4).

    Two paths are modelled beyond the family hints, matching how the runtime actually seats
    tools: an OPEN RepoOps session seats its stage's control-plane tools (journal-read, the
    same mechanism the served journeys exercise), and an owner-only contract
    (`model_visible=False`, e.g. an operator authorization mint) is counted under its
    owner-local path -- a model must never be offered it, which the negative test below
    pins separately.
    """
    import json as _json
    from datetime import datetime, timezone

    from core.capability_graph import _CANONICAL_FAMILIES
    from core.runtime_tool_contracts import runtime_tool_contracts
    from core.tool_offer_state import record_family_expansion, reset_offer_state

    enabled = {
        c.intent
        for c in runtime_tool_contracts()
        if c.supported and c.intent != "respond.direct" and getattr(c, "model_visible", True)
    }
    owner_only = {
        c.intent
        for c in runtime_tool_contracts()
        if c.supported and getattr(c, "model_visible", True) is False
    }
    union: set[str] = set()
    for family in sorted(_CANONICAL_FAMILIES):
        union |= _intents(model_visible_specs(family_hint=family))
        reset_offer_state()
        record_family_expansion({"turn_id": "coverage"}, family)
        union |= _intents(model_visible_specs(source_context={"turn_id": "coverage"}))
    reset_offer_state()

    # The STATEFUL path: an open RepoOps session seats its control-plane tools for the chat
    # session that owns it. A live session journal is seeded exactly as the runtime reads it.
    sessions = tmp_path / "repo_sessions_census"
    sessions.mkdir()
    (sessions / "rs-census.json").write_text(
        _json.dumps(
            {
                "session_key": "rs-census",
                "session_id": "census-chat",
                "stage": "retrieve",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "forge_plan": {"action": "create"},
            }
        ),
        encoding="utf-8",
    )
    # Two moments of one workflow: a retrieve-stage session with a planned create seats the
    # plan step and the create executor; a push-stage session seats the write executors.
    # Both are journal-read (the mechanism the served journeys exercise), unioned as paths.
    for stage, plan in (("retrieve", {"action": "create"}), ("push", {})):
        (sessions / "rs-census.json").write_text(
            _json.dumps(
                {
                    "session_key": "rs-census",
                    "session_id": "census-chat",
                    "stage": stage,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "forge_plan": plan,
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("VOOL_REPOOPS_DIR", str(sessions))
        from core.tool_offer_assembly import assemble_tool_offer

        offer = assemble_tool_offer(user_text="continue the repository workflow",
                                    source_context={"session_id": "census-chat"})
        union |= set(offer.intents)
        monkeypatch.delenv("VOOL_REPOOPS_DIR")

    never = sorted(enabled - union)
    assert not never, f"enabled contracted tools with no legal selection path: {never}"
    # Owner-only contracts must be exactly the ones the owner surface owns -- and must never
    # have leaked into the model-visible union above.
    assert owner_only, "an owner-only contract was expected to exist"
    assert not (owner_only & union), owner_only & union


def test_owner_only_authorization_is_never_offered_to_a_model() -> None:
    """The operator's authorization mint has a legal OWNER path only. No family hint, no
    expansion, and no open-session stateful seat may ever offer it to a model -- pinning the
    model-forged-authorization negative the owner-only marking exists for."""
    from core.capability_graph import _CANONICAL_FAMILIES, model_visible_specs
    from core.tool_offer_state import record_family_expansion, reset_offer_state

    for family in sorted(_CANONICAL_FAMILIES):
        assert "repo.pr.authorize" not in _intents(model_visible_specs(family_hint=family))
        reset_offer_state()
        record_family_expansion({"turn_id": "neg"}, family)
        assert "repo.pr.authorize" not in _intents(model_visible_specs(source_context={"turn_id": "neg"}))
    reset_offer_state()
    # Even the repo family's own seats never include the mint.
    assert "repo.pr.authorize" not in _intents(model_visible_specs(family_hint="repo"))


def test_offer_prompt_budget_stays_bounded() -> None:
    """An adaptive offer is nowhere near the 58-definition always-on catalog."""
    from core.capability_graph import _HARD_MAX_CANDIDATES

    specs = model_visible_specs(
        family_hint="workspace",
        user_text="run the test suite in this repo and read README.md",
    )
    assert len(specs) <= _DEFAULT_MAX_CANDIDATES + 3  # ordinary turn: 8-ish
    assert len(json.dumps(specs, default=str)) < 12_000  # bounded prompt cost

    from core.tool_offer_state import record_family_expansion, reset_offer_state

    reset_offer_state()
    context = {"turn_id": "budget"}
    record_family_expansion(context, "workspace")
    record_family_expansion(context, "filesystem")
    expanded = model_visible_specs(source_context=dict(context))
    assert len(expanded) <= _HARD_MAX_CANDIDATES  # even a double expansion is capped
    reset_offer_state()
