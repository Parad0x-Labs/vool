"""Front-door claims the lane registry cannot see yield to the registered lane that owns the whole turn.

Three residuals of the served Notes folder-routing repair (d6a398af), measured on 2026-09-15 through served turns
(`VoolAgent.run_once`, model stand-in, synthetic Notes runner). They are pinned here at their own seams; the served
proofs are in `tests/pa_beta_gate/test_served_notes_precedence_residuals.py`.

1. The search/git arm of the workspace-runtime lane answered 'append to my Apple note "Ideas" with "count how many
   python files are in this project"' with "No text matches for "Ideas" were found in the workspace." The registry
   named `operator_action_dispatch` as the only lane covering the one unit. The read capability claimed nothing, so
   the arm was an undeclared claim, and it was asked at the read lane's precedence (42), ahead of operator (48).
2. The intent arbiter was asked about 'rename my Apple note "Plan" in the Work folder to "Plan v2"' (a near-miss on
   "folder") before operator dispatch. A `find_folder` pick ran `machine.find_folder("Work")` in place of the rename.
3. 'write a haiku about rain in this project': the live-weather recognizer read "rain in this project" as a
   place-scoped weather request, so the live lanes claimed a request for a poem.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

import pytest

from core import routing_decision_log as rdl
from core import runtime_paths

TIER = "turn_frontdoor_deterministic"
NOTES_SEARCH_PAYLOAD = 'append to my Apple note "Ideas" with "count how many python files are in this project"'
NOTES_FOLDER_RENAME = 'rename my Apple note "Plan" in the Work folder to "Plan v2"'
MENU = ("workspace_read", "machine_fact")


# ------------------------------------------------------------------------------------------------------ the law


def test_a_claim_outside_the_askers_declared_capability_ranks_as_the_front_door_tier():
    from core.agent_runtime.demand_ownership import demand_coverage, registered_owner_ahead_of

    coverage = demand_coverage(NOTES_SEARCH_PAYLOAD)
    assert coverage.lane_unit_ids("workspace_read_fast_path") == (), "the read capability claims no unit here"
    assert coverage.per_unit_lanes == (("operator_action_dispatch",),)
    assert (
        registered_owner_ahead_of(NOTES_SEARCH_PAYLOAD, "workspace_read_fast_path", capability="workspace_read")
        == "operator_action_dispatch"
    )


def test_a_declared_claim_keeps_its_own_precedence():
    """When the read capability DOES claim the unit, the claim is declared and the catalog order stands (42 < 48)."""
    from core.agent_runtime.demand_ownership import (
        demand_coverage,
        registered_owner_ahead_of,
        scoped_coverage_capabilities,
    )

    with scoped_coverage_capabilities({"workspace_read": lambda unit: True}):
        assert "workspace_read_fast_path" in demand_coverage(NOTES_SEARCH_PAYLOAD).per_unit_lanes[0]
        assert registered_owner_ahead_of(NOTES_SEARCH_PAYLOAD, "workspace_read_fast_path", capability="workspace_read") == ""


def test_the_undeclared_rank_is_the_catalogs_tier_not_a_lane_name():
    from core.agent_runtime.demand_ownership import registered_owner_ahead_of
    from core.lane_registry import active_catalog, scoped_catalog

    tier_first = [replace(spec, precedence=47) if spec.lane_id == TIER else spec for spec in active_catalog()]
    with scoped_catalog(tier_first):
        assert registered_owner_ahead_of(NOTES_SEARCH_PAYLOAD, "workspace_read_fast_path", capability="workspace_read") == ""
    assert (
        registered_owner_ahead_of(NOTES_SEARCH_PAYLOAD, "workspace_read_fast_path", capability="workspace_read")
        == "operator_action_dispatch"
    )


def test_several_capabilities_name_several_sibling_domains():
    from core.agent_runtime.demand_ownership import registered_owner_ahead_of

    audit = "right, can you check Token hunter folder on this machine and run audit"
    disk = "How much disk space is left on this machine?"
    assert registered_owner_ahead_of(audit, TIER) == "workspace_audit_frontdoor"
    assert registered_owner_ahead_of(disk, TIER) == "machine_fact_fast_path"
    assert registered_owner_ahead_of(disk, TIER, capability=("workspace_read",)) == "machine_fact_fast_path"
    assert registered_owner_ahead_of(audit, TIER, capability=MENU) == ""
    assert registered_owner_ahead_of(disk, TIER, capability=MENU) == ""
    assert registered_owner_ahead_of(NOTES_FOLDER_RENAME, TIER, capability=MENU) == "operator_action_dispatch"


# -------------------------------------------------------------------------------------------- the search arm


class _Agent:
    def __init__(self):
        self.events = []

    def _plan_tool_workflow(self, **kwargs):
        from core.execution.planner import plan_tool_workflow

        return plan_tool_workflow(**kwargs)

    def _emit_runtime_event(self, source_context, **kwargs):
        self.events.append(kwargs)

    def _fast_path_result(self, *, session_id, user_input, response, confidence, source_context, reason):
        return {"response": response, "reason": reason}


@pytest.fixture
def search_arm(tmp_path, monkeypatch):
    import core.agent_runtime.fast_paths_utility as utility

    (tmp_path / "tide_table.py").write_text("def tide():\n    return 'TODO: check tide'\n", encoding="utf-8")
    ran = []

    def tool(intent, arguments, **kwargs):
        ran.append((intent, dict(arguments)))
        return SimpleNamespace(ok=True, response_text=f"ran {intent}", details={})

    monkeypatch.setattr(utility, "execute_runtime_tool", tool)
    context = {"workspace": str(tmp_path), "surface": "api"}

    def claim(text):
        return utility.maybe_handle_direct_workspace_runtime_request(
            _Agent(), text, session_id="s", source_surface="api", source_context=context
        )

    return SimpleNamespace(claim=claim, ran=ran, context=context)


_NOTES_WITH_SEARCH_PAYLOADS = [
    NOTES_SEARCH_PAYLOAD,
    'append to my Apple note "Ideas" with "search the codebase for TODO"',
    'append to my Apple note "Ideas" with "where is main defined"',
    'rename my Apple note "Ideas" to "grep for TODO"',
    'append to my Apple note "Groceries" with "where is the harbor manifest used in this repo"',
]


@pytest.mark.parametrize("text", _NOTES_WITH_SEARCH_PAYLOADS)
def test_the_search_arm_declines_a_turn_another_domain_owns(search_arm, text):
    from core.execution.planner import plan_tool_workflow

    planned = plan_tool_workflow(user_text=text, task_class="file_inspection", executed_steps=[],
                                 source_context=search_arm.context)
    assert (planned.next_payload or {}).get("intent") == "workspace.search_text", "the arm would claim this turn"
    assert search_arm.claim(text) is None
    assert search_arm.ran == [], "no workspace search runs for another domain's request"


@pytest.mark.parametrize(
    "text", ["search the codebase for TODO", "where is tide defined", 'look through the workspace and find "tide"',
             "which files mention tide", "grep for TODO"],
)
def test_the_search_arm_keeps_workspace_searches(search_arm, text):
    result = search_arm.claim(text)
    assert result is not None and result["reason"] == "workspace_runtime_fast_path", result
    assert [intent for intent, _ in search_arm.ran] == ["workspace.search_text"]


def test_a_registry_fault_keeps_the_search_arms_claim(search_arm, monkeypatch):
    import core.agent_runtime.demand_ownership as ownership

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(ownership, "registered_owner_ahead_of", unavailable)
    result = search_arm.claim(NOTES_SEARCH_PAYLOAD)
    assert result is not None and result["reason"] == "workspace_runtime_fast_path"


# ------------------------------------------------------------------------------------------------ the arbiter


@pytest.fixture
def arbiter_env(tmp_path, monkeypatch):
    runtime_paths.configure_runtime_home(tmp_path / "home")
    monkeypatch.setenv("VOOL_INTENT_ARBITER", "1")
    monkeypatch.setenv("VOOL_ARBITER_MODEL", "qwen3:0.6b")
    yield
    runtime_paths.configure_runtime_home(None)


def _gate(text, gate):
    from core.agent_runtime.turn_frontdoor import _maybe_arbitrate_intent

    return _maybe_arbitrate_intent(
        mock.Mock(), effective_input=text, session_id="openclaw:residuals0000000000", source_surface="chat",
        source_context={}, gate=gate,
    )


@pytest.mark.parametrize(
    "text",
    [
        NOTES_FOLDER_RENAME,
        'append to my Apple note "Ideas" with "the folder structure looks fine"',
        'rename my Apple note "Ideas" to "disk cleanup plan"',
        'append to my apple note "Ideas" with "ram and cpu notes"',
        "remind me to file the report tomorrow at 9am",
        "biggest files on my drive",
    ],
)
def test_the_arbiter_is_not_asked_about_a_turn_another_domains_lane_owns(arbiter_env, text):
    from core.agent_runtime.intent_claims import near_miss, probe_claims
    from core.agent_runtime.turn_frontdoor import _ARBITRATE_ON_NEAR_MISS

    assert near_miss(text, probe_claims(text)), "the near-miss gate would consult the arbiter"
    with mock.patch("core.intent_arbiter.arbitrate", side_effect=AssertionError("the arbiter must not be asked")):
        assert _gate(text, _ARBITRATE_ON_NEAR_MISS) is None
    assert rdl.recent_decisions()[-1]["arbiter"] == "declined:registered_owner:operator_action_dispatch"


@pytest.mark.parametrize(
    ("text", "signal"),
    [
        ("fint the oken hunter folder", "near_miss"),
        ("What is in my Downloads folder?", "near_miss"),
        ("hey lets audit thsi folder?", "near_miss"),
        ("How much disk space is left on this machine?", "ambiguous"),
        ("right, can you check Token hunter folder on this machine and run audit", "ambiguous"),
    ],
)
def test_turns_owned_inside_the_menus_domains_keep_the_arbiter(arbiter_env, text, signal):
    from core.agent_runtime.turn_frontdoor import _ARBITRATE_ON_AMBIGUITY, _ARBITRATE_ON_NEAR_MISS
    from core.intent_arbiter import ArbiterDecision

    gate = _ARBITRATE_ON_NEAR_MISS if signal == "near_miss" else _ARBITRATE_ON_AMBIGUITY
    with mock.patch("core.intent_arbiter.arbitrate", return_value=ArbiterDecision("chat")) as arbitrate:
        assert _gate(text, gate) is None
    arbitrate.assert_called_once()
    assert rdl.recent_decisions()[-1]["arbiter"] == "declined:chat"


def test_a_registry_fault_keeps_the_arbiter(arbiter_env, monkeypatch):
    import core.agent_runtime.demand_ownership as ownership
    from core.agent_runtime.turn_frontdoor import _ARBITRATE_ON_NEAR_MISS
    from core.intent_arbiter import ArbiterDecision

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(ownership, "registered_owner_ahead_of", unavailable)
    with mock.patch("core.intent_arbiter.arbitrate", return_value=ArbiterDecision("chat")) as arbitrate:
        assert _gate(NOTES_FOLDER_RENAME, _ARBITRATE_ON_NEAR_MISS) is None
    arbitrate.assert_called_once()


# ------------------------------------------------------------------------------------------ the creative register

_POEMS_ABOUT_WEATHER_WORDS = [
    "write a haiku about rain in this project",
    "compose a short poem about rain for this project",
    "give me a haiku on the rain in this repo",
    "could you write a limerick about snow in this workspace",
    "i'd like a sonnet about the wind in this folder",
    "draft a few lines of verse about rain in this project",
    "write a haiku about rain in Vilnius",
    "write a poem about the weather in this folder",
    "write a haiku abt rain in this projct",
    "WRITE A HAIKU ABOUT RAIN IN THIS PROJECT",
    "pls write me a haiku bout rain in this project thx",
    "yo compose a quick haiku about the rain in this repo",
]


@pytest.mark.parametrize("text", _POEMS_ABOUT_WEATHER_WORDS)
def test_a_poem_about_a_weather_word_is_not_a_live_lookup(text):
    from core.agent_runtime.demand_ownership import demand_coverage
    from core.agent_runtime.fast_live_info_mode_classifier import _looks_like_live_weather_request, live_info_mode
    from core.execution_requirements import _live_data_classification

    assert _looks_like_live_weather_request(" ".join(text.lower().split())), "the weather words are still there"
    assert live_info_mode(None, text, interpretation=None) == ""
    assert _live_data_classification(text) is None
    assert demand_coverage(text).per_unit_lanes == ((),)


@pytest.mark.parametrize(
    ("text", "toolsets"),
    [
        ("what is the price of gold now? also write a poem.", ("market_prices",)),
        ("Tell me the weather in Vilnius, write a haiku about rain, and give me BTC price", ("market_prices", "weather")),
        ("write a haiku about rain and tell me the weather in Vilnius", ("weather",)),
    ],
)
def test_another_clause_keeps_its_own_lookup(text, toolsets):
    from core.execution_requirements import _live_data_classification

    reading = _live_data_classification(text)
    assert reading is not None and reading[2] == toolsets, reading


@pytest.mark.parametrize(
    "text",
    [
        "write a haiku about today's weather in Vilnius",
        "write a haiku about the current weather in Vilnius",
        "will it rain in Vilnius today?",
        "rain in london tomorrow?",
        "weather in kaunas",
        "tell me if it's going to rain in paris",
        "write down whether it will rain in Vilnius today",
    ],
)
def test_a_request_about_the_present_weather_keeps_its_lookup(text):
    from core.agent_runtime.fast_live_info_mode_classifier import live_info_mode
    from core.execution_requirements import _live_data_classification

    assert live_info_mode(None, text, interpretation=None) == "weather"
    assert _live_data_classification(text) is not None


def test_text_with_no_writing_clause_comes_back_unchanged():
    from core.agent_runtime.grounded_mode import creative_writing_remainder

    listing = "Weather for:\n  Vilnius\n  Kaunas\n"
    assert creative_writing_remainder(listing) is listing
    assert creative_writing_remainder("what is the price of gold now? also write a poem.") == (
        "what is the price of gold now?"
    )


def test_which_clause_asks_for_writing_is_the_creative_authoritys_reading(monkeypatch):
    import core.creative_director as creative
    from core.agent_runtime.grounded_mode import creative_writing_remainder

    assert creative_writing_remainder("write a haiku about rain in this project") == ""
    monkeypatch.setattr(creative, "detect_prose_request", lambda _text: None)
    assert creative_writing_remainder("write a haiku about rain in this project") == (
        "write a haiku about rain in this project"
    )


@pytest.mark.parametrize(
    "text", ["i'd like a sonnet about the wind in this folder", "write a poem about the weather in this folder"]
)
def test_a_poem_that_names_a_folder_is_not_a_near_miss(text):
    from core.agent_runtime.intent_claims import _TOOLISH_NOUN_RE, near_miss, probe_claims

    claims = probe_claims(text)
    assert claims == [] and _TOOLISH_NOUN_RE.search(text), "a tool-ish noun and no claim: the old near-miss shape"
    assert not near_miss(text, claims)


@pytest.mark.parametrize("text", ["fint the oken hunter folder", "write a haiku about rain. fint the oken hunter folder"])
def test_a_folder_request_beside_or_without_a_poem_is_still_a_near_miss(text):
    from core.agent_runtime.intent_claims import near_miss, probe_claims

    claims = probe_claims(text)
    assert claims == []
    assert near_miss(text, claims)


@pytest.fixture
def overview_context(tmp_path):
    (tmp_path / "tide_table.py").write_text("print('tide')\n", encoding="utf-8")
    return {"workspace": str(tmp_path), "surface": "api"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("write a haiku about rain in this project", None),
        ("could you write a limerick about snow in this workspace", None),
        ("what's in this project?", "folder_overview_fast_path"),
        ("explain the local folder we are in", "folder_overview_fast_path"),
    ],
)
def test_the_folder_overview_reads_a_request_without_its_writing_clause(overview_context, text, expected):
    from core.agent_runtime.fast_paths_utility import maybe_handle_folder_overview_request

    result = maybe_handle_folder_overview_request(
        _Agent(), text, session_id="s", source_surface="api", source_context=overview_context
    )
    assert (result or {}).get("reason") == expected
