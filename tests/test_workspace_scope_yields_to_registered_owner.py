"""The workspace-scope answers yield to another domain's registered lane that owns the whole turn, by catalog precedence.

`core.agent_runtime.demand_ownership.registered_owner_ahead_of` asks the one question: which registered lane owns
every demand unit of this turn ahead of the asking lane. The folder-overview and workspace-identity admissions ask it
before they claim (`fast_paths_utility._another_domain_owns_the_turn`). The measured defect and the served proof are in
`tests/pa_beta_gate/test_served_notes_folder_scope_routing.py`. Here the law and the two admissions are pinned at their
own seams, including the parts a served turn cannot isolate: the catalog order, the domain rule, whole-turn
coverage and the fault direction.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from core.agent_runtime.fast_paths_utility import (
    maybe_handle_folder_overview_request,
    maybe_handle_workspace_identity_request,
)

TIER = "turn_frontdoor_deterministic"
ORIGINAL = 'rename my Apple note "Plan" in the Work folder to "Plan v2"'


class _StubAgent:
    def _fast_path_result(self, *, session_id, user_input, response, confidence, source_context, reason):
        return {"response": response, "reason": reason}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    import core.runtime_execution_tools as runtime_tools

    monkeypatch.setattr(
        runtime_tools,
        "execute_runtime_tool",
        lambda *args, **kwargs: SimpleNamespace(ok=True, response_text=f"The workspace is set to `{tmp_path}`."),
    )
    (tmp_path / "README.md").write_text("# Harbor\nTide tables for the east pier.\n", encoding="utf-8")
    (tmp_path / "tide_table.py").write_text("print('tide')\n", encoding="utf-8")
    return {"workspace": str(tmp_path), "surface": "api"}


def _claims(text, context):
    """(folder-overview reason, workspace-identity reason) -- None where the admission declined."""
    overview = maybe_handle_folder_overview_request(
        _StubAgent(), text, session_id="s", source_surface="api", source_context=context
    )
    identity = maybe_handle_workspace_identity_request(
        _StubAgent(), text, session_id="s", source_surface="api", source_context=context
    )
    return (overview or {}).get("reason"), (identity or {}).get("reason")


# ------------------------------------------------------------------------------------------ the law


def test_the_registry_names_the_operator_lane_for_a_folder_scoped_notes_request():
    from core.agent_runtime.demand_ownership import registered_owner_ahead_of

    assert registered_owner_ahead_of(ORIGINAL, TIER, capability="workspace_read") == "operator_action_dispatch"


@pytest.mark.parametrize(
    "text",
    [
        "what's in the work folder?",
        "whats in this folder",
        "which folder am I in?",
        "count how many python files are in this project",
    ],
)
def test_a_workspace_question_has_no_owner_ahead_of_the_tier(text):
    from core.agent_runtime.demand_ownership import registered_owner_ahead_of

    assert registered_owner_ahead_of(text, TIER, capability="workspace_read") == ""


def test_a_workspace_domain_lane_is_a_sibling_reading_not_an_owner():
    """The audit lane claims "audit this work folder" and ranks ahead of the tier. It reads the same subject, so for a
    workspace-scope asker it is not an owner. Without the domain it would be one, which shows the domain is the reason."""
    from core.agent_runtime.demand_ownership import registered_owner_ahead_of

    assert registered_owner_ahead_of("audit this work folder", TIER) == "workspace_audit_frontdoor"
    assert registered_owner_ahead_of("audit this work folder", TIER, capability="workspace_read") == ""


def test_the_order_is_the_catalog_precedence_not_a_lane_name():
    from core.agent_runtime.demand_ownership import registered_owner_ahead_of
    from core.lane_registry import active_catalog, scoped_catalog

    demoted = [
        replace(spec, precedence=55) if spec.lane_id == "operator_action_dispatch" else spec
        for spec in active_catalog()
    ]
    with scoped_catalog(demoted):
        assert registered_owner_ahead_of(ORIGINAL, TIER, capability="workspace_read") == ""
    assert registered_owner_ahead_of(ORIGINAL, TIER, capability="workspace_read") == "operator_action_dispatch"


def test_an_owner_covers_every_unit_within_its_own_limit():
    from core.agent_runtime.demand_ownership import demand_coverage, registered_owner_ahead_of

    # A Notes request beside an arithmetic one: no single lane covers both units.
    two_requests = ORIGINAL + ". Also what is 17 times 23?"
    assert demand_coverage(two_requests).unit_count == 2
    assert registered_owner_ahead_of(two_requests, TIER, capability="workspace_read") == ""
    # One unit arithmetic covers beside one nobody covers: a lane covering SOME units owns nothing.
    partly = "what is 17 times 23? Also what's in the work folder?"
    per_unit = demand_coverage(partly).per_unit_lanes
    assert len(per_unit) == 2 and any(per_unit) and not all(per_unit), per_unit
    assert registered_owner_ahead_of(partly, TIER, capability="workspace_read") == ""
    # Two Notes requests: the operator lane reads both units, but its parser returns one action (max_units=1), so
    # it cannot own the turn alone. The composite plan does, as `DemandCoverage.mixed` reads it.
    two_notes = 'rename my Apple note "Plan" in the Work folder to "Plan v2" and append to my Apple note "Ideas" with "x"'
    coverage = demand_coverage(two_notes)
    assert coverage.unit_count == 2, coverage.units
    assert all("operator_action_dispatch" in lanes for lanes in coverage.per_unit_lanes), coverage.per_unit_lanes
    assert registered_owner_ahead_of(two_notes, TIER, capability="workspace_read") == ""


def test_an_unregistered_asker_ranks_after_the_whole_catalog():
    from core.agent_runtime.demand_ownership import registered_owner_ahead_of

    assert registered_owner_ahead_of(ORIGINAL, "an_unregistered_lane") == "operator_action_dispatch"
    assert registered_owner_ahead_of("   ", TIER) == ""


# ------------------------------------------------------------------------------------ the admissions


@pytest.mark.parametrize(
    "text",
    [
        ORIGINAL,
        'append to my Apple note "Packing list" in the Local folder with "passport and charger"',
        # the payload itself reads as a workspace question, to each arm in turn
        'append to my Apple note "Ideas" with "which folder am I in?"',
        'append to my Apple note "Ideas" with "review the project budget"',
        'append to my Apple note "Ideas" with "number of python files"',
    ],
)
def test_the_workspace_scope_admissions_decline_a_turn_another_domain_owns(workspace, text):
    assert _claims(text, workspace) == (None, None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("what's in the work folder?", ("folder_overview_fast_path", None)),
        ("explain the local folder we are in", ("folder_overview_fast_path", None)),
        ("which folder am I in?", (None, "workspace_identity_fast_path")),
        ("count how many python files are in this project", ("workspace_measurement_fast_path", None)),
        # same-domain overlap: unchanged by this rule (the front door runs the audit lane first)
        ("audit this work folder", ("folder_overview_fast_path", None)),
    ],
)
def test_the_workspace_scope_admissions_keep_workspace_questions(workspace, text, expected):
    assert _claims(text, workspace) == expected


def test_a_registry_fault_keeps_the_pre_existing_claim(workspace, monkeypatch):
    """Fail-soft in the direction every finalize check in the front door takes: an unavailable registry grants the
    admission its old claim and never raises into the turn."""
    import core.agent_runtime.demand_ownership as ownership

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(ownership, "registered_owner_ahead_of", unavailable)
    assert _claims("what's in the work folder?", workspace) == ("folder_overview_fast_path", None)
    assert _claims(ORIGINAL, workspace) == ("folder_overview_fast_path", None)
